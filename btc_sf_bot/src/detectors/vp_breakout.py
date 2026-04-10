"""
VP Breakout Detector — v77.0 MOD-97

Detects VA Breakout + POC Shift signals: price breaks VAH/VAL + volume confirm + POC shift
= institutional accept new range.

v77.0: Score-to-Condition Migration (Two-Lane System)
- Removed scoring system (score_threshold=6)
- Implemented 4 Gates: Price Breakout, Order Flow Alignment, False Breakout Guard, Wall Resistance
- Fixed score=1 when all conditions pass
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
    # v77.0: REMOVED score_threshold - now condition-based system

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.last_reject_reason = ''
        self.logger = logger

    def detect(self, ctx: DetectionContext) -> List[SignalResult]:
        """Detect VP_BREAKOUT signals — Condition-based (Two-Lane System) v77.0"""
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
        regime = ctx.regime.regime
        m5_state = ctx.snapshot.m5_state
        oi_change = ctx.snapshot.oi_change_pct if hasattr(ctx.snapshot, 'oi_change_pct') else 0.0
        volume_ratio = ctx.snapshot.volume_ratio_m5 if hasattr(ctx.snapshot, 'volume_ratio_m5') else 1.0

        # Gate 0: Need VA levels
        if not vah or not val:
            self.last_reject_reason = 'No VA levels available'
            return []

        # ===============================
        # GATE 1: PRICE BREAKOUT (Trigger)
        # ===============================
        direction = None
        if current_price > vah and volume_ratio > 1.2:
            direction = 'LONG'
        elif current_price < val and volume_ratio > 1.2:
            direction = 'SHORT'

        if not direction:
            self.last_reject_reason = f'No breakout: price not breaking VAH/VAL or vol<1.2x (vol:{volume_ratio:.1f}x)'
            return []

        # ===============================
        # GATE 2: ORDER FLOW ALIGNMENT (Confirmation)
        # ===============================
        if direction == 'LONG':
            # LONG needs: der > 0.4 AND delta > 0
            if not (der > 0.4 and delta > 0):
                self.last_reject_reason = f'LONG order flow not aligned: der={der:.2f}, delta={delta:.0f} (need der>0.4 and delta>0)'
                return []
        else:  # SHORT
            # SHORT needs: der > 0.4 AND delta < 0
            if not (der > 0.4 and delta < 0):
                self.last_reject_reason = f'SHORT order flow not aligned: der={der:.2f}, delta={delta:.0f} (need der>0.4 and delta<0)'
                return []

        # ===============================
        # GATE 3: FALSE BREAKOUT GUARD (Blocker)
        # ===============================
        # Block if M5 state is EXHAUSTION
        if m5_state == 'EXHAUSTION':
            self.last_reject_reason = 'M5 state EXHAUSTION - false breakout likely'
            return []
        
        # Block if OI declining (liquidation, not new positions)
        if oi_change < -0.05:
            self.last_reject_reason = f'OI declining {oi_change:.1%} - likely liquidation not new positions'
            return []

        # ===============================
        # GATE 4: WALL RESISTANCE BLOCK (Blocker)
        # ===============================
        wall_scan = binance_data.get('wall_scan', {})
        wall_ratio = 0
        if wall_scan:
            wall_ratio = wall_scan.get('wall_ratio', 0)
            wall_distance = wall_scan.get('distance_pct', 999)
            if direction == 'LONG':
                # For LONG breakout, check wall ABOVE current price
                if wall_ratio > 20 and wall_distance < 5:
                    self.last_reject_reason = f'Wall resistance {wall_ratio:.0f}x blocks LONG breakout (distance:{wall_distance:.1f}%)'
                    return []
            else:  # SHORT
                # For SHORT breakout, check wall BELOW current price
                if wall_ratio > 20 and wall_distance < 5:
                    self.last_reject_reason = f'Wall resistance {wall_ratio:.0f}x blocks SHORT breakout (distance:{wall_distance:.1f}%)'
                    return []

        # ===============================
        # ALL GATES PASSED — CREATE SIGNAL
        # ===============================
        
        # Entry: Retest VAH/VAL (don't chase breakout bar)
        entry = vah if direction == 'LONG' else val
        
        # v77.0: Build breakdown for telemetry
        breakdown = {
            'gate1_price_breakout': True,
            'gate2_order_flow': f'der={der:.2f},delta={delta:.0f}',
            'gate3_false_bo': f'oi={oi_change:.1%},state={m5_state}',
            'gate4_wall': f'ratio={wall_ratio if wall_scan else 0:.0f}x',
            'volume_ratio': round(volume_ratio, 2),
            'atr_m5': round(atr_m5, 2),
        }

        result = SignalResult(
            signal_type='VP_BREAKOUT',
            direction=direction,
            entry_price=entry,
            score=1,  # v77.0: Fixed score since condition-based
            threshold=1,  # v77.0: No threshold needed
            score_breakdown=breakdown,
            regime=regime,
            m5_state=m5_state,
            h1_bias_level=ctx.h1_bias.bias_level,
            h1_dist_pct=binance_data.get('h1_ema_dist_pct', 0.0),
            der=der,
            delta=delta,
            wall_info=wall_scan.get('raw_dominant', 'NONE') if wall_scan else 'NONE',
            session=session,
            atr_m5=atr_m5,
            atr_ratio=ctx.snapshot.atr_ratio if hasattr(ctx.snapshot, 'atr_ratio') else 1.0,
        )

        results.append(result)
        return results

    # v77.0: _detect_false_breakout logic integrated into Gate 3 above
    # Remaining for backwards compatibility if needed elsewhere
