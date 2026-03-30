"""
Structure Validator Module v3.1
Validates Break of Structure using Binance data (CVD, OI, Volume, POC, Liquidity Walls)
"""
from typing import Dict, List, Tuple, Optional
import pandas as pd
import numpy as np
from enum import Enum

from ..utils.logger import get_logger
from ..enums import BOSStatus
from src.utils.decorators import log_errors, retry, circuit_breaker
from src.utils.metrics import timed_metric

logger = get_logger(__name__)


class StructureValidator:
    """
    Validates Break of Structure using Binance data.
    
    Scoring Factors (Total 0-13):
    1. Body Close Confirmation (+3 pts)
    2. CVD Confirmation (+2 pts)
    3. OI Increase (+2 pts)
    4. Displacement Ratio (+2 pts)
    5. Volume at Break (+1 pt)
    6. POC Proximity (+1 pt) - NEW
    7. Liquidity Wall Support (+2 pts) - NEW
    
    Status Thresholds (scaled for max 13):
    - Score >= 9: CONFIRMED
    - Score 6-8: PENDING
    - Score < 6: SWEEP
    """
    
    def __init__(self, config: Dict = None):
        """
        Initialize StructureValidator.
        
        Args:
            config: Configuration dictionary
        """
        self.config = config or {}
        
        self.confirmed_threshold = self.config.get('confirmed_threshold', 9)  # Adjusted for max 15
        self.pending_threshold = self.config.get('pending_threshold', 6)    # Adjusted for max 15
        self.displacement_ratio_threshold = self.config.get('displacement_ratio_threshold', 2.0)
        self.volume_spike_threshold = self.config.get('volume_spike_threshold', 1.5)
        
        # POC settings
        self.poc_proximity_pct = self.config.get('poc_proximity_pct', 0.3)
        
        # Liquidity Wall settings
        self.use_liquidity_walls = self.config.get('use_liquidity_walls', True)
    
    @log_errors
    @timed_metric("StructureValidator.validate_bos")
    @retry(max_attempts=3, delay=0.1, backoff=2.0, exceptions=(Exception,))
    @circuit_breaker(failure_threshold=5, timeout=30.0, expected_exception=Exception)
    def validate_bos(
        self,
        direction: str,
        swing_level: float,
        candles: pd.DataFrame,
        cvd_series: List[float],
        oi_current: float,
        oi_before: float,
        trades: List[Dict],
        cvd_at_swing: Optional[float] = None,
        poc_data: Optional[Dict] = None,
        liquidity_wall_data: Optional[Dict] = None,
        analysis_data: Optional[Dict] = None,
        htf_trend: Optional[str] = None
    ) -> Dict:
        """
        Validate Break of Structure with scoring.
        
        Args:
            direction: 'BULLISH' or 'BEARISH'
            swing_level: The swing level that was broken
            candles: OHLCV DataFrame
            cvd_series: CVD values series
            oi_current: Current Open Interest
            oi_before: OI before the break
            trades: List of trades in the break candle
            cvd_at_swing: CVD value at the swing point (optional)
            poc_data: POC data from VolumeProfile (optional)
                {'poc': float, 'vah': float, 'val': float}
            liquidity_wall_data: Liquidity wall data (optional)
                {'nearest_bid_wall': LiquidityWall, 'nearest_ask_wall': LiquidityWall}
            analysis_data: ICT analysis data for IDM confirmation (optional)
        
        Returns:
            {
                'score': 0-18,
                'max_score': 15,
                'status': CONFIRMED/PENDING/SWEEP,
                'reasons': [...],
                'details': {...}
            }
        """
        score = 0
        reasons = []
        details = {}
        max_score = 15
        
        if candles.empty or len(candles) < 1:
            return self._empty_result()
        
        last_candle = candles.iloc[-1]
        
        # Factor 1: Body Close Confirmation (+3 pts)
        body_score, body_reason, body_details = self._check_body_close(
            direction, swing_level, last_candle
        )
        score += body_score
        if body_reason:
            reasons.append(body_reason)
        details.update(body_details)
        
        # Factor 2: CVD Confirmation (+2 pts)
        cvd_score, cvd_reason, cvd_details = self._check_cvd_confirmation(
            direction, cvd_series, cvd_at_swing
        )
        score += cvd_score
        if cvd_reason:
            reasons.append(cvd_reason)
        details.update(cvd_details)
        
        # Factor 3: OI Increase (+2 pts)
        oi_score, oi_reason, oi_details = self._check_oi_increase(
            direction, oi_current, oi_before
        )
        score += oi_score
        if oi_reason:
            reasons.append(oi_reason)
        details.update(oi_details)
        
        # Factor 4: Displacement Ratio (+2 pts)
        disp_score, disp_reason, disp_details = self._check_displacement_ratio(
            direction, trades
        )
        score += disp_score
        if disp_reason:
            reasons.append(disp_reason)
        details.update(disp_details)
        
        # Factor 5: Volume at Break (+1 pt)
        vol_score, vol_reason, vol_details = self._check_volume_at_break(
            candles, last_candle
        )
        score += vol_score
        if vol_reason:
            reasons.append(vol_reason)
        details.update(vol_details)
        
        # Factor 6: POC Proximity (+1 pt)
        if poc_data:
            poc_score, poc_reason, poc_details = self._check_poc_proximity(
                direction, swing_level, poc_data
            )
            score += poc_score
            if poc_reason:
                reasons.append(poc_reason)
            details.update(poc_details)
        
        # Factor 7: Liquidity Wall Support (+2 pts)
        if self.use_liquidity_walls and liquidity_wall_data:
            wall_score, wall_reason, wall_details = self._check_liquidity_wall(
                direction, liquidity_wall_data
            )
            score += wall_score
            if wall_reason:
                reasons.append(wall_reason)
            details.update(wall_details)
        
        # Factor 8: Inducement Confirmation (+3 pts) - Institutional Logic
        if analysis_data and analysis_data.get('is_confirmed', False):
            score += 3
            reasons.append("IDM_CONFIRMED")
            details['idm_confirmed'] = True
        
        # Factor 9: Multi-Candle Hold (+2 pts) - Institutional Logic
        # After initial break, check if next 1-2 candles hold above/below the broken level
        if len(candles) >= 2:
            hold_score, hold_reason, hold_details = self._check_multi_candle_hold(
                direction, swing_level, candles
            )
            score += hold_score
            if hold_reason:
                reasons.append(hold_reason)
            details.update(hold_details)
        
        # Factor 10: HTF Trend Filter (+0 to -3 pts)
        if htf_trend and htf_trend != 'RANGE':
            htf_opposes_bos = (
                (htf_trend == 'BULLISH' and direction == 'BEARISH') or
                (htf_trend == 'BEARISH' and direction == 'BULLISH')
            )
            if htf_opposes_bos:
                score -= 2
                reasons.append("HTF_COUNTER_TREND")
                details['htf_filter'] = 'counter_trend'
        
        # Determine status
        status = self._determine_status(score)
        
        details['total_score'] = score
        details['max_score'] = max_score
        details['direction'] = direction
        details['swing_level'] = swing_level
        
        logger.info(
            f"BOS Validation: {direction} @ {swing_level} | "
            f"Score: {score}/{max_score} | Status: {status.value} | "
            f"Reasons: {', '.join(reasons)}"
        )
        return {
            'score': score,
            'max_score': max_score,
            'status': status,
            'reasons': reasons,
            'details': details,
            'direction': direction,
            'level': swing_level
        }
    
    @log_errors
    @timed_metric("StructureValidator.validate_internal_bos")
    @retry(max_attempts=3, delay=0.1, backoff=2.0, exceptions=(Exception,))
    @circuit_breaker(failure_threshold=5, timeout=30.0, expected_exception=Exception)
    def validate_internal_bos(
        self,
        direction: str,
        swing_level: float,
        candles: pd.DataFrame,
        cvd_series: List[float],
        oi_current: float,
        oi_before: float,
        trades: List[Dict]
    ) -> Dict:
        """
        Validate Internal Break of Structure (iBOS).
        Use for faster entries within a larger structural move.
        Thresholds are naturally lower than main BOS.
        """
        # We reuse the same logic but with different threshold expectations
        result = self.validate_bos(
            direction=direction,
            swing_level=swing_level,
            candles=candles,
            cvd_series=cvd_series,
            oi_current=oi_current,
            oi_before=oi_before,
            trades=trades
        )
        
        # Override status for internal context (BE MORE AGGRESSIVE)
        # 7+ is often enough for iBOS in ISF modes
        score = result['score']
        if score >= 7:
            result['status'] = BOSStatus.CONFIRMED
            result['is_internal'] = True
        elif score >= 4:
            result['status'] = BOSStatus.PENDING
            result['is_internal'] = True
        else:
            result['status'] = BOSStatus.SWEEP
            result['is_internal'] = True
            
        return result
    
    def _check_body_close(
        self,
        direction: str,
        swing_level: float,
        last_candle: pd.Series
    ) -> Tuple[int, str, Dict]:
        """
        Check if body close confirms the break.
        
        Returns:
            (score, reason, details)
        """
        close = last_candle['close']
        body_size = abs(last_candle['close'] - last_candle['open'])
        candle_range = last_candle['high'] - last_candle['low']
        
        details = {
            'close': close,
            'swing_level': swing_level,
            'body_size': body_size,
            'candle_range': candle_range
        }
        
        if direction == 'BULLISH':
            if close > swing_level:
                return 3, 'BODY_CLOSE_ABOVE', details
            else:
                details['fail_reason'] = 'Close not above swing level'
                return 0, '', details
        else:
            if close < swing_level:
                return 3, 'BODY_CLOSE_BELOW', details
            else:
                details['fail_reason'] = 'Close not below swing level'
                return 0, '', details
    
    def _check_cvd_confirmation(
        self,
        direction: str,
        cvd_series: List[float],
        cvd_at_swing: Optional[float] = None
    ) -> Tuple[int, str, Dict]:
        """
        Check if CVD confirms the break.
        
        Returns:
            (score, reason, details)
        """
        details = {
            'cvd_current': None,
            'cvd_at_swing': cvd_at_swing,
            'cvd_makes_new_high': False,
            'cvd_makes_new_low': False
        }
        
        if not cvd_series or len(cvd_series) < 10:
            details['fail_reason'] = 'Insufficient CVD data'
            return 0, '', details
        
        cvd_current = cvd_series[-1]
        details['cvd_current'] = cvd_current
        
        if cvd_at_swing is None:
            if direction == 'BULLISH':
                cvd_at_swing = max(cvd_series[:-5]) if len(cvd_series) > 5 else max(cvd_series)
            else:
                cvd_at_swing = min(cvd_series[:-5]) if len(cvd_series) > 5 else min(cvd_series)
            details['cvd_at_swing'] = cvd_at_swing
        
        if direction == 'BULLISH':
            if cvd_current > cvd_at_swing:
                details['cvd_makes_new_high'] = True
                return 2, f'CVD_NEW_HIGH_{cvd_current:.0f}', details
            else:
                details['fail_reason'] = 'CVD divergence (no new high)'
                return 0, '', details
        else:
            if cvd_current < cvd_at_swing:
                details['cvd_makes_new_low'] = True
                return 2, f'CVD_NEW_LOW_{cvd_current:.0f}', details
            else:
                details['fail_reason'] = 'CVD divergence (no new low)'
                return 0, '', details
    
    def _check_oi_increase(
        self,
        direction: str,
        oi_current: float,
        oi_before: float
    ) -> Tuple[int, str, Dict]:
        """
        Check if OI increased on the break.
        
        Returns:
            (score, reason, details)
        """
        oi_change = oi_current - oi_before if oi_current and oi_before else 0
        oi_change_pct = (oi_change / oi_before * 100) if oi_before and oi_before > 0 else 0
        
        details = {
            'oi_current': oi_current,
            'oi_before': oi_before,
            'oi_change': oi_change,
            'oi_change_pct': oi_change_pct
        }
        
        if oi_change > 0:
            return 2, f'OI_UP_{oi_change:.0f}', details
        else:
            details['fail_reason'] = f'OI decreased ({oi_change:.0f}) - likely squeeze'
            return 0, '', details
    
    def _check_displacement_ratio(
        self,
        direction: str,
        trades: List[Dict]
    ) -> Tuple[int, str, Dict]:
        """
        Check displacement ratio from trades.
        
        Returns:
            (score, reason, details)
        """
        if not trades:
            details = {'fail_reason': 'No trade data available'}
            return 0, '', details
        
        buy_volume = 0.0
        sell_volume = 0.0
        
        for trade in trades:
            volume = trade.get('volume', 0)
            is_buyer_maker = trade.get('is_buyer_maker', True)
            
            if is_buyer_maker:
                sell_volume += volume
            else:
                buy_volume += volume
        
        details = {
            'buy_volume': buy_volume,
            'sell_volume': sell_volume,
            'ratio': None
        }
        
        if direction == 'BULLISH':
            if sell_volume > 0:
                ratio = buy_volume / sell_volume
                details['ratio'] = ratio
                
                if ratio >= self.displacement_ratio_threshold:
                    return 2, f'DISP_RATIO_{ratio:.1f}x', details
                else:
                    details['fail_reason'] = f'Weak displacement ({ratio:.1f}x < {self.displacement_ratio_threshold}x)'
                    return 0, '', details
            elif buy_volume > 0:
                details['ratio'] = float('inf')
                return 2, 'DISP_ALL_BUY', details
            else:
                details['fail_reason'] = 'No volume data'
                return 0, '', details
        else:
            if buy_volume > 0:
                ratio = sell_volume / buy_volume
                details['ratio'] = ratio
                
                if ratio >= self.displacement_ratio_threshold:
                    return 2, f'DISP_RATIO_{ratio:.1f}x', details
                else:
                    details['fail_reason'] = f'Weak displacement ({ratio:.1f}x < {self.displacement_ratio_threshold}x)'
                    return 0, '', details
            elif sell_volume > 0:
                details['ratio'] = float('inf')
                return 2, 'DISP_ALL_SELL', details
            else:
                details['fail_reason'] = 'No volume data'
                return 0, '', details
    
    def _check_volume_at_break(
        self,
        candles: pd.DataFrame,
        last_candle: pd.Series
    ) -> Tuple[int, str, Dict]:
        """
        Check if volume at break is higher than average.
        
        Returns:
            (score, reason, details)
        """
        if len(candles) < 20:
            details = {'fail_reason': 'Insufficient candle data for average'}
            return 0, '', details
        
        avg_volume = candles['volume'].tail(20).mean()
        break_volume = last_candle['volume']
        volume_ratio = break_volume / avg_volume if avg_volume > 0 else 0
        
        details = {
            'break_volume': break_volume,
            'avg_volume': avg_volume,
            'volume_ratio': volume_ratio
        }
        
        if volume_ratio >= self.volume_spike_threshold:
            return 1, f'VOL_SPIKE_{volume_ratio:.1f}x', details
        else:
            details['fail_reason'] = f'Volume below threshold ({volume_ratio:.1f}x < {self.volume_spike_threshold}x)'
            return 0, '', details
    
    def _check_poc_proximity(
        self,
        direction: str,
        swing_level: float,
        poc_data: Dict
    ) -> Tuple[int, str, Dict]:
        """
        Check if the break occurs near POC (Point of Control).
        
        POC is the price level with highest volume - institutional interest zone.
        
        Args:
            direction: 'BULLISH' or 'BEARISH'
            swing_level: The swing level that was broken
            poc_data: {'poc': float, 'vah': float, 'val': float}
        
        Returns:
            (score, reason, details)
            score: 0-1
        """
        poc = poc_data.get('poc', 0)
        vah = poc_data.get('vah', 0)
        val = poc_data.get('val', 0)
        
        if poc == 0:
            details = {'fail_reason': 'No POC data available'}
            return 0, '', details
        
        # Calculate distance from swing level to POC
        distance = abs(swing_level - poc)
        distance_pct = (distance / poc) * 100 if poc > 0 else float('inf')
        
        # Check if swing level is within Value Area (between VAH and VAL)
        in_value_area = val <= swing_level <= vah if val > 0 and vah > 0 else False
        
        details = {
            'poc': poc,
            'vah': vah,
            'val': val,
            'swing_level': swing_level,
            'distance_to_poc': distance,
            'distance_pct': distance_pct,
            'in_value_area': in_value_area
        }
        
        # Score if swing level is near POC (within threshold %)
        if distance_pct <= self.poc_proximity_pct:
            return 1, f'POC_NEAR_{distance_pct:.2f}%', details
        
        # Score if within Value Area
        if in_value_area:
            return 1, 'POC_IN_VALUE_AREA', details
        
        details['fail_reason'] = f'Swing level not near POC ({distance_pct:.2f}% > {self.poc_proximity_pct}%)'
        return 0, '', details
    
    def _check_liquidity_wall(
        self,
        direction: str,
        liquidity_wall_data: Dict
    ) -> Tuple[int, str, Dict]:
        """
        Check if price is near a liquidity wall (large limit orders).
        
        For LONG: Check for Bid Wall support below
        For SHORT: Check for Ask Wall resistance above
        
        Args:
            direction: 'BULLISH' or 'BEARISH'
            liquidity_wall_data: From LiquidityWallAnalyzer
        
        Returns:
            (score, reason, details)
            score: 0-2
        """
        details = {
            'has_bid_wall': False,
            'has_ask_wall': False,
            'bid_wall_strength': 0,
            'ask_wall_strength': 0
        }
        
        if direction == 'BULLISH':
            # For LONG, check for Bid Wall (support)
            bid_wall = liquidity_wall_data.get('nearest_bid_wall')
            
            if bid_wall:
                details['has_bid_wall'] = True
                details['bid_wall_strength'] = bid_wall.strength
                details['bid_wall_price'] = bid_wall.price
                details['bid_wall_volume'] = bid_wall.volume
                
                # Score based on wall strength
                if bid_wall.strength >= 2:
                    return 2, f'BID_WALL_Q{bid_wall.strength}', details
                elif bid_wall.strength >= 1:
                    return 1, f'BID_WALL_Q{bid_wall.strength}', details
            
            details['fail_reason'] = 'No bid wall support nearby'
            return 0, '', details
        
        else:  # BEARISH
            # For SHORT, check for Ask Wall (resistance)
            ask_wall = liquidity_wall_data.get('nearest_ask_wall')
            
            if ask_wall:
                details['has_ask_wall'] = True
                details['ask_wall_strength'] = ask_wall.strength
                details['ask_wall_price'] = ask_wall.price
                details['ask_wall_volume'] = ask_wall.volume
                
                # Score based on wall strength
                if ask_wall.strength >= 2:
                    return 2, f'ASK_WALL_Q{ask_wall.strength}', details
                elif ask_wall.strength >= 1:
                    return 1, f'ASK_WALL_Q{ask_wall.strength}', details
            
            details['fail_reason'] = 'No ask wall resistance nearby'
            return 0, '', details
    
    def _check_multi_candle_hold(
        self,
        direction: str,
        swing_level: float,
        candles: pd.DataFrame
    ) -> Tuple[int, str, Dict]:
        """
        Check if price held above/below the broken level for multiple candles.
        
        Args:
            direction: 'BULLISH' or 'BEARISH'
            swing_level: The level that was broken
            candles: OHLCV DataFrame
        
        Returns:
            Tuple of (score, reason, details)
        """
        if len(candles) < 2:
            return 0, '', {}
        
        details = {}
        
        # Check if price held for at least 2 candles after break
        last_two = candles.iloc[-2:]
        
        if direction == 'BULLISH':
            # For bullish break, price should stay above swing level
            held_count = sum(1 for _, row in last_two.iterrows() if row['close'] > swing_level)
        else:
            # For bearish break, price should stay below swing level
            held_count = sum(1 for _, row in last_two.iterrows() if row['close'] < swing_level)
        
        details['held_candles'] = held_count
        
        if held_count >= 2:
            return 2, 'MULTI_CANDLE_HOLD', details
        elif held_count >= 1:
            return 1, 'PARTIAL_HOLD', details
        
        return 0, '', details
    
    def _determine_status(self, score: int) -> BOSStatus:
        """
        Determine BOS status from score.
        
        Args:
            score: Total score (0-13)
        
        Returns:
            BOSStatus enum
        """
        if score >= self.confirmed_threshold:
            return BOSStatus.CONFIRMED
        elif score >= self.pending_threshold:
            return BOSStatus.PENDING
        else:
            return BOSStatus.SWEEP
    
    def _empty_result(self) -> Dict:
        """Return empty result for insufficient data."""
        return {
            'score': 0,
            'max_score': 15,
            'status': BOSStatus.SWEEP,
            'reasons': ['INSUFFICIENT_DATA'],
            'details': {}
        }
    
    def get_required_score(self, status: BOSStatus) -> int:
        """
        Get minimum score required for a status.
        
        Args:
            status: BOSStatus enum
        
        Returns:
            Minimum score required
        """
        if status == BOSStatus.CONFIRMED:
            return self.confirmed_threshold
        elif status == BOSStatus.PENDING:
            return self.pending_threshold
        else:
            return 0
    
    def get_scoring_factors(self) -> Dict:
        """
        Get list of scoring factors and their max points.
        
        Returns:
            Dictionary of factors and max points
        """
        return {
            'body_close_confirmation': 3,
            'cvd_confirmation': 2,
            'oi_increase': 2,
            'displacement_ratio': 2,
            'volume_at_break': 1,
            'poc_proximity': 1,
            'liquidity_wall': 2,
            'total_max': 13
        }
