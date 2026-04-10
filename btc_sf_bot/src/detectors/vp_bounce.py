\"\"\"
VP Bounce Detector — v77.0 MOD-98

Detects HVN Bounce signals: price touches HVN (institutional defend) + bounce + flow confirm.
MOD-48: Reaction-based direction detection for HVN/VAH/VAL levels.

v77.0: Score-to-Condition Migration (Two-Lane System)
- Removed scoring system (score_threshold=6)
- Implemented 4 Gates: Precise Reaction, Flow Alignment, Distance Guard, Market State
- Fixed score=1 when all conditions pass
\"\"\"
from typing import List, Dict, Any, Optional

import numpy as np
import pandas as pd

from src.detectors.base import BaseDetector, SignalResult, DetectionContext
from src.utils.logger import get_logger

logger = get_logger(__name__)


class VPBounceDetector(BaseDetector):
    signal_type = 'VP_BOUNCE'
    timing = 'CANDLE_CLOSE'

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.last_reject_reason = ''
        self.logger = logger

    def _detect_level_reaction(self, candles, level_price, atr):
        if candles is None or len(candles) < 4:
            return 'UNKNOWN', 0.0

        threshold = atr * 0.3
        curr = candles.iloc[-1]
        prev = candles.iloc[-2]

        c_c, c_l, c_h, c_o = float(curr['close']), float(curr['low']), float(curr['high']), float(curr['open'])
        p_l, p_h = float(prev['low']), float(prev['high'])
        rng = c_h - c_l

        if p_l < level_price - threshold and c_c > level_price: return 'FALSE_BO_UP', 0.0
        if p_h > level_price + threshold and c_c < level_price: return 'FALSE_BO_DOWN', 0.0

        if c_l <= level_price <= min(c_o, c_c) + threshold:
            lower_wick = min(c_o, c_c) - c_l
            wick_ratio = lower_wick / rng if rng > 0 else 0
            if wick_ratio > 0.25: return 'REJECT_UP', wick_ratio

        if max(c_o, c_c) - threshold <= level_price <= c_h:
            upper_wick = c_h - max(c_o, c_c)
            wick_ratio = upper_wick / rng if rng > 0 else 0
            if wick_ratio > 0.25: return 'REJECT_DOWN', wick_ratio

        prev_closes = [float(candles.iloc[-i]['close']) for i in range(2, 4)]
        if all(pc < level_price - threshold for pc in prev_closes) and c_c > level_price + threshold: return 'BREAK_UP', 0.0
        if all(pc > level_price + threshold for pc in prev_closes) and c_c < level_price - threshold: return 'BREAK_DOWN', 0.0
        if abs(c_c - level_price) < threshold: return 'PROXIMITY', 0.0
        return 'UNKNOWN', 0.0

    def detect(self, ctx: DetectionContext) -> List[SignalResult]:
        results = []
        binance_data = ctx.binance_data or {}
        current_price = ctx.current_price
        frvp = ctx.frvp_data or {}
        swing = frvp.get('layers', {}).get('swing_anchored', {})
        hvn_list = swing.get('hvn', [])
        session = ctx.session
        atr_m5 = ctx.snapshot.atr_m5 if hasattr(ctx.snapshot, 'atr_m5') else 0.0
        delta, der, regime, m5_state, candles_m5 = ctx.snapshot.delta, ctx.snapshot.der, ctx.regime.regime, ctx.snapshot.m5_state, ctx.candles_m5

        vah, val = swing.get('vah', 0), swing.get('val', 0)
        institutional_levels = list(hvn_list)
        if vah > 0: institutional_levels.append({'price': vah, 'type': 'VAH'})
        if val > 0: institutional_levels.append({'price': val, 'type': 'VAL'})

        if not institutional_levels:
            self.last_reject_reason = 'No HVN/VA data'
            return []

        nearest_hvn = min(institutional_levels, key=lambda h: abs(h['price'] - current_price))
        level_type, hvn_price = nearest_hvn.get('type', 'HVN'), nearest_hvn['price']
        dist = abs(current_price - hvn_price)

        reaction, wick_ratio = self._detect_level_reaction(candles_m5, hvn_price, atr_m5)
        if reaction in ('BREAK_UP', 'BREAK_DOWN'):
            self.last_reject_reason = f'Price broke through {level_type} without rejection ({reaction})'
            return []
        if reaction in ('FALSE_BO_UP', 'REJECT_UP'): direction = 'LONG'
        elif reaction in ('FALSE_BO_DOWN', 'REJECT_DOWN'): direction = 'SHORT'
        else:
            self.last_reject_reason = f'Unclear or no reaction at {level_type}: {reaction}'
            return []

        if der < 0.15:
            self.last_reject_reason = f'Low institutional flow (der={der:.2f} < 0.15)'
            return []
        if dist > atr_m5 * 1.0:
            self.last_reject_reason = f'Price too far from {level_type} ({dist:.1f} > 1.0 ATR)'
            return []
        if m5_state == 'EXHAUSTION':
            self.last_reject_reason = f'M5 state EXHAUSTION at {level_type} - risk of break'
            return []

        breakdown = {
            'gate1_reaction': reaction, 'gate1_wick_ratio': round(wick_ratio, 2),
            'gate2_flow': f'der={der:.2f}', 'gate3_dist': f'{dist:.1f} (level:{hvn_price:.1f})',
            'gate4_state': m5_state, 'bounce_level_type': level_type
        }
        result = SignalResult(
            signal_type='VP_BOUNCE', direction=direction, entry_price=hvn_price,
            score=1, threshold=1, score_breakdown=breakdown, regime=regime,
            m5_state=m5_state, h1_bias_level=ctx.h1_bias.bias_level,
            h1_dist_pct=binance_data.get('h1_ema_dist_pct', 0.0), der=der, delta=delta,
            wall_info=binance_data.get('wall_scan', {}).get('raw_dominant', 'NONE') if binance_data.get('wall_scan') else 'NONE',
            session=session, atr_m5=atr_m5, atr_ratio=ctx.snapshot.atr_ratio if hasattr(ctx.snapshot, 'atr_ratio') else 1.0,
        )
        results.append(result)
        return results
