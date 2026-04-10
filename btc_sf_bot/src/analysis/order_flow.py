"""
Order Flow Analysis Module
"""
from typing import Dict, List, Tuple, Optional
import pandas as pd
import numpy as np

from .institutional_flow import InstitutionalFlowAnalyzer
from ..utils.logger import get_logger
from src.utils.decorators import log_errors, retry, circuit_breaker
from src.utils.metrics import timed_metric

logger = get_logger(__name__)


class OrderFlowAnalyzer:
    """Order Flow analysis for trading."""
    
    def __init__(self, config: Dict = None):
        """
        Initialize Order Flow analyzer.
        
        Args:
            config: Configuration dictionary
        """
        self.config = config or {}
        
        # Default parameters
        self.imbalance_threshold = self.config.get('imbalance_threshold', 2.0)
        self.divergence_lookback = self.config.get('divergence_lookback', 10)
        self.volume_ma_period = self.config.get('volume_ma_period', 20)
        self.volume_threshold = self.config.get('volume_threshold', 1.5)
        self.inst_flow = InstitutionalFlowAnalyzer(self.config.get('institutional', {}))
        self.last_price_movement = 0.0
        # v42.8: Data Gathering Buffers
        self.imbalance_history = []  # List of recent imbalance ratios
        self.wall_history = {}       # Price -> timestamp/seconds tracking
    
    @log_errors
    @timed_metric("OrderFlowAnalyzer.calculate_delta")
    @retry(max_attempts=3, delay=0.1, backoff=2.0, exceptions=(Exception,))
    @circuit_breaker(failure_threshold=5, timeout=30.0, expected_exception=Exception)
    def calculate_delta(self, trades: List[Dict]) -> float:
        """
        Calculate delta from trades.
        
        Args:
            trades: List of trade dictionaries
        
        Returns:
            Delta value (positive = buying pressure)
        """
        buy_volume = 0
        sell_volume = 0
        
        for t in trades:
            # Handle different formats from exchanges
            volume = t.get('volume') or t.get('amount') or 0
            side = t.get('side') or t.get('takerOrMaker')
            is_buyer_maker = t.get('is_buyer_maker')
            
            if is_buyer_maker is not None:
                # CCXT format: is_buyer_maker = True means sell
                if not is_buyer_maker:
                    buy_volume += float(volume)
                else:
                    sell_volume += float(volume)
            elif side:
                # Alternative format: side = 'buy' or 'sell'
                if side.lower() == 'buy':
                    buy_volume += float(volume)
                else:
                    sell_volume += float(volume)
        
        return buy_volume - sell_volume
    
    @log_errors
    @timed_metric("OrderFlowAnalyzer.calculate_cumulative_delta")
    @retry(max_attempts=3, delay=0.1, backoff=2.0, exceptions=(Exception,))
    @circuit_breaker(failure_threshold=5, timeout=30.0, expected_exception=Exception)
    def calculate_cumulative_delta(self, trades: List[Dict]) -> List[float]:
        """
        Calculate cumulative delta over time.
        
        Args:
            trades: List of trades
        
        Returns:
            List of cumulative delta values
        """
        cumulative = []
        delta = 0
        
        for trade in trades:
            # v42.2: Handle different trade formats (CCXT uses 'amount', not 'volume')
            volume = trade.get('volume') or trade.get('amount') or 0
            is_buyer_maker = trade.get('is_buyer_maker', False)
            
            if is_buyer_maker:
                delta -= float(volume)
            else:
                delta += float(volume)
            cumulative.append(delta)
        
        return cumulative
    
    @log_errors
    @timed_metric("OrderFlowAnalyzer.calculate_imbalance")
    @retry(max_attempts=3, delay=0.1, backoff=2.0, exceptions=(Exception,))
    @circuit_breaker(failure_threshold=5, timeout=30.0, expected_exception=Exception)
    def calculate_imbalance(self, bids, asks, levels: int = 20) -> float:
        """v42.3: Expand to 20 levels for better stability."""
        # Handle both formats
        if isinstance(bids, dict):
            bid_vols = list(bids.values())[:levels]
        elif isinstance(bids, list):
            bid_vols = [b[1] for b in bids[:levels] if len(b) >= 2]
        else:
            bid_vols = []

        if isinstance(asks, dict):
            ask_vols = list(asks.values())[:levels]
        elif isinstance(asks, list):
            ask_vols = [a[1] for a in asks[:levels] if len(a) >= 2]
        else:
            ask_vols = []

        bid_vol = sum(bid_vols)
        ask_vol = sum(ask_vols)

        if ask_vol <= 0.001: # Avoid div by zero
            return 20.0

        ratio = bid_vol / ask_vol
        return min(ratio, 20.0) # Lower cap for display stability
    
    def detect_cvd_divergence(
        self, 
        prices: List[float], 
        cvd_series: List[float],
        window: int = 20
    ) -> Tuple[str, float]:
        """
        Detect robust CVD vs Price divergence using swing points.
        """
        if len(prices) < window or len(cvd_series) < window:
            return "NONE", 0
            
        # Find swing points
        def get_swings(data, w=5):
            highs = []
            lows = []
            for i in range(w, len(data) - w):
                if data[i] == max(data[i-w:i+w+1]):
                    highs.append(i)
                if data[i] == min(data[i-w:i+w+1]):
                    lows.append(i)
            return highs, lows
            
        p_highs, p_lows = get_swings(prices)
        
        if len(p_highs) < 2 or len(p_lows) < 2:
            return "NONE", 0
            
        # Bearish Divergence: Price HH, CVD LH
        if prices[p_highs[-1]] > prices[p_highs[-2]] and cvd_series[p_highs[-1]] < cvd_series[p_highs[-2]]:
            return "BEARISH", 1.0
            
        # Bullish Divergence: Price LL, CVD HL
        if prices[p_lows[-1]] < prices[p_lows[-2]] and cvd_series[p_lows[-1]] > cvd_series[p_lows[-2]]:
            return "BULLISH", 1.0
            
        return "NONE", 0
            
        return "NONE", 0

    def analyze_cvd_trend(self, cvd_series: List[float], lookback: int = 10) -> str:
        """
        Analyze the trend of Cumulative Volume Delta.
        
        Returns:
            'BULLISH', 'BEARISH', or 'NEUTRAL'
        """
        if len(cvd_series) < lookback:
            return "NEUTRAL"
        
        recent = cvd_series[-lookback:]
        
        # Calculate slope using simple linear trend
        x = np.arange(len(recent))
        y = np.array(recent)
        
        if len(y) < 2:
            return "NEUTRAL"
            
        slope = np.polyfit(x, y, 1)[0]
        
        # Normalize slope by average volume if possible, or use threshold
        # For simplicity, use a small threshold
        if slope > 0.1:
            return "BULLISH"
        elif slope < -0.1:
            return "BEARISH"
        
        return "NEUTRAL"
    
    def detect_absorption(
        self, 
        volume: float, 
        price_change: float, 
        avg_volume: float,
        avg_change_abs: float,
        delta: float
    ) -> Tuple[bool, str]:
        """
        Detect absorption pattern (High Volume/Delta, low price move).
        """
        if avg_volume == 0 or avg_change_abs == 0:
            return False, ""
        
        volume_ratio = volume / avg_volume
        
        # Absorption: High volume but price stalling
        # Usually occurs at key levels
        if volume_ratio > 2.0 and abs(price_change) < avg_change_abs * 0.5:
            # Check delta to see who is being absorbed
            if delta > 10.0 and price_change <= 0:
                # Buyers buying market (high positive delta) but price not rising = Sellers absorbing
                return True, "SELL_ABSORPTION"
            elif delta < -10.0 and price_change >= 0:
                # Sellers selling market (high negative delta) but price not falling = Buyers absorbing
                return True, "BUY_ABSORPTION"
        
        return False, ""
    
    def detect_exhaustion(
        self, 
        delta: float, 
        avg_delta: float,
        price_change: float
    ) -> Tuple[bool, str]:
        """
        Detect delta climax / exhaustion (High Delta vs price move).
        """
        if avg_delta == 0:
            return False, ""
        
        delta_ratio = abs(delta) / abs(avg_delta)
        
        # Exhaustion: Extreme delta (> 3.5x average) 
        if delta_ratio > 3.5:
            if delta > 0 and price_change > 0:
                return True, "BUY_EXHAUSTION"
            elif delta < 0 and price_change < 0:
                return True, "SELL_EXHAUSTION"
        
        return False, ""
    
    def detect_delta_spike(
        self,
        delta: float,
        avg_delta: float,
        threshold: float = 2.0
    ) -> Tuple[bool, str]:
        """
        Detect delta spike (sudden surge in buying/selling pressure).
        
        Args:
            delta: Current delta
            avg_delta: Average delta
            threshold: Spike threshold (default 2.0x)
        
        Returns:
            Tuple of (is_spike, spike_type)
        """
        if avg_delta == 0:
            return False, ""
        
        delta_ratio = abs(delta) / abs(avg_delta)
        
        # Spike: Delta > threshold * average
        if delta_ratio >= threshold:
            if delta > 0:
                return True, "BUY_SPIKE"
            else:
                return True, "SELL_SPIKE"
        
        return False, ""
    
    def analyze_cvd_trend_quality(
        self, 
        cvd_series: List[float],
        prices: List[float] = None,
        lookback: int = 10
    ) -> Tuple[str, int]:
        """
        Analyze CVD trend with quality check.
        
        Args:
            cvd_series: Cumulative Volume Delta series
            prices: Price series (optional)
            lookback: Lookback period
            
        Returns:
            Tuple of (trend, quality)
            trend: 'BULLISH', 'BEARISH', or 'NEUTRAL'
            quality: 0-3
        """
        if len(cvd_series) < lookback:
            return "NEUTRAL", 0
        
        recent_cvd = cvd_series[-lookback:]
        
        # 1. Slope Strength
        x = np.arange(len(recent_cvd))
        y = np.array(recent_cvd)
        
        if len(y) < 2:
            return "NEUTRAL", 0
            
        slope = np.polyfit(x, y, 1)[0]
        
        # Determine trend direction
        if slope > 0.1:
            trend = 'BULLISH'
        elif slope < -0.1:
            trend = 'BEARISH'
        else:
            return "NEUTRAL", 0
        
        quality = 0
        
        # Quality 1: Slope Strength (> 0.5)
        slope_normalized = abs(slope) / (np.max(np.abs(y)) + 1e-6)
        if slope_normalized > 0.5:
            quality += 1
        
        # Quality 2: Price-CVD Alignment
        if prices is not None and len(prices) >= lookback:
            recent_prices = prices[-lookback:]
            x_prices = np.arange(len(recent_prices))
            y_prices = np.array(recent_prices)
            price_slope = np.polyfit(x_prices, y_prices, 1)[0]
            
            # Aligned = same direction
            if (slope > 0 and price_slope > 0) or (slope < 0 and price_slope < 0):
                quality += 1
        
        # Quality 3: Momentum (accelerating)
        if len(recent_cvd) >= 5:
            recent_5 = recent_cvd[-5:]
            x_5 = np.arange(len(recent_5))
            y_5 = np.array(recent_5)
            recent_slope = np.polyfit(x_5, y_5, 1)[0]
            
            # Accelerating
            if abs(recent_slope) > abs(slope):
                quality += 1
        
        return trend, quality
    
    def calculate_delta_per_candle(
        self, 
        candles: pd.DataFrame, 
        trades: List[Dict]
    ) -> pd.DataFrame:
        """
        Calculate delta for each candle.
        
        Args:
            candles: DataFrame with OHLCV
            trades: List of trades
        
        Returns:
            DataFrame with delta column
        """
        result = candles.copy()
        result['delta'] = 0.0
        
        for i in range(len(result)):
            candle = result.iloc[i]
            candle_start = candle.name
            candle_end = candle.name + pd.Timedelta(minutes=1)
            
            # Get trades in this candle
            candle_trades = [
                t for t in trades 
                if candle_start <= pd.to_datetime(t['time'], unit='ms') < candle_end
            ]
            
            result.iloc[i, result.columns.get_loc('delta')] = self.calculate_delta(candle_trades)
        
        return result
    
    def get_order_flow_summary(
        self, 
        bids: Dict[float, float],
        asks: Dict[float, float],
        trades: List[Dict],
        price: float,
        open_interest: float = None,
        prev_oi: float = None
    ) -> Dict:
        """
        Get complete order flow summary with Open Interest support.
        
        Args:
            bids: Order book bids
            asks: Order book asks
            trades: Recent trades
            price: Current price
            open_interest: Current Open Interest from Binance
            prev_oi: Previous Open Interest for change calculation
        
        Returns:
            Dictionary with order flow data
        """
        # v42.2: Normalize bids/asks to dict format for compatibility
        if isinstance(bids, list):
            # List format: [[price, volume], ...]
            bids_dict = {b[0]: b[1] for b in bids if len(b) >= 2}
            asks_dict = {a[0]: a[1] for a in asks if len(a) >= 2}
        else:
            bids_dict = bids if bids else {}
            asks_dict = asks if asks else {}
        
        # Calculate values
        imbalance = self.calculate_imbalance(bids_dict, asks_dict)
        delta = self.calculate_delta(trades)
        
        # Calculate CVD Tilt (Slope of recent delta)
        cvd_tilt = 0
        if len(trades) >= 20:
            cumulative = self.calculate_cumulative_delta(trades[-20:])
            x = np.arange(len(cumulative))
            y = np.array(cumulative)
            if len(y) >= 2:
                cvd_tilt = np.polyfit(x, y, 1)[0]
        
        # Determine imbalance direction
        if imbalance > self.imbalance_threshold:
            imbalance_direction = "BULLISH"
        elif imbalance < 1 / self.imbalance_threshold:
            imbalance_direction = "BEARISH"
        else:
            imbalance_direction = "NEUTRAL"
        
        # Calculate buy/sell percentages
        buy_vol = 0
        sell_vol = 0
        for t in trades:
            volume = t.get('volume') or t.get('amount') or 0
            is_buyer_maker = t.get('is_buyer_maker')
            side = t.get('side')
            
            if is_buyer_maker is not None:
                if not is_buyer_maker:
                    buy_vol += float(volume)
                else:
                    sell_vol += float(volume)
            elif side:
                if side.lower() == 'buy':
                    buy_vol += float(volume)
                else:
                    sell_vol += float(volume)
        
        total_vol = buy_vol + sell_vol
        
        # v26.1: DER = Delta / Volume (Fix 4A)
        der = abs(delta / total_vol) if total_vol > 0 else 0
        
        buy_pct = (buy_vol / total_vol * 100) if total_vol > 0 else 50
        sell_pct = (sell_vol / total_vol * 100) if total_vol > 0 else 50
        
        # OI Change
        oi_change = 0
        oi_change_pct = 0
        if open_interest is not None and prev_oi is not None:
            oi_change = open_interest - prev_oi
            if prev_oi > 0:
                oi_change_pct = (oi_change / prev_oi) * 100
        
        # CVD Delta Pct (Relative to volume)
        cvd_delta_pct = (delta / total_vol * 100) if total_vol > 0 else 0
        
        # Volume ratio (relative to typical volume - simplified)
        volume_ratio = 1.0
        
        return {
            'imbalance': imbalance,
            'imbalance_direction': imbalance_direction,
            'delta': delta,
            'der': round(der, 4),  # v26.1: Delta Efficiency Ratio (Fix 4A)
            'cvd_delta': cvd_delta_pct,  # SmartFlowManager expects cvd_delta
            'cvd_delta_pct': cvd_delta_pct,
            'buy_volume': buy_vol,
            'sell_volume': sell_vol,
            'buy_pct': buy_pct,
            'sell_pct': sell_pct,
            'total_volume': total_vol,
            'volume_ratio': volume_ratio,
            'price': price,
            'bid_volume': sum(bids_dict.values()),
            'ask_volume': sum(asks_dict.values()),
            'open_interest': open_interest,
            'oi_change': oi_change,
            'oi_change_pct': oi_change_pct,
            'cvd_tilt': cvd_tilt
        }
    
    def check_long_conditions(self, order_flow: Dict, avg_volume: float) -> Tuple[bool, str]:
        """
        Check long entry conditions.
        
        Args:
            order_flow: Order flow summary
            avg_volume: Average volume
        
        Returns:
            Tuple of (meets_conditions, reason)
        """
        reasons = []
        
        # Condition 1: Bullish Imbalance
        if order_flow['imbalance'] > self.imbalance_threshold:
            reasons.append(f"BULLISH_IMBALANCE({order_flow['imbalance']:.2f})")
        
        # Condition 2: Buying Pressure
        if order_flow['buy_pct'] > 60:
            reasons.append(f"BUYING_PRESSURE({order_flow['buy_pct']:.1f}%)")
        
        # Condition 3: Volume Confirmation
        if avg_volume > 0 and order_flow['total_volume'] > avg_volume * self.volume_threshold:
            reasons.append(f"VOLUME_CONF({order_flow['total_volume']/avg_volume:.2f}x)")
        
        # Need at least 2 conditions
        if len(reasons) >= 2:
            return True, " + ".join(reasons)
        
        return False, ""
    
    def check_short_conditions(self, order_flow: Dict, avg_volume: float) -> Tuple[bool, str]:
        """
        Check short entry conditions.
        
        Args:
            order_flow: Order flow summary
            avg_volume: Average volume
        
        Returns:
            Tuple of (meets_conditions, reason)
        """
        reasons = []
        
        # Condition 1: Bearish Imbalance
        if order_flow['imbalance'] < 1 / self.imbalance_threshold:
            reasons.append(f"BEARISH_IMBALANCE({order_flow['imbalance']:.2f})")
        
        # Condition 2: Selling Pressure
        if order_flow['sell_pct'] > 60:
            reasons.append(f"SELLING_PRESSURE({order_flow['sell_pct']:.1f}%)")
        
# Condition 3: Volume Confirmation
        if avg_volume > 0 and order_flow['total_volume'] > avg_volume * self.volume_threshold:
            reasons.append(f"VOLUME_CONF({order_flow['total_volume']/avg_volume:.2f}x)")
        
        # Need at least 2 conditions
        if len(reasons) >= 2:
            return True, " + ".join(reasons)
        
        return False, ""
    
    def set_price_movement(self, movement: float):
        """Set the price movement for the current tick/candle to calculate DER."""
        self.last_price_movement = movement

    def analyze_institutional_absorption(self, delta: float, volume: float) -> Tuple[bool, float, str]:
        """Analyze Delta Efficiency Ratio (DA Pattern)."""
        return self.inst_flow.analyze_delta_absorption(self.last_price_movement, delta, volume)
    
    # === Section 40.2: CVD Slope Divergence Analysis ===
    
    def calculate_cvd_slope(self, cvd_values: List[float], lookback: int = 5) -> Dict:
        """
        Section 40.2: Calculate CVD Slope for Divergence Analysis.
        
        Analyzes the slope of Cumulative Volume Delta (CVD) line to detect
        institutional exhaustion when price makes Higher High but CVD slope
        is negative or flat.
        
        Args:
            cvd_values: List of CVD values over time
            lookback: Number of candles to analyze (default 5)
            
        Returns:
            Dict with slope, direction, and divergence status
        """
        if len(cvd_values) < lookback:
            return {
                'slope': 0.0,
                'direction': 'NEUTRAL',
                'divergence': False,
                'status': 'insufficient_data'
            }
        
        try:
            # Get recent CVD values
            recent_cvd = cvd_values[-lookback:]
            
            # Calculate slope using linear regression
            x = np.arange(len(recent_cvd))
            slope, intercept = np.polyfit(x, recent_cvd, 1)
            
            # Determine direction
            if slope > 0.1:
                direction = 'BULLISH'
            elif slope < -0.1:
                direction = 'BEARISH'
            else:
                direction = 'NEUTRAL'
            
            # Calculate R-squared for slope reliability
            y_pred = slope * x + intercept
            ss_res = np.sum((recent_cvd - y_pred) ** 2)
            ss_tot = np.sum((recent_cvd - np.mean(recent_cvd)) ** 2)
            r_squared = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0
            
            return {
                'slope': float(slope),
                'intercept': float(intercept),
                'direction': direction,
                'r_squared': float(r_squared),
                'divergence': False,  # Will be set by divergence check
                'status': 'success'
            }
            
        except Exception as e:
            logger.debug(f"CVD slope calculation error: {e}")
            return {
                'slope': 0.0,
                'direction': 'NEUTRAL',
                'divergence': False,
                'status': f'error: {str(e)}'
            }
    
    def check_cvd_price_divergence(self, cvd_values: List[float], price_highs: List[float],
                                    price_lows: List[float], lookback: int = 5) -> Dict:
        """
        Section 40.2: Check for CVD-Price Divergence (Institutional Exhaustion).
        
        Detects when price makes Higher High but CVD Slope is negative/flat,
        indicating institutional exhaustion.
        
        Args:
            cvd_values: List of CVD values
            price_highs: List of price highs
            price_lows: List of price lows
            lookback: Number of candles to analyze
            
        Returns:
            Dict with divergence status and bonus score
        """
        if len(cvd_values) < lookback or len(price_highs) < lookback:
            return {
                'divergence': False,
                'type': 'NONE',
                'bonus_score': 0.0,
                'status': 'insufficient_data'
            }
        
        # Get CVD slope
        cvd_result = self.calculate_cvd_slope(cvd_values, lookback)
        cvd_slope = cvd_result['slope']
        
        # Check for Higher High in price
        recent_highs = price_highs[-lookback:]
        higher_high = recent_highs[-1] > recent_highs[-2] if len(recent_highs) >= 2 else False
        
        # Check for Lower Low in price
        recent_lows = price_lows[-lookback:]
        lower_low = recent_lows[-1] < recent_lows[-2] if len(recent_lows) >= 2 else False
        
        # Bullish Divergence: Price Lower Low + CVD Bullish (accumulation)
        if lower_low and cvd_slope > 0.1:
            return {
                'divergence': True,
                'type': 'BULLISH_DIVERGENCE',
                'bonus_score': 2.0,
                'cvd_slope': cvd_slope,
                'description': f"Bullish divergence: Price LL + CVD Bullish ({cvd_slope:.4f})",
                'status': 'success'
            }
        
        # Bearish Divergence (Institutional Exhaustion): Price Higher High + CVD Bearish/Flat
        if higher_high and cvd_slope <= 0:
            return {
                'divergence': True,
                'type': 'INSTITUTIONAL_EXHAUSTION',
                'bonus_score': 3.0,
                'cvd_slope': cvd_slope,
                'description': f"Institutional exhaustion: Price HH + CVD Bearish ({cvd_slope:.4f})",
                'status': 'success'
            }
        
        # Partial exhaustion: Price Higher High + CVD weak positive
        if higher_high and 0 < cvd_slope < 0.1:
            return {
                'divergence': True,
                'type': 'PARTIAL_EXHAUSTION',
                'bonus_score': 1.5,
                'cvd_slope': cvd_slope,
                'description': f"Partial exhaustion: Price HH + Weak CVD ({cvd_slope:.4f})",
                'status': 'success'
            }
        
        return {
            'divergence': False,
            'type': 'NONE',
            'bonus_score': 0.0,
            'cvd_slope': cvd_slope,
            'status': 'no_divergence'
        }



