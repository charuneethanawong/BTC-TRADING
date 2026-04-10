"""
VP POC Detector — v43.7

Detects POC Reaction: price reacts with POC (reject/break/accept).
"""
from typing import List, Dict, Any, Optional, Tuple

import numpy as np
import pandas as pd

from src.detectors.base import BaseDetector, SignalResult, DetectionContext
from src.utils.logger import get_logger

logger = get_logger(__name__)


class VPPOCDetector(BaseDetector):
    signal_type = 'VP_POC'
    timing = 'CANDLE_CLOSE'
    score_threshold = 8

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.last_reject_reason = ''
        self.logger = logger

    def detect(self, ctx: DetectionContext) -> List[SignalResult]:
        """Detect VP_POC signals — POC Reaction."""
        results = []

        binance_data = ctx.binance_data or {}
        current_price = ctx.current_price
        frvp = ctx.frvp_data or {}
        swing = frvp.get('layers', {}).get('swing_anchored', {})
        poc = swing.get('poc', 0) or 0.0
        vah = swing.get('vah', 0) or 0.0
        val = swing.get('val', 0) or 0.0
        session = ctx.session
        atr_m5 = ctx.snapshot.atr_m5 if hasattr(ctx.snapshot, 'atr_m5') else 0.0
        
        # v50.3: Data Sync — ensure we use Snapshot values (calculated) not raw binance_data
        delta = ctx.snapshot.delta
        der = ctx.snapshot.der
        der_dir = ctx.snapshot.der_direction
        regime = ctx.regime.regime
        m5_state = ctx.snapshot.m5_state
        
        volume_ratio = ctx.snapshot.volume_ratio_m5 if hasattr(ctx.snapshot, 'volume_ratio_m5') else 1.0
        candles_m5 = ctx.candles_m5
        delta_mismatch = False

        # Gate: Need POC
        if not poc:
            self.last_reject_reason = 'No POC data'
            return []

        # 1. Price near POC
        poc_dist = abs(current_price - poc)
        # v51.4: Distance is scoring factor, not hard reject (institutional POC can be far)

        # 2. Detect reaction from last 3 candles
        reaction, wick_ratio = self._detect_poc_reaction(candles_m5, poc, atr_m5)

        # v64.0 MOD-66: Shrinking Wall Detection (same as VP_BOUNCE)
        wall_scan = binance_data.get('wall_scan', {})
        wall_shrink_pct = getattr(ctx.snapshot, 'wall_shrink_pct', 0.0)
        wall_zone_price = getattr(ctx.snapshot, 'wall_zone_price', 0.0)
        wall_stability_sec = getattr(ctx.snapshot, 'wall_stability_sec', 0)
        
        if wall_zone_price > 0 and atr_m5 > 0:
            wall_proximity = abs(current_price - wall_zone_price) / atr_m5
            # If price within 0.5 ATR of wall AND wall shrank > 30% → reject
            if wall_proximity < 0.5 and wall_shrink_pct > 30:
                self.last_reject_reason = f'Wall shrank {wall_shrink_pct:.0f}% — institution withdrew liquidity'
                return []
            
            # Wall Ratio Cap — block if wall > 60x but stability < 60s (Liquidity Magnet trap)
            wall_ratio = wall_scan.get('raw_ratio', 0)
            if wall_ratio > 60 and wall_stability_sec < 60:
                self.last_reject_reason = f'Wall ratio {wall_ratio:.0f}x too high, stability {wall_stability_sec}s too low — Liquidity Magnet'
                return []

        if reaction == 'ACCEPT':
            self.last_reject_reason = 'POC Accept (no edge — price circling fair value)'
            return []
        if reaction == 'UNKNOWN':
            self.last_reject_reason = 'No clear POC reaction'
            return []

        # v51.4: FALSE_BO — strongest reaction (institutional trap)
        if reaction in ('FALSE_BO_UP', 'FALSE_BO_DOWN'):
            direction = 'LONG' if reaction == 'FALSE_BO_UP' else 'SHORT'
        # v50.8: PROXIMITY — no reaction yet, wait
        elif reaction == 'PROXIMITY':
            self.last_reject_reason = f'POC proximity — waiting for reaction'
            return []
        # Determine direction based on reaction
        elif reaction in ('REJECT_UP', 'BREAK_UP'):
            direction = 'LONG'
        elif reaction in ('REJECT_DOWN', 'BREAK_DOWN'):
            direction = 'SHORT'
        else:
            self.last_reject_reason = f'Unclear direction from reaction: {reaction}'
            return []

        # 3. Flow check
        if der < 0.2:  # v51.2: reduced — POC reaction doesn't need strong flow
            self.last_reject_reason = f'[{direction}] DER {der:.2f} < 0.2 (flow required for POC shift)'
            return []

        # 4. State check
        if m5_state not in ('ACCUMULATION', 'TRENDING', 'EXPANSION'):
            self.last_reject_reason = f'[{direction}] M5 {m5_state} not moving POC'
            return []
        
        # v51.4: Delta opposite — don't reject, reaction at POC is more important than delta
        # if (direction == 'LONG' and delta < 0) or (direction == 'SHORT' and delta > 0):
        #     self.last_reject_reason = f'[{direction}] Delta {delta:.0f} opposite sign'
        #     return []

        # Initialize breakdown
        breakdown = self._init_breakdown(ctx)

        # Scoring
        score = 0

        # POC proximity scoring (distance-based)
        if poc_dist <= atr_m5 * 0.5:
            score += 3
            breakdown['poc_near'] = 3
        elif poc_dist <= atr_m5 * 1.5:
            score += 2
            breakdown['poc_near'] = 2
        elif poc_dist <= atr_m5 * 3.0:
            score += 1
            breakdown['poc_near'] = 1
        else:
            breakdown['poc_near'] = 0

        # Reaction scoring
        if reaction.startswith('FALSE_BO'):
            score += 4
            breakdown['reaction'] = 4   # strongest — institutional trap
        elif reaction.startswith('REJECT'):
            score += 3
            breakdown['reaction'] = 3
        elif reaction.startswith('BREAK'):
            score += 2
            breakdown['reaction'] = 2

        # Wick rejection strength
        if wick_ratio > 0.6:
            score += 2
            breakdown['wick_rej'] = 2
        elif wick_ratio > 0.4:
            score += 1
            breakdown['wick_rej'] = 1

        # Volume at reaction
        if volume_ratio > 1.5:
            score += 2
            breakdown['vol'] = 2
        elif volume_ratio > 1.2:
            score += 1
            breakdown['vol'] = 1

        # DER confirm
        if der > 0.5:
            score += 1
            breakdown['der'] = 1

        # POC proximity bonus
        if poc_dist < atr_m5 * 0.1:
            score += 1
            breakdown['poc_close'] = 1

        # False breakout check
        fb_score, fb_indicators = self._calc_false_breakout_score(ctx, direction)
        breakdown['false_bo_score'] = fb_score
        breakdown['false_bo_likely'] = fb_score >= 3
        
        # MOD-45: Institutional Confirmation (v52.0)
        prev_delta = 0
        if candles_m5 is not None and len(candles_m5) >= 2:
            try: prev_delta = candles_m5.iloc[-2].get('delta', 0)
            except: pass
        
        delta_shift = (direction == 'LONG' and prev_delta <= 0 and delta > 0) or (direction == 'SHORT' and prev_delta >= 0 and delta < 0)
        if delta_shift:
            score += 2
            breakdown['delta_shift'] = 2
            
        wall_scan = binance_data.get('wall_scan', {})
        wall_dom = wall_scan.get('raw_dominant', 'NONE')
        wall_stability = wall_scan.get('stability_seconds', 0)
        wall_defend = (direction == 'LONG' and wall_dom == 'BID') or (direction == 'SHORT' and wall_dom == 'ASK')
        if wall_defend and wall_stability >= 15:
            score += 1
            breakdown['wall_defend'] = 1

        # Breakdown: POC-specific data
        breakdown['poc_reaction'] = reaction
        breakdown['poc_price'] = round(poc, 2)
        breakdown['poc_dist'] = round(poc_dist, 2)
        breakdown['poc_volume_at_react'] = round(volume_ratio, 2)
        breakdown['poc_wick_rejection'] = round(wick_ratio, 2)

        if score < self.score_threshold:
            # ... scoring reason logic ...
            return []

        # v48.0: MOD-21 BUG-6 — Store mismatch status in breakdown for main.py to handle
        # v51.4: Delta check — store if delta opposes direction
        delta = ctx.snapshot.delta if hasattr(ctx.snapshot, 'delta') else 0
        delta_mismatch = (direction == 'LONG' and delta < 0) or (direction == 'SHORT' and delta > 0)
        breakdown['pending_delta'] = delta_mismatch
        breakdown['entry_zone_min'] = poc - (atr_m5 * 0.2)
        breakdown['entry_zone_max'] = poc + (atr_m5 * 0.2)

        # Entry: at POC for reject, retest for break
        if reaction.startswith('REJECT'):
            entry = poc
        else:  # BREAK
            entry = current_price

        result = SignalResult(
            signal_type='VP_POC',
            direction=direction,
            entry_price=entry,
            score=score,
            threshold=self.score_threshold,
            score_breakdown=breakdown,
            regime=regime,
            m5_state=m5_state,
            h1_bias_level=ctx.h1_bias.bias_level,
            h1_dist_pct=binance_data.get('h1_ema_dist_pct', 0.0),
            der=der,
            delta=delta,
            wall_info=binance_data.get('wall_scan', {}).get('raw_dominant', 'NONE'),
            session=session,
            atr_m5=atr_m5,
            atr_ratio=ctx.snapshot.atr_ratio if hasattr(ctx.snapshot, 'atr_ratio') else 1.0,
        )

        results.append(result)
        return results

    def _detect_poc_reaction(self, candles: pd.DataFrame, poc: float, atr: float) -> Tuple[str, float]:
        """
        v43.7: Detect POC reaction from last 3 candles.
        
        Returns: (reaction_type, wick_ratio)
        - REJECT_UP: down to POC → bounce up
        - REJECT_DOWN: up to POC → bounce down
        - BREAK_UP: was below POC → close above + threshold
        - BREAK_DOWN: was above POC → close below - threshold
        - ACCEPT: all 3 candles close near POC (no edge)
        - UNKNOWN: unclear
        """
        if candles is None or len(candles) < 4:
            return 'UNKNOWN', 0.0

        threshold = atr * 0.3
        last3 = candles.iloc[-3:]
        curr = candles.iloc[-1]

        o_c = float(curr['open'])
        h_c = float(curr['high'])
        l_c = float(curr['low'])
        c_c = float(curr['close'])
        rng = h_c - l_c
        wick_ratio = 0.0

        # v51.4: False Breakout at POC (strongest reaction — institutional trap)
        prev = candles.iloc[-2]
        p_l = float(prev['low'])
        p_h = float(prev['high'])

        # FALSE_BO_UP: prev breached POC downward, current closes above
        if p_l < poc - threshold and c_c > poc:
            return 'FALSE_BO_UP', 0.0

        # FALSE_BO_DOWN: prev breached POC upward, current closes below
        if p_h > poc + threshold and c_c < poc:
            return 'FALSE_BO_DOWN', 0.0

        # Check REJECT patterns
        if l_c <= poc <= min(o_c, c_c) + threshold:
            # Price touched POC from above, bounced
            lower_wick = min(o_c, c_c) - l_c
            wick_ratio = lower_wick / rng if rng > 0 else 0
            if wick_ratio > 0.25:
                return 'REJECT_UP', wick_ratio

        if l_c <= poc + threshold and max(o_c, c_c) <= poc + threshold:
            # Price touched POC from below, rejected down
            upper_wick = h_c - max(o_c, c_c)
            wick_ratio = upper_wick / rng if rng > 0 else 0
            if wick_ratio > 0.25:
                return 'REJECT_DOWN', wick_ratio

        # Check BREAK patterns (3 candle transition)
        prev_closes = [float(candles.iloc[-i]['close']) for i in range(2, 4)]
        was_below = all(pc < poc - threshold for pc in prev_closes)
        was_above = all(pc > poc + threshold for pc in prev_closes)

        if was_below and c_c > poc + threshold:
            return 'BREAK_UP', 0.0
        if was_above and c_c < poc - threshold:
            return 'BREAK_DOWN', 0.0

        # Check ACCEPT: all 3 candles close near POC
        all_near = all(abs(float(c['close']) - poc) < threshold for _, c in last3.iterrows())
        if all_near:
            return 'ACCEPT', 0.0

        # v50.8: PROXIMITY — price at POC but no clear wick reaction
        if abs(c_c - poc) < threshold:
            return 'PROXIMITY', 0.0

        return 'UNKNOWN', 0.0
