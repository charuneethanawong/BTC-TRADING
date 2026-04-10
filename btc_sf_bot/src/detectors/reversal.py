"""
Reversal Detector — v61.0 MOD-59/60

Detects REVERSAL_OB and REVERSAL_OS signals.
- MOD-59: Market Context Guard (No TRENDING, No EXHAUSTION)
- MOD-60: Institutional Flow Guard (DER < 0.15, Wall < 15x, ATR < 0.8 bonus)

REVERSAL_OB: M5 overbought + weak momentum → SHORT (counter-momentum)
REVERSAL_OS: M5 oversold + weak momentum → LONG (counter-momentum)

These are counter-trend signals that catch reversals at market extremes.
"""
from typing import List, Dict, Any, Optional

import numpy as np
import pandas as pd

from src.detectors.base import BaseDetector, SignalResult, DetectionContext
from src.utils.logger import get_logger

logger = get_logger(__name__)


class ReversalOBDetector(BaseDetector):
    """M5 Overbought + Weak Momentum → SHORT"""
    signal_type = 'REVERSAL_OB'
    timing = BaseDetector.TIMING_60S  # v43.2: EVERY_60S (was EVERY_CYCLE)
    score_threshold = 1  # v71.0 MOD-88: Condition-based, no scoring

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.der_max: float = self.config.get('der_max', 0.5)  
        self.last_reject_reason = ''
        self.logger = logger

    def detect(self, ctx: DetectionContext) -> List[SignalResult]:
        """Detect REVERSAL_OB signals."""
        results = []

        binance_data = ctx.binance_data or {}
        snapshot = ctx.snapshot
        current_price = ctx.current_price
        atr_m5 = ctx.snapshot.atr_m5 if hasattr(ctx.snapshot, 'atr_m5') else 0.0
        session = ctx.session

        # v50.3: Data Sync — ensure we use Snapshot values (calculated) not raw binance_data
        der = ctx.snapshot.der
        der_dir = ctx.snapshot.der_direction
        m5_state = ctx.snapshot.m5_state
        delta = ctx.snapshot.delta
        regime = ctx.regime.regime
        
        # === v61.0 MOD-59: Market Context Guard ===
        # REVERSAL: Block in TRENDING (high failure) and EXHAUSTION (36% WR)
        if regime == 'TRENDING':
            self.last_reject_reason = f'REGIME_TRENDING (reversal high risk in runaway trend)'
            return []
        
        # v67.0 MOD-75: M5 State Strict Block - No EXHAUSTION or RECOVERY
        if m5_state == 'EXHAUSTION':
            self.last_reject_reason = f'M5_STATE_EXHAUSTION (reversal WR only 36% here)'
            return []
        
        if m5_state == 'RECOVERY':
            self.last_reject_reason = f'M5_STATE_RECOVERY (reversal WR only 50% — price may continue)'
            return []
        
        # === v67.0 MOD-76: Minimum Wall Gate ===
        # Wall check first (cheap) - must have SOME institutional presence
        wall_scan = ctx.binance_data.get('wall_scan', {}) if ctx.binance_data else {}
        wall_ratio = wall_scan.get('raw_ratio', 0)
        if wall_ratio < 1.4:
            self.last_reject_reason = f'Wall ratio {wall_ratio:.1f}x < 1.5x (no institutional defense — thin wall)'
            return []
        
        # Note: Max Wall Gate (>= 15x) removed per user request

        # === v67.0 MOD-74: Two-Peak DER Logic ===
        # Zone 1 (Safe Exhaustion): DER < 0.12 (weak momentum = safe to reverse)
        # Zone 2 (V-Shape Hunter): DER > 0.40 (strong counter-force = opportunity)
        # Dead Zone Block: 0.12 <= DER <= 0.40 (ambiguous = high loss zone)
        if 0.12 <= der <= 0.40:
            self.last_reject_reason = f'DER {der:.2f} in DEAD ZONE [0.12-0.40] (ambiguous — high loss risk)'
            return []

        # OB condition: DER direction LONG (buying pressure)
        # + momentum weakening (DER < der_max) → reversal SHORT
        # v53.1: DISABLED — m5_bias check (BULLISH gate below) handles direction filtering
        # if der_dir != 'LONG':
        #     self.last_reject_reason = f'DER dir {der_dir} not LONG (no OB)'
        #     return []

        # v50.8: MOD-37 — DER minimum: must have some flow to detect "weakening"
        if der < 0.05:
            self.last_reject_reason = f'DER {der:.3f} too low (no flow to reverse)'
            return []

        # Note: der >= self.der_max check removed - handled by Two-Peak logic above

        # v53.0: OB (overbought → SHORT) only fires when m5_bias BULLISH
        # If m5 not bullish, price hasn't risen enough to be "overbought"
        m5_bias = getattr(ctx.snapshot, 'm5_bias', 'NEUTRAL')
        if m5_bias != 'BULLISH':
            self.last_reject_reason = f'm5_bias {m5_bias} not BULLISH — OB needs bullish trend to reverse'
            return []

        # Confirm: M5 state should be EXHAUSTION or delta declining
        if m5_state not in ('EXHAUSTION', 'CAUTION', 'SIDEWAY'):
            if delta > 0 and der >= 0.3:
                self.last_reject_reason = f'M5 {m5_state} + positive delta, not reversing'
                return []

        # v50.8: MOD-37 — Swing structure: don't SHORT into BULLISH structure
        m5_swing = getattr(ctx.snapshot, 'm5_swing_structure', 'NEUTRAL')
        if m5_swing == 'BULLISH':
            self.last_reject_reason = f'Swing BULLISH contradicts SHORT reversal'
            return []

        # v52.0 MOD-46 Rule 1: h1 bias must support reversal (not NEUTRAL)
        # REVERSAL_OB SHORT works best when h1 BULLISH (overextended → snap back)
        h1_bias = ctx.h1_bias.bias
        if h1_bias == 'NEUTRAL':
            self.last_reject_reason = f'H1 bias NEUTRAL — no trend to reverse'
            return []

        # v52.0 MOD-46 Rule 2: M5 overextended without H1 = fake overextension
        m5_ext = getattr(ctx.snapshot, 'm5_swing_ema_overextended', False)
        h1_ext = getattr(ctx.snapshot, 'h1_swing_ema_overextended', False)
        if m5_ext and not h1_ext:
            self.last_reject_reason = f'M5 overextended but H1 not — fake overextension'
            return []
        
        # === v71.0 MOD-88: Condition-Based Perfect Turn ===
        
        # Condition 1: Wall Ratio max check (Anti-Spoofing)
        wall_scan = ctx.binance_data.get('wall_scan', {}) if ctx.binance_data else {}
        wall_ratio = wall_scan.get('raw_ratio', 0)
        if wall_ratio > 40:
            self.last_reject_reason = f'Wall ratio {wall_ratio:.1f}x > 40x — potential spoofing trap'
            return []
        
        # Condition 2: H1 Distance > 0.5% (Price Tension requirement)
        h1_dist = binance_data.get('h1_ema_dist_pct', 0.0) if binance_data else 0.0
        if abs(h1_dist) < 0.5:
            self.last_reject_reason = f'H1 distance {h1_dist:.2f}% < 0.5% — not enough tension for reversal'
            return []
        
        # === v73.0 MOD-93: Dual-Confirmation System ===
        # Get OI change and efficiency for confirmation checks
        oi_change = snapshot.oi_change_pct if hasattr(snapshot, 'oi_change_pct') else 0.0
        er = snapshot.m5_efficiency if hasattr(snapshot, 'm5_efficiency') else 0.5
        
        # Anti-Falling Knife Block: OI Change < -0.10% AND ER > 0.40 = toxic combo
        # This means people fleeing + price dropping in straight line = don't catch falling knife
        if oi_change < -0.10 and er > 0.40:
            self.last_reject_reason = f'ANTI_FALLING_KNIFE: OI:{oi_change*100:.2f}% + ER:{er:.2f} > 0.40 (people fleeing + straight drop)'
            return []
        
        # Institutional Safe Entry: Must pass EITHER Flow OR OI confirmation
        # Confirm 1: Flow - der_direction must match SHORT (we're selling into buying pressure)
        flow_confirmed = der_dir == 'SHORT'
        # Confirm 2: OI - must have new money entering if flow not confirmed
        oi_confirmed = oi_change > 0.05
        
        if not (flow_confirmed or oi_confirmed):
            self.last_reject_reason = f'INSTITUTIONAL_CONFIRMATION_FAILED: Flow:{flow_confirmed} + OI:{oi_change*100:.2f}% > 0.05% = {oi_confirmed}'
            return []
        
        # === v73.0 MOD-94: M5 EMA Distance Buffer ===
        # Must be at least 50 pts away from M5 EMA20 for profit room
        m5_ema20 = snapshot.m5_ema20 if hasattr(snapshot, 'm5_ema20') and snapshot.m5_ema20 > 0 else current_price
        m5_dist_pts = current_price - m5_ema20  # SHORT = price above EMA = positive distance
        if m5_dist_pts < 50:
            self.last_reject_reason = f'M5 EMA Distance {m5_dist_pts:.0f} pts < 50 pts — not enough room for snap-back'
            return []
        
        # All conditions passed — this is a PERFECT TURN
        # Determine direction: SHORT (reversing from overbought)
        direction = 'SHORT'
        
        # v71.0 MOD-88: Condition-based — record conditions in breakdown (no scoring)
        # Get atr_ratio for breakdown (available in snapshot)
        atr_ratio = snapshot.atr_ratio if hasattr(snapshot, 'atr_ratio') else 1.0
        breakdown = {
            'conditions': [
                f'DER:{der:.3f}',
                f'Wall:{wall_ratio:.1f}x',
                f'H1Dist:{h1_dist:.2f}%',
                f'Bias:{m5_bias}',
                f'State:{m5_state}',
                f'OI:{oi_change*100:+.2f}%',
                f'ER:{er:.2f}'
            ],
            'der': round(der, 3),
            'wall_ratio': round(wall_ratio, 1),
            'h1_dist': round(h1_dist, 2),
            'm5_bias': m5_bias,
            'm5_state': m5_state,
            'atr_ratio': round(atr_ratio, 2),
            'oi_change_pct': round(oi_change * 100, 2),
            'm5_efficiency': round(er, 2),
            'm5_ema20': round(m5_ema20, 2),
            'm5_dist_pts': round(m5_dist_pts, 1),
        }
        
        # Score = 1 (passes threshold) for condition-based mode
        score = 1
        
        # Get H1 context for result
        h1_bias = ctx.h1_bias.bias
        h1_dist = binance_data.get('h1_ema_dist_pct', 0.0)
        regime = ctx.regime.regime
        wall_dominant = wall_scan.get('raw_dominant', 'NONE') if wall_scan else 'NONE'

        result = SignalResult(
            signal_type='REVERSAL_OB',
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
            wall_info=f"{wall_dominant} {wall_ratio:.1f}x" if wall_ratio > 0 else '',
            session=session,
            atr_m5=atr_m5,
            atr_ratio=atr_ratio,
        )

        results.append(result)
        return results


class ReversalOSDetector(BaseDetector):
    """M5 Oversold + Weak Momentum → LONG"""
    signal_type = 'REVERSAL_OS'
    timing = BaseDetector.TIMING_60S  # v43.2: EVERY_60S (was EVERY_CYCLE)
    score_threshold = 1  # v71.0 MOD-88: Condition-based, no scoring

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.der_max: float = self.config.get('der_max', 0.5)
        self.momentum_threshold: float = self.config.get('momentum_threshold', 0.4)
        self.last_reject_reason = ''
        self.logger = logger

    def detect(self, ctx: DetectionContext) -> List[SignalResult]:
        """Detect REVERSAL_OS signals."""
        results = []

        binance_data = ctx.binance_data or {}
        snapshot = ctx.snapshot
        current_price = ctx.current_price
        atr_m5 = ctx.snapshot.atr_m5 if hasattr(ctx.snapshot, 'atr_m5') else 0.0
        session = ctx.session

        # v50.3: Data Sync — ensure we use Snapshot values (calculated) not raw binance_data
        der = ctx.snapshot.der
        der_dir = ctx.snapshot.der_direction
        m5_state = ctx.snapshot.m5_state
        delta = ctx.snapshot.delta
        regime = ctx.regime.regime
        
        # === v61.0 MOD-59: Market Context Guard ===
        # REVERSAL: Block in TRENDING (high failure) and EXHAUSTION (36% WR)
        if regime == 'TRENDING':
            self.last_reject_reason = f'REGIME_TRENDING (reversal high risk in runaway trend)'
            return []
        
        # v67.0 MOD-75: M5 State Strict Block - No EXHAUSTION or RECOVERY
        if m5_state == 'EXHAUSTION':
            self.last_reject_reason = f'M5_STATE_EXHAUSTION (reversal WR only 36% here)'
            return []
        
        if m5_state == 'RECOVERY':
            self.last_reject_reason = f'M5_STATE_RECOVERY (reversal WR only 50% — price may continue)'
            return []
        
        # === v67.0 MOD-76: Minimum Wall Gate ===
        # Wall check first (cheap) - must have SOME institutional presence
        wall_scan = ctx.binance_data.get('wall_scan', {}) if ctx.binance_data else {}
        wall_ratio = wall_scan.get('raw_ratio', 0)
        if wall_ratio < 1.4:
            self.last_reject_reason = f'Wall ratio {wall_ratio:.1f}x < 1.5x (no institutional defense — thin wall)'
            return []
        
        
        
        # === v67.0 MOD-74: Two-Peak DER Logic ===
        # Zone 1 (Safe Exhaustion): DER < 0.12 (weak momentum = safe to reverse)
        # Zone 2 (V-Shape Hunter): DER > 0.40 (strong counter-force = opportunity)
        # Dead Zone Block: 0.12 <= DER <= 0.40 (ambiguous = high loss zone)
        if 0.12 <= der <= 0.40:
            self.last_reject_reason = f'DER {der:.2f} in DEAD ZONE [0.12-0.40] (ambiguous — high loss risk)'
            return []

        # v42.1: Advanced Parameters
        er = snapshot.m5_efficiency if hasattr(snapshot, 'm5_efficiency') else 0.5
        pers = snapshot.der_persistence if hasattr(snapshot, 'der_persistence') else 0
        oi_change = snapshot.oi_change_pct if hasattr(snapshot, 'oi_change_pct') else 0.0
        atr_ratio = snapshot.atr_ratio if hasattr(snapshot, 'atr_ratio') else 1.0
        candle_pattern = snapshot.m5_candle_pattern if hasattr(snapshot, 'm5_candle_pattern') else 'NONE'

        # OS condition: DER direction SHORT (selling pressure)
        # + momentum weakening (DER < der_max) → reversal LONG
        # v53.1: DISABLED — m5_bias check (BEARISH gate below) handles direction filtering
        # if der_dir != 'SHORT':
        #     self.last_reject_reason = f'DER dir {der_dir} not SHORT (no OS)'
        #     return []

        # === v67.0 MOD-74: Two-Peak DER Logic (already checked above, skip redundant checks) ===
        # Note: Dead zone check already done at line 323-325
        # v50.8: MOD-37 — DER minimum: must have some flow to detect "weakening"
        if der < 0.05:
            self.last_reject_reason = f'DER {der:.3f} too low (no flow to reverse)'
            return []

        # Note: der >= self.der_max check removed - handled by Two-Peak logic above

        # v53.0: OS (oversold → LONG) only fires when m5_bias BEARISH
        # If m5 not bearish, price hasn't fallen enough to be "oversold"
        m5_bias = getattr(ctx.snapshot, 'm5_bias', 'NEUTRAL')
        if m5_bias != 'BEARISH':
            self.last_reject_reason = f'm5_bias {m5_bias} not BEARISH — OS needs bearish trend to reverse'
            return []

        # Confirm: M5 state should be EXHAUSTION or delta declining
        if m5_state not in ('EXHAUSTION', 'CAUTION', 'SIDEWAY'):
            if delta < 0 and der >= 0.3:
                self.last_reject_reason = f'M5 {m5_state} + negative delta, not reversing'
                return []

        # v50.8: MOD-37 — Swing structure: don't LONG into BEARISH structure
        m5_swing = getattr(ctx.snapshot, 'm5_swing_structure', 'NEUTRAL')
        if m5_swing == 'BEARISH':
            self.last_reject_reason = f'Swing BEARISH contradicts LONG reversal'
            return []

        # v52.0 MOD-46 Rule 1: h1 bias must support reversal (not NEUTRAL)
        # REVERSAL_OS LONG works best when h1 BEARISH (overextended → snap back)
        h1_bias = ctx.h1_bias.bias
        if h1_bias == 'NEUTRAL':
            self.last_reject_reason = f'H1 bias NEUTRAL — no trend to reverse'
            return []

        # v52.0 MOD-46 Rule 2: M5 overextended without H1 = fake overextension
        m5_ext = getattr(ctx.snapshot, 'm5_swing_ema_overextended', False)
        h1_ext = getattr(ctx.snapshot, 'h1_swing_ema_overextended', False)
        if m5_ext and not h1_ext:
            self.last_reject_reason = f'M5 overextended but H1 not — fake overextension'
            return []
        
        # === v71.0 MOD-88: Condition-Based Perfect Turn ===
        
        # Condition 1: Wall Ratio max check (Anti-Spoofing)
        wall_scan = ctx.binance_data.get('wall_scan', {}) if ctx.binance_data else {}
        wall_ratio = wall_scan.get('raw_ratio', 0)
        if wall_ratio > 40:
            self.last_reject_reason = f'Wall ratio {wall_ratio:.1f}x > 40x — potential spoofing trap'
            return []
        
        # Condition 2: H1 Distance > 0.5% (Price Tension requirement)
        h1_dist = binance_data.get('h1_ema_dist_pct', 0.0) if binance_data else 0.0
        if abs(h1_dist) < 0.5:
            self.last_reject_reason = f'H1 distance {h1_dist:.2f}% < 0.5% — not enough tension for reversal'
            return []
        
        # === v73.0 MOD-93: Dual-Confirmation System ===
        # Get OI change and efficiency for confirmation checks
        oi_change = snapshot.oi_change_pct if hasattr(snapshot, 'oi_change_pct') else 0.0
        er = snapshot.m5_efficiency if hasattr(snapshot, 'm5_efficiency') else 0.5
        
        # Anti-Falling Knife Block: OI Change < -0.10% AND ER > 0.40 = toxic combo
        # This means people fleeing + price dropping in straight line = don't catch falling knife
        if oi_change < -0.10 and er > 0.40:
            self.last_reject_reason = f'ANTI_FALLING_KNIFE: OI:{oi_change*100:.2f}% + ER:{er:.2f} > 0.40 (people fleeing + straight drop)'
            return []
        
        # Institutional Safe Entry: Must pass EITHER Flow OR OI confirmation
        # Confirm 1: Flow - der_direction must match LONG (we're buying into selling pressure)
        flow_confirmed = der_dir == 'LONG'
        # Confirm 2: OI - must have new money entering if flow not confirmed
        oi_confirmed = oi_change > 0.05
        
        if not (flow_confirmed or oi_confirmed):
            self.last_reject_reason = f'INSTITUTIONAL_CONFIRMATION_FAILED: Flow:{flow_confirmed} + OI:{oi_change*100:.2f}% > 0.05% = {oi_confirmed}'
            return []
        
        # === v73.0 MOD-94: M5 EMA Distance Buffer ===
        # Must be at least 50 pts away from M5 EMA20 for profit room
        m5_ema20 = snapshot.m5_ema20 if hasattr(snapshot, 'm5_ema20') and snapshot.m5_ema20 > 0 else current_price
        m5_dist_pts = m5_ema20 - current_price  # LONG = price below EMA = positive distance
        if m5_dist_pts < 50:
            self.last_reject_reason = f'M5 EMA Distance {m5_dist_pts:.0f} pts < 50 pts — not enough room for snap-back'
            return []
        
        # All conditions passed — this is a PERFECT TURN
        # Determine direction: LONG (reversing from oversold)
        direction = 'LONG'
        
        # v71.0 MOD-88: Condition-based — record conditions in breakdown (no scoring)
        # Get atr_ratio for breakdown (defined earlier in line 264)
        atr_ratio = snapshot.atr_ratio if hasattr(snapshot, 'atr_ratio') else 1.0
        breakdown = {
            'conditions': [
                f'DER:{der:.3f}',
                f'Wall:{wall_ratio:.1f}x',
                f'H1Dist:{h1_dist:.2f}%',
                f'Bias:{m5_bias}',
                f'State:{m5_state}',
                f'OI:{oi_change*100:+.2f}%',
                f'ER:{er:.2f}'
            ],
            'der': round(der, 3),
            'wall_ratio': round(wall_ratio, 1),
            'h1_dist': round(h1_dist, 2),
            'm5_bias': m5_bias,
            'm5_state': m5_state,
            'atr_ratio': round(atr_ratio, 2),
            'oi_change_pct': round(oi_change * 100, 2),
            'm5_efficiency': round(er, 2),
            'm5_ema20': round(m5_ema20, 2),
            'm5_dist_pts': round(m5_dist_pts, 1),
        }
        
        # Score = 1 (passes threshold) for condition-based mode
        score = 1
        
        # Get H1 context for result
        h1_bias = ctx.h1_bias.bias
        h1_dist = binance_data.get('h1_ema_dist_pct', 0.0)
        regime = ctx.regime.regime
        wall_dominant = wall_scan.get('raw_dominant', 'NONE') if wall_scan else 'NONE'

        result = SignalResult(
            signal_type='REVERSAL_OS',
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
            wall_info=f"{wall_dominant} {wall_ratio:.1f}x" if wall_ratio > 0 else '',
            session=session,
            atr_m5=atr_m5,
            atr_ratio=atr_ratio,
        )

        results.append(result)
        return results
