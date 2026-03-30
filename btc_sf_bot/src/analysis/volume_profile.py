"""
Volume Profile Analysis Module
"""
from typing import Dict, List, Tuple, Optional
import pandas as pd
import numpy as np
from collections import defaultdict

from ..utils.logger import get_logger
from src.utils.decorators import log_errors, retry, circuit_breaker
from src.utils.metrics import timed_metric

logger = get_logger(__name__)


class VolumeProfileAnalyzer:
    """Volume Profile analysis for trading."""
    
    def __init__(self, config: Dict = None):
        """
        Initialize Volume Profile analyzer.
        
        Args:
            config: Configuration dictionary
        """
        self.config = config or {}
        
        # Default parameters
        self.bins = self.config.get('bins', 50)
        self.value_area_pct = self.config.get('value_area_pct', 0.70)
    
    @log_errors
    @timed_metric("VolumeProfileAnalyzer.calculate_profile")
    @retry(max_attempts=3, delay=0.1, backoff=2.0, exceptions=(Exception,))
    @circuit_breaker(failure_threshold=5, timeout=30.0, expected_exception=Exception)
    def calculate_profile(
        self, 
        high: float, 
        low: float, 
        volume_data: List[Tuple[float, float]]
    ) -> Dict[float, float]:
        """
        Calculate volume profile.
        
        Args:
            high: High price
            low: Low price
            volume_data: List of (price, volume) tuples
        
        Returns:
            Dictionary of price levels to volume
        """
        if high <= low or not volume_data:
            return {}
        
        # Calculate bin size
        price_range = high - low
        bin_size = price_range / self.bins
        
        if bin_size == 0:
            return {}
        
        # Initialize profile
        profile = defaultdict(float)
        
        # Distribute volume to bins
        for price, volume in volume_data:
            bin_idx = int((price - low) / bin_size)
            bin_price = low + (bin_idx + 0.5) * bin_size
            profile[bin_price] += volume
        
        return dict(profile)
    
    @log_errors
    @timed_metric("VolumeProfileAnalyzer.calculate_profile_from_df")
    @retry(max_attempts=3, delay=0.1, backoff=2.0, exceptions=(Exception,))
    @circuit_breaker(failure_threshold=5, timeout=30.0, expected_exception=Exception)
    def calculate_profile_from_df(self, df: pd.DataFrame) -> Dict[float, float]:
        """
        Calculate volume profile from OHLCV DataFrame.
        
        Args:
            df: DataFrame with high, low, volume columns
        
        Returns:
            Dictionary of price levels to volume
        """
        if df.empty:
            return {}
        
        high = df['high'].max()
        low = df['low'].min()
        
        # Create volume data from each candle - OPTIMIZED with vectorized binning
        volume_data = []
        
        # Calculate dynamic step size based on overall price range
        price_range = high - low
        if price_range > 0:
            # Use ~100 bins for entire dataset (adjustable via self.bins)
            dynamic_step = price_range / max(self.bins, 50)
        else:
            dynamic_step = 1.0  # Fallback
        
        # Use numpy for faster binning instead of loop
        for _, row in df.iterrows():
            if row['high'] > row['low']:
                # Create price bins for this candle using numpy
                candle_prices = np.linspace(row['low'], row['high'], 
                                             max(int((row['high'] - row['low']) / dynamic_step) + 1, 2))
                volume_per_bin = row['volume'] / len(candle_prices)
                for price in candle_prices:
                    volume_data.append((price, volume_per_bin))
        
        return self.calculate_profile(high, low, volume_data)
    
    @log_errors
    @timed_metric("VolumeProfileAnalyzer.find_poc")
    @retry(max_attempts=3, delay=0.1, backoff=2.0, exceptions=(Exception,))
    @circuit_breaker(failure_threshold=5, timeout=30.0, expected_exception=Exception)
    def find_poc(self, profile: Dict[float, float]) -> Optional[float]:
        """
        Find Point of Control (POC).
        
        Args:
            profile: Volume profile dictionary
        
        Returns:
            POC price or None
        """
        if not profile:
            return None
        
        return max(profile, key=profile.get)
    
    @log_errors
    @timed_metric("VolumeProfileAnalyzer.find_value_area")
    @retry(max_attempts=3, delay=0.1, backoff=2.0, exceptions=(Exception,))
    @circuit_breaker(failure_threshold=5, timeout=30.0, expected_exception=Exception)
    def find_value_area(
        self, 
        profile: Dict[float, float]
    ) -> Tuple[Optional[float], Optional[float], Optional[float]]:
        """
        Find Value Area (VAH, VAL).
        
        Args:
            profile: Volume profile dictionary
        
        Returns:
            Tuple of (VAH, VAL, POC)
        """
        if not profile:
            return None, None, None
        
        # Get POC
        poc = self.find_poc(profile)
        
        # Calculate total volume
        total_volume = sum(profile.values())
        
        # Sort profile by price
        sorted_profile = sorted(profile.items(), key=lambda x: x[1], reverse=True)
        
        # Find value area (70% of volume)
        target_volume = total_volume * self.value_area_pct
        current_volume = 0
        
        value_area_prices = []
        for price, volume in sorted_profile:
            current_volume += volume
            value_area_prices.append(price)
            if current_volume >= target_volume:
                break
        
        if not value_area_prices:
            return poc, poc, poc
        
        val = min(value_area_prices)
        vah = max(value_area_prices)
        
        return vah, val, poc
    
    @log_errors
    @timed_metric("VolumeProfileAnalyzer.find_high_volume_nodes")
    @retry(max_attempts=3, delay=0.1, backoff=2.0, exceptions=(Exception,))
    @circuit_breaker(failure_threshold=5, timeout=30.0, expected_exception=Exception)
    def find_high_volume_nodes(
        self, 
        profile: Dict[float, float],
        threshold: float = 1.5
    ) -> List[float]:
        """
        Find High Volume Nodes (HVN).
        
        Args:
            profile: Volume profile
            threshold: Multiplier above average to be considered HVN
        
        Returns:
            List of HVN prices
        """
        if not profile:
            return []
        
        avg_volume = sum(profile.values()) / len(profile)
        min_volume = avg_volume * threshold
        
        hvns = [price for price, vol in profile.items() if vol >= min_volume]
        
        # Sort by volume descending
        hvns.sort(key=lambda p: profile[p], reverse=True)
        
        return hvns
    
    def find_low_volume_nodes(
        self, 
        profile: Dict[float, float],
        threshold: float = 0.5
    ) -> List[float]:
        """
        Find Low Volume Nodes (LVN).
        
        Args:
            profile: Volume profile
            threshold: Multiplier below average to be considered LVN
        
        Returns:
            List of LVN prices
        """
        if not profile:
            return []
        
        avg_volume = sum(profile.values()) / len(profile)
        max_volume = avg_volume * threshold
        
        lvns = [price for price, vol in profile.items() if vol <= max_volume]
        
        # Sort by volume ascending
        lvns.sort(key=lambda p: profile[p])
        
        return lvns
    
    def detect_profile_shape(self, profile: Dict[float, float]) -> str:
        """
        Detect volume profile shape.
        
        Args:
            profile: Volume profile
        
        Returns:
            Profile shape type
        """
        if not profile:
            return "UNKNOWN"
        
        # Get POC position relative to range
        poc = self.find_poc(profile)
        prices = sorted(profile.keys())
        
        if not prices:
            return "UNKNOWN"
        
        range_position = (poc - prices[0]) / (prices[-1] - prices[0]) if prices[-1] > prices[0] else 0.5
        
        if range_position > 0.7:
            return "P_SHAPE"  # Bullish - volume at top
        elif range_position < 0.3:
            return "B_SHAPE"  # Bearish - volume at bottom
        else:
            return "BELL_SHAPE"  # Neutral - volume in middle
    
    def find_support_resistance_zones(
        self, 
        profile: Dict[float, float],
        current_price: float
    ) -> Dict[str, List[float]]:
        """
        Find support and resistance zones.
        
        Args:
            profile: Volume profile
            current_price: Current price
        
        Returns:
            Dictionary with 'support' and 'resistance' lists
        """
        hvns = self.find_high_volume_nodes(profile, threshold=1.2)
        
        support = [hvn for hvn in hvns if hvn < current_price]
        resistance = [hvn for hvn in hvns if hvn > current_price]
        
        # Sort
        support.sort(reverse=True)
        resistance.sort()
        
        return {
            'support': support[:5],  # Top 5 support levels
            'resistance': resistance[:5]  # Top 5 resistance levels
        }
    
    def get_volume_profile_summary(
        self, 
        candles: pd.DataFrame,
        current_price: float
    ) -> Dict:
        """
        Get complete volume profile summary.
        
        Args:
            candles: OHLCV DataFrame
            current_price: Current price
        
        Returns:
            Dictionary with volume profile data
        """
        profile = self.calculate_profile_from_df(candles)
        
        if not profile:
            return {}
        
        vah, val, poc = self.find_value_area(profile)
        hvns = self.find_high_volume_nodes(profile)
        lvns = self.find_low_volume_nodes(profile)
        shape = self.detect_profile_shape(profile)
        sr_zones = self.find_support_resistance_zones(profile, current_price)
        
        return {
            'profile': profile,
            'poc': poc,
            'vah': vah,
            'val': val,
            'hvns': hvns,
            'lvns': lvns,
            'shape': shape,
            'support_zones': sr_zones['support'],
            'resistance_zones': sr_zones['resistance'],
            'value_area_pct': self.value_area_pct * 100
        }
    
    def is_price_in_value(self, price: float, vah: float, val: float) -> bool:
        """
        Check if price is in value area.
        
        Args:
            price: Current price
            vah: Value Area High
            val: Value Area Low
        
        Returns:
            True if price is in value area
        """
        return val <= price <= vah
    
    def is_price_above_value(self, price: float, vah: float) -> bool:
        """Check if price is above value area (Premium)."""
        return price > vah
    
    def is_price_below_value(self, price: float, val: float) -> bool:
        """Check if price is below value area (Discount)."""
        return price < val
    
    def get_zone_context(
        self, 
        price: float,
        candles: pd.DataFrame
    ) -> str:
        """
        Get price zone context.
        
        Args:
            price: Current price
            candles: OHLCV DataFrame
        
        Returns:
            Zone context (PREMIUM, DISCOUNT, VALUE)
        """
        profile = self.calculate_profile_from_df(candles)
        
        if not profile:
            return "UNKNOWN"
        
        vah, val, poc = self.find_value_area(profile)
        
        if vah is None or val is None:
            return "UNKNOWN"
        
        if price > vah:
            return "PREMIUM"
        elif price < val:
            return "DISCOUNT"
        else:
            return "VALUE"
