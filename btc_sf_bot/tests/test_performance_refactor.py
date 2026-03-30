import sys
import os
import pandas as pd
import numpy as np
from unittest.mock import MagicMock

# Add src to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.signals.signal_manager_v3 import SignalManager
from src.signals.bot_state import BotState

def test_signal_manager_refactor():
    print("🧪 Testing SignalManager Performance Refactor...")
    
    # Mock dependencies
    config = {
        'isf': {'enabled': True},
        'structure_validation': {'enabled': True},
        'htf_mss': {'enabled': True},
        'trailing_stop': {'enabled': True},
        'aggressive_mode': {'enabled': True}
    }
    bot_state = MagicMock()
    bot_state.can_look_for_entry.return_value = True
    
    cache = MagicMock()
    connector = MagicMock()
    
    # Create SignalManager
    manager = SignalManager(config)
    
    # Mock components that are called inside generate_signal
    manager.analyze_market = MagicMock(return_value={'order_flow': {'delta': 100}, 'ict': {}})
    manager.entry_scanner.scan = MagicMock(return_value={'found': False})
    manager.isf_manager.scan_isf_signals = MagicMock(return_value=[])
    manager.htf_mss_analyzer.analyze_h1_structure = MagicMock(return_value={'trend': 'BULLISH'})
    
    # Dummy data with all required columns
    columns = ['open', 'high', 'low', 'close', 'volume']
    data = {col: np.random.rand(100) for col in columns}
    candles = pd.DataFrame(index=pd.date_range('2026-03-11', periods=100, freq='5min'), data=data)
    
    data_h1 = {col: np.random.rand(24) for col in columns}
    candles_h1 = pd.DataFrame(index=pd.date_range('2026-03-11', periods=24, freq='h'), data=data_h1)
    order_book = {'bids': {}, 'asks': {}}
    trades = []
    
    print("🏃 Running generate_signal...")
    try:
        signal = manager.generate_signal(
            candles=candles,
            order_book=order_book,
            trades=trades,
            current_price=69000.0,
            candles_h1=candles_h1
        )
        
        # Check call counts to verify optimization
        call_count = manager.analyze_market.call_count
        print(f"📊 analyze_market call count: {call_count}")
        
        if call_count == 1:
            print("✅ SUCCESS: analyze_market called exactly once.")
        else:
            print(f"❌ FAILURE: analyze_market called {call_count} times.")
            
    except Exception as e:
        print(f"❌ CRASH: SignalManager failed with error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_signal_manager_refactor()
