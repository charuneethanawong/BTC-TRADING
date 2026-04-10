"""
Mean Revert Detector — v40.0

Extracted from iof_analyzer.py MEAN_REVERT logic.
Detects mean reversion signals based on H1 distance + wall.
"""
from typing import List, Dict, Any, Optional

import numpy as np
import pandas as pd

from src.detectors.base import BaseDetector, SignalResult, DetectionContext
from src.utils.logger import get_logger

logger = get_logger(__name__)


class MeanRevertDetector(BaseDetector):
    signal_type = 'MEAN_REVERT'
    timing = 'CANDLE_CLOSE'  # v43.2: Run on M5 candle close only
    score_threshold = 8

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.h1_dist_min: float = self.config.get('h1_dist_min', 2.0)
        self.wall_ratio_min: float = self.config.get('wall_ratio_min', 5.0)
        self.last_reject_reason = ''  # v40.1: for terminal display
        self.logger = logger

    def detect(self, ctx: DetectionContext) -> List[SignalResult]:
        """Detect MEAN_REVERT signals."""
        results = []

        binance_data = ctx.binance_data or {}
        current_price = ctx.current_price
        atr_m5 = ctx.snapshot.atr_m5 if hasattr(ctx.snapshot, 'atr_m5') else 0.0
        session = ctx.session

        # v50.3: Data Sync — ensure we use Snapshot values (calculated) not raw binance_data
        h1_dist = binance_data.get('h1_ema_dist_pct', 0.0)
        h1_bias = ctx.h1_bias.bias
        regime = ctx.regime.regime
        m5_state = ctx.snapshot.m5_state
        wall_scan = binance_data.get('wall_scan')
        der = ctx.snapshot.der
        delta = ctx.snapshot.delta
        oi_change = ctx.snapshot.oi_change_pct if hasattr(ctx.snapshot, 'oi_change_pct') else 0.0
        
        # v42.1: Advanced Parameters
        snapshot = ctx.snapshot
        er = snapshot.m5_efficiency if hasattr(snapshot, 'm5_efficiency') else 0.5
        pers = snapshot.der_persistence if hasattr(snapshot, 'der_persistence') else 0
        atr_ratio = snapshot.atr_ratio if hasattr(snapshot, 'atr_ratio') else 1.0
        candle_pattern = snapshot.m5_candle_pattern if hasattr(snapshot, 'm5_candle_pattern') else 'NONE'

        # Gate: H1 distance minimum (v39.2: 2.0%)
        if h1_dist < self.h1_dist_min:
            self.last_reject_reason = f'H1 dist {h1_dist:.1f}% < {self.h1_dist_min}%'
            return []

        # Gate: Block in CHOPPY regime (v39.2)
        if regime == 'CHOPPY':
            self.last_reject_reason = 'CHOPPY regime'
            return []

        # Gate: Wall ratio minimum
        if not wall_scan or wall_scan.get('raw_ratio', 0) < self.wall_ratio_min:
            self.last_reject_reason = f'Wall ratio < {self.wall_ratio_min}'
            return []

        wall_ratio = wall_scan['raw_ratio']
        wall_dom = wall_scan.get('raw_dominant', 'NONE')

        # Determine direction: wall protects opposite direction
        # BID wall → price bounce up → LONG
        # ASK wall → price bounce down → SHORT
        direction = 'LONG' if wall_dom == 'BID' else 'SHORT'

        # Check mean revert condition: wall direction opposite to H1 bias
        bullish_biases = ['BULLISH', 'STRONG', 'STRONG_EARLY', 'CONFIRMED', 'CONFIRMED+', 'EARLY', 'EARLY_STRUCTURE']
        bearish_biases = ['BEARISH', 'STRONG_BEARISH', 'BEARISH_CONFIRMED']
        
        is_revert = (
            (direction == 'LONG' and h1_bias in bearish_biases) or
            (direction == 'SHORT' and h1_bias in bullish_biases)
        )

        if not is_revert:
            self.last_reject_reason = 'No mean reversion condition'
            return []

        # Gate: When M5=TRENDING + strong DER, prefer MOMENTUM
        if m5_state == 'TRENDING' and der >= 0.6:
            self.last_reject_reason = 'M5 TRENDING + DER >= 0.6 (use MOMENTUM)'
            return []

        # Calculate score
        score = 0
        breakdown = {'h1_dist': 0, 'wall': 0, 'vol': 0, 'er': 0, 'pers': 0, 'oi': 0, 'cont': 0, 'rej': 0, 'm5_pen': 0}

        # H1 distance score
        if h1_dist >= 2.5:
            score += 5
            breakdown['h1_dist'] = 5
        elif h1_dist >= 2.0:
            score += 3
            breakdown['h1_dist'] = 3

        # Wall score
        if wall_ratio >= 10:
            score += 3
            breakdown['wall'] = 3
        elif wall_ratio >= 5:
            score += 2
            breakdown['wall'] = 2

        # Rejection score
        rejection = candle_pattern in ('HAMMER', 'ENGULFING_BULL') if direction == 'LONG' else candle_pattern in ('SHOOTING_STAR', 'ENGULFING_BEAR')
        if rejection:
            score += 1
            breakdown['rej'] = 1

        # M5 caution penalty
        if m5_state in ('SIDEWAY', 'ACCUMULATION'):
            score -= 1
            breakdown['m5_pen'] = -1

        # v42.1: Mean Revert Bonuses
        if er < 0.2:
            score += 2
            breakdown['er'] = 2
            
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
                scorecard_keys = ['der', 'wall', 'vol', 'er', 'pers', 'oi', 'cont', 'rej'] + ['h1_dist', 'm5_pen']
                scoring_items = [f"{k}:{breakdown[k]}" for k in scorecard_keys if k in breakdown]
                
                # Add critical warning flags
                if breakdown.get('false_bo_likely'): scoring_items.append("false_bo:TRUE!!")
                
                details = ", ".join(scoring_items)
                self.last_reject_reason = f"{reason} ({details})" if details else reason
            else:
                self.last_reject_reason = reason
            return []

        wall_info = f'{wall_dom} {wall_ratio:.1f}x'

        result = SignalResult(
            signal_type='MEAN_REVERT',
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
