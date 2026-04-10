"""
Signal Detector Base — v43.7

Unified interface for all signal detectors.
Every detector inherits BaseDetector and returns SignalResult.
Includes shared breakdown helper and false breakout detection.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Tuple

import pandas as pd


@dataclass
class SignalResult:
    """
    Unified output from every detector.
    All signal types use this same interface.
    """
    signal_type: str          # 'MOMENTUM', 'MEAN_REVERT', 'ABSORPTION', 'IPA', 'VP_BOUNCE', etc.
    direction: str            # 'LONG' | 'SHORT'
    entry_price: float
    score: int
    threshold: int = 9        # v40.1: for terminal display (Score: 11/9)
    score_breakdown: Dict[str, Any] = None

    # Market context at entry (from Layer 1 engines)
    regime: str = 'RANGING'
    m5_state: str = 'RANGING'
    m5_ema_position: str = 'NEUTRAL'
    h1_bias_level: str = 'NEUTRAL'
    h1_dist_pct: float = 0.0
    der: float = 0.0
    der_direction: str = 'NEUTRAL'
    delta: float = 0.0
    wall_info: str = ''
    session: str = 'ASIA'

    # Entry-specific
    entry_zone_min: float = 0.0
    entry_zone_max: float = 0.0
    atr_m5: float = 0.0
    atr_ratio: float = 1.0

    # Optional per-type fields (stored in breakdown JSON)
    extra: Dict[str, Any] = field(default_factory=dict)


class DetectionContext:
    """
    Shared context from Layer 1 — every detector receives the same data.
    """
    def __init__(
        self,
        candles_m5: pd.DataFrame,
        candles_h1: pd.DataFrame,
        current_price: float,
        snapshot: Any,           # MarketSnapshot
        regime: Any,             # RegimeResult
        h1_bias: Any,            # H1BiasResult
        session: str,
        magnets: Optional[Dict] = None,
        frvp_data: Optional[Dict] = None,
        new_candle: bool = False,
        binance_data: Optional[Dict] = None,
    ):
        self.candles_m5 = candles_m5
        self.candles_h1 = candles_h1
        self.current_price = current_price
        self.snapshot = snapshot
        self.regime = regime
        self.h1_bias = h1_bias
        self.session = session
        self.magnets = magnets or {}
        self.frvp_data = frvp_data or {}
        self.new_candle = new_candle
        self.binance_data = binance_data or {}


class BaseDetector(ABC):
    """
    Every detector inherits from this.
    """
    # v43.2: Timing constants
    TIMING_60S = 'EVERY_60S'       # Run every 60 seconds (MOMENTUM, REVERSAL)
    TIMING_CANDLE = 'CANDLE_CLOSE'  # Run on M5 candle close only (ABSORPTION, MEAN_REVERT, IPA types, VP types)
    
    signal_type: str                    # e.g. 'MOMENTUM'
    timing: str                         # v43.2: 'EVERY_60S' | 'CANDLE_CLOSE' (was 'EVERY_CYCLE')
    score_threshold: int                # minimum score to emit signal
    last_reject_reason: str = ''        # v40.1: reason for terminal display
    _last_detect_time: float = 0        # v43.2: for interval tracking

    @abstractmethod
    def detect(self, ctx: DetectionContext) -> List[SignalResult]:
        """Return 0+ signals"""
        pass

    def _init_breakdown(self, ctx: DetectionContext) -> Dict[str, Any]:
        """v43.7: Shared breakdown — every signal type stores same data: VP + Bias + Flow"""
        snapshot = ctx.snapshot
        bd = ctx.binance_data or {}
        frvp = ctx.frvp_data or {}
        comp = frvp.get('composite', {})
        swing = frvp.get('layers', {}).get('swing_anchored', {})

        return {
            # 8-column scorecard
            'der': 0, 'wall': 0, 'vol': 0, 'er': 0,
            'pers': 0, 'oi': 0, 'cont': 0, 'rej': 0,

            # VP Data
            'vp_poc': round(comp.get('poc', 0) or 0, 2),
            'vp_vah': round(comp.get('vah', 0) or 0, 2),
            'vp_val': round(comp.get('val', 0) or 0, 2),
            'vp_price_vs_va': getattr(snapshot, 'vp_price_vs_va', 'INSIDE'),
            'vp_poc_dist_atr': round(getattr(snapshot, 'vp_poc_distance_atr', 0), 2),
            
            
            'vp_trigger': getattr(snapshot, 'vp_trigger_anchor', 'none'),
            'vp_hvn_count': len(swing.get('hvn', [])),
            'vp_lvn_count': len(swing.get('lvn', [])),
            'vp_nearest_hvn': round(getattr(snapshot, 'vp_nearest_hvn', 0), 2),
            'vp_nearest_lvn': round(getattr(snapshot, 'vp_nearest_lvn', 0), 2),
            # v43.8: Swing Anchor Info
            'vp_anchor_type': swing.get('anchor_type', 'unknown'),
            'vp_anchor_price': round(swing.get('anchor_price', 0) or 0, 2),
            'vp_anchor_move': round(swing.get('anchor_move', 0) or 0, 2),
            'vp_anchor_age_candles': swing.get('anchor_age_candles', 0),

            # Bias Data (v50.4: Corrected to use authoritative sources)
            'h1_bias': ctx.h1_bias.bias,
            'h1_bias_level': ctx.h1_bias.bias_level,
            'm5_bias': snapshot.m5_bias,
            'm5_bias_level': snapshot.m5_bias_level,
            'm5_state': snapshot.m5_state,

            # Flow Data
            'der_value': round(snapshot.der, 3),
            'der_dir': snapshot.der_direction,
            'delta': round(snapshot.delta, 1),
            'imb_current': round(getattr(snapshot, 'imbalance', 1.0), 2),
            'imb_avg_5m': round(getattr(snapshot, 'imbalance_avg_5m', 1.0), 2),
        }

        def _is_level_accepted(self, candles: pd.DataFrame, level_price: float, atr: float, lookback: int = 4) -> bool:
            # v58.0 MOD-54: Check if price has been range-bound/accepted at a level.
            if candles is None or len(candles) < lookback:
                return False
            
            # Use standard threshold for acceptance
            threshold = atr * 0.3
            recent = candles.iloc[-lookback:]
            
            # Check if all candles in lookback closed within the threshold of the level
            all_near = all(abs(float(c['close']) - level_price) < threshold for _, c in recent.iterrows())
            return all_near

    def _calc_false_breakout_score(self, ctx: DetectionContext, direction: str) -> Tuple[int, Dict]:
        """v43.7: Calculate false breakout probability — store data every time"""
        score = 0  # high = likely false breakout
        indicators = {}

        frvp = ctx.frvp_data or {}
        comp = frvp.get('composite', {})
        bd = ctx.binance_data or {}
        snapshot = ctx.snapshot

        # 1. Volume ตอน break ต่ำ
        vol_ratio = getattr(snapshot, 'volume_ratio_m5', 1.0)
        if vol_ratio < 1.2:
            score += 2
            indicators['low_volume'] = round(vol_ratio, 2)

        # 2. POC ไม่ shift
        poc_shift = getattr(snapshot, 'vp_poc_shift', 0)
        atr_m5 = getattr(snapshot, 'atr_m5', 100)
        if abs(poc_shift) < atr_m5 * 0.1:
            score += 2
            indicators['no_poc_shift'] = round(poc_shift, 2)

        # 3. OI ลด (liquidation)
        oi_change = getattr(snapshot, 'oi_change_pct', 0)
        if oi_change < -0.05:
            score += 1
            indicators['oi_declining'] = round(oi_change, 4)

        # 4. DER ไม่ sustained
        der_pers = getattr(snapshot, 'der_persistence', 0)
        if der_pers < 2:
            score += 1
            indicators['der_not_sustained'] = der_pers

        # 5. Delta spike แล้วหาย
        der = getattr(snapshot, 'der', 0)
        if der > 0.8 and der_pers == 1:
            score += 1
            indicators['delta_spike'] = round(der, 3)

        return score, indicators  # score >= 3 = likely false breakout
