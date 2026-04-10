"""
VP Breakout Detector — v57.0 MOD-53

Detects VA Breakout + POC Shift signals: price breaks VAH/VAL + volume confirm + POC shift
= institutional accept new range.
"""
from typing import List, Dict, Any, Optional

import numpy as np
import pandas as pd

from src.detectors.base import BaseDetector, SignalResult, DetectionContext
from src.utils.logger import get_logger

logger = get_logger(__name__)


class VPBreakoutDetector(BaseDetector):
    signal_type = 'VP_BREAKOUT'
    timing = BaseDetector.TIMING_60S  # v57.0: Catch breakout in real-time
    score_threshold = 6  # v57.0: Lowered to catch early momentum # v47.0: MOD-19 reduced from 9 to 8

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.last_reject_reason = ''
        self.logger = logger

    def detect(self, ctx: DetectionContext) -> List[SignalResult]:
        """Detect VP_BREAKOUT signals — VA Breakout + POC Shift."""
        results = []

        binance_data = ctx.binance_data or {}
        current_price = ctx.current_price
        frvp = ctx.frvp_data or {}
        swing = frvp.get('layers', {}).get('swing_anchored', {})
        vah = swing.get('vah', 0) or 0.0
        val = swing.get('val', 0) or 0.0
        session = ctx.session
        atr_m5 = ctx.snapshot.atr_m5 if hasattr(ctx.snapshot, 'atr_m5') else 0.0
        
        # v50.3: Data Sync — ensure we use Snapshot values (calculated) not raw binance_data
        delta = ctx.snapshot.delta
        der = ctx.snapshot.der
        der_persistence = ctx.snapshot.der_persistence if hasattr(ctx.snapshot, 'der_persistence') else 0
        regime = ctx.regime.regime
        m5_state = ctx.snapshot.m5_state
        oi_change = ctx.snapshot.oi_change_pct if hasattr(ctx.snapshot, 'oi_change_pct') else 0.0
        
        poc_shift = getattr(ctx.snapshot, 'vp_poc_shift', 0.0)
        poc_shift_dir = getattr(ctx.snapshot, 'vp_poc_shift_direction', 'NEUTRAL')
        volume_ratio = ctx.snapshot.volume_ratio_m5 if hasattr(ctx.snapshot, 'volume_ratio_m5') else 1.0

        # Gate: Need VA levels
        if not vah or not val:
            self.last_reject_reason = 'No VA levels available'
            return []

        # 1. Detect breakout
        direction = None
        if current_price > vah and volume_ratio > 1.2:
            direction = 'LONG'
        elif current_price < val and volume_ratio > 1.2:
            direction = 'SHORT'

        if not direction:
            self.last_reject_reason = f'No breakout detected (vol:{volume_ratio:.1f}x)'
            return []

        # v51.2: POC shift is now scoring bonus, not hard gate
        # POC is a lagging indicator — shift comes 15-30min AFTER breakout
        # Volume + price breakout alone is sufficient to confirm

        # 3. False breakout detection
        is_false, false_reasons = self._detect_false_breakout(ctx, direction)
        if is_false:
            self.last_reject_reason = f'[{direction}] False breakout: {",".join(false_reasons)}'
            return []

        # Initialize breakdown
        breakdown = self._init_breakdown(ctx)

        # Scoring
        score = 0

        # Volume
        if volume_ratio > 2.0:
            score += 3
            breakdown['vol'] = 3
        elif volume_ratio > 1.5:
            score += 2
            breakdown['vol'] = 2
        elif volume_ratio > 1.2:
            score += 1
            breakdown['vol'] = 1

        # POC shift confirm
        if (direction == 'LONG' and poc_shift > 0) or (direction == 'SHORT' and poc_shift < 0):
            score += 2
            breakdown['poc_shift'] = 2

                # POC shift confirm (lagging but high weight if it happened)
        if (direction == 'LONG' and poc_shift > 0) or (direction == 'SHORT' and poc_shift < 0):
            score += 2
            breakdown['poc_shift'] = 2
# DER direction aligned
        if der > 0.6:
            score += 3
            breakdown['der'] = 3
        elif der > 0.4:
            score += 2
            breakdown['der'] = 2

        # OI increase (new positions)
        if oi_change > 0.05:
            score += 1
            breakdown['oi'] = 1

        # LVN ahead (speed zone)
        lvn_list = swing.get('lvn', [])
        if lvn_list:
            if direction == 'LONG':
                lvn_ahead = [l for l in lvn_list if l['price'] > current_price]
            else:
                lvn_ahead = [l for l in lvn_list if l['price'] < current_price]
            if lvn_ahead:
                score += 1
                breakdown['lvn_ahead'] = 1

        # Break distance > 0.3 ATR
        break_dist = abs(current_price - (vah if direction == 'LONG' else val))
        if break_dist > atr_m5 * 0.3:
            score += 1
            breakdown['break_dist'] = 1

        # False breakout data
        breakdown['false_bo_score'] = len(false_reasons)
        breakdown['false_bo_reasons'] = ','.join(false_reasons) if false_reasons else 'none'
        breakdown['false_bo_volume'] = round(volume_ratio, 2)
        
        breakdown['false_bo_oi_change'] = round(oi_change, 4)
        breakdown['false_bo_der_pers'] = der_persistence

        if score < self.score_threshold:
            reason = f"Score {score} < {self.score_threshold}"
                        # v43.9: Full Scorecard Log - Show all scoring components (even if 0)
            scorecard_keys = ['der', 'wall', 'vol', 'er', 'pers', 'oi', 'cont', 'rej', 'reaction', 'wick_rej', 'hvn_touch', 'hvn_vol', 'breakout_vol', 'poc_shift_pts', 'wall_hold', 'false_bo_score']
            scoring_items = []
            for k in scorecard_keys:
                if k in breakdown:
                    v = breakdown[k]
                    scoring_items.append(f"{k}:{v}")
            
            # Add False BO warning if likely
            if breakdown.get('false_bo_likely'):
                scoring_items.append("false_bo:TRUE!!")
            
            details = ", ".join(scoring_items)
            self.last_reject_reason = f"{reason} ({details})" if details else reason
            return []

        # Entry: Retest VAH/VAL (don't chase breakout bar)
        entry = vah if direction == 'LONG' else val

        result = SignalResult(
            signal_type='VP_BREAKOUT',
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

    def _detect_false_breakout(self, ctx: DetectionContext, direction: str) -> tuple:
        """v53.1: False Breakout Detection — only real indicators."""
        reasons = []

        volume_ratio = ctx.snapshot.volume_ratio_m5 if hasattr(ctx.snapshot, 'volume_ratio_m5') else 1.0
        oi_change = ctx.snapshot.oi_change_pct if hasattr(ctx.snapshot, 'oi_change_pct') else 0.0

        # 1. Volume ต่ำตอน break (no conviction)
        if volume_ratio < 1.2:
            reasons.append('LOW_VOLUME')

        # 2. OI ลด (liquidation not new position)
        if oi_change < -0.05:
            reasons.append('OI_DECLINING')

        # v53.1: Removed — not real false BO indicators:
        # NO_POC_SHIFT (POC lags 15-30 min after breakout)
        # DER_NOT_SUSTAINED (breakout just started = persistence 1 is normal)
        # LVN_SWEEP (LVN = speed zone ahead = good, not fake)

        is_false = len(reasons) >= 2
        return is_false, reasons
