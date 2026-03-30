"""
Position Flip Intelligence - Section 27.1
Detects rapid direction changes from order flow to prevent premature exits.

Key Features:
1. Tracks order flow direction changes
2. Detects rapid flips (absorption/exhaustion patterns)
3. Provides flip score for position management decisions
"""
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timezone, timedelta
from collections import deque
import numpy as np

from ..utils.logger import get_logger

logger = get_logger(__name__)


class PositionFlipIntelligence:
    """
    Detects rapid position direction changes to prevent premature exits.
    
    Section 27.1: Tracks order counts per direction and detects when:
    - 3+ orders in one direction followed by sudden opposite orders
    - This indicates potential absorption or exhaustion
    - Helps decide whether to close early or hold through noise
    """
    
    def __init__(self, config: Dict = None):
        self.config = config or {}
        
        pfi_config = self.config.get('position_flip_intelligence', {})
        
        # Order flow tracking
        self.order_history: deque = deque(maxlen=100)  # Last 100 orders
        self.direction_history: deque = deque(maxlen=50)  # Direction changes
        
        # Flip detection parameters
        self.min_orders_for_flip = pfi_config.get('min_orders_for_flip', 3)
        self.flip_threshold_multiplier = pfi_config.get('flip_threshold_multiplier', 2.0)
        self.lookback_seconds = pfi_config.get('lookback_seconds', 30)
        
        # CVD tracking
        self.cvd_history: deque = deque(maxlen=20)  # Last 20 CVD readings
        self.cvd_velocity_threshold = pfi_config.get('cvd_velocity_threshold', 0.5)
        
        # Whale tracking
        self.whale_history: deque = deque(maxlen=10)  # Last 10 whale trades
        
        # State
        self.last_flip_score = 0.0
        self.flip_warning_active = False
        self.flip_warning_reason = None
        
    def record_order(self, order: Dict):
        """
        Record an order for direction tracking.
        
        Args:
            order: {'direction': 'BUY'|'SELL', 'size': float, 'price': float, 'timestamp': datetime}
        """
        try:
            self.order_history.append({
                'direction': order.get('direction', 'UNKNOWN'),
                'size': order.get('size', 0),
                'price': order.get('price', 0),
                'timestamp': order.get('timestamp', datetime.now(timezone.utc))
            })
        except Exception as e:
            logger.debug(f"Error recording order: {e}")
    
    def record_cvd(self, cvd_value: float):
        """
        Record CVD value for velocity tracking.
        
        Args:
            cvd_value: Current CVD delta value
        """
        try:
            self.cvd_history.append({
                'value': cvd_value,
                'timestamp': datetime.now(timezone.utc)
            })
        except Exception as e:
            logger.debug(f"Error recording CVD: {e}")
    
    def record_whale(self, whale_data: Dict):
        """
        Record whale trade data.
        
        Args:
            whale_data: {'direction': 'BUY'|'SELL', 'value_usd': float, 'timestamp': datetime}
        """
        try:
            if whale_data.get('value_usd', 0) >=500_000:
                self.whale_history.append({
                    'direction': whale_data.get('direction', 'UNKNOWN'),
                    'value_usd': whale_data.get('value_usd', 0),
                    'timestamp': whale_data.get('timestamp', datetime.now(timezone.utc))
                })
        except Exception as e:
            logger.debug(f"Error recording whale: {e}")
    
    def calculate_flip_score(self, current_direction: str, binance_data: Dict) -> Tuple[float, Dict]:
        """
        Calculate flip score based on order flow analysis.
        
        Args:
            current_direction: Current position direction ('BUY' or'SELL')
            binance_data: Latest Binance market data
            
        Returns:
            Tuple[float, Dict]: (flip_score, details)
            - flip_score: 0-100, higher = more likely to flip
            - details: Analysis breakdown
        """
        try:
            score = 0.0
            details = {}
            
            # 1. Order Flow Analysis (0-30 points)
            order_score, order_details = self._analyze_order_flow(current_direction)
            score += order_score
            details['order_flow'] = order_details
            
            # 2. CVD Velocity Analysis (0-30 points)
            cvd_score, cvd_details = self._analyze_cvd_velocity(current_direction, binance_data)
            score += cvd_score
            details['cvd_velocity'] = cvd_details
            
            # 3. Whale Activity Analysis (0-25 points)
            whale_score, whale_details = self._analyze_whale_activity(current_direction, binance_data)
            score += whale_score
            details['whale_activity'] = whale_details
            
            # 4. Order Book Imbalance (0-15 points)
            ob_score, ob_details = self._analyze_order_book(current_direction, binance_data)
            score += ob_score
            details['order_book'] = ob_details
            
            self.last_flip_score = score
            self.flip_warning_active = score >= 70
            if self.flip_warning_active:
                self.flip_warning_reason = self._get_flip_reason(details)
            
            return score, details
            
        except Exception as e:
            logger.error(f"Error calculating flip score: {e}")
            return 0.0, {'error': str(e)}
    
    def _analyze_order_flow(self, current_direction: str) -> Tuple[float, Dict]:
        """
        Analyze recent order flow fordirection changes.
        
        Returns:
            Tuple[float, Dict]: (score, details)
        """
        if len(self.order_history) < self.min_orders_for_flip:
            return 0.0, {'status': 'insufficient_data'}
        
        try:
            now = datetime.now(timezone.utc)
            recent_orders = [
                o for o in self.order_history
                if (now - o['timestamp']).total_seconds() <= self.lookback_seconds
            ]
            
            if len(recent_orders) < self.min_orders_for_flip:
                return 0.0, {'status': 'insufficient_recent_orders'}
            
            # Count orders by direction
            buy_orders = [o for o in recent_orders if o['direction'] == 'BUY']
            sell_orders = [o for o in recent_orders if o['direction'] == 'SELL']
            
            buy_count = len(buy_orders)
            sell_count = len(sell_orders)
            
            # Calculate direction ratio
            total = buy_count + sell_count
            buy_ratio = buy_count / total if total > 0 else 0.5
            sell_ratio = sell_count / total if total > 0 else 0.5
            
            # Detect flip: opposite direction overwhelming
            opposite_direction = 'SELL' if current_direction == 'BUY' else 'BUY'
            opposite_ratio = sell_ratio if opposite_direction == 'SELL' else buy_ratio
            
            score = 0.0
            details = {
                'buy_count': buy_count,
                'sell_count': sell_count,
                'opposite_ratio': opposite_ratio
            }
            
            # Score based on opposite direction dominance
            if opposite_ratio > 0.7:
                score = 30.0  # Strong flip signal
                details['signal'] = 'strong_opposite_flow'
            elif opposite_ratio > 0.6:
                score = 20.0
                details['signal'] = 'moderate_opposite_flow'
            elif opposite_ratio > 0.55:
                score = 10.0
                details['signal'] = 'weak_opposite_flow'
            else:
                details['signal'] = 'aligned_flow'
            
            return score, details
            
        except Exception as e:
            logger.debug(f"Order flow analysis error: {e}")
            return 0.0, {'error': str(e)}
    
    def _analyze_cvd_velocity(self, current_direction: str, binance_data: Dict) -> Tuple[float, Dict]:
        """
        Analyze CVD velocity for exhaustion detection.
        
        Returns:
            Tuple[float, Dict]: (score, details)
        """
        try:
            cvd_data = binance_data.get('cvd', {})
            current_cvd = cvd_data.get('cvd', 0)
            
            # Record current CVD
            self.record_cvd(current_cvd)
            
            if len(self.cvd_history) < 3:
                return 0.0, {'status': 'insufficient_cvd_history'}
            
            # Calculate CVD velocity (change rate)
            recent_cvd = list(self.cvd_history)[-5:]
            values = [c['value'] for c in recent_cvd]
            
            if len(values) < 2:
                return 0.0, {'status': 'insufficient_values'}
            
            # Velocity = (current - previous) / abs(previous)
            cvd_changes = []
            for i in range(1, len(values)):
                prev = values[i-1]
                curr = values[i]
                if prev != 0:
                    cvd_changes.append((curr - prev) / abs(prev))
            
            if not cvd_changes:
                return 0.0, {'status': 'no_changes'}
            
            avg_velocity = np.mean(cvd_changes)
            
            score = 0.0
            details = {
                'cvd_values': values[-3:],
                'avg_velocity': avg_velocity
            }
            
            # Detect velocity reversal
            if current_direction == 'BUY':
                # For long, watch for CVD velocity turning negative
                if avg_velocity < -self.cvd_velocity_threshold:
                    score = 30.0
                    details['signal'] = 'cvd_velocity_reversal_bearish'
                elif avg_velocity < -self.cvd_velocity_threshold * 0.5:
                    score = 15.0
                    details['signal'] = 'cvd_slowing_bearish'
                else:
                    details['signal'] = 'cvd_aligned'
            else:  # SELL
                # For short, watch for CVD velocity turning positive
                if avg_velocity > self.cvd_velocity_threshold:
                    score =30.0
                    details['signal'] = 'cvd_velocity_reversal_bullish'
                elif avg_velocity > self.cvd_velocity_threshold * 0.5:
                    score = 15.0
                    details['signal'] = 'cvd_slowing_bullish'
                else:
                    details['signal'] = 'cvd_aligned'
            
            return score, details
            
        except Exception as e:
            logger.debug(f"CVD velocity analysis error: {e}")
            return 0.0, {'error': str(e)}
    
    def _analyze_whale_activity(self, current_direction: str, binance_data: Dict) -> Tuple[float, Dict]:
        """
        Analyze whale activity for contrarian signals.
        
        Returns:
            Tuple[float, Dict]: (score, details)
        """
        try:
            whales = binance_data.get('whales', {})
            buy_whales = whales.get('buy_whales', [])
            sell_whales = whales.get('sell_whales', [])
            
            # Record whales
            for w in buy_whales:
                self.record_whale({'direction': 'BUY', 'value_usd': w.get('value_usd', 0)})
            for w in sell_whales:
                self.record_whale({'direction': 'SELL', 'value_usd': w.get('value_usd', 0)})
            
            # Calculate whale pressure
            total_buy = whales.get('total_buy', 0)
            total_sell = whales.get('total_sell', 0)
            
            buy_count = len(buy_whales)
            sell_count = len(sell_whales)
            
            score = 0.0
            details = {
                'total_buy_usd': total_buy,
                'total_sell_usd': total_sell,
                'buy_count': buy_count,
                'sell_count': sell_count
            }
            
            # Detect contrarian whale activity
            if current_direction == 'BUY':
                # For long, watch for selling whales
                if total_sell > total_buy * 2 and total_sell > 1_000_000:
                    score = 25.0
                    details['signal'] = 'contrarian_whale_selling'
                elif total_sell > total_buy * 1.5:
                    score = 15.0
                    details['signal'] = 'moderate_whale_selling'
            else:  # SELL
                # For short, watch for buying whales
                if total_buy > total_sell * 2 and total_buy > 1_000_000:
                    score = 25.0
                    details['signal'] = 'contrarian_whale_buying'
                elif total_buy > total_sell * 1.5:
                    score = 15.0
                    details['signal'] = 'moderate_whale_buying'
            
            return score, details
            
        except Exception as e:
            logger.debug(f"Whale activity analysis error: {e}")
            return 0.0, {'error': str(e)}
    
    def _analyze_order_book(self, current_direction: str, binance_data: Dict) -> Tuple[float, Dict]:
        """
        Analyze order book for absorption signals.
        
        Returns:
            Tuple[float, Dict]: (score, details)
        """
        try:
            orderbook = binance_data.get('orderbook', {})
            if not orderbook:
                return 0.0, {'status': 'no_orderbook'}
            
            bid_walls = orderbook.get('bid_walls', [])
            ask_walls = orderbook.get('ask_walls', [])
            
            # Calculate wall imbalance
            total_bid_wall = sum(w.get('size', 0) for w in bid_walls)
            total_ask_wall = sum(w.get('size', 0) for w in ask_walls)
            
            score = 0.0
            details = {
                'bid_wall_total': total_bid_wall,
                'ask_wall_total': total_ask_wall,
                'bid_wall_count': len(bid_walls),
                'ask_wall_count': len(ask_walls)
            }
            
            # Detect wall imbalance against position
            if current_direction == 'BUY':
                # For long, watch for large ask walls
                if total_ask_wall > total_bid_wall * 2:
                    score = 15.0
                    details['signal'] = 'ask_wall_dominance'
                elif total_ask_wall > total_bid_wall * 1.5:
                    score = 8.0
                    details['signal'] = 'moderate_ask_wall'
            else:  # SELL
                # For short, watch for large bid walls
                if total_bid_wall > total_ask_wall * 2:
                    score = 15.0
                    details['signal'] = 'bid_wall_dominance'
                elif total_bid_wall > total_ask_wall * 1.5:
                    score = 8.0
                    details['signal'] = 'moderate_bid_wall'
            
            return score, details
            
        except Exception as e:
            logger.debug(f"Order book analysis error: {e}")
            return 0.0, {'error': str(e)}
    
    def _get_flip_reason(self, details: Dict) -> str:
        """
        Get human-readable flip warning reason.
        """
        reasons = []
        
        order_flow = details.get('order_flow', {})
        if order_flow.get('signal', '').startswith('strong'):
            reasons.append(f"Order flow: {order_flow.get('signal')}")
        
        cvd = details.get('cvd_velocity', {})
        if cvd.get('signal', '').startswith('cvd_velocity'):
            reasons.append(f"CVD: {cvd.get('signal')}")
        
        whale = details.get('whale_activity', {})
        if whale.get('signal', '').startswith('contrarian'):
            reasons.append(f"Whale: {whale.get('signal')}")
        
        ob = details.get('order_book', {})
        if ob.get('signal', '').endswith('_dominance'):
            reasons.append(f"OrderBook: {ob.get('signal')}")
        
        return '; '.join(reasons) if reasons else 'Multiple signals'
    
    def should_close_early(self, current_direction: str, binance_data: Dict) -> Tuple[bool, float, str]:
        """
        Determine if position should close early based on flip analysis.
        
        Args:
            current_direction: Current position direction ('BUY' or 'SELL')
            binance_data: Latest market data
            
        Returns:
            Tuple[bool, float, str]: (should_close, flip_score, reason)
        """
        try:
            flip_score, details = self.calculate_flip_score(current_direction, binance_data)
            
            should_close = flip_score >= 70
            reason = self.flip_warning_reason if should_close else ''
            
            return should_close, flip_score, reason
            
        except Exception as e:
            logger.error(f"Error in should_close_early: {e}")
            return False, 0.0, str(e)
    
    def get_state(self) -> Dict:
        """Get current state for debugging/logging."""
        return {
            'last_flip_score': self.last_flip_score,
            'flip_warning_active': self.flip_warning_active,
            'flip_warning_reason': self.flip_warning_reason,
            'order_history_count': len(self.order_history),
            'cvd_history_count': len(self.cvd_history),
            'whale_history_count': len(self.whale_history)
        }
    
    def reset(self):
        """Reset all tracking state."""
        self.order_history.clear()
        self.direction_history.clear()
        self.cvd_history.clear()
        self.whale_history.clear()
        self.last_flip_score = 0.0
        self.flip_warning_active = False
        self.flip_warning_reason = None