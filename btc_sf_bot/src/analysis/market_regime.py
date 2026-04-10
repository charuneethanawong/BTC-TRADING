"""
Market Regime Detector — v4.9 M5 Upgrade
Detects: TRENDING | RANGING | VOLATILE | DEAD

Used by:
  - IPA Analyzer (suitable when H1 has clear trend, ADX > 15)
  - IOF Analyzer (suitable when NOT extreme trend, ADX < 40)
"""
from dataclasses import dataclass
from typing import Optional, Tuple
import pandas as pd
import numpy as np

from src.utils.logger import get_logger
from src.utils.decorators import retry, circuit_breaker, log_errors
from src.utils.metrics import timed_metric

logger = get_logger(__name__)


@dataclass
class RegimeResult:
    """Result of regime detection. v34.3: Added CHOPPY regime + regime_confidence."""
    regime: str           # 'TRENDING' | 'WEAKENING' | 'RANGING' | 'VOLATILE' | 'CHOPPY' | 'DEAD'
    regime_confidence: str  # 'HIGH' | 'LOW' - v34.3: cross-check with M5 state
    atr_h1: float         # ATR(14) on H1
    adx_h1: float         # ADX(14) on H1
    bb_width: float       # Bollinger Band width on M5
    is_ipa_suitable: bool # True if H1 has trend bias (ADX > 15)
    is_iof_suitable: bool # True if NOT extreme trend (ADX < 40) or WEAKENING
    plus_di: float        # +DI(14) from ADX calculation
    minus_di: float       # -DI(14) from ADX calculation
    di_spread: float      # |plus_di - minus_di|
    atr_ratio: float      # ATR(5)/ATR(14) - volatility contraction

    def __str__(self) -> str:
        return (f"Regime: {self.regime} [{self.regime_confidence}] | ADX:{self.adx_h1:.1f} | "
                f"+DI:{self.plus_di:.0f} -DI:{self.minus_di:.0f} (spread:{self.di_spread:.1f}) | "
                f"ATR_R:{self.atr_ratio:.2f} | IPA:{self.is_ipa_suitable} | IOF:{self.is_iof_suitable}")


class MarketRegimeDetector:
    """
    Detects market regime using ADX, ATR, and Bollinger Band width.

    Regime Logic:
      - TRENDING:  ADX > 40 (strong directional move)
      - RANGING:   ADX 20-40 (consolidation, good for mean-reversion)
      - VOLATILE:  ADX 20-40 + BB width > 2σ (high volatility, breakout possible)
      - DEAD:      BB width < threshold (very low volatility)
    """

    def __init__(self, config: dict = None):
        self.config = config or {}

        # ATR settings
        self.atr_period: int = self.config.get('atr_period', 14)
        self.atr_ma_period: int = self.config.get('atr_ma_period', 20)

        # ADX settings
        self.adx_period: int = self.config.get('adx_period', 14)
        self.adx_strong_threshold: float = self.config.get('adx_strong_threshold', 40.0)  # TRENDING
        self.adx_active_threshold: float = self.config.get('adx_active_threshold', 20.0)  # RANGING

        # Bollinger Band settings
        # v30.6: Calibrated for BTC M5 - BB width is ~1-2% not 50-200%
        self.bb_period: int = self.config.get('bb_period', 20)
        self.bb_std: float = self.config.get('bb_std', 2.0)
        self.bb_volatile_threshold: float = self.config.get('bb_volatile_threshold', 0.015)  # was 2.0
        self.bb_dead_threshold: float = self.config.get('bb_dead_threshold', 0.003)          # was 0.5

        # Suitability thresholds
        self.ipa_adx_min: float = self.config.get('ipa_adx_min', 15.0)   # Min ADX for IPA
        self.iof_adx_max: float = self.config.get('iof_adx_max', 40.0)  # Max ADX for IOF

        # v27.1: WEAKENING thresholds (from config)
        self.weakening_di_spread_max: float = self.config.get('weakening_di_spread_max', 10.0)
        self.weakening_atr_ratio_max: float = self.config.get('weakening_atr_ratio_max', 0.75)
        
        # v34.3: CHOPPY regime thresholds
        self.bb_choppy_threshold: float = self.config.get('bb_choppy_threshold', 0.008)  # >0.8% BB width = CHOPPY

    @log_errors
    @timed_metric("MarketRegimeDetector.detect")
    @retry(max_attempts=3, delay=0.1, backoff=2.0, exceptions=(Exception,))
    @circuit_breaker(failure_threshold=5, timeout=30.0, expected_exception=Exception)
    def detect(self, candles_m5: pd.DataFrame,
                candles_h1: Optional[pd.DataFrame],
                m5_state: str = None,
                er_short: float = None,
                er_long: float = None) -> RegimeResult:
        """
        Detect market regime from M5 and H1 candles.
        v11.1: Robust handling for missing H1 data (candles_h1=None).
        v34.3: Added m5_state parameter for regime_confidence cross-check.
        v39.0: Added er_short/er_long for TRENDING regime detection.
               If not provided, calculates ER from candles_m5 directly.

        Args:
            candles_m5: DataFrame with M5 OHLCV data
            candles_h1: DataFrame with H1 OHLCV data (can be None)
            m5_state: M5 state from MarketSnapshot ('TRENDING', 'PULLBACK', etc.) - for confidence check
            er_short: Efficiency Ratio short (0-1) - for TRENDING detection (auto-calculated if None)
            er_long: Efficiency Ratio long (0-1) - for TRENDING detection (auto-calculated if None)

        Returns:
            RegimeResult with regime classification and suitability flags
        """
        try:
            # 1. Calculate M5 BB width (ATR M5 moved to MarketSnapshot)
            if candles_m5 is not None and not candles_m5.empty:
                bb_width = self._calc_bb_width(candles_m5, self.bb_period)
                
                # v39.0: Calculate ER from M5 candles if not provided
                if er_short is None or er_long is None:
                    er_short, er_long = self._calc_er_from_m5(candles_m5)
            else:
                bb_width = 1.0
                er_short = er_short or 0.5
                er_long = er_long or 0.5

            # 2. Calculate H1 indicators (handle missing data)
            if candles_h1 is not None and not candles_h1.empty and len(candles_h1) > self.atr_period:
                atr_h1 = self._calc_atr(candles_h1, self.atr_period)
                # v27.0: Get ADX with DI values
                adx_h1, plus_di, minus_di = self._calc_adx_full(candles_h1, self.adx_period)
            else:
                # Use safe defaults if H1 data missing
                atr_h1 = 100.0
                adx_h1 = 25.0
                plus_di = minus_di = 15.0
            
            # v27.0: Calculate DI spread and ATR ratio
            di_spread = abs(plus_di - minus_di)
            atr_ratio = self._calc_atr_ratio(candles_m5)

            # 3. Classify regime (v39.0: include ER for TRENDING detection)
            max_er = max(er_short, er_long)
            regime = self._classify_regime(adx_h1, bb_width, di_spread, atr_ratio, max_er)

            # 4. Determine suitability
            is_ipa_suitable = adx_h1 > self.ipa_adx_min
            # v27.0: IOF suitable if ADX < 40 OR regime is WEAKENING
            # v35.4: IOF/IOFF works in ALL regimes except DEAD (gates filter)
            is_iof_suitable = adx_h1 < self.iof_adx_max or regime == 'WEAKENING'

            # 5. v34.3: Calculate regime_confidence (cross-check H1 regime vs M5 state)
            regime_confidence = self._calc_regime_confidence(regime, m5_state)

            result = RegimeResult(
                regime=regime,
                regime_confidence=regime_confidence,
                atr_h1=atr_h1,
                adx_h1=adx_h1,
                bb_width=bb_width,
                is_ipa_suitable=is_ipa_suitable,
                is_iof_suitable=is_iof_suitable,
                plus_di=plus_di,
                minus_di=minus_di,
                di_spread=di_spread,
                atr_ratio=atr_ratio,
            )

            logger.debug(f"[Regime] {result}")
            return result

        except Exception as e:
            logger.error(f"[Regime] Detection error: {e}", exc_info=True)
            # Return safe defaults
            return RegimeResult(
                regime='RANGING',
                regime_confidence='LOW',
                atr_h1=100.0,
                adx_h1=25.0,
                bb_width=1.0,
                is_ipa_suitable=True,
                is_iof_suitable=True,
                plus_di=15.0,
                minus_di=15.0,
                di_spread=0.0,
                atr_ratio=1.0,
            )

    def _classify_regime(self, adx: float, bb_width: float, di_spread: float = 0, atr_ratio: float = 1.0, max_er: float = 0.5) -> str:
        """
        Classify market regime based on ADX, DI spread, ATR ratio, BB width, and ER.
        v34.3: Added CHOPPY regime for ADX <= 20 with BB > 0.008
        v39.0: Added max_er for TRENDING detection (ADX 22-40 + ER > 0.7)

        Decision tree (BTC-calibrated):
          WEAKENING:    ADX > 40 + DI spread < 10 + ATR ratio < 0.75
          TRENDING:     ADX > 22 + max_er > 0.7 (strong momentum with efficiency)
          ADX > 20:
            BB > 0.015  → VOLATILE (breakout, range > $1000)
            BB <= 0.015 → RANGING (consolidation)
          ADX <= 20:
            BB > 0.008  → CHOPPY (fake breaks, whipsaw) [NEW v34.3]
            BB < 0.003  → DEAD (very low volatility, range < $200)
            else        → RANGING (flat sideway)
        """
        # v27.1: WEAKENING check first (strong trend but losing momentum)
        if adx > self.adx_strong_threshold and di_spread < self.weakening_di_spread_max and atr_ratio < self.weakening_atr_ratio_max:
            return 'WEAKENING'
        
        # v39.0: TRENDING check (ADX > 22 + ER > 0.7)
        # This catches trends before ADX reaches 40, using ER for confirmation
        if adx > 22 and max_er > 0.7:
            return 'TRENDING'
        elif adx > self.adx_strong_threshold:
            # ADX > 40 but ER not high enough = possible exhaustion
            return 'TRENDING'
        elif adx > self.adx_active_threshold:
            if bb_width > self.bb_volatile_threshold:
                return 'VOLATILE'
            else:
                return 'RANGING'
        else:
            # v34.3: ADX <= 20 - split into CHOPPY / RANGING / DEAD
            if bb_width > self.bb_choppy_threshold:
                return 'CHOPPY'  # Fake breaks, whipsaw
            elif bb_width < self.bb_dead_threshold:
                return 'DEAD'   # Very low volatility
            else:
                return 'RANGING'  # Flat sideway
    
    def _calc_regime_confidence(self, regime: str, m5_state: str = None) -> str:
        """
        v34.3: Calculate regime confidence by cross-checking H1 regime vs M5 state.
        
        HIGH = H1 regime and M5 state are aligned (both trending or both ranging)
        LOW  = H1 RANGING but M5 TRENDING (contradiction - structure not reliable)
        
        Args:
            regime: H1 regime from _classify_regime
            m5_state: M5 state from MarketSnapshot
            
        Returns:
            'HIGH' or 'LOW'
        """
        if m5_state is None:
            return 'HIGH'  # Default to high confidence if no M5 state
        
        # Alignable states: TRENDING, VOLATILE
        # Non-alignable: PULLBACK, EXHAUSTION, ACCUMULATION, SIDEWAY
        trending_states = {'TRENDING', 'VOLATILE', 'CHOPPY'}
        sideway_states = {'PULLBACK', 'EXHAUSTION', 'ACCUMULATION', 'SIDEWAY', 'RANGING'}
        
        h1_trending = regime in trending_states
        m5_trending = m5_state in trending_states
        
        # If both trending or both sideway → HIGH confidence
        if h1_trending == m5_trending:
            return 'HIGH'
        
        # If H1 RANGING but M5 TRENDING → LOW confidence (contradiction)
        # If H1 TRENDING but M5 sideway → also contradiction
        return 'LOW'
    
    def _calc_atr_ratio(self, candles: pd.DataFrame, short_period: int = 5, long_period: int = 14) -> float:
        """v27.0: Calculate ATR ratio = ATR(short) / ATR(long) for volatility contraction detection."""
        if candles is None or len(candles) < long_period + 1:
            return 1.0
        
        atr_short = self._calc_atr(candles, short_period)
        atr_long = self._calc_atr(candles, long_period)
        
        if atr_long == 0:
            return 1.0
        
        return atr_short / atr_long

    def _calc_atr(self, candles: pd.DataFrame, period: int = 14) -> float:
        """
        Calculate ATR (Average True Range).

        ATR = Average of True Range over period
        True Range = max(High-Low, |High-PrevClose|, |Low-PrevClose|)
        """
        if candles is None or len(candles) < period + 1:
            return 100.0  # Safe default for BTC

        high = candles['high'].values
        low = candles['low'].values
        close = candles['close'].values

        # True Range
        tr1 = high - low                          # Current high - current low
        tr2 = np.abs(high - np.roll(close, 1))    # |High - Previous Close|
        tr3 = np.abs(low - np.roll(close, 1))     # |Low - Previous Close|

        # Fix first element (no previous close available — use H-L as safe proxy)
        tr2[0] = tr1[0]
        tr3[0] = tr1[0]

        # Now all three arrays have the same length (300,) — safe to max together
        tr = np.maximum(tr1, np.maximum(tr2, tr3))

        # Average
        atr = pd.Series(tr).rolling(window=period).mean().iloc[-1]
        return float(atr) if not np.isnan(atr) else 100.0

    def _calc_adx_full(self, candles: pd.DataFrame, period: int = 14) -> Tuple[float, float, float]:
        """
        Calculate ADX using Wilder's Sum method (v26.4).
        v27.0: Returns ADX, +DI, -DI.

        ADX measures trend strength regardless of direction.
        Range: 0-100
          - ADX < 20: Weak/No trend
          - ADX 20-40: Moderate trend
          - ADX 40+: Strong trend
          
        Returns:
            Tuple of (adx, plus_di, minus_di)
        """
        if candles is None or len(candles) < period * 2 + 1:
            return 25.0, 15.0, 15.0

        high = candles['high'].values.astype(float)
        low = candles['low'].values.astype(float)
        close = candles['close'].values.astype(float)

        tr = np.maximum(high[1:] - low[1:],
             np.maximum(np.abs(high[1:] - close[:-1]), np.abs(low[1:] - close[:-1])))
        plus_dm = np.maximum(high[1:] - high[:-1], 0)
        minus_dm = np.maximum(low[:-1] - low[1:], 0)
        mask = plus_dm > minus_dm
        plus_dm = np.where(mask, plus_dm, 0)
        minus_dm = np.where(~mask, minus_dm, 0)

        def wilder_sum(arr, p):
            s = np.zeros(len(arr))
            s[p-1] = np.sum(arr[:p])
            for i in range(p, len(arr)):
                s[i] = s[i-1] - s[i-1]/p + arr[i]
            return s

        atr_s = wilder_sum(tr, period)
        pdm_s = wilder_sum(plus_dm, period)
        mdm_s = wilder_sum(minus_dm, period)

        valid = slice(period-1, None)
        plus_di_arr = 100 * pdm_s[valid] / np.maximum(atr_s[valid], 0.001)
        minus_di_arr = 100 * mdm_s[valid] / np.maximum(atr_s[valid], 0.001)

        dx = np.abs(plus_di_arr - minus_di_arr) / np.maximum(plus_di_arr + minus_di_arr, 0.001) * 100

        adx_arr = np.zeros(len(dx))
        if len(dx) >= period:
            adx_arr[period-1] = np.mean(dx[:period])
            for i in range(period, len(dx)):
                adx_arr[i] = (adx_arr[i-1] * (period-1) + dx[i]) / period
            adx = round(float(adx_arr[-1]), 1)
            plus_di = round(float(plus_di_arr[-1]), 1)
            minus_di = round(float(minus_di_arr[-1]), 1)
            return adx, plus_di, minus_di
        return 25.0, 15.0, 15.0

    def _calc_bb_width(self, candles: pd.DataFrame,
                       period: int = 20) -> float:
        """
        Calculate Bollinger Band width ratio.

        BB Width = (Upper Band - Lower Band) / Middle Band
        High width (>2.0) = high volatility
        Low width (<0.5) = low volatility (dead market)
        """
        if candles is None or len(candles) < period:
            return 1.0  # Safe default

        close = candles['close'].values

        # Calculate bands
        sma = pd.Series(close).rolling(window=period).mean().iloc[-1]
        std = pd.Series(close).rolling(window=period).std().iloc[-1]

        if sma == 0:
            return 1.0

        upper = sma + (std * self.bb_std)
        lower = sma - (std * self.bb_std)

        bb_width = (upper - lower) / sma
        return float(bb_width) if not np.isnan(bb_width) else 1.0

    def _calc_er_from_m5(self, candles: pd.DataFrame) -> Tuple[float, float]:
        """v39.0: Calculate Efficiency Ratio from M5 candles for regime detection."""
        if candles is None or len(candles) < 21:
            return 0.5, 0.5
        
        closes = candles['close'].values
        
        # ER short (10 candles)
        period_short = 10
        if len(closes) >= period_short + 1:
            change_short = abs(closes[-1] - closes[-period_short])
            volatility_short = sum(abs(closes[i] - closes[i-1]) for i in range(-period_short, 0))
            er_short = change_short / volatility_short if volatility_short > 0 else 0
        else:
            er_short = 0.5
        
        # ER long (20 candles)
        period_long = 20
        if len(closes) >= period_long + 1:
            change_long = abs(closes[-1] - closes[-period_long])
            volatility_long = sum(abs(closes[i] - closes[i-1]) for i in range(-period_long, 0))
            er_long = change_long / volatility_long if volatility_long > 0 else 0
        else:
            er_long = er_short
        
        return er_short, er_long

    def get_session_thresholds(self, session: str) -> dict:
        """
        Get session-specific threshold adjustments.

        ASIA: Lower volatility, tighter ranges
        LONDON/NY: Higher volatility, wider ranges
        """
        thresholds = {
            'ASIA': {
                'volume_mult': 1.2,    # Lower than standard (quieter market)
                'atr_mult': 0.8,        # Tighter ATR targets
                'adx_threshold': 15.0,  # Lower ADX threshold
            },
            'LONDON': {
                'volume_mult': 1.3,
                'atr_mult': 1.0,
                'adx_threshold': 20.0,
            },
            'LONDON-NY': {
                'volume_mult': 1.3,
                'atr_mult': 1.0,
                'adx_threshold': 20.0,
            },
            'NY': {
                'volume_mult': 1.3,
                'atr_mult': 1.0,
                'adx_threshold': 20.0,
            },
            'ASIA-LATE': {
                'volume_mult': 1.1,
                'atr_mult': 0.7,
                'adx_threshold': 15.0,
            },
        }
        return thresholds.get(session, thresholds['LONDON'])
