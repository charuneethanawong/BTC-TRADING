"""
H1BiasEngine — Unified H1 Bias Analysis
Version: 27.0

Single source of truth for H1 bias calculation.
Moved from ipa_analyzer.py methods:
  - _check_h1_bias()
  - _detect_swings()
  - _detect_significant_swings_h1()
  - _detect_h1_structure_bias()
  - _detect_h1_candle_bias()
  - _detect_early_reversal_confluence()
  - _calc_gate1_with_lc_lr()

Usage:
    h1_bias = h1_bias_engine.analyze(
        candles_h1=candles_h1, candles_m5=candles_m5,
        binance_data=binance_data, regime=regime_string,
    )
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Tuple, List
import pandas as pd
import numpy as np

from src.utils.logger import get_logger
from src.utils.decorators import retry, circuit_breaker, log_errors
from src.utils.metrics import timed_metric

logger = get_logger(__name__)


@dataclass
class H1BiasResult:
    """Result of H1 bias analysis."""
    bias: str               # 'BULLISH' | 'BEARISH' | 'NEUTRAL'
    direction: str         # 'LONG' | 'SHORT' | None
    bias_level: str         # 'STRONG' | 'CONFIRMED+' | 'CONFIRMED' | 'EARLY' | 'EARLY_STRUCTURE' | 'NONE'
    score_adj: int
    l0: str                 # Structure bias ('BULLISH' | 'BEARISH' | 'NEUTRAL')
    l1: str                 # Break bias ('BULLISH' | 'BEARISH' | 'NEUTRAL')
    l2: str                 # EMA9 vs EMA20 ('BULLISH' | 'BEARISH' | 'NEUTRAL')
    l3: str                 # EMA20 vs EMA50 ('BULLISH' | 'BEARISH' | 'NEUTRAL')
    ema9: float
    ema20: float
    ema50: float
    lc: str                 # H1 candle bias ('BULLISH' | 'BEARISH' | 'NEUTRAL')
    lr: str                 # Early reversal confluence ('BULLISH' | 'BEARISH' | 'NEUTRAL')
    lr_count: int
    structure_type: str     # 'BOS' | 'CHoCH' | 'RANGE' | 'NONE'
    structure_direction: str
    
    # State variables for persistent L1 tracking (stored in engine instance)
    _l1_broken_high: float = None
    _l1_broken_low: float = None
    _l1_break_time: datetime = None


class H1BiasEngine:
    """
    Unified H1 Bias Engine.
    
    Calculates H1 bias once per cycle, used by all modes (IPA, IOF, IPAF, IOFF).
    Separated from IPA analyzer in v27.0 for single source of truth.
    """
    
    def __init__(self, config: dict = None):
        self.config = config or {}
        
        # v13.6: Persistent L1 state - survives across cycles
        self._l1_broken_high: Optional[float] = None
        self._l1_broken_low: Optional[float] = None
        self._l1_break_time: Optional[datetime] = None
        
        # Fractal settings
        self.m5_fractal_n = 3
        self.h1_fractal_n = 3
        
        # ATR period
        self.atr_period = 14
    
    @log_errors
    @timed_metric("H1BiasEngine.analyze")
    @retry(max_attempts=3, delay=0.1, backoff=2.0, exceptions=(Exception,))
    @circuit_breaker(failure_threshold=5, timeout=30.0, expected_exception=Exception)
    def analyze(self, candles_h1: pd.DataFrame, candles_m5: pd.DataFrame,
                binance_data: dict, regime: str = 'RANGING') -> H1BiasResult:
        """
        Main entry point: Analyze H1 bias.
        
        Args:
            candles_h1: H1 OHLCV data
            candles_m5: M5 OHLCV data (for L1 detection)
            binance_data: dict with wall_scan, der_direction, oi, etc.
            regime: Current market regime ('TRENDING', 'WEAKENING', 'RANGING', etc.)
            
        Returns:
            H1BiasResult with all bias information
        """
        # === Step 1: Calculate EMAs ===
        if candles_h1 is None or len(candles_h1) < 50:
            return self._empty_result()
        
        closes_h1 = candles_h1['close'].values
        highs_h1 = candles_h1['high'].values
        lows_h1 = candles_h1['low'].values
        
        ema9 = pd.Series(closes_h1).ewm(span=9, adjust=False).mean().values
        ema20 = pd.Series(closes_h1).ewm(span=20, adjust=False).mean().values
        ema50 = pd.Series(closes_h1).ewm(span=50, adjust=False).mean().values
        
        last_close = closes_h1[-1]
        last_ema9 = ema9[-1]
        last_ema20 = ema20[-1]
        last_ema50 = ema50[-1]
        
        # === Step 2: Layer 0 - H1 Structure Bias ===
        l0_bias, struct_type, struct_dir = self._detect_h1_structure_bias(candles_h1)
        layer0_bull = l0_bias == 'BULLISH'
        layer0_bear = l0_bias == 'BEARISH'
        
        # === Step 3: Layer 3 - EMA20 vs EMA50 ===
        layer3_bull = last_ema20 > last_ema50 and last_close > last_ema20
        layer3_bear = last_ema20 < last_ema50 and last_close < last_ema20
        
        # === Step 4: Layer 2 - EMA9 vs EMA20 ===
        layer2_bull = last_ema9 > last_ema20
        layer2_bear = last_ema9 < last_ema20
        
        # === Step 5: Layer 1 - M5 close break H1 swing (Persistent) ===
        h1_swing_highs = self._detect_swings(highs_h1, 'high', n=3)
        h1_swing_lows = self._detect_swings(lows_h1, 'low', n=3)
        
        last_h1_swing_high = h1_swing_highs[-1] if h1_swing_highs else None
        last_h1_swing_low = h1_swing_lows[-1] if h1_swing_lows else None
        
        m5_close = candles_m5['close'].iloc[-1]
        m5_body = abs(candles_m5['close'].iloc[-1] - candles_m5['open'].iloc[-1])
        atr_h1 = self._calc_atr(candles_h1, self.atr_period)
        
        # Check if price breaks NEW high (update state)
        if last_h1_swing_high is not None and m5_close > last_h1_swing_high:
            if self._l1_broken_high is None or m5_close > self._l1_broken_high:
                self._l1_broken_high = last_h1_swing_high
                self._l1_break_time = datetime.now(timezone.utc)
        
        if last_h1_swing_low is not None and m5_close < last_h1_swing_low:
            if self._l1_broken_low is None or m5_close < self._l1_broken_low:
                self._l1_broken_low = last_h1_swing_low
                self._l1_break_time = datetime.now(timezone.utc)
        
        # Sustained check: price still above/below remembered break level
        layer1_bull = (
            self._l1_broken_high is not None
            and m5_close > self._l1_broken_high
            and m5_body > atr_h1 * 0.15
        )
        
        layer1_bear = (
            self._l1_broken_low is not None
            and m5_close < self._l1_broken_low
            and m5_body > atr_h1 * 0.15
        )
        
        # Reset L1 state if price retests back through break level (stop hunt)
        if self._l1_broken_high is not None and m5_close < self._l1_broken_high:
            self._l1_broken_high = None
        
        if self._l1_broken_low is not None and m5_close > self._l1_broken_low:
            self._l1_broken_low = None
        
        # === Step 6: Determine bias level and direction ===
        direction, bias_level, score_adj = self._determine_bias_direction(
            layer0_bull, layer0_bear,
            layer1_bull, layer1_bear,
            layer2_bull, layer2_bear,
            layer3_bull, layer3_bear,
        )
        
        # === Step 7: LC (H1 Candle Bias) ===
        lc_bias = self._detect_h1_candle_bias(candles_h1)
        
        # === Step 8: LR (Early Reversal Confluence) ===
        wall_scan = binance_data.get('wall_scan', {})
        lr_bias, lr_count = self._detect_early_reversal_confluence(
            candles_h1, candles_m5, binance_data, wall_scan
        )
        
        # === Step 9: WEAKENING regime - downgrade bias 1 level ===
        if regime == 'WEAKENING':
            bias_level = self._downgrade_bias(bias_level)

        # === Step 10: M5 Reality Check (v28.2) ===
        if direction and bias_level != 'NONE':
            m5_adj = self._m5_reality_check(
                bias_direction=direction,
                m5_state=binance_data.get('m5_state', 'RANGING'),
                m5_ema_position=binance_data.get('m5_ema_position', 'BETWEEN'),
                der=float(binance_data.get('der', 0)),
                der_direction=binance_data.get('der_direction', 'NEUTRAL'),
                der_persistence=int(binance_data.get('der_persistence', 0)),
                delta=float(binance_data.get('delta', 0)),
            )
            if m5_adj != 0:
                bias_level, score_adj = self._apply_m5_adjustment(bias_level, score_adj, m5_adj)
                if bias_level == 'NONE':
                    direction = None

        # === Log ===
        l0 = "L0:STR" if (layer0_bull or layer0_bear) else "L0:---"
        l1 = "L1:BRK" if (layer1_bull or layer1_bear) else "L1:---"
        l2 = "L2:EMA9" if (layer2_bull or layer2_bear) else "L2:---"
        l3 = "L3:EMA50" if (layer3_bull or layer3_bear) else "L3:---"
        dir_display = "BULLISH" if direction == "LONG" else "BEARISH" if direction == "SHORT" else "NEUTRAL"
        gate_status = "PASSED" if direction else "FAILED"
        
        logger.info(
            f"[H1Bias] {gate_status} | {dir_display} | {bias_level} | "
            f"{l0} {l1} {l2} {l3} | EMA9:{last_ema9:.0f} EMA20:{last_ema20:.0f} EMA50:{last_ema50:.0f} | "
            f"C:{last_close:.0f} | adj:{score_adj:+d}"
        )
        
        # Layer directions for dashboard
        l0_dir = 'BULLISH' if layer0_bull else 'BEARISH' if layer0_bear else 'NEUTRAL'
        l1_dir = 'BULLISH' if layer1_bull else 'BEARISH' if layer1_bear else 'NEUTRAL'
        l2_dir = 'BULLISH' if layer2_bull else 'BEARISH' if layer2_bear else 'NEUTRAL'
        l3_dir = 'BULLISH' if layer3_bull else 'BEARISH' if layer3_bear else 'NEUTRAL'
        
        return H1BiasResult(
            bias=dir_display,
            direction=direction,
            bias_level=bias_level,
            score_adj=score_adj,
            l0=l0_dir,
            l1=l1_dir,
            l2=l2_dir,
            l3=l3_dir,
            ema9=last_ema9,
            ema20=last_ema20,
            ema50=last_ema50,
            lc=lc_bias,
            lr=lr_bias,
            lr_count=lr_count,
            structure_type=struct_type,
            structure_direction=struct_dir,
            _l1_broken_high=self._l1_broken_high,
            _l1_broken_low=self._l1_broken_low,
            _l1_break_time=self._l1_break_time,
        )
    
    def _determine_bias_direction(self,
                                   layer0_bull: bool, layer0_bear: bool,
                                   layer1_bull: bool, layer1_bear: bool,
                                   layer2_bull: bool, layer2_bear: bool,
                                   layer3_bull: bool, layer3_bear: bool) -> Tuple[Optional[str], str, int]:
        """Determine direction, bias_level, and score_adjustment."""
        
        # BULLISH combos
        if layer0_bull and layer1_bull and layer2_bull and layer3_bull:
            return 'LONG', 'STRONG', +1
        elif layer0_bull and layer2_bull and layer3_bull:
            return 'LONG', 'CONFIRMED+', 0
        elif layer0_bull and layer1_bull and layer2_bull:
            return 'LONG', 'STRONG_EARLY', 0
        elif layer0_bull and layer2_bull:
            return 'LONG', 'EARLY_STRUCTURE', -1
        elif layer1_bull and layer2_bull and layer3_bull:
            return 'LONG', 'STRONG', +1
        elif layer2_bull and layer3_bull:
            return 'LONG', 'CONFIRMED', 0
        elif layer1_bull and layer2_bull:
            return 'LONG', 'EARLY', -1
        
        # BEARISH combos
        elif layer0_bear and layer1_bear and layer2_bear and layer3_bear:
            return 'SHORT', 'STRONG', +1
        elif layer0_bear and layer2_bear and layer3_bear:
            return 'SHORT', 'CONFIRMED+', 0
        elif layer0_bear and layer1_bear and layer2_bear:
            return 'SHORT', 'STRONG_EARLY', 0
        elif layer0_bear and layer2_bear:
            return 'SHORT', 'EARLY_STRUCTURE', -1
        elif layer1_bear and layer2_bear and layer3_bear:
            return 'SHORT', 'STRONG', +1
        elif layer2_bear and layer3_bear:
            return 'SHORT', 'CONFIRMED', 0
        elif layer1_bear and layer2_bear:
            return 'SHORT', 'EARLY', -1
        
        # FAIL
        return None, 'NONE', 0
    
    def _downgrade_bias(self, bias_level: str) -> str:
        """Downgrade bias level by 1 when WEAKENING regime."""
        downgrade_map = {
            'STRONG': 'CONFIRMED+',
            'CONFIRMED+': 'CONFIRMED',
            'STRONG_EARLY': 'CONFIRMED',
            'CONFIRMED': 'EARLY',
            'EARLY_STRUCTURE': 'EARLY',
            'EARLY': 'NONE',
            'NONE': 'NONE',
        }
        return downgrade_map.get(bias_level, 'NONE')
    
    # === M5 Reality Check (v28.2) ===

    LEVEL_ORDER = ['NONE', 'EARLY_STRUCTURE', 'EARLY', 'CONFIRMED', 'CONFIRMED+', 'STRONG_EARLY', 'STRONG']

    def _m5_reality_check(self, bias_direction: str, m5_state: str, m5_ema_position: str,
                           der: float, der_direction: str, der_persistence: int, delta: float) -> int:
        """
        v28.2: M5 ปรับ bias_level — ไม่เปลี่ยน direction แค่ปรับ confidence.
        Returns: adjustment (-4 to +3)
        """
        adj = 0
        is_long = bias_direction == 'LONG'

        # Signal 1: M5 State
        if m5_state == 'TRENDING':
            m5_confirms = (is_long and m5_ema_position == 'ABOVE_ALL') or \
                          (not is_long and m5_ema_position == 'BELOW_ALL')
            adj += 1 if m5_confirms else -1
        elif m5_state == 'EXHAUSTION':
            adj -= 1  # trend ending regardless of direction
        elif m5_state == 'SIDEWAY':
            adj -= 1  # no momentum

        # Signal 2: DER Flow
        if der > 0.6 and der_persistence >= 3:
            der_confirms = (is_long and der_direction == 'LONG') or \
                           (not is_long and der_direction == 'SHORT')
            adj += 1 if der_confirms else -2  # strong opposing flow = big penalty
        elif 0.3 <= der <= 0.6:
            der_opposes = (is_long and der_direction == 'SHORT') or \
                          (not is_long and der_direction == 'LONG')
            if der_opposes:
                adj -= 1

        # Signal 3: M5 EMA Position
        if m5_ema_position == 'ABOVE_ALL':
            adj += 1 if is_long else -1
        elif m5_ema_position == 'BELOW_ALL':
            adj += 1 if not is_long else -1

        return adj

    def _apply_m5_adjustment(self, bias_level: str, score_adj: int, m5_adj: int) -> tuple:
        """Apply M5 reality check adjustment to bias_level."""
        if m5_adj == 0:
            return bias_level, score_adj
        try:
            idx = self.LEVEL_ORDER.index(bias_level)
        except ValueError:
            return bias_level, score_adj
        new_idx = max(0, min(len(self.LEVEL_ORDER) - 1, idx + m5_adj))
        new_level = self.LEVEL_ORDER[new_idx]
        return new_level, score_adj + m5_adj

    def _detect_swings(self, values: np.ndarray, lookback: str = 'high', n: int = None) -> List[float]:
        """Detect swing highs/lows using simple pivot logic."""
        if len(values) < 3:
            return []
        
        swings = []
        period = n if n is not None else self.m5_fractal_n
        
        for i in range(period, len(values) - period):
            if lookback == 'high':
                if all(values[i] > values[i - j] for j in range(1, period + 1)) and \
                   all(values[i] > values[i + j] for j in range(1, period + 1)):
                    swings.append(float(values[i]))
            else:
                if all(values[i] < values[i - j] for j in range(1, period + 1)) and \
                   all(values[i] < values[i + j] for j in range(1, period + 1)):
                    swings.append(float(values[i]))
        
        return swings
    
    def _detect_significant_swings_h1(self, candles_h1: pd.DataFrame) -> Tuple[List[float], List[float]]:
        """H1 swing detection — ATR filtered + lookback 48 hours."""
        lookback = min(48, len(candles_h1))
        recent_h1 = candles_h1.iloc[-lookback:]
        
        atr = self._calc_atr(recent_h1, 14)
        min_swing_size = atr * 0.8
        
        raw_highs = self._detect_swings(recent_h1['high'].values, 'high', n=3)
        raw_lows = self._detect_swings(recent_h1['low'].values, 'low', n=3)
        
        sig_highs = []
        for sh in raw_highs:
            if not sig_highs or abs(sh - sig_highs[-1]) >= min_swing_size:
                sig_highs.append(sh)
        
        sig_lows = []
        for sl in raw_lows:
            if not sig_lows or abs(sl - sig_lows[-1]) >= min_swing_size:
                sig_lows.append(sl)
        
        # Add current price as potential swing level
        current_price = recent_h1['close'].iloc[-1]
        
        if len(sig_lows) > 0 and current_price < sig_lows[-1]:
            sig_lows.append(current_price)
        
        if len(sig_highs) > 0 and current_price > sig_highs[-1]:
            sig_highs.append(current_price)
        
        logger.debug(
            f"[H1Bias] L0 DEBUG | ATR:{atr:.0f} minSwing:{min_swing_size:.0f} | lookback:{lookback}h | "
            f"RawHi:{len(raw_highs)} SigHi:{len(sig_highs)} | RawLo:{len(raw_lows)} SigLo:{len(sig_lows)} | "
            f"C:{current_price:.0f}"
        )
        
        return sig_highs, sig_lows
    
    def _detect_h1_structure_bias(self, candles_h1: pd.DataFrame) -> Tuple[str, str, str]:
        """H1 bias from structure — faster than EMA (3-6 hours)."""
        sig_highs, sig_lows = self._detect_significant_swings_h1(candles_h1)
        
        if len(sig_highs) < 2 or len(sig_lows) < 2:
            logger.debug(f"[H1Bias] L0: NEUTRAL (not enough swings: highs={len(sig_highs)}, lows={len(sig_lows)})")
            return 'NEUTRAL', 'NONE', 'NEUTRAL'
        
        higher_high = sig_highs[-1] > sig_highs[-2]
        higher_low = sig_lows[-1] > sig_lows[-2]
        lower_high = sig_highs[-1] < sig_highs[-2]
        lower_low = sig_lows[-1] < sig_lows[-2]
        
        if higher_high and higher_low:
            logger.debug(f"[H1Bias] L0: BULLISH (HH:{sig_highs[-1]:.0f}>{sig_highs[-2]:.0f} HL:{sig_lows[-1]:.0f}>{sig_lows[-2]:.0f})")
            return 'BULLISH', 'BOS', 'BULLISH'
        elif lower_high and lower_low:
            logger.debug(f"[H1Bias] L0: BEARISH (LH:{sig_highs[-1]:.0f}<{sig_highs[-2]:.0f} LL:{sig_lows[-1]:.0f}<{sig_lows[-2]:.0f})")
            return 'BEARISH', 'BOS', 'BEARISH'
        else:
            logger.debug(f"[H1Bias] L0: NEUTRAL (mixed structure)")
            return 'NEUTRAL', 'RANGE', 'NEUTRAL'
    
    def _detect_h1_candle_bias(self, candles_h1: pd.DataFrame) -> str:
        """H1 Candle Bias — pattern + confirm (2 candles)."""
        if len(candles_h1) < 3:
            return 'NEUTRAL'
        
        prev2 = candles_h1.iloc[-3]
        prev = candles_h1.iloc[-2]
        atr = self._calc_atr(candles_h1, 14)
        if atr <= 0:
            return 'NEUTRAL'
        
        # Hammer + confirm (Bullish)
        hammer_range = prev2['high'] - prev2['low']
        if hammer_range > 0:
            lower_wick = min(prev2['open'], prev2['close']) - prev2['low']
            is_hammer = (lower_wick / hammer_range > 0.6
                         and prev2['close'] > prev2['open']
                         and abs(prev2['close'] - prev2['open']) > atr * 0.1)
            if is_hammer and prev['close'] > prev2['high']:
                logger.debug(f"[H1Bias] LC: BULLISH | Hammer + confirm")
                return 'BULLISH'
        
        # Shooting Star + confirm (Bearish)
        if hammer_range > 0:
            upper_wick = prev2['high'] - max(prev2['open'], prev2['close'])
            is_shooting = (upper_wick / hammer_range > 0.6
                           and prev2['close'] < prev2['open']
                           and abs(prev2['close'] - prev2['open']) > atr * 0.1)
            if is_shooting and prev['close'] < prev2['low']:
                logger.debug(f"[H1Bias] LC: BEARISH | Shooting Star + confirm")
                return 'BEARISH'
        
        # Bullish Engulfing + confirm
        if (prev2['close'] < prev2['open']
            and prev['close'] > prev['open']
            and prev['close'] > prev2['open']
            and prev['open'] < prev2['close']
            and candles_h1.iloc[-1]['close'] > prev['close']):
            logger.debug(f"[H1Bias] LC: BULLISH | Bullish Engulfing + confirm")
            return 'BULLISH'
        
        # Bearish Engulfing + confirm
        if (prev2['close'] > prev2['open']
            and prev['close'] < prev['open']
            and prev['close'] < prev2['open']
            and prev['open'] > prev2['close']
            and candles_h1.iloc[-1]['close'] < prev['close']):
            logger.debug(f"[H1Bias] LC: BEARISH | Bearish Engulfing + confirm")
            return 'BEARISH'
        
        return 'NEUTRAL'
    
    def _detect_early_reversal_confluence(self, candles_h1: pd.DataFrame, candles_m5: pd.DataFrame,
                                          binance_data: dict, wall_scan: dict) -> Tuple[str, int]:
        """
        Early Reversal Confluence (4 signals):
        1. Volume climax → decline (H1)
        2. DER shift
        3. Wall dominant (raw ratio ≥ 2x)
        4. OI declining
        """
        bull = 0
        bear = 0
        
        # 1. Volume climax → decline (H1)
        if len(candles_h1) >= 5:
            vols = candles_h1['volume'].iloc[-5:].values
            vol_peak = max(vols[:-2])
            vol_now = (vols[-1] + vols[-2]) / 2
            vol_avg = candles_h1['volume'].iloc[-20:].mean()
            if vol_peak > vol_avg * 2.0 and vol_now < vol_peak * 0.6:
                if candles_h1.iloc[-1]['close'] > candles_h1.iloc[-1]['open']:
                    bull += 1
                    logger.debug(f"[H1Bias] LR: Volume climax → decline (BULLISH)")
                else:
                    bear += 1
                    logger.debug(f"[H1Bias] LR: Volume climax → decline (BEARISH)")
        
        # 2. DER shift
        der_dir = binance_data.get('der_direction', None)
        if der_dir == 'LONG':
            bull += 1
            logger.debug(f"[H1Bias] LR: DER LONG")
        elif der_dir == 'SHORT':
            bear += 1
            logger.debug(f"[H1Bias] LR: DER SHORT")
        
        # 3. Wall dominant
        if wall_scan:
            raw_dom = wall_scan.get('raw_dominant', 'NONE')
            raw_ratio = wall_scan.get('raw_ratio', 1)
            if raw_dom == 'BID' and raw_ratio >= 2.0:
                bull += 1
                logger.debug(f"[H1Bias] LR: Wall BID {raw_ratio:.1f}x")
            elif raw_dom == 'ASK' and raw_ratio >= 2.0:
                bear += 1
                logger.debug(f"[H1Bias] LR: Wall ASK {raw_ratio:.1f}x")
        
        # 4. OI declining
        oi = binance_data.get('oi', 0)
        oi_prev = binance_data.get('oi_1min_ago', 0)
        if oi > 0 and oi_prev > 0:
            oi_change = (oi - oi_prev) / oi_prev
            if oi_change < -0.001:
                if candles_m5.iloc[-1]['close'] > candles_m5.iloc[-3]['close']:
                    bull += 1
                    logger.debug(f"[H1Bias] LR: OI declining (BULLISH)")
                else:
                    bear += 1
                    logger.debug(f"[H1Bias] LR: OI declining (BEARISH)")
        
        if bull >= 3:
            return 'BULLISH', bull
        elif bear >= 3:
            return 'BEARISH', bear
        else:
            return 'NEUTRAL', max(bull, bear)
    
    def _calc_atr(self, candles: pd.DataFrame, period: int = 14) -> float:
        """Calculate ATR."""
        if candles is None or len(candles) < period + 1:
            return 100.0
        
        high = candles['high'].values
        low = candles['low'].values
        close = candles['close'].values
        
        tr1 = high - low
        tr2 = np.abs(high - np.roll(close, 1))
        tr3 = np.abs(low - np.roll(close, 1))
        
        tr2[0] = tr1[0]
        tr3[0] = tr1[0]
        
        tr = np.maximum(tr1, np.maximum(tr2, tr3))
        atr = pd.Series(tr).rolling(window=period).mean().iloc[-1]
        return float(atr) if not np.isnan(atr) else 100.0
    
    def _empty_result(self) -> H1BiasResult:
        """Return empty result when not enough data."""
        return H1BiasResult(
            bias='NEUTRAL',
            direction=None,
            bias_level='NONE',
            score_adj=0,
            l0='NEUTRAL',
            l1='NEUTRAL',
            l2='NEUTRAL',
            l3='NEUTRAL',
            ema9=0,
            ema20=0,
            ema50=0,
            lc='NEUTRAL',
            lr='NEUTRAL',
            lr_count=0,
            structure_type='NONE',
            structure_direction='NEUTRAL',
        )
