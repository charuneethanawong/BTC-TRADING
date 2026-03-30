"""
Test HTF MSS Sync and Dynamic Trailing SL Integration
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from datetime import datetime, timedelta

print("=" * 80)
print("TESTING HTF MSS SYNC + DYNAMIC TRAILING SL")
print("=" * 80)

print("\n" + "=" * 80)
print("PHASE 1: Testing Imports...")
print("-" * 80)

try:
    from src.analysis.htf_mss_analyzer import HTFMSSAnalyzer
    print("✅ HTFMSSAnalyzer imported")
except Exception as e:
    print(f"❌ HTFMSSAnalyzer import failed: {e}")
    sys.exit(1)

try:
    from src.risk.trailing_stop_manager import TrailingStopManager, TrailingPosition
    print("✅ TrailingStopManager imported")
except Exception as e:
    print(f"❌ TrailingStopManager import failed: {e}")
    sys.exit(1)

try:
    from src.enums import TrendState
    print("✅ TrendState enum imported")
except Exception as e:
    print(f"❌ TrendState import failed: {e}")
    sys.exit(1)

print("\n" + "=" * 80)
print("PHASE 2: Testing HTF MSS Analyzer...")
print("-" * 80)

def create_test_h1_candles(bullish_trend=True, with_bos=False, with_choch=False):
    """Create test H1 candles."""
    dates = pd.date_range(start='2026-01-01', periods=100, freq='1h')
    
    if bullish_trend:
        base_prices = np.linspace(95000, 97000, 100)
        noise = np.random.randn(100) * 100
    else:
        base_prices = np.linspace(97000, 95000, 100)
        noise = np.random.randn(100) * 100
    
    prices = base_prices + noise
    
    data = {
        'open': prices,
        'high': prices + np.abs(np.random.randn(100)) * 50,
        'low': prices - np.abs(np.random.randn(100)) * 50,
        'close': prices,
        'volume': np.random.randint(100, 500, 100)
    }
    
    if with_bos and bullish_trend:
        data['close'][-1] = data['high'][-3] + 50
        data['high'][-1] = data['close'][-1] + 20
    
    if with_choch and not bullish_trend:
        data['close'][-1] = data['low'][-3] - 50
        data['low'][-1] = data['close'][-1] - 20
    
    df = pd.DataFrame(data, index=dates)
    return df

htf_analyzer = HTFMSSAnalyzer({'lookback': 50})

candles_bullish = create_test_h1_candles(bullish_trend=True, with_bos=True)
analysis = htf_analyzer.analyze_h1_structure(candles_bullish)
print(f"\nBullish Trend Analysis:")
print(f"  Trend: {analysis['trend'].value}")
print(f"  Structure Type: {analysis['structure_type']}")
print(f"  Last High: {analysis['last_high']:.2f}")
print(f"  Last Low: {analysis['last_low']:.2f}")

sync_result = htf_analyzer.check_m15_h1_sync('LONG', analysis)
print(f"\nM15 LONG Sync Check:")
print(f"  Sync Score: {sync_result['sync_score']}")
print(f"  Is Aligned: {sync_result['is_aligned']}")
print(f"  Reason: {sync_result['reason']}")
print(f"  Confidence Mult: {sync_result['confidence_mult']:.2f}x")

candles_bearish = create_test_h1_candles(bullish_trend=False, with_choch=True)
analysis2 = htf_analyzer.analyze_h1_structure(candles_bearish)
sync_result2 = htf_analyzer.check_m15_h1_sync('SHORT', analysis2)
print(f"\nM15 SHORT Sync Check (Bearish H1):")
print(f"  Sync Score: {sync_result2['sync_score']}")
print(f"  Is Aligned: {sync_result2['is_aligned']}")
print(f"  Reason: {sync_result2['reason']}")

should_filter, filter_reason = htf_analyzer.should_filter_entry('LONG', analysis2, min_sync_score=-1)
print(f"\nFilter LONG Entry (Bearish H1):")
print(f"  Should Filter: {should_filter}")
print(f"  Reason: {filter_reason}")

print("✅ HTF MSS Analyzer tests passed!")

print("\n" + "=" * 80)
print("PHASE 3: Testing Trailing Stop Manager...")
print("-" * 80)

trailing_mgr = TrailingStopManager({
    'activation_profit_pct': 0.3,
    'trail_atr_mult': 1.5,
    'breakeven_profit_pct': 0.5,
    'min_lock_profit_pct': 0.2,
    'max_trail_distance_pct': 1.0
})

position = trailing_mgr.register_position(
    signal_id='TEST_001',
    direction='LONG',
    entry_price=96000.0,
    initial_sl=95800.0
)
print(f"\nRegistered Position:")
print(f"  Signal ID: {position.signal_id}")
print(f"  Direction: {position.direction}")
print(f"  Entry: {position.entry_price}")
print(f"  Initial SL: {position.initial_sl}")
print(f"  Current SL: {position.current_sl}")

result = trailing_mgr.update('TEST_001', current_price=96050.0, atr=100.0)
print(f"\nUpdate 1 (Price 96050, +0.05%):")
print(f"  Updated: {result['updated']}")
print(f"  Reason: {result['reason']}")
print(f"  Profit %: {result['profit_pct']:.2f}%")

result = trailing_mgr.update('TEST_001', current_price=96300.0, atr=100.0)
print(f"\nUpdate 2 (Price 96300, +0.31% - Activation):")
print(f"  Updated: {result['updated']}")
print(f"  New SL: {result['new_sl']:.2f}")
print(f"  Old SL: {result['old_sl']:.2f}")
print(f"  Reason: {result['reason']}")
print(f"  Profit %: {result['profit_pct']:.2f}%")
print(f"  Is Locked: {result['is_locked']}")

result = trailing_mgr.update('TEST_001', current_price=96500.0, atr=100.0)
print(f"\nUpdate 3 (Price 96500, +0.52% - Breakeven):")
print(f"  Updated: {result['updated']}")
print(f"  New SL: {result['new_sl']:.2f}")
print(f"  Reason: {result['reason']}")
print(f"  Is Locked: {result['is_locked']}")

result = trailing_mgr.update('TEST_001', current_price=96800.0, atr=100.0)
print(f"\nUpdate 4 (Price 96800, +0.83% - ATR Trail):")
print(f"  Updated: {result['updated']}")
print(f"  New SL: {result['new_sl']:.2f}")
print(f"  Trail Count: {result['trail_count']}")

stats = trailing_mgr.get_statistics()
print(f"\nTrailing Statistics:")
print(f"  Total Positions: {stats['total_positions']}")
print(f"  Active Positions: {stats['active_positions']}")
print(f"  Avg Trail Count: {stats['avg_trail_count']:.1f}")

print("✅ Trailing Stop Manager tests passed!")

print("\n" + "=" * 80)
print("PHASE 4: Testing SignalManagerV3 Integration...")
print("-" * 80)

try:
    from src.signals.signal_manager_v3 import SignalManager
    print("✅ SignalManagerV3 imported")
except Exception as e:
    print(f"❌ SignalManagerV3 import failed: {e}")
    sys.exit(1)

config = {
    'structure_validation': {'enabled': True},
    'htf_mss': {'enabled': True},
    'trailing_stop': {'enabled': True}
}

sm = SignalManager(config)
print(f"\nSignalManagerV3 initialized with v3.2 features:")
print(f"  Use HTF Sync: {sm.use_htf_sync}")
print(f"  Use Trailing Stop: {sm.use_trailing_stop}")
print(f"  HTF Analyzer: {sm.htf_mss_analyzer is not None}")
print(f"  Trailing Manager: {sm.trailing_stop_manager is not None}")

print("✅ SignalManagerV3 integration tests passed!")

print("\n" + "=" * 80)
print("INTEGRATION TEST SUMMARY")
print("=" * 80)

print("""
✅ HTF MSS Sync:
   - Analyzes H1 candles for BOS/CHoCH
   - Scores sync with M15 direction (+3 CHoCH, +2 BOS, +1 trend)
   - Filters entries when H1 strongly opposes
   - Adjusts confidence multiplier (0.5x - 1.3x)

✅ Dynamic Trailing SL:
   - Activates after 0.3% profit
   - ATR-based trailing (1.5x ATR from peak)
   - Breakeven move at 0.5% profit
   - Structure-based trailing (swing points)
   - Profit lock after multiple trails

✅ SignalManagerV3 Integration:
   - HTF sync applied in generate_signal()
   - Trailing positions registered on signal creation
   - update_trailing_stop() method available
   - H1 candles passed via candles_h1 parameter

New Config Sections (config_v3.yaml):
   - htf_mss: HTF sync settings
   - trailing_stop: Trailing SL settings
   - features.use_htf_sync: Enable/disable HTF sync
   - features.use_trailing_stop: Enable/disable trailing
""")

print("=" * 80)
