"""
Pullback Detector - v18.0
Detects structural pullbacks using EMA distance, volume, and slope.
Distinguishes between True and False pullbacks.
v18.0: Persist state to JSON file (survive bot restart) - CLEAN VERSION
"""
import pandas as pd
import numpy as np
import json
from pathlib import Path
from typing import Dict, Any, Optional


class PullbackDetector:
    STATE_FILE = 'data/pullback_state.json'

    def __init__(self, config: Optional[Dict] = None):
        self.config = config or {}
        self.ema_h1_period = 20
        self.ema_m5_period = 20
        self.h1_dist_threshold_pct = 1.0
        self.vol_decline_threshold = 0.7

        # Persistent state tracking
        self._pb_active = False          
        self._pb_direction = None         
        self._pb_start_idx = None         
        self._pb_slope_resumed = False    
        self._pb_ended = False            
        
        # v18.0: Load state from file
        self._load_state()

    def _load_state(self):
        """Load state from JSON file."""
        try:
            path = Path(self.STATE_FILE)
            if path.exists():
                with open(path, 'r') as f:
                    state = json.load(f)
                self._pb_active = state.get('pb_active', False)
                self._pb_ended = state.get('pb_ended', False)
                self._pb_direction = state.get('pb_direction', None)
        except Exception:
            pass

    def _save_state(self):
        """Save state to JSON file."""
        try:
            path = Path(self.STATE_FILE)
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, 'w') as f:
                json.dump({
                    'pb_active': self._pb_active,
                    'pb_ended': self._pb_ended,
                    'pb_direction': self._pb_direction
                }, f)
        except Exception:
            pass

    def reset_state(self):
        """Reset all persistent state. Call when regime changes."""
        self._pb_active = False
        self._pb_direction = None
        self._pb_start_idx = None
        self._pb_slope_resumed = False
        self._pb_ended = False
        self._save_state()

    def _calc_atr(self, candles: pd.DataFrame, period: int = 14) -> float:
        if len(candles) < period + 1:
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

    def analyze(self, candles_m5: pd.DataFrame, candles_h1: pd.DataFrame,
                h1_bias: str, current_price: float) -> Dict[str, Any]:
        """
        v16.9: Analyze pullback with M5 CHoCH structure + H1 dist filter.
        v18.0: Added state persistence.
        """
        if candles_h1 is None or len(candles_h1) < self.ema_h1_period:
            self._pb_active = False
            self._pb_ended = False
            self._save_state()
            return {
                'status': 'NONE',
                'h1_ema_dist_pct': 0,
                'vol_declining': False,
                'ema_m5_slope': 0,
                'ema20_h1': 0,
                'pb_active': False
            }

        # 1. H1 dist (filter - not trigger)
        ema20_h1 = candles_h1['close'].ewm(span=self.ema_h1_period).mean().iloc[-1]
        if ema20_h1 <= 0:
            ema20_h1 = current_price
        h1_dist_pct = abs(current_price - ema20_h1) / ema20_h1 * 100

        # 2. M5 swing detection
        if len(candles_m5) < 10:
            self._pb_active = False
            self._pb_ended = False
            self._save_state()
            return {'status': 'NONE', 'h1_ema_dist_pct': 0, 'vol_declining': False}

        closes = candles_m5['close'].values

        # v18.6: Use CLOSE n=3 for structure detection (body, not wick)
        # close = price market accepts, wick = noise/stop hunt
        # n=3 = swing must hold 15 min → stable structure
        n = 3
        m5_swing_highs = []
        m5_swing_lows = []
        for i in range(n, len(candles_m5) - n):
            if all(closes[i] > closes[i-j] for j in range(1, n+1)) and \
               all(closes[i] > closes[i+j] for j in range(1, n+1)):
                m5_swing_highs.append((i, closes[i]))
            if all(closes[i] < closes[i-j] for j in range(1, n+1)) and \
               all(closes[i] < closes[i+j] for j in range(1, n+1)):
                m5_swing_lows.append((i, closes[i]))

        # Get last swing levels (v18.6: fallback to close instead of high/low)
        last_sh = m5_swing_highs[-1][1] if m5_swing_highs else closes[-1]
        prev_sh = m5_swing_highs[-2][1] if len(m5_swing_highs) >= 2 else (closes[-5] if len(closes) >= 5 else closes[0])
        last_sl = m5_swing_lows[-1][1] if m5_swing_lows else closes[-1]
        prev_sl = m5_swing_lows[-2][1] if len(m5_swing_lows) >= 2 else (closes[-5] if len(closes) >= 5 else closes[0])

        # 3. M5 structure vs H1
        if h1_bias == 'BULLISH':
            m5_against = (last_sh < prev_sh) or (last_sl < prev_sl)
            m5_choch = closes[-1] > last_sh
        elif h1_bias == 'BEARISH':
            m5_against = (last_sh > prev_sh) or (last_sl > prev_sl)
            m5_choch = closes[-1] < last_sl
        else:
            # v29.1 C6: H1 NEUTRAL fallback — use M5 EMA20 slope as trend proxy
            ema20_m5 = pd.Series(closes).ewm(span=20, adjust=False).mean()
            atr_est = self._calc_atr(candles_m5, 14) if len(candles_m5) >= 15 else 100.0
            slope = (ema20_m5.iloc[-1] - ema20_m5.iloc[-3]) / atr_est if atr_est > 0 and len(ema20_m5) >= 4 else 0

            if slope > 0.3:
                # M5 slope bullish → treat like BULLISH for pullback detection
                m5_against = (last_sh < prev_sh) or (last_sl < prev_sl)
                m5_choch = closes[-1] > last_sh
            elif slope < -0.3:
                # M5 slope bearish → treat like BEARISH for pullback detection
                m5_against = (last_sh > prev_sh) or (last_sl > prev_sl)
                m5_choch = closes[-1] < last_sl
            else:
                m5_against = False
                m5_choch = False

        # === State Machine ===
        status = 'NONE'

        if self._pb_active:
            if m5_choch:
                status = 'ENDED'
                self._pb_active = False
                self._pb_ended = True
            else:
                status = 'ACTIVE'
        else:
            if m5_against and h1_dist_pct > 0.5:
                status = 'ACTIVE'
                self._pb_active = True
                self._pb_ended = False
            else:
                status = 'NONE'

        # Additional metrics
        vol_declining = False
        if len(candles_m5) >= 8:
            vols = candles_m5['volume'].iloc[-8:]
            vol_recent = vols.iloc[-3:].mean()
            vol_before = vols.iloc[:5].mean()
            if vol_before > 0:
                vol_declining = vol_recent < vol_before * 0.7

        atr_m5 = self._calc_atr(candles_m5, 14)
        slope = 0
        if len(candles_m5) >= 3:
            ema20_m5 = candles_m5['close'].ewm(span=20).mean()
            slope = (ema20_m5.iloc[-1] - ema20_m5.iloc[-3]) / atr_m5 if atr_m5 > 0 else 0

        self._save_state()  # v18.0: Persist every cycle
        return {
            'status': status,
            'h1_ema_dist_pct': round(h1_dist_pct, 2),
            'vol_declining': vol_declining,
            'ema_m5_slope': round(slope, 2),
            'ema20_h1': ema20_h1,
            'pb_active': self._pb_active,
            'pb_ended': self._pb_ended,
        }

    def is_true_pullback(self, candles_m5: pd.DataFrame, h1_bias: str, atr: float,
                          start_idx: Optional[int] = None) -> Dict[str, Any]:
        """
        Distinguish true vs false pullback.
        """
        if len(candles_m5) < 8 or atr <= 0 or h1_bias == 'NEUTRAL':
            return {'is_true': False, 'score': 0, 'duration': 0, 'depth_atr': 0}

        recent = candles_m5.iloc[-8:]
        vol_before = recent['volume'].iloc[:5].mean()
        vol_recent = recent['volume'].iloc[-3:].mean()
        vol_declining = vol_before > 0 and vol_recent < vol_before * 0.7

        avg_body = candles_m5.iloc[-3:].apply(lambda r: abs(r['close'] - r['open']), axis=1).mean()
        body_ok = avg_body >= atr * 0.15

        count = 0
        neutral_count = 0
        for i in range(-1, -8, -1):
            c = candles_m5.iloc[i]
            is_bearish = c['close'] < c['open']
            is_bullish = c['close'] > c['open']

            if h1_bias == 'BEARISH' and is_bullish:
                count += 1
                neutral_count = 0
            elif h1_bias == 'BULLISH' and is_bearish:
                count += 1
                neutral_count = 0
            elif is_bearish or is_bullish:
                if neutral_count < 2:
                    neutral_count += 1
                else:
                    break
            else:
                break
        duration_ok = count >= 3

        depth = 0
        depth_ok = False
        if count > 0 and count < len(candles_m5):
            idx_start = len(candles_m5) - count - 1
            idx_end = len(candles_m5) - 1
            if h1_bias == 'BEARISH':
                depth = candles_m5.iloc[idx_start]['high'] - candles_m5.iloc[idx_end]['low']
            else:
                depth = candles_m5.iloc[idx_end]['high'] - candles_m5.iloc[idx_start]['low']
            depth_ok = depth >= atr * 0.5

        ema20 = candles_m5['close'].ewm(span=20).mean()
        slope_val = abs(ema20.iloc[-1] - ema20.iloc[-3]) / atr if atr > 0 else 0
        slope_slowing = slope_val < 0.3

        score = sum([vol_declining, body_ok, duration_ok, depth_ok, slope_slowing])
        return {
            'is_true': score >= 3,
            'score': score,
            'duration': count,
            'depth_atr': round(depth / atr, 1) if atr > 0 else 0,
        }
