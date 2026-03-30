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


@dataclass
class MarketSnapshot:
    """Unified market indicators - calculated once per cycle."""
    
    # ATR (was calculated 5 times, now 1)
    atr_m5: float           # ATR(14) on M5
    atr_h1: float           # ATR(14) on H1 (from RegimeResult)
    
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
    m5_state: str           # 'SIDEWAY' | 'ACCUMULATION' | 'TRENDING' | 'EXHAUSTION' | 'PULLBACK' | 'CAUTION' | 'RANGING'

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
        # v29.1: Instance-level state for hysteresis (C3) and range persistence (C1)
        self._prev_m5_state = 'RANGING'
        self._sideway_exit_count = 0
        self._last_range_high = 0.0
        self._last_range_low = 0.0
        self._range_persist_count = 0
    
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
            
            # === 2. Delta/CVD - authoritative source: order_flow from real trades ===
            trades = binance_data.get('trades', [])
            bids = binance_data.get('bids', {})
            asks = binance_data.get('asks', {})
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

            # === 3c. M5 Market State — Efficiency Ratio + Volume (v27.3) ===
            m5_efficiency, m5_state = self._calc_m5_state(candles_m5)

            # === 3d. M5 EMA Position + Range + Candle Pattern (v28.1) ===
            m5_ema_position = self._calc_m5_ema_position(candles_m5)
            m5_range_high, m5_range_low = self._calc_m5_range(candles_m5, m5_state)
            m5_candle_pattern = self._calc_m5_candle_pattern(candles_m5)

            # === 4. Volume ratio (micro: M5) ===
            volume_ratio_m5 = self._calc_volume_ratio_m5(candles_m5)
            
            # === 5. Walls - from binance_data (already fetched) ===
            wall_scan = binance_data.get('wall_scan', {})
            
            # === 6. Magnets ===
            magnets = self.ict.get_active_magnets(candles_m5, current_price)
            
            # === Build result ===
            snapshot = MarketSnapshot(
                atr_m5=round(atr_m5, 2),
                atr_h1=regime_result.atr_h1 if regime_result else 0,
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
    
    def refine_m5_state(self, snapshot: 'MarketSnapshot', candles_m5: pd.DataFrame, h1_bias: str) -> 'MarketSnapshot':
        """
        v29.1: 2-pass refinement — called after h1_bias is computed.
        Re-calculates m5_state with H1 context (C4, C5).
        """
        if h1_bias == 'NEUTRAL' and snapshot.m5_state == 'RANGING':
            # Already would be caught, but ensure consistency
            pass
        _, new_state = self._calc_m5_state(candles_m5, h1_bias)
        if new_state != snapshot.m5_state:
            logger.info(f"[Snapshot] M5 State refined: {snapshot.m5_state} → {new_state} (H1:{h1_bias})")
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
    
    def _calc_m5_state(self, candles_m5: pd.DataFrame, h1_bias: str = 'NEUTRAL') -> tuple:
        """
        v29.1: M5 Market State using dual-period Efficiency Ratio + Volume + H1 context.

        Changes from v28.2:
        - C3: Hysteresis — exit SIDEWAY requires ER > 0.35 for 2 consecutive candles
        - C4: H1 context — H1 BULLISH/BEARISH + ER dip + EMA slope = PULLBACK not SIDEWAY
        - C5: H1 NEUTRAL + ER gray zone = CAUTION state

        Returns: (efficiency: float, state: str)
        """
        if candles_m5 is None or len(candles_m5) < 21:
            return 0.5, 'RANGING'

        closes_long = candles_m5['close'].values[-21:]
        closes_short = candles_m5['close'].values[-11:]

        # Dual-period ER
        def _er(closes_arr):
            net = abs(float(closes_arr[-1]) - float(closes_arr[0]))
            total = sum(abs(float(closes_arr[i+1]) - float(closes_arr[i])) for i in range(len(closes_arr)-1))
            return net / total if total > 0 else 0.5

        er_long = _er(closes_long)    # 20 candles = 100 min
        er_short = _er(closes_short)  # 10 candles = 50 min

        # Volume trend: last 5 vs previous 5
        vols = candles_m5['volume'].values
        if len(vols) >= 10:
            vol_recent = float(np.mean(vols[-5:]))
            vol_prev = float(np.mean(vols[-10:-5]))
            vol_rising = vol_recent > vol_prev * 1.1
        else:
            vol_rising = False

        # M5 EMA20 slope (for pullback detection)
        ema20_vals = pd.Series(candles_m5['close'].values).ewm(span=20, adjust=False).mean().values
        atr_est = float(np.mean(np.abs(np.diff(candles_m5['close'].values[-14:])))) if len(candles_m5) >= 15 else 100.0
        ema_slope = (ema20_vals[-1] - ema20_vals[-3]) / atr_est if atr_est > 0 and len(ema20_vals) >= 4 else 0

        # v29.2: Net direction check — choppy trend ≠ sideway
        net_long = abs(float(closes_long[-1]) - float(closes_long[0]))
        net_short = abs(float(closes_short[-1]) - float(closes_short[0]))

        # === Base classification ===
        if er_long < 0.25:
            if net_long > atr_est * 1.0:
                state = 'TRENDING'  # choppy but directional (net > 1×ATR)
            else:
                state = 'ACCUMULATION' if vol_rising else 'SIDEWAY'
        elif er_long > 0.50:
            state = 'TRENDING' if vol_rising else 'EXHAUSTION'
        elif er_short < 0.20:
            if net_short > atr_est * 0.8:
                state = 'TRENDING'  # early detect: choppy but moving
            else:
                state = 'ACCUMULATION' if vol_rising else 'SIDEWAY'
        elif er_short > 0.55:
            state = 'TRENDING' if vol_rising else 'EXHAUSTION'
        else:
            state = 'RANGING'

        # === C4: H1 context — PULLBACK detection ===
        # If H1 has clear bias + ER dipped temporarily + EMA slope still aligns = PULLBACK
        if state in ('SIDEWAY', 'RANGING') and h1_bias in ('BULLISH', 'BEARISH'):
            slope_confirms = (h1_bias == 'BULLISH' and ema_slope > 0.1) or \
                             (h1_bias == 'BEARISH' and ema_slope < -0.1)
            if slope_confirms and er_long >= 0.15:
                state = 'PULLBACK'

        # === C5: H1 NEUTRAL + gray zone = CAUTION ===
        if h1_bias == 'NEUTRAL' and state == 'RANGING':
            state = 'CAUTION'

        # === C3: Hysteresis — SIDEWAY exit requires 2 consecutive candles ER > 0.35 ===
        if self._prev_m5_state in ('SIDEWAY', 'ACCUMULATION') and state not in ('SIDEWAY', 'ACCUMULATION', 'PULLBACK'):
            if er_long > 0.35:
                self._sideway_exit_count += 1
            else:
                self._sideway_exit_count = 0

            if self._sideway_exit_count < 2:
                state = self._prev_m5_state  # stay in SIDEWAY until confirmed

        if state not in ('SIDEWAY', 'ACCUMULATION'):
            self._sideway_exit_count = 0

        self._prev_m5_state = state
        return round(er_long, 2), state

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
        if der_persistence < 3:
            return 'TOO_EARLY'

        # 1. Try OI first
        if oi > 0 and oi_prev > 0:
            oi_change = (oi - oi_prev) / oi_prev
            if oi_change > 0.001:
                return 'LOADING'      # new positions → sustainable
            elif oi_change < -0.001:
                return 'EXHAUSTION'   # closing positions → fading

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

    def _calc_m5_range(self, candles_m5: pd.DataFrame, m5_state: str) -> tuple:
        """v29.1: Consolidation range boundaries. Persists 3 candles after SIDEWAY exit (C1)."""
        if candles_m5 is None or len(candles_m5) < 10:
            return 0.0, 0.0
        if m5_state not in ('SIDEWAY', 'ACCUMULATION', 'RANGING', 'CAUTION'):
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
        )


def create_snapshot_builder(order_flow_analyzer, ict_analyzer) -> MarketSnapshotBuilder:
    """Factory function to create MarketSnapshotBuilder."""
    return MarketSnapshotBuilder(order_flow_analyzer, ict_analyzer)
