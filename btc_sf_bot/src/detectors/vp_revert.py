"""
VP Revert Detector — v43.7

Detects Mean Revert to POC: price outside Value Area + POC stable = overextended → revert to POC.
"""
from typing import List, Dict, Any, Optional

import numpy as np
import pandas as pd

from src.detectors.base import BaseDetector, SignalResult, DetectionContext
from src.utils.logger import get_logger

logger = get_logger(__name__)


class VPRevertDetector(BaseDetector):
    signal_type = 'VP_REVERT'
    timing = 'CANDLE_CLOSE'
    score_threshold = 7  # v49.1: reduced from 6 to 7

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.last_reject_reason = ''
        self.logger = logger

    def detect(self, ctx: DetectionContext) -> List[SignalResult]:
        """Detect VP_REVERT signals — Mean Revert to POC."""
        results = []

        binance_data = ctx.binance_data or {}
        current_price = ctx.current_price
        frvp = ctx.frvp_data or {}
        swing = frvp.get('layers', {}).get('swing_anchored', {})
        vah = swing.get('vah', 0) or 0.0
        val = swing.get('val', 0) or 0.0
        poc = swing.get('poc', 0) or 0.0
        session = ctx.session
        atr_m5 = ctx.snapshot.atr_m5 if hasattr(ctx.snapshot, 'atr_m5') else 0.0
        
        # v50.3: Data Sync — ensure we use Snapshot values (calculated) not raw binance_data
        delta = ctx.snapshot.delta
        der = ctx.snapshot.der
        regime = ctx.regime.regime
        m5_state = ctx.snapshot.m5_state
        
        wall_scan = binance_data.get('wall_scan', {})
        
        price_vs_va = getattr(ctx.snapshot, 'vp_price_vs_va', 'INSIDE')
        
        # v60.0: POC shift from snapshot
        poc_shift = getattr(ctx.snapshot, 'vp_poc_shift', 0.0) or 0.0

        # 1. Direction: revert toward POC
        direction = 'SHORT' if price_vs_va == 'ABOVE_VA' else 'LONG'

        # Gate: Need VA levels
        if not vah or not val:
            self.last_reject_reason = 'No VA levels available'
            return []

        # 1. Price must be outside VA
        if price_vs_va == 'INSIDE':
            self.last_reject_reason = f'[{direction}] Price inside VA (no revert edge)'
            return []

        # 2. POC stable (no shift = institutional not changing position = price will revert)
        if abs(poc_shift) > atr_m5 * 0.3:
            self.last_reject_reason = f'[{direction}] POC shift {poc_shift:.0f} > 0.3 ATR (new trend, not revert)'
            return []
        
        # v48.0: MOD-22 BUG-7 — Regime Guard (Block in TRENDING)
        if regime == 'TRENDING':
            self.last_reject_reason = f'[{direction}] Market in {regime} regime (revert is high risk)'
            return []

        # v64.0 MOD-66: Shrinking Wall Detection (Anti-Spoofing)
        wall_shrink_pct = getattr(ctx.snapshot, 'wall_shrink_pct', 0.0)
        wall_zone_price = getattr(ctx.snapshot, 'wall_zone_price', 0.0)
        wall_stability_sec = getattr(ctx.snapshot, 'wall_stability_sec', 0)
        wall_ratio = wall_scan.get('raw_ratio', 0)
        
        if wall_zone_price > 0 and atr_m5 > 0:
            wall_proximity = abs(current_price - wall_zone_price) / atr_m5
            # If price within 0.5 ATR of wall AND wall shrank > 30% → reject
            if wall_proximity < 0.5 and wall_shrink_pct > 30:
                self.last_reject_reason = f'Wall shrank {wall_shrink_pct:.0f}% — institution withdrew liquidity'
                return []
            
            # Wall Ratio Cap — block if wall > 60x but stability < 60s (Liquidity Magnet trap)
            if wall_ratio > 60 and wall_stability_sec < 60:
                self.last_reject_reason = f'Wall ratio {wall_ratio:.0f}x too high, stability {wall_stability_sec}s too low — Liquidity Magnet'
                return []

        # v64.0 MOD-68: High Force Block — don't countertrend while institutional force is strong
        if der > 0.30:
            self.last_reject_reason = f'[{direction}] DER {der:.2f} > 0.30 — institutional force too strong to revert'
            return []

        # v64.0 MOD-68: Context Guard — block RANGING (Regime) + TRENDING (M5 State) combo
        if regime == 'RANGING' and m5_state == 'TRENDING':
            self.last_reject_reason = f'[{direction}] RANGING regime + TRENDING M5 — false revert signal'
            return []

        # v64.0 MOD-68: Inverse Wall (Strict) — don't revert if wall > 25x blocks direction
        wall_ratio = wall_scan.get('raw_ratio', 0)
        wall_dom = wall_scan.get('raw_dominant', 'NONE')
        # Inverse wall = wall opposes revert direction (BID for SHORT revert, ASK for LONG revert)
        inverse_wall = (direction == 'LONG' and wall_dom == 'ASK') or (direction == 'SHORT' and wall_dom == 'BID')
        if inverse_wall and wall_ratio > 25:
            self.last_reject_reason = f'[{direction}] Inverse wall {wall_ratio:.0f}x > 25 — institutional defending'
            return []


        # Initialize breakdown
        breakdown = self._init_breakdown(ctx)

        # Scoring
        score = 0

        # Price outside VA
        score += 2
        breakdown['outside_va'] = 2

        # POC stable (no shift)
        score += 2
        breakdown['poc_stable'] = 2

        # POC distance > 1.0 ATR (overextended)
        poc_dist = abs(current_price - poc)
        poc_dist_atr = poc_dist / atr_m5 if atr_m5 > 0 else 0
        if poc_dist_atr > 1.0:
            score += 2
            breakdown['poc_dist'] = 2
        elif poc_dist_atr > 0.5:
            score += 1
            breakdown['poc_dist'] = 1

        # DER declining (momentum weakening = ready to revert)
        if abs(poc_shift) > atr_m5 * 0.3:
            self.last_reject_reason = f'[{direction}] POC shift {poc_shift:.0f} > 0.3 ATR (new trend, not revert)'
            return []
        
        if der < 0.15:  # v51.2: reduced — revert doesn't need strong flow
            score += 1
            breakdown['der_decline'] = 1

        # Wall support revert direction
        wall_ratio = wall_scan.get('raw_ratio', 0)
        wall_dom = wall_scan.get('raw_dominant', 'NONE')
        if (direction == 'LONG' and wall_dom == 'BID') or (direction == 'SHORT' and wall_dom == 'ASK'):
            if wall_ratio >= 2.0:
                score += 1
                breakdown['wall'] = 1

        # False breakout check
        fb_score, fb_indicators = self._calc_false_breakout_score(ctx, direction)
        breakdown['false_bo_score'] = fb_score
        breakdown['false_bo_likely'] = fb_score >= 3

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

        result = SignalResult(
            signal_type='VP_REVERT',
            direction=direction,
            entry_price=current_price,
            score=score,
            threshold=self.score_threshold,
            score_breakdown=breakdown,
            regime=regime,
            m5_state=m5_state,
            h1_bias_level=ctx.h1_bias.bias_level,
            h1_dist_pct=binance_data.get('h1_ema_dist_pct', 0.0),
            der=der,
            delta=delta,
            wall_info=f"{wall_dom} {wall_ratio:.1f}x",
            session=session,
            atr_m5=atr_m5,
            atr_ratio=ctx.snapshot.atr_ratio if hasattr(ctx.snapshot, 'atr_ratio') else 1.0,
        )

        results.append(result)
        return results
