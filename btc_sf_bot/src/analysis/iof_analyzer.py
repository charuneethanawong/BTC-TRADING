"""
IOF Analyzer — Institutional Order Flow — v5.0 Aggressive Mode

Logic Flow (Section 4.2 of Architecture Plan):
  Gate 1: Market Regime (NOT extreme trending, ADX < 40)
  Gate 1: Delta Absorption (DER > 0.3, Volume > 1.0x average)
  Gate 3: OI Signal (SOFT GATE - OI change > 0.1%, direction opposite to price)
  Gate 4: Order Book Wall (size > session threshold, within 0.5% of price)
  Gate 5: M5 Rejection Candle (wick or close rejection at wall level)

Scoring (Section 4.3 — max 20 points):
  Delta Absorption Quality (max 7):
    +5  DER > 3.0 (Strong absorption)
    +4  DER 2.0–3.0 (Moderate)
    +3  DER 1.5–2.0 (Weak)
    +2  Volume Surge > 2.0x
    +1  Volume Surge 1.2–2.0x
  OI & Funding Signal (max 6):
    +3  OI Divergence > 0.3% (opposite to price)
    +2  OI Divergence 0.1–0.3%
    +2  Funding Rate Extreme (> |0.05%|) opposite to price
    +1  Funding Rate Moderate (0.02–0.05%)
  Wall Quality (max 5):
    +3  Wall > $1M + Refill confirmed
    +2  Wall $500K–$1M + Stable
    +1  Wall $300K–$500K + Stable (ASIA only)
    +1  Wall stability > 60 seconds
  Confirmation (max 2):
    +1  Liquidation cascade opposite direction
    +1  M5 Rejection candle at wall level

Score Threshold: >= 9 → Signal (v5.0 Aggressive)
"""
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Tuple
import pandas as pd
import numpy as np
from src.utils.logger import get_logger
from src.utils.decorators import log_errors, retry, circuit_breaker
from src.utils.metrics import timed_metric

logger = get_logger(__name__)


@dataclass
class IOFResult:
    """
    Result from IOF Analyzer.
    Returned when all 5 gates pass and score >= 11.
    """
    direction: str                   # 'LONG' or 'SHORT'
    score: int                       # 0-20
    wall_price: float               # Iceberg wall price level
    wall_size_usd: float            # Wall size in USD
    der_score: float                 # Delta Efficiency Ratio
    oi_change_pct: float             # OI change percentage
    funding_rate: float             # Funding rate
    volume_ratio: float             # Volume at signal / average volume
    rejection_candle: bool          # M5 rejection at wall
    liquidation_cascade: bool       # Liquidation cascade opposite direction
    wall_refill: bool               # Wall refilled after hit
    wall_stability_seconds: float    # How long wall held
    atr_m5: float
    rr_target: float                 # Session-adaptive RR target
    volume_spike: bool
    session: str = 'LONDON'
    score_breakdown: Dict[str, Any] = field(default_factory=dict)
    next_resistance: Optional[float] = None
    next_support: Optional[float] = None
    signal_type: str = 'MOMENTUM'   # 'MOMENTUM' or 'ABSORPTION' (v9.6)
    reversal_mode: str = 'STRUCTURAL'  # v15.9: 'STRUCTURAL', 'STRONG_EXHAUST', 'EXHAUSTED', 'MODERATE'
    custom_magnets: Optional[Dict] = None  # v15.9: Custom magnets (e.g., EMA20_H1_REVERT)

    def __str__(self) -> str:
        return (f"IOF {self.direction} | Score: {self.score}/20 | "
                f"Wall: ${self.wall_size_usd/1000:.0f}K @ {self.wall_price} | "
                f"DER: {self.der_score:.1f} | Session: {self.session}")


class IOFAnalyzer:
    """
    Institutional Order Flow Analyzer.

    Detects high-probability IOF setups using real-time Binance data:
      - Delta Absorption (DER) shows institutional participation
      - OI Divergence confirms directional loading
      - Iceberg Wall provides entry zone and SL reference
      - Rejection Candle confirms market reaction
    """

    def __init__(self, config: dict = None, logger=None, log_prefix="[IOF]"):
        self.config = config or {}
        self.logger = logger if logger else get_logger(__name__)
        self.log_prefix = log_prefix

        # v24.0: IOFF sets True → OB/OS ไม่ถูก override เป็น MOMENTUM
        self._keep_reversal_on_momentum = False

        # === DER Settings (Section 4.2 Gate 1) ===
        # DER = |directional volume| / total volume (0-1 scale)
        # > 0.3 = 30%+ directional = institutional absorption
        # (Threshold adjusted from broken formula: was 1.5/2.0/3.0 which was impossible on 0-1 scale)
        self.der_min: float = self.config.get('der_min', 0.3)
        self.der_strong: float = self.config.get('der_strong', 0.6)
        self.der_moderate: float = self.config.get('der_moderate', 0.45)
        self.volume_absorb_min: float = self.config.get('volume_absorb_min', 1.0)

        # === OI Settings (Section 4.2 Gate 3) ===
        self.oi_change_min_pct: float = self.config.get('oi_change_min_pct', 0.001)   # 0.1%
        self.oi_divergence_strong: float = self.config.get('oi_divergence_strong', 0.003)  # 0.3%

        # === Wall Settings (Section 4.2 Gate 4 - v8.1) ===
        # v8.1: Updated per architecture plan
        self.wall_threshold_asia: float = self.config.get('wall_threshold_asia', 100000)
        self.wall_threshold_london: float = self.config.get('wall_threshold_london', 200000)
        self.wall_threshold_ny: float = self.config.get('wall_threshold_ny', 300000)
        self.wall_max_distance_pct: float = self.config.get('wall_max_distance_pct', 0.005)
        # v8.1 A1: Anti-spoofing — wall must be stable at least 15s (v16.9: reduced from 45s)
        self.min_wall_stability: float = self.config.get('min_wall_stability', 15.0)
        # v8.1 A2: Dynamic threshold relative to avg volume
        # v13.3: Reduced from 0.005 to 0.0005 (weekday $979K→$98K threshold)
        self.wall_relative_size_mult: float = self.config.get('wall_relative_size_multiplier', 0.0005)
        self.absolute_min_wall_usd: float = self.config.get('absolute_min_wall_usd', 50000.0)
        # Cache for avg volume (computed in Gate 1, used in Gate 4)
        self._avg_vol_btc: float = 0.0

        # === Funding Settings ===
        self.funding_extreme: float = self.config.get('funding_extreme', 0.0005)  # 0.05%

        # === Score Threshold ===
        # v6.0: Reduced from 9 to 6 to allow signals when OI/OB data unavailable
        # (soft gates mean max possible score is reduced without data)
        self.score_threshold: int = self.config.get('score_threshold', 6)

        # === RR Targets by Session ===
        self.rr_target_by_session = {
            'ASIA': 1.8,
            'ASIA-LATE': 1.8,
            'LONDON': 2.0,
            'LONDON-NY': 2.0,
            'NY': 2.0,
        }

    @log_errors
    @timed_metric("IOFAnalyzer.analyze")
    @retry(max_attempts=3, delay=0.1, backoff=2.0, exceptions=(Exception,))
    @circuit_breaker(failure_threshold=5, timeout=30.0, expected_exception=Exception)
    def analyze(self,
                candles_m5: pd.DataFrame,
                binance_data: Dict[str, Any],
                current_price: float,
                session: str = 'LONDON',
                # v27.0: Single Source of Truth parameters
                atr_m5: Optional[float] = None,
                h1_bias_result: Optional[Any] = None,
                  snapshot: Optional[Any] = None) -> Optional[IOFResult]:
        """
        Main entry point: analyze for IOF signal.

        Args:
            candles_m5: M5 OHLCV DataFrame
            binance_data: Dict with keys: oi, oi_1min_ago, funding_rate, 
                          order_book, recent_trades, liquidations
            current_price: Current BTC price
            session: Trading session name
            atr_m5: v27.0 - Optional ATR from MarketSnapshot (single source of truth)
            h1_bias_result: v27.0 - Optional H1BiasResult from H1BiasEngine

        Returns:
            IOFResult if all gates pass and score >= 11, else None
        """
        try:
            self.session = session
            self.current_price = current_price

            # Ensure enough data
            if len(candles_m5) < 10:
                self.logger.info(f"{self.log_prefix} Insufficient candles: {len(candles_m5)}")
                return None

            # v27.0: Set ATR from snapshot or calculate
            if atr_m5 is not None:
                self.atr_m5 = atr_m5

            # === v14.1: Gate 1a — Scan walls both sides FIRST ===
            wall_scan = self._scan_walls_both_sides(binance_data, current_price, candles_m5)

            # === v14.3: Gate 1b — DER + Direction (with DER Bypass) ===
            # Pass wall_scan for DER Bypass logic
            delta_result = self._check_delta_absorption(candles_m5, binance_data, wall_scan)
            if delta_result is None:
                return None  # Log already in _check_delta_absorption

            raw_direction = delta_result['direction']
            signal_type = delta_result['signal_type']  # v14.3: MOMENTUM/ABSORPTION/REVERSAL_OB/REVERSAL_OS/MEAN_REVERT

            # === v29.1: M5 State Gate — SIDEWAY/CAUTION/Range Position Filter ===
            m5_state = binance_data.get('m5_state', 'RANGING')
            if m5_state == 'SIDEWAY':
                self.logger.info(f"{self.log_prefix} M5 State Gate: {signal_type} blocked in SIDEWAY")
                return None

            # C5: CAUTION state — MOMENTUM gets penalty -2 (must have strong DER+Wall to pass)
            if m5_state == 'CAUTION' and signal_type == 'MOMENTUM':
                self._caution_penalty = -2
                self.logger.info(f"{self.log_prefix} M5 State Gate: CAUTION — MOMENTUM penalty -2")
            else:
                self._caution_penalty = 0

            # C1: Range Position Filter — block MOMENTUM at range extremes after SIDEWAY exit
            m5_range_high = binance_data.get('m5_range_high', 0)
            m5_range_low = binance_data.get('m5_range_low', 0)
            if signal_type == 'MOMENTUM' and m5_range_high > 0 and m5_range_low > 0:
                range_size = m5_range_high - m5_range_low
                if range_size > 0:
                    current_price = binance_data.get('current_price', 0) or binance_data.get('price', 0)
                    range_pct = (current_price - m5_range_low) / range_size if current_price else 0.5
                    if raw_direction == 'LONG' and range_pct > 0.80:
                        self.logger.info(
                            f"{self.log_prefix} M5 State Gate: MOMENTUM LONG blocked — "
                            f"price at {range_pct:.0%} of range ({m5_range_low:.0f}-{m5_range_high:.0f})"
                        )
                        return None
                    if raw_direction == 'SHORT' and range_pct < 0.20:
                        self.logger.info(
                            f"{self.log_prefix} M5 State Gate: MOMENTUM SHORT blocked — "
                            f"price at {range_pct:.0%} of range ({m5_range_low:.0f}-{m5_range_high:.0f})"
                        )
                        return None

            # === v14.1: Gate 1c — DER + Wall → final direction ===
            direction, wall_adjust, resolve_reason = self._resolve_direction(
                raw_direction, signal_type, wall_scan
            )
            self.direction = direction  # Store for later methods
            self._wall_adjust = wall_adjust  # +1 aligned, 0 override, -2 conflict

            # v17.0: Add DER value to log
            current_der = getattr(self, '_current_der', 0)
            self.logger.info(
                f"{self.log_prefix} Gate 1c: {resolve_reason} | "
                f"DER:{current_der:.3f}({raw_direction}) Wall:{wall_scan['dominant'] if wall_scan else 'NONE'} → {direction} (adj:{wall_adjust:+d})"
            )

            # === v14.8: Gate 1d — H1 Distance Gate + Reversal Confirmation ===
            if signal_type in ('REVERSAL_OB', 'REVERSAL_OS', 'MEAN_REVERT'):
                h1_dist = binance_data.get('h1_ema_dist_pct', 0)
                use_exhaustion = getattr(self, '_use_exhaustion_quality', False)
                
                # v15.9: IOFF mode uses Exhaustion Quality instead of dist blocking
                if use_exhaustion and signal_type in ('REVERSAL_OB', 'REVERSAL_OS'):
                    # === IOFF mode: Exhaustion Quality (ไม่ block ที่ dist) ===
                    exhaustion = self._check_exhaustion_quality(candles_m5, direction)
                    
                    if exhaustion >= 5:
                        min_rev = 4
                        self._reversal_mode = 'STRONG_EXHAUST'
                    elif exhaustion >= 4:
                        min_rev = 5
                        self._reversal_mode = 'EXHAUSTED'
                    elif exhaustion >= 3:
                        min_rev = 7
                        self._reversal_mode = 'MODERATE'
                    else:
                        self.logger.info(
                            f"{self.log_prefix} Gate 1d: BLOCKED | exhaustion {exhaustion}/6 < 3"
                        )
                        return None
                    
                    # H1 dist สูง upgrade เป็น STRUCTURAL
                    if h1_dist >= 1.5:
                        self._reversal_mode = 'STRUCTURAL'
                        min_rev = min(min_rev, 5)
                    
                    # v17.0: Add DER to log
                    self.logger.info(
                        f"{self.log_prefix} Gate 1d: {self._reversal_mode} | "
                        f"DER:{current_der:.3f} exhaustion:{exhaustion}/6 H1:{h1_dist:.1f}% → rev≥{min_rev}"
                    )

                elif signal_type in ('REVERSAL_OB', 'REVERSAL_OS'):
                    # === IOF mode: H1 dist blocking (เดิม) ===
                    if h1_dist < 1.2:
                        self.logger.info(
                            f"{self.log_prefix} Gate 1d: BLOCKED | DER:{current_der:.3f} H1 dist {h1_dist:.1f}% < 1.2%"
                        )
                        return None
                    
                    if h1_dist >= 2.0:
                        min_rev = 5
                    elif h1_dist >= 1.5:
                        min_rev = 6
                    else:
                        min_rev = 7
                    self._reversal_mode = 'STRUCTURAL'
                
                elif signal_type == 'MEAN_REVERT':
                    # MEAN_REVERT: ยังใช้ dist blocking เหมือนเดิม (ทั้ง IOF และ IOFF)
                    if h1_dist < 1.2:
                        self.logger.info(
                            f"{self.log_prefix} Gate 1d: BLOCKED | MEAN_REVERT H1 dist {h1_dist:.1f}% < 1.2%"
                        )
                        return None
                    
                    if h1_dist >= 2.0:
                        min_rev = 5
                    elif h1_dist >= 1.5:
                        min_rev = 6
                    else:
                        min_rev = 7
                    self._reversal_mode = 'STRUCTURAL'
                
                # เรียก rev_score จริง
                rev_score = self._check_reversal_confirmation(candles_m5, binance_data, direction)
                
                if rev_score < min_rev:
                    mode_info = f" mode:{self._reversal_mode}" if hasattr(self, '_reversal_mode') else ""
                    self.logger.info(
                        f"{self.log_prefix} Gate 1d: FAILED | rev:{rev_score} < {min_rev}{mode_info}"
                    )
                    return None
                
                # v17.0: Add DER to PASSED log
                self.logger.info(
                    f"{self.log_prefix} Gate 1d: PASSED | {signal_type} DER:{current_der:.3f} rev:{rev_score}/{min_rev} | H1:{h1_dist:.1f}%"
                )

            # === Gate 3: OI Signal (v6.0 SOFT GATE - missing data doesn't block) ===
            oi_result = self._check_oi_signal(binance_data, direction)

            # === v14.1: Gate 4 — Use wall from scan (don't re-find) ===
            # Select wall according to final direction
            if wall_scan and wall_scan['dominant'] != 'NONE':
                if direction == 'LONG':
                    chosen_wall = wall_scan['bid_wall']
                else:
                    chosen_wall = wall_scan['ask_wall']
            else:
                chosen_wall = None

            # Validate wall (threshold, stability, bounce check)
            wall_result = self._validate_wall(chosen_wall, binance_data, direction, candles_m5)
            self._wall_result = wall_result  # v26.0: store for counter-trend check

            # v17.2: Handle wall_unstable_penalty (-1) from _validate_wall
            wall_unstable = wall_result is not None and wall_result.get('wall_unstable_penalty') == -1
            if wall_unstable:
                use_soft_wall_momentum = getattr(self, '_use_soft_wall_momentum', False)
                if signal_type == 'MOMENTUM' and use_soft_wall_momentum:
                    # IOFF: unstable wall = penalty -1
                    self.logger.info(
                        f"{self.log_prefix} Gate 4: SOFT (IOFF MOMENTUM) | Wall unstable → penalty -1"
                    )
                    self._wall_missing_penalty = -1
                elif signal_type == 'MOMENTUM' and not use_soft_wall_momentum:
                    # IOF: unstable wall = BLOCKED (need stable wall)
                    self.logger.info(
                        f"{self.log_prefix} Gate 4: HARD (IOF MOMENTUM) | Wall unstable → blocked"
                    )
                    return None
                # REVERSAL/MEAN_REVERT/ABSORPTION: soft, allow with penalty -1

            if wall_result is None:
                # === v16.9: Dynamic HARD/SOFT Gate ===
                # Check if IOFF mode (soft wall for MOMENTUM)
                use_soft_wall_momentum = getattr(self, '_use_soft_wall_momentum', False)
                
                # REVERSAL / MEAN_REVERT: wall = SOFT (มี H1 dist + rev_score confirm แล้ว)
                if signal_type in ('REVERSAL_OB', 'REVERSAL_OS', 'MEAN_REVERT'):
                    self.logger.info(f"{self.log_prefix} Gate 4: SOFT ({signal_type}) | No valid wall — continuing")
                    wall_result = {
                        'wall_price': current_price,
                        'wall_size_usd': 0,
                        'stability_seconds': 0,
                        'refill_confirmed': False,
                        'wall_break': False,
                    }

                # MOMENTUM: IOF = HARD, IOFF = SOFT (penalty -3)
                elif signal_type == 'MOMENTUM':
                    if use_soft_wall_momentum:
                        # IOFF: SOFT wall — ผ่านได้แต่ penalty -3
                        self.logger.info(
                            f"{self.log_prefix} Gate 4: SOFT (IOFF MOMENTUM) | No wall → penalty -3"
                        )
                        wall_result = {
                            'wall_price': current_price,
                            'wall_size_usd': 0,
                            'stability_seconds': 0,
                            'refill_confirmed': False,
                            'wall_break': False,
                        }
                        self._wall_missing_penalty = -3  # for scoring
                    else:
                        # IOF: HARD — ต้องมี wall
                        self.logger.info(
                            f"{self.log_prefix} Gate 4: HARD (IOF MOMENTUM) | No valid wall → blocked"
                        )
                        return None
                
                # MOMENTUM DER ≥ 0.5: wall = SOFT (DER แรงพอเป็น signal เอง)
                elif signal_type == 'MOMENTUM' and delta_result['der'] >= 0.5:
                    self.logger.info(
                        f"{self.log_prefix} Gate 4: SOFT (STRONG DER {delta_result['der']:.3f}) | "
                        f"No valid wall — DER strong enough"
                    )
                    wall_result = {
                        'wall_price': current_price,
                        'wall_size_usd': 0,
                        'stability_seconds': 0,
                        'refill_confirmed': False,
                        'wall_break': False,
                    }

                # ABSORPTION: wall = HARD
                else:
                    self.logger.info(
                        f"{self.log_prefix} Gate 4: HARD | No valid wall "
                        f"({signal_type}) → blocked"
                    )
                    return None

            # === Gate 5: M5 Rejection ===
            rejection = self._check_m5_rejection(candles_m5, wall_result['wall_price'], direction)

            # === v25.0: Entry Quality Score for MOMENTUM/ABSORPTION ===
            iof_eqs = 0
            if signal_type in ('MOMENTUM', 'ABSORPTION'):
                iof_eqs = self._check_iof_entry_quality(candles_m5, direction)
                self._iof_eqs = iof_eqs  # v26.0: store for counter-trend check
                if iof_eqs <= -2:
                    self.logger.info(
                        f"{self.log_prefix} EQS: BLOCKED | {signal_type} EQS={iof_eqs} (impulse entry)"
                    )
                    return None

            # === Calculate Score ===
            score, breakdown = self._calculate_score(
                delta_result=delta_result,
                oi_result=oi_result,
                wall_result=wall_result,
                rejection=rejection,
                binance_data=binance_data
            )
            # v25.0: Apply EQS to score
            if iof_eqs != 0:
                score += iof_eqs
                breakdown['entry_quality'] = iof_eqs

            # v26.0: Score threshold แยกตาม signal type
            # REVERSAL_OS: 0W 2L → threshold 10 (ต้องมี confirmation สูงมาก)
            if signal_type == 'REVERSAL_OS':
                effective_threshold = max(self.score_threshold, 10)
            elif signal_type in ('MEAN_REVERT', 'REVERSAL_OB'):
                effective_threshold = max(self.score_threshold, 8)
            else:
                effective_threshold = self.score_threshold  # ปกติ 6

            if score < effective_threshold:
                self.logger.info(
                    f"{self.log_prefix} Score {score} < {effective_threshold} "
                    f"({signal_type}) | breakdown: {breakdown}"
                )
                return None

            # === Get next major levels ===
            levels = self._get_major_levels(
                candles_m5, direction,
                wall_result['wall_price'],
                entry_price=wall_result['wall_price']  # IOF: entry = wall price
            )

            # === RR Target ===
            rr_target = self.rr_target_by_session.get(session, 2.0)

            result = IOFResult(
                direction=direction,
                score=score,
                wall_price=wall_result['wall_price'],
                wall_size_usd=wall_result['wall_size_usd'],
                der_score=delta_result['der'],
                oi_change_pct=oi_result['oi_change_pct'],
                funding_rate=binance_data.get('funding_rate', 0.0),
                volume_ratio=delta_result['volume_ratio'],
                rejection_candle=rejection,
                liquidation_cascade=binance_data.get('liquidation_cascade', False),
                wall_refill=wall_result.get('refill_confirmed', False),
                wall_stability_seconds=wall_result.get('stability_seconds', 0),
                atr_m5=self.atr_m5,
                rr_target=rr_target,
                volume_spike=delta_result['volume_ratio'] >= 1.2,
                session=session,
                score_breakdown=breakdown,
                next_resistance=levels.get('resistance'),
                next_support=levels.get('support'),
                signal_type=delta_result['signal_type'],  # v9.6: MOMENTUM or ABSORPTION
                reversal_mode=getattr(self, '_reversal_mode', 'STRUCTURAL'),  # v15.9
                custom_magnets=None,  # v15.9: Standard IOF doesn't use custom magnets
            )

            self.logger.debug(f"{self.log_prefix} Analysis result: {result}")
            return result

        except Exception as e:
            self.logger.error(f"{self.log_prefix} Analysis error: {e}", exc_info=True)
            return None

    def _check_delta_absorption(self, candles_m5: pd.DataFrame, binance_data: Dict,
                                wall_scan: Optional[Dict] = None) -> Optional[Dict]:
        """
        Gate 1: Signal Classification — v15.1

        ลำดับ: เฉพาะสุด → กว้างสุด
          1. ABSORPTION (DER ≥ 0.6 + flat — ไม่ overlap กับอื่น)
          2. REVERSAL (M5 OB/OS — ต้องเช็คก่อน MOMENTUM)
          3. MEAN_REVERT (H1 dist + wall — ต้องเช็คก่อน MOMENTUM)
          4. MOMENTUM (catch-all DER ≥ 0.3)
          5. FAIL
        """
        if len(candles_m5) < 4:
            return None

        # Calculate ATR and current price
        atr = self._calc_atr(candles_m5, 14)
        current_close = float(candles_m5.iloc[-1]['close'])

        # Effective ATR Clamping
        effective_atr = max(atr, current_close * 0.0005)
        self.atr_m5 = effective_atr

        # === Delta Calculation ===
        recent = candles_m5.iloc[-5:]
        deltas = []
        for _, row in recent.iterrows():
            if row['close'] > row['open']:
                delta = row['volume']
            elif row['close'] < row['open']:
                delta = -row['volume']
            else:
                delta = 0
            deltas.append(delta)

        cumulative_delta = sum(deltas)
        total_volume = sum(abs(d) for d in deltas)
        avg_vol = candles_m5['volume'].iloc[-20:].mean()

        # DER = |cumulative_delta| / total_volume
        der = abs(cumulative_delta) / total_volume if total_volume > 0 else 0.0
        self._current_der = der
        raw_direction = 'LONG' if cumulative_delta > 0 else 'SHORT'

        # H1 distance + bias
        h1_dist = binance_data.get('h1_ema_dist_pct', 0)
        h1_bias = binance_data.get('h1_bias', 'NEUTRAL')

        # EMA M5 distance (สำหรับ OB/OS detection)
        ema20_m5 = candles_m5['close'].ewm(span=20).mean().iloc[-1]
        ema_distance = (current_close - ema20_m5) / effective_atr
        volatility_factor = effective_atr / (current_close * 0.001)
        max_ema_dist = 1.0 * max(0.8, min(volatility_factor, 1.5))

        m5_overbought = ema_distance > max_ema_dist
        m5_oversold = ema_distance < -max_ema_dist

        # Price movement (ATR units)
        price_move_signed = float(recent.iloc[-1]['close'] - recent.iloc[0]['open'])
        price_move = abs(price_move_signed)
        price_move_atr = price_move / effective_atr

        # v17.1: Momentum Strength
        der_aligned = (raw_direction == 'LONG' and price_move_signed > 0) or \
                      (raw_direction == 'SHORT' and price_move_signed < 0)
        momentum_strength = der * price_move_atr if der_aligned else 0.0

        # v25.0: Pre-check MEAN_REVERT conditions (wall + H1 dist)
        # ถ้า wall strong + H1 dist สูง → MEAN_REVERT มีสิทธิ์ก่อน REVERSAL
        # v26.0: MEAN_REVERT — ลด threshold ให้เหมาะกับ BTC (เดิมยากเกินไม่เคย trigger)
        is_mean_revert_candidate = False
        if wall_scan and wall_scan.get('raw_ratio', 0) >= 2.0:
            mr_wall_ratio = wall_scan['raw_ratio']
            mr_min_dist = 0.8 if mr_wall_ratio >= 10 else 1.0 if mr_wall_ratio >= 5 else 1.2
            if h1_dist >= mr_min_dist:
                mr_wall_dir = 'LONG' if wall_scan['raw_dominant'] == 'BID' else 'SHORT'
                is_mean_revert_candidate = (
                    (mr_wall_dir == 'LONG' and h1_bias == 'BEARISH') or
                    (mr_wall_dir == 'SHORT' and h1_bias == 'BULLISH')
                )

        # ==============================================
        # 1. ABSORPTION (เฉพาะสุด — เช็คก่อน)
        #    DER ≥ 0.6 + price flat < 0.5 ATR
        # ==============================================
        if der >= self.der_strong and price_move_atr < 0.5:
            direction = 'SHORT' if cumulative_delta > 0 else 'LONG'
            signal_type = 'ABSORPTION'
            self.logger.info(
                f"{self.log_prefix} Gate 1b: ABSORPTION | DER:{der:.3f} "
                f"price_move:{price_move_atr:.1f}ATR → {direction}"
            )

        # ==============================================
        # 2. REVERSAL (M5 OB/OS — DER เท่าไหร่ก็ได้)
        # v17.1: Momentum Strength (DER * PriceMove/ATR) ≥ 0.5 → MOMENTUM (safety filter)
        # ==============================================
        # v25.0: MEAN_REVERT takes priority over REVERSAL when wall+dist qualify
        elif is_mean_revert_candidate and (m5_overbought or m5_oversold):
            direction = mr_wall_dir
            signal_type = 'MEAN_REVERT'
            self.logger.info(
                f"{self.log_prefix} Gate 1b: MEAN_REVERT | "
                f"H1:{h1_dist:.1f}% (min:{mr_min_dist}%) "
                f"Wall:{wall_scan['raw_dominant']} ratio:{mr_wall_ratio:.1f}x "
                f"M5:{'OB' if m5_overbought else 'OS'} DER:{der:.3f} → {direction}"
            )

        elif m5_overbought:
            if momentum_strength >= 0.5 and raw_direction == 'LONG' and not self._keep_reversal_on_momentum:
                # IOF: Momentum strong LONG + M5 OB = momentum still strong → don't reverse
                if der >= self.der_min:
                    direction = 'LONG'
                    signal_type = 'MOMENTUM'
                    self.logger.info(
                        f"{self.log_prefix} Gate 1b: MOMENTUM (OB but Momentum Strength {momentum_strength:.2f} ≥ 0.5) → LONG"
                    )
                else:
                    return None
            else:
                # Momentum weak or opposite → reversal
                # v24.0: IOFF always reaches here (keep_reversal=True) → REVERSAL ทำงานอิสระ
                direction = 'SHORT'
                signal_type = 'REVERSAL_OB'
                self.logger.info(
                    f"{self.log_prefix} Gate 1b: REVERSAL_OB | M5 OB Momentum:{momentum_strength:.2f} DER:{der:.3f}({raw_direction}) → SHORT"
                )

        elif m5_oversold:
            if momentum_strength >= 0.5 and raw_direction == 'SHORT' and not self._keep_reversal_on_momentum:
                # IOF: Momentum strong SHORT + M5 OS = momentum still strong → don't reverse
                if der >= self.der_min:
                    direction = 'SHORT'
                    signal_type = 'MOMENTUM'
                    self.logger.info(
                        f"{self.log_prefix} Gate 1b: MOMENTUM (OS but Momentum Strength {momentum_strength:.2f} ≥ 0.5) → SHORT"
                    )
                else:
                    return None
            else:
                # v24.0: IOFF always reaches here (keep_reversal=True) → REVERSAL ทำงานอิสระ
                direction = 'LONG'
                signal_type = 'REVERSAL_OS'
                self.logger.info(
                    f"{self.log_prefix} Gate 1b: REVERSAL_OS | M5 OS Momentum:{momentum_strength:.2f} DER:{der:.3f}({raw_direction}) → LONG"
                )

        # ==============================================
        # 3. MEAN_REVERT (H1 dist + wall — DER เท่าไหร่ก็ได้)
        # ==============================================
        elif (wall_scan and wall_scan['raw_ratio'] >= 2.0):
            wall_ratio = wall_scan['raw_ratio']
            min_dist = 0.8 if wall_ratio >= 10 else 1.0 if wall_ratio >= 5 else 1.2

            if h1_dist >= min_dist:
                wall_dir = 'LONG' if wall_scan['raw_dominant'] == 'BID' else 'SHORT'
                is_revert = (
                    (wall_dir == 'LONG' and h1_bias == 'BEARISH') or
                    (wall_dir == 'SHORT' and h1_bias == 'BULLISH')
                )
                if is_revert:
                    direction = wall_dir
                    signal_type = 'MEAN_REVERT'
                    self.logger.info(
                        f"{self.log_prefix} Gate 1b: MEAN_REVERT | "
                        f"H1:{h1_dist:.1f}% (min:{min_dist}%) "
                        f"Wall:{wall_scan['raw_dominant']} ratio:{wall_ratio:.1f}x "
                        f"DER:{der:.3f} → {direction}"
                    )
                else:
                    # wall ตาม trend → fallback MOMENTUM (ถ้า DER ≥ 0.3)
                    if der >= self.der_min:
                        direction = raw_direction
                        signal_type = 'MOMENTUM'
                        if direction == 'LONG' and ema_distance > max_ema_dist:
                            return None
                        elif direction == 'SHORT' and ema_distance < -max_ema_dist:
                            return None
                        self.logger.info(
                            f"{self.log_prefix} Gate 1b: MOMENTUM (fallback) | "
                            f"wall same trend → DER:{der:.3f} → {direction}"
                        )
                    else:
                        self.logger.info(f"{self.log_prefix} Gate 1: FAILED | wall same trend + DER:{der:.3f}")
                        return None
            else:
                # dist ไม่ถึง min → fallback MOMENTUM (ถ้า DER ≥ 0.3)
                if der >= self.der_min:
                    direction = raw_direction
                    signal_type = 'MOMENTUM'
                    if direction == 'LONG' and ema_distance > max_ema_dist:
                        return None
                    elif direction == 'SHORT' and ema_distance < -max_ema_dist:
                        return None
                    self.logger.info(f"{self.log_prefix} Gate 1b: MOMENTUM (fallback) | DER:{der:.3f} → {direction}")
                else:
                    self.logger.info(
                        f"{self.log_prefix} Gate 1: FAILED | "
                        f"H1:{h1_dist:.1f}%<{min_dist}% + DER:{der:.3f}<{self.der_min}"
                    )
                    return None

        # ==============================================
        # 4. MOMENTUM (catch-all — DER ≥ 0.3)
        # ==============================================
        elif der >= self.der_min:
            direction = raw_direction
            signal_type = 'MOMENTUM'
            # EMA exhaustion check
            if direction == 'LONG' and ema_distance > max_ema_dist:
                self.logger.info(f"{self.log_prefix} Gate 1: FAILED | MOMENTUM OB EMA:{ema_distance:.1f}ATR")
                return None
            elif direction == 'SHORT' and ema_distance < -max_ema_dist:
                self.logger.info(f"{self.log_prefix} Gate 1: FAILED | MOMENTUM OS EMA:{ema_distance:.1f}ATR")
                return None
            self.logger.info(f"{self.log_prefix} Gate 1b: MOMENTUM | DER:{der:.3f} → {direction}")

        # ==============================================
        # 5. FAIL
        # ==============================================
        else:
            reasons = [f"DER:{der:.3f}<{self.der_min}"]
            if not (m5_overbought or m5_oversold): reasons.append("no OB/OS")
            self.logger.info(f"{self.log_prefix} Gate 1: FAILED | {' | '.join(reasons)}")
            return None

        # === Volume Ratio ===
        volume_ratio = total_volume / (avg_vol * 5) if avg_vol > 0 else 1.0
        volumes = candles_m5['volume'].iloc[-20:].values
        median_vol = float(np.median(volumes)) if len(volumes) > 0 else avg_vol
        self._avg_vol_btc = median_vol

        # Pullback Integration
        pullback = binance_data.get('pullback', {'status': 'NONE'})
        pb_status = pullback.get('status', 'NONE')
        pb_quality = pullback.get('quality', {})
        
        # Block False Pullback entries against trend
        if pb_status == 'ACTIVE' and not pb_quality.get('is_true', False):
            h1_bias = binance_data.get('h1_bias', 'NEUTRAL')
            # If LONG during bearish false pullback OR SHORT during bullish false pullback -> Block
            if (direction == 'LONG' and h1_bias == 'BEARISH') or (direction == 'SHORT' and h1_bias == 'BULLISH'):
                self.logger.info(f"{self.log_prefix} Gate 1: FAILED | False Pullback Trap (dir:{direction} vs bias:{h1_bias})")
                return None
                
        # Limit TP for True Pullback counter-trend entries (handled in TP calc, but we can log it here)
        tp_note = ""
        if pb_status == 'ACTIVE' and pb_quality.get('is_true', False):
            h1_bias = binance_data.get('h1_bias', 'NEUTRAL')
            if (direction == 'LONG' and h1_bias == 'BEARISH') or (direction == 'SHORT' and h1_bias == 'BULLISH'):
                tp_note = " | TP Limited (Counter-trend PB)"
                
        self.logger.info(f"{self.log_prefix} Gate 1: PASSED | {signal_type} | DER:{der:.3f} | dir:{direction} | EMA:{ema_distance:.1f}ATR{tp_note}")

        return {
            'der': der,
            'delta': cumulative_delta,
            'direction': direction,
            'signal_type': signal_type,
            'ema_distance': round(ema_distance, 2),
            'volume_ratio': round(volume_ratio, 2),
            'price_move_atr': round(price_move_atr, 2),
            'wall_price': None,
            'signal_id': None,
        }

    def _check_oi_signal(self, binance_data: Dict[str, Any],
                        direction: str) -> Optional[Dict]:
        """
        Gate 3 (v6.0): OI Signal - SOFT GATE (Architecture Plan Section 2.5)
        
        OI increase + price decrease = SHORT loading (LONG signal)
        OI increase + price increase = LONG loading (SHORT signal)
        
        v6.0: Returns dict with 'skipped': True when data unavailable,
        instead of returning None (which blocks the entire signal).
        """
        oi = binance_data.get('oi', 0)
        oi_1min = binance_data.get('oi_1min_ago', oi)
        price = binance_data.get('current_price', getattr(self, 'current_price', 70000.0))
        price_1min = binance_data.get('price_1min_ago', price)

        # v6.0: Soft Gate - don't block when data unavailable
        if oi <= 0 or oi_1min <= 0:
            self.logger.info(f"{self.log_prefix} Gate 3: SOFT | Data unavailable (OI=0)")
            return {'skipped': True, 'oi_change_pct': 0, 'diverges': False}

        oi_change_pct = (oi - oi_1min) / oi_1min
        price_change_pct = (price - price_1min) / price_1min if price_1min > 0 else 0

        # OI divergence: OI and price move in opposite directions
        oi_direction = 'UP' if oi_change_pct > 0 else 'DOWN'
        price_direction = 'UP' if price_change_pct > 0 else 'DOWN'

        # Valid signal: OI moves opposite to price
        oi_diverges = (
            (oi_direction == 'UP' and price_direction == 'DOWN') or
            (oi_direction == 'DOWN' and price_direction == 'UP')
        )

        if not oi_diverges:
            self.logger.info(f"{self.log_prefix} Gate 3: SOFT | No divergence (OI:{oi_direction}, Price:{price_direction})")
            return {'skipped': True, 'oi_change_pct': oi_change_pct, 'diverges': False}

        if abs(oi_change_pct) < self.oi_change_min_pct:
            self.logger.info(f"{self.log_prefix} Gate 3: SOFT | OI change too small ({oi_change_pct*100:.2f}% < {self.oi_change_min_pct*100:.1f}%)")
            return {'skipped': True, 'oi_change_pct': oi_change_pct, 'diverges': False}

        # OI signal confirms direction (opposite to price)
        # Price down + OI up = LONG
        # Price up + OI down = SHORT
        signal_direction = 'LONG' if price_direction == 'DOWN' else 'SHORT'

        if signal_direction != direction:
            self.logger.info(f"{self.log_prefix} Gate 3: SOFT | OI contradicts delta (OI:{signal_direction} vs Delta:{direction})")
            return {'skipped': True, 'oi_change_pct': oi_change_pct, 'diverges': False}

        self.logger.info(f"{self.log_prefix} Gate 3: PASSED | OI:{oi_direction} | Price:{price_direction} | Change:{oi_change_pct*100:.2f}%")
        return {
            'skipped': False,
            'oi_change_pct': oi_change_pct,
            'oi_direction': oi_direction,
            'price_direction': price_direction,
            'diverges': oi_diverges,
        }

    def _normalize_ob(self, ob) -> list:
        """Normalize order book to list of [price, size] regardless of input format."""
        if isinstance(ob, dict):
            return [[float(k), float(v)] for k, v in ob.items()]
        elif isinstance(ob, list):
            return [[float(item[0]), float(item[1])] for item in ob if len(item) >= 2]
        return []

    def _find_iceberg_wall(self, binance_data: Dict[str, Any],
                          direction: str,
                          candles_m5: pd.DataFrame) -> Optional[Dict]:
        """
        Gate 4 (v8.1): Iceberg Order Book Wall Detection.

        Finds largest order book wall near current price.
        v8.1: Dynamic threshold (relative to avg volume) + stability check.
        """
        order_book = binance_data.get('order_book', {})
        if not order_book:
            self.logger.info(f"{self.log_prefix} Gate 4: FAILED | No order book")
            return None

        bids = self._normalize_ob(order_book.get('bids', []))
        asks = self._normalize_ob(order_book.get('asks', []))
        current_price = binance_data.get('current_price', self.current_price)

        if not bids and not asks:
            self.logger.info(f"{self.log_prefix} Gate 4: FAILED | Empty order book")
            return None

        # Determine wall threshold by session
        wall_threshold = self._get_wall_threshold()
        max_distance = current_price * self.wall_max_distance_pct

        result = {
            'wall_price': None,
            'wall_size_usd': 0,
            'stability_seconds': 0,
            'refill_confirmed': False,
        }

        # Find biggest wall within distance
        # v7.0: CORRECT sides — LONG=bid(support), SHORT=ask(resistance)
        sides_to_check = []
        if direction == 'LONG':
            sides_to_check = bids   # Support below = LONG bounce target
        else:
            sides_to_check = asks   # Resistance above = SHORT bounce target

        for level_price, level_size in sides_to_check:
            distance = abs(level_price - current_price)
            distance_pct = distance / current_price * 100

            # Estimate USD value
            if level_price > 0 and level_size > 0:
                # Assume size is in BTC: USD = price * BTC
                wall_usd = level_price * level_size
            else:
                wall_usd = 0

            # Track best wall within 1% (v5.1: relaxed from 0.5%)
            if distance_pct <= 1.0 and wall_usd > result['wall_size_usd']:
                result['wall_price'] = level_price
                result['wall_size_usd'] = wall_usd
                result['wall_size_btc'] = level_size
                result['distance_pct'] = distance_pct

        # v7.0: NO FALLBACK — if no wall within 1%, return None
        if result['wall_price'] is None:
            self.logger.info(f"{self.log_prefix} Gate 4: FAILED | No wall within 1%")
            return None

        # v7.0: Wall Bounce Check (Section 2.4) — FIX #2
        # atr_m5 is already calculated in _check_delta_absorption (Gate 1)
        wall_price = result['wall_price']
        atr = self.atr_m5 if self.atr_m5 > 0 else 200.0

        # v7.0: Wall Bounce Check (Section 2.4)
        if direction == 'LONG':
            if current_price < wall_price:
                # Price broke bid wall → flip to SHORT
                self.direction = 'SHORT'
                direction = 'SHORT'
                result['wall_break'] = True
            else:
                bounce_dist = current_price - wall_price
                max_bounce = atr * 0.5
                if bounce_dist > max_bounce:
                    self.logger.info(f"{self.log_prefix} Gate 4: FAILED | Bounce expired ${bounce_dist:.0f} > ${max_bounce:.0f}")
                    return None
                result['wall_break'] = False
        else:
            if current_price > wall_price:
                # Price broke ask wall → flip to LONG
                self.direction = 'LONG'
                direction = 'LONG'
                result['wall_break'] = True
            else:
                bounce_dist = wall_price - current_price
                max_bounce = atr * 0.5
                if bounce_dist > max_bounce:
                    self.logger.info(f"{self.log_prefix} Gate 4: FAILED | Bounce expired ${bounce_dist:.0f} > ${max_bounce:.0f}")
                    return None
                result['wall_break'] = False

        # === A2: Dynamic Volume Threshold (relative to avg M5 volume) ===
        # Wall ต้องใหญ่กว่า avg_vol * multiplier ของ session
        avg_vol_btc = self._avg_vol_btc
        avg_vol_usd = avg_vol_btc * current_price if avg_vol_btc > 0 else 0
        dynamic_threshold = avg_vol_usd * self.wall_relative_size_mult
        effective_threshold = max(self.absolute_min_wall_usd, dynamic_threshold)

        if result['wall_size_usd'] < effective_threshold:
            self.logger.info(f"{self.log_prefix} Gate 4: FAILED | Wall ${result['wall_size_usd']:,.0f} < ${effective_threshold:,.0f}")
            return None

        # === A1: Anti-Spoofing — Wall Stability Check (>= 45s) ===
        wall_history = binance_data.get('wall_history', [])
        if len(wall_history) >= 2:
            for entry in wall_history[-3:]:
                if entry.get('price') == result['wall_price']:
                    if entry.get('refilled', False):
                        result['refill_confirmed'] = True
                    result['stability_seconds'] = entry.get('stability_seconds', 15)

        # v11.6: Weekend scale — order book thinner on weekends
        # v28.3: 0.5→0.67 — 7.5s too low (spoof risk), 10s balances thin book vs anti-spoof
        from datetime import datetime as dt
        is_weekend = dt.now().weekday() >= 5  # 5=Saturday, 6=Sunday
        weekend_scale = 0.67 if is_weekend else 1.0
        effective_min_stability = self.min_wall_stability * weekend_scale

        # v8.1 A1: Block if wall not stable enough (anti-spoofing)
        stability = result.get('stability_seconds', 0)
        if stability < effective_min_stability:
            self.logger.info(f"{self.log_prefix} Gate 4: FAILED | Unstable {stability:.0f}s < {effective_min_stability:.0f}s (weekend:{is_weekend})")
            return None
            return None

        # Status: BREAK = wall broken, BOUNCE = price near wall, FAR = price far from wall
        if result.get('wall_break'):
            status = "BREAK"
        else:
            distance = abs(current_price - result['wall_price'])
            max_bounce = atr * 0.5
            if distance <= max_bounce:
                status = "BOUNCE"
            else:
                status = "FAR"
        self.logger.info(f"{self.log_prefix} Gate 4: PASSED | {status} | Wall ${result['wall_size_usd']:,.0f} @ {result['wall_price']:.0f} | {stability:.0f}s")
        return result

    def _check_m5_rejection(self, candles_m5: pd.DataFrame,
                            wall_price: float,
                            direction: str) -> bool:
        """
        Gate 5: M5 Rejection Candle at Wall Level (SOFT GATE).

        Valid rejection: candle wick extends to wall, then price rejects back.
        """
        if len(candles_m5) < 2:
            self.logger.info(f"{self.log_prefix} Gate 5: FAILED | Insufficient candles ({len(candles_m5)})")
            return False

        recent = candles_m5.iloc[-2:]

        for idx, row in recent.iterrows():
            high = float(row['high'])
            low = float(row['low'])
            close = float(row['close'])
            open_price = float(row['open'])

            wick_threshold = abs(high - low) * 0.3  # Wick must be 30%+ of range

            if direction == 'LONG':
                # Price came down to wall, rejected back up
                if low <= wall_price <= high:
                    # Check if it's a rejection candle (wick below body)
                    lower_wick = wall_price - min(low, open_price, close)
                    if lower_wick > wick_threshold:
                        wick_size = lower_wick
                        self.logger.info(f"{self.log_prefix} Gate 5: SOFT | Rejection confirmed at wall {wall_price:.0f} (wick:{wick_size:.0f}) +1 score")
                        return True
            else:
                if low <= wall_price <= high:
                    upper_wick = max(high, open_price, close) - wall_price
                    if upper_wick > wick_threshold:
                        wick_size = upper_wick
                        self.logger.info(f"{self.log_prefix} Gate 5: SOFT | Rejection confirmed at wall {wall_price:.0f} (wick:{wick_size:.0f}) +1 score")
                        return True

        self.logger.info(f"{self.log_prefix} Gate 5: SOFT | No rejection at wall {wall_price:.0f} (skip)")
        return False

    def _calculate_score(self,
                         delta_result: Dict,
                         oi_result: Dict,
                         wall_result: Dict,
                         rejection: bool,
                         binance_data: Dict[str, Any]) -> Tuple[int, Dict]:
        """
        Calculate IOF score (max 20 points).
        See Section 4.3 of Architecture Plan.
        """
        score = 0
        breakdown = {}

        # Extract signal_type from delta_result
        signal_type = delta_result.get('signal_type', 'MOMENTUM')

        # === Delta Absorption Quality (max 7) ===
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

        vol_ratio = delta_result['volume_ratio']
        if vol_ratio >= 2.0:
            score += 2
            breakdown['volume_surge_high'] = 2
        elif vol_ratio >= 1.2:
            score += 1
            breakdown['volume_surge'] = 1

        # === OI & Funding Signal (max 6) ===
        oi_change = abs(oi_result['oi_change_pct'])
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

        # === Wall Quality (max 5) - v6.0: Lowered thresholds ===
        wall_usd = wall_result['wall_size_usd']
        # v6.0: Lowered thresholds to match Gate 4 thresholds
        if wall_usd > 500000 and wall_result.get('refill_confirmed'):
            score += 3
            breakdown['wall_strong'] = 3
        elif wall_usd > 300000:
            score += 2
            breakdown['wall_medium'] = 2
        elif wall_usd > 100000:
            score += 1
            breakdown['wall_low'] = 1
        elif wall_usd > 0:
            # v6.0: Any wall gets at least 0.5 bonus (soft gate)
            score += 1
            breakdown['wall_minimal'] = 1

        if wall_result.get('stability_seconds', 0) >= 60:
            score += 1
            breakdown['wall_stable'] = 1

        # === Confirmation (max 2) ===
        if binance_data.get('liquidation_cascade', False):
            score += 1
            breakdown['liquidation'] = 1

        if rejection:
            score += 1
            breakdown['rejection'] = 1

        # v15.2: H1 EMA9 Direction — เฉพาะ MOMENTUM/ABSORPTION
        # REVERSAL/MEAN_REVERT สวน trend เสมอ → skip (ไม่โดน -2 ฟรี)
        if signal_type in ('MOMENTUM', 'ABSORPTION'):
            h1_ema9_dir = binance_data.get('h1_ema9_direction', 'NEUTRAL')
            if h1_ema9_dir != 'NEUTRAL':
                direction = delta_result.get('direction', 'NEUTRAL')
                if direction != 'NEUTRAL':
                    iof_is_aligned = (
                        (direction == 'LONG' and h1_ema9_dir == 'BULLISH') or
                        (direction == 'SHORT' and h1_ema9_dir == 'BEARISH')
                    )
                    iof_is_counter = (
                        (direction == 'LONG' and h1_ema9_dir == 'BEARISH') or
                        (direction == 'SHORT' and h1_ema9_dir == 'BULLISH')
                    )
                    if iof_is_aligned:
                        score += 1
                        breakdown['h1_ema9_aligned'] = 1
                    elif iof_is_counter:
                        score -= 2
                        breakdown['h1_ema9_counter'] = -2

        # v14.1: Wall alignment adjustment (from DER + Wall resolution)
        wall_adjust = getattr(self, '_wall_adjust', 0)
        score += wall_adjust
        if wall_adjust != 0:
            breakdown['wall_alignment'] = wall_adjust

        # v14.3: H1 Distance bonus/penalty
        h1_dist = binance_data.get('h1_ema_dist_pct', 0)
        h1_bias = binance_data.get('h1_bias', 'NEUTRAL')
        direction = delta_result.get('direction', 'NEUTRAL')

        if direction != 'NEUTRAL' and h1_bias != 'NEUTRAL':
            is_counter = (
                (direction == 'LONG' and h1_bias == 'BEARISH') or
                (direction == 'SHORT' and h1_bias == 'BULLISH')
            )
            is_with = (
                (direction == 'LONG' and h1_bias == 'BULLISH') or
                (direction == 'SHORT' and h1_bias == 'BEARISH')
            )

            # v26.0: MOMENTUM/ABSORPTION — counter-trend quality gate
            if signal_type in ('MOMENTUM', 'ABSORPTION'):
                if is_with and h1_dist > 1.5:
                    score -= 2  # With trend but overextended
                    breakdown['h1_dist_overextended'] = -2

                elif is_counter:
                    # สวน H1 trend → ต้องมี confirmation มากกว่า
                    counter_penalty = 0

                    # 1. H1 EMA alignment check (EMA9<20<50 = strong downtrend)
                    ema9 = binance_data.get('ema9', 0)
                    ema20 = binance_data.get('ema20', 0)
                    ema50 = binance_data.get('ema50', 0)
                    strong_trend = (ema9 > 0 and ema20 > 0 and ema50 > 0 and
                        ((ema9 < ema20 < ema50) or (ema9 > ema20 > ema50)))
                    if strong_trend:
                        counter_penalty -= 2  # สวน EMA aligned = ลำบาก
                        breakdown['counter_strong_trend'] = -2

                    # 2. H1 distance — สวน trend ยิ่งไกลยิ่งอันตราย
                    if h1_dist > 1.0:
                        counter_penalty -= 1
                        breakdown['counter_h1_dist'] = -1

                    # 3. Wall support check — สวน trend ต้องมี wall หนุน
                    wall_result = getattr(self, '_wall_result', {}) or {}
                    wall_size = wall_result.get('wall_size_usd', 0)
                    if wall_size < 500000:  # wall < $500K = ไม่มี institutional support
                        counter_penalty -= 1
                        breakdown['counter_no_wall'] = -1

                    # 4. EQS check — สวน trend ต้องเปิดที่ pullback ไม่ใช่ impulse
                    eqs = getattr(self, '_iof_eqs', 0)
                    if eqs <= 0:
                        counter_penalty -= 1
                        breakdown['counter_impulse'] = -1

                    score += counter_penalty
                    if counter_penalty < 0:
                        self.logger.info(
                            f"{self.log_prefix} Counter-trend {direction} vs H1:{h1_bias} | "
                            f"penalty:{counter_penalty} (strong:{strong_trend} dist:{h1_dist:.1f}% wall:${wall_size:.0f} eqs:{eqs})"
                        )

            # REVERSAL / MEAN_REVERT: ใช้แค่ bonus (reversal distance)
            elif signal_type in ('REVERSAL_OB', 'REVERSAL_OS', 'MEAN_REVERT'):
                if is_counter and h1_dist > 2.5:
                    score += 3  # 83% reversal
                    breakdown['h1_dist_reversal'] = 3
                elif is_counter and h1_dist > 2.0:
                    score += 2  # 80% reversal
                    breakdown['h1_dist_reversal'] = 2
                elif is_counter and h1_dist > 1.5:
                    score += 1  # 58% reversal (small edge)
                    breakdown['h1_dist_reversal'] = 1

        # v18.1: IOFF REVERSAL score enhancement (4 factors)
        if signal_type in ('REVERSAL_OB', 'REVERSAL_OS', 'MEAN_REVERT'):
            # 1. FRVP POC Proximity (+2)
            frvp_data = binance_data.get('frvp', {})
            if frvp_data:
                poc = frvp_data.get('poc', 0)
                if poc > 0 and atr > 0:
                    poc_dist = abs(self.current_price - poc) / atr
                    if poc_dist <= 0.3:
                        score += 2
                        breakdown['frvp_poc_near'] = 2

            # 2. Wall Ratio Aligned (+2/+3)
            wall_scan = binance_data.get('wall_scan', {})
            if wall_scan:
                raw_ratio = wall_scan.get('raw_ratio', 1)
                raw_dominant = wall_scan.get('raw_dominant', 'NONE')
                wall_aligned = (
                    (direction == 'LONG' and raw_dominant == 'BID') or
                    (direction == 'SHORT' and raw_dominant == 'ASK')
                )
                if wall_aligned:
                    if raw_ratio >= 5:
                        score += 3
                        breakdown['wall_ratio_strong'] = 3
                    elif raw_ratio >= 3:
                        score += 2
                        breakdown['wall_ratio_good'] = 2

            # 3. Funding Rate Extreme (+1)
            funding = binance_data.get('funding_rate', 0)
            if direction == 'LONG' and funding < -0.0001:
                score += 1
                breakdown['funding_crowded_short'] = 1
            elif direction == 'SHORT' and funding > 0.0001:
                score += 1
                breakdown['funding_crowded_long'] = 1

            # 4. Exhaustion Quality Bonus (+1/+2)
            exhaustion = getattr(self, '_exhaustion_score', 0)
            if exhaustion >= 5:
                score += 2
                breakdown['exhaustion_strong'] = 2
            elif exhaustion >= 4:
                score += 1
                breakdown['exhaustion_good'] = 1

        # v17.6: REVERSAL + Pullback ACTIVE ตาม H1 → score +2
        if signal_type in ('REVERSAL_OB', 'REVERSAL_OS', 'MEAN_REVERT'):
            pullback = binance_data.get('pullback', {})
            pb_status = pullback.get('status', 'NONE')
            h1_bias = binance_data.get('h1_bias', 'NEUTRAL')

            reversal_aligned_h1 = (
                (direction == 'LONG' and h1_bias == 'BULLISH') or
                (direction == 'SHORT' and h1_bias == 'BEARISH')
            )

            if pb_status == 'ACTIVE' and reversal_aligned_h1:
                score += 2
                breakdown['pb_reversal_aligned'] = 2

        # v15.1: DER bonus สำหรับ REVERSAL/MEAN_REVERT
        if signal_type in ('REVERSAL_OB', 'REVERSAL_OS') and der >= 0.5:
            score += 1
            breakdown['der_climactic'] = 1

        if signal_type == 'MEAN_REVERT' and der < 0.2:
            score += 1
            breakdown['der_exhaustion'] = 1

        # v15.9: Exhaustion mode scoring bonuses/penalties
        if signal_type in ('REVERSAL_OB', 'REVERSAL_OS'):
            reversal_mode = getattr(self, '_reversal_mode', 'STRUCTURAL')
            
            if reversal_mode == 'STRONG_EXHAUST':
                score += 1
                breakdown['exhaustion_bonus'] = 1
            elif reversal_mode == 'EXHAUSTED':
                score += 0  # No bonus, just passed
                breakdown['exhaustion_passed'] = 0
            elif reversal_mode == 'MODERATE':
                score -= 2
                breakdown['moderate_penalty'] = -2
            # STRUCTURAL: uses h1_dist bonuses (already applied above)

        # v16.9: Wall missing penalty (IOFF MOMENTUM without wall = -3)
        wall_penalty = getattr(self, '_wall_missing_penalty', 0)
        if wall_penalty != 0:
            score += wall_penalty
            breakdown['wall_missing'] = wall_penalty
            self._wall_missing_penalty = 0  # reset

        # v28.0: M5 State score adjustment
        m5_state = binance_data.get('m5_state', 'RANGING')
        if m5_state == 'TRENDING':
            if signal_type == 'MOMENTUM':
                score += 2
                breakdown['m5_trending_momentum'] = 2
        elif m5_state == 'EXHAUSTION':
            if signal_type in ('REVERSAL_OB', 'REVERSAL_OS', 'MEAN_REVERT'):
                score += 2
                breakdown['m5_exhaustion_reversal'] = 2
            elif signal_type == 'ABSORPTION':
                score += 1
                breakdown['m5_exhaustion_absorption'] = 1
        elif m5_state == 'PULLBACK':
            # v29.1: PULLBACK = temporary dip in trend, bonus for trend-aligned
            if signal_type == 'MOMENTUM':
                score += 1
                breakdown['m5_pullback_momentum'] = 1
        elif m5_state == 'CAUTION':
            # v29.1 C5: H1 NEUTRAL + gray zone — penalty for MOMENTUM
            caution_pen = getattr(self, '_caution_penalty', 0)
            if caution_pen != 0:
                score += caution_pen
                breakdown['m5_caution'] = caution_pen
                self._caution_penalty = 0
            else:
                score -= 1
                breakdown['m5_caution'] = -1
        elif m5_state == 'RANGING':
            score -= 1
            breakdown['m5_ranging'] = -1

        breakdown['total'] = score
        return score, breakdown

    def _get_wall_threshold(self, session: str = None) -> float:
        """Get wall threshold by session."""
        sess = session or getattr(self, 'session', 'LONDON')
        return {
            'ASIA': self.wall_threshold_asia,
            'ASIA-LATE': self.wall_threshold_asia,
            'LONDON': self.wall_threshold_london,
            'LONDON-NY': self.wall_threshold_london,
            'NY': self.wall_threshold_ny,
        }.get(sess, self.wall_threshold_london)

    def _get_major_levels(self, candles_m5: pd.DataFrame,
                         direction: str,
                         wall_price: float,
                         entry_price: float) -> Dict[str, Optional[float]]:
        """Get next major resistance and support levels."""
        lookback = min(50, len(candles_m5) - 1)
        recent = candles_m5.iloc[-lookback:]

        highs = recent['high'].values
        lows = recent['low'].values

        if direction == 'LONG':
            # Support (SL ref): nearest low below wall = highest low in below_wall
            below_wall = [l for l in lows if l < wall_price]
            support = max(below_wall) if below_wall else None
            # Resistance (TP ref): nearest high above entry = lowest high in above
            above = [h for h in highs if h > entry_price * 1.001]
            resistance = min(above) if above else None
        else:
            # Resistance (SL ref): nearest high above wall = lowest high in above_wall
            above_wall = [h for h in highs if h > wall_price]
            resistance = min(above_wall) if above_wall else None
            # Support (TP ref): nearest low below entry = highest low in below
            below = [l for l in lows if l < entry_price * 0.999]
            support = max(below) if below else None

        return {'resistance': resistance, 'support': support}

    def _check_iof_entry_quality(self, candles_m5, direction: str) -> int:
        """
        v25.0: Entry Quality Score for IOF/IOFF MOMENTUM & ABSORPTION
        ป้องกันเข้าที่ยอดดอย/ก้นดอย (impulse tip)

        return: -3 to +3
          +2/+1 = pullback entry (ดี)
          -2/-3 = impulse entry (ยอดดอย/ก้นดอย)
        """
        import numpy as np

        if len(candles_m5) < 10:
            return 0

        # v26.0: Fixed - use proper ATR calculation
        tr = candles_m5['high'].iloc[-14:] - candles_m5['low'].iloc[-14:]
        atr = float(tr.mean()) if len(tr) > 0 else 1.0
        if atr <= 0:
            return 0

        eqs = 0
        current_price = float(candles_m5['close'].iloc[-1])

        # === 1. Retrace Ratio (±2) ===
        # ดู 10 แท่งล่าสุด: ราคาวิ่งจาก extreme แล้ว retrace กลับเท่าไหร่
        recent = candles_m5.iloc[-10:]
        if direction == 'LONG':
            high_after = float(recent['high'].max())
            low_start = float(recent['low'].iloc[0])
            impulse = high_after - low_start
            retrace = high_after - current_price
        else:
            low_after = float(recent['low'].min())
            high_start = float(recent['high'].iloc[0])
            impulse = high_start - low_after
            retrace = current_price - low_after

        retrace_ratio = retrace / impulse if impulse > 0 else 0

        r_log = ""
        if retrace_ratio >= 0.5:
            eqs += 2; r_log = "50%+"
        elif retrace_ratio >= 0.3:
            eqs += 1; r_log = "30-50%"
        elif retrace_ratio < 0.1:
            eqs -= 2; r_log = "<10%"
        else:
            r_log = "10-30%"

        # === 2. M5 EMA20 Distance (±1) ===
        ema20 = float(candles_m5['close'].ewm(span=20).mean().iloc[-1])
        m5_dist = abs(current_price - ema20) / atr

        e_log = ""
        if m5_dist < 0.5:
            eqs += 1; e_log = "near"
        elif m5_dist > 1.5:
            eqs -= 1; e_log = "far"
        else:
            e_log = "mid"

        # === 3. Volume Declining (±1) ===
        vol_avg = float(candles_m5['volume'].iloc[-20:].mean())
        vol_now = float(candles_m5['volume'].iloc[-3:].mean())

        v_log = ""
        if vol_avg > 0:
            if vol_now < vol_avg * 0.7:
                eqs += 1; v_log = "low"
            elif vol_now > vol_avg * 1.5:
                eqs -= 1; v_log = "spike"
            else:
                v_log = "mid"

        self.logger.info(
            f"{self.log_prefix} EQS: {eqs:+d}/3 | Retrace:{r_log} | M5 EMA:{e_log} | Vol:{v_log}"
        )
        return eqs

    def _check_exhaustion_quality(self, candles_m5: pd.DataFrame, direction: str) -> int:
        """
        v15.9: Exhaustion Quality Score for IOFF REVERSAL signals.

        Measures momentum exhaustion from M5 candles.
        return: score 0-6 (higher = more exhausted = better for reversal)
        """
        if len(candles_m5) < 8:
            return 0
        
        recent = candles_m5.iloc[-8:]
        atr = self.atr_m5 if self.atr_m5 > 0 else 100
        score = 0
        
        # 1. Volume Climax → Decline (+1)
        vols = recent['volume'].values
        vol_peak = vols[:-2].max()
        vol_now = vols[-2:].mean()
        vol_avg = vols.mean()
        if vol_peak > vol_avg * 2.0 and vol_now < vol_peak * 0.6:
            score += 1
        
        # 2. Body Shrinking (+1)
        bodies = [abs(r['close'] - r['open']) for _, r in recent.iterrows()]
        body_before = max(bodies[-5:-2]) if len(bodies) >= 5 else 0
        body_now = bodies[-1]
        if body_before > atr * 0.5 and body_now < atr * 0.3:
            score += 1
        
        # 3. Wick Rejection (+1)
        for i in range(-3, 0):
            if i >= -len(recent):
                r = recent.iloc[i]
                body = abs(r['close'] - r['open'])
                if direction == 'LONG':
                    wick = r['high'] - max(r['close'], r['open'])
                else:
                    wick = min(r['close'], r['open']) - r['low']
                if wick > body * 0.4:
                    score += 1
                    break
        
        # 4. Consecutive Candles Same Direction → Fatigue (+1)
        consecutive = 0
        for i in range(-1, -6, -1):
            if abs(i) > len(candles_m5): break
            r = recent.iloc[i]
            if (direction == 'LONG' and r['close'] > r['open']) or \
               (direction == 'SHORT' and r['close'] < r['open']):
                consecutive += 1
            else:
                break
        if consecutive >= 3:
            score += 1
        
        # 5. DER Fading (+1)
        early_delta = sum([
            r['volume'] if r['close'] > r['open'] else -r['volume']
            for _, r in recent.iloc[:4].iterrows()
        ])
        late_delta = sum([
            r['volume'] if r['close'] > r['open'] else -r['volume']
            for _, r in recent.iloc[4:].iterrows()
        ])
        if abs(early_delta) > 0 and abs(late_delta) < abs(early_delta) * 0.5:
            score += 1
        
        # 6. ATR Expansion → Contraction (+1)
        tr = [recent.iloc[i]['high'] - recent.iloc[i]['low'] for i in range(len(recent))]
        tr_peak = max(tr[:-2]) if len(tr) >= 3 else 0
        tr_now = (tr[-1] + tr[-2]) / 2 if len(tr) >= 2 else 0
        if tr_peak > atr * 1.5 and tr_now < tr_peak * 0.6:
            score += 1
        
        return score

    def _check_reversal_confirmation(self, candles_m5: pd.DataFrame, binance_data: Dict,
                                     reverse_dir: str) -> int:
        """
        v12.6: Reversal confirmation for overbought/oversold signals.
        
        reverse_dir: 'SHORT' ถ้า overbought, 'LONG' ถ้า oversold
        return: score 0-11 (ต้อง >= 5 ถึง flip — จากเดิม 4)
        
        Score Breakdown (v12.6):
          +1: Volume declining trend (5 แท่ง)
          +1: Candle reversal body ≥ 0.3 ATR
          +1: Wick rejection (> 40%)
          +2: OI divergence (> 0.3%)
          +2: Wall (dynamic threshold)
          +1: Funding (> 0.02%)
          +2: H1 bias alignment (ใหม่)
          -1: H1 bias สวนทิศ (penalty ใหม่)
          +1: Consecutive ≥ 4 candles ในทิศ reverse (ใหม่)
        """
        score = 0
        current_price = binance_data.get('current_price', self.current_price)
        atr = self.atr_m5 if self.atr_m5 > 0 else (current_price * 0.0005 if current_price > 0 else 100)
        recent = candles_m5.iloc[-5:]

        # 1. Volume Declining (+1) — v12.6: trend 5 แท่ง (จาก 3 แท่ง)
        vols = recent['volume'].values
        if len(vols) >= 4 and vols[-1] < vols[-2] < vols[-3]:
            score += 1

        # 2. Candle Reversal Body ≥ 0.3 ATR (+1) — v12.6: ATR threshold
        last = candles_m5.iloc[-1]
        body = abs(last['close'] - last['open'])
        if body >= atr * 0.3:
            if reverse_dir == 'SHORT' and last['close'] < last['open']:
                score += 1
            elif reverse_dir == 'LONG' and last['close'] > last['open']:
                score += 1

        # 3. Wick Rejection (+1) — unchanged
        candle_range = last['high'] - last['low']
        if candle_range > 0:
            if reverse_dir == 'SHORT':
                upper_wick = last['high'] - max(last['open'], last['close'])
                if upper_wick / candle_range > 0.4:
                    score += 1
            else:
                lower_wick = min(last['open'], last['close']) - last['low']
                if lower_wick / candle_range > 0.4:
                    score += 1

        # 4. OI Divergence (+2) — v12.6: threshold 0.3% (จาก 0.1%)
        oi = binance_data.get('oi', 0)
        oi_prev = binance_data.get('oi_1min_ago', 0)
        price = binance_data.get('current_price', 0)
        price_prev = binance_data.get('price_1min_ago', price)
        if oi > 0 and oi_prev > 0 and price_prev > 0:
            oi_chg = (oi - oi_prev) / oi_prev
            price_chg = (price - price_prev) / price_prev
            if reverse_dir == 'SHORT' and oi_chg < -0.003 and price_chg > 0:
                score += 2
            elif reverse_dir == 'LONG' and oi_chg < -0.003 and price_chg < 0:
                score += 2

        # 5. Wall ฝั่ง reverse (+2) — v12.6: dynamic threshold
        # v13.5 FIX: fallback 0.2 → 0.0005 to match wall_relative_size_mult
        order_book = binance_data.get('order_book', {})
        bids = self._normalize_ob(order_book.get('bids', []))
        asks = self._normalize_ob(order_book.get('asks', []))
        avg_vol_btc = getattr(self, '_avg_vol_btc', 0)
        if avg_vol_btc <= 0:
            avg_vol_btc = candles_m5['volume'].iloc[-20:].median() if len(candles_m5) >= 20 else candles_m5['volume'].mean()
        avg_vol_usd = avg_vol_btc * current_price
        wall_thresh = max(50000, avg_vol_usd * getattr(self, 'wall_relative_size_mult', 0.0005))

        if reverse_dir == 'SHORT':
            for p, s in asks:
                if 0 < (p - current_price) <= atr * 0.5:
                    wall_usd = p * s
                    if wall_usd > wall_thresh:
                        score += 2
                        break
        else:
            for p, s in bids:
                if 0 < (current_price - p) <= atr * 0.5:
                    wall_usd = p * s
                    if wall_usd > wall_thresh:
                        score += 2
                        break

        # 6. Funding Rate (+1) — v12.6: threshold 0.02% (จาก 0.05%)
        funding = abs(binance_data.get('funding_rate', 0))
        if funding > 0.0002:
            score += 1

        # 7. H1 Bias Alignment (+2/-1) — v12.6: ใหม่
        h1_bias = binance_data.get('h1_bias', None)
        if h1_bias:
            if reverse_dir == 'LONG' and h1_bias == 'BULLISH':
                score += 2
            elif reverse_dir == 'SHORT' and h1_bias == 'BEARISH':
                score += 2
            elif reverse_dir == 'LONG' and h1_bias == 'BEARISH':
                score -= 1
            elif reverse_dir == 'SHORT' and h1_bias == 'BULLISH':
                score -= 1

        # 8. Consecutive Candles (+1) — v12.6: ใหม่
        # นับจำนวนแท่งที่เป็นไปในทิศ reverse ติดกัน (สูงสุด 5 แท่ง)
        count = 0
        for i in range(-1, -6, -1):
            if abs(i) > len(candles_m5):
                break
            c = candles_m5.iloc[i]
            if reverse_dir == 'SHORT' and c['close'] > c['open']:
                count += 1
            elif reverse_dir == 'LONG' and c['close'] < c['open']:
                count += 1
            else:
                break
        if count >= 4:
            score += 1

        return score

    def _calc_atr(self, candles: pd.DataFrame, period: int = 14) -> float:
        """Calculate ATR."""
        if len(candles) < period + 1:
            return 100.0

        high = candles['high'].values
        low = candles['low'].values
        close = candles['close'].values

        tr1 = high - low
        tr2 = np.abs(high - np.roll(close, 1))
        tr3 = np.abs(low - np.roll(close, 1))

        tr = np.maximum(tr1, np.maximum(tr2, tr3))
        tr[0] = tr1[0]  # First element: H - L only

        atr = pd.Series(tr).rolling(window=period).mean().iloc[-1]
        return float(atr) if not np.isnan(atr) else 100.0

    def _scan_walls_both_sides(self, binance_data: Dict[str, Any],
                               current_price: float,
                               candles_m5: pd.DataFrame) -> Optional[Dict]:
        """
        v14.8: สแกน wall 2 ระดับ
        - raw: size อย่างเดียว → ใช้สำหรับ MEAN_REVERT (wall เพิ่งวางก็นับ)
        - validated: size + stability → ใช้สำหรับ WALL OVERRIDE (ต้อง stable)
        """
        order_book = binance_data.get('order_book', {})
        if not order_book:
            self.logger.info(f"{self.log_prefix} Gate 1a: WALL SCAN | No order book data")
            return None

        bids = self._normalize_ob(order_book.get('bids', []))
        asks = self._normalize_ob(order_book.get('asks', []))

        if not bids and not asks:
            self.logger.info(f"{self.log_prefix} Gate 1a: WALL SCAN | Empty order book")
            return None

        # หา biggest wall แต่ละฝั่ง (within 1% of price)
        best_bid = {'price': None, 'size_usd': 0}
        best_ask = {'price': None, 'size_usd': 0}

        for price, size in bids:
            dist_pct = abs(price - current_price) / current_price * 100
            wall_usd = price * size
            if dist_pct <= 1.0 and wall_usd > best_bid['size_usd']:
                best_bid = {'price': price, 'size_usd': wall_usd, 'size_btc': size, 'dist_pct': dist_pct}

        for price, size in asks:
            dist_pct = abs(price - current_price) / current_price * 100
            wall_usd = price * size
            if dist_pct <= 1.0 and wall_usd > best_ask['size_usd']:
                best_ask = {'price': price, 'size_usd': wall_usd, 'size_btc': size, 'dist_pct': dist_pct}

        bid_usd = best_bid['size_usd']
        ask_usd = best_ask['size_usd']

        # --- Raw dominant (ไม่สน stability) → MEAN_REVERT ---
        raw_dominant = 'BID' if bid_usd >= ask_usd else 'ASK'
        raw_ratio = max(bid_usd, ask_usd) / max(min(bid_usd, ask_usd), 1) if min(bid_usd, ask_usd) > 0 else 0

        # --- Validated dominant (stability confirmed) → WALL OVERRIDE ---
        wall_history = binance_data.get('wall_history', [])
        from datetime import datetime as dt
        is_weekend = dt.now().weekday() >= 5
        min_stab = self.min_wall_stability * (0.67 if is_weekend else 1.0)

        def _is_stable(wall_data):
            if wall_data.get('price') is None:
                return False
            for entry in wall_history[-3:]:
                if entry.get('price') == wall_data['price']:
                    return entry.get('stability_seconds', 0) >= min_stab
            return False

        bid_stable = _is_stable(best_bid)
        ask_stable = _is_stable(best_ask)

        v_bid = bid_usd if bid_stable else 0
        v_ask = ask_usd if ask_stable else 0

        if v_bid > 0 or v_ask > 0:
            validated_dominant = 'BID' if v_bid >= v_ask else 'ASK'
            validated_ratio = max(v_bid, v_ask) / max(min(v_bid, v_ask), 1) if min(v_bid, v_ask) > 0 else 999
        else:
            validated_dominant = 'NONE'
            validated_ratio = 0

        self.logger.info(
            f"{self.log_prefix} Gate 1a: WALL SCAN | "
            f"Bid:${bid_usd:,.0f}{'✓' if bid_stable else '✗'} "
            f"Ask:${ask_usd:,.0f}{'✓' if ask_stable else '✗'} | "
            f"Raw:{raw_dominant} {raw_ratio:.1f}x | "
            f"Valid:{validated_dominant} {validated_ratio:.1f}x"
        )

        return {
            'bid_wall': best_bid, 'ask_wall': best_ask,
            'raw_dominant': raw_dominant, 'raw_ratio': raw_ratio,
            'validated_dominant': validated_dominant, 'validated_ratio': validated_ratio,
            'dominant': raw_dominant,  # backward compatibility
            'ratio': raw_ratio,       # backward compatibility
        }

    def _resolve_direction(self, der_direction: str,
                          signal_type: str,
                          wall_scan: Optional[Dict]) -> Tuple[str, int, str]:
        """
        v14.8: ร่วมตัดสิน direction จาก DER + Wall.

        Rules:
        1. MEAN_REVERT: raw wall ตัดสินแล้ว → ใช้เลย
        2. ABSORPTION: lock (validated wall confirm)
        3. REVERSAL: lock (M5 OB/OS ตัดสินแล้ว)
        4. MOMENTUM: validated wall (ต้อง stable)
        """
        if wall_scan is None:
            return der_direction, 0, 'DER_ONLY'

        # MEAN_REVERT: raw wall ตัดสินแล้ว → ใช้เลย
        if signal_type == 'MEAN_REVERT':
            return der_direction, 0, 'MEAN_REVERT'

        # ABSORPTION lock — ใช้ validated wall
        if signal_type == 'ABSORPTION':
            v_dom = wall_scan['validated_dominant']
            wall_dir = 'LONG' if v_dom == 'BID' else 'SHORT' if v_dom == 'ASK' else None
            adjust = +1 if (wall_dir and der_direction == wall_dir) else -1 if wall_dir else 0
            return der_direction, adjust, 'ABSORPTION_LOCKED'

        # REVERSAL lock — ใช้ validated wall
        if signal_type in ('REVERSAL_OB', 'REVERSAL_OS'):
            v_dom = wall_scan['validated_dominant']
            wall_dir = 'LONG' if v_dom == 'BID' else 'SHORT' if v_dom == 'ASK' else None
            adjust = +1 if (wall_dir and der_direction == wall_dir) else 0
            return der_direction, adjust, 'REVERSAL_LOCKED'

        # MOMENTUM: ใช้ validated_dominant (ต้อง stable)
        v_dom = wall_scan['validated_dominant']
        if v_dom == 'NONE':
            # ไม่มี stable wall → ไม่ override
            return der_direction, 0, 'DER_ONLY'

        wall_dir = 'LONG' if v_dom == 'BID' else 'SHORT'
        v_ratio = wall_scan['validated_ratio']

        if der_direction == wall_dir:
            return der_direction, +1, 'ALIGNED'

        elif v_ratio >= 3.0:
            # v14.8: WALL OVERRIDE เฉพาะ DER ไม่แรง (< 0.5)
            if self._current_der < 0.5:
                self.logger.info(
                    f"{self.log_prefix} Gate 1c: WALL OVERRIDE | "
                    f"DER:{der_direction} vs Wall:{wall_dir} (ratio:{v_ratio:.1f}x, DER:{self._current_der:.3f}) → {wall_dir}"
                )
                return wall_dir, 0, 'WALL_OVERRIDE'
            else:
                # DER แรงเกินให้ wall ชนะ → ใช้ DER แต่ penalty
                self.logger.info(
                    f"{self.log_prefix} Gate 1c: CONFLICT_DER_STRONG | "
                    f"DER:{der_direction} vs Wall:{wall_dir} (ratio:{v_ratio:.1f}x, DER:{self._current_der:.3f}) → DER with penalty"
                )
                return der_direction, -2, 'CONFLICT_DER_STRONG'
        else:
            self.logger.info(
                f"{self.log_prefix} Gate 1c: CONFLICT | "
                f"DER:{der_direction} vs Wall:{wall_dir} (ratio:{v_ratio:.1f}x) → DER with penalty"
            )
            return der_direction, -2, 'CONFLICT'

    def _validate_wall(self, wall_data: Optional[Dict],
                       binance_data: Dict[str, Any],
                       direction: str,
                       candles_m5: pd.DataFrame) -> Optional[Dict]:
        """
        v14.1: Validate chosen wall (threshold, stability, bounce check).
        Extracted from _find_iceberg_wall() — validation logic only.
        """
        if wall_data is None or wall_data.get('price') is None:
            self.logger.info(f"{self.log_prefix} Gate 4: FAILED | No wall data to validate")
            return None

        current_price = binance_data.get('current_price', self.current_price)
        wall_price = wall_data['price']
        wall_usd = wall_data['size_usd']

        result = {
            'wall_price': wall_price,
            'wall_size_usd': wall_usd,
            'wall_size_btc': wall_data.get('size_btc', 0),
            'stability_seconds': 0,
            'refill_confirmed': False,
            'wall_break': False,
        }

        # === Bounce Check ===
        atr = self.atr_m5 if self.atr_m5 > 0 else 200.0

        if direction == 'LONG':
            if current_price < wall_price:
                # Price broke bid wall → wall is broken, but still valid (bounce failed)
                result['wall_break'] = True
            else:
                bounce_dist = current_price - wall_price
                max_bounce = atr * 0.5
                if bounce_dist > max_bounce:
                    self.logger.info(f"{self.log_prefix} Gate 4: FAILED | Bounce expired ${bounce_dist:.0f} > ${max_bounce:.0f}")
                    return None
                result['wall_break'] = False
        else:  # SHORT
            if current_price > wall_price:
                # Price broke ask wall → wall is broken
                result['wall_break'] = True
            else:
                bounce_dist = wall_price - current_price
                max_bounce = atr * 0.5
                if bounce_dist > max_bounce:
                    self.logger.info(f"{self.log_prefix} Gate 4: FAILED | Bounce expired ${bounce_dist:.0f} > ${max_bounce:.0f}")
                    return None
                result['wall_break'] = False

        # === Dynamic Volume Threshold ===
        avg_vol_btc = self._avg_vol_btc
        avg_vol_usd = avg_vol_btc * current_price if avg_vol_btc > 0 else 0
        dynamic_threshold = avg_vol_usd * self.wall_relative_size_mult
        effective_threshold = max(self.absolute_min_wall_usd, dynamic_threshold)

        if wall_usd < effective_threshold:
            self.logger.info(f"{self.log_prefix} Gate 4: FAILED | Wall ${wall_usd:,.0f} < ${effective_threshold:,.0f}")
            return None

        # === Anti-Spoofing — Stability Check ===
        wall_history = binance_data.get('wall_history', [])
        if len(wall_history) >= 2:
            for entry in wall_history[-3:]:
                if entry.get('price') == wall_price:
                    if entry.get('refilled', False):
                        result['refill_confirmed'] = True
                    result['stability_seconds'] = entry.get('stability_seconds', 15)

        # Weekend scale
        # v28.3: 0.5→0.67 — 10s minimum for anti-spoof on thin weekend books
        from datetime import datetime as dt
        is_weekend = dt.now().weekday() >= 5
        weekend_scale = 0.67 if is_weekend else 1.0
        effective_min_stability = self.min_wall_stability * weekend_scale

        stability = result.get('stability_seconds', 0)
        if stability < effective_min_stability:
            self.logger.info(f"{self.log_prefix} Gate 4: UNSTABLE | {stability:.0f}s < {effective_min_stability:.0f}s (weekend:{is_weekend}) → penalty -1")
            result['wall_unstable_penalty'] = -1
            return result

        # Status log
        if result.get('wall_break'):
            status = "BREAK"
        else:
            distance = abs(current_price - wall_price)
            max_bounce = atr * 0.5
            if distance <= max_bounce:
                status = "BOUNCE"
            else:
                status = "FAR"
        self.logger.info(f"{self.log_prefix} Gate 4: PASSED | {status} | Wall ${wall_usd:,.0f} @ {wall_price:.0f} | {stability:.0f}s")
        return result