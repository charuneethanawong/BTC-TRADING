"""
HTF MSS Sync Analyzer
Syncs M15 signals with H1 Market Structure Shift (BOS/CHoCH)
"""
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timezone, timedelta
import pandas as pd
import numpy as np

from ..utils.logger import get_logger
from ..enums import TrendState, StructureStrength, StructureEvent
from src.utils.decorators import log_errors, retry, circuit_breaker
from src.utils.metrics import timed_metric

logger = get_logger(__name__)


class HTFMSSAnalyzer:
    """
    Higher Timeframe Market Structure Shift Analyzer.
    
    Syncs M15 signals with H1 structure for higher probability trades.
    
    Scoring:
    - H1 BOS in same direction: +2 pts
    - H1 CHoCH in same direction: +3 pts (reversal = stronger)
    - H1 structure aligned (no conflict): +1 pt
    - H1 structure opposed: -2 pts (reduces confidence)
    """
    
    def __init__(self, config: Dict = None):
        self.config = config or {}
        
        self.lookback = self.config.get('lookback', 300) # Increased lookback for h1 n=5
        self.bos_threshold = self.config.get('bos_threshold', 9)
        self.sync_weight = self.config.get('sync_weight', 1.5)
        
        # v4.0: Persistent Structure State (Sticky Trend)
        self.last_h1_trend = TrendState.RANGE
        self._last_stype = "NONE"
        self._last_labels = "NONE"
        self.last_h1_structure = None
        self.last_h1_level = 0
        self.last_h1_time: Optional[datetime] = None
        self.last_structural_labels = "NONE"
        
        self.h1_swings = {'highs': [], 'lows': []}
        self.displacement_threshold = self.config.get('displacement_threshold', 1.2)
        
        # v4.5: Range & Liquidity Tracking
        self.last_h1_range = {
            'high': 0.0,
            'low': 0.0,
            'is_active': False,
            'type': 'NONE' # BROADENING, CONTRACTING, EQUAL
        }
        self.confirmed_swings = {'high': None, 'low': None}
        
        # v4.7: Institutional Grade Structure Tracking
        self.last_protected_level: Optional[float] = None
        self.choch_internal_level: Optional[float] = None
        self._consecutive_structure_count: int = 0
        self._initial_scan_done: bool = False
        
        # v4.8: Configurable H1 fractal parameter
        # H1 uses n=5 (5 candles = 5 hours each side) for major structural swings
        self.h1_fractal_n: int = self.config.get('h1_fractal_n', 5)
        
        # v4.9: Multi-swing sequence tracking
        self._structure_strength: StructureStrength = StructureStrength.WEAK
        
        # P2.E: Time-based filter for H1 structure (ignore old structures)
        # Default to 168 hours (1 week) - set lower in config for aggressive filtering
        self.max_structure_age_hours = self.config.get('max_structure_age_hours', 168)
        self._structure_timestamps = []  # Track timestamps of recent structures
        
        # P2.B: Recency bias - weight recent structures more (0.0 to 1.0, higher = more recency)
        self.recency_weight = self.config.get('recency_weight', 0.7)
    
    def detect_initial_trend(self, candles_h1: pd.DataFrame) -> TrendState:
        """
        Scan historical data to detect initial trend when bot starts.
        This ensures the bot doesn't start from RANGE when the market
        has already established a clear trend.
        """
        if len(candles_h1) < 50:
            return TrendState.RANGE
        
        # Scan through recent candles to find the last confirmed BOS/CHoCH
        fractals = self._get_fractals(candles_h1, n=self.h1_fractal_n)
        if not fractals['highs'] or not fractals['lows']:
            logger.debug(f"[H1] No fractals found, defaulting to RANGE")
            return TrendState.RANGE
        
        major_highs = np.array([f['level'] for f in fractals['highs']])
        major_lows = np.array([f['level'] for f in fractals['lows']])
        recent_close = candles_h1['close'].iloc[-1]
        
        logger.debug(f"[H1_DEBUG] Initial scan: highs={major_highs[-3:]}, lows={major_lows[-3:]}, close={recent_close}")
        
        # v4.7: Check 2+ consecutive pairs for stronger trend detection
        if len(major_highs) >= 3 and len(major_lows) >= 3:
            # Count consecutive HH+HL and LH+LL pairs
            bull_seq = 0
            bear_seq = 0
            
            max_pairs = min(4, len(major_highs), len(major_lows))
            for i in range(1, max_pairs):  # Check up to 3 pairs
                hh = major_highs[-i] > major_highs[-i-1]
                hl = major_lows[-i] > major_lows[-i-1]
                lh = major_highs[-i] < major_highs[-i-1]
                ll = major_lows[-i] < major_lows[-i-1]
                
                if hh and hl:
                    if bear_seq > 0:
                        break
                    bull_seq += 1
                elif lh and ll:
                    if bull_seq > 0:
                        break
                    bear_seq += 1
                else:
                    break
            
            # Determine trend based on consecutive patterns
            equilibrium = (major_highs[-1] + major_lows[-1]) / 2
            
            if bull_seq >= 2:
                # STRONG BULLISH: 2+ consecutive HH+HL pairs
                if recent_close > major_lows[-1]:  # Price above last HL
                    logger.debug(f"[H1] Initial trend detected: STRONG_BULLISH ({bull_seq} consecutive HH+HL)")
                    self.last_h1_trend = TrendState.BULLISH
                    self._last_labels = "HH/HL"
                    self.last_protected_level = self._get_last_protected_level(TrendState.BULLISH, fractals)
                    self._consecutive_structure_count = bull_seq
                    return TrendState.BULLISH
            elif bear_seq >= 2:
                # STRONG BEARISH: 2+ consecutive LH+LL pairs
                if recent_close < equilibrium:  # Price below equilibrium for bearish
                    logger.debug(f"[H1] Initial trend detected: STRONG_BEARISH ({bear_seq} consecutive LH+LL)")
                    self.last_h1_trend = TrendState.BEARISH
                    self._last_labels = "LH/LL"
                    self.last_protected_level = self._get_last_protected_level(TrendState.BEARISH, fractals)
                    self._consecutive_structure_count = bear_seq
                    return TrendState.BEARISH
            else:
                # Weak pattern - check single pair with price validation
                hh = major_highs[-1] > major_highs[-2]
                hl = major_lows[-1] > major_lows[-2]
                lh = major_highs[-1] < major_highs[-2]
                ll = major_lows[-1] < major_lows[-2]
                
                if hh and hl and recent_close > major_lows[-1]:
                    logger.debug(f"[H1] Initial trend detected: WEAK_BULLISH (HH+HL, price above HL)")
                    self.last_h1_trend = TrendState.BULLISH
                    self._last_labels = "HH/HL"
                    self.last_protected_level = major_lows[-1]
                    self._consecutive_structure_count = 1
                    return TrendState.BULLISH
                elif lh and ll and recent_close < equilibrium:
                    logger.debug(f"[H1] Initial trend detected: WEAK_BEARISH (LH+LL, price below equilibrium)")
                    self.last_h1_trend = TrendState.BEARISH
                    self._last_labels = "LH/LL"
                    self.last_protected_level = self._get_last_protected_level(TrendState.BEARISH, fractals)
                    self._consecutive_structure_count = 1
                    return TrendState.BEARISH
        
        logger.debug(f"[H1] No clear initial trend found, defaulting to RANGE")
        
        # P2.B: Fallback to recency-weighted calculation if consecutive patterns not found
        recency_trend, recency_labels, recency_count = self._calculate_recency_weighted_trend(fractals, recent_close)
        if recency_trend != TrendState.RANGE and recency_count >= 2:
            logger.debug(f"[H1] Recency-weighted trend: {recency_trend.value} (score: {recency_count})")
            self.last_h1_trend = recency_trend
            self._last_labels = recency_labels
            self._consecutive_structure_count = recency_count
            return recency_trend
        
        return TrendState.RANGE
    
    @log_errors
    @timed_metric("HTFMSSAnalyzer.analyze_h1_structure")
    @retry(max_attempts=3, delay=0.1, backoff=2.0, exceptions=(Exception,))
    @circuit_breaker(failure_threshold=5, timeout=30.0, expected_exception=Exception)
    def analyze_h1_structure(
        self,
        candles_h1: pd.DataFrame
    ) -> Dict:
        """
        Analyze H1 candles for BOS/CHoCH with Structural Swing (n=5).
        
        Args:
            candles_h1: H1 OHLCV DataFrame
        
        Returns:
            {
                'trend': TrendState,
                'structure_type': str,  # BOS, CHoCH, NONE
                'direction': str,  # BULLISH, BEARISH
                'level': float,
                'score_adjustment': int,
                'is_aligned': bool,
                'last_high': float,
                'last_low': float,
                'labels': str # HH/HL or LH/LL
            }
        """
        if candles_h1.empty or len(candles_h1) < 40:
            return self._empty_result()
        
        # v4.7: Initial trend detection - scan history once when bot starts
        if not self._initial_scan_done:
            self._initial_scan_done = True
            self.detect_initial_trend(candles_h1)
        
        # Use n=5 for major structural highlights
        fractals = self._get_fractals(candles_h1, n=self.h1_fractal_n)
        
        if not fractals['highs'] or not fractals['lows']:
            return self._empty_result()
        
        last_high = fractals['highs'][-1]
        last_low = fractals['lows'][-1]
        
        self.h1_swings = fractals
        
        current_candle = candles_h1.iloc[-1]
        
        # Identify current potential trend from fractals
        new_trend, new_labels = self._infer_trend_and_labels(fractals)
        
        # v4.6 Debug: Log H1 trend detection
        high_levels = [f['level'] for f in fractals['highs'][-3:]] if fractals['highs'] else []
        low_levels = [f['level'] for f in fractals['lows'][-3:]] if fractals['lows'] else []
        logger.debug(f"[H1_DEBUG] fractals: highs={high_levels}, lows={low_levels}, new_trend={new_trend}, new_labels={new_labels}")
        
        # 1. Body Close Enforcement for BOS
        is_bull_bos = current_candle['close'] > last_high['level']
        is_bear_bos = current_candle['close'] < last_low['level']
        
        logger.debug(f"[H1_DEBUG] is_bull_bos={is_bull_bos}, is_bear_bos={is_bear_bos}, close={current_candle['close']}, last_high={last_high['level']}, last_low={last_low['level']}")
        
        structure_type = "NONE"
        direction = None
        level = 0
        
        # Calculate Displacement
        body = abs(current_candle['close'] - current_candle['open'])
        avg_body = self._get_avg_body(candles_h1)
        is_displaced = body > (avg_body * self.displacement_threshold)
        
        logger.debug(f"[H1_DEBUG] body={body}, avg_body={avg_body}, is_displaced={is_displaced}")
        
        # 2. Inducement (IDM) Integration - Confirming High/Low (with Historical Scan)
        minor_fractals = self._get_fractals(candles_h1, n=2)
        idm = self._detect_inducement(candles_h1, minor_fractals)
        
        is_h_confirmed = False
        is_l_confirmed = False
        
        # Use "Deep Structure Recovery" (DSR) - Scan full available history
        lookback_window = len(candles_h1)
        recent_prices = candles_h1.tail(lookback_window)
        
        if idm:
            # High is confirmed if price ALREADY took out IDM Low within history
            if idm['type'] == 'IDM_LOW':
                if (recent_prices['low'] < idm['level']).any():
                    is_h_confirmed = True
                    self.confirmed_swings['high'] = last_high['level']
                    if current_candle['low'] < idm['level']: # Only log if it happened JUST NOW
                        logger.debug(f"[H1 Swing High Confirmed via IDM sweep at {idm['level']}")
            
            # Low is confirmed if price ALREADY took out IDM High within history
            elif idm['type'] == 'IDM_HIGH':
                if (recent_prices['high'] > idm['level']).any():
                    is_l_confirmed = True
                    self.confirmed_swings['low'] = last_low['level']
                    if current_candle['high'] > idm['level']:
                        logger.debug(f"[H1 Swing Low Confirmed via IDM sweep at {idm['level']}")
        
        # Absolute Fallback (Plan B): If still not confirmed, use Absolute High/Low of history
        if not is_h_confirmed and self.confirmed_swings['high'] is None:
            self.confirmed_swings['high'] = recent_prices['high'].max()
        if not is_l_confirmed and self.confirmed_swings['low'] is None:
            self.confirmed_swings['low'] = recent_prices['low'].min()
        
        # v4.7: Get protected level (last HL/LH)
        protected_level = self._get_last_protected_level(self.last_h1_trend, fractals)
        if protected_level:
            self.last_protected_level = protected_level
        
        # v4.7: Calculate displacement_score (0/1/2) for confidence
        displacement_score = 2 if is_displaced else (1 if body > avg_body else 0)
        
        # v4.7: Count consecutive structures
        high_levels_all = [f['level'] for f in fractals['highs'][-4:]]
        low_levels_all = [f['level'] for f in fractals['lows'][-4:]]
        bull_seq = 0
        bear_seq = 0
        if len(high_levels_all) >= 2 and len(low_levels_all) >= 2:
            for i in range(len(high_levels_all) - 1):
                if high_levels_all[i+1] > high_levels_all[i] and low_levels_all[i+1] > low_levels_all[i]:
                    bull_seq += 1
                elif high_levels_all[i+1] < high_levels_all[i] and low_levels_all[i+1] < low_levels_all[i]:
                    bear_seq += 1
        is_strong_structure = bull_seq >= 2 or bear_seq >= 2
        
        # 3. Market State: Trend vs RANGE Logic - Check BOS FIRST, then RANGE as fallback
        # 4. BOS/CHoCH Processing - Body Close ONLY (no displacement gate)
        if is_bull_bos:
            # Body close above major high = BOS or CHoCH
            stype = "CHoCH" if self.last_h1_trend == TrendState.BEARISH else "BOS"
            structure_type = stype
            direction = "BULLISH"
            level = last_high['level']
            self.last_h1_trend = TrendState.BULLISH
            self._last_stype = structure_type
            self._last_labels = "BULLISH"
            # Update protected level
            self.last_protected_level = last_low['level']
            self._consecutive_structure_count += 1
            logger.debug(f"[H1_DEBUG] Set BULLISH: {stype} (Body Close above high)")
            self._update_state(TrendState.BULLISH, structure_type, level, "BULLISH")
            
        elif is_bear_bos:
            stype = "CHoCH" if self.last_h1_trend == TrendState.BULLISH else "BOS"
            structure_type = stype
            direction = "BEARISH"
            level = last_low['level']
            self.last_h1_trend = TrendState.BEARISH
            self._last_stype = structure_type
            self._last_labels = "BEARISH"
            # Update protected level
            self.last_protected_level = last_high['level']
            self._consecutive_structure_count += 1
            logger.debug(f"[H1_DEBUG] Set BEARISH: {stype} (Body Close below low)")
            self._update_state(TrendState.BEARISH, structure_type, level, "BEARISH")
        
        elif new_labels in ["HH/LL?", "LH/HL?", "SIDEWAY"]:
            self.last_h1_trend = TrendState.RANGE
            structure_type = "RANGE"
            self._last_stype = "RANGE"
            self._last_labels = new_labels
            
        else:
            # v4.7: CHoCH_INTERNAL - protected level breach without body close confirmation
            if self.last_h1_trend == TrendState.BULLISH and self.last_protected_level:
                if current_candle['close'] < self.last_protected_level:
                    structure_type = "CHoCH_INTERNAL"
                    direction = "BEARISH"  # Warning only, don't flip trend
                    self.choch_internal_level = self.last_protected_level
                    logger.debug(f"[H1_DEBUG] CHoCH_INTERNAL: BULLISH trend but price below protected level {self.last_protected_level}")
            elif self.last_h1_trend == TrendState.BEARISH and self.last_protected_level:
                if current_candle['close'] > self.last_protected_level:
                    structure_type = "CHoCH_INTERNAL"
                    direction = "BULLISH"  # Warning only, don't flip trend
                    self.choch_internal_level = self.last_protected_level
                    logger.debug(f"[H1_DEBUG] CHoCH_INTERNAL: BEARISH trend but price above protected level {self.last_protected_level}")
            
            # v4.7: Check for RANGE conditions (clearation of consecutive structures)
            if new_labels in ["HH/LL?", "LH/HL?", "SIDEWAY"]:
                self._consecutive_structure_count = 0
                self.last_h1_trend = TrendState.RANGE
                structure_type = "RANGE"
                self._last_stype = "RANGE"

        return {
            'trend': self.last_h1_trend,
            'structure_type': structure_type,
            'direction': direction,
            'level': level,
            'last_high': last_high['level'],
            'last_low': last_low['level'],
            'confirmed_high': self.confirmed_swings['high'],
            'confirmed_low': self.confirmed_swings['low'],
            'is_h_confirmed': is_h_confirmed,
            'is_l_confirmed': is_l_confirmed,
            'idm_level': idm['level'] if idm else None,
            'score_adjustment': 0,
            'is_aligned': True,
            'labels': self._last_labels if self.last_h1_trend != TrendState.RANGE else "RANGE",
            # v4.7: New fields for institutional grade
            'displacement_score': displacement_score,
            'choch_internal_level': self.choch_internal_level,
            'is_strong_structure': is_strong_structure,
            'protected_level': self.last_protected_level,
            'consecutive_structures': self._consecutive_structure_count
        }
    
    def _update_state(self, trend: TrendState, structure_type: str, level: float, labels: str = "NONE"):
        """Update internal state with new H1 structure."""
        self.last_h1_trend = trend
        self.last_h1_structure = structure_type
        self.last_h1_level = level
        self.last_h1_time = datetime.now(timezone.utc)
        self.last_structural_labels = labels
        
        # logger.info(f"H1 Structure Update: {structure_type} {trend.value} ({labels}) @ {level}")
    
    def _infer_trend_and_labels(self, fractals: Dict, current_price: float = None) -> Tuple[TrendState, str]:
        """
        Infer trend and structural labels (HH, HL, LH, LL) from fractal pattern.
        
        Args:
            fractals: Dictionary of highs and lows fractals
            current_price: Optional current price to check for breakout
            
        Returns:
            (TrendState, label_string)
        """
        if len(fractals['highs']) < 2 or len(fractals['lows']) < 2:
            return TrendState.RANGE, "SIDEWAY"
        
        last_high = fractals['highs'][-1]['level']
        prev_high = fractals['highs'][-2]['level']
        last_low = fractals['lows'][-1]['level']
        prev_low = fractals['lows'][-2]['level']
        
        # Bullish conditions: Higher High (HH) and Higher Low (HL)
        is_hh = last_high > prev_high
        is_hl = last_low > prev_low
        
        # Bearish conditions: Lower High (LH) and Lower Low (LL)
        is_lh = last_high < prev_high
        is_ll = last_low < prev_low
        
        # Determine labels and trend
        if is_hh and is_hl:
            return TrendState.BULLISH, "HH/HL"
        elif is_lh and is_ll:
            return TrendState.BEARISH, "LH/LL"
        elif is_hh and not is_hl:
            return TrendState.RANGE, "HH/LL?" # Expanding range
        elif not is_hh and is_hl:
            return TrendState.RANGE, "LH/HL?" # Contracting range
            
        return TrendState.RANGE, "SIDEWAY"
    
    def check_m15_h1_sync(
        self,
        m15_direction: str,
        h1_analysis: Dict
    ) -> Dict:
        """
        Check if M15 signal aligns with H1 structure.
        
        Args:
            m15_direction: 'LONG' or 'SHORT'
            h1_analysis: Result from analyze_h1_structure()
        
        Returns:
            {
                'sync_score': int,  # -2 to +3
                'is_aligned': bool,
                'reason': str,
                'htf_trend': str,
                'htf_structure': str,
                'confidence_mult': float
            }
        """
        h1_trend = h1_analysis.get('trend', TrendState.RANGE)
        h1_structure = h1_analysis.get('structure_type', 'NONE')
        h1_direction = h1_analysis.get('direction')
        
        sync_score = 0
        is_aligned = True
        reason = ""
        
        m15_bullish = m15_direction == 'LONG'
        h1_bullish = h1_trend == TrendState.BULLISH
        h1_bearish = h1_trend == TrendState.BEARISH
        
        if h1_structure == "CHoCH":
            if (m15_bullish and h1_bullish) or (not m15_bullish and h1_bearish):
                sync_score = 3
                reason = f"H1_CHoCH_{m15_direction}"
                is_aligned = True
            else:
                sync_score = -2
                reason = f"H1_CHoCH_OPPOSED"
                is_aligned = False
        
        elif h1_structure == "BOS":
            if (m15_bullish and h1_bullish) or (not m15_bullish and h1_bearish):
                sync_score = 2
                reason = f"H1_BOS_{m15_direction}"
                is_aligned = True
            else:
                sync_score = -2
                reason = f"H1_BOS_OPPOSED"
                is_aligned = False
        
        else:
            if (m15_bullish and h1_bullish) or (not m15_bullish and h1_bearish):
                sync_score = 1
                reason = f"H1_TREND_{m15_direction}"
                is_aligned = True
            elif (m15_bullish and h1_bearish) or (not m15_bullish and h1_bullish):
                sync_score = -1
                reason = "H1_TREND_CONFLICT"
                is_aligned = False
            else:
                sync_score = 0
                reason = "H1_NEUTRAL"
                is_aligned = True
        
        confidence_mult = self._calculate_confidence_mult(sync_score)
        
        return {
            'sync_score': sync_score,
            'is_aligned': is_aligned,
            'reason': reason,
            'htf_trend': h1_trend.value,
            'htf_structure': h1_structure,
            'confidence_mult': confidence_mult
        }
    
    def _calculate_confidence_mult(self, sync_score: int) -> float:
        """Calculate confidence multiplier from sync score."""
        if sync_score >= 3:
            return 1.3
        elif sync_score >= 2:
            return 1.15
        elif sync_score >= 1:
            return 1.0
        elif sync_score == 0:
            return 0.9
        elif sync_score == -1:
            return 0.75
        else:
            return 0.5
    
    def get_entry_adjustment(
        self,
        m15_direction: str,
        h1_analysis: Dict
    ) -> Tuple[int, str, float]:
        """
        Get entry score adjustment and confidence multiplier.
        
        Args:
            m15_direction: 'LONG' or 'SHORT'
            h1_analysis: Result from analyze_h1_structure()
        
        Returns:
            (score_adjustment, reason, confidence_multiplier)
        """
        sync = self.check_m15_h1_sync(m15_direction, h1_analysis)
        
        score_adj = max(0, sync['sync_score'])
        
        return score_adj, sync['reason'], sync['confidence_mult']
    
    def should_filter_entry(
        self,
        m15_direction: str,
        h1_analysis: Dict,
        min_sync_score: int = -1
    ) -> Tuple[bool, str]:
        """
        Check if entry should be filtered based on H1 conflict.
        
        Args:
            m15_direction: 'LONG' or 'SHORT'
            h1_analysis: Result from analyze_h1_structure()
            min_sync_score: Minimum sync score to allow entry
        
        Returns:
            (should_filter, reason)
        """
        sync = self.check_m15_h1_sync(m15_direction, h1_analysis)
        
        if sync['sync_score'] < min_sync_score:
            return True, f"HTF_CONFLICT: {sync['reason']}"
        
        return False, ""
    
    def _get_fractals(self, candles: pd.DataFrame, n: int = 2) -> Dict[str, List[Dict]]:
        """Identify Fractal Swing Points with support for equal highs/lows (flat tops/bottoms).
        
        P2.E: Added time-based filtering to ignore old structures.
        """
        if len(candles) < 2 * n + 1:
            return {'highs': [], 'lows': []}
        
        highs = []
        lows = []
        now = datetime.now(timezone.utc)
        
        for i in range(n, len(candles) - n):
            curr_high = candles.iloc[i]['high']
            curr_low = candles.iloc[i]['low']
            
            # Get timestamp for P2.E age filtering
            candle_time = candles.index[i]
            age_hours = 0  # Default: don't filter if no valid timestamp
            
            if candle_time is not None:
                if isinstance(candle_time, str):
                    try:
                        candle_time = pd.to_datetime(candle_time)
                    except:
                        candle_time = None
                
                if candle_time is not None:
                    if hasattr(candle_time, 'tz') and candle_time.tz is not None:
                        candle_time_utc = candle_time.astimezone(timezone.utc)
                    elif hasattr(candle_time, 'timestamp'):
                        candle_time_utc = datetime.fromtimestamp(candle_time.timestamp(), tz=timezone.utc)
                    else:
                        candle_time_utc = None
                    
                    if candle_time_utc is not None:
                        age_hours = (now - candle_time_utc).total_seconds() / 3600
            
            # P2.E: Skip structures older than max_structure_age_hours (only if we have valid timestamp)
            if age_hours > self.max_structure_age_hours:
                continue
            
            # Check for Fractal High: i is higher than n neighbors on left and >= neighbors on right
            # This ensures we pick the FIRST occurrence of a flat top
            is_high = True
            for j in range(1, n + 1):
                if candles.iloc[i - j]['high'] > curr_high or candles.iloc[i + j]['high'] > curr_high:
                    is_high = False
                    break
                # If equal to neighbor, we only accept it if it's the FIRST (left side can't be equal)
                if candles.iloc[i - j]['high'] == curr_high:
                    is_high = False
                    break
            
            if is_high:
                highs.append({
                    'level': curr_high,
                    'time': candles.index[i],
                    'index': i,
                    'type': 'SWING_HIGH',
                    'age_hours': age_hours  # P2.E: Track age
                })
            
            # Check for Fractal Low: i is lower than n neighbors on left and <= neighbors on right
            is_low = True
            for j in range(1, n + 1):
                if candles.iloc[i - j]['low'] < curr_low or candles.iloc[i + j]['low'] < curr_low:
                    is_low = False
                    break
                # If equal to neighbor, we only accept it if it's the FIRST (left side can't be equal)
                if candles.iloc[i - j]['low'] == curr_low:
                    is_low = False
                    break
            
            if is_low:
                lows.append({
                    'level': curr_low,
                    'time': candles.index[i],
                    'index': i,
                    'type': 'SWING_LOW',
                    'age_hours': age_hours  # P2.E: Track age
                })
        
        return {'highs': highs, 'lows': lows}
    
    def _detect_inducement(self, candles: pd.DataFrame, minor_fractals: Dict) -> Optional[Dict]:
        """Identify the first internal pullback (Inducement) for H1."""
        if not minor_fractals['highs'] or not minor_fractals['lows']:
            return None
            
        last_minor_low = minor_fractals['lows'][-1]
        last_minor_high = minor_fractals['highs'][-1]
        
        # Determine based on which one is more recent
        if last_minor_low['index'] > last_minor_high['index']:
            return {'level': last_minor_low['level'], 'type': 'IDM_LOW', 'index': last_minor_low['index']}
        else:
            return {'level': last_minor_high['level'], 'type': 'IDM_HIGH', 'index': last_minor_high['index']}
    
    def _get_last_protected_level(self, trend: TrendState, fractals: Dict) -> Optional[float]:
        """
        Get last protected level (last HL for BULLISH, last LH for BEARISH).
        This is used for CHoCH_INTERNAL detection.
        """
        highs = fractals.get('highs', [])
        lows = fractals.get('lows', [])
        
        if trend == TrendState.BULLISH:
            # Find last HL = swing low that is higher than previous swing low
            for i in range(len(lows) - 1, 0, -1):
                if lows[i]['level'] > lows[i-1]['level']:
                    return lows[i]['level']
        elif trend == TrendState.BEARISH:
            # Find last LH = swing high that is lower than previous swing high
            for i in range(len(highs) - 1, 0, -1):
                if highs[i]['level'] < highs[i-1]['level']:
                    return highs[i]['level']
        return None
    
    def _get_avg_body(self, candles: pd.DataFrame, window: int = 20) -> float:
        """Calculate average body size to detect displacement."""
        if len(candles) < window:
            window = len(candles)
        
        recent = candles.iloc[-window:]
        bodies = abs(recent['close'] - recent['open'])
        return float(bodies.mean())
    
    def _calculate_recency_weighted_trend(self, fractals: Dict, current_price: float) -> Tuple[TrendState, str, int]:
        """
        P2.B: Calculate trend with recency bias - recent structures get higher weight.
        
        Uses exponential decay: weight = recency_weight ^ (distance from current)
        Recent pairs (i=1) get weight = 1.0
        Previous pairs (i=2) get weight = recency_weight
        Older pairs get progressively lower weights
        
        Returns: (trend_state, labels, consecutive_count)
        """
        highs = fractals.get('highs', [])
        lows = fractals.get('lows', [])
        
        if len(highs) < 2 or len(lows) < 2:
            return TrendState.RANGE, "SIDEWAY", 0
        
        bull_score = 0.0
        bear_score = 0.0
        max_pairs = min(4, len(highs), len(lows))
        
        for i in range(1, max_pairs):
            weight = self.recency_weight ** (i - 1)
            
            hh = highs[-i]['level'] > highs[-i-1]['level']
            hl = lows[-i]['level'] > lows[-i-1]['level']
            lh = highs[-i]['level'] < highs[-i-1]['level']
            ll = lows[-i]['level'] < lows[-i-1]['level']
            
            if hh and hl:
                bull_score += weight
            elif lh and ll:
                bear_score += weight
        
        if bull_score > bear_score:
            if current_price > lows[-1]['level']:
                return TrendState.BULLISH, "HH/HL", int(bull_score)
        elif bear_score > bull_score:
            if current_price < highs[-1]['level']:
                return TrendState.BEARISH, "LH/LL", int(bear_score)
        
        return TrendState.RANGE, "SIDEWAY", 0
    
    def _empty_result(self) -> Dict:
        """Return empty result for insufficient data."""
        return {
            'trend': TrendState.RANGE,
            'structure_type': 'NONE',
            'direction': None,
            'level': 0,
            'last_high': 0,
            'last_low': 0,
            'score_adjustment': 0,
            'is_aligned': True,
            'labels': 'WAIT_DATA'
        }
    
    def get_state_dict(self) -> Dict:
        """Get current state as dictionary."""
        return {
            'last_h1_trend': self.last_h1_trend.value,
            'last_h1_structure': self.last_h1_structure,
            'last_h1_level': self.last_h1_level,
            'last_h1_time': self.last_h1_time.isoformat() if self.last_h1_time else None
        }
    
    def check_m5_h1_coherence(
        self,
        m5_trend: str,
        m5_structure_type: str
    ) -> Dict:
        """
        Check H1-M5 trend coherence.
        
        When M5 detects a CHoCH that conflicts with H1 trend:
        - If H1 is BULLISH and M5 detects BEARISH CHoCH: Mark as CHoCH_COUNTER_TREND
        - Only allow M5 trend flip if H1 also shows CHoCH_INTERNAL or opposing structure
        
        Args:
            m5_trend: M5 trend ('BULLISH', 'BEARISH', 'RANGE')
            m5_structure_type: M5 structure type ('CHoCH', 'BOS', 'NONE', etc.)
        
        Returns:
            {
                'is_coherent': bool,
                'coherence_type': str,  # ALIGNED, COUNTER_TREND, NO_CONFLICT
                'warning': str or None,
                'should_flip_m5': bool
            }
        """
        h1_trend_value = self.last_h1_trend.value
        
        if h1_trend_value == 'RANGE' or m5_trend == 'RANGE':
            return {
                'is_coherent': True,
                'coherence_type': 'NO_CONFLICT',
                'warning': None,
                'should_flip_m5': True
            }
        
        m5_bullish = m5_trend == 'BULLISH'
        m5_bearish = m5_trend == 'BEARISH'
        h1_bullish = h1_trend_value == 'BULLISH'
        h1_bearish = h1_trend_value == 'BEARISH'
        
        if m5_structure_type == 'CHoCH':
            if m5_bullish and h1_bearish:
                return {
                    'is_coherent': False,
                    'coherence_type': 'COUNTER_TREND',
                    'warning': 'M5 CHoCH opposes H1 trend - likely false flip',
                    'should_flip_m5': False
                }
            elif m5_bearish and h1_bullish:
                return {
                    'is_coherent': False,
                    'coherence_type': 'COUNTER_TREND',
                    'warning': 'M5 CHoCH opposes H1 trend - likely false flip',
                    'should_flip_m5': False
                }
            else:
                return {
                    'is_coherent': True,
                    'coherence_type': 'ALIGNED',
                    'warning': None,
                    'should_flip_m5': True
                }
        
        if (m5_bullish and h1_bearish) or (m5_bearish and h1_bullish):
            if self.last_h1_structure in ['CHoCH', 'CHoCH_INTERNAL']:
                return {
                    'is_coherent': True,
                    'coherence_type': 'ALIGNED',
                    'warning': None,
                    'should_flip_m5': True
                }
            else:
                return {
                    'is_coherent': False,
                    'coherence_type': 'COUNTER_TREND',
                    'warning': 'M5 trend opposes H1 without H1 structure confirmation',
                    'should_flip_m5': False
                }
        
        return {
            'is_coherent': True,
            'coherence_type': 'ALIGNED',
            'warning': None,
            'should_flip_m5': True
        }

    def get_alignment_context(
        self,
        m5_trend: str,
        m5_structure_type: str,
        m5_structure_strength: str = "WEAK",
        m5_internal_bos: Dict = None
    ) -> Dict:
        """
        Get comprehensive H1-M5 alignment context for signal scoring.
        
        Returns structured alignment data including:
        - H1 phase (IMPULSE/CORRECTION)
        - M5 phase
        - Coherence score
        - Recommended action (TRADE/WAIT/COUNTER_ONLY)
        - Confidence multiplier
        
        Args:
            m5_trend: M5 trend ('BULLISH', 'BEARISH', 'RANGE')
            m5_structure_type: M5 structure type ('CHoCH', 'BOS', 'CHoCH_INTERNAL', 'NONE', etc.)
            m5_structure_strength: M5 structure strength ('WEAK', 'MODERATE', 'STRONG')
            m5_internal_bos: Internal BOS data from M5
        
        Returns:
            {
                'h1_phase': str,  # IMPULSE, CORRECTION, NEUTRAL
                'm5_phase': str,
                'coherence_score': float,
                'recommendation': str,  # TRADE, WAIT, COUNTER_ONLY
                'confidence_mult': float,
                'should_filter': bool,
                'details': {...}
            }
        """
        h1_trend = self.last_h1_trend.value
        h1_structure = self._last_stype
        h1_strength = self._structure_strength
        
        h1_bullish = h1_trend == 'BULLISH'
        h1_bearish = h1_trend == 'BEARISH'
        m5_bullish = m5_trend == 'BULLISH'
        m5_bearish = m5_trend == 'BEARISH'
        
        details = {
            'h1_trend': h1_trend,
            'h1_structure': h1_structure,
            'h1_strength': h1_strength.value,
            'h1_consecutive': self._consecutive_structure_count,
            'm5_trend': m5_trend,
            'm5_structure_type': m5_structure_type,
            'm5_strength': m5_structure_strength,
            'protected_level': self.last_protected_level
        }
        
        coherence_score = 1.0
        recommendation = 'WAIT'
        should_filter = False
        
        if h1_trend == 'RANGE' or m5_trend == 'RANGE':
            h1_phase = 'NEUTRAL'
            m5_phase = 'NEUTRAL'
            confidence_mult = 0.8
            return {
                'h1_phase': h1_phase,
                'm5_phase': m5_phase,
                'coherence_score': coherence_score,
                'recommendation': 'WAIT',
                'confidence_mult': confidence_mult,
                'should_filter': False,
                'details': details
            }
        
        h1_phase = 'IMPULSE' if self._consecutive_structure_count >= 2 else 'CORRECTION'
        m5_phase = 'IMPULSE' if m5_structure_type in ['BOS', 'CHoCH'] else 'CORRECTION'
        
        aligned = (m5_bullish and h1_bullish) or (m5_bearish and h1_bearish)
        counter = (m5_bullish and h1_bearish) or (m5_bearish and h1_bullish)
        
        if aligned:
            coherence_score = 1.5
            
            h1_strong = h1_strength == StructureStrength.STRONG
            m5_strong = m5_structure_strength == 'STRONG'
            
            if h1_strong and m5_strong:
                confidence_mult = 1.5
                recommendation = 'TRADE'
                details['alignment_type'] = 'STRONG_ALIGNED'
            elif h1_strong or m5_strong:
                confidence_mult = 1.25
                recommendation = 'TRADE'
                details['alignment_type'] = 'MODERATE_ALIGNED'
            else:
                confidence_mult = 1.0
                recommendation = 'TRADE'
                details['alignment_type'] = 'WEAK_ALIGNED'
                
        elif counter:
            coherence_score = 0.5
            
            if m5_structure_type == 'CHoCH_INTERNAL':
                coherence_score = 0.3
                confidence_mult = 0.3
                recommendation = 'WAIT'
                should_filter = True
                details['alignment_type'] = 'CHoCH_INTERNAL_COUNTER'
                details['warning'] = 'M5 CHoCH_INTERNAL opposes H1 - likely correction, not reversal'
                
                if self.last_protected_level:
                    if h1_bullish and m5_bearish:
                        if m5_trend != 'RANGE':
                            details['protected_intact'] = True
                    elif h1_bearish and m5_bullish:
                        if m5_trend != 'RANGE':
                            details['protected_intact'] = True
                            
            elif m5_structure_type == 'CHoCH':
                h1_strong = h1_strength == StructureStrength.STRONG
                m5_strength_val = m5_structure_strength
                
                if h1_strong:
                    confidence_mult = 0.3
                    should_filter = True
                    recommendation = 'WAIT'
                    details['alignment_type'] = 'STRONG_H1_COUNTER'
                    details['warning'] = 'Strong H1 opposes M5 CHoCH - high risk false flip'
                else:
                    confidence_mult = 0.5
                    recommendation = 'COUNTER_ONLY'
                    details['alignment_type'] = 'MODERATE_COUNTER'
                    
            else:
                confidence_mult = 0.5
                recommendation = 'COUNTER_ONLY'
                details['alignment_type'] = 'TREND_COUNTER'
        
        else:
            coherence_score = 1.0
            confidence_mult = 1.0
            recommendation = 'WAIT'
            details['alignment_type'] = 'NEUTRAL'
        
        details['h1_phase'] = h1_phase
        details['m5_phase'] = m5_phase
        
        return {
            'h1_phase': h1_phase,
            'm5_phase': m5_phase,
            'coherence_score': coherence_score,
            'recommendation': recommendation,
            'confidence_mult': confidence_mult,
            'should_filter': should_filter,
            'details': details
        }
