"""
Unit tests for MetricsCollector class.
"""
import time
import sys
import os

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from src.utils.metrics import MetricsCollector, timed_metric


def test_metrics_collector_singleton():
    """Test that MetricsCollector is a singleton."""
    print("Testing MetricsCollector singleton...")
    
    collector1 = MetricsCollector()
    collector2 = MetricsCollector()
    
    assert collector1 is collector2
    print("✅ MetricsCollector singleton test passed")


def test_timer_metric():
    """Test TimerMetric functionality."""
    print("Testing TimerMetric...")
    
    collector = MetricsCollector()
    timer = collector.timer("test_timer")
    
    # Record some times
    timer.record(0.1)
    timer.record(0.2)
    timer.record(0.15)
    
    stats = timer.get_stats()
    
    assert stats['name'] == 'test_timer'
    assert stats['calls'] == 3
    assert abs(stats['total_time'] - 0.45) < 0.0001  # Handle floating point precision
    assert abs(stats['avg_time'] - 0.15) < 0.0001
    assert stats['min_time'] == 0.1
    assert stats['max_time'] == 0.2
    assert len(stats['recent_times']) == 3
    
    print("✅ TimerMetric test passed")


def test_gauge_and_counter():
    """Test gauge and counter metrics."""
    print("Testing gauge and counter...")
    
    collector = MetricsCollector()
    
    # Test gauge
    collector.gauge("test_gauge", 42.5, {"tag": "value"})
    assert collector._gauges["test_gauge"].value == 42.5
    assert collector._gauges["test_gauge"].tags["tag"] == "value"
    
    # Test counter
    collector.counter("test_counter", 5)
    collector.counter("test_counter", 3)
    assert collector._counters["test_counter"] == 8
    
    print("✅ Gauge and counter test passed")


def test_timed_metric_decorator():
    """Test timed_metric decorator."""
    print("Testing timed_metric decorator...")
    
    @timed_metric("test_function")
    def test_func():
        time.sleep(0.01)  # Sleep for 10ms
        return "result"
    
    result = test_func()
    assert result == "result"
    
    # Check that metric was recorded
    collector = MetricsCollector()
    stats = collector.get_timer_stats("test_function")
    
    assert stats is not None
    assert stats['calls'] == 1
    assert stats['avg_time'] >= 0.01  # Should be at least 10ms
    
    print("✅ Timed metric decorator test passed")


def test_get_all_metrics():
    """Test getting all metrics."""
    print("Testing get_all_metrics...")
    
    collector = MetricsCollector()
    
    # Reset to clear any existing metrics from other tests
    collector.reset()
    
    # Add some metrics
    collector.timer("test_timer").record(0.1)
    collector.gauge("test_gauge", 99.5)
    collector.counter("test_counter", 7)
    
    all_metrics = collector.get_all_metrics()
    
    assert 'test_timer' in all_metrics['timers']
    assert all_metrics['timers']['test_timer']['calls'] == 1
    assert 'test_gauge' in all_metrics['gauges']
    assert all_metrics['gauges']['test_gauge']['value'] == 99.5
    assert all_metrics['counters']['test_counter'] == 7
    
    print("✅ Get all metrics test passed")


def test_reset():
    """Test reset functionality."""
    print("Testing reset...")
    
    collector = MetricsCollector()
    
    # Add some metrics
    collector.timer("test_timer").record(0.1)
    collector.gauge("test_gauge", 99.5)
    collector.counter("test_counter", 7)
    
    # Reset
    collector.reset()
    
    # Check that everything is cleared
    assert len(collector._timers) == 0
    assert len(collector._gauges) == 0
    assert len(collector._counters) == 0
    
    print("✅ Reset test passed")


if __name__ == "__main__":
    print("Running MetricsCollector unit tests...")
    print("=" * 50)
    
    test_metrics_collector_singleton()
    test_timer_metric()
    test_gauge_and_counter()
    test_timed_metric_decorator()
    test_get_all_metrics()
    test_reset()
    
    print("=" * 50)
    print("All MetricsCollector tests completed!")