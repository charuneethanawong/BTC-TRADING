"""
IOF_FRVP Analyzer - Order Flow + Volume Profile
Combines IOF (Wall/Delta/OI) with FRVP (POC/VAH/VAL/HVN)
"""
import pandas as pd
import numpy as np
from typing import Optional, Dict, Any
from dataclasses import dataclass
from src.utils.logger import get_logger
from src.analysis.iof_analyzer import IOFAnalyzer
from src.utils.decorators import log_errors, retry, circuit_breaker
from src.utils.metrics import timed_metric

logger = get_logger(__name__)


@dataclass
class IOFResult:
    """
    IOF_FRVP Result — compatible with IOF IOFResult for signal_builder.build_iof().
    v11.2: signal_type has default value for compatibility.
    v15.9: Added reversal_mode and custom_magnets fields.
    """
    direction: str
    score: int
    wall_price: float
    wall_size_usd: float
    der_score: float
    oi_change_pct: float
    funding_rate: float
    volume_ratio: float
    rejection_candle: bool
    liquidation_cascade: bool
    wall_refill: bool
    wall_stability_seconds: float
    atr_m5: float
    rr_target: float
    volume_spike: bool
    session: str
    score_breakdown: Dict
    next_resistance: float
    next_support: float
    signal_type: str = 'MOMENTUM'  # v15.5: default 'MOMENTUM' (was 'IOF_FRVP')
    # FRVP-specific
    frvp_data: Dict = None
    wall_at_hvn: bool = False
    lvn_breakout: bool = False
    # v15.9: Added from IOF base class
    reversal_mode: str = 'STRUCTURAL'
    custom_magnets: Optional[Dict] = None


class IOFFRVPAnalyzer(IOFAnalyzer):
    """
    IOF_FRVP = IOF + FRVP
    
    Gate System (4 Gates):
    - Gate 1: Delta + EMA Context (same as IOF)
    - Gate 2: OI Change (soft gate, same as IOF)
    - Gate 3: Wall + FRVP Zone Validation
    - Gate 4: Rejection (same as IOF)
    
    Score Breakdown:
    - DER: max 5
    - Volume surge: max 2
    - OI divergence: max 3
    - Funding: max 2
    - Wall quality: max 3
    - Wall + HVN: max 2 (new)
    - Wall stability: max 1
    - Rejection: max 1
    - Liquidation: max 1
    Total: 20, Threshold: 6
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        # H1 FIX: Extract both FRVP-specific and IOF base settings from the full config dict.
        iof_config = config.get('iof_frvp', {}) if config else {}
        
        # Pass the IOF sub-section to the base class so flat keys like
        # 'score_threshold' resolve correctly instead of returning None.
        super().__init__(config.get('iof', {}) if config else {}, logger=logger, log_prefix="[IOFF]")

        # v24.0: IOFF ให้ REVERSAL_OB/OS ทำงานอิสระจาก MOMENTUM (ไม่ถูก override)
        self._keep_reversal_on_momentum = True

        # FRVP-specific settings
        self.wall_hvn_bonus = iof_config.get('wall_hvn_bonus', 2)  # Wall + HVN = +2
        self.lvn_tp_extend = iof_config.get('lvn_tp_extend', True)  # LVN breakout → extend TP
        
        # Override score threshold for IOF_FRVP
        self.score_threshold = iof_config.get('score_threshold', 6)
        
        self.logger.info(f"{self.log_prefix} IOF_FRVP Analyzer initialized | Threshold: {self.score_threshold}")
    
    @log_errors
    @timed_metric("IOFFRVPAnalyzer.analyze")
    @retry(max_attempts=3, delay=0.1, backoff=2.0, exceptions=(Exception,))
    @circuit_breaker(failure_threshold=5, timeout=30.0, expected_exception=Exception)
    def analyze(self, candles_m5: pd.DataFrame, binance_data: Dict,
                current_price: float, session: str, magnets: list,
                frvp_data: Dict,
                # v27.0: Optional atr_m5 from snapshot
                atr_m5: Optional[float] = None, snapshot: Optional[Any] = None) -> Optional[IOFResult]:
        """
        v15.9: IOFF = IOF base + FRVP layer + Exhaustion Quality for REVERSAL
        
        IOFF ต่างจาก IOF:
        - IOF Gate 2d: dist < 1.2% → BLOCK
        - IOFF Gate 2d: dist < 1.2% → Exhaustion Quality check (ไม่ block)
        
        Args:
            candles_m5: M5 candles
            binance_data: Binance data (funding, OI, etc.)
            current_price: current BTC price
            session: trading session
            magnets: list of magnetic levels
            frvp_data: pre-calculated FRVP data
            atr_m5: v27.0 - Optional ATR from MarketSnapshot
            
        Returns:
            IOFResult or None
        """
        self.current_price = current_price
        self.session = session
        self._last_candles_m5 = candles_m5
        
        # v27.0: Set ATR from snapshot or calculate
        if atr_m5 is not None:
            self.atr_m5 = atr_m5
        
        try:
            # === v16.9: Override modes before calling IOF ===
            # IOFF uses exhaustion instead of dist blocking
            self._use_exhaustion_quality = True
            # IOFF: MOMENTUM wall = SOFT (penalty -3 instead of blocking)
            self._use_soft_wall_momentum = True
            
            # === เรียก IOF analyze() (superclass) ===
            iof_result = super().analyze(
                candles_m5=candles_m5,
                binance_data=binance_data,
                current_price=current_price,
                session=session
            )
            
            self._use_exhaustion_quality = False  # reset
            self._use_soft_wall_momentum = False  # reset
            
            if iof_result is None:
                return None
            
            # === v15.9: FRVP Enhancement Layer ===
            frvp_bonus = 0
            zone_bonus = 0
            wall_at_hvn = False
            wall_at_lvn = False
            
            # v20.0: Handle new MultiLayerVolumeProfile format (composite) vs old FRVP format
            frvp_composite = None
            frvp_layers = None
            if frvp_data:
                if 'composite' in frvp_data:
                    # New format from MultiLayerVolumeProfile
                    frvp_composite = frvp_data.get('composite', {})
                    frvp_layers = frvp_data.get('layers', {})
                    poc = frvp_composite.get('poc') if frvp_composite else None
                    vah = frvp_composite.get('vah') if frvp_composite else None
                    val = frvp_composite.get('val') if frvp_composite else None
                    hvn_levels = []  # Composite doesn't have HVN/LVN
                    lvn_levels = []
                else:
                    # Old format from FRVPEngine
                    poc = frvp_data.get('poc', 0)
                    vah = frvp_data.get('vah', 0)
                    val = frvp_data.get('val', 0)
                    hvn_levels = frvp_data.get('hvn', [])
                    lvn_levels = frvp_data.get('lvn', [])
                
                # Validate wall against FRVP zones
                frvp_validation = self._validate_wall_frvp(
                    {'wall_price': iof_result.wall_price, 'wall_size_usd': iof_result.wall_size_usd},
                    frvp_data, iof_result.direction
                )
                wall_at_hvn = frvp_validation.get('at_hvn', False)
                wall_at_lvn = frvp_validation.get('at_lvn', False)
                
                # HVN bonus
                if wall_at_hvn:
                    frvp_bonus = 2
                
                # Zone bonus (LONG at VAL, SHORT at VAH)
                if iof_result.direction == 'LONG' and val and current_price <= val:
                    zone_bonus = 1
                elif iof_result.direction == 'SHORT' and vah and current_price >= vah:
                    zone_bonus = 1
                
                # v20.0: Log Multi-Layer info
                if frvp_layers:
                    curr_session = frvp_layers.get('current_session', {})
                    self.logger.info(
                        f"{self.log_prefix} MLVP | Composite POC:{poc if poc else 0:.0f} "
                        f"VAH:{vah if vah else 0:.0f} VAL:{val if val else 0:.0f} | "
                        f"Current:{curr_session.get('session_name', 'N/A')} | "
                        f"HVN:{wall_at_hvn} LVN:{wall_at_lvn} Zone:{zone_bonus:+d} | bonus:{frvp_bonus+zone_bonus:+d}"
                    )
                else:
                    self.logger.info(
                        f"{self.log_prefix} FRVP | POC:{poc if poc else 0:.0f} "
                        f"VAH:{vah if vah else 0:.0f} VAL:{val if val else 0:.0f} | "
                        f"HVN:{wall_at_hvn} LVN:{wall_at_lvn} Zone:{zone_bonus:+d} | bonus:{frvp_bonus+zone_bonus:+d}"
                    )
            
            # v22.0: Confluence Zones bonus
            confluence_bonus = 0
            if frvp_data and frvp_data.get('confluence_zones'):
                for zone in frvp_data['confluence_zones']:
                    zone_dist = abs(current_price - zone['price']) / current_price * 100
                    if zone_dist < 0.3:
                        if zone['strength'] >= 3:
                            confluence_bonus = 2
                        elif zone['strength'] >= 2:
                            confluence_bonus = 1
                        break

            # Calculate new score with FRVP bonuses
            new_score = iof_result.score + frvp_bonus + zone_bonus + confluence_bonus
            
            # Handle LVN breakout TP extension
            lvn_breakout = False
            tp_extend_info = ""
            if wall_at_lvn and self.lvn_tp_extend:
                next_hvn = self._get_next_hvn_after_lvn(iof_result.wall_price, frvp_data, iof_result.direction)
                if next_hvn:
                    tp_extend_info = f" → TP extend to HVN {next_hvn:.0f}"
                    lvn_breakout = True
            
            # Add POC to magnets if available
            frvp_magnets = dict(magnets) if magnets else {'buy_magnets': [], 'sell_magnets': []}
            # v20.0: Handle new MultiLayerVolumeProfile format
            poc_level = 0
            if frvp_data:
                if 'composite' in frvp_data and frvp_data.get('composite'):
                    poc_level = frvp_data['composite'].get('poc', 0) or 0
                else:
                    poc_level = frvp_data.get('poc', 0)
            if poc_level > 0:
                poc_magnet = {'level': poc_level, 'type': 'POC', 'tier': 1}
                if iof_result.direction == 'LONG':
                    frvp_magnets.setdefault('buy_magnets', []).append(poc_magnet)
                else:
                    frvp_magnets.setdefault('sell_magnets', []).append(poc_magnet)
            
            # v15.9: Add EMA20_H1_REVERT magnet for STRUCTURAL reversal mode
            # When in STRUCTURAL mode, TP should target EMA20 H1 (mean reversion)
            reversal_mode = getattr(self, '_reversal_mode', None)
            if reversal_mode == 'STRUCTURAL' and iof_result.signal_type in ('REVERSAL_OB', 'REVERSAL_OS'):
                ema20_h1 = binance_data.get('ema20_h1', 0)
                if ema20_h1 > 0:
                    ema20_magnet = {'level': ema20_h1, 'type': 'EMA20_H1_REVERT', 'tier': 1}
                    if iof_result.direction == 'LONG':
                        # TP above for LONG = resistance = EMA20 above current
                        frvp_magnets.setdefault('buy_magnets', []).append(ema20_magnet)
                    else:
                        # TP below for SHORT = support = EMA20 below current
                        frvp_magnets.setdefault('sell_magnets', []).append(ema20_magnet)
                    self.logger.info(
                        f"{self.log_prefix} TP Magnet: EMA20_H1_REVERT {ema20_h1:.0f} for {reversal_mode}"
                    )
            
            # Return new IOFResult with FRVP enhancements
            return IOFResult(
                direction=iof_result.direction,
                score=new_score,
                wall_price=iof_result.wall_price,
                wall_size_usd=iof_result.wall_size_usd,
                der_score=iof_result.der_score,
                oi_change_pct=iof_result.oi_change_pct,
                funding_rate=iof_result.funding_rate,
                volume_ratio=iof_result.volume_ratio,
                rejection_candle=iof_result.rejection_candle,
                liquidation_cascade=iof_result.liquidation_cascade,
                wall_refill=iof_result.wall_refill,
                wall_stability_seconds=iof_result.wall_stability_seconds,
                atr_m5=iof_result.atr_m5,
                rr_target=iof_result.rr_target,
                volume_spike=iof_result.volume_spike,
                session=session,
                score_breakdown={
                    **iof_result.score_breakdown,
                    'frvp_hvn': frvp_bonus,
                    'frvp_zone': zone_bonus,
                    'confluence': confluence_bonus,
                },
                next_resistance=iof_result.next_resistance,
                next_support=iof_result.next_support,
                m5_efficiency=getattr(snapshot, "m5_efficiency", 0.5) if snapshot else 0.5,
                wall_scan=getattr(snapshot, "wall_scan", {}) if snapshot else {},
                signal_type=iof_result.signal_type,
                frvp_data=frvp_data,
                wall_at_hvn=wall_at_hvn,
                lvn_breakout=lvn_breakout,
                reversal_mode=getattr(self, '_reversal_mode', 'STRUCTURAL'),  # v15.9
                custom_magnets=frvp_magnets,  # v15.9: Include EMA20_H1_REVERT magnet
            )
            
        except Exception as e:
            self.logger.error(f"{self.log_prefix} Analysis error: {e}", exc_info=True)
            return None
            
            # === Calculate Score ===
            # Return new IOFResult with FRVP enhancements
            return iof_result
            
        except Exception as e:
            self.logger.error(f"{self.log_prefix} Analysis error: {e}", exc_info=True)
            return None

    def _check_oi_signal(self, binance_data, direction):
        """Override: call parent logic but suppress [IOF] logs."""
        import io, logging
        _old = io.StringIO()
        _h = logging.StreamHandler(_old)
        _h.setLevel(logging.DEBUG)
        _parent_logger = logging.getLogger('src.analysis.iof_analyzer')
        _parent_logger.addHandler(_h)
        try:
            result = super()._check_oi_signal(binance_data, direction)
        finally:
            _parent_logger.removeHandler(_h)
        return result

    def _find_iceberg_wall(self, binance_data, direction, candles_m5):
        """Override: call parent logic but suppress [IOF] logs."""
        import io, logging
        _old = io.StringIO()
        _h = logging.StreamHandler(_old)
        _h.setLevel(logging.DEBUG)
        _parent_logger = logging.getLogger('src.analysis.iof_analyzer')
        _parent_logger.addHandler(_h)
        try:
            result = super()._find_iceberg_wall(binance_data, direction, candles_m5)
        finally:
            _parent_logger.removeHandler(_h)
        return result

    def _check_m5_rejection(self, candles_m5, wall_price, direction):
        """Override: call parent logic but suppress [IOF] logs."""
        import io, logging
        _old = io.StringIO()
        _h = logging.StreamHandler(_old)
        _h.setLevel(logging.DEBUG)
        _parent_logger = logging.getLogger('src.analysis.iof_analyzer')
        _parent_logger.addHandler(_h)
        try:
            result = super()._check_m5_rejection(candles_m5, wall_price, direction)
        finally:
            _parent_logger.removeHandler(_h)
        return result

    def _get_major_levels(self, candles_m5, direction, wall_price, entry_price):
        """Override: call parent logic but suppress [IOF] logs."""
        import io, logging
        _old = io.StringIO()
        _h = logging.StreamHandler(_old)
        _h.setLevel(logging.DEBUG)
        _parent_logger = logging.getLogger('src.analysis.iof_analyzer')
        _parent_logger.addHandler(_h)
        try:
            result = super()._get_major_levels(candles_m5, direction, wall_price, entry_price)
        finally:
            _parent_logger.removeHandler(_h)
        return result

    def _validate_wall_frvp(self, wall_result: Dict, frvp_data: Dict, direction: str) -> Dict:
        """
        Validate wall position against FRVP.
        
        Returns:
            - at_hvn: wall is at or near an HVN level
            - at_lvn: wall is at or near an LVN level
            - wall_frvp_score: bonus score for wall + FRVP alignment
        
        v20.0: Updated to handle MultiLayerVolumeProfile format (composite POC)
        """
        wall_price = wall_result.get('wall_price', 0)
        
        # v20.0: Handle both old FRVP (hvn/lvn) and new MLVP (composite) formats
        hvn_levels = []
        lvn_levels = []
        
        if 'layers' in frvp_data:
            # New MLVP format - create synthetic HVN/LVN from layer POCs
            # Use current session + daily POC as "HVN-like" (high activity)
            layers = frvp_data.get('layers', {})
            for layer_name, layer_data in layers.items():
                if layer_data.get('poc'):
                    if layer_name in ('current_session', 'daily'):
                        hvn_levels.append(layer_data['poc'])
                    else:
                        lvn_levels.append(layer_data['poc'])
        else:
            # Old FRVP format
            hvn_levels = frvp_data.get('hvn', [])
            lvn_levels = frvp_data.get('lvn', [])
        
        tolerance = 20  # $20 tolerance
        
        at_hvn = False
        at_lvn = False
        
        for hvn in hvn_levels:
            if abs(wall_price - hvn) <= tolerance:
                at_hvn = True
                break
        
        for lvn in lvn_levels:
            if abs(wall_price - lvn) <= tolerance:
                at_lvn = True
                break
        
        return {
            'at_hvn': at_hvn,
            'at_lvn': at_lvn,
        }
    
    def _get_next_hvn_after_lvn(self, lvn_price: float, frvp_data: Dict, direction: str) -> Optional[float]:
        """Get next HVN after LVN for TP extension. v22.0: supports MLVP format."""
        # v22.0: รองรับทั้ง old FRVP (hvn key) และ MLVP (layer POCs)
        hvn_levels = []
        if 'layers' in frvp_data:
            for layer_name in ('current_session', 'daily'):
                layer = frvp_data.get('layers', {}).get(layer_name, {})
                if layer.get('poc'):
                    hvn_levels.append(layer['poc'])
        else:
            hvn_levels = frvp_data.get('hvn', [])
        
        if direction == 'LONG':
            # Find HVN above LVN
            candidates = [h for h in hvn_levels if h > lvn_price]
            if candidates:
                return min(candidates)
        else:  # SHORT
            # Find HVN below LVN
            candidates = [h for h in hvn_levels if h < lvn_price]
            if candidates:
                return max(candidates)
        
        return None
    
    def _calculate_score_frvp(self, delta_result: Dict, oi_result: Dict,
                             wall_result: Dict, frvp_validation: Dict,
                             rejection: bool, binance_data: Dict) -> tuple:
        """
        Calculate IOF_FRVP score (max 20 points).
        
        Breakdown:
        - DER: max 5
        - Volume surge: max 2
        - OI divergence: max 3
        - Funding: max 2
        - Wall quality: max 3
        - Wall + HVN: max 2 (new)
        - Wall stability: max 1
        - Rejection: max 1
        - Liquidation: max 1
        """
        score = 0
        breakdown = {}
        
        # === Delta Absorption Quality (max 5) ===
        der = delta_result['der']
        if der > self.der_strong:
            score += 5
            breakdown['der_strong'] = 5
        elif der > self.der_moderate:
            score += 4
            breakdown['der_moderate'] = 4
        elif der > self.der_min:
            score += 3
            breakdown['der_weak'] = 3
        
        # Volume surge (max 2)
        vol_ratio = delta_result['volume_ratio']
        if vol_ratio >= 2.0:
            score += 2
            breakdown['volume_surge_high'] = 2
        elif vol_ratio >= 1.2:
            score += 1
            breakdown['volume_surge'] = 1
        
        # === OI & Funding (max 3+2=5) ===
        oi_change = abs(oi_result.get('oi_change_pct', 0))
        if oi_change > self.oi_divergence_strong:
            score += 3
            breakdown['oi_divergence_strong'] = 3
        elif oi_change > self.oi_change_min_pct:
            score += 2
            breakdown['oi_divergence'] = 2
        
        funding = abs(binance_data.get('funding_rate', 0))
        if funding > self.funding_extreme:
            score += 2
            breakdown['funding_extreme'] = 2
        elif funding > self.funding_extreme * 0.4:
            score += 1
            breakdown['funding_moderate'] = 1
        
        # === Wall Quality (max 3) ===
        wall_usd = wall_result['wall_size_usd']
        if wall_usd > 500000 and wall_result.get('refill_confirmed'):
            score += 3
            breakdown['wall_strong'] = 3
        elif wall_usd > 300000:
            score += 2
            breakdown['wall_medium'] = 2
        elif wall_usd > 100000:
            score += 1
            breakdown['wall_low'] = 1
        else:
            score += 1
            breakdown['wall_minimal'] = 1
        
        # === Wall + HVN/LVN (max 2) ===
        if frvp_validation['at_hvn']:
            score += self.wall_hvn_bonus
            breakdown['wall_hvn'] = self.wall_hvn_bonus
        elif frvp_validation['at_lvn']:
            score += 1
            breakdown['wall_lvn'] = 1
        
        # === Wall Stability (max 1) ===
        if wall_result.get('stability_seconds', 0) >= 60:
            score += 1
            breakdown['wall_stable'] = 1
        
        # === Rejection (max 1) ===
        if rejection:
            score += 1
            breakdown['rejection'] = 1
        
        # === Liquidation (max 1) ===
        if binance_data.get('liquidation_cascade', False):
            score += 1
            breakdown['liquidation'] = 1
        
        breakdown['total'] = score
        return score, breakdown






