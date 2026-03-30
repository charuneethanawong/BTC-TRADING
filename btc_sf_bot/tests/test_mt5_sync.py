from src.risk.position_sizer import RiskManager
from src.signals.signal_manager_v3 import SignalManager
import pandas as pd
import numpy as np

def test_mt5_sync_logic():
    print("🧪 Testing MT5 State Sync Logic...")
    
    # 1. Setup RiskManager
    config = {
        'max_positions': 2,
        'risk_per_trade': 0.5
    }
    risk_manager = RiskManager(config)
    
    # 2. Simulate initial state (0 positions)
    can_open, reason = risk_manager.check_trading_allowed()
    print(f"Initial check - Can open: {can_open}, Reason: {reason}")
    assert can_open is True
    
    # 3. Simulate receiving position_info from MT5
    # Two positions currently open in MT5
    mock_positions = [
        {'ticket': 12345, 'comment': 'SIG_ABC_123', 'volume': 0.01},
        {'ticket': 67890, 'comment': 'SIG_XYZ_456', 'volume': 0.01}
    ]
    
    print(f"📥 Simulating position_info update: {len(mock_positions)} positions")
    risk_manager.update_positions_state(mock_positions)
    
    # 4. Check if trading is suppressed due to MaxPositions
    can_open, reason = risk_manager.check_trading_allowed()
    print(f"Post-update check - Can open: {can_open}, Reason: {reason}")
    
    if not can_open and "Max positions reached" in reason:
        print("✅ SUCCESS: RiskManager correctly suppressed trading based on MT5 state.")
    else:
        print(f"❌ FAILURE: RiskManager allowed trading or gave wrong reason: {reason}")
        return

    # 5. Simulate closing one position in MT5
    mock_positions_reduced = [
        {'ticket': 12345, 'comment': 'SIG_ABC_123', 'volume': 0.01}
    ]
    print(f"📥 Simulating position_info update: 1 position remains")
    risk_manager.update_positions_state(mock_positions_reduced)
    
    can_open, reason = risk_manager.check_trading_allowed()
    print(f"Post-close check - Can open: {can_open}, Reason: {reason}")
    
    if can_open:
        print("✅ SUCCESS: RiskManager correctly allowed trading after MT5 position closed.")
    else:
        print(f"❌ FAILURE: RiskManager still blocked trading: {reason}")

if __name__ == "__main__":
    test_mt5_sync_logic()
