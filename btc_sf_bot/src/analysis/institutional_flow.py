"""
Institutional Flow Intelligence (IFI) Module
Focuses on micro-analysis of order flow to detect Smart Money footprints.
"""
import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timezone
from collections import deque

from ..utils.logger import get_logger
from src.utils.decorators import log_errors, retry, circuit_breaker
from src.utils.metrics import timed_metric

logger = get_logger(__name__)

class InstitutionalFlowAnalyzer:
    """
    Institutional Flow Intelligence (IFI) Analyzer.
    - LP (Liquidity Purge): Stop run + Absorption + OI Spike
    - DB (Defensive Block): Iceberg detection + Refill rate
    - DA (Delta Absorption): Delta Efficiency Ratio (DER) + Exhaustion
    """

    def __init__(self, config: Dict = None):
        self.config = config or {}
        
        # LP (Liquidity Purge) Configuration
        # Architecture Plan Section 2.2: Reduced OI threshold for intraday detection
        self.lp_delta_spike_threshold = self.config.get('lp_delta_spike', 2.5)
        self.lp_oi_change_min = self.config.get('lp_oi_change', 0.15)  # Changed from 0.5% to 0.15%
        
        # DB (Defensive Block) Configuration
        self.db_min_refill_ratio = self.config.get('db_refill_ratio', 1.2)
        self.db_min_wall_persistence = self.config.get('db_persistence', 30) # seconds
        self.db_obi_threshold = self.config.get('db_obi_threshold', 0.7)
        
        # DA (Delta Absorption) Configuration
        self.da_der_threshold = self.config.get('da_der_threshold', 3.0)
        
        # === Section 37.2: Institutional Wall Memory (DB Pattern) ===
        # === Section 41.1: Extended Wall History (20 minutes for M5 support) ===
        # Cache for storing order book history (20 minutes = 4 M5 candles)
        self.wall_history_cache = deque(maxlen=600)  # ~20 min at 2s intervals (was 150 for 5min)
        self.wall_history_max_age = 1200  # 20 minutes in seconds (was 300)


    def _calculate_dynamic_wall_threshold(self) -> float:
        """
        Calculate dynamic minimum wall size based on recent order book history.
        """
        if len(self.wall_history_cache) < 2:
            return 250000.0  # Fallback to absolute minimum
            
        total_size = 0
        count = 0
        for snapshot in self.wall_history_cache:
            for price, size in snapshot.get('bids', [])[:3]:
                total_size += size
                count += 1
            for price, size in snapshot.get('asks', [])[:3]:
                total_size += size
                count += 1
                
        avg_depth = total_size / max(count, 1)
        absolute_min_floor = 150000.0  # Reduced absolute floor
        
        return max(avg_depth * 3.0, absolute_min_floor)

    def calculate_cancellation_rate(self, wall_price: float, side: str, lookback_seconds: int = 5) -> float:
        """
        Calculate Pre-emptive Wall Pulling Detection (Spoofing Guard).
        Returns cancellation rate (0.0 to 1.0)
        """
        if len(self.wall_history_cache) < 2:
            return 0.0
            
        from datetime import datetime, timezone
        import pandas as pd
        now = datetime.now(timezone.utc)
        cutoff_time = now - pd.Timedelta(seconds=lookback_seconds)
        
        initial_snapshot = None
        for snapshot in self.wall_history_cache:
            if snapshot['timestamp'] >= cutoff_time:
                initial_snapshot = snapshot
                break
                
        if not initial_snapshot:
            return 0.0
            
        current_snapshot = self.wall_history_cache[-1]
        
        def get_size(snapshot, side, wall_price):
            levels = snapshot['bids'] if side == 'bid' else snapshot['asks']
            for price, size in levels:
                if abs(price - wall_price) / wall_price < 0.001:
                    return size
            return 0.0
            
        initial_size = get_size(initial_snapshot, side, wall_price)
        current_size = get_size(current_snapshot, side, wall_price)
        
        if initial_size <= 0:
            return 0.0
            
        if current_size < initial_size:
            cancellation_rate = (initial_size - current_size) / initial_size
            return cancellation_rate
            
        return 0.0

    @log_errors
    @timed_metric("InstitutionalFlowAnalyzer.analyze_liquidity_purge")
    @retry(max_attempts=3, delay=0.1, backoff=2.0, exceptions=(Exception,))
    @circuit_breaker(failure_threshold=5, timeout=30.0, expected_exception=Exception)
    def analyze_liquidity_purge(self, price_action: Dict, flow_data: Dict) -> Tuple[bool, float, str]:
        """
        Analyze if a recent sweep is a true Institutional Liquidity Purge (LP).
        Requires:
        1. High Delta against the sweep direction (Absorption).
        2. Immediate Open Interest increase.
        """
        is_purge = False
        confidence = 0.0
        
        delta = flow_data.get('delta', 0)
        avg_delta = flow_data.get('avg_delta', 1)
        oi_change_pct = flow_data.get('oi_change_pct', 0)
        sweep_direction = price_action.get('sweep_direction') # 'LONG' or 'SHORT'

        # 1. Delta Spike (Absorption Check)
        delta_ratio = abs(delta) / max(abs(avg_delta), 1)
        is_absorbing = delta_ratio > self.lp_delta_spike_threshold
        
        # 2. Opposing Flow (If sweeping HIGHS, expect huge buy limit absorption = negative delta impact logically, depending on calculation)
        delta_opposing = (sweep_direction == 'SHORT' and delta > 0) or \
                         (sweep_direction == 'LONG' and delta < 0)

        # 3. OI Spike (Institutional Loading)
        oi_supporting = oi_change_pct > self.lp_oi_change_min

        if is_absorbing and delta_opposing and oi_supporting:
            is_purge = True
            confidence = min(100.0, (delta_ratio * 10) + (oi_change_pct * 20))
            reason = f"LP_DETECTED: Absorption={delta_ratio:.1f}x, OI_Spike={oi_change_pct:.2f}%"
        else:
            reason = f"NO_LP: Absorb={is_absorbing}, Oppose={delta_opposing}, OI={oi_supporting}"

        return is_purge, confidence, reason

    @log_errors
    @timed_metric("InstitutionalFlowAnalyzer.analyze_defensive_block")
    @retry(max_attempts=3, delay=0.1, backoff=2.0, exceptions=(Exception,))
    @circuit_breaker(failure_threshold=5, timeout=30.0, expected_exception=Exception)
    def analyze_defensive_block(self, wall_data: Dict, executed_volume: float) -> Tuple[bool, float, str]:
        """
        Analyze if a limit wall is a true Defensive Block (DB) / Iceberg.
        Requires:
        1. Wall size doesn't decrease proportionally to executed volume (Refill).
        2. High Order Book Imbalance (OBI).
        """
        initial_wall_size = wall_data.get('initial_size', 0)
        current_wall_size = wall_data.get('current_size', 0)
        obi = wall_data.get('obi', 0.5)
        
        if initial_wall_size == 0 or executed_volume == 0:
            return False, 0.0, "INSUFFICIENT_DATA"

        # Calculate theoretical size without refill
        expected_size = max(0, initial_wall_size - executed_volume)
        
        # Check Refill (Iceberg behavior)
        refill_amount = current_wall_size - expected_size
        refill_ratio = refill_amount / max(executed_volume, 1)

        is_iceberg = refill_ratio > self.db_min_refill_ratio
        obi_supporting = obi > self.db_obi_threshold

        if is_iceberg and obi_supporting:
            confidence = min(100.0, (refill_ratio * 20) + (obi * 50))
            return True, confidence, f"DB_DETECTED: Iceberg_Refill={refill_ratio:.2f}, OBI={obi:.2f}"
            
        return False, 0.0, f"NO_DB: Refill_Ratio={refill_ratio:.2f}"

    @log_errors
    @timed_metric("InstitutionalFlowAnalyzer.analyze_delta_absorption")
    @retry(max_attempts=3, delay=0.1, backoff=2.0, exceptions=(Exception,))
    @circuit_breaker(failure_threshold=5, timeout=30.0, expected_exception=Exception)
    def analyze_delta_absorption(self, price_movement: float, delta: float, volume: float) -> Tuple[bool, float, str]:
        """
        Analyze Delta Efficiency Ratio (DER) for exhaustion (DA Pattern).
        DER = Delta / Price Movement. High DER = Effort without result (Absorption).
        
        Fixed: Added abs() around price_movement and null value checks for API stability.
        """
        # Check for null/invalid values from API
        if price_movement is None or delta is None or volume is None:
            return False, 0.0, "INSUFFICIENT_API_DATA"
        
        # Use abs() to ensure positive values for ratio calculation
        abs_price_movement = abs(price_movement)
        abs_delta = abs(delta)
        
        if abs_price_movement < 1e-8:
            der = abs_delta / 1.0  # Prevent division by zero, base 1 tick
        else:
            der = abs_delta / abs_price_movement

        # Normalize DER relative to volume to avoid tiny noise
        normalized_der = der / max(abs(volume), 1)

        if normalized_der > self.da_der_threshold:
            confidence = min(100.0, normalized_der * 10)
            return True, confidence, f"DA_DETECTED: Extreme_DER={normalized_der:.2f}"

        return False, 0.0, f"NO_DA: DER={normalized_der:.2f}"
    
    # === Section 40: Advanced DA Precision (Institutional Upgrade) ===
    
    def analyze_volume_weighted_der(self, price_movement: float, delta: float, volume: float, 
                                     avg_volume: float, volume_threshold: float = 1.2) -> Tuple[bool, float, str]:
        """
        Section 40.1: Volume-Weighted DER (V-DER)
        
        Prevents ghost signals in quiet markets where price doesn't move but DER spikes.
        Logic: Accept DA signal only when Volume > 1.2x of 20-candle average.
        "No Volume, No Absorption"
        
        Args:
            price_movement: Price movement in points
            delta: Cumulative Volume Delta
            volume: Current volume
            avg_volume: Average volume over last 20 candles
            volume_threshold: Volume multiplier threshold (default 1.2x)
            
        Returns:
            Tuple[bool, float, str]: (is_valid, confidence, description)
        """
        # Check for null/invalid values
        if price_movement is None or delta is None or volume is None or avg_volume is None:
            return False, 0.0, "INSUFFICIENT_DATA"
        
        # Calculate volume ratio
        volume_ratio = volume / max(avg_volume, 1)
        
        # Volume check: Must be above threshold
        if volume_ratio < volume_threshold:
            return False, 0.0, f"V-DER_REJECTED: Volume too low ({volume_ratio:.2f}x < {volume_threshold}x)"
        
        # Calculate DER with volume weighting
        abs_price_movement = abs(price_movement)
        abs_delta = abs(delta)
        
        if abs_price_movement < 1e-8:
            der = abs_delta / 1.0
        else:
            der = abs_delta / abs_price_movement
        
        # Volume-weighted DER: Multiply by volume ratio for significance
        v_der = der * volume_ratio
        
        # Normalize
        normalized_v_der = v_der / max(abs(volume), 1)
        
        if normalized_v_der > self.da_der_threshold:
            confidence = min(100.0, normalized_v_der * 10)
            return True, confidence, f"V-DER_DETECTED: {normalized_v_der:.2f} (Vol: {volume_ratio:.2f}x)"
        
        return False, 0.0, f"NO_V-DER: {normalized_v_der:.2f}"
    
    def analyze_cvd_slope_divergence(self, cvd_values: List[float], price_highs: List[float], 
                                      lookback: int = 5) -> Tuple[bool, float, str]:
        """
        Section 40.2: CVD Slope Divergence Analysis
        
        Analyzes the slope of Cumulative Volume Delta (CVD) line.
        If price makes Higher High but CVD Slope is negative or flat → Institutional Exhaustion.
        
        Args:
            cvd_values: List of CVD values over time
            price_highs: List of price highs over time
            lookback: Number of candles to analyze (default 5)
            
        Returns:
            Tuple[bool, float, str]: (is_exhaustion, bonus_score, description)
        """
        if len(cvd_values) < lookback or len(price_highs) < lookback:
            return False, 0.0, "INSUFFICIENT_DATA"
        
        try:
            import numpy as np
            
            # Get recent values
            recent_cvd = cvd_values[-lookback:]
            recent_highs = price_highs[-lookback:]
            
            # Calculate CVD slope using linear regression
            x = np.arange(len(recent_cvd))
            cvd_slope = np.polyfit(x, recent_cvd, 1)[0]  # Slope coefficient
            
            # Check for Higher High in price
            higher_high = recent_highs[-1] > recent_highs[-2] if len(recent_highs) >= 2 else False
            
            # Institutional Exhaustion: Price Higher High + CVD Slope negative/flat
            if higher_high and cvd_slope <= 0:
                bonus_score = 3.0
                return True, bonus_score, f"INSTITUTIONAL_EXHAUSTION: HH + CVD_Slope={cvd_slope:.4f}"
            
            # Partial exhaustion: Price Higher High + CVD Slope weak positive
            if higher_high and 0 < cvd_slope < 0.1:
                bonus_score = 1.5
                return True, bonus_score, f"PARTIAL_EXHAUSTION: HH + Weak_CVD_Slope={cvd_slope:.4f}"
            
            return False, 0.0, f"NO_EXHAUSTION: CVD_Slope={cvd_slope:.4f}"
            
        except Exception as e:
            return False, 0.0, f"ERROR: {str(e)}"
    
    def check_micro_expansion(self, current_price: float, candle_open: float, 
                               direction: str, min_expansion_pct: float = 0.05) -> Tuple[bool, str]:
        """
        Section 40.3: Micro-Expansion Confirmation
        
        Prevents entering too early during accumulation.
        After DER Spike, wait for price to move at least 0.05% from Open in reversal direction.
        
        Args:
            current_price: Current price
            candle_open: Open price of current candle
            direction: 'LONG' or 'SHORT' (reversal direction)
            min_expansion_pct: Minimum expansion percentage (default 0.05%)
            
        Returns:
            Tuple[bool, str]: (is_confirmed, description)
        """
        if current_price <= 0 or candle_open <= 0:
            return False, "INVALID_PRICES"
        
        # Calculate expansion percentage
        expansion_pct = abs(current_price - candle_open) / candle_open * 100
        
        # Check if expansion is in the reversal direction
        if direction == 'LONG':
            # For LONG: Price should be above open (expansion upward)
            is_expanding = current_price > candle_open and expansion_pct >= min_expansion_pct
        else:  # SHORT
            # For SHORT: Price should be below open (expansion downward)
            is_expanding = current_price < candle_open and expansion_pct >= min_expansion_pct
        
        if is_expanding:
            return True, f"MICRO_EXPANSION_CONFIRMED: {expansion_pct:.3f}% in {direction} direction"
        
        return False, f"NO_EXPANSION: {expansion_pct:.3f}% < {min_expansion_pct}% required"
    
    # === Section 37.2: Institutional Wall Memory (DB Pattern) ===
    
    def update_wall_history(self, order_book: Dict) -> None:
        """
        Update wall history cache with current order book snapshot.
        
        Architecture Plan Section 37.2: Wall History Cache
        Stores order book data for the last 5 minutes to enable
        True Refill Analysis (comparing wall size before and after being hit).
        
        Args:
            order_book: Current order book with 'bids' and 'asks'
        """
        timestamp = datetime.now(timezone.utc)
        
        # Extract top walls from order book
        bids = order_book.get('bids', [])
        asks = order_book.get('asks', [])
        
        # Get top 5 levels
        top_bids = bids[:5] if bids else []
        top_asks = asks[:5] if asks else []
        
        snapshot = {
            'timestamp': timestamp,
            'bids': [(float(b[0]), float(b[1])) for b in top_bids if len(b) >= 2],
            'asks': [(float(a[0]), float(a[1])) for a in top_asks if len(a) >= 2]
        }
        
        self.wall_history_cache.append(snapshot)
    
    def get_wall_size_change(self, price_level: float, side: str, lookback_seconds: int = 300) -> Dict:
        """
        Compare wall size at a price level before and after being hit.
        
        Architecture Plan Section 37.2: True Refill Analysis
        Returns the change in wall size over the specified time period.
        
        Args:
            price_level: The price level to check
            side: 'bid' or 'ask'
            lookback_seconds: How far back to look (default 5 minutes)
            
        Returns:
            Dict with initial_size, current_size, refill_amount, refill_ratio
        """
        if len(self.wall_history_cache) < 2:
            return {'initial_size': 0, 'current_size': 0, 'refill_amount': 0, 'refill_ratio': 0}
        
        now = datetime.now(timezone.utc)
        cutoff_time = now - pd.Timedelta(seconds=lookback_seconds)
        
        # Find initial snapshot (oldest within lookback)
        initial_snapshot = None
        initial_size = 0
        
        for snapshot in self.wall_history_cache:
            if snapshot['timestamp'] >= cutoff_time:
                initial_snapshot = snapshot
                break
        
        if initial_snapshot is None:
            return {'initial_size': 0, 'current_size': 0, 'refill_amount': 0, 'refill_ratio': 0}
        
        # Get initial size at price level
        levels = initial_snapshot['bids'] if side == 'bid' else initial_snapshot['asks']
        for level_price, level_size in levels:
            if abs(level_price - price_level) / price_level < 0.001:  # Within 0.1%
                initial_size = level_size
                break
        
        # Get current size from latest snapshot
        current_snapshot = self.wall_history_cache[-1]
        current_size = 0
        levels = current_snapshot['bids'] if side == 'bid' else current_snapshot['asks']
        for level_price, level_size in levels:
            if abs(level_price - price_level) / price_level < 0.001:
                current_size = level_size
                break
        
        # Calculate refill
        refill_amount = current_size - initial_size
        refill_ratio = refill_amount / max(initial_size, 1) if initial_size > 0 else 0
        
        return {
            'initial_size': initial_size,
            'current_size': current_size,
            'refill_amount': refill_amount,
            'refill_ratio': refill_ratio,
            'lookback_seconds': lookback_seconds
        }
    
    def analyze_true_refill(self, wall_price: float, side: str, executed_volume: float) -> Tuple[bool, float, str]:
        """
        Section 37.2: True Refill Analysis for DB Pattern.
        
        Compares wall size before and after being hit to detect true iceberg behavior.
        A true refill means the wall was replenished after being consumed.
        
        Args:
            wall_price: The price level of the wall
            side: 'bid' or 'ask'
            executed_volume: Volume that was executed against the wall
            
        Returns:
            Tuple[bool, float, str]: (is_true_refill, confidence, description)
        """
        # Get wall size change from history
        change = self.get_wall_size_change(wall_price, side, lookback_seconds=300)
        
        initial_size = change['initial_size']
        current_size = change['current_size']
        refill_amount = change['refill_amount']
        
        if initial_size <= 0:
            return False, 0.0, "NO_HISTORY"
        
        # Calculate true refill ratio
        # If wall was hit and size increased or stayed same, it's a refill
        expected_size_after_hit = max(0, initial_size - executed_volume)
        actual_refill = current_size - expected_size_after_hit
        
        # True refill ratio: how much of the executed volume was replenished
        true_refill_ratio = actual_refill / max(executed_volume, 1)
        
        # Iceberg detection: refill ratio > 1.0 means more volume appeared than was consumed
        is_iceberg = true_refill_ratio > self.db_min_refill_ratio
        
        if is_iceberg:
            confidence = min(100.0, true_refill_ratio * 25)  # Scale to 0-100
            return True, confidence, f"TRUE_REFILL: Ratio={true_refill_ratio:.2f}, Initial={initial_size:.0f}, Current={current_size:.0f}"
        
        return False, 0.0, f"NO_REFILL: Ratio={true_refill_ratio:.2f}"
    
    # === Section 41.1: Wall Longevity Analysis ===
    
    def calculate_wall_longevity(self, wall_price: float, side: str, 
                                  min_stability_minutes: float = 15.0) -> Tuple[float, str, Dict]:
        """
        Section 41.1: Calculate wall longevity for Institutional Defense Grade A bonus.
        
        Analyzes how long a wall has been stable at a price level.
        Walls stable > 15 minutes get +5 bonus score ("Institutional Defense Grade A").
        
        Args:
            wall_price: The price level of the wall
            side: 'bid' or 'ask'
            min_stability_minutes: Minimum stability time for bonus (default 15 minutes)
            
        Returns:
            Tuple[float, str, Dict]: (bonus_score, grade, details)
                - bonus_score: 0-5 points based on longevity
                - grade: 'A', 'B', or 'C'
                - details: Dict with longevity metrics
        """
        if len(self.wall_history_cache) < 2:
            return 0.0, "C", {'longevity_seconds': 0, 'reason': 'INSUFFICIENT_HISTORY'}
        
        now = datetime.now(timezone.utc)
        min_stability_seconds = min_stability_minutes * 60
        
        # Find first occurrence of wall at this price level
        first_seen = None
        initial_size = 0
        
        for snapshot in self.wall_history_cache:
            levels = snapshot['bids'] if side == 'bid' else snapshot['asks']
            for level_price, level_size in levels:
                # Check if price matches within 0.1% tolerance
                if abs(level_price - wall_price) / wall_price < 0.001:
                    first_seen = snapshot['timestamp']
                    initial_size = level_size
                    break
            if first_seen:
                break
        
        if first_seen is None:
            return 0.0, "C", {'longevity_seconds': 0, 'reason': 'WALL_NOT_FOUND'}
        
        # Calculate longevity
        longevity_seconds = (now - first_seen).total_seconds()
        longevity_minutes = longevity_seconds / 60
        
        # Check if wall size is still significant (not eroded)
        current_snapshot = self.wall_history_cache[-1]
        current_levels = current_snapshot['bids'] if side == 'bid' else current_snapshot['asks']
        current_size = 0
        
        for level_price, level_size in current_levels:
            if abs(level_price - wall_price) / wall_price < 0.001:
                current_size = level_size
                break
        
        # Size retention ratio
        size_retention = current_size / max(initial_size, 1) if initial_size > 0 else 0
        
        # Determine grade and bonus
        if longevity_seconds >= min_stability_seconds and size_retention >= 0.7:
            # Grade A: Stable > 15 min with > 70% size retention
            bonus_score = 5.0
            grade = "A"
            reason = f"INSTITUTIONAL_DEFENSE_GRADE_A: {longevity_minutes:.1f}min stable, {size_retention*100:.0f}% size"
        elif longevity_seconds >= min_stability_seconds * 0.5 and size_retention >= 0.5:
            # Grade B: Stable > 7.5 min with > 50% size retention
            bonus_score = 2.5
            grade = "B"
            reason = f"MODERATE_LONGEVITY: {longevity_minutes:.1f}min stable, {size_retention*100:.0f}% size"
        else:
            # Grade C: New or eroding wall
            bonus_score = 0.0
            grade = "C"
            reason = f"LOW_LONGEVITY: {longevity_minutes:.1f}min, {size_retention*100:.0f}% size"
        
        details = {
            'longevity_seconds': longevity_seconds,
            'longevity_minutes': longevity_minutes,
            'size_retention': size_retention,
            'initial_size': initial_size,
            'current_size': current_size,
            'grade': grade,
            'reason': reason
        }
        
        return bonus_score, grade, details
    
    def check_erosion_guard(self, wall_price: float, side: str, aggressor_volume: float,
                            refill_rate: float) -> Tuple[bool, str]:
        """
        Section 41.3: Erosion Guard - Prevent trading on collapsing walls.
        
        Measures the speed of aggressor market orders vs wall size.
        If aggressor volume hits the wall faster than 3.0x of refill rate,
        cancel DB signal temporarily (prevents getting run over by trucks).
        
        Args:
            wall_price: The price level of the wall
            side: 'bid' or 'ask'
            aggressor_volume: Volume of market orders hitting the wall
            refill_rate: Rate at which the wall is being replenished (volume/second)
            
        Returns:
            Tuple[bool, str]: (is_safe, description)
                - is_safe: True if wall is not being eroded, False if dangerous
                - description: Explanation of the guard decision
        """
        # Get current wall size
        if len(self.wall_history_cache) < 1:
            return True, "NO_HISTORY"
        
        current_snapshot = self.wall_history_cache[-1]
        levels = current_snapshot['bids'] if side == 'bid' else current_snapshot['asks']
        wall_size = 0
        
        for level_price, level_size in levels:
            if abs(level_price - wall_price) / wall_price < 0.001:
                wall_size = level_size
                break
        
        if wall_size <= 0:
            return True, "WALL_NOT_FOUND"
        
        # Calculate erosion rate
        # If aggressor volume is significant and refill rate is low
        if aggressor_volume <= 0:
            return True, "NO_AGGRESSOR_VOLUME"
        
        # Calculate impact ratio
        # How fast is the wall being consumed vs replenished
        if refill_rate > 0:
            erosion_ratio = aggressor_volume / refill_rate
        else:
            # No refill happening, high danger
            erosion_ratio = float('inf') if aggressor_volume > 0 else 0
        
        # Threshold: 3.0x means aggressor is 3x faster than refill
        erosion_threshold = 3.0
        
        if erosion_ratio > erosion_threshold:
            return False, f"EROSION_DETECTED: Aggressor {erosion_ratio:.1f}x faster than refill"
        
        # Additional check: Wall size vs aggressor
        # If aggressor volume > 50% of wall size, be cautious
        if aggressor_volume > wall_size * 0.5:
            return False, f"WALL_UNDER_PRESSURE: Aggressor {aggressor_volume:.0f} vs Wall {wall_size:.0f}"
        
        return True, f"SAFE: Erosion ratio {erosion_ratio:.2f}x < {erosion_threshold}x threshold"
    
    # === Section 2: Flow-Enhanced DB Intelligence ===
    
    def calculate_wall_zscore(self, wall_size: float, avg_wall_size: float, std_wall_size: float) -> Tuple[float, str]:
        """
        Section 1.1: Z-Score Significance for Wall Detection.
        
        Wall must be > 2.5 SD above average to be considered institutional.
        
        Args:
            wall_size: Current wall size in USD
            avg_wall_size: Average wall size from history
            std_wall_size: Standard deviation of wall sizes
            
        Returns:
            Tuple[float, str]: (z_score, significance_level)
        """
        if std_wall_size <= 0 or avg_wall_size <= 0:
            return 0.0, "INSUFFICIENT_DATA"
        
        z_score = (wall_size - avg_wall_size) / std_wall_size
        
        if z_score >= 3.0:
            significance = "VERY_STRONG"  # > 3 SD - Major institutional wall
        elif z_score >= 2.5:
            significance = "STRONG"  # > 2.5 SD - Institutional wall
        elif z_score >= 2.0:
            significance = "MODERATE"  # > 2 SD - Possible institutional
        elif z_score >= 1.5:
            significance = "WEAK"  # > 1.5 SD - Retail level
        else:
            significance = "INSIGNIFICANT"  # < 1.5 SD - Noise
        
        return z_score, significance
    
    def calculate_wall_specific_der(self, wall_price: float, side: str, 
                                      delta_at_wall: float, price_move_at_wall: float,
                                      volume_at_wall: float) -> Tuple[float, str]:
        """
        Section 1.2: Wall-Specific DER (Delta Efficiency Ratio at Wall).
        
        Measures absorption efficiency specifically at the wall level.
        High DER at wall = Iceberg Refill (institutional defense).
        
        Args:
            wall_price: The price level of the wall
            side: 'bid' or 'ask'
            delta_at_wall: Cumulative delta at wall level
            price_move_at_wall: Price movement while at wall
            volume_at_wall: Volume traded at wall level
            
        Returns:
            Tuple[float, str]: (der_score, description)
        """
        if price_move_at_wall == 0 or volume_at_wall == 0:
            return 0.0, "NO_MOVEMENT"
        
        # Calculate DER specifically at wall
        # High DER = Price not moving despite high delta = Absorption
        abs_price_move = abs(price_move_at_wall)
        abs_delta = abs(delta_at_wall)
        
        if abs_price_move < 1e-8:
            der = abs_delta / 1.0  # Prevent division by zero
        else:
            der = abs_delta / abs_price_move
        
        # Normalize by volume
        normalized_der = der / max(volume_at_wall, 1)
        
        # Score based on DER
        if normalized_der >= 3.0:
            score = 5.0  # Strong iceberg detection
            description = f"ICEBERG_DETECTED: DER={normalized_der:.2f} (Strong Absorption)"
        elif normalized_der >= 2.0:
            score = 3.0  # Moderate absorption
            description = f"ABSORPTION: DER={normalized_der:.2f} (Moderate)"
        elif normalized_der >= 1.0:
            score = 1.0  # Weak absorption
            description = f"WEAK_ABSORPTION: DER={normalized_der:.2f}"
        else:
            score = 0.0
            description = f"NO_ABSORPTION: DER={normalized_der:.2f}"
        
        return score, description
    
    def detect_stacking_vs_pulling(self, wall_price: float, side: str, 
                                     order_book_history: List[Dict]) -> Tuple[str, float, Dict]:
        """
        Section 1.2: Stacking vs Pulling Detection.
        
        Analyzes order book movements to distinguish real walls from spoofing.
        - Stacking: Orders being added (real institutional interest)
        - Pulling: Orders being removed (spoofing/exit liquidity)
        
        Args:
            wall_price: The price level of the wall
            side: 'bid' or 'ask'
            order_book_history: List of order book snapshots over time
            
        Returns:
            Tuple[str, float, Dict]: (behavior, confidence, details)
        """
        if len(order_book_history) < 3:
            return "UNKNOWN", 0.0, {"reason": "INSUFFICIENT_HISTORY"}
        
        # Track wall size changes over time
        wall_sizes = []
        for snapshot in order_book_history[-10:]:  # Last 10 snapshots
            levels = snapshot.get('bids', []) if side == 'bid' else snapshot.get('asks', [])
            wall_size = 0
            for level_price, level_size in levels:
                if abs(level_price - wall_price) / wall_price < 0.001:
                    wall_size = level_size
                    break
            wall_sizes.append(wall_size)
        
        if len(wall_sizes) < 3:
            return "UNKNOWN", 0.0, {"reason": "INSUFFICIENT_DATA"}
        
        # Calculate trend
        first_half = wall_sizes[:len(wall_sizes)//2]
        second_half = wall_sizes[len(wall_sizes)//2:]
        
        avg_first = sum(first_half) / len(first_half) if first_half else 0
        avg_second = sum(second_half) / len(second_half) if second_half else 0
        
        if avg_first <= 0:
            return "UNKNOWN", 0.0, {"reason": "ZERO_WALL_SIZE"}
        
        # Calculate change rate
        change_rate = (avg_second - avg_first) / avg_first
        
        # Determine behavior
        if change_rate > 0.2:  # 20% increase
            behavior = "STACKING"
            confidence = min(abs(change_rate) * 2, 1.0)
            details = {
                "reason": "Orders being added",
                "change_rate": change_rate,
                "avg_first": avg_first,
                "avg_second": avg_second
            }
        elif change_rate < -0.2:  # 20% decrease
            behavior = "PULLING"
            confidence = min(abs(change_rate) * 2, 1.0)
            details = {
                "reason": "Orders being removed (potential spoofing)",
                "change_rate": change_rate,
                "avg_first": avg_first,
                "avg_second": avg_second
            }
        else:
            behavior = "STABLE"
            confidence = 0.5
            details = {
                "reason": "Wall size stable",
                "change_rate": change_rate,
                "avg_first": avg_first,
                "avg_second": avg_second
            }
        
        return behavior, confidence, details
    
    def calculate_aggressor_exhaustion(self, cvd_values: List[float], 
                                         lookback: int = 10) -> Tuple[bool, float, str]:
        """
        Section 1.2: Aggressor Exhaustion Guard.
        
        Analyzes CVD slope to detect if aggressor momentum is fading before wall contact.
        Flat CVD slope = Aggressor exhaustion = Higher chance wall will hold.
        
        Args:
            cvd_values: List of CVD values
            lookback: Number of candles to analyze
            
        Returns:
            Tuple[bool, float, str]: (is_exhausted, slope, description)
        """
        if len(cvd_values) < lookback:
            return False, 0.0, "INSUFFICIENT_DATA"
        
        try:
            # Get recent CVD values
            recent_cvd = cvd_values[-lookback:]
            
            # Calculate CVD slope using linear regression
            x = np.arange(len(recent_cvd))
            slope = np.polyfit(x, recent_cvd, 1)[0]
            
            # Normalize slope by average CVD
            avg_cvd = np.mean(np.abs(recent_cvd))
            if avg_cvd > 0:
                normalized_slope = slope / avg_cvd
            else:
                normalized_slope = 0
            
            # Determine exhaustion
            # Flat or declining slope = Exhaustion
            if abs(normalized_slope) < 0.1:  # Very flat
                is_exhausted = True
                description = f"STRONG_EXHAUSTION: CVD slope flat ({normalized_slope:.4f})"
            elif normalized_slope < 0:  # Declining
                is_exhausted = True
                description = f"EXHAUSTION: CVD declining ({normalized_slope:.4f})"
            elif normalized_slope < 0.3:  # Weak momentum
                is_exhausted = True
                description = f"WEAK_MOMENTUM: CVD slope weak ({normalized_slope:.4f})"
            else:
                is_exhausted = False
                description = f"STRONG_MOMENTUM: CVD slope strong ({normalized_slope:.4f})"
            
            return is_exhausted, normalized_slope, description
            
        except Exception as e:
            return False, 0.0, f"ERROR: {str(e)}"


class WallClusterManager:
    """
    Section 1.1: Fuzzy Memory Wall Manager.
    
    Manages walls in $20 price buckets for continuity tracking.
    This allows tracking wall history across price movements.
    """
    
    def __init__(self, bucket_size: float = 20.0):
        """
        Initialize Wall Cluster Manager.
        
        Args:
            bucket_size: Price bucket size in USD (default $20)
        """
        self.bucket_size = bucket_size
        self.wall_buckets: Dict[int, Dict] = {}  # bucket_id -> wall_data
        
    def get_bucket_id(self, price: float) -> int:
        """Get bucket ID for a price level."""
        return int(price / self.bucket_size)
    
    def update_wall(self, price: float, size: float, side: str, timestamp: datetime):
        """
        Update wall in appropriate bucket.
        
        Args:
            price: Wall price
            size: Wall size in USD
            side: 'bid' or 'ask'
            timestamp: Current timestamp
        """
        bucket_id = self.get_bucket_id(price)
        
        if bucket_id not in self.wall_buckets:
            self.wall_buckets[bucket_id] = {
                'first_seen': timestamp,
                'last_seen': timestamp,
                'max_size': size,
                'total_updates': 1,
                'side': side,
                'prices': [price]
            }
        else:
            bucket = self.wall_buckets[bucket_id]
            bucket['last_seen'] = timestamp
            bucket['max_size'] = max(bucket['max_size'], size)
            bucket['total_updates'] += 1
            if price not in bucket['prices']:
                bucket['prices'].append(price)
    
    def get_wall_longevity(self, price: float, current_time: datetime) -> Tuple[float, int]:
        """
        Get wall longevity for a price level.
        
        Args:
            price: Wall price
            current_time: Current timestamp
            
        Returns:
            Tuple[float, int]: (longevity_seconds, update_count)
        """
        bucket_id = self.get_bucket_id(price)
        
        if bucket_id not in self.wall_buckets:
            return 0.0, 0
        
        bucket = self.wall_buckets[bucket_id]
        longevity = (current_time - bucket['first_seen']).total_seconds()
        
        return longevity, bucket['total_updates']
    
    def get_bucket_stats(self, price: float) -> Dict:
        """
        Get statistics for a bucket.
        
        Args:
            price: Wall price
            
        Returns:
            Dict with bucket statistics
        """
        bucket_id = self.get_bucket_id(price)
        
        if bucket_id not in self.wall_buckets:
            return {
                'bucket_id': bucket_id,
                'exists': False,
                'max_size': 0,
                'total_updates': 0,
                'price_range': []
            }
        
        bucket = self.wall_buckets[bucket_id]
        return {
            'bucket_id': bucket_id,
            'exists': True,
            'max_size': bucket['max_size'],
            'total_updates': bucket['total_updates'],
            'price_range': bucket['prices'],
            'side': bucket['side']
        }
    
    def cleanup_old_buckets(self, max_age_seconds: float = 1200):
        """
        Remove buckets older than max_age_seconds.
        
        Args:
            max_age_seconds: Maximum age in seconds (default 20 minutes)
        """
        current_time = datetime.now(timezone.utc)
        buckets_to_remove = []
        
        for bucket_id, bucket in self.wall_buckets.items():
            age = (current_time - bucket['last_seen']).total_seconds()
            if age > max_age_seconds:
                buckets_to_remove.append(bucket_id)
        
        for bucket_id in buckets_to_remove:
            del self.wall_buckets[bucket_id]



