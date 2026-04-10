"""
Signal Builder — v4.9 M5 Upgrade

Builds standardized Signal JSON contracts for Python → EA communication.

Signal JSON Contract:
{
  "signal_id": "IPA_LONG_165700",
  "mode": "IPA",           # "IPA" or "IOF"
  "direction": "LONG",     # "LONG" or "SHORT"
  "entry_price": 95000.00,
  "stop_loss": 94525.00,
  "take_profit": 95950.00,
  "score": 16,
  "required_rr": 1.90,
  "sl_reason": "OB_BOUNDARY",
  "tp_reason": "SWING_HIGH_LIQ",
  "institutional_grade": true,
  "session": "LONDON",
  "timestamp": "2026-03-19 16:57:00"
}

Key Rules:
  - No lot_size — EA calculates from RiskPercent via CalculateLotSize()
  - No lot_size — EA calculates position size
  - mode field is MANDATORY — EA routes to IPA or IOF trailing logic
  - required_rr = actual_rr × 0.95 (5% tolerance for spread/slippage)
"""
from dataclasses import dataclass
from typing import Optional, Dict, Any
from datetime import datetime, timezone

from src.utils.logger import get_logger
from src.utils.decorators import log_errors, retry, circuit_breaker
from src.utils.metrics import timed_metric

logger = get_logger(__name__)


@dataclass
class IPAResult:
    """
    Result from IPA Analyzer.
    Passed to signal_builder.build_ipa_signal().
    """
    direction: str
    score: int
    ob_high: Optional[float]
    ob_low: Optional[float]
    entry_zone_min: float
    entry_zone_max: float
    h1_bias: str
    sweep_confirmed: bool
    fvg_overlap: bool
    volume_spike: bool
    atr_m5: float
    swing_highs: list
    swing_lows: list
    pdh: Optional[float] = None
    pdl: Optional[float] = None
    h1_fvg_boundary: Optional[float] = None
    m5_efficiency: float = 0.5
    wall_scan: dict = None


@dataclass
class IOFResult:
    """
    Result from IOF Analyzer.
    Passed to signal_builder.build_iof_signal().
    """
    direction: str
    score: int
    wall_price: float
    wall_size_usd: float
    der_score: float
    oi_change_pct: float
    rr_target: float
    volume_spike: bool
    rejection_candle: bool
    atr_m5: float
    next_resistance: Optional[float] = None
    next_support: Optional[float] = None
    m5_efficiency: float = 0.5
    wall_scan: dict = None


class SignalBuilder:
    """
    Builds standardized Signal JSON contracts.

    Usage:
        builder = SignalBuilder()
        signal = builder.build(
            mode='IPA',
            ipa_result=ipa_result,
            sl_tp=sl_tp_result,
            session='LONDON',
            entry_price=95000.0
        )
    """

    # v15.4: Mode name mapping for short_reason
    MODE_SHORT = {
        'IPA': 'IPA',
        'IPA_FRVP': 'IPA',
        'IOF': 'IOF',
        'IOF_FRVP': 'IOFF',
    }

    def __init__(self, config: dict = None):
        self.config = config or {}
        self._signal_counter = 0
        self._last_signal_time = None

    def build(self,
            mode: str,
            direction: str,
            entry_price: float,
            sl_tp: 'SLTPRESult',
            session: str,
            score: int,
            regime: str = 'RANGING',
            institutional_grade: bool = True,
            extra_data: Optional[Dict[str, Any]] = None,
            short_reason: str = '') -> Dict[str, Any]:
        """
        Build a complete Signal JSON contract.

        Args:
            mode: 'IPA' or 'IOF'
            direction: 'LONG' or 'SHORT'
            entry_price: Entry price
            sl_tp: SLTPRESult from SL/TP calculator
            session: Trading session
            score: Signal score (0-20)
            regime: Market regime (TRENDING/RANGING/VOLATILE/DEAD)
            institutional_grade: Whether this is an institutional-grade signal
            extra_data: Additional data to include
            short_reason: Pattern type for EA's BE unlock / same-pattern guard (e.g. 'MOMENTUM', 'REVERSAL_OB')

        Returns:
            Signal dictionary ready for EA
        """
        self._signal_counter += 1

        # Short direction
        dir_short = 'LONG' if direction == 'LONG' else 'SHORT'

        # Timestamp for log
        timestamp = datetime.now(timezone.utc)

        # Generate signal_id: v23.1 {ShortReason}_{HHMMSS}
        # Example: IPA_LONG_213414 or IOFF_REVERSAL_OB_SHORT_213414
        # This guarantees max length <= 31 characters for MT5 DEAL_COMMENT
        time_str = timestamp.strftime('%H%M%S')
        signal_id = f"{short_reason}_{time_str}" if short_reason else f"{mode}_{dir_short}_{time_str}"

        signal = {
            # Core fields
            'signal_id': signal_id,
            'mode': mode,
            'direction': direction,
            'regime': regime,
            'entry_price': round(entry_price, 2),
            'stop_loss': sl_tp.stop_loss,
            'take_profit': sl_tp.take_profit,
            'score': score,
            'required_rr': sl_tp.actual_rr,
            'actual_rr': sl_tp.actual_rr,

            # Reasons
            'sl_reason': sl_tp.sl_reason,
            'tp_reason': sl_tp.tp2_reason,
            'short_reason': signal_type,   # v6.1: main TP = TP2

            # Classification
            'institutional_grade': institutional_grade,
            'session': session,
            'short_reason': short_reason,  # v12.6: pattern type for EA BE unlock / same-pattern guard

            # Metadata
            'timestamp': timestamp.strftime('%Y-%m-%d %H:%M:%S'),
            'utc_timestamp': timestamp.isoformat(),

            # SL/TP details
            'sl_distance': sl_tp.sl_distance,
            'sl_pct': sl_tp.sl_pct,

            # TP1 (BE trigger) & TP2 (actual TP) — v6.1
            'tp1_level': sl_tp.tp1_level,
            'tp2_level': sl_tp.tp2_level,
            'tp1_reason': sl_tp.tp1_reason,
            'tp2_reason': sl_tp.tp2_reason,
        }

        # Add extra data if provided
        if extra_data:
            signal.update(extra_data)

        logger.info(
            f"[SignalBuilder] Built {mode} {direction} [{regime}] | "
            f"ID: {signal_id} | Score: {score}/20 | "
            f"RR: {sl_tp.actual_rr:.2f} ({sl_tp.required_rr:.2f} req) | "
            f"Session: {session}"
        )

        return signal

    def build_ipa(self,
                  ipa_result: IPAResult,
                  sl_tp: 'SLTPRESult',
                  session: str,
                  regime: str = 'RANGING',
                  mode: str = 'IPA') -> Dict[str, Any]:
        """
        Build Signal from IPA result.

        Entry price is the mid-point of the entry zone.
        
        Args:
            mode: 'IPA' or 'IPA_FRVP' — used in signal_id for EA position tracking
        """
        # Entry zone mid-point
        entry_price = (ipa_result.entry_zone_min + ipa_result.entry_zone_max) / 2

        extra_data = {
            # v23.0: signal_type for AI logging
            'signal_type': getattr(ipa_result, 'signal_type', 'IPA'),  # IPA/IPA_FRVP/MOMENTUM
            
            # v26.1: score_breakdown for AI logging (Fix 2A)
            'score_breakdown': getattr(ipa_result, 'score_breakdown', {}),
            
            # IPA-specific fields
            'h1_bias': ipa_result.h1_bias,
            'ob_high': ipa_result.ob_high,
            'ob_low': ipa_result.ob_low,
            'entry_zone': [ipa_result.entry_zone_min, ipa_result.entry_zone_max],
            'sweep_confirmed': ipa_result.sweep_confirmed,
            'fvg_overlap': ipa_result.fvg_overlap,
            'volume_spike': ipa_result.volume_spike,
            'atr_m5': round(ipa_result.atr_m5, 2), 'm5_efficiency': getattr(ipa_result, 'm5_efficiency', 0.5), 'wall_scan': getattr(ipa_result, 'wall_scan', {}),

            # Levels
            'pdh': ipa_result.pdh,
            'pdl': ipa_result.pdl,
            'h1_fvg_boundary': ipa_result.h1_fvg_boundary,
        }

        return self.build(
            mode=mode,
            direction=ipa_result.direction,
            entry_price=entry_price,
            sl_tp=sl_tp,
            session=session,
            score=ipa_result.score,
            regime=regime,
            institutional_grade=ipa_result.score >= 14,
            extra_data=extra_data,
            # v26.0: IPAF short_reason includes entry source (OB/FVG/EMA/POC)
            # IPA → 'IPA_SHORT', IPAF → 'IPAF_OB_SHORT' / 'IPAF_FVG_SHORT' / 'IPAF_EMA_SHORT'
            short_reason=getattr(ipa_result, 'signal_type', 'IPA'),
        )



    def build_iof(self,
                  iof_result: IOFResult,
                  sl_tp: 'SLTPRESult',
                  session: str,
                  entry_price: float,
                  regime: str = 'RANGING',
                  mode: str = 'IOF') -> Dict[str, Any]:
        """
        Build Signal from IOF result.

        v8.1: entry_price must be current broker price (current_price),
        NOT wall_price. This ensures EA gets correct RR ratio matching SL/TP calc.
        
        Args:
            mode: 'IOF' or 'IOF_FRVP' — used in signal_id for EA position tracking
        """

        extra_data = {
            # v23.0: signal_type for AI logging
            'signal_type': iof_result.signal_type,  # MOMENTUM/ABSORPTION/REVERSAL/MEAN_REVERT
            
            # v26.1: score_breakdown for AI logging (Fix 2A)
            'score_breakdown': getattr(iof_result, 'score_breakdown', {}),
            
            # IOF-specific fields
            'wall_price': iof_result.wall_price,
            'wall_size_usd': iof_result.wall_size_usd,
            'der_score': round(iof_result.der_score, 2),
            'oi_change_pct': round(iof_result.oi_change_pct, 4),
            'rr_target': iof_result.rr_target,
            'volume_spike': iof_result.volume_spike,
            'rejection_candle': iof_result.rejection_candle,
            'atr_m5': round(iof_result.atr_m5, 2), 'm5_efficiency': getattr(iof_result, 'm5_efficiency', 0.5), 'wall_scan': getattr(iof_result, 'wall_scan', {}),

            # Levels
            'next_resistance': iof_result.next_resistance,
            'next_support': iof_result.next_support,
            
            # v39.1: MOMENTUM-specific analysis fields
            'momentum_direction': getattr(iof_result, 'momentum_direction', ''),
            'momentum_vs_regime': getattr(iof_result, 'momentum_vs_regime', ''),
            'momentum_vs_m5': getattr(iof_result, 'momentum_vs_m5', ''),
            'der_before_entry': round(getattr(iof_result, 'der_before_entry', 0.0), 3),
            'ema_counter_before': getattr(iof_result, 'ema_counter_before', False),
            'pullback_depth': round(getattr(iof_result, 'pullback_depth', 0.0), 3),
            'impulse_strength': round(getattr(iof_result, 'impulse_strength', 0.0), 3),
            'wall_proximity': round(getattr(iof_result, 'wall_proximity', 0.0), 2),
            'entry_timing': getattr(iof_result, 'entry_timing', ''),
        }

        return self.build(
            mode=mode,
            direction=iof_result.direction,
            entry_price=entry_price,
            sl_tp=sl_tp,
            session=session,
            score=iof_result.score,
            regime=regime,
            institutional_grade=iof_result.score >= 14,
            extra_data=extra_data,
            # v15.4: short_reason ใช้ชื่อย่อ ป้องกัน cooldown block ข้ามโหมด
            # IOF → 'IOF_MOMENTUM_LONG', IOFF → 'IOFF_MOMENTUM_LONG' (ไม่ซ้ำ!)
            short_reason=iof_result.signal_type,
        )

    def to_json_string(self, signal: Dict[str, Any]) -> str:
        """Serialize signal to JSON string for EA transmission."""
        import json
        return json.dumps(signal, indent=2)

    def from_json_string(self, json_str: str) -> Dict[str, Any]:
        """Deserialize signal from JSON string."""
        import json
        try:
            return json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.error(f"[SignalBuilder] Failed to parse JSON: {e}")
            return {}

    # ==============================================
    # v40.0: Unified build() method for SignalResult
    # ==============================================

    def build_from_result(self, signal, sl_tp) -> Dict[str, Any]:
        """
        v40.0: Unified build method for any SignalResult.
        
        Accepts either a SignalResult (v40.0) or a dict (backward compat).
        """
        # Support both SignalResult and dict
        if hasattr(signal, 'signal_type'):
            # SignalResult object
            signal_type = signal.signal_type
            direction = signal.direction
            entry_price = signal.entry_price
            score = signal.score
            session = signal.session
            regime = signal.regime
            atr_m5 = signal.atr_m5
            extra = signal.extra
            score_breakdown = signal.score_breakdown

            # Determine mode - use signal_type directly (no more IPA/IOF)
            mode = signal_type  # e.g., MOMENTUM, REVERSAL_OB, ABSORPTION, etc.

            extra_data = {
                'signal_type': signal_type,
                'score_breakdown': score_breakdown,
                'wall_price': extra.get('wall_price', entry_price),
                'wall_size_usd': extra.get('wall_size_usd', 0),
                'der_score': round(signal.der, 2),
                'oi_change_pct': extra.get('oi_change_pct', 0),
                'rr_target': extra.get('rr_target', 1.0),
                'volume_spike': extra.get('volume_spike', False),
                'rejection_candle': extra.get('rejection_candle', False),
                'atr_m5': round(atr_m5, 2),
                'm5_efficiency': extra.get('m5_efficiency', 0.5),
                'wall_scan': extra.get('wall_scan', {}),
                'next_resistance': extra.get('next_resistance'),
                'next_support': extra.get('next_support'),
                'regime': regime,
                'm5_state': signal.m5_state,
                'h1_bias_level': signal.h1_bias_level,
                'h1_dist_pct': signal.h1_dist_pct,
            }
        else:
            # Dict (backward compat)
            signal_type = signal.get('signal_type', signal.get('mode', 'MOMENTUM'))
            direction = signal.get('direction', 'LONG')
            entry_price = signal.get('entry_price', 0)
            score = signal.get('score', 0)
            session = signal.get('session', 'LONDON')
            regime = signal.get('regime', 'RANGING')
            atr_m5 = signal.get('atr_m5', 0)
            mode = signal.get('mode', signal_type)  # v72.1: default to signal_type
            extra_data = signal.get('extra_data', {})

        # v40.3: Removed lock_group (no longer used after v40.2)

        # Build signal_id
        timestamp = datetime.now(timezone.utc).strftime('%H%M%S')
        signal_id = f"{signal_type}_{direction}_{timestamp}"

        # Build payload
        payload = {
            'signal_id': signal_id,
            'mode': mode,  # backward compat for EA
            'signal_type': signal_type,  # v40.0: new field
            'direction': direction,
            'entry_price': entry_price,
            'stop_loss': sl_tp.stop_loss,
            'take_profit': sl_tp.take_profit,
            'score': score,
            'required_rr': sl_tp.actual_rr,
            'sl_reason': sl_tp.sl_reason,
            'tp_reason': sl_tp.tp2_reason,
            'short_reason': signal_type,
            'institutional_grade': score >= 14,
            'session': session,
            'timestamp': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'),
            'extra_data': extra_data,
        }

        return payload
