"""
Verification Test for Integrated Smart Flow (ISF)
Simulates market conditions for ISF_SWEEP, ISF_WALL, and ISF_INST
"""
import pandas as pd
import numpy as np
from datetime import datetime, timezone
from src.signals.signal_manager_v3 import SignalManager
from src.enums import BOSStatus

def create_mock_candles(base_price=50000, n=100):
    times = pd.date_range(end=datetime.now(), periods=n, freq='5min')
    data = {
        'open': np.full(n, base_price),
        'high': np.full(n, base_price + 100),
        'low': np.full(n, base_price - 100),
        'close': np.full(n, base_price),
        'volume': np.random.randint(100, 1000, n)
    }
    df = pd.DataFrame(data, index=times)
    # Create a Bullish FVG
    df.iloc[-5, df.columns.get_loc('low')] = base_price + 200 # Candle 1 low
    df.iloc[-4, df.columns.get_loc('open')] = base_price + 210
    df.iloc[-4, df.columns.get_loc('close')] = base_price + 300
    df.iloc[-3, df.columns.get_loc('high')] = base_price + 250 # Candle 3 high
    # Gap between index -5 low (50200) and -3 high (50250) is empty? 
    # Wait, Bullish FVG: C1 Low > C3 High. Correct.
    return df

def test_isf_logic():
    print("Starting ISF Logic Verification...")
    
    config = {
        'isf': {'enabled': True, 'isf_synergy_threshold': 10}, # Low threshold for test
        'structure_validation': {'enabled': True},
        'htf_mss': {'enabled': False}
    }
    
    manager = SignalManager(config)
    # We need to manually set some attributes that are normally set via connectors
    manager.order_flow.last_vol_ratio = 5.0 
    
    candles = create_mock_candles()
    
    # Test Price inside the FVG we created
    current_price = 50225 
    order_book = {
        'open_interest': 20000.0,
        'prev_oi': 15000.0, # Massive OI Surge
        'bids': {50220: 100, 50221: 150},
        'asks': {50230: 5, 50231: 5}
    }
    
    # Mock analysis result to force P1 score
    # In a real scenario, SignalManager.analyze_market would do this.
    # Here we can patch the analyzer or ensure the data is right.
    
    print(f"Testing signal generation at price: {current_price}")
    
    # We will mock the p1_data calculation results in the SignalManager momentarily for testing
    original_analyze = manager.analyze_market
    def mock_analyze(*args, **kwargs):
        res = original_analyze(*args, **kwargs)
        res['order_flow']['oi_change_pct'] = 2.0
        res['order_flow']['cvd_delta_pct'] = 30.0
        res['order_flow']['flow_persistence'] = 15
        res['order_flow']['volume_ratio'] = 4.0
        return res
    
    manager.analyze_market = mock_analyze
    
    signal = manager.generate_signal(
        candles=candles,
        order_book=order_book,
        trades=[],
        current_price=current_price
    )
    
    if signal:
        print(f"SUCCESS: Signal Generated!")
        print(f"Mode: {signal.metadata.get('short_reason')}")
        print(f"Score: {signal.metadata.get('score')}")
        print(f"TP-SL: {signal.take_profit:.2f} / {signal.stop_loss:.2f}")
    else:
        print("FAIL: No signal generated. Check scoring logic.")

if __name__ == "__main__":
    test_isf_logic()
