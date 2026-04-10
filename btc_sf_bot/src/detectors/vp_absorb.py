"""
VP Absorb Detector — v43.7

Detects Volume Decline at HVN: volume declining + DER high + Wall hold = institutional absorb.
"""
from typing import List, Dict, Any, Optional

import numpy as np
import pandas as pd

from src.detectors.base import BaseDetector, SignalResult, DetectionContext
from src.utils.logger import get_logger

logger = get_logger(__name__)


class VPAbsorbDetector(BaseDetector):
    signal_type = 'VP_ABSORB'
    timing = 'CANDLE_CLOSE'
    score_threshold = 7  # v46.0: MOD-13 reduced from 8 to 7

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.last_reject_reason = ''
        self.logger = logger

    def detect(self, ctx: DetectionContext) -> List[SignalResult]:
        """Detect VP_ABSORB signals — Volume Decline at HVN."""
        results = []

        binance_data = ctx.binance_data or {}
        current_price = ctx.current_price
        frvp = ctx.frvp_data or {}
        swing = frvp.get('layers', {}).get('swing_anchored', {})
        hvn_list = swing.get('hvn', [])
        session = ctx.session
        atr_m5 = ctx.snapshot.atr_m5 if hasattr(ctx.snapshot, 'atr_m5') else 0.0
        
        # v50.3: Data Sync — ensure we use Snapshot values (calculated) not raw binance_data
        delta = ctx.snapshot.delta
        # v50.4: Audit - Forensic Fix - Define direction early for reject message
        direction = 'SHORT' if delta > 0 else 'LONG'
        der = ctx.snapshot.der
        regime = ctx.regime.regime
        m5_state = ctx.snapshot.m5_state
        
        wall_scan = binance_data.get('wall_scan', {})
        candles_m5 = ctx.candles_m5

        # Gate: Need HVN data
        if not hvn_list:
            self.last_reject_reason = 'No HVN data'
            return []

        # 1. Price must be near HVN
        nearest_hvn = min(hvn_list, key=lambda h: abs(h['price'] - current_price))
        hvn_price = nearest_hvn['price']
        dist = abs(current_price - hvn_price)

        # v51.4: Distance is scoring factor, not hard reject (institutional HVN can be far)
        # Closer = higher score, further = lower score

        # 2. Volume declining (recent 3 candle < avg)
        vol_declining = False
        if candles_m5 is not None and len(candles_m5) >= 10:
            vols = candles_m5['volume'].values
            vol_recent_3 = np.mean(vols[-3:])
            vol_avg_10 = np.mean(vols[-10:])
            vol_declining = vol_recent_3 < vol_avg_10 * 0.8

        if not vol_declining:
            v_curr = round(vol_recent_3, 0)
            v_avg = round(vol_avg_10, 0)
            threshold = round(vol_avg_10 * 0.8, 0)
            self.last_reject_reason = f'[{direction}] Volume not declining (Recent {v_curr} >= {threshold} [80% of Avg {v_avg}])'
            return []

        # 3. DER must be high (flow still directional despite volume decline)
        if der < 0.15:  # v51.2: reduced — absorption occurs during weak flow
            self.last_reject_reason = f'[{direction}] DER {der:.2f} < 0.15 (flow drying up)'
            return []

        # 4. Wall hold (wall not disappearing)
        wall_ratio = wall_scan.get('raw_ratio', 0)
        if wall_ratio < 1.9:  # v43.7.1: Allow float precision tolerance (was < 2.0)
            self.last_reject_reason = f'Wall ratio {wall_ratio:.1f} < 1.9 (no wall hold)'
            return []
        
        # v64.0 MOD-66: Shrinking Wall Detection (Anti-Spoofing)
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
            if wall_ratio > 60 and wall_stability_sec < 60:
                self.last_reject_reason = f'Wall ratio {wall_ratio:.0f}x too high, stability {wall_stability_sec}s too low — Liquidity Magnet'
                return []
            
        # v48.0: MOD-24 — Imbalance Confirmation
        # True absorption = high delta but neutral imbalance (someone is absorbing the flow)
        imb_avg = ctx.snapshot.imbalance_avg_5m if hasattr(ctx.snapshot, 'imbalance_avg_5m') else 1.0
        if imb_avg < 0.8 or imb_avg > 1.2:
            self.last_reject_reason = f'Imbalance {imb_avg:.2f} outside neutral range [0.8–1.2] (driving, not absorbing)'
            return []


        # Initialize breakdown
        breakdown = self._init_breakdown(ctx)

        # Scoring
        score = 0

        # HVN proximity scoring (distance-based)
        if dist <= atr_m5 * 0.5:
            score += 3
            breakdown['hvn_near'] = 3   # very close — institutional defend zone
        elif dist <= atr_m5 * 1.5:
            score += 2
            breakdown['hvn_near'] = 2
        elif dist <= atr_m5 * 3.0:
            score += 1
            breakdown['hvn_near'] = 1
        else:
            breakdown['hvn_near'] = 0   # too far — score 0 but not rejected

        # Volume declining
        score += 2
        breakdown['vol_decline'] = 2

        # DER high (flow still active)
        if der > 0.6:
            score += 2
            breakdown['der'] = 2
        else:
            score += 1
            breakdown['der'] = 1

        # Wall hold
        if wall_ratio >= 5.0:
            score += 2
            breakdown['wall'] = 2
        elif wall_ratio >= 2.0:
            score += 1
            breakdown['wall'] = 1

        # Contraction (atr_ratio < 0.7)
        atr_ratio = ctx.snapshot.atr_ratio if hasattr(ctx.snapshot, 'atr_ratio') else 1.0
        if atr_ratio < 0.7:
            score += 1
            breakdown['cont'] = 1

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

        # v48.0: MOD-21 — Delta alignment window fields (direction derived from delta — no mismatch possible)
        breakdown['pending_delta'] = False
        breakdown['entry_zone_min'] = hvn_price - (atr_m5 * 0.2)
        breakdown['entry_zone_max'] = hvn_price + (atr_m5 * 0.2)

        result = SignalResult(
            signal_type='VP_ABSORB',
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
            wall_info=f"{wall_scan.get('raw_dominant', 'NONE')} {wall_ratio:.1f}x",
            session=session,
            atr_m5=atr_m5,
            atr_ratio=atr_ratio,
        )

        results.append(result)
        return results
