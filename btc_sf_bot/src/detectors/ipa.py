"""
IPA Detector — v60.0 MOD-58

Unified IPA detector: merges IPA_OB + IPA_FVG logic into single detector.
- MOD-58: Ranging State Block - reject if m5_state == 'RANGING'
- Finds best zone: OB or FVG (whichever is closer to price)
- If both found and overlapping → OB+FVG (strongest)
- If both separate → use closest to current_price
"""
from typing import List, Dict, Any, Optional

import pandas as pd
import numpy as np

from src.detectors.base import BaseDetector, SignalResult, DetectionContext
from src.detectors.ipa_shared import IPAShared
from src.utils.logger import get_logger

logger = get_logger(__name__)


class IPADetector(BaseDetector):
    signal_type = 'IPA'
    timing = 'CANDLE_CLOSE'
    score_threshold = 8  # v54.0: reduced from 10 (WR 100% at all score levels)

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.shared = IPAShared(config, logger=logger, log_prefix="[IPA]")
        self.ob_body_min_pct = self.config.get('ob_body_min_pct', 0.0005)
        self.ob_max_distance_atr = self.config.get('ob_max_distance_atr', 1.0)
        self.last_reject_reason = ''
        self.logger = logger

    def detect(self, ctx: DetectionContext) -> List[SignalResult]:
        # MOD-58: Hard Gate - Reject if M5 State is RANGING
        m5_state = ctx.snapshot.m5_state if hasattr(ctx.snapshot, 'm5_state') else 'UNKNOWN'
        if m5_state == 'RANGING':
            self.last_reject_reason = f'M5_STATE_RANGING (WR low in choppy market)'
            return []

        if not ctx.new_candle:
            self.last_reject_reason = 'Not new candle'
            return []

        results = []
        
        # Shared pre-checks (unchanged from ipa_ob/ipa_fvg)
        h1_result = self.shared.check_h1_bias(ctx.candles_h1, ctx.candles_m5, ctx.current_price)
        if not h1_result:
            self.last_reject_reason = 'H1 bias not confirmed'
            return []

        direction = h1_result['direction']
        
        # v65.2 MOD-69: Dynamic Bias Filter - Tension-Adaptive Sniper
        # Early Trend: Allow EARLY, STRONG_EARLY, EARLY_STRUCTURE
        # Late Trend Bypass: Allow CONFIRMED, CONFIRMED+ ONLY if H1 Dist > 0.5% (tension high = big runner)
        h1_bias_level = h1_result.get('bias_level', 'NONE')
        h1_dist_pct = ctx.binance_data.get('h1_ema_dist_pct', 0.0) if ctx.binance_data else 0.0
        
        early_bias_levels = ('EARLY', 'STRONG_EARLY', 'EARLY_STRUCTURE')
        late_bias_levels = ('CONFIRMED', 'CONFIRMED+', 'STRONG', 'MODERATE')
        
        if h1_bias_level in late_bias_levels and abs(h1_dist_pct) <= 0.5:
            # Late trend but NOT stretched - too risky (market may reverse)
            self.last_reject_reason = f'H1_BIAS_{h1_bias_level}_LOW_TENSION (Dist {abs(h1_dist_pct):.2f}% <= 0.5%)'
            return []
        
        m5_result = self.shared.check_m5_structure(ctx.candles_m5, direction)
        if not m5_result:
            self.last_reject_reason = 'No M5 structure break'
            return []

        break_idx = m5_result['break_idx']
        eqs = self.shared.check_eqs(ctx.candles_m5, direction, break_idx)
        
        # v65.0 MOD-69: EQS Quality Gate - require total > 0 for impulsive break
        eqs_total = eqs.total if hasattr(eqs, 'total') else 0
        if eqs_total <= 0:
            self.last_reject_reason = f'EQS_TOTAL {eqs_total} <= 0 (no impulsive break — no institutional zone)'
            return []
        
        # v65.2 MOD-69: Minimum Force Gate - DER > 0.20 required
        der = ctx.snapshot.der if hasattr(ctx.snapshot, 'der') else 0.0
        if der <= 0.20:
            self.last_reject_reason = f'DER {der:.2f} <= 0.20 (no institutional force — low win probability)'
            return []
        
        # Get parameters
        snapshot = ctx.snapshot
        binance_data = ctx.binance_data or {}
        er = snapshot.m5_efficiency if hasattr(snapshot, 'm5_efficiency') else 0.5
        pers = snapshot.der_persistence if hasattr(snapshot, 'der_persistence') else 0
        oi_change = snapshot.oi_change_pct if hasattr(snapshot, 'oi_change_pct') else 0.0
        atr_ratio = snapshot.atr_ratio if hasattr(snapshot, 'atr_ratio') else 1.0
        candle_pattern = snapshot.m5_candle_pattern if hasattr(snapshot, 'm5_candle_pattern') else 'NONE'
        volume_ratio = snapshot.volume_ratio_m5 if hasattr(snapshot, 'volume_ratio_m5') else 1.0
        atr_m5 = ctx.snapshot.atr_m5 if hasattr(ctx.snapshot, 'atr_m5') else 0.0

        # === Find Zones ===
        ob_zone = self._find_ob_zone(ctx.candles_m5, direction, break_idx)
        fvg_zone = self._find_fvg(ctx.candles_m5, direction, break_idx)

        # === Select Best Zone ===
        zone_type, best_zone = self._select_best_zone(ob_zone, fvg_zone, ctx.current_price, atr_m5)
        
        if not best_zone:
            self.last_reject_reason = f'No OB or FVG found (zone_type: none)'
            return []
        
        # v65.0 MOD-69: Zone Touch Confirmation (Wick Response)
        # Check if price had wick rejection at zone (>0.2 wick ratio = confirmed institutional defense)
        zone_price = (best_zone['high'] + best_zone['low']) / 2
        candles_m5 = ctx.candles_m5
        wick_confirmed = False
        
        if candles_m5 is not None and len(candles_m5) >= 3:
            last_candle = candles_m5.iloc[-1]
            prev_candle = candles_m5.iloc[-2]
            current_price = ctx.current_price
            
            # Check last 2 candles for wick rejection at zone
            for candle in [last_candle, prev_candle]:
                c_l = float(candle['low'])
                c_h = float(candle['high'])
                c_o = float(candle['open'])
                c_c = float(candle['close'])
                rng = c_h - c_l
                
                if rng > 0:
                    # LONG entry: check lower wick rejection at zone
                    if direction == 'LONG' and c_l <= zone_price:
                        lower_wick = min(c_o, c_c) - c_l
                        wick_ratio = lower_wick / rng
                        if wick_ratio > 0.2:
                            wick_confirmed = True
                            break
                    # SHORT entry: check upper wick rejection at zone
                    elif direction == 'SHORT' and c_h >= zone_price:
                        upper_wick = c_h - max(c_o, c_c)
                        wick_ratio = upper_wick / rng
                        if wick_ratio > 0.2:
                            wick_confirmed = True
                            break
        
        if not wick_confirmed:
            self.last_reject_reason = f'No Wick Response at zone (wick_ratio <= 0.2 — price may steamroll zone)'
            return []

        # === Build Breakdown ===
        breakdown = self._build_breakdown(zone_type, ob_zone, fvg_zone, eqs, ctx, h1_result)

        # === Score Calculation ===
        score = self._calc_score(breakdown)
        
        if score < self.score_threshold:
            reason = f"Score {score} < {self.score_threshold}"
            if breakdown:
                scorecard_keys = ['der', 'wall', 'vol', 'er', 'pers', 'oi', 'cont', 'rej'] + ['h1_structure', 'm5_entry', 'liquidity', 'eqs']
                scoring_items = [f"{k}:{breakdown[k]}" for k in scorecard_keys if k in breakdown]
                details = ", ".join(scoring_items)
                self.last_reject_reason = f"{reason} ({details})" if details else reason
            else:
                self.last_reject_reason = reason
            return []

        # === Return Single Signal ===
        return [SignalResult(
            signal_type='IPA',
            direction=direction,
            entry_price=(best_zone['high'] + best_zone['low']) / 2,
            score=score,
            threshold=self.score_threshold,
            score_breakdown=breakdown,
            regime=ctx.regime.regime,
            m5_state=ctx.snapshot.m5_state,
            h1_bias_level=h1_result.get('bias_level', 'NEUTRAL'),
            h1_dist_pct=ctx.binance_data.get('h1_ema_dist_pct', 0.0),
            der=ctx.snapshot.der,
            delta=ctx.snapshot.delta,
            session=ctx.session,
            entry_zone_min=best_zone['low'],
            entry_zone_max=best_zone['high'],
            atr_m5=atr_m5,
            atr_ratio=atr_ratio,
        )]

    def _select_best_zone(self, ob_zone: Optional[Dict], fvg_zone: Optional[Dict], 
                          current_price: float, atr_m5: float) -> tuple:
        """Select best zone: OB+FVG overlap, or closest to price."""
        if not ob_zone and not fvg_zone:
            return 'NONE', None
        
        # Both found - check overlap
        if ob_zone and fvg_zone:
            # Check if overlapping (within 0.5 ATR)
            overlap_threshold = atr_m5 * 0.5
            ob_mid = (ob_zone['high'] + ob_zone['low']) / 2
            fvg_mid = (fvg_zone['high'] + fvg_zone['low']) / 2
            
            if abs(ob_mid - fvg_mid) <= overlap_threshold:
                # Merge into strongest zone
                return 'OB+FVG', {
                    'low': min(ob_zone['low'], fvg_zone['low']),
                    'high': max(ob_zone['high'], fvg_zone['high']),
                }
            else:
                # Use closest to price
                ob_dist = abs(current_price - ob_mid)
                fvg_dist = abs(current_price - fvg_mid)
                if ob_dist <= fvg_dist:
                    return 'OB', ob_zone
                else:
                    return 'FVG', fvg_zone
        
        # Only OB
        if ob_zone:
            return 'OB', ob_zone
        
        # Only FVG
        return 'FVG', fvg_zone

    def _find_ob_zone(self, candles_m5: pd.DataFrame, direction: str, break_idx: int) -> Optional[Dict]:
        """Find Order Block (from ipa_ob.py logic)."""
        lookback = min(30, break_idx)
        before_break = candles_m5.iloc[max(0, break_idx - lookback):break_idx + 1]
        
        if len(before_break) < 2:
            return None
        
        for i in range(len(before_break) - 2, 0, -1):
            c1 = before_break.iloc[i - 1]
            c2 = before_break.iloc[i]
            
            if direction == 'LONG':
                # Bearish candle followed by bullish candle = OB
                if c1['close'] < c1['open'] and c2['close'] > c2['open']:
                    body_pct = abs(c2['close'] - c2['open']) / c2['open']
                    if body_pct >= self.ob_body_min_pct:
                        return {
                            'low': min(c1['low'], c2['low']),
                            'high': max(c1['high'], c2['high']),
                            'c1': c1, 'c2': c2
                        }
            else:  # SHORT
                # Bullish candle followed by bearish candle = OB
                if c1['close'] > c1['open'] and c2['close'] < c2['open']:
                    body_pct = abs(c2['close'] - c2['open']) / c2['open']
                    if body_pct >= self.ob_body_min_pct:
                        return {
                            'low': min(c1['low'], c2['low']),
                            'high': max(c1['high'], c2['high']),
                            'c1': c1, 'c2': c2
                        }
        return None

    def _find_fvg(self, candles_m5: pd.DataFrame, direction: str, break_idx: int) -> Optional[Dict]:
        """Find Fair Value Gap (from ipa_fvg.py logic)."""
        lookback = min(30, break_idx)
        before_break = candles_m5.iloc[max(0, break_idx - lookback):break_idx + 1]

        if len(before_break) < 3:
            return None

        for i in range(1, len(before_break) - 1):
            c1 = before_break.iloc[i - 1]
            c2 = before_break.iloc[i]
            c3 = before_break.iloc[i + 1]

            if direction == 'LONG':
                # FVG: gap up (c1 low > c3 high)
                if c1['low'] > c3['high']:
                    return {'low': c3['high'], 'high': c1['low']}
            else:  # SHORT
                # FVG: gap down (c1 high < c3 low)
                if c1['high'] < c3['low']:
                    return {'low': c1['high'], 'high': c3['low']}
        return None

    def _build_breakdown(self, zone_type: str, ob_zone: Optional[Dict], fvg_zone: Optional[Dict],
                        eqs: Any, ctx: DetectionContext, h1_result: Dict) -> Dict:
        """Build unified breakdown combining OB and FVG scores."""
        snapshot = ctx.snapshot
        binance_data = ctx.binance_data or {}
        
        breakdown = {
            'zone_type': zone_type,  # OB | FVG | OB+FVG
            'der': 0, 'wall': 0, 'vol': 0, 'er': 0, 'pers': 0, 'oi': 0, 
            'cont': 0, 'rej': 0, 'h1_structure': 0, 'm5_entry': 0, 
            'liquidity': 0, 'eqs': 0
        }
        
        # DER scoring (0-2)
        der = snapshot.der if hasattr(snapshot, 'der') else 0
        if abs(der) >= 0.3:
            breakdown['der'] = 2
        elif abs(der) >= 0.15:
            breakdown['der'] = 1
        
        # Wall scoring (0-2)
        wall_scan = binance_data.get('wall_scan', {})
        wall_ratio = wall_scan.get('raw_ratio', 0)
        if wall_ratio >= 2.5:
            breakdown['wall'] = 2
        elif wall_ratio >= 1.9:
            breakdown['wall'] = 1
        
        # Volume (0-1)
        vol_ratio = snapshot.volume_ratio_m5 if hasattr(snapshot, 'volume_ratio_m5') else 1.0
        if vol_ratio >= 1.2:
            breakdown['vol'] = 1
        
        # ER (0-1)
        er = snapshot.m5_efficiency if hasattr(snapshot, 'm5_efficiency') else 0.5
        if er >= 0.35:
            breakdown['er'] = 1
        
        # Persistence (0-1)
        pers = snapshot.der_persistence if hasattr(snapshot, 'der_persistence') else 0
        if pers >= 2:
            breakdown['pers'] = 1
        
        # H1 Structure (0-2)
        h1_bias = h1_result.get('bias_level', 'NEUTRAL')
        if h1_bias in ('STRONG', 'CONFIRMED+'):
            breakdown['h1_structure'] = 2
        elif h1_bias in ('CONFIRMED', 'MODERATE'):
            breakdown['h1_structure'] = 1
        
        # M5 Entry (0-1) - from m5_result
        # (simplified - could add more detail)
        
        # Liquidity (0-1) - check for sweeps
        # (simplified)
        
        # EQS (0-1)
        if eqs and hasattr(eqs, 'total'):
            breakdown['eqs'] = 1 if eqs.total >= -2 else 0
        
        # Contraction (0-1)
        # (simplified)
        
        # Rejection (0-1)
        # (simplified)
        
        # === Zone-specific scoring ===
        if zone_type == 'OB' and ob_zone:
            breakdown['ob_body'] = 1  # OB found
            breakdown['fvg_size'] = 0
        elif zone_type == 'FVG' and fvg_zone:
            breakdown['ob_body'] = 0
            breakdown['fvg_size'] = 1  # FVG found
        elif zone_type == 'OB+FVG':
            breakdown['ob_body'] = 1
            breakdown['fvg_size'] = 1  # Both = strongest
        
        # Add EQS details
        if eqs:
            breakdown['eqs_total'] = eqs.total if hasattr(eqs, 'total') else 0
            breakdown['eqs_retrace'] = eqs.retrace if hasattr(eqs, 'retrace') else 0
        
        # Add forensic data
        breakdown['imb_current'] = round(snapshot.imbalance if hasattr(snapshot, 'imbalance') else 1.0, 2)
        breakdown['imb_avg_5m'] = round(snapshot.imbalance_avg_5m if hasattr(snapshot, 'imbalance_avg_5m') else 1.0, 2)
        breakdown['m5_bias'] = snapshot.m5_bias if hasattr(snapshot, 'm5_bias') else 'NEUTRAL'
        breakdown['m5_bias_level'] = snapshot.m5_bias_level if hasattr(snapshot, 'm5_bias_level') else 'NEUTRAL'
        
        return breakdown

    def _calc_score(self, breakdown: Dict) -> int:
        """Calculate unified score from breakdown."""
        score = 0
        
        # Core scores
        score += breakdown.get('der', 0)
        score += breakdown.get('wall', 0)
        score += breakdown.get('vol', 0)
        score += breakdown.get('er', 0)
        score += breakdown.get('pers', 0)
        score += breakdown.get('oi', 0)
        score += breakdown.get('cont', 0)
        score += breakdown.get('rej', 0)
        score += breakdown.get('h1_structure', 0)
        score += breakdown.get('m5_entry', 0)
        score += breakdown.get('liquidity', 0)
        score += breakdown.get('eqs', 0)
        
        # Zone bonus (OB or FVG found)
        score += breakdown.get('ob_body', 0)
        score += breakdown.get('fvg_size', 0)
        
        return score
