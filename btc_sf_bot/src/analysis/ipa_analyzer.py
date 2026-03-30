"""
IPA Analyzer — Institutional Price Action — v5.0 Aggressive Mode

Logic Flow (Section 3.2 of Architecture Plan):
  Gate 1: H1 Bias (EMA20/EMA50 cross + close > EMA20)
  Gate 2: M5 Structure Break (CHoCH or BOS aligned with H1, n=2)
  Gate 3: Order Block (body > 0.05%, not mitigated, retest within 12 candles)
  Gate 4: Liquidity Context (Discount/Premium zone or recent Sweep)
  Gate 5: Session Filter (volume_mult adapted by session)

Scoring (Section 3.3 — max 20 points):
  H1 Structure (max 6):
    +3  H1 BOS/BREAK (body close beyond swing)
    +2  H1 CHoCH (change of character)
    +1  H1 FVG (unfilled)
  M5 Entry Quality (max 9):
    +3  M5 CHoCH
    +2  M5 BOS
    +2  OB Quality (body > 0.05%, not mitigated)
    +1  OB Retest (within 12 candles) or OB Zone Entry
    +1  FVG overlap with OB
  Liquidity & Context (max 5):
    +2  Liquidity Sweep before signal
    +2  Discount/Premium zone alignment
    +1  Volume spike at structure break

Score Threshold: >= 10 → Signal (v5.0 Aggressive)
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from src.utils.decorators import log_errors, retry, circuit_breaker
from src.utils.metrics import timed_metric
from typing import Optional, List, Dict, Any, Tuple
import pandas as pd
import numpy as np

from src.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class IPAResult:
    """
    Result from IPA Analyzer.
    Returned when all 5 gates pass and score >= 10.
    """
    direction: str                   # 'LONG' or 'SHORT'
    score: int                       # 0-20
    h1_bias: str                    # 'BULLISH' | 'BEARISH' | 'NEUTRAL'
    h1_bos: bool
    h1_choch: bool
    h1_fvg_unfilled: bool
    m5_choch: bool
    m5_bos: bool
    ob_high: Optional[float]
    ob_low: Optional[float]
    ob_body_pct: float
    ob_mitigated: bool
    fvg_high: Optional[float]
    fvg_low: Optional[float]
    fvg_overlap: bool
    sweep_confirmed: bool
    sweep_candles_ago: int
    volume_spike: bool
    zone_context: str               # 'DISCOUNT' | 'PREMIUM' | 'NEUTRAL'
    volume_ratio: float
    atr_m5: float
    entry_zone_min: float
    entry_zone_max: float
    ob_distance_atr: float = 999.0  # v11.1: ATR distance from OB (0 = in zone)
    swing_highs: List[float] = field(default_factory=list)
    swing_lows: List[float] = field(default_factory=list)
    pdh: Optional[float] = None
    pdl: Optional[float] = None
    h1_fvg_boundary: Optional[float] = None
    session: str = 'LONDON'
    score_breakdown: Dict[str, Any] = field(default_factory=dict)
    # v26.4: Layer directions for dashboard
    l0_direction: str = '---'
    l1_direction: str = '---'
    l2_direction: str = '---'
    l3_direction: str = '---'

    @property
    def entry_price(self) -> float:
        """Mid-point of entry zone."""
        return (self.entry_zone_min + self.entry_zone_max) / 2

    def __str__(self) -> str:
        return (f"IPA {self.direction} | Score: {self.score}/20 | "
                f"H1: {self.h1_bias} | Zone: {self.zone_context} | "
                f"Sweep: {self.sweep_confirmed} | Session: {self.session}")


class IPAAnalyzer:
    """
    Institutional Price Action Analyzer.

    Detects high-probability IPA setups using HTF-LTF confluence:
      - H1 BOS/CHoCH sets the directional bias
      - M5 CHoCH/BOS confirms the entry
      - Order Block provides the entry zone
      - Liquidity Sweep confirms institutional participation
    """

    def __init__(self, config: dict = None, logger=None, log_prefix="[IPA]"):
        self.config = config or {}
        self.logger = logger if logger else get_logger(__name__)
        self.log_prefix = log_prefix

        # === H1 Settings ===
        self.h1_lookback_candles: int = self.config.get('h1_lookback_candles', 20)

        # === M5 Fractal Settings ===
        self.m5_fractal_n: int = self.config.get('m5_fractal_n', 2)

        # === OB Settings (Section 3.2 Gate 3) ===
        self.ob_body_min_pct: float = self.config.get('ob_body_min_pct', 0.0005)   # 0.05% (v5.0 Aggressive)
        # v11.1: Replace retest_candles with ATR-based distance
        self.ob_max_distance_atr: float = self.config.get('ob_max_distance_atr', 1.0)

        # === Volume Settings ===
        self.volume_spike_min: float = self.config.get('volume_spike_min', 1.0)

        # === Score Thresholds ===
        self.score_threshold: int = self.config.get('score_threshold', 10)
        
        # v11.8: Store last score breakdown for logging
        self._last_score_breakdown: Dict = {}

        # v13.6: Persistent L1 (Layer 1) state — remember broken swing levels
        self._l1_broken_high: Optional[float] = None  # Price level where L1 bullish break occurred
        self._l1_broken_low: Optional[float] = None   # Price level where L1 bearish break occurred
        self._l1_break_time: Optional[datetime] = None  # When break occurred (for timeout)

        # === RR Threshold ===
        # === Session Volume Multipliers (v5.0 Aggressive — reduced) ===
        self.volume_mult_by_session = {
            'ASIA': 1.0,
            'LONDON': 1.1,
            'LONDON-NY': 1.1,
            'NY': 1.1,
            'ASIA-LATE': 1.0,
        }

    @log_errors
    @timed_metric("IPAAnalyzer.analyze")
    @retry(max_attempts=3, delay=0.1, backoff=2.0, exceptions=(Exception,))
    @circuit_breaker(failure_threshold=5, timeout=30.0, expected_exception=Exception)
    def analyze(self,
                candles_m5: pd.DataFrame,
                candles_h1: pd.DataFrame,
                current_price: float,
                session: str = 'LONDON',
                magnets: Optional[Dict[str, Any]] = None,
                binance_data: Optional[Dict[str, Any]] = None,
                # v27.0: Single Source of Truth parameters
                atr_m5: Optional[float] = None,
                h1_bias_result: Optional[Any] = None,
                  snapshot: Optional[Any] = None) -> Optional[IPAResult]:
        """
        Main entry point: analyze for IPA signal.

        Args:
            candles_m5: M5 OHLCV DataFrame (300 bars = 25 hours)
            candles_h1: H1 OHLCV DataFrame
            current_price: Current BTC price
            session: Trading session name
            magnets: Optional magnet levels from magnet_scanner (v10.0)
            binance_data: Optional dict with market data
            atr_m5: v27.0 - Optional ATR from MarketSnapshot (single source of truth)
            h1_bias_result: v27.0 - Optional H1BiasResult from H1BiasEngine

        Returns:
            IPAResult if all gates pass and score >= 10, else None
        """
        try:
            self.session = session
            self.current_price = current_price

            # Ensure enough data
            if len(candles_m5) < 50 or len(candles_h1) < 4:
                self.logger.debug(f"{self.log_prefix} Insufficient data: M5={len(candles_m5)}, H1={len(candles_h1)}")
                return None

            # === Calculate indicators (v27.0: use provided atr_m5 if available) ===
            self._prepare_indicators(candles_m5, candles_h1, atr_m5)

            # === Gate 1: H1 Bias (v13.4: 3-Layer) ===
            h1_result = self._check_h1_bias(candles_h1, candles_m5)
            # v25.0: Store for dashboard access
            self._last_h1_result = h1_result
            if h1_result is None:
                return None  # Log already in _check_h1_bias

            direction = h1_result['direction']
            h1_bias = h1_result['bias']
            # v13.4: Get score adjustment from bias level
            score_adjust = h1_result.get('score_adjust', 0)

            # === Gate 1.5: Overextended Filter (v14.6 - statistics based) ===
            # Statistical basis: 1.5% = 58% reversal point, 2.0% = 80% reversal
            ema20_h1 = h1_result.get('ema20', 0)
            h1_dist_pct = abs(current_price - ema20_h1) / ema20_h1 * 100 if ema20_h1 > 0 else 0

            # Overextended = LONG but price above EMA (or SHORT but below)
            overextended = (
                (direction == 'LONG' and current_price > ema20_h1) or
                (direction == 'SHORT' and current_price < ema20_h1)
            )

            self._overextended_penalty = 0
            if overextended and h1_dist_pct > 1.5:
                self.logger.info(f"{self.log_prefix} Gate 1.5: BLOCKED | Overextended {h1_dist_pct:.1f}%")
                return None
            elif overextended and h1_dist_pct > 1.0:
                self._overextended_penalty = -2
                self.logger.info(f"{self.log_prefix} Gate 1.5: WARNING | Overextended {h1_dist_pct:.1f}% → score -2")
            # v27.1: Gate 1.5 OK log removed — Dist shown in MARKET display

            # === Gate 2: M5 Structure Break ===
            m5_result = self._check_m5_structure(candles_m5, direction)
            if m5_result is None:
                return None  # Log already in _check_m5_structure

            m5_choch = m5_result['choch']
            m5_bos = m5_result['bos']

            # === Gate 2.5: M5 Current Structure Confirm (v18.8) ===
            # v18.8: เปลี่ยนจาก BLOCK → Score Penalty -2
            # ดึงค่าจาก _check_m5_structure แทนคำนวณใหม่
            self._m5_conflict_penalty = -2 if m5_result.get('m5_conflict', False) else 0

            # === Gate 3: Order Block ===
            ob_result = self._find_order_block(candles_m5, direction, m5_result['break_idx'])
            if ob_result is None:
                return None  # Log already in _find_order_block
            
            # === v16.4: Entry Quality Score (EQS) ===
            # วัดคุณภาพจุดเข้า: Pullback (ดี) vs Impulse (แย่)
            eqs = self._check_entry_quality(candles_m5, direction, m5_result['break_idx'])
            self._entry_quality_adj = eqs
            
            # Block impulse entries (EQS <= -2)
            if eqs <= -2:
                self.logger.info(f"{self.log_prefix} Entry Quality: BLOCKED | EQS {eqs} (impulse entry)")
                return None
            
            # === Gate 4: Liquidity Context ===
            liq_result = self._check_liquidity_context(candles_m5, direction, magnets)
            
            # v13.0: Pullback Integration for IPA
            # v18.9: Pullback ACTIVE + EQS >= 2 → allow (ไม่ต้อง M5 aligned)
            # EQS วัดจาก price action (retrace, EMA distance, volume, body) = พอแล้ว
            pullback = (binance_data or {}).get('pullback', {'status': 'NONE'})
            pb_status = pullback.get('status', 'NONE')
            
            if pb_status == 'ACTIVE':
                # v18.9: ใช้ EQS >= 2 อย่างเดียว (ลบ M5 aligned)
                eqs = getattr(self, '_entry_quality_adj', 0)
                
                if eqs >= 2:
                    # Pullback กำลังจบ: EQS สูง = price action บอกว่าจบแล้ว
                    liq_result['pullback_ending'] = True
                    self.logger.info(
                        f"{self.log_prefix} Gate 4: PULLBACK ACTIVE but EQS {eqs} → allow"
                    )
                else:
                    self.logger.info(
                        f"{self.log_prefix} Gate 4: FAILED | Pullback ACTIVE "
                        f"(EQS:{eqs} < 2)"
                    )
                    return None
            elif pb_status == 'ENDED':
                liq_result['pullback_ended'] = True  # Add bonus in scoring
            # v27.1: Gate 4 log — show only active confirmations
            confirms = [k for k in ['sweep_confirmed', 'ema_pullback', 'equal_levels', 'session_level', 'pullback_ending', 'pullback_ended'] if liq_result.get(k)]
            self.logger.info(f"{self.log_prefix} Gate 4: PASSED | {liq_result.get('zone_context', 'N/A')} | {'+'.join(confirms) if confirms else 'zone only'}")

            # === Gate 5: Session Volume Filter (v10.1: Soft Gate) ===
            volume_ok = self._check_session_volume(candles_m5, m5_result['break_idx'])
            # v27.1: Gate 5 soft log removed — never blocks, no actionable info

            # === Calculate Score ===
            score, breakdown = self._calculate_score(
                h1_result=h1_result,
                m5_result=m5_result,
                ob_result=ob_result,
                liq_result=liq_result,
                volume_ok=volume_ok,
                score_adjust=score_adjust  # v13.4: Gate 1 bias adjustment
            )
            self._last_score_breakdown = breakdown
            # v27.1: Score log — compact (full breakdown in signal_builder log)
            status = "✅" if score >= self.score_threshold else "❌"
            self.logger.info(f"{self.log_prefix} Score: {status} {score}/{self.score_threshold}")

            if score < self.score_threshold:
                return None

            # === Build Entry Zone ===
            entry_zone = self._build_entry_zone(ob_result, m5_result, direction)

            # === Get swing levels for SL/TP ===
            swings = self._get_swing_levels(candles_m5, direction)

            # === Get PDH/PDL ===
            pdh, pdl = self._get_pdh_pdl(candles_h1)

            # === Get H1 FVG boundary ===
            h1_fvg_boundary = self._get_h1_fvg_boundary(candles_h1, direction)

            # v26.4: Get layer directions from h1_result
            l0_dir = h1_result.get('l0_direction', '---')
            l1_dir = h1_result.get('l1_direction', '---')
            l2_dir = h1_result.get('l2_direction', '---')
            l3_dir = h1_result.get('l3_direction', '---')
            
            result = IPAResult(
                direction=direction,
                score=score,
                h1_bias=h1_bias,
                h1_bos=h1_result['bos'],
                h1_choch=h1_result['choch'],
                h1_fvg_unfilled=h1_result['fvg_unfilled'],
                m5_choch=m5_choch,
                m5_bos=m5_bos,
                ob_high=ob_result.get('ob_high') or ob_result.get('fvg_high', 0),
                ob_low=ob_result.get('ob_low') or ob_result.get('fvg_low', 0),
                ob_body_pct=ob_result['body_pct'],
                ob_mitigated=ob_result['mitigated'],
                ob_distance_atr=ob_result.get('ob_distance_atr', 999.0),  # v11.1
                fvg_high=ob_result.get('fvg_high'),
                fvg_low=ob_result.get('fvg_low'),
                fvg_overlap=ob_result['fvg_overlap'],
                sweep_confirmed=liq_result['sweep_confirmed'],
                sweep_candles_ago=liq_result['sweep_candles_ago'],
                volume_spike=volume_ok,
                zone_context=liq_result['zone_context'],
                volume_ratio=ob_result.get('volume_ratio', 1.0),
                atr_m5=self.atr_m5,
                entry_zone_min=entry_zone['min'],
                entry_zone_max=entry_zone['max'],
                swing_highs=swings['highs'],
                swing_lows=swings['lows'],
                pdh=pdh,
                pdl=pdl,
                h1_fvg_boundary=h1_fvg_boundary,
                session=session,
                score_breakdown=self._last_score_breakdown,
                # v26.4: Layer directions
                l0_direction=l0_dir,
                l1_direction=l1_dir,
                l2_direction=l2_dir,
                l3_direction=l3_dir
            )

            self.logger.debug(f"{self.log_prefix} Analysis result: {result}")
            return result

        except Exception as e:
            self.logger.error(f"{self.log_prefix} Analysis error: {e}", exc_info=True)
            return None

    def _prepare_indicators(self, candles_m5: pd.DataFrame, candles_h1: pd.DataFrame, atr_m5: Optional[float] = None):
        """Calculate indicators used throughout analysis.
        
        v27.0: If atr_m5 is provided (from MarketSnapshot), use it instead of calculating.
        """
        # ATR on M5 - use provided value or calculate
        self.atr_m5 = atr_m5 if atr_m5 is not None else self._calc_atr(candles_m5, period=14)

        # Volume stats
        self.avg_volume = candles_m5['volume'].iloc[-20:].mean()
        self.volume_ratio = candles_m5['volume'].iloc[-1] / self.avg_volume if self.avg_volume > 0 else 1.0

    def _check_h1_bias(self, candles_h1: pd.DataFrame, candles_m5: pd.DataFrame) -> Optional[Dict]:
        """
        Gate 1 v13.4: 3-Layer Bias Detection
        
        Layer 1: M5 close break H1 swing (TRIGGER)     → 5-15 นาที
        Layer 2: H1 EMA9 cross EMA20 (CONFIRM)         → 3-6 ชม.
        Layer 3: H1 EMA20 cross EMA50 (FULL CONFIRM)  → 6-12 ชม.
        
        PASS conditions:
          - Layer 1 + Layer 2: EARLY PASS (score -1)
          - Layer 2 + Layer 3: CONFIRMED PASS (full score)
          - Layer 1 + 2 + 3: STRONG PASS (score +1)
        
        FAIL conditions:
          - Layer 1 alone: FAIL (prevent stop hunt)
          - Layer 2 alone: FAIL
          - Layer 3 alone: FAIL (imply Layer 2)
          - No layers: FAIL
        """
        if len(candles_h1) < 50:
            self.logger.info(f"{self.log_prefix} Gate 1: FAILED | Not enough H1 candles ({len(candles_h1)} < 50)")
            return None

        closes_h1 = candles_h1['close'].values
        highs_h1 = candles_h1['high'].values
        lows_h1 = candles_h1['low'].values
        
        # === Calculate EMAs ===
        ema9 = pd.Series(closes_h1).ewm(span=9, adjust=False).mean().values
        ema20 = pd.Series(closes_h1).ewm(span=20, adjust=False).mean().values
        ema50 = pd.Series(closes_h1).ewm(span=50, adjust=False).mean().values

        last_close = closes_h1[-1]
        last_ema9 = ema9[-1]
        last_ema20 = ema20[-1]
        last_ema50 = ema50[-1]

        # === Layer 0: H1 Structure Bias (v17.3) - เร็วสุด ===
        h1_structure = self._detect_h1_structure_bias(candles_h1)
        layer0_bull = h1_structure == 'BULLISH'
        layer0_bear = h1_structure == 'BEARISH'

        # === Layer 3: EMA20 cross EMA50 (FULL CONFIRM - original) ===
        layer3_bull = last_ema20 > last_ema50 and last_close > last_ema20
        layer3_bear = last_ema20 < last_ema50 and last_close < last_ema20

        # === Layer 2: EMA9 cross EMA20 (CONFIRM) ===
        layer2_bull = last_ema9 > last_ema20
        layer2_bear = last_ema9 < last_ema20

        # === Layer 1: M5 close break H1 swing (TRIGGER) - Persistent (v13.6) ===
        # Detect H1 swing high/low (n=3 for major swings)
        h1_swing_highs = self._detect_swings(highs_h1, 'high', n=3)
        h1_swing_lows = self._detect_swings(lows_h1, 'low', n=3)
        
        last_h1_swing_high = h1_swing_highs[-1] if h1_swing_highs else None
        last_h1_swing_low = h1_swing_lows[-1] if h1_swing_lows else None
        
        # M5 close for layer 1 detection
        m5_close = candles_m5['close'].iloc[-1]
        m5_body = abs(candles_m5['close'].iloc[-1] - candles_m5['open'].iloc[-1])
        atr_h1 = self._calc_atr(candles_h1, period=14)
        
        # v13.6: Persistent L1 - track and remember break levels
        # Check if price breaks NEW high (update state)
        if last_h1_swing_high is not None and m5_close > last_h1_swing_high:
            if self._l1_broken_high is None or m5_close > self._l1_broken_high:
                self._l1_broken_high = last_h1_swing_high  # Remember the break level
                self._l1_break_time = datetime.now(timezone.utc)
        
        if last_h1_swing_low is not None and m5_close < last_h1_swing_low:
            if self._l1_broken_low is None or m5_close < self._l1_broken_low:
                self._l1_broken_low = last_h1_swing_low  # Remember the break level
                self._l1_break_time = datetime.now(timezone.utc)
        
        # Sustained check: price still above/below remembered break level
        layer1_bull = (
            self._l1_broken_high is not None
            and m5_close > self._l1_broken_high
            and m5_body > atr_h1 * 0.15
        )
        
        layer1_bear = (
            self._l1_broken_low is not None
            and m5_close < self._l1_broken_low
            and m5_body > atr_h1 * 0.15
        )
        
        # v13.6: Reset L1 state if price retests back through break level (stop hunt)
        # If price fell back below broken high → reset bullish state
        if self._l1_broken_high is not None and m5_close < self._l1_broken_high:
            # Price retested back down - reset
            self._l1_broken_high = None
        
        if self._l1_broken_low is not None and m5_close > self._l1_broken_low:
            # Price retested back up - reset
            self._l1_broken_low = None

        # === Determine bias level and direction ===
        bias_level = 'NONE'
        direction = None
        score_adjust = 0

        # BULLISH combos (v17.3: new L0 combos)
        if layer0_bull and layer1_bull and layer2_bull and layer3_bull:
            bias_level = 'STRONG'
            direction = 'LONG'
            score_adjust = +1
        elif layer0_bull and layer2_bull and layer3_bull:
            bias_level = 'CONFIRMED+'
            direction = 'LONG'
            score_adjust = 0
        elif layer0_bull and layer1_bull and layer2_bull:
            bias_level = 'STRONG_EARLY'
            direction = 'LONG'
            score_adjust = 0
        elif layer0_bull and layer2_bull:
            bias_level = 'EARLY_STRUCTURE'
            direction = 'LONG'
            score_adjust = -1
        elif layer1_bull and layer2_bull and layer3_bull:
            bias_level = 'STRONG'
            direction = 'LONG'
            score_adjust = +1
        elif layer2_bull and layer3_bull:
            bias_level = 'CONFIRMED'
            direction = 'LONG'
            score_adjust = 0
        elif layer1_bull and layer2_bull:
            bias_level = 'EARLY'
            direction = 'LONG'
            score_adjust = -1
        # BEARISH combos (v17.3: new L0 combos)
        elif layer0_bear and layer1_bear and layer2_bear and layer3_bear:
            bias_level = 'STRONG'
            direction = 'SHORT'
            score_adjust = +1
        elif layer0_bear and layer2_bear and layer3_bear:
            bias_level = 'CONFIRMED+'
            direction = 'SHORT'
            score_adjust = 0
        elif layer0_bear and layer1_bear and layer2_bear:
            bias_level = 'STRONG_EARLY'
            direction = 'SHORT'
            score_adjust = 0
        elif layer0_bear and layer2_bear:
            bias_level = 'EARLY_STRUCTURE'
            direction = 'SHORT'
            score_adjust = -1
        elif layer1_bear and layer2_bear and layer3_bear:
            bias_level = 'STRONG'
            direction = 'SHORT'
            score_adjust = +1
        elif layer2_bear and layer3_bear:
            bias_level = 'CONFIRMED'
            direction = 'SHORT'
            score_adjust = 0
        elif layer1_bear and layer2_bear:
            bias_level = 'EARLY'
            direction = 'SHORT'
            score_adjust = -1
        # FAIL - no valid combo
        else:
            direction = None
            bias_level = 'NONE'

        # === Log format ===
        l0 = "L0:STR" if (layer0_bull or layer0_bear) else "L0:---"
        l1 = "L1:BRK" if (layer1_bull or layer1_bear) else "L1:---"
        l2 = "L2:EMA9" if (layer2_bull or layer2_bear) else "L2:---"
        l3 = "L3:EMA50" if (layer3_bull or layer3_bear) else "L3:---"
        dir_display = "BULLISH" if direction == "LONG" else "BEARISH" if direction == "SHORT" else "NEUTRAL"
        gate1_status = "PASSED" if direction else "FAILED"

        # v27.1: Gate 1 detail log removed — bias/layers/EMAs shown in MARKET display
        # Only log PASS/FAIL status for gate flow tracking
        self.logger.info(f"{self.log_prefix} Gate 1: {gate1_status} | {dir_display} {bias_level} adj:{score_adjust:+d}")

        if direction is None:
            return None

        # Strong bias for backward compatibility (from EMA cross)
        strong_bias = layer3_bull or layer3_bear

        # v25.0: Store layer directions for dashboard
        l0_dir = 'BULLISH' if layer0_bull else 'BEARISH' if layer0_bear else 'NEUTRAL'
        l1_dir = 'BULLISH' if layer1_bull else 'BEARISH' if layer1_bear else 'NEUTRAL'
        l2_dir = 'BULLISH' if layer2_bull else 'BEARISH' if layer2_bear else 'NEUTRAL'
        l3_dir = 'BULLISH' if layer3_bull else 'BEARISH' if layer3_bear else 'NEUTRAL'

        return {
            'direction': direction,
            'bias': dir_display,
            'bias_level': bias_level,
            'score_adjust': score_adjust,
            'bos': True,
            'choch': strong_bias,
            'fvg_unfilled': self._check_h1_fvg(candles_h1, direction),
            'ema9': last_ema9,
            'ema20': last_ema20,
            'ema50': last_ema50,
            'strong_bias': strong_bias,
            'layer0_triggered': layer0_bull or layer0_bear,
            'layer1_triggered': layer1_bull or layer1_bear,
            'layer2_triggered': layer2_bull or layer2_bear,
            'layer3_triggered': layer3_bull or layer3_bear,
            # v25.0: Layer directions for frontend dashboard
            'l0_direction': l0_dir,
            'l1_direction': l1_dir,
            'l2_direction': l2_dir,
            'l3_direction': l3_dir,
            'h1_swing_high': last_h1_swing_high,
            'h1_swing_low': last_h1_swing_low,
        }

    def _detect_swings(self, values: np.ndarray, lookback: str = 'high', n: int = None) -> List[float]:
        """
        Detect swing highs/lows using simple pivot logic.
        
        Args:
            values: Array of price values (high or low)
            lookback: 'high' to detect swing highs, 'low' for swing lows
            n: Optional override for fractal period (uses self.m5_fractal_n if None)
        """
        if len(values) < 3:
            return []

        swings = []
        period = n if n is not None else self.m5_fractal_n

        for i in range(period, len(values) - period):
            if lookback == 'high':
                if all(values[i] > values[i - j] for j in range(1, period + 1)) and \
                   all(values[i] > values[i + j] for j in range(1, period + 1)):
                    swings.append(float(values[i]))
            else:
                if all(values[i] < values[i - j] for j in range(1, period + 1)) and \
                   all(values[i] < values[i + j] for j in range(1, period + 1)):
                    swings.append(float(values[i]))

        return swings

    def _detect_significant_swings_h1(self, candles_h1: pd.DataFrame) -> Tuple[List[float], List[float]]:
        """
        v17.4: H1 swing detection — ATR filtered + lookback 48 ชม.
        v17.5: เพิ่ม current price เป็น potential swing
        """
        # v17.4: จำกัด lookback 48 แท่ง H1 = 48 ชม.
        lookback = min(48, len(candles_h1))
        recent_h1 = candles_h1.iloc[-lookback:]

        atr = self._calc_atr(recent_h1, 14)
        min_swing_size = atr * 0.8

        raw_highs = self._detect_swings(recent_h1['high'].values, 'high', n=3)
        raw_lows = self._detect_swings(recent_h1['low'].values, 'low', n=3)

        sig_highs = []
        for sh in raw_highs:
            if not sig_highs or abs(sh - sig_highs[-1]) >= min_swing_size:
                sig_highs.append(sh)

        sig_lows = []
        for sl in raw_lows:
            if not sig_lows or abs(sl - sig_lows[-1]) >= min_swing_size:
                sig_lows.append(sl)

        # v17.5: เพิ่ม current price เป็น potential swing level
        current_price = recent_h1['close'].iloc[-1]
        current_low = recent_h1['low'].iloc[-1]
        current_high = recent_h1['high'].iloc[-1]

        # ถ้าราคาปัจจุบันต่ำกว่า last significant low → เป็น lower low ใหม่
        if len(sig_lows) > 0 and current_price < sig_lows[-1]:
            sig_lows.append(current_price)

        # ถ้าราคาปัจจุบันสูงกว่า last significant high → เป็น higher high ใหม่
        if len(sig_highs) > 0 and current_price > sig_highs[-1]:
            sig_highs.append(current_price)

        # v27.1: L0 debug log removed — structure shown in MARKET display (L0:B/S)

        return sig_highs, sig_lows

    def _detect_h1_structure_bias(self, candles_h1: pd.DataFrame) -> str:
        """
        v17.3: H1 bias จาก structure — เร็วกว่า EMA 3-6 ชม.
        """
        sig_highs, sig_lows = self._detect_significant_swings_h1(candles_h1)

        if len(sig_highs) < 2 or len(sig_lows) < 2:
            return 'NEUTRAL'

        higher_high = sig_highs[-1] > sig_highs[-2]
        higher_low = sig_lows[-1] > sig_lows[-2]
        lower_high = sig_highs[-1] < sig_highs[-2]
        lower_low = sig_lows[-1] < sig_lows[-2]

        if higher_high and higher_low:
            return 'BULLISH'
        elif lower_high and lower_low:
            return 'BEARISH'
        else:
            return 'NEUTRAL'
        # v27.1: L0 detail logs removed — shown in MARKET display

    def _check_h1_fvg(self, candles_h1: pd.DataFrame, direction: str) -> bool:
        """Check for unfilled H1 FVG in the direction of trade."""
        if len(candles_h1) < 3:
            return False

        for i in range(len(candles_h1) - 3):
            h1 = candles_h1.iloc[i:i + 3]
            if direction == 'LONG':
                # Bullish FVG: gap between candle 1 high and candle 3 low
                gap = h1.iloc[2]['low'] - h1.iloc[0]['high']
                if gap > 0 and h1.iloc[-1]['low'] > h1.iloc[0]['high']:
                    return True
            else:
                gap = h1.iloc[0]['low'] - h1.iloc[2]['high']
                if gap > 0 and h1.iloc[-1]['high'] < h1.iloc[0]['low']:
                    return True
        return False

    def _check_m5_structure(self, candles_m5: pd.DataFrame, direction: str) -> Optional[Dict]:
        """
        Gate 2 (Aggressive): M5 structure break.
        Uses n=2 for swing detection. Falls back to momentum (3 consecutive closes).
        Returns absolute candles_m5 index.
        """
        lookback = min(30, len(candles_m5) - 1)
        recent = candles_m5.iloc[-lookback:]

        highs = recent['high'].values
        lows = recent['low'].values
        closes = recent['close'].values
        volumes = recent['volume'].values
        avg_vol = volumes.mean()

        # v18.6: Use CLOSE n=3 for structure detection (body, not wick)
        # close = price market accepts, wick = noise/stop hunt
        # n=3 = swing must hold 15 min (3 candles) → stable structure
        m5_swing_highs = self._detect_swings(closes, 'high', n=3)
        m5_swing_lows = self._detect_swings(closes, 'low', n=3)

        m5_bos = False
        m5_choch = False
        break_idx_relative = -1

        if m5_swing_highs and m5_swing_lows:
            last_swing_high = m5_swing_highs[-1]
            last_swing_low = m5_swing_lows[-1]

            for i in range(len(closes) - 1, 0, -1):
                if direction == 'LONG' and closes[i] > last_swing_high:
                    m5_bos = True
                    break_idx_relative = i
                    break
                elif direction == 'SHORT' and closes[i] < last_swing_low:
                    m5_bos = True
                    break_idx_relative = i
                    break

        # Momentum fallback: 3 consecutive closes in direction
        if not m5_bos:
            for i in range(2, len(closes)):
                if direction == 'LONG':
                    if closes[i] > closes[i-1] > closes[i-2]:
                        m5_bos = True
                        break_idx_relative = i
                        break
                else:
                    if closes[i] < closes[i-1] < closes[i-2]:
                        m5_bos = True
                        break_idx_relative = i
                        break

        if not m5_bos:
            return None

        # Convert relative index to absolute candles_m5 index
        abs_start = len(candles_m5) - lookback
        break_idx_absolute = abs_start + break_idx_relative

        vol_ratio = volumes[break_idx_relative] / avg_vol if avg_vol > 0 else 1.0

        self.logger.info(f"{self.log_prefix} Gate 2: PASSED | {'CHoCH' if m5_choch else 'BOS'} at idx={break_idx_absolute}")

        # === Gate 2.5: M5 EMA Confirm (v18.8) ===
        # v18.8: เปลี่ยนจาก BLOCK → Score Penalty -2
        ema9_m5 = candles_m5['close'].ewm(span=9).mean().iloc[-1]
        ema20_m5 = candles_m5['close'].ewm(span=20).mean().iloc[-1]

        m5_conflict = False
        if direction == 'SHORT' and ema9_m5 > ema20_m5:
            m5_conflict = True
            self.logger.info(
                f"{self.log_prefix} Gate 2.5: WARNING | SHORT but M5 bullish "
                f"(EMA9:{ema9_m5:.0f} > EMA20:{ema20_m5:.0f}) → penalty -2"
            )

        if direction == 'LONG' and ema9_m5 < ema20_m5:
            m5_conflict = True
            self.logger.info(f"{self.log_prefix} Gate 2.5: WARNING | M5 conflict → -2")
        # v27.1: Gate 2.5 OK log removed — no actionable info

        return {
            'choch': m5_choch,
            'bos': m5_bos,
            'break_idx': break_idx_absolute,   # ABSOLUTE index into candles_m5
            'volume_ratio': vol_ratio,
            'm5_conflict': m5_conflict,  # v18.8: flag for score penalty
        }

    def _find_order_block(self, candles_m5: pd.DataFrame, direction: str,
                         break_idx: int) -> Optional[Dict]:
        """
        Gate 3 v11.1: Find Order Block before the structure break.

        OB = Last opposing candle(s) before the M5 structure break.
        Criteria:
          - Body size > 0.05% of price
          - NOT mitigated (price hasn't passed through it)
          - Price within ob_max_distance_atr (default 1.0 ATR) from OB
        """
        lookback = min(30, break_idx)
        before_break = candles_m5.iloc[max(0, break_idx - lookback):break_idx + 1]

        if len(before_break) < 2:
            self.logger.info(f"{self.log_prefix} Gate 3: FAILED | Not enough candles")
            return None

        ob_candidates = []

        for i, row in before_break.iterrows():
            body_size = abs(row['close'] - row['open'])
            body_pct = body_size / row['close']

            if body_pct >= self.ob_body_min_pct:
                if direction == 'LONG':
                    # Bearish candle (potential OB for LONG)
                    if row['close'] < row['open']:
                        ob_candidates.append({
                            'idx': before_break.index.get_loc(i),
                            'high': row['high'],
                            'low': row['low'],
                            'body_pct': body_pct,
                            'open': row['open'],
                            'close': row['close'],
                        })
                else:
                    # Bullish candle (potential OB for SHORT)
                    if row['close'] > row['open']:
                        ob_candidates.append({
                            'idx': before_break.index.get_loc(i),
                            'high': row['high'],
                            'low': row['low'],
                            'body_pct': body_pct,
                            'open': row['open'],
                            'close': row['close'],
                        })

        if not ob_candidates:
            self.logger.info(f"{self.log_prefix} Gate 3: FAILED | No OB candidates")
            return None

        # Use the most recent qualifying candle as OB
        ob = ob_candidates[-1]

        # Check if OB is mitigated (price passed through it)
        after_break = candles_m5.iloc[break_idx:]
        ob_high = ob['high']
        ob_low = ob['low']

        mitigated = False

        for i, row in after_break.iterrows():
            if direction == 'LONG':
                # For LONG OB: mitigated if price goes below OB low
                if row['low'] < ob_low:
                    mitigated = True
                    break
            else:
                if row['high'] > ob_high:
                    mitigated = True
                    break

        if mitigated:
            self.logger.debug(f"{self.log_prefix} OB mitigated, looking for FVG fallback")
            # Try FVG as fallback
            fvg_result = self._find_fvg(candles_m5, direction, break_idx)
            if fvg_result:
                # v10.3: Log FVG fallback as Gate 3 PASSED
                self.logger.info(f"{self.log_prefix} Gate 3: PASSED | FVG {fvg_result['fvg_low']:.0f}-{fvg_result['fvg_high']:.0f} | size:{fvg_result['fvg_size']:.0f}")
                return fvg_result
            self.logger.info(f"{self.log_prefix} Gate 3: FAILED | OB mitigated + no FVG")
            return None

        # v11.1: OB Distance check — replace retest_candles with ATR-based distance
        # v30.9: Check both wick AND close for OB distance
        atr = self._calc_atr(candles_m5, 14)
        close_price = float(candles_m5.iloc[-1]['close'])

        # v30.9: Check both wick and close for OB distance
        if direction == 'LONG':
            wick_price = float(candles_m5.iloc[-1]['low'])
            wick_touched = wick_price <= ob_high  # wick reached down into OB
            close_dist = abs(close_price - ob_low)
        else:
            wick_price = float(candles_m5.iloc[-1]['high'])
            wick_touched = wick_price >= ob_low  # wick reached up into OB
            close_dist = abs(close_price - ob_high)

        close_dist_atr = close_dist / atr if atr > 0 else 999

        # Determine OB distance: close inside OB = perfect, wick touched + close near = OK
        in_zone = ob_low <= close_price <= ob_high
        if in_zone:
            ob_distance_atr = 0.0  # close inside OB = perfect
        elif wick_touched and close_dist_atr <= 1.0:
            ob_distance_atr = close_dist_atr  # wick tested OB + close still near
        else:
            ob_distance_atr = close_dist_atr  # normal check

        if ob_distance_atr > self.ob_max_distance_atr:
            self.logger.info(f"{self.log_prefix} Gate 3: FAILED | OB too far ({ob_distance_atr:.1f} ATR)")
            return None

        # Check FVG overlap
        fvg = self._find_fvg(candles_m5, direction, break_idx)
        fvg_overlap = fvg is not None

        result = {
            'ob_high': ob_high,
            'ob_low': ob_low,
            'body_pct': ob['body_pct'],
            'mitigated': mitigated,
            'ob_distance_atr': ob_distance_atr,  # v11.1: replace retest_candles
            'fvg_overlap': fvg_overlap,
            'volume_ratio': 1.0,
        }

        if fvg:
            result['fvg_high'] = fvg['fvg_high']
            result['fvg_low'] = fvg['fvg_low']

        # v11.1: Log OB confirmation with ATR distance
        self.logger.info(f"{self.log_prefix} Gate 3: PASSED | OB {ob_low:.0f}-{ob_high:.0f} | body:{ob['body_pct']*100:.3f}% | dist:{ob_distance_atr:.1f}ATR")
        return result

    def _find_fvg(self, candles_m5: pd.DataFrame, direction: str,
                  break_idx: int) -> Optional[Dict]:
        """Find Fair Value Gap (FVG) — 3-candle imbalance."""
        if len(candles_m5) < break_idx + 3:
            self.logger.info(f"{self.log_prefix} Gate 3: FAILED | Insufficient candles for FVG")
            return None

        for i in range(break_idx, min(break_idx + 10, len(candles_m5) - 2)):
            candle1 = candles_m5.iloc[i]
            candle2 = candles_m5.iloc[i + 1]
            candle3 = candles_m5.iloc[i + 2]

            if direction == 'LONG':
                # Bullish FVG: gap between candle 1 high and candle 3 low
                gap = candle3['low'] - candle1['high']
                if gap > 0:
                    return {
                        'ob_high': candle1['high'],   # FVG top = candle1 high
                        'ob_low': candle3['low'],    # FVG bottom = candle3 low
                        'fvg_high': candle3['low'],
                        'fvg_low': candle1['high'],
                        'fvg_mid': (candle3['low'] + candle1['high']) / 2,
                        'fvg_size': gap,
                        'body_pct': gap / candle2['close'],
                        'mitigated': True,   # OB was mitigated → FVG fallback
                        'fvg_overlap': True,  # It's an FVG
                        'ob_distance_atr': self.ob_max_distance_atr + 0.1,  # v11.1
                        'volume_ratio': 1.0,
                    }
            else:
                gap = candle1['low'] - candle3['high']
                if gap > 0:
                    return {
                        'ob_high': candle3['high'],   # FVG top = candle3 high
                        'ob_low': candle1['low'],    # FVG bottom = candle1 low
                        'fvg_high': candle1['low'],
                        'fvg_low': candle3['high'],
                        'fvg_mid': (candle1['low'] + candle3['high']) / 2,
                        'fvg_size': gap,
                        'body_pct': gap / candle2['close'],
                        'mitigated': True,   # OB was mitigated → FVG fallback
                        'fvg_overlap': True,  # It's an FVG
                        'ob_distance_atr': self.ob_max_distance_atr + 0.1,  # v11.1
                        'volume_ratio': 1.0,
                    }
        self.logger.info(f"{self.log_prefix} Gate 3: FAILED | No FVG found")
        return None

    def _check_entry_quality(self, candles_m5: pd.DataFrame, direction: str, break_idx: int) -> int:
        """
        v16.4: Entry Quality Score (EQS) — วัดคุณภาพจุดเข้า: Pullback (ดี) vs Impulse (แย่)
        
        ใช้ retrace ratio เป็นหลัก (ไม่ใช่นับแท่ง)
        return: score -3 to +3
        
        Pullback entry (ดี):
          BOS เกิดนานแล้ว → ราคา retrace ≥ 50% → ใกล้ EMA → vol ต่ำ → body เล็ก
          → เข้าที่ OB หลัง pullback → ราคาไปต่อ → TP
        
        Impulse entry (แย่):
          BOS เพิ่งเกิด → ราคาแทบไม่ retrace (< 10%) → ไกล EMA → vol spike → body ใหญ่
          → เข้าที่ยอด/ก้น impulse → retrace → SL
        """
        atr = self.atr_m5
        if atr <= 0:
            return 0
        
        eqs = 0
        
        # Get post-BOS candles
        post_bos = candles_m5.iloc[break_idx:]
        bos_price = candles_m5.iloc[break_idx]['close']
        current_price = candles_m5.iloc[-1]['close']
        
        if len(post_bos) < 2:
            return 0
        
        # === 1. Retrace Ratio (สำคัญสุด — น้ำหนัก ±2) ===
        # วัดว่าราคา pullback กลับจาก high/low หลัง BOS แล้วกี่ %
        if direction == 'LONG':
            high_after = post_bos['high'].max()
            impulse = high_after - bos_price
            retrace = high_after - current_price
        else:
            low_after = post_bos['low'].min()
            impulse = bos_price - low_after
            retrace = current_price - low_after
        
        retrace_ratio = retrace / impulse if impulse > 0 else 0
        
        retrace_log = ""
        if retrace_ratio >= 0.5:
            eqs += 2    # pullback 50%+ = retest zone ดี
            retrace_log = "50%+"
        elif retrace_ratio >= 0.3:
            eqs += 1    # pullback 30% = เริ่ม retrace
            retrace_log = "30-50%"
        elif retrace_ratio < 0.1:
            eqs -= 2    # แทบไม่ retrace = ยอด/ก้น impulse
            retrace_log = "<10%"
        else:
            retrace_log = "10-30%"
        
        # === 2. M5 EMA20 Distance (น้ำหนัก ±1) ===
        ema20_m5 = candles_m5['close'].ewm(span=20).mean().iloc[-1]
        m5_dist = abs(current_price - ema20_m5) / atr
        
        ema_log = ""
        if m5_dist < 0.5:
            eqs += 1    # ใกล้ EMA = pullback zone
            ema_log = "near"
        elif m5_dist > 1.5:
            eqs -= 1    # ไกล EMA = impulse tip
            ema_log = "far"
        else:
            ema_log = "mid"
        
        # === 3. Volume Declining (น้ำหนัก ±1) ===
        recent = candles_m5.iloc[-5:]
        vols = recent['volume'].values
        vol_avg = candles_m5['volume'].iloc[-20:].mean()
        
        vol_log = ""
        if vol_avg > 0:
            vol_now = vols[-2:].mean()
            if vol_now < vol_avg * 0.7:
                eqs += 1    # volume declining = pullback
                vol_log = "low"
            elif vols[-1] > vol_avg * 1.5:
                eqs -= 1    # volume spike = impulse
                vol_log = "high"
            else:
                vol_log = "mid"
        
        # === 4. Body Size (น้ำหนัก ±1) ===
        last_body = abs(candles_m5.iloc[-1]['close'] - candles_m5.iloc[-1]['open'])
        
        body_log = ""
        if last_body < atr * 0.25:
            eqs += 1    # indecision/doji = pullback slowing
            body_log = "sm"
        elif last_body > atr * 0.6:
            eqs -= 1    # big body = impulse candle
            body_log = "lg"
        else:
            body_log = "md"
        
        # Log the EQS components
        self.logger.info(
            f"{self.log_prefix} Entry Quality: {eqs:+d}/3 | "
            f"Retrace:{retrace_log} | M5 EMA:{ema_log} | "
            f"Vol:{vol_log} | Body:{body_log}"
        )
        
        return max(-3, min(3, eqs))

    def _check_liquidity_context(self, candles_m5: pd.DataFrame,
                                  direction: str,
                                  magnets: Optional[Dict[str, Any]] = None) -> Dict:
        """
        Gate 4: Liquidity Context — Zone, Sweep, EMA Pullback, Equal Levels, Session Level.
        v10.0: Added EMA pullback, equal levels, session level confirmations.
        """
        result = {
            'zone_context': 'NEUTRAL',
            'sweep_confirmed': False,
            'sweep_candles_ago': 999,
            'ema_pullback': False,
            'equal_levels': False,
            'session_level': False,
        }

        # Check H1 range for zone context
        if len(candles_m5) >= 60:
            h1_equiv = candles_m5.iloc[-60:]
            h1_high = h1_equiv['high'].max()
            h1_low = h1_equiv['low'].min()
            h1_range = h1_high - h1_low
            h1_mid = h1_low + h1_range / 2

            current = self.current_price

            if direction == 'LONG':
                if current < h1_mid:  # v9.8: Below mid = DISCOUNT
                    result['zone_context'] = 'DISCOUNT'
            else:
                if current > h1_mid:  # v9.8: Above mid = PREMIUM
                    result['zone_context'] = 'PREMIUM'

        # Check for liquidity sweep (within last 10 candles)
        sweep_result = self._check_liquidity_sweep(candles_m5, direction)
        if sweep_result:
            result['sweep_confirmed'] = sweep_result['confirmed']
            result['sweep_candles_ago'] = sweep_result['candles_ago']

        # === v10.0: EMA Pullback ===
        atr = self._calc_atr(candles_m5, 14)
        ema20_m5 = candles_m5['close'].ewm(span=20).mean().iloc[-1]
        ema_dist = abs(self.current_price - ema20_m5) / atr if atr > 0 else 999
        last = candles_m5.iloc[-1]
        
        if direction == 'SHORT':
            near_ema = (self.current_price > ema20_m5) and (ema_dist <= 0.5)
            rejecting = last['close'] < last['open']
            if near_ema and rejecting:
                result['ema_pullback'] = True
        elif direction == 'LONG':
            near_ema = (self.current_price < ema20_m5) and (ema_dist <= 0.5)
            rejecting = last['close'] > last['open']
            if near_ema and rejecting:
                result['ema_pullback'] = True

        # === v10.0: Equal Levels ===
        if self._check_equal_levels(candles_m5, direction):
            result['equal_levels'] = True

        # === v10.0: Session Level Proximity ===
        if magnets and self._check_session_level(magnets, direction, atr):
            result['session_level'] = True

        return result

    def _check_liquidity_sweep(self, candles_m5: pd.DataFrame,
                                direction: str) -> Optional[Dict]:
        """Check for liquidity sweep within last 10 candles."""
        lookback = min(10, len(candles_m5) - 1)
        recent = candles_m5.iloc[-lookback:]

        highs = recent['high'].values
        lows = recent['low'].values
        closes = recent['close'].values

        # Find swing highs/lows
        swing_highs = self._detect_swings(highs, 'high')
        swing_lows = self._detect_swings(lows, 'low')

        if not swing_highs or not swing_lows:
            return None

        for i in range(len(recent) - 1, -1, -1):
            if direction == 'LONG':
                # Sweep: price spikes above swing high, then rejects back below
                if len(swing_highs) >= 1:
                    last_swing = swing_highs[-1]
                    if highs[i] > last_swing * 1.001:  # 0.1% above
                        # Check if subsequent candles rejected back
                        if i < len(recent) - 1:
                            if closes[i + 1] < last_swing:
                                return {
                                    'confirmed': True,
                                    'candles_ago': len(recent) - 1 - i,
                                }
            else:
                if len(swing_lows) >= 1:
                    last_swing = swing_lows[-1]
                    if lows[i] < last_swing * 0.999:  # 0.1% below
                        if i < len(recent) - 1:
                            if closes[i + 1] > last_swing:
                                return {
                                    'confirmed': True,
                                    'candles_ago': len(recent) - 1 - i,
                                }
        return None

    def _check_equal_levels(self, candles_m5: pd.DataFrame, direction: str) -> bool:
        """
        v10.0: Check for equal highs/lows (within 0.1 ATR tolerance).
        
        Returns True if >= 2 swing levels are within tolerance on correct side.
        """
        lookback = min(50, len(candles_m5) - 1)
        recent = candles_m5.iloc[-lookback:]
        atr = self._calc_atr(candles_m5, 14)
        tolerance = atr * 0.1

        swing_highs = self._detect_swings(recent['high'].values, 'high')
        swing_lows = self._detect_swings(recent['low'].values, 'low')

        if direction == 'SHORT':
            # Equal highs above current price
            for i, h1 in enumerate(swing_highs):
                for h2 in swing_highs[i+1:]:
                    if abs(h1 - h2) <= tolerance and h1 > self.current_price:
                        return True
        else:
            # Equal lows below current price
            for i, l1 in enumerate(swing_lows):
                for l2 in swing_lows[i+1:]:
                    if abs(l1 - l2) <= tolerance and l1 < self.current_price:
                        return True
        return False

    def _check_session_level(self, magnets: Dict[str, Any], direction: str, atr: float) -> bool:
        """
        v10.0: Check if price is near Tier 1-2 magnet (within 0.5 ATR).
        
        Returns True if near relevant magnet on correct side.
        """
        if not magnets:
            return False
        
        key = 'sell_magnets' if direction == 'LONG' else 'buy_magnets'
        for m in magnets.get(key, []):
            if m.get('tier', 9) <= 2:  # PDH/PDL/PWH/PWL/Asian only
                dist = abs(m['level'] - self.current_price)
                if dist <= atr * 0.5:
                    return True
        return False

    def _check_session_volume(self, candles_m5: pd.DataFrame, break_idx: int) -> bool:
        """
        Gate 5: Volume must meet session-adaptive threshold.
        v6.0: Soft gate - allows 80% of threshold (for marginal cases)
        v10.1: Weekend scale - reduces threshold by 50% on Sat/Sun
        """
        if break_idx < 0 or break_idx >= len(candles_m5):
            return False

        vol_mult = self.volume_mult_by_session.get(self.session, 1.3)
        avg_vol = candles_m5['volume'].iloc[-20:].mean()
        break_vol = candles_m5.iloc[break_idx]['volume']

        # v10.1: Weekend scale - volume ต่ำ 40-60% วันเสาร์-อาทิตย์
        is_weekend = datetime.now().weekday() >= 5
        weekend_scale = 0.5 if is_weekend else 1.0

        # v6.0: Soft gate - pass if >= 80% of threshold
        soft_threshold = avg_vol * vol_mult * 0.8 * weekend_scale
        if break_vol >= soft_threshold:
            self.logger.debug(f"{self.log_prefix} Gate 5: Vol {break_vol:.0f} >= {soft_threshold:.0f} (80% of {avg_vol*vol_mult*weekend_scale:.0f})")
            return True

        return break_vol >= avg_vol * vol_mult * weekend_scale

    def _calculate_score(self,
                        h1_result: Dict,
                        m5_result: Dict,
                        ob_result: Dict,
                        liq_result: Dict,
                        volume_ok: bool,
                        score_adjust: int = 0) -> Tuple[int, Dict]:
        """
        Calculate IPA score (max 20 points).
        See Section 3.3 of Architecture Plan.
        """
        score = 0
        breakdown = {}

        # === H1 Structure (max 6) ===
        if m5_result['choch']:
            score += 3
            breakdown['m5_choch'] = 3
        elif m5_result['bos']:
            score += 2
            breakdown['m5_bos'] = 2

        if h1_result['bos']:
            score += 3
            breakdown['h1_bos'] = 3
        elif h1_result['choch']:
            score += 2
            breakdown['h1_choch'] = 2
        elif h1_result['fvg_unfilled']:
            score += 1
            breakdown['h1_fvg'] = 1

        # === M5 Entry Quality (max 9) ===
        if ob_result.get('body_pct', 0) >= self.ob_body_min_pct:  # v9.8: use config (0.0005)
            score += 2
            breakdown['ob_quality'] = 2

        # v11.1: OB Distance quality — closer = better score
        ob_dist = ob_result.get('ob_distance_atr', 999)
        if ob_dist <= 0.1:
            # Price in OB zone = maximum quality
            score += 2
            breakdown['ob_zone_entry'] = 2
        elif ob_dist <= 0.5:
            score += 1
            breakdown['ob_close'] = 1

        if ob_result.get('fvg_overlap', False):
            score += 1
            breakdown['fvg_overlap'] = 1

        # === Liquidity & Context (max 9) — v10.0 ===
        if liq_result['sweep_confirmed'] and liq_result['sweep_candles_ago'] <= 10:
            score += 2
            breakdown['sweep'] = 2

        if liq_result['zone_context'] in ('DISCOUNT', 'PREMIUM'):
            score += 2
            breakdown['zone'] = 2

        # v10.0: EMA Pullback (+2)
        if liq_result.get('ema_pullback'):
            score += 2
            breakdown['ema_pullback'] = 2

        # v10.0: Equal Levels (+1)
        if liq_result.get('equal_levels'):
            score += 1
            breakdown['equal_levels'] = 1

        # v10.0: Session Level (+1)
        if liq_result.get('session_level'):
            score += 1
            breakdown['session_level'] = 1

        if volume_ok:
            score += 1
            breakdown['volume'] = 1

        # v13.4: Gate 1 bias level adjustment
        bias_adjust = score_adjust  # +1 (STRONG), 0 (CONFIRMED), -1 (EARLY)
        score += bias_adjust
        breakdown['bias_level'] = bias_adjust

        # v14.0: Overextended penalty
        overextended_penalty = getattr(self, '_overextended_penalty', 0)
        score += overextended_penalty
        if overextended_penalty != 0:
            breakdown['overextended'] = overextended_penalty

        # v14.0: Pullback ENDED bonus (+2)
        if liq_result.get('pullback_ended'):
            score += 2
            breakdown['pullback_ended'] = 2

        # v18.4: Pullback ENDING bonus (+2) — when ACTIVE + EQS >= 2 + M5 aligned
        if liq_result.get('pullback_ending'):
            score += 2
            breakdown['pullback_ending'] = 2

        # v16.4: Entry Quality Score (EQS) adjustment
        eqs_adj = getattr(self, '_entry_quality_adj', 0)
        if eqs_adj != 0:
            score += eqs_adj
            breakdown['entry_quality'] = eqs_adj

        # v18.8: M5 Conflict Penalty (Gate 2.5)
        m5_penalty = getattr(self, '_m5_conflict_penalty', 0)
        if m5_penalty != 0:
            score += m5_penalty
            breakdown['m5_conflict'] = m5_penalty

        # v28.0: M5 State penalty for IPA (trend-following in exhaustion = dangerous)
        m5_state = getattr(self, '_binance_data', {}).get('m5_state', 'RANGING') if hasattr(self, '_binance_data') else 'RANGING'
        if m5_state == 'EXHAUSTION':
            score -= 2
            breakdown['m5_exhaustion'] = -2
        elif m5_state == 'TRENDING':
            score += 1
            breakdown['m5_trending'] = 1

        breakdown['total'] = score
        return score, breakdown

    def _build_entry_zone(self, ob_result: Dict, m5_result: Dict,
                         direction: str) -> Dict:
        """Build entry zone from OB and FVG."""
        ob_high = ob_result.get('ob_high')
        ob_low = ob_result.get('ob_low')
        fvg_high = ob_result.get('fvg_high')
        fvg_low = ob_result.get('fvg_low')

        if direction == 'LONG':
            zone_low = ob_low if ob_low else (fvg_low or self.current_price * 0.999)
            zone_high = ob_high if ob_high else (fvg_high or self.current_price * 0.9995)
        else:
            zone_low = ob_low if ob_low else (fvg_low or self.current_price * 0.9995)
            zone_high = ob_high if ob_high else (fvg_high or self.current_price * 1.001)

        return {'min': zone_low, 'max': zone_high}

    def _get_swing_levels(self, candles_m5: pd.DataFrame,
                         direction: str) -> Dict[str, List[float]]:
        """Get swing highs/lows for SL/TP calculation."""
        lookback = min(self.swing_lookback_candles, len(candles_m5) - 1)
        recent = candles_m5.iloc[-lookback:]

        highs = recent['high'].values
        lows = recent['low'].values

        swing_highs = self._detect_swings(highs, 'high')
        swing_lows = self._detect_swings(lows, 'low')

        # Filter: only include levels not too close to current price
        threshold_pct = 0.002  # 0.2%

        if direction == 'LONG':
            swing_highs = [s for s in swing_highs if s > self.current_price * (1 + threshold_pct)]
            swing_lows = [s for s in swing_lows if s < self.current_price * (1 - threshold_pct)]
        else:
            swing_highs = [s for s in swing_highs if s > self.current_price * (1 + threshold_pct)]
            swing_lows = [s for s in swing_lows if s < self.current_price * (1 - threshold_pct)]

        return {'highs': sorted(swing_highs), 'lows': sorted(swing_lows)}

    def _get_pdh_pdl(self, candles_h1: pd.DataFrame) -> Tuple[Optional[float], Optional[float]]:
        """Get Previous Day High/Low from H1 candles."""
        if len(candles_h1) < 25:
            return None, None

        # Last 24 H1 candles = previous day
        last_day = candles_h1.iloc[-24:]
        pdh = float(last_day['high'].max())
        pdl = float(last_day['low'].min())

        return pdh, pdl

    def _get_h1_fvg_boundary(self, candles_h1: pd.DataFrame,
                            direction: str) -> Optional[float]:
        """Get unfilled H1 FVG boundary."""
        if len(candles_h1) < 3:
            return None

        for i in range(len(candles_h1) - 3):
            h1 = candles_h1.iloc[i:i + 3]
            if direction == 'LONG':
                gap = h1.iloc[2]['low'] - h1.iloc[0]['high']
                if gap > 0 and h1.iloc[-1]['low'] > h1.iloc[0]['high']:
                    return float(h1.iloc[2]['low'])
            else:
                gap = h1.iloc[0]['low'] - h1.iloc[2]['high']
                if gap > 0 and h1.iloc[-1]['high'] < h1.iloc[0]['low']:
                    return float(h1.iloc[2]['high'])
        return None

    # === v18.3: LC + LR Layers (Gate 1 Enhancement) ===

    def _detect_h1_candle_bias(self, candles_h1: pd.DataFrame) -> str:
        """
        H1 Candle Bias — pattern + confirm (2 แท่ง)
        Hammer/Engulfing + แท่งถัดไป confirm
        
        Returns: 'BULLISH', 'BEARISH', or 'NEUTRAL'
        """
        if len(candles_h1) < 3:
            return 'NEUTRAL'

        prev2 = candles_h1.iloc[-3]  # pattern candle
        prev = candles_h1.iloc[-2]     # confirm candle
        atr = self._calc_atr(candles_h1, 14)
        if atr <= 0:
            return 'NEUTRAL'

        # === Hammer + confirm (Bullish) ===
        hammer_range = prev2['high'] - prev2['low']
        if hammer_range > 0:
            lower_wick = min(prev2['open'], prev2['close']) - prev2['low']
            is_hammer = (lower_wick / hammer_range > 0.6
                         and prev2['close'] > prev2['open']
                         and abs(prev2['close'] - prev2['open']) > atr * 0.1)
            if is_hammer and prev['close'] > prev2['high']:
                return 'BULLISH'

        # === Shooting Star + confirm (Bearish) ===
        if hammer_range > 0:
            upper_wick = prev2['high'] - max(prev2['open'], prev2['close'])
            is_shooting = (upper_wick / hammer_range > 0.6
                           and prev2['close'] < prev2['open']
                           and abs(prev2['close'] - prev2['open']) > atr * 0.1)
            if is_shooting and prev['close'] < prev2['low']:
                return 'BEARISH'

        # === Bullish Engulfing + confirm ===
        if (prev2['close'] < prev2['open']
            and prev['close'] > prev['open']
            and prev['close'] > prev2['open']
            and prev['open'] < prev2['close']
            and candles_h1.iloc[-1]['close'] > prev['close']):
            return 'BULLISH'

        # === Bearish Engulfing + confirm ===
        if (prev2['close'] > prev2['open']
            and prev['close'] < prev['open']
            and prev['close'] < prev2['open']
            and prev['open'] > prev2['close']
            and candles_h1.iloc[-1]['close'] < prev['close']):
            return 'BEARISH'
        # v27.1: LC detail logs removed — shown in MARKET display (LC:B/S)

        return 'NEUTRAL'

    def _detect_early_reversal_confluence(self, candles_h1: pd.DataFrame, candles_m5: pd.DataFrame,
                                          binance_data: dict, wall_scan: dict) -> Tuple[str, int]:
        """
        Early Reversal Confluence (4 signals — ไม่ซ้ำกับ LC/L1):
        1. Volume climax → decline (H1)
        2. DER shift
        3. Wall dominant (raw ratio ≥ 2x)
        4. OI declining
        
        Returns: ('BULLISH', count) or ('BEARISH', count) or ('NEUTRAL', count)
        """
        bull = 0
        bear = 0

        # 1. Volume climax → decline (H1)
        if len(candles_h1) >= 5:
            vols = candles_h1['volume'].iloc[-5:].values
            vol_peak = max(vols[:-2])
            vol_now = (vols[-1] + vols[-2]) / 2
            vol_avg = candles_h1['volume'].iloc[-20:].mean()
            if vol_peak > vol_avg * 2.0 and vol_now < vol_peak * 0.6:
                if candles_h1.iloc[-1]['close'] > candles_h1.iloc[-1]['open']:
                    bull += 1
                else:
                    bear += 1

        # 2. DER shift (from IOF delta data)
        der_dir = binance_data.get('der_direction', None)
        if der_dir == 'LONG':
            bull += 1
        elif der_dir == 'SHORT':
            bear += 1

        # 3. Wall dominant (raw ratio ≥ 2x)
        if wall_scan:
            raw_dom = wall_scan.get('raw_dominant', 'NONE')
            raw_ratio = wall_scan.get('raw_ratio', 1)
            if raw_dom == 'BID' and raw_ratio >= 2.0:
                bull += 1
            elif raw_dom == 'ASK' and raw_ratio >= 2.0:
                bear += 1

        # 4. OI declining (positions closing)
        oi = binance_data.get('oi', 0)
        oi_prev = binance_data.get('oi_1min_ago', 0)
        if oi > 0 and oi_prev > 0:
            oi_change = (oi - oi_prev) / oi_prev
            if oi_change < -0.001:
                if candles_m5.iloc[-1]['close'] > candles_m5.iloc[-3]['close']:
                    bull += 1
                else:
                    bear += 1
        # v27.1: LR detail logs removed — result shown in MARKET display (LR:B/S count/4)

        if bull >= 3:
            return 'BULLISH', bull
        elif bear >= 3:
            return 'BEARISH', bear
        else:
            return 'NEUTRAL', max(bull, bear)

    def _calc_gate1_with_lc_lr(self, candles_h1: pd.DataFrame, candles_m5: pd.DataFrame,
                                binance_data: dict, wall_scan: dict) -> Tuple[str, str, int]:
        """
        v18.3: Gate 1 with LC + LR Layers for EARLY reversal detection.
        
        Returns: (direction, bias_level, score_adjust)
        
        Combo Matrix:
        | LC | LR (≥3) | L2 | Level | Score |
        |---|---|---|---|---|
        | ✅ | ✅ | — | LEADING_REVERSAL | -1 |
        | ✅ | ✅ | ✅ | REVERSAL_EARLY | 0 |
        """
        # LC: H1 Candle Bias
        lc_bias = self._detect_h1_candle_bias(candles_h1)
        
        # LR: Early Reversal Confluence
        lr_bias, lr_count = self._detect_early_reversal_confluence(
            candles_h1, candles_m5, binance_data, wall_scan
        )
        
        # L2: EMA9 bias (from existing _check_h1_bias)
        closes_h1 = candles_h1['close'].values
        ema9 = pd.Series(closes_h1).ewm(span=9, adjust=False).mean().values[-1]
        ema20 = pd.Series(closes_h1).ewm(span=20, adjust=False).mean().values[-1]
        l2_bull = ema9 > ema20
        l2_bear = ema9 < ema20
        
        # === Determine combo ===
        lc_active = lc_bias in ('BULLISH', 'BEARISH')
        lr_active = lr_count >= 3
        
        # LEADING_REVERSAL: LC + LR ≥ 3 (ไม่ต้อง L2!)
        if lc_active and lr_active and not (l2_bull or l2_bear):
            direction = 'LONG' if lc_bias == 'BULLISH' else 'SHORT'
            self.logger.info(
                f"{self.log_prefix} Gate 1: LEADING_REVERSAL | "
                f"LC:{lc_bias} LR:{lr_count}/4 L2:--- → {direction} (score:-1)"
            )
            return direction, 'LEADING_REVERSAL', -1
        
        # REVERSAL_EARLY: LC + LR ≥ 3 + L2
        if lc_active and lr_active:
            l2_ok = (lc_bias == 'BULLISH' and l2_bull) or (lc_bias == 'BEARISH' and l2_bear)
            if l2_ok:
                direction = 'LONG' if lc_bias == 'BULLISH' else 'SHORT'
                self.logger.info(
                    f"{self.log_prefix} Gate 1: REVERSAL_EARLY | "
                    f"LC:{lc_bias} LR:{lr_count}/4 L2:OK → {direction} (score:0)"
                )
                return direction, 'REVERSAL_EARLY', 0
        
        # No LC/LR combo - return None (use standard Gate 1)
        return None, 'NONE', 0

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

    @property
    def swing_lookback_candles(self) -> int:
        return self.config.get('swing_lookback_candles', 20)











