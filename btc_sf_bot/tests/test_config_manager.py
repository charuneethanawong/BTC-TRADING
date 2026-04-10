"""
Unit tests for ConfigManager class.
"""
import os
import tempfile
import yaml
from pathlib import Path

# Add project root to path
import sys
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from src.utils.config_v2 import ConfigManager, Config, ExchangeConfig, IPAConfig, IOFConfig


def test_config_manager_initialization():
    """Test ConfigManager initialization with default values."""
    print("Testing ConfigManager initialization...")
    
    # Create temporary config file
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        config_data = {
            'exchange': {
                'symbol': 'ETH/USDT:USDT',
                'timeframe': '15m',
                'htf_timeframe': '4h',
                'htf_trend_ema_period': 100
            },
            'ipa': {
                'score_threshold': 12,
                'ob_max_distance_atr': 1.5,
                'ob_body_min_pct': 0.001,
                'h1_lookback_candles': 30,
                'volume_spike_min': 1.5,
                'rr_min': 1.5
            },
            'iof': {
                'score_threshold': 8,
                'der_min': 0.4,
                'der_strong': 0.7,
                'der_moderate': 0.5,
                'oi_change_min_pct': 0.2,
                'wall_threshold_asia': 150000,
                'wall_threshold_london': 250000,
                'wall_threshold_ny': 350000,
                'wall_max_distance_pct': 0.008,
                'min_wall_stability': 20,
                'wall_relative_size_multiplier': 0.0008,
                'absolute_min_wall_usd': 75000
            }
        }
        yaml.dump(config_data, f)
        config_path = f.name
    
    try:
        # Initialize ConfigManager
        config_manager = ConfigManager(config_path)
        
        # Test that config was loaded correctly
        assert config_manager.config is not None
        assert config_manager.config.exchange.symbol == 'ETH/USDT:USDT'
        assert config_manager.config.exchange.timeframe == '15m'
        assert config_manager.config.ipa.score_threshold == 12
        assert config_manager.config.iof.der_min == 0.4
        
        print("✅ ConfigManager initialization test passed")
        
    finally:
        # Clean up temp file
        os.unlink(config_path)


def test_config_manager_get_method():
    """Test ConfigManager get method with dot notation."""
    print("Testing ConfigManager get method...")
    
    # Create temporary config file with all required sections
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        config_data = {
            'exchange': {
                'symbol': 'BTC/USDT:USDT',
                'timeframe': '5m',
                'htf_timeframe': '1h',
                'htf_trend_ema_period': 50
            },
            'ipa': {
                'score_threshold': 10,
                'ob_max_distance_atr': 1.0,
                'ob_body_min_pct': 0.0005,
                'h1_lookback_candles': 50,
                'volume_spike_min': 1.0,
                'rr_min': 1.0
            },
            'iof': {
                'score_threshold': 8,
                'der_min': 0.3,
                'der_strong': 0.6,
                'der_moderate': 0.45,
                'oi_change_min_pct': 0.1,
                'wall_threshold_asia': 100000,
                'wall_threshold_london': 200000,
                'wall_threshold_ny': 300000,
                'wall_max_distance_pct': 0.005,
                'min_wall_stability': 15,
                'wall_relative_size_multiplier': 0.0005,
                'absolute_min_wall_usd': 50000
            },
            'nested': {
                'level1': {
                    'level2': {
                        'value': 'test_value'
                    }
                }
            }
        }
        yaml.dump(config_data, f)
        config_path = f.name
    
    try:
        config_manager = ConfigManager(config_path)
        
        # Test basic get
        assert config_manager.get('exchange.symbol') == 'BTC/USDT:USDT'
        assert config_manager.get('exchange.timeframe') == '5m'
        assert config_manager.get('ipa.score_threshold') == 10
        assert config_manager.get('nested.level1.level2.value') == 'test_value'
        
        # Test default values
        assert config_manager.get('nonexistent.key', 'default') == 'default'
        assert config_manager.get('exchange.nonexistent', 42) == 42
        
        print("✅ ConfigManager get method test passed")
        
    finally:
        # Clean up temp file
        os.unlink(config_path)


def test_config_manager_validation():
    """Test ConfigManager validation with Pydantic models."""
    print("Testing ConfigManager validation...")
    
    # Test valid config
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        config_data = {
            'exchange': {
                'symbol': 'BTC/USDT:USDT',
                'timeframe': '5m'
            },
            'ipa': {
                'score_threshold': 10,  # Valid range: 1-20
                'ob_max_distance_atr': 1.0,  # Valid range: 0.1-5.0
                'ob_body_min_pct': 0.0005,  # Valid range: 0.0001-0.01
                'h1_lookback_candles': 50,  # Valid range: 10-200
                'volume_spike_min': 1.0,  # Valid range: 0.5-5.0
                'rr_min': 1.0  # Valid range: 0.1-10.0
            },
            'iof': {
                'score_threshold': 8,  # Valid range: 1-20
                'der_min': 0.3,  # Valid range: 0.1-1.0
                'der_strong': 0.6,  # Valid range: 0.1-1.0
                'der_moderate': 0.45,  # Valid range: 0.1-1.0
                'oi_change_min_pct': 0.1,  # Valid range: 0.01-10.0
                'wall_threshold_asia': 100000,  # Valid range: >=1000
                'wall_threshold_london': 200000,  # Valid range: >=1000
                'wall_threshold_ny': 300000,  # Valid range: >=1000
                'wall_max_distance_pct': 0.005,  # Valid range: 0.001-0.1
                'min_wall_stability': 15,  # Valid range: 5-300
                'wall_relative_size_multiplier': 0.0005,  # Valid range: 0.00001-0.1
                'absolute_min_wall_usd': 50000  # Valid range: >=1000
            }
        }
        yaml.dump(config_data, f)
        config_path = f.name
    
    try:
        # This should succeed
        config_manager = ConfigManager(config_path)
        assert config_manager.config is not None
        print("✅ Valid config test passed")
        
    finally:
        os.unlink(config_path)
    
    # Test invalid config (should raise exception)
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        config_data = {
            'exchange': {
                'symbol': 'BTC/USDT:USDT',
                'timeframe': '5m'
            },
            'ipa': {
                'score_threshold': 25  # Invalid: > 20
            }
        }
        yaml.dump(config_data, f)
        config_path = f.name
    
    try:
        config_manager = ConfigManager(config_path)
        assert False, "Should have raised validation error"
    except Exception:
        print("✅ Invalid config validation test passed")
    finally:
        os.unlink(config_path)


def test_config_manager_hot_reload():
    """Test ConfigManager hot-reload capability."""
    print("Testing ConfigManager hot-reload...")
    
    # Create initial config file with all required sections
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        config_data = {
            'exchange': {
                'symbol': 'BTC/USDT:USDT',
                'timeframe': '5m',
                'htf_timeframe': '1h',
                'htf_trend_ema_period': 50
            },
            'ipa': {
                'score_threshold': 10,
                'ob_max_distance_atr': 1.0,
                'ob_body_min_pct': 0.0005,
                'h1_lookback_candles': 50,
                'volume_spike_min': 1.0,
                'rr_min': 1.0
            },
            'iof': {
                'score_threshold': 6,
                'der_min': 0.3,
                'der_strong': 0.6,
                'der_moderate': 0.45,
                'oi_change_min_pct': 0.1,
                'wall_threshold_asia': 100000,
                'wall_threshold_london': 200000,
                'wall_threshold_ny': 300000,
                'wall_max_distance_pct': 0.005,
                'min_wall_stability': 15,
                'wall_relative_size_multiplier': 0.0005,
                'absolute_min_wall_usd': 50000
            }
        }
        yaml.dump(config_data, f)
        config_path = f.name
    
    try:
        config_manager = ConfigManager(config_path)
        
        # Verify initial value
        assert config_manager.get('ipa.score_threshold') == 10
        
        # Modify the config file
        with open(config_path, 'w') as f:
            config_data = {
                'exchange': {
                    'symbol': 'BTC/USDT:USDT',
                    'timeframe': '5m',
                    'htf_timeframe': '1h',
                    'htf_trend_ema_period': 50
                },
                'ipa': {
                    'score_threshold': 15,  # Changed value
                    'ob_max_distance_atr': 1.0,
                    'ob_body_min_pct': 0.0005,
                    'h1_lookback_candles': 50,
                    'volume_spike_min': 1.0,
                    'rr_min': 1.0
                },
                'iof': {
                     'score_threshold': 8,
                    'der_min': 0.3,
                    'der_strong': 0.6,
                    'der_moderate': 0.45,
                    'oi_change_min_pct': 0.1,
                    'wall_threshold_asia': 100000,
                    'wall_threshold_london': 200000,
                    'wall_threshold_ny': 300000,
                    'wall_max_distance_pct': 0.005,
                    'min_wall_stability': 15,
                    'wall_relative_size_multiplier': 0.0005,
                    'absolute_min_wall_usd': 50000
                }
            }
            yaml.dump(config_data, f)
        
        # Force reload
        reloaded = config_manager.reload()
        assert reloaded == True
        
        # Verify updated value
        assert config_manager.get('ipa.score_threshold') == 15
        
        print("✅ ConfigManager hot-reload test passed")
        
    finally:
        os.unlink(config_path)


if __name__ == "__main__":
    print("Running ConfigManager unit tests...")
    print("=" * 50)
    
    test_config_manager_initialization()
    test_config_manager_get_method()
    test_config_manager_validation()
    test_config_manager_hot_reload()
    
    print("=" * 50)
    print("All ConfigManager tests completed!")