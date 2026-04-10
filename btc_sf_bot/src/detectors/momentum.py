"""
Momentum Detector — v72.0 MOD-91 (Two-Lane System)

Dual-Path Momentum (Sniper & Rocket):
- MOD-62: Dual-Path Logic (Path A: Sniper <40pts, Path B: Rocket >40pts with Wall>50x/OI>0.1%/DER>0.8)
- MOD-63: Market State Optimization (RECOVERY boost, EXHAUSTION/SIDEWAY block)

Direction: m5_bias (not delta) — except fast-trigger uses delta
  - BULLISH → LONG
  - BEARISH → SHORT
  - NEUTRAL → reject (or use delta in fast-trigger)
"""
from typing import List, Dict, Any, Optional

import numpy as np
import pandas as pd

from src.detectors.base import BaseDetector, SignalResult, DetectionContext
from src.utils.logger import get_logger

logger = get_logger(__name__)


class MomentumDetector(BaseDetector):
    signal_type = 'MOMENTUM'
    timing = BaseDetector.TIMING_60S
    score_threshold = 1  # v59.0: Conditions only, no score threshold

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.last_reject_reason = ''
        self.logger = logger
        # v59.0 MOD-56: Velocity tracking
        self._last_price: Optional[float] = None
        self._last_price_time: Optional[float] = None

    def detect(self, ctx: DetectionContext) -> List[SignalResult]:
        """Detect MOMENTUM signals from conditions (no score) with fast-trigger."""
        results = []

        binance_data = ctx.binance_data or {}
        snapshot = ctx.snapshot

        # Extract data
        der = ctx.snapshot.der
        delta = ctx.snapshot.delta
        h1_dist = binance_data.get('h1_ema_dist_pct', 0.0)
        h1_bias = ctx.h1_bias.bias
        wall_scan = binance_data.get('wall_scan')
        current_price = ctx.current_price
        atr_m5 = ctx.snapshot.atr_m5 if hasattr(ctx.snapshot, 'atr_m5') else 0.0
        m5_state = ctx.snapshot.m5_state
        m5_bias = snapshot.m5_bias if hasattr(snapshot, 'm5_bias') else 'NEUTRAL'
        m5_bias_level = snapshot.m5_bias_level if hasattr(snapshot, 'm5_bias_level') else 'NEUTRAL'
        regime = ctx.regime.regime
        active_lane = 'NONE'
        session = ctx.session

        # v54.0 MOD-49: Advanced Parameters
        er = snapshot.m5_efficiency if hasattr(snapshot, 'm5_efficiency') else 0.5
        pers = snapshot.der_persistence if hasattr(snapshot, 'der_persistence') else 0
        der_sustainability = snapshot.der_sustainability if hasattr(snapshot, 'der_sustainability') else 'NEUTRAL'
        oi_change = snapshot.oi_change_pct if hasattr(snapshot, 'oi_change_pct') else 0.0
        atr_ratio = snapshot.atr_ratio if hasattr(snapshot, 'atr_ratio') else 1.0
        candle_pattern = snapshot.m5_candle_pattern if hasattr(snapshot, 'm5_candle_pattern') else 'NONE'
        volume_ratio = snapshot.volume_ratio_m5 if hasattr(snapshot, 'volume_ratio_m5') else 1.0
        imbalance = snapshot.imbalance if hasattr(snapshot, 'imbalance') else 1.0
        imbalance_avg = snapshot.imbalance_avg_5m if hasattr(snapshot, 'imbalance_avg_5m') else 1.0
        m5_ema_position = snapshot.m5_ema_position if hasattr(snapshot, 'm5_ema_position') else 'BETWEEN'
        
        # v69.0 MOD-81: Force Alignment Guard - get der_direction from snapshot
        der_direction = ctx.snapshot.der_direction if hasattr(ctx.snapshot, 'der_direction') else 'NEUTRAL'
        
        # v62.0 MOD-62: M5 Distance in pts (needed early for rocket calculation)
        m5_dist_pts = abs(getattr(snapshot, 'm5_dist_pct', 0)) * current_price / 100 if current_price > 0 else 0
        
        # Wall ratio for rocket calculation
        wall_ratio = wall_scan.get('raw_ratio', 0) if wall_scan else 0

        # v59.0: Get current timestamp for velocity tracking
        current_time = snapshot.time if hasattr(snapshot, 'time') else None

        # v65.2 MOD-70: Fast Trigger - must be defined BEFORE bad_states check
        fast_trigger_enabled = der > 0.8 and volume_ratio > 1.5

        # === v70.0 MOD-84: Force-First preliminary direction ===
        # Use m5_bias as base direction
        direction_bias = 'LONG' if m5_bias in ('BULLISH', 'STRONG', 'STRONG_EARLY', 'CONFIRMED', 'CONFIRMED+', 'EARLY', 'EARLY_STRUCTURE') else \
                         'SHORT' if m5_bias in ('BEARISH', 'STRONG_BEARISH', 'BEARISH_CONFIRMED') else 'NEUTRAL'
        
        # Check Force Alignment
        der_aligned = (direction_bias == der_direction)
        imb_aligned = (direction_bias == 'LONG' and imbalance > 1.10) or (direction_bias == 'SHORT' and imbalance < 0.90)  # v74.0: Relaxed
        is_perfect_alignment = der_aligned and imb_aligned and (direction_bias != 'NEUTRAL')
        
        # direction_raw for fast-trigger mode (defined early for later use)
        direction_raw = 'LONG' if delta > 0 else 'SHORT'
        
        # === v65.2 MOD-70: Rocket Mode Calculation ===
        rocket_by_wall = (wall_ratio > 30 and der > 0.7)
        rocket_by_force = (der > 0.80 and volume_ratio > 1.5)
        
        # === v72.0 MOD-91: Two-Lane Momentum System ===
        # Lane A: Institutional Standard (requires PERFECT ALIGNMENT)
        # Lane B: Pure Force Rocket (bypasses Bias, requires EXTREME FORCE)
        
        # Lane B: Pure Force Rocket - EXTREME FORCE Thresholds
        # DER > 0.85 (higher than standard) + EXTREME Imbalance (> 3.0 or < 0.33)
        is_extreme_force = (der > 0.70) and (
            (direction_raw == 'LONG' and imbalance > 3.0) or 
            (direction_raw == 'SHORT' and imbalance < 0.33)
        )
        
        # Determine which lane qualifies (if any)
        lane_a_qualified = is_perfect_alignment and (direction_bias != 'NEUTRAL')
        lane_b_qualified = is_extreme_force
        
        # v72.0: Use Lane B if Lane A fails but Lane B has extreme force
        use_lane_b = lane_b_qualified and not lane_a_qualified

        # Update active_lane for context bypass
        if lane_b_qualified: active_lane = 'LANE_B'
        elif lane_a_qualified: active_lane = 'LANE_A'
        
        rocket_requirements_met = (
            m5_dist_pts > 50 and
            (rocket_by_wall or rocket_by_force or is_extreme_force)  # Include Lane B extreme force
        )

        # === v70.0 MOD-84: Dynamic State Guard (Bypass via Perfect Alignment) ===
        # EXHAUSTION: Keep Hard Block (WR 33%)
        if m5_state == 'EXHAUSTION' and not fast_trigger_enabled:
            self.last_reject_reason = f'M5_STATE_EXHAUSTION (High reversal risk)'
            return []

        # RANGING / SIDEWAY / ACCUMULATION: Allow ONLY if Perfect Alignment
        toxic_states = ('SIDEWAY', 'ACCUMULATION', 'CAUTION')
        is_toxic_context = (regime == 'RANGING') or (m5_state in toxic_states)
        
        if is_toxic_context and active_lane == 'NONE' and not fast_trigger_enabled:
            # v70.1: Show detailed alignment mismatch info
            b_status = direction_bias
            f_status = f"{der_direction}{'' if der_aligned else '(!!)'}"
            a_status = f"{imbalance:.2f}{'' if imb_aligned else '(!!)'}"
            self.last_reject_reason = f'NON_PERFECT_ALIGNMENT [Bias:{b_status}, Flow:{f_status}, Agg:{a_status}] ({regime}+{m5_state})'
            return []
        
        # Velocity Tracking: Trigger if price moves > 0.5x ATR within 60 seconds
        velocity_trigger = False
        if self._last_price is not None and self._last_price_time is not None and atr_m5 > 0 and current_time:
            price_move = abs(current_price - self._last_price)
            atr_threshold = atr_m5 * 0.5
            time_diff = current_time - self._last_price_time
            # Check if move > 0.5x ATR within 60 seconds (60000ms)
            if price_move > atr_threshold and time_diff < 60000:
                velocity_trigger = True
        
        # Update velocity tracking
        self._last_price = current_price
        self._last_price_time = current_time
        
        # Determine direction
        # If fast-trigger (Delta Conviction or Velocity), use delta direction
        # Otherwise, use m5_bias direction
        # Note: direction_raw already defined at line 95 for Perfect Alignment check
        
        # v72.0 MOD-91: Lane B (Pure Force Rocket) uses DER direction instead of Bias
        if use_lane_b:
            # Lane B bypasses Bias - uses pure force direction (DER)
            direction = direction_raw
            trigger_mode = 'LANE_B_ROCKET'
            active_lane = 'LANE_B'
        elif fast_trigger_enabled or velocity_trigger:
            # Fast-trigger mode: use delta direction
            direction = direction_raw
            trigger_mode = 'FAST_TRIGGER'
            active_lane = 'FAST_TRIGGER'
        else:
            # Normal mode: use m5_bias direction
            direction = 'LONG' if m5_bias in ('BULLISH', 'STRONG', 'STRONG_EARLY', 'CONFIRMED', 'CONFIRMED+', 'EARLY', 'EARLY_STRUCTURE') else \
                        'SHORT' if m5_bias in ('BEARISH', 'STRONG_BEARISH', 'BEARISH_CONFIRMED') else 'NEUTRAL'
            trigger_mode = 'BIAS_CONFIRMED'
            active_lane = 'LANE_A'

        # === BLOCK (toxic - reject immediately) ===
        blocks = []

        # v65.2 MOD-70: Path A (Sniper) - M5 Dist > 50 pts blocks normal entry
        # Path B (Rocket) bypasses this if rocket_requirements_met
        if m5_dist_pts > 50 and not rocket_requirements_met:
            blocks.append(f'M5_DIST>{int(m5_dist_pts)}pts_noRocket')

        # TOO_EARLY state (early in trend) — MOD-57: Allow if Wall Ratio > 20x or OI Change > 0.1%
        if der_sustainability == 'TOO_EARLY':
            # MOD-57: Smart Entry Relaxation - allow TOO_EARLY if institutional support
            if not (wall_ratio > 20 or oi_change > 0.001):
                blocks.append('TOO_EARLY')

        # m5_near + pers < 2 — MOD-57: Reduce persistence to 1 if OI Change > 0.1%
        m5_overextended = getattr(ctx.snapshot, 'm5_swing_ema_overextended', False)
        pers_threshold = 1 if oi_change > 0.001 else 2  # MOD-57: Relax to 1 if OI rising
        if not m5_overextended and pers < pers_threshold:
            blocks.append(f'm5_near+pers<{pers_threshold}')

        # h1_overextended + delta > 7 + vol > 0.7 (h1 extreme + high delta + low vol = false move)
        # H1 overextended: h1_dist > 85% (price far from EMA on H1)
        # delta > 7: high delta opposite to direction = weakness
        # volume > 0.7: low volume = no institutional support
        h1_extreme = (direction == 'LONG' and h1_dist > 85) or (direction == 'SHORT' and h1_dist < 15)
        if h1_extreme and abs(delta) > 7 and volume_ratio > 0.7:
            blocks.append('h1_overext+delta>7+vol>0.7')
        
        # === v69.0 MOD-81: Force Alignment Guard ===
        # 1. Strict Flow Alignment: Block if Bias (direction) conflicts with DER direction
        # v72.0 MOD-91: Lane B (Pure Force Rocket) bypasses this - uses DER direction directly
        if direction == 'LONG' and der_direction == 'SHORT' and not use_lane_b:
            blocks.append('FORCE_MISMATCH_LONG+DER_SHORT')
        elif direction == 'SHORT' and der_direction == 'LONG' and not use_lane_b:
            blocks.append('FORCE_MISMATCH_SHORT+DER_LONG')
        
        # 2. Imbalance Aggression Requirement - Bypass for Rocket Mode and Lane B
        # v71.0 MOD-89: Tightened to 1.2/0.8 (was 1.1/0.9)
        # v72.0: Lane B (Extreme Force) bypasses imbalance requirement
        if direction == 'LONG' and imbalance <= 1.10 and not (rocket_requirements_met or use_lane_b):  # v74.0: Relaxed
            blocks.append(f'IMBALANCE_LOW_LONG({imbalance:.2f})')
        elif direction == 'SHORT' and imbalance >= 0.90 and not (rocket_requirements_met or use_lane_b):  # v74.0: Relaxed
            blocks.append(f'IMBALANCE_HIGH_SHORT({imbalance:.2f})')
        
        # 3. Fading State Block - Bypass for Rocket Mode and Lane B (high conviction)
        if der_sustainability == 'FADING' and not (rocket_requirements_met or use_lane_b):
            blocks.append('FADING_STATE')

        if blocks:
            self.last_reject_reason = f"BLOCK:{'+'.join(blocks)}"
            return []

        # === REQUIRED (must have at least 1) ===
        conditions = []
        
        # v62.0 MOD-63: RECOVERY boost (WR 92%) - highest confidence
        # RECOVERY alone is sufficient - no other conditions needed
        if m5_state == 'RECOVERY':
            conditions.append('RECOVERY')
        
        # LIKELY (from der_sustainability)
        if der_sustainability == 'LIKELY':
            conditions.append('LIKELY')
        
        # TRENDING + bias_aligned
        if m5_state == 'TRENDING':
            if (direction == 'LONG' and m5_bias in ('BULLISH', 'STRONG', 'STRONG_EARLY', 'CONFIRMED', 'CONFIRMED+', 'EARLY', 'EARLY_STRUCTURE')) or \
               (direction == 'SHORT' and m5_bias in ('BEARISH', 'STRONG_BEARISH', 'BEARISH_CONFIRMED')):
                conditions.append('TRENDING+bias_aligned')

        # h1_dist > 0.5 + delta < 4 (distance from EMA + low delta = range bound)
        if h1_dist > 0.5 and abs(delta) < 4:
            conditions.append('h1_dist>0.5+delta<4')

        # PULLBACK + bias_aligned (pullback = retracement, enter with trend)
        if m5_state == 'PULLBACK':
            if (direction == 'LONG' and m5_bias in ('BULLISH', 'STRONG', 'STRONG_EARLY', 'CONFIRMED', 'CONFIRMED+', 'EARLY', 'EARLY_STRUCTURE')) or \
               (direction == 'SHORT' and m5_bias in ('BEARISH', 'STRONG_BEARISH', 'BEARISH_CONFIRMED')):
                conditions.append('PULLBACK+bias_aligned')

        # MOD-56: Fast-trigger bypass - Delta Conviction allows bypassing required conditions
        # v62.0 MOD-62: Also allow Rocket mode to bypass
        if not conditions:
            # If no required conditions met, but fast-trigger or rocket is active, allow it
            if fast_trigger_enabled or velocity_trigger or rocket_requirements_met:
                conditions.append('FAST_TRIGGER_BYPASS')
                conditions.append('FAST_TRIGGER_BYPASS')
            else:
                self.last_reject_reason = "NO_CONDITION_MET"
                return []

        if direction == 'NEUTRAL':
            self.last_reject_reason = "m5_bias_NEUTRAL"
            return []

        # === Build breakdown for data collection (not decision) ===
        breakdown = {
            # Conditions
            'blocks': ','.join(blocks) if blocks else 'none',
            'conditions': ','.join(conditions),
            # v62.0 MOD-62: Dual-Path (Sniper/Rocket)
            'trigger_mode': trigger_mode,
            'active_lane': active_lane,  # v72.0 MOD-92: Lane identification
            'fast_trigger': 1 if fast_trigger_enabled else 0,
            'velocity_trigger': 1 if velocity_trigger else 0,
            'rocket_mode': 1 if rocket_requirements_met else 0,
            # v72.0 MOD-91: Lane identification
            'lane_a_qualified': 1 if lane_a_qualified else 0,
            'lane_b_qualified': 1 if lane_b_qualified else 0,
            'use_lane_b': 1 if use_lane_b else 0,
            'is_extreme_force': 1 if is_extreme_force else 0,
            'm5_dist_pts': round(m5_dist_pts, 1),
            # Data collection
            'm5_state': m5_state,
            'm5_bias': m5_bias,
            'm5_bias_level': m5_bias_level,
            'm5_ema_position': m5_ema_position,
            'der': round(der, 3),
            'delta': round(delta, 1),
            'h1_dist_pct': round(h1_dist, 2),
            'er': round(er, 2),
            'pers': pers,
            'volume_ratio': round(volume_ratio, 2),
            'imbalance': round(imbalance, 2),
            'imbalance_avg': round(imbalance_avg, 2),
            'oi_change': round(oi_change, 4),
            'atr_ratio': round(atr_ratio, 2),
            'candle_pattern': candle_pattern,
            'h1_bias': h1_bias,
            'der_sustainability': der_sustainability,
            'direction_raw': direction_raw,  # from delta
            'direction_final': direction,   # from m5_bias or delta (fast-trigger)
            # v69.0 MOD-83: Perfect Alignment Recognition (for A+ grade)
            'active_lane': active_lane,
            'der_direction': der_direction,
        }

        # Get wall info
        wall_dom = wall_scan.get('raw_dominant', 'NONE') if wall_scan else 'NONE'
        wall_info = f'{wall_dom} {wall_ratio:.1f}x' if wall_dom != 'NONE' else ''

        # v54.0: Score = 1 (passes threshold)
        score = 1

        result = SignalResult(
            signal_type='MOMENTUM',
            direction=direction,
            entry_price=current_price,
            score=score,
            threshold=self.score_threshold,
            score_breakdown=breakdown,
            regime=regime,
            m5_state=m5_state,
            h1_bias_level=ctx.h1_bias.bias_level,
            h1_dist_pct=h1_dist,
            der=der,
            delta=delta,
            wall_info=wall_info,
            session=session,
            atr_m5=atr_m5,
            atr_ratio=atr_ratio,
        )

        results.append(result)
        return results