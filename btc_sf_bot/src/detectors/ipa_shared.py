"""
IPA Shared Logic — v40.0

Shared pre-checks for the unified IPA detector (v51.0 MOD-38).
Extracted from ipa_analyzer.py to avoid duplication.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List, Tuple

import pandas as pd
import numpy as np

from src.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class IPAContext:
    """Shared context passed between IPAShared and IPA detectors."""
    direction: str                    # 'LONG' | 'SHORT'
    h1_result: Dict[str, Any]         # H1 bias result
    m5_result: Dict[str, Any]         # M5 structure result
    eqs: int                          # Entry Quality Score (v40.4: now EQSResult)
    break_idx: int                    # Index of structure break
    score_adjust: int = 0             # H1 bias score adjustment


@dataclass
class EQSResult:
    """v40.4: EQS breakdown for forensic analysis"""
    total: int                # -3 to +3 (same as before)
    retrace: int             # +2 (good pullback) / -1 (too much) / 0
    retrace_pct: float       # actual retrace % (0.0-1.0)
    ema_position: int        # +1 (aligned) / -1 (impulse) / 0
    ema_detail: str          # 'ABOVE_BOTH' / 'BELOW_EMA20' / etc.
    volume: int              # 0 / -1 (high volume = impulse)
    volume_ratio: float     # recent/avg ratio
    body_size: int          # 0 / -1 (large body = impulse)
    body_atr_ratio: float   # body/ATR ratio


class IPAShared:
    """
    Shared pre-checks for IPA types ทุกตัว.
    
    Gate 1: H1 Bias → direction
    Gate 2: M5 Structure (bos/choch) → break confirmation
    EQS: Entry Quality Score
    
    Return None = blocked, IPAContext = passed
    """

    def __init__(self, config: dict = None, logger=None, log_prefix: str = "[IPA]"):
        self.config = config or {}
        self.logger = logger if logger else get_logger(__name__)
        self.log_prefix = log_prefix

        # === H1 Settings ===
        self.h1_lookback_candles: int = self.config.get('h1_lookback_candles', 20)

        # === M5 Fractal Settings ===
        self.m5_fractal_n: int = self.config.get('m5_fractal_n', 2)

        # === OB Settings ===
        self.ob_body_min_pct: float = self.config.get('ob_body_min_pct', 0.0005)
        self.ob_max_distance_atr: float = self.config.get('ob_max_distance_atr', 1.0)

        # Persistent L1 state
        self._l1_broken_high: Optional[float] = None
        self._l1_broken_low: Optional[float] = None
        self._l1_break_time: Optional[datetime] = None

    def pre_check(self, candles_m5: pd.DataFrame, candles_h1: pd.DataFrame,
                  current_price: float, atr_m5: Optional[float] = None) -> Optional[IPAContext]:
        """
        Run shared IPA pre-checks.
        
        Returns IPAContext if all gates pass, None if blocked.
        
        v40.3: Deprecated — use check_h1_bias(), check_m5_structure(), check_eqs() separately
        for full independence between IPA detectors.
        """
        # Gate 1 + 1.5: H1 Bias + Overextended
        h1_result = self.check_h1_bias(candles_h1, candles_m5, current_price)
        if not h1_result:
            return None

        # Gate 2: M5 Structure
        m5_result = self.check_m5_structure(candles_m5, h1_result['direction'])
        if not m5_result:
            return None

        # Gate 3: EQS
        eqs = self.check_eqs(candles_m5, h1_result['direction'], m5_result['break_idx'])

        return IPAContext(
            direction=h1_result['direction'],
            h1_result=h1_result,
            m5_result=m5_result,
            eqs=eqs,
            break_idx=m5_result['break_idx'],
            score_adjust=h1_result.get('score_adjust', 0),
        )

    def check_h1_bias(self, candles_h1: pd.DataFrame, candles_m5: pd.DataFrame,
                       current_price: float) -> Optional[Dict]:
        """
        Gate 1 + 1.5: H1 Bias + Overextended filter → h1_result or None
        
        v40.3: Independent check — each IPA detector can use this separately.
        """
        if len(candles_h1) < 4:
            return None

        # Gate 1: H1 Bias
        h1_result = self._check_h1_bias(candles_h1, candles_m5)
        if not h1_result:
            return None

        # Gate 1.5: Overextended Filter
        direction = h1_result['direction']
        ema20_h1 = h1_result.get('ema20', 0)
        if ema20_h1 > 0:
            h1_dist_pct = abs(current_price - ema20_h1) / ema20_h1 * 100
            overextended = (
                (direction == 'LONG' and current_price > ema20_h1) or
                (direction == 'SHORT' and current_price < ema20_h1)
            )
            if overextended and h1_dist_pct > 1.5:
                self.logger.info(f"{self.log_prefix} Gate 1.5: BLOCKED | Overextended {h1_dist_pct:.1f}%")
                return None

        return h1_result

    def check_m5_structure(self, candles_m5: pd.DataFrame, direction: str) -> Optional[Dict]:
        """
        Gate 2: M5 Structure break → m5_result or None
        
        v40.3: Independent check — each IPA detector can use this separately.
        """
        return self._check_m5_structure(candles_m5, direction)

    def check_eqs(self, candles_m5: pd.DataFrame, direction: str, break_idx: int) -> EQSResult:
        """
        Gate 3: Entry Quality Score → EQSResult
        
        v40.3: Independent check — each IPA detector can use this separately.
        v40.4: Returns EQSResult with full breakdown instead of int.
        """
        return self._check_entry_quality(candles_m5, direction, break_idx)

    def _check_h1_bias(self, candles_h1: pd.DataFrame, candles_m5: pd.DataFrame) -> Optional[Dict]:
        """
        Gate 1 v13.4: 3-Layer Bias Detection
        
        Layer 1: M5 close break H1 swing (TRIGGER)
        Layer 2: H1 EMA9 cross EMA20 (CONFIRM)
        Layer 3: H1 EMA20 cross EMA50 (FULL CONFIRM)
        """
        if len(candles_h1) < 50:
            return None

        closes_h1 = candles_h1['close'].values
        highs_h1 = candles_h1['high'].values
        lows_h1 = candles_h1['low'].values

        # Calculate EMAs
        ema9 = pd.Series(closes_h1).ewm(span=9, adjust=False).mean().values
        ema20 = pd.Series(closes_h1).ewm(span=20, adjust=False).mean().values
        ema50 = pd.Series(closes_h1).ewm(span=50, adjust=False).mean().values

        last_close = closes_h1[-1]
        last_ema9 = ema9[-1]
        last_ema20 = ema20[-1]
        last_ema50 = ema50[-1]

        # Layer 0: H1 Structure Bias
        h1_structure = self._detect_h1_structure_bias(candles_h1)
        layer0_bull = h1_structure == 'BULLISH'
        layer0_bear = h1_structure == 'BEARISH'

        # Layer 3: EMA20 cross EMA50
        layer3_bull = last_ema20 > last_ema50 and last_close > last_ema20
        layer3_bear = last_ema20 < last_ema50 and last_close < last_ema20

        # Layer 2: EMA9 cross EMA20
        layer2_bull = last_ema9 > last_ema20
        layer2_bear = last_ema9 < last_ema20

        # Layer 1: M5 close break H1 swing
        h1_swing_highs = self._detect_swings(highs_h1, 'high', n=3)
        h1_swing_lows = self._detect_swings(lows_h1, 'low', n=3)

        last_h1_swing_high = h1_swing_highs[-1] if h1_swing_highs else None
        last_h1_swing_low = h1_swing_lows[-1] if h1_swing_lows else None

        m5_close = candles_m5['close'].iloc[-1]
        m5_body = abs(candles_m5['close'].iloc[-1] - candles_m5['open'].iloc[-1])
        atr_h1 = self._calc_atr(candles_h1, period=14)

        # Persistent L1 state
        if last_h1_swing_high is not None and m5_close > last_h1_swing_high:
            if self._l1_broken_high is None or m5_close > self._l1_broken_high:
                self._l1_broken_high = last_h1_swing_high
                self._l1_break_time = datetime.now(timezone.utc)

        if last_h1_swing_low is not None and m5_close < last_h1_swing_low:
            if self._l1_broken_low is None or m5_close < self._l1_broken_low:
                self._l1_broken_low = last_h1_swing_low
                self._l1_break_time = datetime.now(timezone.utc)

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

        # Reset if price retests back
        if self._l1_broken_high is not None and m5_close < self._l1_broken_high:
            self._l1_broken_high = None

        if self._l1_broken_low is not None and m5_close > self._l1_broken_low:
            self._l1_broken_low = None

        # Determine bias level and direction
        bias_level = 'NONE'
        direction = None
        score_adjust = 0

        # BULLISH combos
        if layer0_bull and layer1_bull and layer2_bull and layer3_bull:
            bias_level = 'STRONG'
            direction = 'LONG'
            score_adjust = +1
        elif layer0_bull and layer2_bull and layer3_bull:
            bias_level = 'CONFIRMED+'
            direction = 'LONG'
            score_adjust = 0
        elif layer0_bull and layer1_bull and layer2_bull:
            bias_level = 'STRONG_EARLY'
            direction = 'LONG'
            score_adjust = 0
        elif layer0_bull and layer2_bull:
            bias_level = 'EARLY_STRUCTURE'
            direction = 'LONG'
            score_adjust = -1
        elif layer1_bull and layer2_bull and layer3_bull:
            bias_level = 'STRONG'
            direction = 'LONG'
            score_adjust = +1
        elif layer2_bull and layer3_bull:
            bias_level = 'CONFIRMED'
            direction = 'LONG'
            score_adjust = 0
        elif layer1_bull and layer2_bull:
            bias_level = 'EARLY'
            direction = 'LONG'
            score_adjust = -1
        # BEARISH combos
        elif layer0_bear and layer1_bear and layer2_bear and layer3_bear:
            bias_level = 'STRONG'
            direction = 'SHORT'
            score_adjust = +1
        elif layer0_bear and layer2_bear and layer3_bear:
            bias_level = 'CONFIRMED+'
            direction = 'SHORT'
            score_adjust = 0
        elif layer0_bear and layer1_bear and layer2_bear:
            bias_level = 'STRONG_EARLY'
            direction = 'SHORT'
            score_adjust = 0
        elif layer0_bear and layer2_bear:
            bias_level = 'EARLY_STRUCTURE'
            direction = 'SHORT'
            score_adjust = -1
        elif layer1_bear and layer2_bear and layer3_bear:
            bias_level = 'STRONG'
            direction = 'SHORT'
            score_adjust = +1
        elif layer2_bear and layer3_bear:
            bias_level = 'CONFIRMED'
            direction = 'SHORT'
            score_adjust = 0
        elif layer1_bear and layer2_bear:
            bias_level = 'EARLY'
            direction = 'SHORT'
            score_adjust = -1
        else:
            return None

        dir_display = "BULLISH" if direction == "LONG" else "BEARISH"
        strong_bias = layer3_bull or layer3_bear

        return {
            'direction': direction,
            'bias': dir_display,
            'bias_level': bias_level,
            'score_adjust': score_adjust,
            'bos': True,
            'choch': strong_bias,
            'fvg_unfilled': self._check_h1_fvg(candles_h1, direction),
            'ema9': last_ema9,
            'ema20': last_ema20,
            'ema50': last_ema50,
            'strong_bias': strong_bias,
            'h1_swing_high': last_h1_swing_high,
            'h1_swing_low': last_h1_swing_low,
        }

    def _check_m5_structure(self, candles_m5: pd.DataFrame, direction: str) -> Optional[Dict]:
        """
        Gate 2: M5 structure break.
        Uses n=2 for swing detection. Falls back to momentum (3 consecutive closes).
        """
        lookback = min(30, len(candles_m5) - 1)
        recent = candles_m5.iloc[-lookback:]

        closes = recent['close'].values
        volumes = recent['volume'].values
        avg_vol = volumes.mean()

        m5_swing_highs = self._detect_swings(closes, 'high', n=3)
        m5_swing_lows = self._detect_swings(closes, 'low', n=3)

        m5_bos = False
        break_idx_relative = -1

        if m5_swing_highs and m5_swing_lows:
            last_swing_high = m5_swing_highs[-1]
            last_swing_low = m5_swing_lows[-1]

            for i in range(len(closes) - 1, 0, -1):
                if direction == 'LONG' and closes[i] > last_swing_high:
                    m5_bos = True
                    break_idx_relative = i
                    break
                elif direction == 'SHORT' and closes[i] < last_swing_low:
                    m5_bos = True
                    break_idx_relative = i
                    break

        # Momentum fallback
        if not m5_bos:
            for i in range(2, len(closes)):
                if direction == 'LONG':
                    if closes[i] > closes[i-1] > closes[i-2]:
                        m5_bos = True
                        break_idx_relative = i
                        break
                else:
                    if closes[i] < closes[i-1] < closes[i-2]:
                        m5_bos = True
                        break_idx_relative = i
                        break

        if not m5_bos:
            return None

        abs_start = len(candles_m5) - lookback
        break_idx_absolute = abs_start + break_idx_relative

        vol_ratio = volumes[break_idx_relative] / avg_vol if avg_vol > 0 else 1.0

        return {
            'choch': False,
            'bos': m5_bos,
            'break_idx': break_idx_absolute,
            'volume_ratio': vol_ratio,
            'm5_conflict': False,
        }

    def _check_entry_quality(self, candles_m5: pd.DataFrame, direction: str,
                             break_idx: int) -> EQSResult:
        """
        Entry Quality Score (EQS) - v40.4: Returns breakdown for forensic analysis.
        Measures pullback (good) vs impulse (bad) entry.
        Returns EQSResult with full breakdown.
        """
        score = 0
        current_price = candles_m5['close'].iloc[-1]

        # Defaults
        retrace_score = 0
        retrace_pct = 0.0
        ema_score = 0
        ema_detail = 'UNKNOWN'
        vol_score = 0
        vol_ratio = 1.0
        body_score = 0
        body_atr = 0.0

        # Get break candle
        if break_idx < 0 or break_idx >= len(candles_m5):
            return EQSResult(
                total=0, retrace=0, retrace_pct=0,
                ema_position=0, ema_detail='N/A',
                volume=0, volume_ratio=1.0,
                body_size=0, body_atr_ratio=0.0
            )

        break_candle = candles_m5.iloc[break_idx]
        break_close = break_candle['close']

        # 1. Retrace check: price pulled back after break
        candles_after = candles_m5.iloc[break_idx + 1:]
        if len(candles_after) > 0:
            if direction == 'LONG':
                max_after = candles_after['high'].max()
                retrace_pct = (max_after - current_price) / (max_after - break_close) if max_after > break_close else 0
                if 0.3 <= retrace_pct <= 0.7:
                    retrace_score = 2  # Good pullback
                elif retrace_pct > 0.7:
                    retrace_score = -1  # Too much retrace
            else:
                min_after = candles_after['low'].min()
                retrace_pct = (current_price - min_after) / (break_close - min_after) if break_close > min_after else 0
                if 0.3 <= retrace_pct <= 0.7:
                    retrace_score = 2
                elif retrace_pct > 0.7:
                    retrace_score = -1
        score += retrace_score

        # 2. EMA distance check
        ema9 = candles_m5['close'].ewm(span=9).mean().iloc[-1]
        ema20 = candles_m5['close'].ewm(span=20).mean().iloc[-1]

        if direction == 'LONG':
            if current_price > ema9 > ema20:
                ema_score = 1  # Above EMAs = good
                ema_detail = 'ABOVE_BOTH_ALIGNED'
            elif current_price < ema20:
                ema_score = -1  # Below EMA20 = impulse
                ema_detail = 'BELOW_EMA20'
            else:
                ema_detail = 'BETWEEN'
        else:
            if current_price < ema9 < ema20:
                ema_score = 1
                ema_detail = 'BELOW_BOTH_ALIGNED'
            elif current_price > ema20:
                ema_score = -1
                ema_detail = 'ABOVE_EMA20'
            else:
                ema_detail = 'BETWEEN'
        score += ema_score

        # 3. Volume check
        recent_vol = candles_m5['volume'].iloc[-5:].mean()
        avg_vol = candles_m5['volume'].iloc[-20:].mean()
        vol_ratio = recent_vol / avg_vol if avg_vol > 0 else 1.0
        if vol_ratio > 1.5:
            vol_score = -1  # High volume = impulse
        score += vol_score

        # 4. Body size check
        last_candle = candles_m5.iloc[-1]
        body = abs(last_candle['close'] - last_candle['open'])
        atr = self._calc_atr(candles_m5, 14)
        body_atr = body / atr if atr > 0 else 0
        if body_atr > 1.5:
            body_score = -1  # Large body = impulse
        score += body_score

        total = max(-3, min(3, score))

        return EQSResult(
            total=total,
            retrace=retrace_score,
            retrace_pct=round(retrace_pct, 3),
            ema_position=ema_score,
            ema_detail=ema_detail,
            volume=vol_score,
            volume_ratio=round(vol_ratio, 2),
            body_size=body_score,
            body_atr_ratio=round(body_atr, 2),
        )

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

    def _detect_h1_structure_bias(self, candles_h1: pd.DataFrame) -> str:
        """v17.3: H1 bias จาก structure."""
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

        if len(sig_highs) < 2 or len(sig_lows) < 2:
            return 'NEUTRAL'

        higher_high = sig_highs[-1] > sig_highs[-2]
        higher_low = sig_lows[-1] > sig_lows[-2]
        lower_high = sig_highs[-1] < sig_highs[-2]
        lower_low = sig_lows[-1] < sig_lows[-2]

        if higher_high and higher_low:
            return 'BULLISH'
        elif lower_high and lower_low:
            return 'BEARISH'
        else:
            return 'NEUTRAL'

    def _check_h1_fvg(self, candles_h1: pd.DataFrame, direction: str) -> bool:
        """Check for unfilled H1 FVG."""
        if len(candles_h1) < 3:
            return False

        for i in range(len(candles_h1) - 3):
            h1 = candles_h1.iloc[i:i + 3]
            if direction == 'LONG':
                gap = h1.iloc[2]['low'] - h1.iloc[0]['high']
                if gap > 0 and h1.iloc[-1]['low'] > h1.iloc[0]['high']:
                    return True
            else:
                gap = h1.iloc[0]['low'] - h1.iloc[2]['high']
                if gap > 0 and h1.iloc[-1]['high'] < h1.iloc[0]['low']:
                    return True
        return False

    def _calc_atr(self, df: pd.DataFrame, period: int = 14) -> float:
        """Calculate ATR."""
        if len(df) < period + 1:
            return 0.0

        high = df['high'].values
        low = df['low'].values
        close = df['close'].values

        tr = np.maximum(high[1:] - low[1:],
                        np.maximum(np.abs(high[1:] - close[:-1]),
                                   np.abs(low[1:] - close[:-1])))

        return float(np.mean(tr[-period:])) if len(tr) >= period else 0.0
