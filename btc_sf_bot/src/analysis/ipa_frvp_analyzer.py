"""
IPA_FRVP Analyzer - Price Action + Volume Profile
Combines IPA (OB/FVG/Structure) with FRVP (POC/VAH/VAL/HVN)
"""
import pandas as pd
import numpy as np
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field
from src.utils.logger import get_logger
from src.analysis.ipa_analyzer import IPAAnalyzer  # Inherit from IPA
from src.utils.decorators import log_errors, retry, circuit_breaker
from src.utils.metrics import timed_metric

logger = get_logger(__name__)


@dataclass
class IPAResult:
    """
    IPA_FRVP Result — compatible with IPA IPAResult for signal_builder.build_ipa().
    Extends with FRVP-specific fields.
    v11.2: All IPA fields preserved for signal_builder compatibility.
    """
    direction: str
    score: int
    h1_bias: str
    h1_bos: bool
    h1_choch: bool
    h1_fvg_unfilled: bool
    m5_choch: bool
    m5_bos: bool
    ob_high: float
    ob_low: float
    ob_body_pct: float
    ob_mitigated: bool
    fvg_overlap: bool
    sweep_confirmed: bool
    sweep_candles_ago: int
    volume_spike: bool
    zone_context: str
    atr_m5: float
    entry_zone_min: float
    entry_zone_max: float
    ob_distance_atr: float = 999.0  # v11.1
    swing_highs: List[float] = field(default_factory=list)
    swing_lows: List[float] = field(default_factory=list)
    pdh: Optional[float] = None
    pdl: Optional[float] = None
    h1_fvg_boundary: Optional[float] = None
    session: str = 'LONDON'
    score_breakdown: Dict = field(default_factory=dict)
    # v35.2: Added m5_efficiency field
    m5_efficiency: float = 0.5
    # v36.2: Added wall_scan field
    wall_scan: Dict = field(default_factory=dict)
    # v11.2: FRVP-specific fields
    frvp_data: Dict = None
    ob_at_hvn: bool = False
    signal_type: str = 'IPA_FRVP'
    fvg_high: Optional[float] = None
    fvg_low: Optional[float] = None


class IPAFRVPAnalyzer(IPAAnalyzer):
    """
    IPA_FRVP = IPA + FRVP
    
    Gate System (5 Gates):
    - Gate 1: H1 EMA Bias (same as IPA)
    - Gate 2: M5 BOS/CHoCH (same as IPA)
    - Gate 3: OB + HVN Validation (IPA + FRVP)
    - Gate 4: VAH/VAL Zone + Sweep (FRVP-based)
    - Gate 5: Volume (soft gate, same as IPA)
    
    Score Breakdown:
    - H1 Structure: max 6
    - OB + HVN: max 4 (OB+HVN=3, retest=1)
    - VAH/VAL zone: max 2
    - Sweep: max 2
    - EMA pullback: max 2
    - Equal levels: max 1
    - Session level: max 1
    - Volume: max 1
    Total: 20, Threshold: 10
    """
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        # H1 FIX: Extract both FRVP-specific and IPA base settings from the full config dict.
        ipa_config = config.get('ipa_frvp', {}) if config else {}
        
        # Pass the IPA sub-section to the base class so flat keys like
        # 'score_threshold' resolve correctly instead of returning None.
        super().__init__(config.get('ipa', {}) if config else {}, logger=logger, log_prefix="[IPAF]")
        
        # FRVP-specific settings
        self.frvp_hvn_bonus = ipa_config.get('ob_hvn_bonus', 3)  # OB + HVN = +3
        self.frvp_ob_only_score = ipa_config.get('ob_only_score', 1)  # OB without HVN = +1
        self.frvp_hvn_tolerance = ipa_config.get('hvn_tolerance', 20)  # $20 tolerance
        
        # Override score threshold for IPA_FRVP
        self.score_threshold = ipa_config.get('score_threshold', 10)
        
        self.logger.info(f"{self.log_prefix} IPA_FRVP Analyzer initialized | Threshold: {self.score_threshold}")
    
    @log_errors
    @timed_metric("IPAFRVPAnalyzer.analyze")
    @retry(max_attempts=3, delay=0.1, backoff=2.0, exceptions=(Exception,))
    @circuit_breaker(failure_threshold=5, timeout=30.0, expected_exception=Exception)
    def analyze(self, candles_m5: pd.DataFrame, candles_h1: pd.DataFrame,
                current_price: float, session: str, magnets: list,
                frvp_data: Dict, binance_data: Optional[Dict[str, Any]] = None,
                # v27.0: Optional atr_m5 from snapshot
                atr_m5: Optional[float] = None, snapshot: Optional[Any] = None) -> Optional[IPAResult]:
        """
        IPA_FRVP Analysis with FRVP integration.
        
        Args:
            candles_m5: M5 candles
            candles_h1: H1 candles
            current_price: current BTC price
            session: trading session
            magnets: list of magnetic levels
            frvp_data: pre-calculated FRVP data
            binance_data: optional market data
            atr_m5: v27.0 - Optional ATR from MarketSnapshot
            
        Returns:
            IPAResult or None
        """
        self.current_price = current_price
        self.session = session

        try:
            # v25.0: Suppress parent verbose logs — use _ipaf_quiet flag
            self._ipaf_quiet = True
            
            # v27.0: Set ATR from snapshot or calculate
            if atr_m5 is not None:
                self.atr_m5 = atr_m5

            # === Gate 1: H1 EMA Bias ===
            h1_result = self._check_h1_bias(candles_h1, candles_m5)
            if h1_result is None:
                return None

            direction = h1_result['direction']
            h1_bias = h1_result['bias']
            score_adjust = h1_result.get('score_adjust', 0)
            m5_result = self._check_m5_structure(candles_m5, direction)
            if m5_result is None:
                return None

            # === Gate 3: OB + HVN + Fallback ===
            ob_result = self._find_order_block_with_frvp(
                candles_m5, direction, m5_result['break_idx'], frvp_data
            )
            if ob_result is None:
                ob_result = self._gate3_ipaf_fallback(candles_m5, direction, frvp_data)
                if ob_result is None:
                    return None

            # === EQS ===
            self.atr_m5 = self._calc_atr(candles_m5, 14)
            eqs = self._check_entry_quality(candles_m5, direction, m5_result['break_idx'])
            self._entry_quality_adj = eqs

            if eqs <= -2:
                self.logger.info(f"{self.log_prefix} BLOCKED | EQS {eqs} (impulse)")
                return None

            ob_at_hvn = ob_result.get('at_hvn', False)
            ob_hvn_bonus = self.frvp_hvn_bonus if ob_at_hvn else 0
            ob_score = self.frvp_hvn_bonus + 1 if ob_at_hvn else self.frvp_ob_only_score
            
            ob_low = ob_result.get('ob_low') or ob_result.get('fvg_low', 0)
            ob_high = ob_result.get('ob_high') or ob_result.get('fvg_high', 0)
            # v26.0: Determine entry source for short_reason
            if ob_result.get('fallback'):
                fallback_type = ob_result['fallback']  # 'EMA' or 'POC'
            elif ob_result.get('fvg_high') and not ob_result.get('ob_body_pct'):
                fallback_type = 'FVG'
            else:
                fallback_type = 'OB'

            # === Gate 4: Zone + Sweep + Pullback ===
            zone_result = self._check_vah_val_zone(current_price, direction, frvp_data)
            sweep_result = self._check_liquidity_sweep(candles_m5, direction)
            sweep_confirmed = sweep_result.get('confirmed', False) if sweep_result else False
            sweep_candles = sweep_result.get('candles_ago', 999) if sweep_result else 999

            atr = self._calc_atr(candles_m5, 14)
            ema20_m5 = candles_m5['close'].ewm(span=20).mean().iloc[-1]
            ema_dist = abs(current_price - ema20_m5) / atr if atr > 0 else 999
            last_c = candles_m5.iloc[-1]
            ema_pullback = False
            if direction == 'SHORT' and current_price > ema20_m5 and ema_dist <= 0.5 and last_c['close'] < last_c['open']:
                ema_pullback = True
            elif direction == 'LONG' and current_price < ema20_m5 and ema_dist <= 0.5 and last_c['close'] > last_c['open']:
                ema_pullback = True

            equal_levels = bool(self._check_equal_levels(candles_m5, direction))
            session_level = bool(magnets and self._check_session_level(magnets, direction, atr))
            volume_ok = self._check_session_volume(candles_m5, m5_result['break_idx'])

            # Pullback check
            pullback = (binance_data or {}).get('pullback', {'status': 'NONE'})
            pb_status = pullback.get('status', 'NONE')
            pullback_ended = False
            if pb_status == 'ACTIVE':
                if eqs >= 2:
                    pullback_ended = True
                else:
                    self.logger.info(f"{self.log_prefix} BLOCKED | PB ACTIVE EQS:{eqs}<2")
                    return None
            elif pb_status == 'ENDED':
                pullback_ended = True

            # === Calculate Score ===
            score = self._calculate_score_frvp(
                h1_result=h1_result, m5_result=m5_result, ob_result=ob_result,
                ob_at_hvn=ob_at_hvn, zone_result=zone_result,
                sweep_confirmed=sweep_confirmed, sweep_candles=sweep_candles,
                ema_pullback=ema_pullback, equal_levels=equal_levels,
                session_level=session_level, volume_ok=volume_ok,
                frvp_data=frvp_data, pullback_ended=pullback_ended,
                score_adjust=score_adjust,
                m5_state=snapshot.m5_state if snapshot else 'RANGING',  # v38.4
            )

            if score < self.score_threshold:
                self.logger.info(
                    f"{self.log_prefix} {direction} Score:{score}/{self.score_threshold} | "
                    f"G3:{fallback_type} {ob_low:.0f}-{ob_high:.0f} | EQS:{eqs:+d} | "
                    f"Zone:{zone_result['zone']} PB:{pb_status} | {self._last_score_breakdown}"
                )
                return None
            
            # === Build Entry Zone ===
            entry_zone = self._build_entry_zone(ob_result, m5_result, direction)
            
            # === Get swings for SL/TP ===
            swings = self._get_swing_levels(candles_m5, direction)

            # === Add POC to magnets ===
            # v20.0: Handle new MLVP format
            frvp_magnets = list(magnets) if magnets else []
            poc_level = 0
            if frvp_data:
                if 'composite' in frvp_data:
                    poc_level = frvp_data['composite'].get('poc', 0) or 0
                else:
                    poc_level = frvp_data.get('poc', 0)
            if poc_level > 0:
                frvp_magnets.append({
                    'level': poc_level,
                    'type': 'POC',
                    'tier': 1
                })

            # v22.0: VAH/VAL as TP candidates
            if frvp_data and frvp_data.get('composite'):
                composite = frvp_data['composite']
                if direction == 'LONG' and composite.get('vah'):
                    frvp_magnets.append({
                        'level': composite['vah'],
                        'type': 'VAH',
                        'tier': 2
                    })
                elif direction == 'SHORT' and composite.get('val'):
                    frvp_magnets.append({
                        'level': composite['val'],
                        'type': 'VAL',
                        'tier': 2
                    })

            # === Build Entry Zone ===
            entry_zone = self._build_entry_zone(ob_result, m5_result, direction)

            # === Build IPAResult compatible with signal_builder.build_ipa() ===
            atr_m5 = self._calc_atr(candles_m5, 14)
            ob_low_val = ob_result.get('ob_low') or ob_result.get('fvg_low', 0)
            ob_high_val = ob_result.get('ob_high') or ob_result.get('fvg_high', 0)

            result = IPAResult(
                direction=direction,
                score=score,
                h1_bias=h1_bias,
                h1_bos=h1_result.get('bos', False),
                h1_choch=h1_result.get('choch', False),
                h1_fvg_unfilled=h1_result.get('fvg_unfilled', False),
                m5_choch=m5_result.get('choch', False),
                m5_bos=not m5_result.get('choch', False),
                ob_high=ob_high_val,
                ob_low=ob_low_val,
                ob_body_pct=ob_result.get('body_pct', 0),
                ob_mitigated=ob_result.get('mitigated', False),
                fvg_overlap=ob_result.get('fvg_overlap', False),
                sweep_confirmed=sweep_confirmed,
                sweep_candles_ago=sweep_candles,
                volume_spike=volume_ok,
                zone_context=zone_result.get('zone', 'NEUTRAL'),
                atr_m5=atr_m5,
                entry_zone_min=entry_zone['min'],
                entry_zone_max=entry_zone['max'],
                ob_distance_atr=ob_result.get('ob_distance_atr', 999.0),
                swing_highs=swings['highs'],
                swing_lows=swings['lows'],
                pdh=None,
                pdl=None,
                h1_fvg_boundary=None,
                m5_efficiency=getattr(snapshot, "m5_efficiency", 0.5) if snapshot else 0.5,
                wall_scan=getattr(snapshot, "wall_scan", {}) if snapshot else {},
                session=session,
                score_breakdown=self._last_score_breakdown,
                # v26.0: signal_type = entry source (OB/FVG/EMA/POC)
                frvp_data=frvp_data,
                ob_at_hvn=ob_at_hvn,
                signal_type=f'IPAF_{fallback_type}',  # IPAF_OB, IPAF_FVG, IPAF_EMA, IPAF_POC
                fvg_high=ob_result.get('fvg_high'),
                fvg_low=ob_result.get('fvg_low')
            )

            return result
            
        except Exception as e:
            self.logger.error(f"{self.log_prefix} Analysis error: {e}", exc_info=True)
            return None
    
    def _find_order_block_with_frvp(self, candles_m5: pd.DataFrame, direction: str,
                                   break_idx: int, frvp_data: Dict) -> Optional[Dict]:
        """Find OB and validate against HVN levels."""
        # Reuse IPA's _find_order_block logic
        ob_result = self._find_order_block(candles_m5, direction, break_idx)
        if ob_result is None:
            return None

        # Check if OB is at HVN
        # v20.0: Handle new MLVP format (layers) vs old FRVP (hvn)
        hvn_levels = []
        if 'layers' in frvp_data:
            # New MLVP format - use layer POCs as HVN
            layers = frvp_data.get('layers', {})
            for layer_name, layer_data in layers.items():
                if layer_data.get('poc'):
                    if layer_name in ('current_session', 'daily'):
                        hvn_levels.append(layer_data['poc'])
        else:
            # Old FRVP format
            hvn_levels = frvp_data.get('hvn', [])
        
        # FVG fallback may not have ob_low/ob_high — use fvg levels instead
        ob_low = ob_result.get('ob_low', ob_result.get('fvg_low'))
        ob_high = ob_result.get('ob_high', ob_result.get('fvg_high'))
        
        at_hvn = False
        for hvn in hvn_levels:
            if ob_low <= hvn <= ob_high:
                at_hvn = True
                break
        
        ob_result['at_hvn'] = at_hvn
        return ob_result


    def _gate3_ipaf_fallback(self, candles_m5, direction: str, frvp_data) -> Optional[Dict]:
        """
        v25.0: IPAF Gate 3 fallback when OB/FVG not found.
        A: EMA20 Pullback Zone — ราคาอยู่ใกล้ M5 EMA20 ±0.3 ATR
        B: MLVP POC Zone — ราคาอยู่ใกล้ composite POC ±0.3%

        Returns OB-like dict or None
        """
        current_price = float(candles_m5.iloc[-1]['close'])
        atr = self._calc_atr(candles_m5, 14)
        if atr <= 0:
            return None

        # === A: EMA20 Pullback Zone ===
        ema20 = float(candles_m5['close'].ewm(span=20).mean().iloc[-1])
        ema_dist = abs(current_price - ema20) / atr

        # v25.0: Composite Sideway Score (0-3) — ≥ 2 = sideway → skip EMA fallback
        recent = candles_m5.iloc[-10:]
        sideway_score = 0
        sw_reasons = []

        # 1. EMA9/20 slope แบน (EMA9 แทบไม่เปลี่ยน 2 แท่งล่าสุด)
        ema9_vals = candles_m5['close'].ewm(span=9).mean()
        ema9_slope = abs(float(ema9_vals.iloc[-1]) - float(ema9_vals.iloc[-3])) if len(ema9_vals) >= 3 else 999
        if ema9_slope < atr * 0.1:
            sideway_score += 1
            sw_reasons.append("EMA_FLAT")

        # 2. ATR contracting (ATR ปัจจุบัน < ATR 10 แท่งก่อน × 0.8)
        tr_recent = candles_m5['high'].iloc[-5:] - candles_m5['low'].iloc[-5:]
        tr_prev = candles_m5['high'].iloc[-15:-5] - candles_m5['low'].iloc[-15:-5]
        atr_now = float(tr_recent.mean()) if len(tr_recent) > 0 else atr
        atr_prev = float(tr_prev.mean()) if len(tr_prev) > 0 else atr
        if atr_prev > 0 and atr_now < atr_prev * 0.8:
            sideway_score += 1
            sw_reasons.append("ATR_CONTRACT")

        # 3. Body ratio เล็ก (avg body 5 แท่ง < 0.3 ATR)
        bodies = (candles_m5['close'].iloc[-5:] - candles_m5['open'].iloc[-5:]).abs()
        avg_body = float(bodies.mean())
        if avg_body < atr * 0.3:
            sideway_score += 1
            sw_reasons.append("SMALL_BODY")

        is_sideway = sideway_score >= 2

        if is_sideway:
            self.logger.info(
                f"{self.log_prefix} Gate 3: EMA fallback SKIP | Sideway {sideway_score}/3 ({'+'.join(sw_reasons)})"
            )
            # Skip EMA fallback, try POC below
        elif ema_dist <= 0.3:
            zone_low = ema20 - atr * 0.15
            zone_high = ema20 + atr * 0.15
            self.logger.info(
                f"{self.log_prefix} Gate 3: PASSED (EMA fallback) | "
                f"EMA20:{ema20:.0f} dist:{ema_dist:.2f}ATR zone:{zone_low:.0f}-{zone_high:.0f}"
            )
            return {
                'ob_low': zone_low,
                'ob_high': zone_high,
                'ob_body_pct': 0,
                'mitigated': False,
                'fvg_overlap': False,
                'ob_distance_atr': ema_dist,
                'at_hvn': False,
                'fallback': 'EMA',
            }

        # === B: MLVP POC Zone ===
        if frvp_data and frvp_data.get('composite'):
            poc = frvp_data['composite'].get('poc')
            if poc and poc > 0:
                poc_dist_pct = abs(current_price - poc) / current_price * 100
                if poc_dist_pct <= 0.3:
                    zone_low = poc - atr * 0.15
                    zone_high = poc + atr * 0.15
                    self.logger.info(
                        f"{self.log_prefix} Gate 3: PASSED (POC fallback) | "
                        f"POC:{poc:.0f} dist:{poc_dist_pct:.2f}% zone:{zone_low:.0f}-{zone_high:.0f}"
                    )
                    return {
                        'ob_low': zone_low,
                        'ob_high': zone_high,
                        'ob_body_pct': 0,
                        'mitigated': False,
                        'fvg_overlap': False,
                        'ob_distance_atr': poc_dist_pct,
                        'at_hvn': True,  # POC = high volume node
                        'fallback': 'POC',
                    }

        self.logger.info(
            f"{self.log_prefix} Gate 3: FAILED | No OB/FVG/EMA/POC | "
            f"EMA dist:{ema_dist:.2f}ATR"
        )
        return None

    def _check_vah_val_zone(self, price: float, direction: str, frvp_data: Dict) -> Dict:
        """
        Gate 4: VAH/VAL Zone Check
        
        Returns zone context based on FRVP:
        - LONG + price < VAL = DISCOUNT (+2)
        - SHORT + price > VAH = PREMIUM (+2)
        - Otherwise = NEUTRAL (0)
        
        v20.0: Updated to handle new MLVP format (composite)
        """
        # v20.0: Handle new MLVP format
        if 'composite' in frvp_data:
            comp = frvp_data.get('composite', {})
            vah = comp.get('vah', 0) or 0
            val = comp.get('val', 0) or 0
        else:
            vah = frvp_data.get('vah', 0)
            val = frvp_data.get('val', 0)
        
        zone = 'NEUTRAL'
        score = 0
        
        if direction == 'LONG' and val > 0:
            if price < val:
                zone = 'DISCOUNT'
                score = 2
        elif direction == 'SHORT' and vah > 0:
            if price > vah:
                zone = 'PREMIUM'
                score = 2
        
        return {'zone': zone, 'score': score, 'vah': vah, 'val': val}
    
    def _calculate_score_frvp(self, h1_result: Dict, m5_result: Dict,
                              ob_result: Dict, ob_at_hvn: bool,
                              zone_result: Dict, sweep_confirmed: bool,
                              sweep_candles: int,
                              ema_pullback: bool,
                              equal_levels: bool,
                              session_level: bool,
                              volume_ok: bool, frvp_data: Dict,
                              pullback_ended: bool = False,
                              score_adjust: int = 0,
                              m5_state: str = 'RANGING') -> int:
        """
        Calculate IPA_FRVP score (max 20 points).

        Breakdown:
        - H1 Structure: max 6
        - OB + HVN: max 4
        - OB Distance: max 2
        - VAH/VAL zone: max 2
        - Sweep: max 2
        - EMA pullback: max 2
        - Equal levels: max 1
        - Session level: max 1
        - Volume: max 1
        """
        self._last_score_breakdown = {}
        score = 0
        
        # === H1 Structure (max 6) ===
        if m5_result.get('choch'):
            score += 4
            self._last_score_breakdown['m5_choch'] = 4
        else:
            score += 2
            self._last_score_breakdown['m5_bos'] = 2
        
        if h1_result.get('choch'):
            # v38.4: Reduce h1_choch weight in RANGING/CHOPPY — false signals
            if m5_state in ('RANGING', 'CHOPPY', 'SIDEWAY', 'RECOVERY'):
                score += 2
                self._last_score_breakdown['h1_choch'] = 2
            else:
                score += 4
                self._last_score_breakdown['h1_choch'] = 4
        elif h1_result.get('bos'):
            score += 3
            self._last_score_breakdown['h1_bos'] = 3
        
        # === OB + HVN (max 4) ===
        # v25.0: Fallback entries get lower score
        is_fallback = ob_result.get('fallback')
        if is_fallback:
            # EMA/POC fallback = less reliable than OB
            if is_fallback == 'POC':
                score += 1
                self._last_score_breakdown['poc_fallback'] = 1
            else:
                score += 0
                self._last_score_breakdown['ema_fallback'] = 0
        elif ob_at_hvn:
            score += 3
            self._last_score_breakdown['ob_hvn'] = 3
        else:
            score += 1
            self._last_score_breakdown['ob_only'] = 1
        
        # v11.1: OB Distance ATR bonus — closer = better
        ob_dist = ob_result.get('ob_distance_atr', 999.0)
        if ob_dist <= 0.1:
            # Price in OB zone
            score += 2
            self._last_score_breakdown['ob_zone_entry'] = 2
        elif ob_dist <= 0.5:
            score += 1
            self._last_score_breakdown['ob_close'] = 1
        
        # === v22.0: OB@POC bonus (+1) ===
        ob_poc_bonus = 0
        if frvp_data and frvp_data.get('composite'):
            poc = frvp_data['composite'].get('poc')
            ob_low_price = ob_result.get('ob_low') or ob_result.get('fvg_low', 0)
            ob_high_price = ob_result.get('ob_high') or ob_result.get('fvg_high', 0)
            ob_mid = (ob_low_price + ob_high_price) / 2 if ob_low_price and ob_high_price else 0
            if poc and ob_mid > 0:
                ob_poc_dist = abs(ob_mid - poc) / poc * 100
                if ob_poc_dist < 0.3:
                    ob_poc_bonus = 1
        score += ob_poc_bonus
        self._last_score_breakdown['ob_at_poc'] = ob_poc_bonus

        # === VAH/VAL Zone (max 2) ===
        zone_score = zone_result.get('score', 0)
        score += zone_score
        self._last_score_breakdown['zone'] = zone_score
        
        # === Sweep (max 2) ===
        if sweep_confirmed and sweep_candles <= 10:
            score += 2
            self._last_score_breakdown['sweep'] = 2

        # === EMA Pullback (max 2) ===
        if ema_pullback:
            score += 2
            self._last_score_breakdown['ema_pullback'] = 2

        # === Equal Levels (max 1) ===
        if equal_levels:
            score += 1
            self._last_score_breakdown['equal_levels'] = 1

        # === Session Level (max 1) ===
        if session_level:
            score += 1
            self._last_score_breakdown['session_level'] = 1

        # === Pullback (v13.0) ===
        if pullback_ended:
            score += 2
            self._last_score_breakdown['pullback_ended'] = 2

        # === Volume (max 1) ===
        if volume_ok:
            score += 1
            self._last_score_breakdown['volume'] = 1
        
        # v22.0: Confluence Zones bonus
        confluence_bonus = 0
        if frvp_data and frvp_data.get('confluence_zones'):
            for zone in frvp_data['confluence_zones']:
                zone_dist = abs(self.current_price - zone['price']) / self.current_price * 100
                if zone_dist < 0.3:
                    if zone['strength'] >= 3:
                        confluence_bonus = 2
                    elif zone['strength'] >= 2:
                        confluence_bonus = 1
                    break
        score += confluence_bonus
        self._last_score_breakdown['confluence'] = confluence_bonus

        # v13.4: Gate 1 bias level adjustment
        score += score_adjust
        self._last_score_breakdown['bias_level'] = score_adjust

        # v16.4: Entry Quality Score (EQS) adjustment
        eqs_adj = getattr(self, '_entry_quality_adj', 0)
        if eqs_adj != 0:
            score += eqs_adj
            self._last_score_breakdown['entry_quality'] = eqs_adj

        # v30.8: Removed danger_hour penalty (data was unreliable - regime was always DEAD)
        
        self._last_score_breakdown['total'] = score
        return score

















