"""
MarketSnapshot — Unified Market Indicators
Version: 28.0

Single source of truth for market indicators calculation.
Consolidates: ATR, Delta, DER, Volume Ratio, OI, Walls, Magnets, M5 State

Usage:
    snapshot = snapshot_builder.build(
        candles_m5=candles_m5, candles_h1=candles_h1,
        binance_data=binance_data, regime_result=regime_result,
        current_price=current_price,
    )

    # Access: snapshot.atr_m5, snapshot.delta, snapshot.der, etc.
"""
from dataclasses import dataclass
from typing import Optional, Dict, Any
import pandas as pd
import numpy as np

from src.utils.logger import get_logger
from src.utils.decorators import retry, circuit_breaker, log_errors
from src.utils.metrics import timed_metric

logger = get_logger(__name__)


# v51.1 MOD-39: Swing Structure 9-Pattern Map
_SWING_STRUCTURE_MAP = {
    ('HH', 'HL'): 'BULLISH',
    ('LH', 'LL'): 'BEARISH',
    ('LH', 'HL'): 'COMPRESSION',
    ('HH', 'LL'): 'EXPANSION',
    ('EQ', 'HL'): 'ASCENDING_TRIANGLE',
    ('EQ', 'LL'): 'DESCENDING_FLAT',
    ('EQ', 'EQ'): 'TRUE_RANGE',
    ('HH', 'EQ'): 'RISING_FLAT',
    ('LH', 'EQ'): 'DESCENDING_TRIANGLE',
}


@dataclass
class MarketSnapshot:
    """Unified market indicators - calculated once per cycle."""

    # ATR (was calculated 5 times, now 1)
    atr_m5: float           # ATR(14) on M5
    atr_h1: float           # ATR(14) on H1 (from RegimeResult)
    atr_ratio: float        # ATR(5)/ATR(14) - volatility contraction

    # Delta / CVD (was calculated 4 times, now 1)
    delta: float            # net delta from trades
    cvd: float              # cumulative volume delta
    buy_volume: float
    sell_volume: float
    total_volume: float

    # DER (was not exported, now is)
    der: float              # |delta| / total_volume

    # v27.2: DER Persistence — วัดความยั่งยืนของ direction
    der_direction: str      # 'LONG' | 'SHORT' | 'NEUTRAL'
    der_persistence: int    # จำนวน candle ที่ DER ชี้ทางเดียวกันต่อเนื่อง (0-5)
    der_sustainability: str # 'LOADING' | 'EXHAUSTION' | 'LIKELY' | 'FADING' | 'TOO_EARLY' | 'NEUTRAL'

    # v27.3: M5 Market State (Efficiency Ratio + Volume)
    m5_efficiency: float    # 0-1: 0=pure sideway, 1=pure trend (Kaufman ER)
    m5_state: str           # 'SIDEWAY' | 'ACCUMULATION' | 'TRENDING' | 'EXHAUSTION' | 'PULLBACK' | 'RANGING' | 'RECOVERY'

    # v28.1: M5 EMA Position + Range + Candle Pattern
    m5_ema_position: str    # 'ABOVE_ALL' | 'BELOW_ALL' | 'BETWEEN'
    m5_range_high: float    # consolidation range high (0 if not sideway)
    m5_range_low: float     # consolidation range low (0 if not sideway)
    m5_candle_pattern: str  # 'HAMMER' | 'ENGULFING_BULL' | 'ENGULFING_BEAR' | 'MARUBOZU_BULL' | 'MARUBOZU_BEAR' | 'NONE'

    # Volume Ratio (was calculated 2 times, now 1)
    volume_ratio_m5: float  # last 5 candles vs avg 20 candles (micro)
    volume_24h: float       # 24h volume from Binance (macro)

    # Order Flow Summary
    imbalance: float
    imbalance_direction: str

    # Walls (fetch once, analyze once)
    wall_scan: dict         # raw_dominant, raw_ratio, bid_walls, ask_walls

    # OI
    oi: float
    oi_change_pct: float

    # Funding
    funding_rate: float

    # Magnets
    magnets: list

    # v42.8: Data Gathering (defaults must be at end)
    imbalance_avg_5m: float = 1.0
    wall_stability_sec: int = 0
    imbalance_min_5m: float = 1.0
    imbalance_max_5m: float = 1.0
    imbalance_std_5m: float = 0.0

    # v43.1: M5 Bias - composite direction field
    m5_bias: str = 'NEUTRAL'          # 'BULLISH' | 'BEARISH' | 'NEUTRAL'
    m5_bias_level: str = 'NEUTRAL'    # 'STRONG' | 'CONFIRMED' | 'EARLY' | 'NEUTRAL'
    m5_swing_structure: str = 'NEUTRAL'  # v51.1: BULLISH|BEARISH|COMPRESSION|EXPANSION|ASCENDING_TRIANGLE|DESCENDING_TRIANGLE|DESCENDING_FLAT|RISING_FLAT|TRUE_RANGE|NEUTRAL
    h1_swing_structure: str = 'NEUTRAL'  # v51.1: same patterns as M5
    
    # v51.2 MOD-40: EMA Reversal Hints
    m5_swing_ema_overextended: bool = False  # dist > 1.5× ATR
    m5_swing_reversal_hint: bool = False     # overextended + snap back
    h1_swing_ema_overextended: bool = False  # dist > 1.5× ATR
    h1_swing_reversal_hint: bool = False     # overextended + snap back
    
    m5_ema9: float = 0.0              # M5 EMA9 value (for forensic)
    m5_ema20: float = 0.0             # M5 EMA20 value (for forensic)
    m5_dist_pct: float = 0.0              # v53.1: distance from M5 EMA20 as % (+ = above, - = below)

    # v43.7: VP (Volume Profile) Fields
    vp_poc: float = 0.0                          # Composite POC
    vp_vah: float = 0.0                          # Value Area High
    vp_val: float = 0.0                          # Value Area Low
    vp_poc_session: float = 0.0                  # Current session POC
    vp_price_vs_va: str = 'INSIDE'               # ABOVE_VA | INSIDE | BELOW_VA
    vp_poc_distance_atr: float = 0.0             # Distance from POC in ATR units
    vp_nearest_hvn: float = 0.0                  # Nearest High Volume Node price
    vp_nearest_lvn: float = 0.0                  # Nearest Low Volume Node price
    vp_trigger_anchor: str = 'none'              # liquidity_sweep | volume_climax | none
    vp_poc_shift: float = 0.0                    # POC shift amount
    vp_poc_shift_direction: str = 'NEUTRAL'      # BULLISH | BEARISH | NEUTRAL
    
    # v64.0 MOD-66: Anti-Spoofing Wall Tracking
    prev_wall_ratio: float = 0.0                 # Previous cycle's wall ratio for shrinkage detection
    wall_shrink_pct: float = 0.0                # Percentage of wall reduction (0-100+)
    wall_zone_price: float = 0.0                # Current wall price for proximity check
    
    

    # v37.6: M5 state debug data (for forensic analysis)
    er_long: float = 0.0       # ER 20 candles
    er_short: float = 0.0      # ER 10 candles
    vol_rising: bool = False   # volume recent > prev * 1.1
    ema_slope: float = 0.0    # EMA20 slope / ATR
    net_long: float = 0.0     # net price move 20 candles
    net_short: float = 0.0     # net price move 10 candles
    atr_est: float = 0.0      # ATR estimate


class MarketSnapshotBuilder:
    """
    Build MarketSnapshot - calculates everything once per cycle.

    ATR:    Uses _calc_atr() method (single implementation)
    Delta:  Uses order_flow.calculate_delta() as authoritative source
            (uses real trades from WebSocket, not candle aggregates)
    DER:    Calculated from delta/volume from above
    Volume: Separated micro (M5) vs macro (24h) clearly
    Walls:  Fetched from binance_data (already fetched)
    OI:     Fetched from binance_data
    """

    def __init__(self, order_flow_analyzer, ict_analyzer):
        self.order_flow = order_flow_analyzer
        self.ict = ict_analyzer
        # v29.1: Instance-level state for range persistence (C1)
        self._prev_m5_state = 'RANGING'
        self._last_range_high = 0.0
        self._last_range_low = 0.0
        self._range_persist_count = 0
        # v38.1: State hold timer — require 2 consecutive same state
        self._pending_state = 'RANGING'
        self._pending_count = 0
        # v38.5: Candle-close-only M5 state calculation
        self._last_candle_time = None
        self._cached_efficiency = 0.5
        # v38.7: M5-native RECOVERY detection — DER from snapshot
        self._current_der = 0.0
        self._current_der_direction = 'NEUTRAL'

    @log_errors
    @timed_metric("MarketSnapshotBuilder.build")
    @retry(max_attempts=3, delay=0.1, backoff=2.0, exceptions=(Exception,))
    @circuit_breaker(failure_threshold=5, timeout=30.0, expected_exception=Exception)
    def build(self, candles_m5: pd.DataFrame, candles_h1: pd.DataFrame,
               binance_data: dict, regime_result, current_price: float) -> MarketSnapshot:
        """
        Build MarketSnapshot with all indicators.

        Args:
            candles_m5: M5 OHLCV data
            candles_h1: H1 OHLCV data
            binance_data: dict with trades, bids, asks, oi, wall_scan, etc.
            regime_result: RegimeResult from MarketRegimeDetector (has atr_h1)
            current_price: Current BTC price

        Returns:
            MarketSnapshot with all indicators calculated once
        """
        try:
            # === 1. ATR - calculated once ===
            atr_m5 = self._calc_atr(candles_m5, 14)
            atr_ratio = self._calc_atr_ratio(candles_m5)

            # === 2. Delta/CVD - authoritative source: order_flow from real trades ===
            trades = binance_data.get('trades', [])
            # v42.2: Fix - bids/asks stored in order_book key, not directly in binance_data
            order_book = binance_data.get('order_book', {})
            bids = order_book.get('bids', {}) if isinstance(order_book.get('bids'), dict) else order_book.get('bids', [])
            asks = order_book.get('asks', {}) if isinstance(order_book.get('asks'), dict) else order_book.get('asks', [])
            oi = binance_data.get('oi', 0)
            prev_oi = binance_data.get('oi_1min_ago', oi)

            of_summary = self.order_flow.get_order_flow_summary(
                bids=bids, asks=asks, trades=trades,
                price=current_price, open_interest=oi, prev_oi=prev_oi
            )

            delta = of_summary.get('delta', 0)
            total_vol = of_summary.get('total_volume', 0)

            # === 3. DER - calculated from delta/volume ===
            der = abs(delta / total_vol) if total_vol > 0 else 0

            # === 3b. DER Persistence + Sustainability (v27.2) ===
            der_persistence, der_direction = self._calc_der_persistence(candles_m5)
            der_sustainability = self._calc_der_sustainability(oi, prev_oi, candles_m5, der_persistence)

            # v38.7: Store DER for M5-native RECOVERY detection
            self._current_der = der
            self._current_der_direction = der_direction
            self._current_der_persistence = der_persistence  # v46.0: Store for MOD-14 override

            # === 3c. M5 Market State — Efficiency Ratio + Volume (v27.3) ===
            m5_efficiency, m5_state = self._calc_m5_state(candles_m5)

            # === 3d. M5 EMA Position + Range + Candle Pattern (v28.1) ===
            m5_ema_position = self._calc_m5_ema_position(candles_m5)
            m5_range_high, m5_range_low = self._calc_m5_range(candles_m5, m5_state)
            m5_candle_pattern = self._calc_m5_candle_pattern(candles_m5)

            # === v43.1: M5 Bias - composite direction ===
            m5_bias, m5_bias_level, m5_ema9, m5_ema20 = self._calc_m5_bias(candles_m5, m5_state)
            m5_dist_pct = ((current_price - m5_ema20) / m5_ema20 * 100) if m5_ema20 > 0 else 0.0

            # === v50.5 MOD-35: M5 Swing Structure (v51.2 MOD-40: returns tuple) ===
            # v53.1: Only use pattern from swing — overextended uses authoritative EMA20
            m5_swing_structure, _, _ = self._calc_swing_structure(candles_m5)
            h1_swing_structure, _, _ = self._calc_swing_structure(candles_h1)

            # Overextended: use SAME EMA20 as m5_dist_pct (from _calc_m5_bias)
            m5_swing_ema_overextended = abs(current_price - m5_ema20) > atr_m5 * 1.5 if m5_ema20 > 0 else False

            # H1: need h1_ema20 — get from h1_bias_result or calculate
            h1_ema20_val = 0
            if candles_h1 is not None and len(candles_h1) >= 20:
                h1_ema20_val = float(candles_h1['close'].ewm(span=20, adjust=False).mean().iloc[-1])
            h1_atr = regime_result.atr_h1 if regime_result else 200
            h1_swing_ema_overextended = abs(current_price - h1_ema20_val) > h1_atr * 1.5 if h1_ema20_val > 0 else False

            # Reversal hint: overextended + snap back (use price-based swing data)
            m5_swing_reversal_hint = False
            h1_swing_reversal_hint = False
            # Hints will be recalculated if needed — for now disabled until swing algorithm is redesigned

            # === v43.7: VP (Volume Profile) Fields ===
            vp_poc, vp_vah, vp_val, vp_poc_session, vp_price_vs_va, vp_poc_dist_atr, \
                vp_nearest_hvn, vp_nearest_lvn, vp_trigger_anchor, vp_poc_shift, vp_poc_shift_dir = \
                self._calc_vp_fields(binance_data, current_price, atr_m5)

            # === 4. Volume ratio (micro: M5) ===
            volume_ratio_m5 = self._calc_volume_ratio_m5(candles_m5)

            # === 5. Walls - from binance_data (already fetched) ===
            wall_scan = binance_data.get('wall_scan', {})

            # === 6. Magnets ===
            magnets = self.ict.get_active_magnets(candles_m5, current_price)

            # v42.2: Update Imbalance History (Data Gathering Phase)
            current_imbalance = of_summary.get('imbalance', 1.0)
            self.order_flow.imbalance_history.append(current_imbalance)
            if len(self.order_flow.imbalance_history) > 20: # 5 mins @ 15s interval
                self.order_flow.imbalance_history.pop(0)
            
            history = self.order_flow.imbalance_history
            if history:
                imbalance_avg_5m = sum(history) / len(history)
                imbalance_min_5m = min(history)
                imbalance_max_5m = max(history)
                imbalance_std_5m = (sum((x - imbalance_avg_5m)**2 for x in history) / len(history)) ** 0.5
            else:
                imbalance_avg_5m = imbalance_min_5m = imbalance_max_5m = 1.0
                imbalance_std_5m = 0.0
            
            # Wall Stability (Identify largest wall price)
            # v42.8: Find the price level with the highest volume in top 10
            wall_scan = binance_data.get('wall_scan', {})
            wall_price = 0
            if bids and asks:
                try:
                    # Get top 10 levels
                    top_bids = bids[:10]
                    top_asks = asks[:10]
                    
                    max_bid_vol = 0
                    max_bid_price = 0
                    for b in top_bids:
                        vol = b[1] if isinstance(b, (list, tuple)) else b.get('quantity', 0)
                        prc = b[0] if isinstance(b, (list, tuple)) else b.get('price', 0)
                        if vol > max_bid_vol:
                            max_bid_vol = vol
                            max_bid_price = prc
                            
                    max_ask_vol = 0
                    max_ask_price = 0
                    for a in top_asks:
                        vol = a[1] if isinstance(a, (list, tuple)) else a.get('quantity', 0)
                        prc = a[0] if isinstance(a, (list, tuple)) else a.get('price', 0)
                        if vol > max_ask_vol:
                            max_ask_vol = vol
                            max_ask_price = prc
                            
                    wall_price = max_bid_price if max_bid_vol > max_ask_vol else max_ask_price
                except (IndexError, KeyError, TypeError):
                    wall_price = 0
            
            if not hasattr(self.order_flow, 'last_wall_price'): self.order_flow.last_wall_price = 0
            if not hasattr(self.order_flow, 'wall_start_time'): self.order_flow.wall_start_time = 0

            import time
            # v52.0: Institutional wall zone tolerance ($20 BTC) + ratio check
            WALL_ZONE_TOLERANCE = 20  # $20 — institutional standard for BTC order book noise
            wall_ratio = wall_scan.get('raw_ratio', 0)
            
            # v64.0 MOD-66: Track previous wall_ratio for shrinkage detection
            if not hasattr(self, '_prev_wall_ratio'):
                self._prev_wall_ratio = 0.0
            
            # Calculate wall shrinkage percentage
            if self._prev_wall_ratio > 0 and wall_ratio > 0:
                wall_shrink_pct = ((self._prev_wall_ratio - wall_ratio) / self._prev_wall_ratio) * 100
            else:
                wall_shrink_pct = 0.0

            if wall_price > 0 and \
               abs(wall_price - self.order_flow.last_wall_price) <= WALL_ZONE_TOLERANCE and \
               wall_ratio >= 2.0:
                # Wall still in same zone + still strong → stability continues
                wall_stability_sec = int(time.time() - self.order_flow.wall_start_time)
            else:
                # Wall moved to new zone OR ratio dropped → reset
                self.order_flow.last_wall_price = wall_price
                self.order_flow.wall_start_time = time.time()
                wall_stability_sec = 0
            
            # Update prev_wall_ratio for next cycle
            self._prev_wall_ratio = wall_ratio

            # === Build result ===
            snapshot = MarketSnapshot(
                atr_m5=round(atr_m5, 2),
                atr_h1=regime_result.atr_h1 if regime_result else 0,
                atr_ratio=round(atr_ratio, 2),
                delta=round(delta, 4),
                cvd=round(of_summary.get('cvd_delta', 0), 4),
                buy_volume=of_summary.get('buy_volume', 0),
                sell_volume=of_summary.get('sell_volume', 0),
                total_volume=total_vol,
                der=round(der, 4),
                der_direction=der_direction,
                der_persistence=der_persistence,
                der_sustainability=der_sustainability,
                m5_efficiency=round(m5_efficiency, 2),
                m5_state=m5_state,
                m5_ema_position=m5_ema_position,
                m5_range_high=round(m5_range_high, 2),
                m5_range_low=round(m5_range_low, 2),
                m5_candle_pattern=m5_candle_pattern,
                volume_ratio_m5=round(volume_ratio_m5, 2),
                volume_24h=binance_data.get('volume', 0),
                imbalance=of_summary.get('imbalance', 0),
                imbalance_direction=of_summary.get('imbalance_direction', 'NEUTRAL'),
                wall_scan=wall_scan,
                oi=oi,
                oi_change_pct=of_summary.get('oi_change_pct', 0),
                funding_rate=binance_data.get('funding_rate', 0),
                magnets=magnets,
                imbalance_avg_5m=round(imbalance_avg_5m, 2),
                wall_stability_sec=wall_stability_sec,
                imbalance_min_5m=round(imbalance_min_5m, 2),
                imbalance_max_5m=round(imbalance_max_5m, 2),
                imbalance_std_5m=round(imbalance_std_5m, 2),
                # v43.1: M5 Bias
                m5_bias=m5_bias,
                m5_bias_level=m5_bias_level,
                m5_ema9=round(m5_ema9, 2),
                m5_ema20=round(m5_ema20, 2),
                m5_dist_pct=round(m5_dist_pct, 4),
                # v50.5 MOD-35: M5 Swing Structure
                m5_swing_structure=m5_swing_structure,
                # v51.2 MOD-40: EMA Reversal Hints
                m5_swing_ema_overextended=m5_swing_ema_overextended,
                m5_swing_reversal_hint=m5_swing_reversal_hint,
                # MOD-35 H1: H1 Swing Structure
                h1_swing_structure=h1_swing_structure,
                # v51.2 MOD-40: EMA Reversal Hints
                h1_swing_ema_overextended=h1_swing_ema_overextended,
                h1_swing_reversal_hint=h1_swing_reversal_hint,
                # v43.7: VP Fields
                vp_poc=round(vp_poc, 2) if vp_poc else 0.0,
                vp_vah=round(vp_vah, 2) if vp_vah else 0.0,
                vp_val=round(vp_val, 2) if vp_val else 0.0,
                vp_poc_session=round(vp_poc_session, 2) if vp_poc_session else 0.0,
                vp_price_vs_va=vp_price_vs_va,
                vp_poc_distance_atr=round(vp_poc_dist_atr, 2),
                vp_nearest_hvn=round(vp_nearest_hvn, 2) if vp_nearest_hvn else 0.0,
                vp_nearest_lvn=round(vp_nearest_lvn, 2) if vp_nearest_lvn else 0.0,
                vp_trigger_anchor=vp_trigger_anchor,
                vp_poc_shift=vp_poc_shift,
                vp_poc_shift_direction=vp_poc_shift_dir,
                
                # v64.0 MOD-66: Anti-Spoofing Wall Tracking
                prev_wall_ratio=round(wall_ratio, 2),
                wall_shrink_pct=round(wall_shrink_pct, 1),
                wall_zone_price=round(wall_price, 2),
                
                # v37.6: M5 state debug data
                er_long=self._m5_debug.get('er_long', 0),
                er_short=self._m5_debug.get('er_short', 0),
                vol_rising=self._m5_debug.get('vol_rising', False),
                ema_slope=self._m5_debug.get('ema_slope', 0),
                net_long=self._m5_debug.get('net_long', 0),
                net_short=self._m5_debug.get('net_short', 0),
                atr_est=self._m5_debug.get('atr_est', 0),
            )

            logger.debug(
                f"[Snapshot] ATR:{snapshot.atr_m5:.0f} | Delta:{snapshot.delta:+.1f} | "
                f"DER:{snapshot.der:.3f} | Vol:{snapshot.volume_ratio_m5:.1f}x | "
                f"OI:{snapshot.oi_change_pct:+.2f}%"
            )

            return snapshot

        except Exception as e:
            logger.error(f"[Snapshot] Build error: {e}", exc_info=True)
            return self._empty_snapshot()

    def refine_m5_state(self, snapshot: 'MarketSnapshot', candles_m5: pd.DataFrame, h1_bias: str = 'NEUTRAL') -> 'MarketSnapshot':
        """
        v29.1: 2-pass refinement — called after H1 bias is computed.
        v38.1: skip_hold_timer=True to avoid double-counting state transitions.
        v38.5: Force recalc (temporarily clear cache).
        v38.7: h1_bias parameter kept for backward compat — no longer used in state logic.
        """
        # v38.5: Temporarily clear cache to force recalc
        saved_time = self._last_candle_time
        self._last_candle_time = None
        _, new_state = self._calc_m5_state(candles_m5, h1_bias, skip_hold_timer=True)
        self._last_candle_time = saved_time  # Restore cache
        if new_state != snapshot.m5_state:
            logger.info(f"[Snapshot] M5 State refined: {snapshot.m5_state} → {new_state}")
            snapshot.m5_state = new_state
        return snapshot

    def _calc_atr(self, candles: pd.DataFrame, period: int = 14) -> float:
        """
        ATR calculation - single implementation for entire system.

        ATR = Average of True Range over period
        True Range = max(High-Low, |High-PrevClose|, |Low-PrevClose|)
        """
        if candles is None or len(candles) < period + 1:
            return 100.0  # Safe default for BTC

        high = candles['high'].values
        low = candles['low'].values
        close = candles['close'].values

        # True Range
        tr1 = high - low
        tr2 = np.abs(high - np.roll(close, 1))
        tr3 = np.abs(low - np.roll(close, 1))

        # Fix first element
        tr2[0] = tr1[0]
        tr3[0] = tr1[0]

        tr = np.maximum(tr1, np.maximum(tr2, tr3))

        # Average
        atr = pd.Series(tr).rolling(window=period).mean().iloc[-1]
        return float(atr) if not np.isnan(atr) else 100.0


    def _calc_atr_ratio(self, candles: pd.DataFrame, short_period: int = 5, long_period: int = 14) -> float:
        """v42.1: Calculate ATR ratio = ATR(short) / ATR(long) for contraction detection."""
        if candles is None or len(candles) < long_period + 1:
            return 1.0
        atr_short = self._calc_atr(candles, short_period)
        atr_long = self._calc_atr(candles, long_period)
        return atr_short / atr_long if atr_long > 0 else 1.0

    def _calc_volume_ratio_m5(self, candles_m5: pd.DataFrame, short: int = 5, long: int = 20) -> float:
        """
        Volume ratio = avg volume last 5 bars / avg volume last 20 bars.

        Micro timeframe ratio - shows recent volume spike/drop.
        """
        if candles_m5 is None or len(candles_m5) < long:
            return 1.0

        volumes = candles_m5['volume'].values

        avg_short = np.mean(volumes[-short:])
        avg_long = np.mean(volumes[-long:])

        if avg_long == 0:
            return 1.0

        return avg_short / avg_long

    def _calc_m5_state(self, candles_m5: pd.DataFrame, h1_bias: str = 'NEUTRAL', skip_hold_timer: bool = False) -> tuple:
        """v38.7: Pure M5-native state calculation — no H1 bias dependency.

        States: TRENDING, EXHAUSTION, ACCUMULATION, SIDEWAY, RANGING, PULLBACK, RECOVERY
        Removed: CAUTION (merged into RANGING), H1 bias dependency from PULLBACK/RECOVERY
        v39.2: ACCUMULATION timeout — recalc every 60s instead of caching full candle
        """
        if candles_m5 is None or len(candles_m5) < 21:
            return 0.5, 'RANGING'

        # v38.5: Only recalculate when new candle closes
        # v39.2: EXCEPTION for ACCUMULATION — recalc every 60s to prevent stale blocks
        last_candle_time = candles_m5.iloc[-1].name
        is_accumulation_cached = (
            self._prev_m5_state == 'ACCUMULATION' and
            self._last_candle_time is not None and
            last_candle_time == self._last_candle_time
        )

        if is_accumulation_cached:
            # Check if 60 seconds have passed since last calc
            import time
            current_time = time.time()
            if not hasattr(self, '_last_accumulation_check'):
                self._last_accumulation_check = 0
            if current_time - self._last_accumulation_check < 60:
                # Still within 60s window — return cached
                return self._cached_efficiency, self._prev_m5_state
            # 60s passed — force recalc to check if ACCUMULATION still valid
            self._last_accumulation_check = current_time

        if self._last_candle_time is not None and last_candle_time == self._last_candle_time:
            # === v46.0: MOD-12 ATR-Triggered Intra-candle Recalc ===
            current_price = float(candles_m5.iloc[-1]['close'])
            current_open = float(candles_m5.iloc[-1]['open'])
            atr_est = float(np.mean(np.abs(np.diff(candles_m5['close'].values[-14:])))) if len(candles_m5) >= 15 else 100.0
            
            if abs(current_price - current_open) > 0.85 * atr_est:
                # Force recalculation (Bypass cache)
                pass 
            else:
                return self._cached_efficiency, self._prev_m5_state

        self._last_candle_time = last_candle_time

        closes_long = candles_m5['close'].values[-21:]
        closes_short = candles_m5['close'].values[-11:]

        def _er(closes_arr):
            net = abs(float(closes_arr[-1]) - float(closes_arr[0]))
            total = sum(abs(float(closes_arr[i+1]) - float(closes_arr[i])) for i in range(len(closes_arr)-1))
            return net / total if total > 0 else 0.5

        er_long = _er(closes_long)
        er_short = _er(closes_short)

        # Volume: gradual ratio (not binary)
        vols = candles_m5['volume'].values
        if len(vols) >= 10:
            vol_recent = float(np.mean(vols[-5:]))
            vol_prev = float(np.mean(vols[-10:-5]))
            vol_ratio = vol_recent / vol_prev if vol_prev > 0 else 1.0
        else:
            vol_ratio = 1.0

        # EMA slope
        ema20_vals = pd.Series(candles_m5['close'].values).ewm(span=20, adjust=False).mean().values
        atr_est = float(np.mean(np.abs(np.diff(candles_m5['close'].values[-14:])))) if len(candles_m5) >= 15 else 100.0
        ema_slope = (ema20_vals[-1] - ema20_vals[-3]) / atr_est if atr_est > 0 and len(ema20_vals) >= 4 else 0

        net_long = abs(float(closes_long[-1]) - float(closes_long[0]))
        net_short = abs(float(closes_short[-1]) - float(closes_short[0]))

        # === v38.1: New classification — er_short primary ===
        if er_short >= 0.45 and er_long >= 0.30:
            state = 'TRENDING'
        elif er_short >= 0.45 and er_long < 0.30:
            state = 'EXHAUSTION' if vol_ratio < 0.9 else 'TRENDING'
        elif er_short < 0.15:
            state = 'ACCUMULATION' if vol_ratio > 1.2 else 'SIDEWAY'
        elif er_long >= 0.40:
            state = 'PULLBACK' if abs(ema_slope) > 0.1 else 'RANGING'
        elif er_short >= 0.25:
            state = 'RANGING'
        else:
            state = 'SIDEWAY'

        # === C4: PULLBACK detection (v38.7: M5-native, no H1 bias) ===
        # PULLBACK = strong trend (ER long high) + EMA slope confirms + price consolidating
        if state in ('SIDEWAY', 'RANGING'):
            if er_long >= 0.35 and abs(ema_slope) > 0.15:
                state = 'PULLBACK'

        # === v38.7: RECOVERY — post-pullback consolidation with trend intent ===
        # After consolidation, if DER has direction + volume building + ER shows direction
        # → RECOVERY (institutional building position), not TRUE SIDEWAY
        if state in ('SIDEWAY', 'RANGING'):
            has_der_direction = self._current_der_direction in ('LONG', 'SHORT') and self._current_der > 0.3
            vol_building = vol_ratio > 1.0
            er_long_some_direction = er_long >= 0.15

            if has_der_direction and vol_building and er_long_some_direction:
                state = 'RECOVERY'

        # === v38.1: State hold timer — require 2 consecutive same state ===
        if not skip_hold_timer:
            if state != self._prev_m5_state:
                # === v46.0: MOD-14 Institutional Override ===
                is_override = False
                if state == 'TRENDING':
                    c_der = getattr(self, '_current_der', 0.0)
                    c_pers = getattr(self, '_current_der_persistence', 0)
                    if vol_ratio > 1.5 and (c_pers >= 2 or abs(c_der) > 0.4):
                        is_override = True

                if is_override:
                    self._pending_state = state
                    self._pending_count = 2  # Bypass hold timer
                else:
                    if state == self._pending_state:
                        self._pending_count += 1
                    else:
                        self._pending_state = state
                        self._pending_count = 1

                if self._pending_count < 2:
                    state = self._prev_m5_state  # hold previous state
                else:
                    self._pending_count = 0  # confirmed, reset

        self._m5_debug = {
            'er_long': round(er_long, 3),
            'er_short': round(er_short, 3),
            'vol_ratio': round(vol_ratio, 2),
            'vol_rising': vol_ratio > 1.1,  # v38.1: derived from vol_ratio for backward compat
            'ema_slope': round(ema_slope, 3),
            'net_long': round(net_long, 1),
            'net_short': round(net_short, 1),
            'atr_est': round(atr_est, 1),
        }

        self._prev_m5_state = state
        return round(er_short, 2), state  # v38.1: return er_short as efficiency

    def _calc_der_persistence(self, candles_m5: pd.DataFrame, der_threshold: float = 0.3) -> tuple:
        """
        v27.2: นับ candle ที่ DER > threshold ชี้ทางเดียวกันต่อเนื่อง.
        Returns: (count: int, direction: str)
        """
        if candles_m5 is None or len(candles_m5) < 3:
            return 0, 'NEUTRAL'

        count = 0
        direction = 'NEUTRAL'

        for i in range(-1, -6, -1):  # last 5 candles
            if abs(i) > len(candles_m5):
                break
            c = candles_m5.iloc[i]
            vol = float(c['volume'])
            if vol <= 0:
                break
            candle_delta = vol if c['close'] > c['open'] else -vol if c['close'] < c['open'] else 0
            candle_der = abs(candle_delta) / vol
            candle_dir = 'LONG' if candle_delta > 0 else 'SHORT' if candle_delta < 0 else 'NEUTRAL'

            if candle_der >= der_threshold and candle_dir != 'NEUTRAL':
                if count == 0:
                    direction = candle_dir
                    count = 1
                elif candle_dir == direction:
                    count += 1
                else:
                    break  # direction changed
            else:
                break  # DER too low

        return count, direction

    def _calc_der_sustainability(self, oi: float, oi_prev: float,
                                 candles_m5: pd.DataFrame, der_persistence: int) -> str:
        """
        v27.2: DER + OI + Volume = sustainable direction?
        Priority: OI > Volume trend (fallback when OI=0)
        """
        # MOD-18: Faster detection (2 candles = 10m is enough for M5 scalp)
        if der_persistence < 2:
            return 'TOO_EARLY'

        # 1. Try OI first
        if oi > 0 and oi_prev > 0 and oi != oi_prev:
            oi_change = (oi - oi_prev) / oi_prev
            if abs(oi_change) > 0.0005:  # v45.0: More sensitive (0.001 -> 0.0005)
                return 'LOADING' if oi_change > 0 else 'EXHAUSTION'

        # 2. Fallback: Volume trend (last 3 vs previous 3)
        if candles_m5 is not None and len(candles_m5) >= 6:
            vol_recent = float(candles_m5['volume'].iloc[-3:].mean())
            vol_prev = float(candles_m5['volume'].iloc[-6:-3].mean())
            if vol_prev > 0:
                vol_ratio = vol_recent / vol_prev
                if vol_ratio > 1.2:
                    return 'LIKELY'   # volume increasing → probably sustainable
                elif vol_ratio < 0.7:
                    return 'FADING'   # volume declining → losing steam

        return 'NEUTRAL'

    def _calc_m5_ema_position(self, candles_m5: pd.DataFrame) -> str:
        """v28.1: Price position relative to M5 EMAs."""
        if candles_m5 is None or len(candles_m5) < 20:
            return 'BETWEEN'
        price = float(candles_m5['close'].iloc[-1])
        ema9 = float(candles_m5['close'].ewm(span=9, adjust=False).mean().iloc[-1])
        ema20 = float(candles_m5['close'].ewm(span=20, adjust=False).mean().iloc[-1])
        if price > ema9 and price > ema20:
            return 'ABOVE_ALL'
        elif price < ema9 and price < ema20:
            return 'BELOW_ALL'
        return 'BETWEEN'

    def _calc_m5_bias(self, candles_m5: pd.DataFrame, m5_state: str) -> tuple:
        """
        v43.1: M5 Bias — composite direction from EMA alignment + position + slope.
        
        Returns: (bias, level, ema9, ema20)
        """
        if candles_m5 is None or len(candles_m5) < 21:
            return 'NEUTRAL', 'NEUTRAL', 0.0, 0.0
        
        price = float(candles_m5['close'].iloc[-1])
        ema9 = float(candles_m5['close'].ewm(span=9, adjust=False).mean().iloc[-1])
        ema20 = float(candles_m5['close'].ewm(span=20, adjust=False).mean().iloc[-1])
        
        # Calculate slope from recent candles
        if len(candles_m5) >= 10:
            ema20_series = candles_m5['close'].ewm(span=20, adjust=False).mean()
            slope = (ema20_series.iloc[-1] - ema20_series.iloc[-5]) / 5
        else:
            slope = 0.0
        
        # Score components (3 components, need ≥2 to confirm)
        bull = 0
        bear = 0
        
        # 1. EMA alignment (EMA9 > EMA20 or <)
        if ema9 > ema20:
            bull += 1
        elif ema9 < ema20:
            bear += 1
        
        # 2. Price position (price > EMA9 or <)
        if price > ema9:
            bull += 1
        elif price < ema9:
            bear += 1
        
        # 3. Slope direction
        if slope > 0.1:
            bull += 1
        elif slope < -0.1:
            bear += 1
        
        # Determine bias
        if bull >= 2:
            bias = 'BULLISH'
            if bull == 3 and m5_state == 'TRENDING':
                level = 'STRONG'
            elif bull == 3:
                level = 'CONFIRMED'
            else:
                level = 'EARLY'
        elif bear >= 2:
            bias = 'BEARISH'
            if bear == 3 and m5_state == 'TRENDING':
                level = 'STRONG'
            elif bear == 3:
                level = 'CONFIRMED'
            else:
                level = 'EARLY'
        else:
            bias = 'NEUTRAL'
            level = 'NEUTRAL'
        
        return bias, level, ema9, ema20

    def _calc_swing_structure(self, candles_m5: pd.DataFrame) -> tuple:
        """
        v51.2 MOD-40: Swing Structure 9-Pattern + Price-Based Comparison + EMA Reversal Hint.
        
        Fix 1: Compare prices (not EMA dist)
        Fix 2: Adjusted thresholds (min 0.5× ATR, sub-swing 1.2× ATR)
        Fix 3: Return reversal hints for early warning
        
        Returns: (pattern: str, ema_overextended: bool, reversal_hint: bool)
        """
        try:
            if candles_m5 is None or len(candles_m5) < 81:
                return 'NEUTRAL', False, False

            close = candles_m5['close'].values[-80:]
            ema20 = candles_m5['close'].ewm(span=20, adjust=False).mean().values[-80:]
            atr = self._calc_atr(candles_m5, 14)
            if atr <= 0:
                return 'NEUTRAL', False, False
            
            # v51.2 MOD-40: Adjusted thresholds
            min_threshold = 0.5 * atr      # was 0.3× ATR
            sub_swing_threshold = 1.2 * atr  # was 0.7× ATR

            dist = close - ema20
            above = dist >= 0
            
            # Store both dist (for threshold) and price (for comparison)
            swing_lows = []  # [{'dist': float, 'price': float}, ...]
            swing_highs = [] # [{'dist': float, 'price': float}, ...]

            i = 0
            while i < len(dist):
                if above[i]:
                    # Above EMA20 cycle
                    cycle_max_dist = dist[i]
                    cycle_max_price = close[i]
                    while i < len(dist) and above[i]:
                        if dist[i] > cycle_max_dist:
                            cycle_max_dist = dist[i]
                            cycle_max_price = close[i]

                        # Layer 2: Sub-swing detection (1.2× ATR threshold)
                        if cycle_max_dist > min_threshold and (cycle_max_dist - dist[i]) > sub_swing_threshold:
                            swing_highs.append({'dist': cycle_max_dist, 'price': cycle_max_price})
                            cycle_max_dist = dist[i]
                            cycle_max_price = close[i]

                        i += 1

                    # Layer 1: EMA20 crossing — end cycle
                    if cycle_max_dist > min_threshold:
                        swing_highs.append({'dist': cycle_max_dist, 'price': cycle_max_price})
                else:
                    # Below EMA20 cycle
                    cycle_min_dist = dist[i]
                    cycle_min_price = close[i]
                    while i < len(dist) and not above[i]:
                        if dist[i] < cycle_min_dist:
                            cycle_min_dist = dist[i]
                            cycle_min_price = close[i]

                        # Layer 2: Sub-swing detection
                        if abs(cycle_min_dist) > min_threshold and (dist[i] - cycle_min_dist) > sub_swing_threshold:
                            swing_lows.append({'dist': cycle_min_dist, 'price': cycle_min_price})
                            cycle_min_dist = dist[i]
                            cycle_min_price = close[i]

                        i += 1

                    # Layer 1: EMA20 crossing — end cycle
                    if abs(cycle_min_dist) > min_threshold:
                        swing_lows.append({'dist': cycle_min_dist, 'price': cycle_min_price})

            # Need at least 2 of each to compare
            if len(swing_lows) < 2 or len(swing_highs) < 2:
                return 'NEUTRAL', False, False

            # v51.2 MOD-40: Compare using PRICE (not EMA dist)
            eq_threshold = 0.3 * atr
            h_diff = swing_highs[-1]['price'] - swing_highs[-2]['price']
            l_diff = swing_lows[-1]['price'] - swing_lows[-2]['price']

            h_status = 'HH' if h_diff > eq_threshold else 'LH' if h_diff < -eq_threshold else 'EQ'
            l_status = 'HL' if l_diff > eq_threshold else 'LL' if l_diff < -eq_threshold else 'EQ'

            # Map to 9-pattern structure
            key = (h_status, l_status)
            pattern = _SWING_STRUCTURE_MAP.get(key, 'NEUTRAL')

            # v53.0: Use CURRENT price distance from EMA20 (not last swing dist)
            # last_swing_dist was stale — showed overextended even when price near EMA
            current_dist = abs(close[-1] - ema20[-1])
            ema_overextended = current_dist > atr * 1.5
            
            # Reversal hint: overextended + snap back toward EMA
            reversal_hint = False
            if ema_overextended:
                if pattern in ('BEARISH', 'DESCENDING_TRIANGLE', 'DESCENDING_FLAT'):
                    # Bearish pattern but last low is higher → may reverse up
                    if swing_lows[-1]['price'] > swing_lows[-2]['price']:
                        reversal_hint = True
                elif pattern in ('BULLISH', 'ASCENDING_TRIANGLE', 'RISING_FLAT'):
                    # Bullish pattern but last high is lower → may reverse down
                    if swing_highs[-1]['price'] < swing_highs[-2]['price']:
                        reversal_hint = True
            
            # v51.2 DEBUG: Log swing structure for debugging
            logger.debug(f"[Swing] Pattern:{pattern} | Overextended:{ema_overextended} | ReversalHint:{reversal_hint}")

            return pattern, ema_overextended, reversal_hint
        except Exception as e:
            logger.warning(f"[Snapshot] _calc_swing_structure error: {e}")
            return 'NEUTRAL', False, False

    def _calc_vp_fields(self, binance_data: dict, current_price: float, atr_m5: float) -> tuple:
        """v43.7: Calculate VP fields from FRVP data."""
        frvp = binance_data.get('frvp_data') or binance_data.get('frvp')
        if not frvp:
            return 0.0, 0.0, 0.0, 0.0, 'INSIDE', 0.0, 0.0, 0.0, 'none', 0.0, 'NEUTRAL'
        
        comp = frvp.get('composite', {})
        layers = frvp.get('layers', {})
        swing = layers.get('swing_anchored', {})
        current = layers.get('current_session', {})
        trigger = layers.get('trigger', {})
        
        # v50.8: Use swing_anchored POC/VAH/VAL (matches TradingView AVP)
        poc = swing.get('poc', 0) or comp.get('poc', 0) or 0.0
        vah = swing.get('vah', 0) or comp.get('vah', 0) or 0.0
        val = swing.get('val', 0) or comp.get('val', 0) or 0.0
        poc_session = current.get('poc', 0) or 0.0
        
        # Price vs Value Area
        if current_price > vah:
            price_vs_va = 'ABOVE_VA'
        elif current_price < val:
            price_vs_va = 'BELOW_VA'
        else:
            price_vs_va = 'INSIDE'
        
        # POC distance in ATR
        poc_dist_atr = abs(current_price - poc) / atr_m5 if atr_m5 > 0 and poc else 0.0
        
        # Nearest HVN/LVN
        hvn_list = swing.get('hvn', [])
        lvn_list = swing.get('lvn', [])
        
        nearest_hvn = 0.0
        if hvn_list:
            nearest_hvn = min(hvn_list, key=lambda h: abs(h['price'] - current_price))['price']
        
        nearest_lvn = 0.0
        if lvn_list:
            nearest_lvn = min(lvn_list, key=lambda l: abs(l['price'] - current_price))['price']
        
        # Trigger anchor
        trigger_anchor = trigger.get('anchor_type', 'none') or 'none'
        
        # POC shift from frvp_data
        poc_shift = frvp.get('poc_shift', 0.0) or 0.0
        poc_shift_dir = frvp.get('poc_shift_direction', 'NEUTRAL') or 'NEUTRAL'
        
        return poc, vah, val, poc_session, price_vs_va, poc_dist_atr, \
               nearest_hvn, nearest_lvn, trigger_anchor, poc_shift, poc_shift_dir

    def _calc_m5_range(self, candles_m5: pd.DataFrame, m5_state: str) -> tuple:
        """v29.1: Consolidation range boundaries. Persists 3 candles after SIDEWAY exit (C1)."""
        if candles_m5 is None or len(candles_m5) < 10:
            return 0.0, 0.0
        if m5_state not in ('SIDEWAY', 'ACCUMULATION', 'RANGING', 'RECOVERY'):
            # Range persist: keep old range for 3 candles after SIDEWAY exit
            if self._last_range_high > 0 and self._range_persist_count < 3:
                self._range_persist_count += 1
                return self._last_range_high, self._last_range_low
            self._last_range_high = 0.0
            self._last_range_low = 0.0
            self._range_persist_count = 0
            return 0.0, 0.0
        self._range_persist_count = 0  # reset when in SIDEWAY
        last10 = candles_m5.iloc[-10:]
        rng_high = float(last10['high'].max())
        rng_low = float(last10['low'].min())
        self._last_range_high = rng_high
        self._last_range_low = rng_low
        return rng_high, rng_low

    def _calc_m5_candle_pattern(self, candles_m5: pd.DataFrame) -> str:
        """v28.1: Detect M5 candle pattern from last 2 candles."""
        if candles_m5 is None or len(candles_m5) < 3:
            return 'NONE'
        prev = candles_m5.iloc[-2]
        curr = candles_m5.iloc[-1]
        o_c, h_c, l_c, c_c = float(curr['open']), float(curr['high']), float(curr['low']), float(curr['close'])
        o_p, h_p, l_p, c_p = float(prev['open']), float(prev['high']), float(prev['low']), float(prev['close'])
        rng = h_c - l_c
        if rng <= 0:
            return 'NONE'
        body_pct = abs(c_c - o_c) / rng
        lower_wick = min(o_c, c_c) - l_c
        upper_wick = h_c - max(o_c, c_c)

        # Marubozu: body > 80%
        if body_pct > 0.80:
            return 'MARUBOZU_BULL' if c_c > o_c else 'MARUBOZU_BEAR'

        # Hammer: lower wick > 60%, small body, bullish close
        if rng > 0 and lower_wick / rng > 0.60 and body_pct < 0.30 and c_c > o_c:
            return 'HAMMER'

        # Shooting star: upper wick > 60%, small body, bearish close
        if rng > 0 and upper_wick / rng > 0.60 and body_pct < 0.30 and c_c < o_c:
            return 'SHOOTING_STAR'

        # Engulfing: current body engulfs previous body
        prev_body = abs(c_p - o_p)
        curr_body = abs(c_c - o_c)
        if curr_body > prev_body * 1.2:
            if c_c > o_c and c_p < o_p:  # bullish engulfs bearish
                return 'ENGULFING_BULL'
            elif c_c < o_c and c_p > o_p:  # bearish engulfs bullish
                return 'ENGULFING_BEAR'

        return 'NONE'

    def _empty_snapshot(self) -> MarketSnapshot:
        """Return empty snapshot on error."""
        return MarketSnapshot(
            atr_m5=100.0,
            atr_h1=100.0,
            atr_ratio=1.0,
            delta=0,
            cvd=0,
            buy_volume=0,
            sell_volume=0,
            total_volume=0,
            der=0,
            der_direction='NEUTRAL',
            der_persistence=0,
            der_sustainability='NEUTRAL',
            m5_efficiency=0.5,
            m5_state='RANGING',
            m5_ema_position='BETWEEN',
            m5_range_high=0,
            m5_range_low=0,
            m5_candle_pattern='NONE',
            volume_ratio_m5=1.0,
            volume_24h=0,
            imbalance=0,
            imbalance_direction='NEUTRAL',
            wall_scan={},
            oi=0,
            oi_change_pct=0,
            funding_rate=0,
            magnets=[],
            # v43.1: M5 Bias
            m5_bias='NEUTRAL',
            m5_bias_level='NEUTRAL',
            m5_ema9=0.0,
            m5_ema20=0.0,
            # v43.7: VP Fields
            vp_poc=0.0,
            vp_vah=0.0,
            vp_val=0.0,
            vp_poc_session=0.0,
            vp_price_vs_va='INSIDE',
            vp_poc_distance_atr=0.0,
            vp_nearest_hvn=0.0,
            vp_nearest_lvn=0.0,
            vp_trigger_anchor='none',
            vp_poc_shift=0.0,
            vp_poc_shift_direction='NEUTRAL',
            # v64.0 MOD-66: Anti-Spoofing Wall Tracking
            prev_wall_ratio=0.0,
            wall_shrink_pct=0.0,
            wall_zone_price=0.0,
        )


def create_snapshot_builder(order_flow_analyzer, ict_analyzer) -> MarketSnapshotBuilder:
    """Factory function to create MarketSnapshotBuilder."""
    return MarketSnapshotBuilder(order_flow_analyzer, ict_analyzer)
