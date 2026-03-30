import pandas as pd
import numpy as np
from src.signals.smart_flow_manager import SmartFlowManager
from datetime import datetime, timezone

def generate_mock_candles(n=100, trend='bullish'):
    base_price = 70000
    np.random.seed(42)
    
    # Generate typical BTC price action
    data = []
    current_price = base_price
    
    for i in range(n):
        open_p = current_price
        close_p = open_p + np.random.randn() * 10
        if trend == 'bullish' and i > 80:
            # Create a sweep high for testing
            if i == 90:
                high_p = open_p + 100 # Spike up
                close_p = open_p - 10  # Close below
            else:
                high_p = max(open_p, close_p) + 5
        else:
            high_p = max(open_p, close_p) + 5
            
        low_p = min(open_p, close_p) - 5
        
        data.append({
            'open': open_p,
            'high': high_p,
            'low': low_p,
            'close': close_p,
            'volume': np.random.randint(100, 500)
        })
        current_price = close_p
        
    df = pd.DataFrame(data)
    df.index = pd.date_range(start='2024-01-01', periods=n, freq='1min')
    return df

def test_signal_scores():
    print("\n--- Starting Signal Debug Test ---")
    
    config = {
        'smart_flow': {
            'threshold_sweep': 8,
            'threshold_wall': 8,
            'threshold_zone': 7
        },
        'trading_symbol': 'BTCUSDT'
    }
    
    manager = SmartFlowManager(config)
    
    # 1. Simulate data that should trigger a sweep
    candles = generate_mock_candles(100, 'bullish')
    current_price = candles['close'].iloc[-1]
    
    # Mock data for order flow (Phase 1)
    p1_data = {
        'cvd_delta': 0.1,  # Positive delta
        'volume_ratio': 1.2
    }
    
    # Mock Binance Data with real OI Change
    # First call to populate cache
    manager._binance_cache = {
        'oi': {'openInterest': 1000000, 'openInterestChange': 0},
        'walls': {'bid_walls': [], 'ask_walls': []},
        'timestamp': datetime.now(timezone.utc).isoformat()
    }
    
    # Simulate Binance data for evaluation
    binance_data = {
        'oi': {'openInterest': 1010000, 'openInterestChange': 1.0}, # +1.0% change
        'walls': {
            'bid_walls': [{'price': 69900, 'size': 10, 'value_usd': 700000}],
            'ask_walls': [],
            'strongest_bid': {'price': 69900, 'size': 10, 'value_usd': 700000}
        },
        'cvd': {'cvd': 50000},
        'timestamp': datetime.now(timezone.utc).isoformat()
    }
    
    print(f"Testing with Price: {current_price:.2f}, OI Change: {binance_data['oi']['openInterestChange']}%")
    
    # Mock the _fetch_binance_data method
    from unittest.mock import MagicMock
    manager._fetch_binance_data = MagicMock(return_value=binance_data)
    
    # Run scan
    signals = manager.scan_patterns(
        candles=candles,
        current_price=current_price,
        p1_data=p1_data,
        phase1_score=5
    )
    
    # Check results
    ict_summary = manager.ict_analyzer.get_ict_summary(candles, current_price, p1_data)
    print(f"ICT Sweep Type: {ict_summary['liquidity_sweep']['type']}, Quality: {ict_summary['liquidity_sweep']['quality']}")
    
    if signals:
        for s in signals:
            print(f"✅ Signal FOUND: {s['pattern_type']} | Score: {s['score']}/{manager.thresholds.get(s['pattern_type'])}")
    else:
        # Check why it failed - maybe score was too low but not 0
        sweep_score = manager._get_pattern_score('SWEEP', candles, current_price, p1_data, ict_summary, None, binance_data)
        print(f"ℹ️ SWEEP Score: {sweep_score} (Threshold: {manager.thresholds['SWEEP']})")
        
        if sweep_score > 0:
            print("✅ SUCCESS: Score is now non-zero!")
        else:
            print("❌ FAILURE: Score is still 0.")

if __name__ == "__main__":
    test_signal_scores()
