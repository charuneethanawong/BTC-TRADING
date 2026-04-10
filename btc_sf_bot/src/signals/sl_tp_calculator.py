"""
Institutional SL/TP Calculator — v38.2

New SL/TP System:
  - SL: ATR × base_mult × session_scale (Session-adaptive ATR)
  - TP1: BE trigger (RR >= 0.5 for MOMENTUM, 0.8 for others)
  - TP2: Actual take profit (RR >= 1.0 for MOMENTUM, 1.2+ for others)

v38.2: MOMENTUM tp1_rr 0.8→0.5, tp2_rr 1.2→1.0, tp2_atr_fallback 1.5→1.2

Config:
  sl:
    ipa_base_mult: 1.0    # IPA uses structural OB → less buffer needed
    iof_base_mult: 1.2    # IOF uses wall → more buffer needed
    min_atr: 0.8          # Safety minimum
    max_atr: 1.5          # Safety maximum
    session_scale:
      ASIA: 0.85
      LONDON: 1.0
      LONDON-NY: 1.15
      NY: 1.1
      ASIA-LATE: 0.85

  tp:
    tp1_rr_min: 0.8        # BE trigger: nearest magnet with RR >= 0.8
    tp2_rr_min: 1.2        # Actual TP: magnet with RR >= 1.2
    tp1_atr_fallback: 1.2   # v9.2: ATR fallback (was 1.0 — ensures RR >= 1.2 when no magnet)
    tp2_atr_fallback: 1.8  # v9.2: ATR fallback (was 1.5 — ensures RR >= 1.2 when no magnet)
    move_sl_to_be: true     # EA will move SL to breakeven when TP1 reached
"""
from dataclasses import dataclass, field
from typing import Optional, List, Tuple, Dict, Any
import numpy as np

from src.utils.logger import get_logger

logger = get_logger(__name__)

# MOD-6 (v44.4): SHORT TP cap - prevent unreachable TP after v43.8 VP range expansion
TP_SHORT_MAX = 300  # Max TP distance in pts for SHORT signals

# v55.0: Fixed-point SL/TP per signal type based on MFE/MAE statistics
# sl_pts / tp_pts are absolute price distances (BTC points)
# v71.0 MOD-90: Dynamic BE - REVERSAL=100pts, MOMENTUM/ABSORPTION=120pts
# v61.0 MOD-61: REVERSAL uses fixed BE at 100 pts (MFE 110-140)
SL_TP_CONFIG: Dict[str, Dict[str, float]] = {
    'MOMENTUM':    {'sl_pts': 200, 'tp_pts': 150},  # BE=120 (50% of TP)
    'REVERSAL_OB': {'sl_pts': 200, 'tp_pts': 300},  # v71.0: BE fixed at 100 (MFE 110-140)
    'REVERSAL_OS': {'sl_pts': 200, 'tp_pts': 300},  # v71.0: BE fixed at 100 (MFE 110-140)
    'ABSORPTION':  {'sl_pts': 200, 'tp_pts': 160},  # BE=120 (50% of TP)
    'IPA':         {'sl_pts': 200, 'tp_pts': 200},
    'VP_BOUNCE':   {'sl_pts': 150, 'tp_pts': 100}, # v70.0 MOD-85: Balanced risk
    'VP_REVERT':   {'sl_pts': 150, 'tp_pts': 100},
    'VP_BREAKOUT': {'sl_pts': 200, 'tp_pts': 150},
    'VP_POC':      {'sl_pts': 200, 'tp_pts': 100},
    'VP_ABSORB':   {'sl_pts': 250, 'tp_pts': 150},
    'MEAN_REVERT': {'sl_pts': 200, 'tp_pts': 150},
}
_SL_TP_DEFAULT = {'sl_pts': 200, 'tp_pts': 150}


@dataclass
class SLTPRESult:
    """Result of SL/TP calculation."""
    stop_loss: float
    take_profit: float
    sl_distance: float          # Distance from entry to SL in price units
    sl_pct: float             # SL as % of entry
    actual_rr: float          # Actual risk-reward ratio (TP2)
    required_rr: float        # RR sent to EA (actual_rr * 0.95 for tolerance)
    tp1_level: float         # BE trigger level (RR >= 0.8)
    tp2_level: float         # Actual TP level (RR >= 1.2)
    sl_reason: str           # Why this SL was chosen
    tp1_reason: str          # Why TP1 was chosen
    tp2_reason: str          # Why TP2 was chosen


class InstitutionalSLTPCalculator:
    """
    v6.1: New SL/TP system with Session-adaptive ATR and Smart TP.

    SL = ATR × base_mult × session_scale
    TP1 = BE trigger (nearest magnet with RR >= 0.8)
    TP2 = Actual TP (next magnet with RR >= 1.2)
    """

    def __init__(self, config: dict = None):
        self.config = config or {}

        # === SL Configuration (v6.1: Session-adaptive ATR)
        # v43.0: SL based on MAE statistics
        # MOMENTUM: MAE=$77 (low) → can use tighter SL
        # ABSORPTION: MAE=$83 (low) → standard SL
        # IPA: MAE=$110 (HIGH!) → need MORE buffer for stop hunt protection
        sl_config = self.config.get('sl', {})
        self.ipa_base_mult: float = sl_config.get('ipa_base_mult', 1.3)  # v43.0: 1.0→1.3 (higher MAE risk!)
        self.iof_base_mult: float = sl_config.get('iof_base_mult', 1.2)  # standard
        self.min_atr: float = sl_config.get('min_atr', 0.8)   # Safety minimum
        self.max_atr: float = sl_config.get('max_atr', 1.8)  # v43.0: 1.5→1.8 (higher for IPA!)
        self.absolute_min_sl_pct: float = sl_config.get('absolute_min_sl_pct', 0.002)  # v7.0 Bug#3: % of price

        # Session scales
        self.session_scale: Dict[str, float] = sl_config.get('session_scale', {
            'ASIA': 0.85,
            'LONDON': 1.0,
            'LONDON-NY': 1.15,
            'NY': 1.1,
            'ASIA-LATE': 0.85,
        })

        # === TP Configuration (v6.1: Smart TP with BE trigger) ===
        tp_config = self.config.get('tp', {})
        self.tp1_rr_min: float = tp_config.get('tp1_rr_min', 0.8)   # BE trigger
        self.tp2_rr_min: float = tp_config.get('tp2_rr_min', 1.2)   # Actual TP
        self.tp1_atr_fallback: float = tp_config.get('tp1_atr_fallback', 1.0)
        self.tp2_atr_fallback: float = tp_config.get('tp2_atr_fallback', 1.2)
        self.move_sl_to_be: bool = tp_config.get('move_sl_to_be', True)

        # === Legacy config for backward compat ===
        ipa_config = self.config.get('ipa', {})
        iof_config = self.config.get('iof', {})
        self.rr_min_ipa: float = ipa_config.get('rr_min', 1.0)
        self.rr_min_iof_asia: float = iof_config.get('rr_min_asia', 1.0)
        self.rr_min_iof_standard: float = iof_config.get('rr_min_standard', 1.0)

        # Round number magnet settings
        self.round_number_step: float = self.config.get('round_number_step', 1000)
        # v25.0: BE trigger ช้าลง (เดิม 0.8 → BE บ่อยเกิน 12/15 WIN = BE)
        # TP1 RR 1.2 = ราคาต้องวิ่ง 67% ของ TP ก่อน BE → โอกาสถึง TP สูงขึ้น
        
        # v43.0: Dynamic SL/TP based on MFE/MAE Statistics
        # MOMENTUM: MFE=$283, MAE=$77 → High accuracy, can push TP slightly higher
        # ABSORPTION: MFE=$399 (highest!), MAE=$83 → Big runner, increase TP significantly
        # REVERSAL: MFE lower → Keep conservative TP
        # IPA: MFE=$275, MAE=$110 (highest risk!) → Reduce TP, increase SL buffer
        self.rr_config = {
            'MOMENTUM':    {'tp1_rr': 0.6, 'tp2_rr': 1.2},  # v71.0: BE at 60% (120pts)
            'ABSORPTION':  {'tp1_rr': 0.6, 'tp2_rr': 0.8},  # v71.0: BE at 60% (120pts)
            # v71.0 MOD-90: REVERSAL BE at 100 pts (MFE 110-140)
            'REVERSAL_OB': {'tp1_rr': 100/200, 'tp2_rr': 300/200},  # SL=200, TP1=100, TP2=300 → 0.5:1.5 RR
            'REVERSAL_OS': {'tp1_rr': 100/200, 'tp2_rr': 300/200},  # SL=200, TP1=100, TP2=300 → 0.5:1.5 RR
            'MEAN_REVERT': {'tp1_rr': 0.5, 'tp2_rr': 0.6},  # v43.0: 0.8→0.6 (conservative)
            # v51.0: MOD-38 - unified IPA
            'IPA':         {'tp1_rr': 0.6, 'tp2_rr': 0.8},  # unified IPA config
            # v43.7: VP signal types
            'VP_BOUNCE':   {'tp1_rr': 0.5, 'tp2_rr': 1.2},  # HVN bounce → moderate TP
            'VP_BREAKOUT': {'tp1_rr': 0.5, 'tp2_rr': 1.5},  # Breakout → higher TP
            'VP_ABSORB':   {'tp1_rr': 0.5, 'tp2_rr': 1.2},  # Absorb → moderate TP
            'VP_REVERT':   {'tp1_rr': 0.5, 'tp2_rr': 0.8},  # Revert to POC → short TP
            'VP_POC':      {'tp1_rr': 0.5, 'tp2_rr': 1.0},  # POC reaction → moderate TP
        }


        logger.debug(f"[SLTP-v6.1] Config | IPA base: {self.ipa_base_mult}x | IOF base: {self.iof_base_mult}x")

    def calculate_ipa(self,
                      entry_price: float,
                      direction: str,
                      ob_high: Optional[float],
                      ob_low: Optional[float],
                      atr_m5: float,
                      session: str = 'LONDON',
                      swing_highs: Optional[List[float]] = None,
                      swing_lows: Optional[List[float]] = None,
                      pdh: Optional[float] = None,
                      pdl: Optional[float] = None,
                      h1_fvg_boundary: Optional[float] = None,
                      magnets: Optional[Dict[str, Any]] = None,
                      signal_type: str = 'MOMENTUM') -> Optional[SLTPRESult]:
        """
        Calculate SL/TP for IPA (Institutional Price Action) signal.

        Args:
            entry_price: Entry price
            direction: 'LONG' or 'SHORT'
            ob_high: Order Block high price
            ob_low: Order Block low price
            atr_m5: ATR(14) on M5
            session: Trading session (ASIA/LONDON/NY)
            swing_highs: List of swing high levels
            swing_lows: List of swing low levels
            pdh: Previous Day High
            pdl: Previous Day Low
            h1_fvg_boundary: H1 FVG boundary (unfilled)
            magnets: Dict with 'buy_magnets' and 'sell_magnets' lists
            signal_type: Signal type (MOMENTUM, ABSORPTION, REVERSAL_OB, REVERSAL_OS, MEAN_REVERT)

        Returns:
            SLTPRESult or None if RR target not met
        """
        try:
            # === SL Calculation (v17.5: Anti-Stop Hunt SL) ===
            sl, sl_reason = self._calc_sl_v17(
                entry=entry_price,
                direction=direction,
                anchor_price=ob_low if direction == 'LONG' else ob_high,
                atr=atr_m5,
                mode='IPA',
                session=session,
                signal_type=signal_type,
                swing_lows=swing_lows,
                swing_highs=swing_highs
            )

            sl_distance = abs(entry_price - sl)
            sl_pct = sl_distance / entry_price

            logger.debug(f"[SLTP-IPA] SL | Entry:{entry_price} | SL:{sl:.2f} | Dist:{sl_distance:.1f} | {sl_reason}")

            # === TP Calculation (v6.1: Smart TP with BE trigger) ===
            tp1, tp2 = self._find_smart_tp_with_be(
                entry=entry_price,
                direction=direction,
                signal_type='IPA',
                sl_distance=sl_distance,
                atr=atr_m5,
                session=session,
                magnets=magnets,
                next_resistance=pdh,
                next_support=pdl,
                mode='IPA'
            )

            if tp1 is None or tp2 is None:
                logger.info(f"[SLTP-IPA] ❌ No valid TP found | Entry:{entry_price} | SL:{sl:.2f} | SLdist:{sl_distance:.1f} — returning None")
                return None

            tp1_price, tp1_reason = tp1
            tp2_price, tp2_reason = tp2

            # === RR Check (v43.0: Dynamic RR based on specific Signal Type) ===
            cfg = self.rr_config.get(signal_type, self.rr_config['IPA'])
            tp2_rr_min = cfg['tp2_rr']
            actual_rr = abs(tp2_price - entry_price) / sl_distance if sl_distance > 0 else 0

            # v43.3: Use <= to allow equality (1.20 == 1.2 should pass)
            if actual_rr <= tp2_rr_min - 0.01:  # Allow 0.01 margin for floating point
                logger.info(f"[SLTP-IPA] RR check failed: actual_rr {actual_rr:.2f} < min {tp2_rr_min} (type:IPA) → NO SIGNAL")
                return None

            return SLTPRESult(
                stop_loss=round(sl, 2),
                take_profit=round(tp2_price, 2),
                sl_distance=round(sl_distance, 2),
                sl_pct=round(sl_pct, 4),
                actual_rr=round(actual_rr, 2),
                required_rr=round(actual_rr * 0.95, 2),
                tp1_level=round(tp1_price, 2),
                tp2_level=round(tp2_price, 2),
                sl_reason=sl_reason,
                tp1_reason=tp1_reason,
                tp2_reason=tp2_reason
            )

        except Exception as e:
            logger.error(f"[SLTP-IPA] Calculation error: {e}", exc_info=True)
            return None

    def calculate_iof(self,
                      entry_price: float,
                      direction: str,
                      wall_price: float,
                      atr_m5: float,
                      session: str = 'LONDON',
                      next_resistance: Optional[float] = None,
                      next_support: Optional[float] = None,
                      magnets: Optional[Dict[str, Any]] = None,
                      swing_highs: Optional[List[float]] = None,
                      swing_lows: Optional[List[float]] = None,
                      h1_fvg_boundary: Optional[float] = None,
                      signal_type: str = 'MOMENTUM') -> Optional[SLTPRESult]:
        """
        Calculate SL/TP for IOF (Institutional Order Flow) signal.

        Args:
            entry_price: Entry price
            direction: 'LONG' or 'SHORT'
            wall_price: Iceberg wall price level
            atr_m5: ATR(14) on M5
            session: Trading session (ASIA/LONDON/NY)
            next_resistance: Next major resistance level
            next_support: Next major support level
            magnets: Dict with 'buy_magnets' and 'sell_magnets' lists
            swing_highs: List of swing high levels
            swing_lows: List of swing low levels
            signal_type: Signal type (MOMENTUM, ABSORPTION, REVERSAL_OB, REVERSAL_OS, MEAN_REVERT)

        Returns:
            SLTPRESult or None if RR target not met
        """
        try:
            logger.debug(f"[SLTP-IOF] Input | Entry:{entry_price} | Dir:{direction} | Wall:{wall_price} | ATR:{atr_m5:.1f} | Session:{session} | Signal:{signal_type}")

            # === SL Calculation (v17.5: Anti-Stop Hunt SL) ===
            sl, sl_reason = self._calc_sl_v17(
                entry=entry_price,
                direction=direction,
                anchor_price=wall_price,
                atr=atr_m5,
                mode='IOF',
                session=session,
                signal_type=signal_type,
                swing_lows=swing_lows,
                swing_highs=swing_highs
            )

            sl_distance = abs(entry_price - sl)
            sl_pct = sl_distance / entry_price

            logger.debug(f"[SLTP-IOF] SL | Entry:{entry_price} | SL:{sl:.2f} | Dist:{sl_distance:.1f} | {sl_reason}")

            # === TP Calculation (v6.1: Smart TP with BE trigger) ===
            tp1, tp2 = self._find_smart_tp_with_be(
                entry=entry_price,
                direction=direction,
                signal_type=signal_type,
                sl_distance=sl_distance,
                atr=atr_m5,
                session=session,
                magnets=magnets,
                next_resistance=next_resistance,
                next_support=next_support,
                mode='IOF'
            )

            if tp1 is None or tp2 is None:
                logger.debug(f"[SLTP-IOF] No valid TP found for {direction}")
                return None

            tp1_price, tp1_reason = tp1
            tp2_price, tp2_reason = tp2

            # === RR Check (v18.2: Dynamic RR based on Signal Type) ===
            cfg = self.rr_config.get(signal_type, self.rr_config['MOMENTUM'])
            tp2_rr_min = cfg['tp2_rr']
            actual_rr = abs(tp2_price - entry_price) / sl_distance if sl_distance > 0 else 0

            # v43.3: Use <= to allow equality (1.20 == 1.2 should pass)
            if actual_rr <= tp2_rr_min - 0.01:  # Allow 0.01 margin for floating point
                logger.info(f"[SLTP-IOF] RR check failed: actual_rr {actual_rr:.2f} < min {tp2_rr_min} (type:{signal_type}) → NO SIGNAL")
                return None

            return SLTPRESult(
                stop_loss=round(sl, 2),
                take_profit=round(tp2_price, 2),
                sl_distance=round(sl_distance, 2),
                sl_pct=round(sl_pct, 4),
                actual_rr=round(actual_rr, 2),
                required_rr=round(actual_rr * 0.95, 2),
                tp1_level=round(tp1_price, 2),
                tp2_level=round(tp2_price, 2),
                sl_reason=sl_reason,
                tp1_reason=tp1_reason,
                tp2_reason=tp2_reason
            )

        except Exception as e:
            logger.error(f"[SLTP-IOF] Calculation error: {e}", exc_info=True)
            return None

    def _calc_sl_v61(self, entry: float, direction: str,
                     anchor_price: Optional[float],
                     atr: float,
                     mode: str,
                     session: str) -> Tuple[float, str]:
        """
        v6.1: Calculate SL using ATR × base_mult × session_scale.

        v9.4 Section 1.3: If anchor ≈ entry (|entry - anchor| < 0.3 ATR),
        use entry as base instead of anchor to avoid SL compression.

        SL = ATR × base_mult × session_scale, clamped to min/max ATR.

        Args:
            entry: Entry price
            direction: 'LONG' or 'SHORT'
            anchor_price: OB boundary (IPA) or Wall price (IOF)
            atr: ATR value
            mode: 'IPA' or 'IOF'
            session: Trading session

        Returns:
            Tuple[float, str]: (SL price, reason)
        """
        # Get base multiplier for mode
        base_mult = self.iof_base_mult if mode == 'IOF' else self.ipa_base_mult

        # Get session scale
        session_mult = self.session_scale.get(session, 1.0)

        # Calculate SL distance
        sl_distance = atr * base_mult * session_mult

        # Apply safety clamps (min/max ATR)
        sl_distance = max(sl_distance, atr * self.min_atr)
        sl_distance = min(sl_distance, atr * self.max_atr)

        # v7.0 Bug#3 FIX: Use percentage of entry price (scales with BTC price, not hardcoded $150)
        abs_min_distance = entry * self.absolute_min_sl_pct
        sl_distance = max(sl_distance, abs_min_distance)

        # v9.4 Section 1.3: Check if anchor is too close to entry (< 0.3 ATR)
        # If so, use entry as base to avoid SL compression
        anchor_threshold = atr * 0.3
        use_entry_as_base = False
        
        if anchor_price and abs(anchor_price - entry) < anchor_threshold:
            use_entry_as_base = True
            logger.debug(f"[SLTP] anchor≈entry (diff:{abs(anchor_price - entry):.1f} < {anchor_threshold:.1f}) → use entry as base")

        # Calculate SL price
        if direction == 'LONG':
            # SL goes below entry
            if anchor_price and anchor_price < entry and not use_entry_as_base:
                # Anchor is below entry (valid OB zone) and far enough → use anchor
                sl = min(anchor_price, entry) - sl_distance
                # Safety: SL must be at least 0.5 ATR below anchor/entry
                min_sl = anchor_price - atr * 0.5
                sl = min(sl, min_sl)
            else:
                # Anchor is above entry OR too close to entry → use entry as base
                sl = entry - sl_distance
                # v9.7: No safety clamp when anchor≈entry (prevents SL compression)
                # v9.4 fix handles this case, no extra clamp needed

            return sl, f'SL_ATR_{base_mult}x{session_mult}'
        else:
            # SL goes above entry
            if anchor_price and anchor_price > entry and not use_entry_as_base:
                # Anchor is above entry (valid wall zone) and far enough → use anchor
                sl = max(anchor_price, entry) + sl_distance
                # Safety: SL must be at least 0.5 ATR above anchor/entry
                max_sl = anchor_price + atr * 0.5
                sl = max(sl, max_sl)
            else:
                # Anchor is below entry OR too close to entry → use entry as base
                sl = entry + sl_distance
                # v9.7: No safety clamp when anchor≈entry (prevents SL compression)
                # v9.4 fix handles this case, no extra clamp needed

            return sl, f'SL_ATR_{base_mult}x{session_mult}'

    def _calc_sl_v17(self, entry: float, direction: str,
                     anchor_price: Optional[float],
                     atr: float,
                     mode: str,
                     session: str,
                     signal_type: str = 'MOMENTUM',
                     swing_lows: Optional[List[float]] = None,
                     swing_highs: Optional[List[float]] = None) -> Tuple[float, str]:
        """
        v17.5: Anti-Stop Hunt SL — 3 ชั้นป้องกัน

        Layers:
        1. ATR buffer (เดิม)
        2. Sweep buffer (ใหม่) — ป้องกัน sweep ที่ swing low/high
        3. Liquidity cluster (ใหม่) — วาง SL ใต้ cluster ทั้งหมด

        SL Multiplier ตาม Signal Type:
        | Signal Type   | Type Mult | Sweep Buffer | Liq Buffer | เหตุผล                 |
        |---------------|-----------|--------------|------------|--------------------------|
        | MOMENTUM      | 1.0 ATR   | price × 0.2% | 0.5 ATR    | ผิดทาง = ออกเร็ว        |
        | ABSORPTION    | 1.3 ATR   | price × 0.2% | 0.5 ATR    | test zone ซ้ำได้          |
        | REVERSAL_OB   | 1.3 ATR   | price × 0.3% | 0.7 ATR    | sweep ก่อน reverse       |
        | REVERSAL_OS   | 1.3 ATR   | price × 0.3% | 0.7 ATR    | sweep ก่อน reverse       |
        | MEAN_REVERT   | 1.5 ATR   | price × 0.3% | 0.7 ATR    | อาจยืดอีกก่อนกลับ        |

        IPA/IPAF ใช้ type_mult เดิม + sweep buffer
        """
        # Signal type config
        type_config = {
            'MOMENTUM':     {'mult': 1.0, 'sweep_pct': 0.002, 'liq_atr': 0.5},
            'ABSORPTION':   {'mult': 1.3, 'sweep_pct': 0.002, 'liq_atr': 0.5},
            'REVERSAL_OB':  {'mult': 1.3, 'sweep_pct': 0.003, 'liq_atr': 0.7},
            'REVERSAL_OS':  {'mult': 1.3, 'sweep_pct': 0.003, 'liq_atr': 0.7},
            'MEAN_REVERT':  {'mult': 2.0, 'sweep_pct': 0.004, 'liq_atr': 1.0},
        }
        cfg = type_config.get(signal_type, type_config['MOMENTUM'])

        # Mode multiplier
        mode_mult = self.iof_base_mult if mode == 'IOF' else self.ipa_base_mult
        session_mult = self.session_scale.get(session, 1.0)

        # 1. ATR buffer
        sl_distance = atr * mode_mult * cfg['mult'] * session_mult
        sl_distance = max(sl_distance, atr * self.min_atr)
        sl_distance = min(sl_distance, atr * self.max_atr)

        # 2. Sweep buffer
        sweep_buffer = max(entry * cfg['sweep_pct'], atr * 0.3)

        # Use anchor or entry
        if direction == 'LONG':
            anchor = anchor_price if anchor_price and anchor_price < entry else entry
            sl_atr = anchor - sl_distance - sweep_buffer

            # 3. Liquidity cluster
            if swing_lows and len(swing_lows) >= 2:
                recent_swings = swing_lows[-3:] if len(swing_lows) >= 3 else swing_lows
                cluster_low = min(recent_swings)
                sl_cluster = cluster_low - atr * cfg['liq_atr']
                sl = min(sl_atr, sl_cluster)
            else:
                sl = sl_atr

            return sl, f'SL_{signal_type}_{mode_mult}x{cfg["mult"]}x_sweep'
        else:  # SHORT
            anchor = anchor_price if anchor_price and anchor_price > entry else entry
            sl_atr = anchor + sl_distance + sweep_buffer

            if swing_highs and len(swing_highs) >= 2:
                recent_swings = swing_highs[-3:] if len(swing_highs) >= 3 else swing_highs
                cluster_high = max(recent_swings)
                sl_cluster = cluster_high + atr * cfg['liq_atr']
                sl = max(sl_atr, sl_cluster)
            else:
                sl = sl_atr

            return sl, f'SL_{signal_type}_{mode_mult}x{cfg["mult"]}x_sweep'

    def _find_smart_tp_with_be(self, entry: float, direction: str,
                              sl_distance: float,
                              atr: float,
                              session: str,
                              signal_type: str = 'MOMENTUM',
                              magnets: Optional[Dict[str, Any]] = None,
                              next_resistance: Optional[float] = None,
                              next_support: Optional[float] = None,
                              mode: str = 'IPA') -> Tuple[Optional[Tuple[float, str]], Optional[Tuple[float, str]]]:
        """
        v6.1: Find TP1 (BE trigger) and TP2 (actual TP) using magnet escalation.

        Logic:
        1. Collect all magnet candidates (buy/sell levels from magnets dict)
        2. Add resistance/support as candidates
        3. Add round numbers
        4. Add ATR projections as fallback
        5. Sort by distance (nearest first)
        6. TP1 = first candidate with RR >= tp1_rr_min (0.8)
        7. TP2 = next candidate with RR >= tp2_rr_min (1.2)

        Args:
            entry: Entry price
            direction: 'LONG' or 'SHORT'
            sl_distance: SL distance in price units
            atr: ATR value
            session: Trading session
            magnets: Dict with 'buy_magnets' and 'sell_magnets' lists
            next_resistance: Next major resistance
            next_support: Next major support
            mode: 'IPA' or 'IOF'

        Returns:
            Tuple[Tuple[float, str], Tuple[float, str]]: ((TP1 price, reason), (TP2 price, reason))
            or (None, None) if no valid TP found
        """
        candidates = []

        # v43.1: SKIP magnets and round numbers — use only ATR fallback
        # (All TP based on sl_distance × tp2_rr config)
        candidates = []
        
        # No candidates from magnets or round numbers
        # Will fall through to ATR fallback below

        # v43.1: No candidates - use ATR fallback only
        # TP = Entry ± (SL_Distance × tp2_rr)
        cfg = self.rr_config.get(signal_type, self.rr_config['MOMENTUM'])
        
        tp1_price = entry + (sl_distance * cfg['tp1_rr']) if direction == 'LONG' \
                   else entry - (sl_distance * cfg['tp1_rr'])
        tp1 = (tp1_price, f'SLDIST_x{cfg["tp1_rr"]}_TP1')

        tp2_price = entry + (sl_distance * cfg['tp2_rr']) if direction == 'LONG' \
                   else entry - (sl_distance * cfg['tp2_rr'])
        tp2 = (tp2_price, f'SLDIST_x{cfg["tp2_rr"]}_TP2')

        logger.debug(f"[SLTP-{mode}] Smart TP | TP1:{tp1[0]:.2f} ({tp1[1]}) | TP2:{tp2[0]:.2f} ({tp2[1]})")

        return tp1, tp2

    def _calc_fixed_sltp(self,
                         entry: float,
                         direction: str,
                         signal_type: str) -> SLTPRESult:
        """
        v55.0: Fixed-point SL/TP calculator based on MFE/MAE statistics.

        Uses SL_TP_CONFIG lookup table — no ATR dependency.
        TP1 = midpoint between entry and TP2 (50% of tp_pts) — acts as BE trigger.
        TP2 = full take profit target.

        Args:
            entry: Entry price
            direction: 'LONG' or 'SHORT'
            signal_type: Signal type key to look up in SL_TP_CONFIG

        Returns:
            SLTPRESult with fixed-point distances
        """
        cfg = SL_TP_CONFIG.get(signal_type, _SL_TP_DEFAULT)
        sl_pts = cfg['sl_pts']
        tp_pts = cfg['tp_pts']

        if direction == 'LONG':
            sl_price  = entry - sl_pts
            tp2_price = entry + tp_pts
            # v71.0 MOD-90: Dynamic BE - REVERSAL=100pts, MOMENTUM/ABSORPTION=120pts
            if signal_type in ('REVERSAL_OB', 'REVERSAL_OS'):
                tp1_price = entry + 100  # Fixed BE at 100 pts (MFE 110-140)
            else:
                tp1_price = entry + tp_pts * 0.6  # BE trigger at 60% of TP (≈120 pts)
        else:  # SHORT
            sl_price  = entry + sl_pts
            tp2_price = entry - tp_pts
            # v71.0 MOD-90: Dynamic BE - REVERSAL=100pts, MOMENTUM/ABSORPTION=120pts
            if signal_type in ('REVERSAL_OB', 'REVERSAL_OS'):
                tp1_price = entry - 100  # Fixed BE at 100 pts (MFE 110-140)
            else:
                tp1_price = entry - tp_pts * 0.6  # BE trigger at 60% of TP (≈120 pts)

        sl_distance = sl_pts
        actual_rr   = tp_pts / sl_pts if sl_pts > 0 else 0.0

        logger.debug(
            f"[SLTP-FIXED] {signal_type} {direction} | Entry:{entry} | "
            f"SL:{sl_price:.2f} ({sl_pts}pts) | TP1:{tp1_price:.2f} | "
            f"TP2:{tp2_price:.2f} ({tp_pts}pts) | RR:{actual_rr:.2f}"
        )

        return SLTPRESult(
            stop_loss=round(sl_price, 2),
            take_profit=round(tp2_price, 2),
            sl_distance=float(sl_distance),
            sl_pct=round(sl_distance / entry, 4) if entry > 0 else 0.0,
            actual_rr=round(actual_rr, 2),
            required_rr=round(actual_rr * 0.95, 2),
            tp1_level=round(tp1_price, 2),
            tp2_level=round(tp2_price, 2),
            sl_reason=f'FIXED_{sl_pts}pts_{signal_type}',
            tp1_reason=f'FIXED_TP1_{int(tp_pts * 0.5)}pts_BE',
            tp2_reason=f'FIXED_TP2_{tp_pts}pts_{signal_type}',
        )

    def _find_nearest_round_number(self, price: float, direction: str,
                                   min_distance: float,
                                   step: float = None) -> Optional[float]:
        """
        Find nearest round number that is at least min_distance away.
        """
        step = step or self.round_number_step

        if direction == 'LONG':
            # Round up to next round number
            next_round = np.ceil(price / step) * step
            if next_round - price >= min_distance:
                return float(next_round)
        else:
            # Round down to previous round number
            prev_round = np.floor(price / step) * step
            if price - prev_round >= min_distance:
                return float(prev_round)

        return None

    # ==============================================
    # v40.0: Unified calculate() method for SignalResult
    # ==============================================

    def calculate(self, signal) -> Optional[SLTPRESult]:
        """
        v40.0: Unified SL/TP calculation for any SignalResult.
        
        Accepts either a SignalResult (v40.0) or a dict (backward compat).
        Routes to calculate_iof() or calculate_ipa() based on signal_type.
        """
        # Support both SignalResult and dict
        if hasattr(signal, 'signal_type'):
            # SignalResult object
            signal_type = signal.signal_type
            entry_price = signal.entry_price
            direction = signal.direction
            atr_m5 = signal.atr_m5
            session = signal.session
            magnets = signal.extra.get('magnets')
            swing_highs = signal.extra.get('swing_highs')
            swing_lows = signal.extra.get('swing_lows')
            pdh = signal.extra.get('pdh')
            pdl = signal.extra.get('pdl')
            h1_fvg_boundary = signal.extra.get('h1_fvg_boundary')
            wall_price = signal.extra.get('wall_price', entry_price)
            ob_high = signal.extra.get('ob_high')
            ob_low = signal.extra.get('ob_low')
        else:
            # Dict (backward compat)
            signal_type = signal.get('signal_type', signal.get('mode', 'MOMENTUM'))
            entry_price = signal.get('entry_price', 0)
            direction = signal.get('direction', 'LONG')
            atr_m5 = signal.get('atr_m5', 0)
            session = signal.get('session', 'LONDON')
            magnets = signal.get('magnets')
            swing_highs = signal.get('swing_highs')
            swing_lows = signal.get('swing_lows')
            pdh = signal.get('pdh')
            pdl = signal.get('pdl')
            h1_fvg_boundary = signal.get('h1_fvg_boundary')
            wall_price = signal.get('wall_price', entry_price)
            ob_high = signal.get('ob_high')
            ob_low = signal.get('ob_low')

        # v55.0: All signal types use fixed-point SL/TP from SL_TP_CONFIG
        result = self._calc_fixed_sltp(
            entry=entry_price,
            direction=direction,
            signal_type=signal_type,
        )

        # v43.7: VP types — additionally adjust TP with VP structure if available
        if signal_type.startswith('VP_') and hasattr(signal, 'extra'):
            frvp_data = signal.extra.get('frvp_data', {})
            if frvp_data:
                adjusted = self._adjust_with_vp(
                    sl=result.stop_loss,
                    tp=result.take_profit,
                    direction=direction,
                    entry=entry_price,
                    frvp_data=frvp_data,
                    atr=atr_m5,
                    signal_type=signal_type,
                )
                if adjusted is not None:
                    result = adjusted

        return result

    def _adjust_with_vp(self, sl: float, tp: float, direction: str, entry: float,
                        frvp_data: dict, atr: float, signal_type: str) -> Optional[SLTPRESult]:
        """v43.7: Use VP levels as targets instead of ATR alone."""
        comp = frvp_data.get('composite', {})
        poc = comp.get('poc', 0) or 0.0
        vah = comp.get('vah', 0) or 0.0
        val = comp.get('val', 0) or 0.0
        swing = frvp_data.get('layers', {}).get('swing_anchored', {})
        hvn_list = swing.get('hvn', [])
        lvn_list = swing.get('lvn', [])

        new_tp = tp
        new_sl = sl

        # TP: Use VP level if closer than ATR-based
        if direction == 'LONG':
            vp_targets = sorted([l for l in [poc, vah] if l and l > entry])
            if vp_targets and abs(vp_targets[0] - entry) < abs(tp - entry):
                new_tp = vp_targets[0]
                logger.debug(f"[SLTP-VP] TP adjusted to VP level {new_tp:.0f} (was {tp:.0f})")
            lvn_above = sorted([l['price'] for l in lvn_list if l['price'] > entry])
            if lvn_above and lvn_above[0] < new_tp:
                new_tp = lvn_above[0]
        else:  # SHORT
            vp_targets = sorted([l for l in [poc, val] if l and l < entry], reverse=True)
            if vp_targets and abs(vp_targets[0] - entry) < abs(tp - entry):
                new_tp = vp_targets[0]
                logger.debug(f"[SLTP-VP] TP adjusted to VP level {new_tp:.0f} (was {tp:.0f})")
            lvn_below = sorted([l['price'] for l in lvn_list if l['price'] < entry], reverse=True)
            if lvn_below and lvn_below[0] > new_tp:
                new_tp = lvn_below[0]

        # SL: Use HVN as floor
        if direction == 'LONG' and hvn_list:
            hvn_below = [h['price'] for h in hvn_list if h['price'] < entry]
            if hvn_below:
                hvn_sl = max(hvn_below) - atr * 0.3
                new_sl = max(new_sl, hvn_sl)
        elif direction == 'SHORT' and hvn_list:
            hvn_above = [h['price'] for h in hvn_list if h['price'] > entry]
            if hvn_above:
                hvn_sl = min(hvn_above) + atr * 0.3
                new_sl = min(new_sl, hvn_sl)

        # Recalculate RR with adjusted levels
        sl_distance = abs(entry - new_sl)
        if sl_distance <= 0:
            return None
        actual_rr = abs(new_tp - entry) / sl_distance

        return SLTPRESult(
            stop_loss=round(new_sl, 2),
            take_profit=round(new_tp, 2),
            sl_distance=round(sl_distance, 2),
            sl_pct=round(sl_distance / entry * 100, 4) if entry > 0 else 0,
            actual_rr=round(actual_rr, 2),
            required_rr=round(actual_rr * 0.95, 2),
            tp1_level=round(entry + (new_tp - entry) * 0.5 if direction == 'LONG' else entry - (entry - new_tp) * 0.5, 2),
            tp2_level=round(new_tp, 2),
            sl_reason=f'VP-adjusted {signal_type}',
            tp1_reason='VP 50% midpoint',
            tp2_reason=f'VP level ({signal_type})',
        )
