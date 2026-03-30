"""
Performance metrics collection module.
"""
import time
import logging
import functools
from typing import Dict, Any, Optional, Callable
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
import threading

logger = logging.getLogger(__name__)


@dataclass
class Metric:
    """Individual metric measurement."""
    value: float
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    tags: Dict[str, str] = field(default_factory=dict)


@dataclass
class TimerMetric:
    """Timer metric for measuring execution times."""
    name: str
    times: deque = field(default_factory=lambda: deque(maxlen=1000))
    calls: int = 0
    total_time: float = 0.0
    min_time: float = float('inf')
    max_time: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock)
    
    def record(self, duration: float) -> None:
        """Record a timing measurement."""
        with self._lock:
            self.times.append(duration)
            self.calls += 1
            self.total_time += duration
            self.min_time = min(self.min_time, duration)
            self.max_time = max(self.max_time, duration)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get statistics for this timer."""
        with self._lock:
            if self.calls == 0:
                return {
                    'name': self.name,
                    'calls': 0,
                    'total_time': 0.0,
                    'avg_time': 0.0,
                    'min_time': 0.0,
                    'max_time': 0.0,
                    'recent_times': []
                }
            
            return {
                'name': self.name,
                'calls': self.calls,
                'total_time': self.total_time,
                'avg_time': self.total_time / self.calls,
                'min_time': self.min_time,
                'max_time': self.max_time,
                'recent_times': list(self.times)[-10:]  # Last 10 measurements
            }


class MetricsCollector:
    """Singleton metrics collector."""
    
    _instance: Optional['MetricsCollector'] = None
    _lock: threading.Lock = threading.Lock()
    
    def __new__(cls) -> 'MetricsCollector':
        """Create or return singleton instance."""
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._timers: Dict[str, TimerMetric] = {}
                cls._instance._gauges: Dict[str, Metric] = {}
                cls._instance._counters: Dict[str, int] = defaultdict(int)
                logger.info("MetricsCollector initialized")
            return cls._instance
    
    def timer(self, name: str) -> TimerMetric:
        """Get or create a timer metric."""
        with self._lock:
            if name not in self._timers:
                self._timers[name] = TimerMetric(name)
            return self._timers[name]
    
    def gauge(self, name: str, value: float, tags: Optional[Dict[str, str]] = None) -> None:
        """Set a gauge metric."""
        with self._lock:
            self._gauges[name] = Metric(value, tags=tags or {})
    
    def counter(self, name: str, value: int = 1) -> None:
        """Increment a counter metric."""
        with self._lock:
            self._counters[name] += value
    
    def get_timer_stats(self, name: str) -> Optional[Dict[str, Any]]:
        """Get statistics for a timer."""
        with self._lock:
            timer = self._timers.get(name)
            return timer.get_stats() if timer else None
    
    def get_all_metrics(self) -> Dict[str, Any]:
        """Get all collected metrics."""
        with self._lock:
            return {
                'timers': {name: timer.get_stats() for name, timer in self._timers.items()},
                'gauges': {name: {'value': metric.value, 'timestamp': metric.timestamp.isoformat(), 'tags': metric.tags} 
                          for name, metric in self._gauges.items()},
                'counters': dict(self._counters)
            }
    
    def reset(self) -> None:
        """Reset all metrics."""
        with self._lock:
            self._timers.clear()
            self._gauges.clear()
            self._counters.clear()
            logger.info("Metrics reset")


# Global metrics instance
metrics = MetricsCollector()


def timed_metric(name: str = None):
    """
    Decorator to measure and record execution time using MetricsCollector.
    
    Args:
        name: Optional name for the metric (defaults to function name)
    """
    def decorator(func: Callable) -> Callable:
        metric_name = name or f"{func.__module__}.{func.__name__}"
        
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            start_time = time.perf_counter()
            try:
                return func(*args, **kwargs)
            finally:
                end_time = time.perf_counter()
                execution_time = end_time - start_time
                metrics.timer(metric_name).record(execution_time)
                
                # Log warning if execution time is too long
                if execution_time > 1.0:  # More than 1 second
                    logger.warning(f"{metric_name} took {execution_time:.4f}s (>1.0s threshold)")
                elif execution_time > 0.5:  # More than 500ms
                    logger.info(f"{metric_name} took {execution_time:.4f}s (>0.5s threshold)")
                    
        return wrapper
    return decorator