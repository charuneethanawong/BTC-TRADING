"""
Liquidity Wall Analyzer Module
Analyzes Order Book depth for large limit orders (liquidity walls)
"""
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

from ..utils.logger import get_logger
from src.utils.decorators import log_errors, retry, circuit_breaker
from src.utils.metrics import timed_metric

logger = get_logger(__name__)


class WallType(Enum):
    BID_WALL = "BID_WALL"
    ASK_WALL = "ASK_WALL"
    NONE = "NONE"


@dataclass
class LiquidityWall:
    """Represents a liquidity wall in the order book."""
    price: float
    volume: float
    volume_ratio: float
    wall_type: WallType
    distance_pct: float
    strength: int  # 0-3 (0=weak, 3=very strong)


class LiquidityWallAnalyzer:
    """
    Analyzes Order Book for liquidity walls (large limit orders).
    
    A liquidity wall is a price level with significantly more volume
    than surrounding levels, indicating institutional interest.
    
    Scoring Impact:
    - Signal near Bid Wall (support) → +2 pts for LONG
    - Signal near Ask Wall (resistance) → +2 pts for SHORT
    """
    
    def __init__(self, config: Dict = None):
        """
        Initialize LiquidityWallAnalyzer.
        
        Args:
            config: Configuration dictionary
        """
        self.config = config or {}
        
        # Wall detection thresholds
        self.wall_volume_multiplier = self.config.get('wall_volume_multiplier', 3.0)
        self.min_wall_volume = self.config.get('min_wall_volume', 10.0)
        self.max_distance_pct = self.config.get('max_distance_pct', 0.5)
        self.depth_levels = self.config.get('depth_levels', 20)
    
    @log_errors
    @timed_metric("LiquidityWallAnalyzer.analyze")
    @retry(max_attempts=3, delay=0.1, backoff=2.0, exceptions=(Exception,))
    @circuit_breaker(failure_threshold=5, timeout=30.0, expected_exception=Exception)
    def analyze(
        self,
        order_book: Dict,
        current_price: float
    ) -> Dict:
        """
        Analyze order book for liquidity walls.
        
        Args:
            order_book: Dictionary with 'bids' and 'asks' (price: volume)
            current_price: Current market price
        
        Returns:
            {
                'bid_walls': List[LiquidityWall],
                'ask_walls': List[LiquidityWall],
                'nearest_bid_wall': Optional[LiquidityWall],
                'nearest_ask_wall': Optional[LiquidityWall],
                'total_bid_volume': float,
                'total_ask_volume': float,
                'imbalance_ratio': float,
                'has_nearby_wall': bool,
                'wall_support_score': int  # 0-2 for scoring
            }
        """
        bids = order_book.get('bids', {})
        asks = order_book.get('asks', {})
        
        if not bids and not asks:
            return self._empty_result()
        
        # Convert to sorted lists
        bid_list = sorted([(float(p), float(v)) for p, v in bids.items()], reverse=True)
        ask_list = sorted([(float(p), float(v)) for p, v in asks.items()])
        
        # Calculate averages
        bid_volumes = [v for _, v in bid_list]
        ask_volumes = [v for _, v in ask_list]
        
        avg_bid_volume = sum(bid_volumes) / len(bid_volumes) if bid_volumes else 0
        avg_ask_volume = sum(ask_volumes) / len(ask_volumes) if ask_volumes else 0
        
        # Find walls
        bid_walls = self._find_walls(
            bid_list, avg_bid_volume, WallType.BID_WALL, current_price, is_bid=True
        )
        ask_walls = self._find_walls(
            ask_list, avg_ask_volume, WallType.ASK_WALL, current_price, is_bid=False
        )
        
        # Find nearest walls
        nearest_bid_wall = self._find_nearest_wall(bid_walls, current_price, is_bid=True)
        nearest_ask_wall = self._find_nearest_wall(ask_walls, current_price, is_bid=False)
        
        # Calculate totals and imbalance
        total_bid_volume = sum(bid_volumes)
        total_ask_volume = sum(ask_volumes)
        imbalance_ratio = total_bid_volume / total_ask_volume if total_ask_volume > 0 else float('inf')
        
        # Determine wall support score
        has_nearby_wall, wall_support_score = self._calculate_wall_support(
            nearest_bid_wall, nearest_ask_wall, current_price
        )
        
        return {
            'bid_walls': bid_walls,
            'ask_walls': ask_walls,
            'nearest_bid_wall': nearest_bid_wall,
            'nearest_ask_wall': nearest_ask_wall,
            'total_bid_volume': total_bid_volume,
            'total_ask_volume': total_ask_volume,
            'imbalance_ratio': imbalance_ratio,
            'has_nearby_wall': has_nearby_wall,
            'wall_support_score': wall_support_score,
            'bid_wall_level': nearest_bid_wall.price if nearest_bid_wall else None,
            'ask_wall_level': nearest_ask_wall.price if nearest_ask_wall else None
        }
    
    def _find_walls(
        self,
        price_volume_list: List[Tuple[float, float]],
        avg_volume: float,
        wall_type: WallType,
        current_price: float,
        is_bid: bool
    ) -> List[LiquidityWall]:
        """
        Find liquidity walls in price-volume list.
        
        Args:
            price_volume_list: List of (price, volume) tuples
            avg_volume: Average volume
            wall_type: Type of wall (BID or ASK)
            current_price: Current price
            is_bid: True for bids, False for asks
        
        Returns:
            List of LiquidityWall objects
        """
        walls = []
        
        if avg_volume == 0:
            return walls
        
        threshold = avg_volume * self.wall_volume_multiplier
        
        for price, volume in price_volume_list:
            # Check if volume exceeds threshold
            if volume >= threshold and volume >= self.min_wall_volume:
                volume_ratio = volume / avg_volume
                
                # Calculate distance from current price
                distance = abs(current_price - price)
                distance_pct = (distance / current_price) * 100 if current_price > 0 else 0
                
                # Only consider walls within max distance
                if distance_pct <= self.max_distance_pct:
                    # Calculate strength (0-3)
                    if volume_ratio >= 5.0:
                        strength = 3  # Very strong
                    elif volume_ratio >= 4.0:
                        strength = 2  # Strong
                    else:
                        strength = 1  # Moderate
                    
                    wall = LiquidityWall(
                        price=price,
                        volume=volume,
                        volume_ratio=volume_ratio,
                        wall_type=wall_type,
                        distance_pct=distance_pct,
                        strength=strength
                    )
                    walls.append(wall)
        
        # Sort by volume (strongest first)
        walls.sort(key=lambda w: w.volume, reverse=True)
        
        return walls
    
    def _find_nearest_wall(
        self,
        walls: List[LiquidityWall],
        current_price: float,
        is_bid: bool
    ) -> Optional[LiquidityWall]:
        """
        Find the nearest wall to current price.
        
        For bids: find wall below current price (support)
        For asks: find wall above current price (resistance)
        
        Args:
            walls: List of walls
            current_price: Current price
            is_bid: True for bids, False for asks
        
        Returns:
            Nearest LiquidityWall or None
        """
        if not walls:
            return None
        
        valid_walls = []
        for wall in walls:
            if is_bid:
                # Bid wall should be below current price (support)
                if wall.price < current_price:
                    valid_walls.append(wall)
            else:
                # Ask wall should be above current price (resistance)
                if wall.price > current_price:
                    valid_walls.append(wall)
        
        if not valid_walls:
            return None
        
        # Sort by distance (nearest first)
        valid_walls.sort(key=lambda w: w.distance_pct)
        
        return valid_walls[0] if valid_walls else None
    
    def _calculate_wall_support(
        self,
        nearest_bid_wall: Optional[LiquidityWall],
        nearest_ask_wall: Optional[LiquidityWall],
        current_price: float
    ) -> Tuple[bool, int]:
        """
        Calculate wall support score for current price.
        
        Args:
            nearest_bid_wall: Nearest bid wall (support)
            nearest_ask_wall: Nearest ask wall (resistance)
            current_price: Current price
        
        Returns:
            Tuple of (has_nearby_wall, wall_support_score)
            wall_support_score: 0-2 (0=none, 2=strong support)
        """
        has_nearby_wall = False
        score = 0
        
        # Check bid wall support (for LONG signals)
        if nearest_bid_wall:
            has_nearby_wall = True
            # Stronger wall + closer = higher score
            if nearest_bid_wall.strength >= 2 and nearest_bid_wall.distance_pct <= 0.2:
                score = 2
            elif nearest_bid_wall.strength >= 1 and nearest_bid_wall.distance_pct <= 0.3:
                score = 1
        
        # Check ask wall resistance (for SHORT signals)
        if nearest_ask_wall:
            has_nearby_wall = True
            # For shorts, ask wall above = resistance = good for short
            if nearest_ask_wall.strength >= 2 and nearest_ask_wall.distance_pct <= 0.2:
                score = max(score, 2)
            elif nearest_ask_wall.strength >= 1 and nearest_ask_wall.distance_pct <= 0.3:
                score = max(score, 1)
        
        return has_nearby_wall, score
    
    def get_wall_score_for_direction(
        self,
        direction: str,
        analysis_result: Dict
    ) -> Tuple[int, str]:
        """
        Get wall support score for a specific direction.
        
        Args:
            direction: 'LONG' or 'SHORT'
            analysis_result: Result from analyze()
        
        Returns:
            Tuple of (score, reason)
            score: 0-2 points
        """
        if direction == 'LONG':
            # For LONG, we want bid wall support below
            bid_wall = analysis_result.get('nearest_bid_wall')
            if bid_wall:
                return bid_wall.strength, f'BID_WALL_Q{bid_wall.strength}'
        
        else:  # SHORT
            # For SHORT, we want ask wall resistance above
            ask_wall = analysis_result.get('nearest_ask_wall')
            if ask_wall:
                return ask_wall.strength, f'ASK_WALL_Q{ask_wall.strength}'
        
        return 0, ''
    
    def is_price_near_wall(
        self,
        current_price: float,
        wall_type: WallType,
        order_book: Dict,
        threshold_pct: float = 0.2
    ) -> bool:
        """
        Check if price is near a liquidity wall.
        
        Args:
            current_price: Current price
            wall_type: BID_WALL or ASK_WALL
            order_book: Order book data
            threshold_pct: Distance threshold in percent
        
        Returns:
            True if price is near a wall
        """
        analysis = self.analyze(order_book, current_price)
        
        if wall_type == WallType.BID_WALL:
            wall = analysis.get('nearest_bid_wall')
        else:
            wall = analysis.get('nearest_ask_wall')
        
        if wall:
            return wall.distance_pct <= threshold_pct
        
        return False
    
    def calculate_sweep_depth_score(
        self,
        sweep_high: float,
        sweep_low: float,
        range_high: float,
        range_low: float
    ) -> Tuple[int, str]:
        """
        P3.2: Calculate sweep depth scoring.
        
        Args:
            sweep_high: Highest price reached during sweep
            sweep_low: Lowest price reached during sweep
            range_high: High of the range being swept
            range_low: Low of the range being swept
        
        Returns:
            (score, description)
            - Deep sweep (>50% of range): +2 pts
            - Shallow sweep (20-50%): +1 pt
            - Very shallow (<20%): 0 pts
        """
        if range_high == range_low:
            return 0, "INVALID_RANGE"
        
        range_size = range_high - range_low
        sweep_range = sweep_high - sweep_low
        sweep_depth_pct = (sweep_range / range_size) * 100 if range_size > 0 else 0
        
        if sweep_depth_pct > 50:
            return 2, f"DEEP_SWEEP ({sweep_depth_pct:.1f}%)"
        elif sweep_depth_pct >= 20:
            return 1, f"SHALLOW_SWEEP ({sweep_depth_pct:.1f}%)"
        else:
            return 0, f"VERY_SHALLOW_SWEEP ({sweep_depth_pct:.1f}%)"
    
    def detect_spoofed_wall(
        self,
        wall_price: float,
        wall_volume: float,
        volume_at_level: float,
        price_reached: float,
        penetration_threshold: float = 0.001
    ) -> Tuple[bool, str]:
        """
        P3.3: Detect if a liquidity wall is likely spoofed.
        
        A spoofed wall has high volume displayed but price doesn't penetrate it,
        indicating the wall may be fake to manipulate sentiment.
        
        Args:
            wall_price: Price level of the wall
            wall_volume: Volume at wall level
            volume_at_level: Actual traded volume at this level
            price_reached: Highest/lowest price reached near this wall
            penetration_threshold: % threshold to consider as penetration
        
        Returns:
            (is_spoofed, reason)
        """
        if wall_volume <= 0:
            return False, "NO_WALL"
        
        volume_ratio = volume_at_level / wall_volume if wall_volume > 0 else 0
        
        if volume_ratio < 0.1 and wall_volume > 100:
            return True, f"HIGH_WALL_LOW_VOLUME (vol_ratio: {volume_ratio:.2f})"
        
        wall_penetration = abs(price_reached - wall_price) / wall_price if wall_price > 0 else 0
        
        if wall_penetration < penetration_threshold and wall_volume > 50:
            return True, f"NOT_PENETRATED (penetration: {wall_penetration*100:.2f}%)"
        
        if volume_ratio < 0.3 and wall_volume > 20:
            return True, f"WEAK_VOLUME_CONFIRMATION (vol_ratio: {volume_ratio:.2f})"
        
        return False, "GENUINE_WALL"
    
    def _empty_result(self) -> Dict:
        """Return empty result for insufficient data."""
        return {
            'bid_walls': [],
            'ask_walls': [],
            'nearest_bid_wall': None,
            'nearest_ask_wall': None,
            'total_bid_volume': 0,
            'total_ask_volume': 0,
            'imbalance_ratio': 1.0,
            'has_nearby_wall': False,
            'wall_support_score': 0,
            'bid_wall_level': None,
            'ask_wall_level': None
        }
