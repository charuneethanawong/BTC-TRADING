"""
Absorption Detector — v68.0 MOD-77

Extracted from iof_analyzer.py ABSORPTION logic.
Detects absorption signals based on DER + flat price movement.
"""
from typing import List, Dict, Any, Optional

import numpy as np
import pandas as pd

from src.detectors.base import BaseDetector, SignalResult, DetectionContext
from src.utils.logger import get_logger

logger = get_logger(__name__)


class AbsorptionDetector(BaseDetector):
    signal_type = 'ABSORPTION'
    timing = 'CANDLE_CLOSE'  # v43.2: Run on M5 candle close only
    score_threshold = 10  # v68.0: Lowered for flexibility with ER gate  # v56.0: Increased due to higher Wall Ratio weights

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.der_strong: float = self.config.get('der_strong', 0.6)
        self.price_move_max_atr: float = self.config.get('price_move_max_atr', 0.5)
        self.last_reject_reason = ''  # v40.1: for terminal display
        self.logger = logger

    def detect(self, ctx: DetectionContext) -> List[SignalResult]:
        """Detect ABSORPTION signals."""
        results = []

        binance_data = ctx.binance_data or {}
        current_price = ctx.current_price
        atr_m5 = ctx.snapshot.atr_m5 if hasattr(ctx.snapshot, 'atr_m5') else 0.0
        session = ctx.session

        # Extract data
        # v50.3: Data Sync — ensure we use Snapshot values (calculated) not raw binance_data
        snapshot = ctx.snapshot
        der = snapshot.der
        delta = snapshot.delta
        h1_dist = binance_data.get('h1_ema_dist_pct', 0.0)
        h1_bias = ctx.h1_bias.bias
        wall_scan = binance_data.get('wall_scan')
        regime = ctx.regime.regime
        m5_state = snapshot.m5_state
        m5_bias = snapshot.m5_bias if hasattr(snapshot, 'm5_bias') else 'NEUTRAL'
        
        # v42.1: Advanced Parameters
        er = snapshot.m5_efficiency if hasattr(snapshot, 'm5_efficiency') else 0.5
        pers = snapshot.der_persistence if hasattr(snapshot, 'der_persistence') else 0
        oi_change = snapshot.oi_change_pct if hasattr(snapshot, 'oi_change_pct') else 0.0
        atr_ratio = snapshot.atr_ratio if hasattr(snapshot, 'atr_ratio') else 1.0
        candle_pattern = snapshot.m5_candle_pattern if hasattr(snapshot, 'm5_candle_pattern') else 'NONE'

        # Gate: DER must be strong
        if der < self.der_strong:
            self.last_reject_reason = f'DER {der:.3f} < {self.der_strong}'
            return []

        # Gate: Price move must be flat (< 0.5 ATR)
        price_move_atr = binance_data.get('price_move_atr', 0.0)
        if price_move_atr >= self.price_move_max_atr:
            self.last_reject_reason = f'Price move {price_move_atr:.2f} ATR >= {self.price_move_max_atr}'
            return []

        # Direction: opposite to delta (absorption)
                        # === v68.0 MOD-77: Smart Stalling Filter (Balanced) ===
        # 1. Volatility Guard: Reject in extreme storms
        if atr_ratio > 1.2:
            self.last_reject_reason = f'VOLATILE_STORM: ATR Ratio {atr_ratio:.2f} > 1.2'
            return []

        # 2. Flexible Wall Guard: Standard 30x OR (15x + High Efficiency)
        wall_ratio = wall_scan.get('raw_ratio', 0) if wall_scan else 0
        is_high_efficiency = er > 0.40
        wall_passed = (wall_ratio >= 30.0) or (wall_ratio >= 15.0 and is_high_efficiency)
        
        if not wall_passed:
            self.last_reject_reason = f'WALL_INSUFFICIENT: {wall_ratio:.1f}x (Needs 30x or 15x+ER>0.4)'
            return []

        m5_bias = snapshot.m5_bias if hasattr(snapshot, 'm5_bias') else 'NEUTRAL'
        direction = 'SHORT' if delta > 0 else 'LONG'

        # Trend Alignment Guard: prevent absorbing into a strong trend move
        is_strong_trend = m5_bias in ('STRONG_BULLISH', 'BULLISH_CONFIRMED', 'STRONG_BEARISH', 'BEARISH_CONFIRMED')
        trend_aligned = (direction == 'LONG' and 'BULLISH' in m5_bias) or (direction == 'SHORT' and 'BEARISH' in m5_bias)
        if is_strong_trend and not trend_aligned:
            self.last_reject_reason = f'TREND_CONTRA: Absorbing into strong {m5_bias} trend'
            return []

        # Calculate score
        score = 0
        breakdown = {'der': 0, 'wall': 0, 'vol': 0, 'er': 0, 'pers': 0, 'oi': 0, 'cont': 0, 'rej': 0}

        # DER score
        if der > 0.6:
            score += 5
            breakdown['der'] = 5
        elif der > 0.45:
            score += 4
            breakdown['der'] = 4
        elif der > 0.3:
            score += 3
            breakdown['der'] = 3

                        # Wall score (v68.0: Flexible weighting)
        wall_dom = wall_scan.get('raw_dominant', 'NONE') if wall_scan else 'NONE'
        if wall_ratio >= 50:
            score += 6
            breakdown['wall'] = 6
        elif wall_ratio >= 30:
            score += 4
            breakdown['wall'] = 4
        elif wall_ratio >= 15:
            score += 2
            breakdown['wall'] = 2
        else:
            score += 0 

        # Volume score
        volume_ratio = snapshot.volume_ratio_m5 if hasattr(snapshot, 'volume_ratio_m5') else 1.0
        if volume_ratio >= 2.0:
            score += 2
            breakdown['vol'] = 2
        elif volume_ratio >= 1.2:
            score += 1
            breakdown['vol'] = 1

        # Rejection score
        rejection = candle_pattern in ('HAMMER', 'ENGULFING_BULL') if direction == 'LONG' else candle_pattern in ('SHOOTING_STAR', 'ENGULFING_BEAR')
        if rejection:
            score += 1
            breakdown['rej'] = 1

        # v42.1: Bonus Scoring
        if er < 0.2:
            score += 2
            breakdown['er'] = 2
        
        if pers == 1:
            score += 1
            breakdown['pers'] = 1
            
        if (direction == 'LONG' and oi_change < -0.1) or (direction == 'SHORT' and oi_change < -0.1):
            score += 1
            breakdown['oi'] = 1
            
        if atr_ratio < 0.7:
            score += 2
            breakdown['cont'] = 2

        
        # v42.2: Forensic Imbalance Data
        breakdown['imb_current'] = round(snapshot.imbalance if hasattr(snapshot, 'imbalance') else 1.0, 2)
        breakdown['imb_avg_5m'] = round(snapshot.imbalance_avg_5m if hasattr(snapshot, 'imbalance_avg_5m') else 1.0, 2)
        breakdown['imb_direction'] = snapshot.imbalance_direction if hasattr(snapshot, 'imbalance_direction') else 'NEUTRAL'
        
        
        # v43.1: M5 Bias Forensic Data
        breakdown['m5_bias'] = snapshot.m5_bias if hasattr(snapshot, 'm5_bias') else 'NEUTRAL'
        breakdown['m5_bias_level'] = snapshot.m5_bias_level if hasattr(snapshot, 'm5_bias_level') else 'NEUTRAL'
        breakdown['m5_ema9'] = round(snapshot.m5_ema9, 2) if hasattr(snapshot, 'm5_ema9') else 0.0
        breakdown['m5_ema20'] = round(snapshot.m5_ema20, 2) if hasattr(snapshot, 'm5_ema20') else 0.0
        if score < self.score_threshold:
            reason = f"Score {score} < {self.score_threshold}"
            # v43.9: Unified Full Scorecard Log
            if breakdown:
                scorecard_keys = ['der', 'wall', 'vol', 'er', 'pers', 'oi', 'cont', 'rej'] + []
                scoring_items = [f"{k}:{breakdown[k]}" for k in scorecard_keys if k in breakdown]
                
                # Add critical warning flags
                if breakdown.get('false_bo_likely'): scoring_items.append("false_bo:TRUE!!")
                
                details = ", ".join(scoring_items)
                self.last_reject_reason = f"{reason} ({details})" if details else reason
            else:
                self.last_reject_reason = reason
            return []

        wall_info = f'{wall_dom} {wall_ratio:.1f}x' if wall_dom != 'NONE' else ''

        result = SignalResult(
            signal_type='ABSORPTION',
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
