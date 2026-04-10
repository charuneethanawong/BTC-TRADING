"""
VP Bounce Detector — v53.1

Detects HVN Bounce signals: price touches HVN (institutional defend) + bounce + flow confirm.
MOD-48: Reaction-based direction detection for HVN/VAH/VAL levels.
"""
from typing import List, Dict, Any, Optional

import numpy as np
import pandas as pd

from src.detectors.base import BaseDetector, SignalResult, DetectionContext
from src.utils.logger import get_logger

logger = get_logger(__name__)


class VPBounceDetector(BaseDetector):
    signal_type = 'VP_BOUNCE'
    timing = 'CANDLE_CLOSE'
    score_threshold = 6  # v51.2: reduced from 8 (HVN is structural — doesn't need high score)

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.last_reject_reason = ''
        self.logger = logger

    def _detect_level_reaction(self, candles, level_price, atr):
        """v53.1 MOD-48: Detect reaction at institutional level (HVN/VAH/VAL)."""
        if candles is None or len(candles) < 4:
            return 'UNKNOWN', 0.0

        threshold = atr * 0.3
        curr = candles.iloc[-1]
        prev = candles.iloc[-2]

        c_c = float(curr['close'])
        c_l = float(curr['low'])
        c_h = float(curr['high'])
        c_o = float(curr['open'])
        p_l = float(prev['low'])
        p_h = float(prev['high'])
        rng = c_h - c_l

        # FALSE_BO_UP: prev breached level down, current close above
        if p_l < level_price - threshold and c_c > level_price:
            return 'FALSE_BO_UP', 0.0

        # FALSE_BO_DOWN: prev breached level up, current close below
        if p_h > level_price + threshold and c_c < level_price:
            return 'FALSE_BO_DOWN', 0.0

        # REJECT_UP: low touched level, bounced up with wick
        if c_l <= level_price <= min(c_o, c_c) + threshold:
            lower_wick = min(c_o, c_c) - c_l
            wick_ratio = lower_wick / rng if rng > 0 else 0
            if wick_ratio > 0.25:
                return 'REJECT_UP', wick_ratio

        # REJECT_DOWN: high touched level, bounced down with wick
        if max(c_o, c_c) - threshold <= level_price <= c_h:
            upper_wick = c_h - max(c_o, c_c)
            wick_ratio = upper_wick / rng if rng > 0 else 0
            if wick_ratio > 0.25:
                return 'REJECT_DOWN', wick_ratio

        # BREAK_UP: was below, now close above
        prev_closes = [float(candles.iloc[-i]['close']) for i in range(2, 4)]
        if all(pc < level_price - threshold for pc in prev_closes) and c_c > level_price + threshold:
            return 'BREAK_UP', 0.0

        # BREAK_DOWN: was above, now close below
        if all(pc > level_price + threshold for pc in prev_closes) and c_c < level_price - threshold:
            return 'BREAK_DOWN', 0.0

        # PROXIMITY: near level but no reaction yet
        if abs(c_c - level_price) < threshold:
            return 'PROXIMITY', 0.0

        return 'UNKNOWN', 0.0

    def detect(self, ctx: DetectionContext) -> List[SignalResult]:
        """Detect VP_BOUNCE signals — HVN Bounce."""
        results = []

        binance_data = ctx.binance_data or {}
        current_price = ctx.current_price
        frvp = ctx.frvp_data or {}
        swing = frvp.get('layers', {}).get('swing_anchored', {})
        hvn_list = swing.get('hvn', [])
        lvn_list = swing.get('lvn', [])
        session = ctx.session
        atr_m5 = ctx.snapshot.atr_m5 if hasattr(ctx.snapshot, 'atr_m5') else 0.0
        
        # v50.3: Data Sync — ensure we use Snapshot values (calculated) not raw binance_data
        delta = ctx.snapshot.delta
        der = ctx.snapshot.der
        regime = ctx.regime.regime
        m5_state = ctx.snapshot.m5_state
        oi_change = ctx.snapshot.oi_change_pct if hasattr(ctx.snapshot, 'oi_change_pct') else 0.0
        
        volume_ratio = ctx.snapshot.volume_ratio_m5 if hasattr(ctx.snapshot, 'volume_ratio_m5') else 1.0
        candle_pattern = ctx.snapshot.m5_candle_pattern if hasattr(ctx.snapshot, 'm5_candle_pattern') else 'NONE'
        candles_m5 = ctx.candles_m5

        # v51.4: Add VAH/VAL as institutional bounce levels (not just HVN)
        vah = swing.get('vah', 0)
        val = swing.get('val', 0)
        institutional_levels = list(hvn_list)  # copy to avoid modifying original
        if vah and vah > 0:
            institutional_levels.append({'price': vah, 'volume': 0, 'type': 'VAH'})
        if val and val > 0:
            institutional_levels.append({'price': val, 'volume': 0, 'type': 'VAL'})

        if not institutional_levels:
            self.last_reject_reason = 'No HVN/VA data'
            return []

        # 1. Find nearest institutional level (HVN or VAH/VAL)
        nearest_hvn = min(institutional_levels, key=lambda h: abs(h['price'] - current_price))
        level_type = nearest_hvn.get('type', 'HVN')  # HVN by default, VAH/VAL if from VA
        hvn_price = nearest_hvn['price']
        dist = abs(current_price - hvn_price)

        # v51.4: Distance is scoring factor, not hard reject (institutional HVN can be far)

        # 2. Detect bounce direction — v53.1 MOD-48: Reaction-based direction (not just price > level)
        reaction, wick_ratio = self._detect_level_reaction(candles_m5, hvn_price, atr_m5)

        # v64.0 MOD-66: Shrinking Wall Detection
        # If price is near wall zone and wall shrank > 30% → reject (institution withdrew liquidity)
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
            
            # v64.0 MOD-66: Wall Ratio Cap — block if wall > 60x but stability < 60s (Liquidity Magnet trap)
            wall_ratio = wall_scan.get('raw_ratio', 0)
            if wall_ratio > 60 and wall_stability_sec < 60:
                self.last_reject_reason = f'Wall ratio {wall_ratio:.0f}x too high, stability {wall_stability_sec}s too low — Liquidity Magnet'
                return []

        # v64.0 MOD-67: Reaction-Only Entry — block UNKNOWN and PROXIMITY
        if reaction == 'UNKNOWN':
            self.last_reject_reason = f'No clear reaction at {level_type} — waiting for FALSE_BO or REJECT'
            return []
        
        if reaction == 'PROXIMITY':
            self.last_reject_reason = f'Level proximity — waiting for reaction at {level_type}'
            return []

        # Direction from reaction
        if reaction in ('FALSE_BO_UP', 'REJECT_UP', 'BREAK_UP'):
            direction = 'LONG'
        elif reaction in ('FALSE_BO_DOWN', 'REJECT_DOWN', 'BREAK_DOWN'):
            direction = 'SHORT'
        else:
            self.last_reject_reason = f'Unclear reaction: {reaction}'
            return []

        # 3. Delta direction check — use pending_delta for MOD-21 (30s window)
        delta_mismatch = False
        if (direction == 'LONG' and delta < 0) or (direction == 'SHORT' and delta > 0):
            delta_mismatch = True  # v51.2: Don't hard reject — mark for 30s alignment window

        # False signal filters
        if der < 0.15:
            self.last_reject_reason = f'[{direction}] DER {der:.2f} < 0.15 (no flow at {level_type})'
            return []
        if m5_state == 'EXHAUSTION':
            self.last_reject_reason = f'M5 {m5_state} at {level_type} (may break not bounce)'
            return []

        # Initialize breakdown
        breakdown = self._init_breakdown(ctx)

        # Scoring
        score = 0
        # HVN proximity scoring (distance-based)
        if dist <= atr_m5 * 0.5:
            score += 3
            breakdown['hvn_near'] = 3
        elif dist <= atr_m5 * 1.5:
            score += 2
            breakdown['hvn_near'] = 2
        elif dist <= atr_m5 * 3.0:
            score += 1
            breakdown['hvn_near'] = 1
        else:
            breakdown['hvn_near'] = 0

        # v53.1 MOD-48: Reaction scoring
        if reaction.startswith('FALSE_BO'):
            score += 4
            breakdown['bounce_reaction_score'] = 4
        elif reaction.startswith('REJECT'):
            score += 3
            breakdown['bounce_reaction_score'] = 3
        elif reaction.startswith('BREAK'):
            score += 2
            breakdown['bounce_reaction_score'] = 2

        # DER + flow aligned
        if der > 0.5:
            score += 3
            breakdown['der'] = 3
        elif der > 0.3:
            score += 2
            breakdown['der'] = 2

        # Volume
        if volume_ratio > 1.5:
            score += 2
            breakdown['vol'] = 2
        elif volume_ratio > 1.2:
            score += 1
            breakdown['vol'] = 1

        # Wick rejection
        if candle_pattern in ('HAMMER', 'ENGULFING_BULL') if direction == 'LONG' else candle_pattern in ('SHOOTING_STAR', 'ENGULFING_BEAR'):
            score += 1
            breakdown['rej'] = 1

        # HVN volume (high vol node = strong defend)
        if nearest_hvn.get('volume', 0) > 0:
            score += 1
            breakdown['hvn_vol'] = 1

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
        breakdown['bounce_level_type'] = level_type
        breakdown['bounce_reaction'] = reaction
        breakdown['bounce_wick_ratio'] = round(wick_ratio, 2)
        breakdown['bounce_level_price'] = round(hvn_price, 2)

        if score < self.score_threshold:
            # ... scoring reason logic ...
            return []

        # v48.0: MOD-21 BUG-6 — Store mismatch status in breakdown for main.py to handle
        breakdown['pending_delta'] = delta_mismatch
        breakdown['entry_zone_min'] = hvn_price - (atr_m5 * 0.2)
        breakdown['entry_zone_max'] = hvn_price + (atr_m5 * 0.2)

        # Entry: at HVN level
        entry = hvn_price
        # TP: POC or nearest LVN in direction
        poc = swing.get('poc', 0) or 0.0

        result = SignalResult(
            signal_type='VP_BOUNCE',
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
