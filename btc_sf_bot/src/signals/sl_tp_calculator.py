"""
Institutional SL/TP Calculator — v6.1

New SL/TP System:
  - SL: ATR × base_mult × session_scale (Session-adaptive ATR)
  - TP1: BE trigger (RR >= 0.8) — EA moves SL to breakeven when price reaches TP1
  - TP2: Actual take profit (RR >= 1.2) — where we close position

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

        # === SL Configuration (v6.1: Session-adaptive ATR) ===
        sl_config = self.config.get('sl', {})
        self.ipa_base_mult: float = sl_config.get('ipa_base_mult', 1.0)
        self.iof_base_mult: float = sl_config.get('iof_base_mult', 1.2)
        self.min_atr: float = sl_config.get('min_atr', 0.8)   # Safety minimum
        self.max_atr: float = sl_config.get('max_atr', 1.5)  # Safety maximum
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
        self.tp2_atr_fallback: float = tp_config.get('tp2_atr_fallback', 1.5)
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
        self.rr_config = {
            'MOMENTUM':    {'tp1_rr': 1.5, 'tp2_rr': 1.5},
            'ABSORPTION':  {'tp1_rr': 2.5, 'tp2_rr': 1.5},  # v26.0: 100% BE → BE ช้ามาก ให้วิ่งถึง TP
            'REVERSAL_OB': {'tp1_rr': 0.8, 'tp2_rr': 1.0},  # สวน = TP สั้น คงเดิม
            'REVERSAL_OS': {'tp1_rr': 0.8, 'tp2_rr': 1.0},
            'MEAN_REVERT': {'tp1_rr': 1.2, 'tp2_rr': 1.5},
            'IPA':         {'tp1_rr': 1.2, 'tp2_rr': 1.5},  # เดิม 1.0/1.5 → 1.2/1.5
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

            # === RR Check (v18.2: Dynamic RR based on Signal Type) ===
            cfg = self.rr_config.get('IPA')
            tp2_rr_min = cfg['tp2_rr']
            actual_rr = abs(tp2_price - entry_price) / sl_distance if sl_distance > 0 else 0

            if actual_rr < tp2_rr_min:
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

            if actual_rr < tp2_rr_min:
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
            'MEAN_REVERT':  {'mult': 1.5, 'sweep_pct': 0.003, 'liq_atr': 0.7},
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

        # Determine which magnet list to use
        # v17.8 FIX: TP for LONG = resistance (sell_magnets), TP for SHORT = support (buy_magnets)
        if direction == 'LONG':
            magnet_key = 'sell_magnets'  # TP for LONG = resistance above entry
        else:
            magnet_key = 'buy_magnets'   # TP for SHORT = support below entry

        # Add magnets from magnets dict
        if magnets and magnet_key in magnets:
            for m in magnets[magnet_key]:
                level = m.get('level', 0)
                if level > 0:
                    dist = abs(level - entry)
                    tier = m.get('tier', 9)
                    m_type = m.get('type', 'MAGNET')
                    candidates.append((level, dist, m_type, tier))

        # Add resistance/support as candidates
        ref = next_resistance if direction == 'LONG' else next_support
        if ref and ref > 0:
            dist = abs(ref - entry)
            candidates.append((ref, dist, f'{mode}_LEVEL', 3))

        # Add round numbers ($500 and $1000)
        for step in [500, 1000]:
            rn = self._find_nearest_round_number(entry, direction, sl_distance * 0.5, step)
            if rn:
                dist = abs(rn - entry)
                candidates.append((rn, dist, f'ROUND_{step}', 4))

        # v17.9: Filter candidates ที่อยู่ฝั่งถูก (LONG: p > entry, SHORT: p < entry)
        if direction == 'LONG':
            candidates = [(p, d, r, t) for p, d, r, t in candidates if p > entry]
        else:
            candidates = [(p, d, r, t) for p, d, r, t in candidates if p < entry]

        # Sort by distance (nearest first)
        candidates.sort(key=lambda x: x[1])

        # Find TP1 (BE trigger) and TP2 (actual TP)
        tp1 = None
        tp2 = None

        for price, dist, reason, tier in candidates:
            if dist <= 0:
                continue

            rr = dist / sl_distance if sl_distance > 0 else 0
            cfg = self.rr_config.get(signal_type, self.rr_config['MOMENTUM'])
            tp1_rr_target = cfg['tp1_rr']
            tp2_rr_target = cfg['tp2_rr']

            # TP1: first candidate with RR >= target
            if tp1 is None and rr >= tp1_rr_target:
                tp1 = (price, f'{reason}_TP1_RR{rr:.2f}')

            # TP2: next candidate after TP1 with RR >= target
            elif tp1 is not None and tp2 is None and rr >= tp2_rr_target:
                tp2 = (price, f'{reason}_TP2_RR{rr:.2f}')
                break

        # ATR fallback — v7.0 Bug#4 FIX
        # BUG FIX v11.x: TP1 and TP2 fallback must be OUTSIDE candidate loop
        # (Previously tp2 fallback was inside elif tp1 is not None block — never triggered!)
        # TP fallback uses sl_distance (not raw atr) to maintain RR when SL is clamped
        if tp1 is None:
            tp1_price = entry + (sl_distance * self.tp1_atr_fallback) if direction == 'LONG' \
                       else entry - (sl_distance * self.tp1_atr_fallback)
            tp1 = (tp1_price, f'SLDIST_x{self.tp1_atr_fallback}_TP1')

        if tp2 is None:
            tp2_price = entry + (sl_distance * self.tp2_atr_fallback) if direction == 'LONG' \
                       else entry - (sl_distance * self.tp2_atr_fallback)
            tp2 = (tp2_price, f'SLDIST_x{self.tp2_atr_fallback}_TP2')

        logger.debug(f"[SLTP-{mode}] Smart TP | TP1:{tp1[0]:.2f} ({tp1[1]}) | TP2:{tp2[0]:.2f} ({tp2[1]})")

        return tp1, tp2

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
