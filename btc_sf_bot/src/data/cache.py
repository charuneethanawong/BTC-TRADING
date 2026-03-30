"""
In-Memory Cache Module
"""
from typing import Dict, List, Optional, Any
from collections import deque
from datetime import datetime, timedelta
import time

from ..utils.logger import get_logger

logger = get_logger(__name__)


class Cache:
    """In-memory cache for trading data."""
    
    def __init__(self, max_trades: int = 1000, max_candles: int = 500):
        """
        Initialize cache.
        
        Args:
            max_trades: Maximum number of trades to store
            max_candles: Maximum number of candles to store
        """
        self.max_trades = max_trades
        self.max_candles = max_candles
        
        # Cache storage
        self._trades: deque = deque(maxlen=max_trades)
        self._order_book: Dict = {'bids': {}, 'asks': {}}
        self._ticker: Dict = {}
        self._candles: Dict[str, deque] = {}
        self._delta_history: deque = deque(maxlen=100)
        self._volume_history: deque = deque(maxlen=100)
        
        # Timestamps
        self._last_update: Dict[str, float] = {}
    
    # ==================== Trades ====================
    
    def add_trade(self, trade: Dict):
        """Add trade to cache."""
        self._trades.append(trade)
        self._last_update['trade'] = time.time()
    
    def get_trades(self, count: int = None) -> List[Dict]:
        """Get trades from cache."""
        if count:
            return list(self._trades)[-count:]
        return list(self._trades)
    
    def get_trades_in_range(self, start_time: int, end_time: int) -> List[Dict]:
        """Get trades within time range."""
        return [
            t for t in self._trades 
            if start_time <= t.get('time', 0) <= end_time
        ]
    
    # ==================== Order Book ====================
    
    def update_order_book(self, order_book: Dict):
        """Update order book in cache."""
        self._order_book = order_book
        self._last_update['order_book'] = time.time()
    
    def get_order_book(self) -> Dict:
        """Get order book from cache."""
        return self._order_book
    
    def get_bids(self) -> Dict[float, float]:
        """Get bids from cache."""
        return self._order_book.get('bids', {})
    
    def get_asks(self) -> Dict[float, float]:
        """Get asks from cache."""
        return self._order_book.get('asks', {})
    
    # ==================== Ticker ====================
    
    def update_ticker(self, ticker: Dict):
        """Update ticker in cache."""
        self._ticker = ticker
        self._last_update['ticker'] = time.time()
    
    def get_ticker(self) -> Dict:
        """Get ticker from cache."""
        return self._ticker
    
    def get_current_price(self) -> float:
        """Get current price from ticker."""
        return self._ticker.get('last', 0)
    
    # ==================== Candles ====================
    
    def add_candle(self, timeframe: str, candle: Dict):
        """Add candle to cache."""
        if timeframe not in self._candles:
            self._candles[timeframe] = deque(maxlen=self.max_candles)
        
        self._candles[timeframe].append(candle)
        self._last_update[f'candle_{timeframe}'] = time.time()
    
    def get_candles(self, timeframe: str, count: int = None) -> List[Dict]:
        """Get candles from cache."""
        candles = self._candles.get(timeframe, [])
        if count:
            return list(candles)[-count:]
        return list(candles)
    
    def get_latest_candle(self, timeframe: str) -> Optional[Dict]:
        """Get latest candle."""
        candles = self._candles.get(timeframe, [])
        if candles:
            return candles[-1]
        return None
    
    # ==================== Delta & Volume ====================
    
    def add_delta(self, delta: float):
        """Add delta value to history."""
        self._delta_history.append({
            'value': delta,
            'time': time.time()
        })
    
    def get_delta_history(self, count: int = None) -> List[Dict]:
        """Get delta history."""
        if count:
            return list(self._delta_history)[-count:]
        return list(self._delta_history)
    
    def get_average_delta(self, count: int = 20) -> float:
        """Get average delta."""
        history = self.get_delta_history(count)
        if not history:
            return 0
        return sum(h['value'] for h in history) / len(history)
    
    def add_volume(self, volume: float):
        """Add volume value to history."""
        self._volume_history.append({
            'value': volume,
            'time': time.time()
        })
    
    def get_volume_history(self, count: int = None) -> List[Dict]:
        """Get volume history."""
        if count:
            return list(self._volume_history)[-count:]
        return list(self._volume_history)
    
    def get_average_volume(self, count: int = 20) -> float:
        """Get average volume."""
        history = self.get_volume_history(count)
        if not history:
            return 0
        return sum(h['value'] for h in history) / len(history)
    
    # ==================== Utilities ====================
    
    def get_last_update(self, key: str = None) -> Optional[float]:
        """Get last update timestamp."""
        if key:
            return self._last_update.get(key)
        return min(self._last_update.values()) if self._last_update else None
    
    def is_stale(self, key: str, max_age_seconds: float = 60) -> bool:
        """Check if cache is stale."""
        last_update = self.get_last_update(key)
        if last_update is None:
            return True
        return (time.time() - last_update) > max_age_seconds
    
    def clear(self):
        """Clear all cache."""
        self._trades.clear()
        self._order_book = {'bids': {}, 'asks': {}}
        self._ticker = {}
        self._candles.clear()
        self._delta_history.clear()
        self._volume_history.clear()
        self._last_update.clear()
        logger.info("Cache cleared")
    
    def get_stats(self) -> Dict:
        """Get cache statistics."""
        return {
            'trades_count': len(self._trades),
            'order_book_stale': self.is_stale('order_book'),
            'ticker_stale': self.is_stale('ticker'),
            'candles_timeframes': list(self._candles.keys()),
            'last_update': self.get_last_update()
        }


# Alias for backward compatibility
MarketCache = Cache


class CacheManager:
    """Manager for multiple caches."""
    
    def __init__(self):
        self.caches: Dict[str, Cache] = {}
    
    def create_cache(self, name: str, **kwargs) -> Cache:
        """Create a new cache."""
        cache = Cache(**kwargs)
        self.caches[name] = cache
        return cache
    
    def get_cache(self, name: str) -> Optional[Cache]:
        """Get cache by name."""
        return self.caches.get(name)
    
    def clear_all(self):
        """Clear all caches."""
        for cache in self.caches.values():
            cache.clear()
