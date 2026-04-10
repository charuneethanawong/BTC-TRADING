"""
VP POC Detector — v80.0 MOD-101

Detects POC Reaction: price reacts with POC (reject/break/accept).
v80.0: Gate-Based Migration & Sideway Liberation
- Removed scoring system (score_threshold=8)
- Removed M5 state block (allows SIDEWAY/RANGING)
- Implemented 4 Gates: Precise Reaction, Flow Alignment, Distance Guard, Market State
- Fixed score=1 when all conditions pass
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

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.last_reject_reason = ''
        self.logger = logger

    def detect(self, ctx: DetectionContext) -> List[SignalResult]:
        """Detect VP_POC signals — Condition-based (v80.0 MOD-101)"""
        results = []

        binance_data = ctx.binance_data or {}
        current_price = ctx.current_price
        frvp = ctx.frvp_data or {}
        swing = frvp.get('layers', {}).get('swing_anchored', {})
        poc = swing.get('poc', 0) or 0.0
        session = ctx.session
        atr_m5 = ctx.snapshot.atr_m5 if hasattr(ctx.snapshot, 'atr_m5') else 0.0

        # v50.3: Data Sync
        delta = ctx.snapshot.delta
        der = ctx.snapshot.der
        regime = ctx.regime.regime
        m5_state = ctx.snapshot.m5_state
        candles_m5 = ctx.candles_m5

        # Gate 0: Need POC
        if not poc:
            self.last_reject_reason = 'No POC data'
            return []

        # ===============================
        # GATE 1: PRECISE REACTION
        # ===============================
        reaction, wick_ratio = self._detect_poc_reaction(candles_m5, poc, atr_m5)

        # v64.0 MOD-66: Shrinking Wall Detection (same as VP_BOUNCE)
        wall_scan = binance_data.get('wall_scan', {})
        wall_shrink_pct = getattr(ctx.snapshot, 'wall_shrink_pct', 0.0)
        wall_zone_price = getattr(ctx.snapshot, 'wall_zone_price', 0.0)
        wall_stability_sec = getattr(ctx.snapshot, 'wall_stability_sec', 0)

        if wall_zone_price > 0 and atr_m5 > 0:
            wall_proximity = abs(current_price - wall_zone_price) / atr_m5
            if wall_proximity < 0.5 and wall_shrink_pct > 30:
                self.last_reject_reason = f'Wall shrank {wall_shrink_pct:.0f}% — institution withdrew liquidity'
                return []
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
        if reaction == 'PROXIMITY':
            self.last_reject_reason = 'POC Proximity — waiting for reaction'
            return []

        # Determine direction
        if reaction in ('FALSE_BO_UP', 'REJECT_UP', 'BREAK_UP'):
            direction = 'LONG'
        elif reaction in ('FALSE_BO_DOWN', 'REJECT_DOWN', 'BREAK_DOWN'):
            direction = 'SHORT'
        else:
            self.last_reject_reason = f'Unclear direction from reaction: {reaction}'
            return []

        # ===============================
        # GATE 2: FLOW ALIGNMENT
        # ===============================
        if der < 0.15:
            self.last_reject_reason = f'Low institutional flow (der={der:.2f} < 0.15)'
            return []

        # ===============================
        # GATE 3: DISTANCE GUARD
        # ===============================
        poc_dist = abs(current_price - poc)
        if poc_dist > atr_m5 * 1.0:
            self.last_reject_reason = f'Price too far from POC ({poc_dist:.1f} > 1.0 ATR)'
            return []

        # ===============================
        # GATE 4: MARKET STATE BLOCKER
        # ===============================
        if m5_state == 'EXHAUSTION':
            self.last_reject_reason = f'M5 state EXHAUSTION at POC - risk of break'
            return []

        # ===============================
        # ALL GATES PASSED — CREATE SIGNAL
        # ===============================
        breakdown = {
            'gate1_reaction': reaction,
            'gate1_wick_ratio': round(wick_ratio, 2),
            'gate2_flow': f'der={der:.2f}',
            'gate3_dist': f'{poc_dist:.1f} (level:{poc:.1f})',
            'gate4_state': m5_state,
            'poc_price': round(poc, 2)
        }

        entry = poc if reaction.startswith('REJECT') or reaction.startswith('FALSE_BO') else current_price

        result = SignalResult(
            signal_type='VP_POC',
            direction=direction,
            entry_price=entry,
            score=1,
            threshold=1,
            score_breakdown=breakdown,
            regime=regime,
            m5_state=m5_state,
            h1_bias_level=ctx.h1_bias.bias_level,
            h1_dist_pct=binance_data.get('h1_ema_dist_pct', 0.0),
            der=der,
            delta=delta,
            wall_info=binance_data.get('wall_scan', {}).get('raw_dominant', 'NONE') if binance_data.get('wall_scan') else 'NONE',
            session=session,
            atr_m5=atr_m5,
            atr_ratio=ctx.snapshot.atr_ratio if hasattr(ctx.snapshot, 'atr_ratio') else 1.0,
        )

        results.append(result)
        return results

    def _detect_poc_reaction(self, candles: pd.DataFrame, poc: float, atr: float) -> Tuple[str, float]:
        if candles is None or len(candles) < 4:
            return 'UNKNOWN', 0.0

        threshold = atr * 0.3
        last3 = candles.iloc[-3:]
        curr = candles.iloc[-1]

        o_c, h_c, l_c, c_c = float(curr['open']), float(curr['high']), float(curr['low']), float(curr['close'])
        rng = h_c - l_c
        wick_ratio = 0.0

        prev = candles.iloc[-2]
        p_l, p_h = float(prev['low']), float(prev['high'])
        if p_l < poc - threshold and c_c > poc: return 'FALSE_BO_UP', 0.0
        if p_h > poc + threshold and c_c < poc: return 'FALSE_BO_DOWN', 0.0

        if l_c <= poc <= min(o_c, c_c) + threshold:
            lower_wick = min(o_c, c_c) - l_c
            wick_ratio = lower_wick / rng if rng > 0 else 0
            if wick_ratio > 0.25: return 'REJECT_UP', wick_ratio

        if l_c <= poc + threshold and max(o_c, c_c) <= poc + threshold:
            upper_wick = h_c - max(o_c, c_c)
            wick_ratio = upper_wick / rng if rng > 0 else 0
            if wick_ratio > 0.25: return 'REJECT_DOWN', wick_ratio

        prev_closes = [float(candles.iloc[-i]['close']) for i in range(2, 4)]
        if all(pc < poc - threshold for pc in prev_closes) and c_c > poc + threshold: return 'BREAK_UP', 0.0
        if all(pc > poc + threshold for pc in prev_closes) and c_c < poc - threshold: return 'BREAK_DOWN', 0.0

        if all(abs(float(c['close']) - poc) < threshold for _, c in last3.iterrows()): return 'ACCEPT', 0.0
        if abs(c_c - poc) < threshold: return 'PROXIMITY', 0.0
        return 'UNKNOWN', 0.0
