import json
import os
import sys
from pathlib import Path
from datetime import datetime, timezone

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# Path to the NEW custom MT5 Files folder specified by user
SIGNAL_FILE = r"C:\mt5\OrderFlowExecutor\MQL5\Files\signal.json"

def test_rich_signal():
    """Create a signal with full indicator data for the new dashboard."""
    print("--- Testing Rich Signal for Dashboard v1.03 ---")
    
    signal_data = {
        "direction": "LONG",
        "action": "BUY",
        "entry": 67550.0,
        "entry_price": 67550.0,
        "sl": 67200.0,
        "stop_loss": 67200.0,
        "tp": 68500.0,
        "take_profit": 68500.0,
        "confidence": 88,
        "reason": "SMC Bullish OB Confirmed + Positive Delta Surge",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "indicators": {
            "delta": 1520.0,
            "imbalance_ratio": 2.45,
            "volume_spike": 3.2,
            "poc": 67480.0,
            "ob_present": True,
            "fvg_present": True,
            "trend": "BULLISH",
            "zone_context": "DISCOUNT"
        }
    }
    
    print(f"[*] Path: {SIGNAL_FILE}")
    
    try:
        # Ensure directory exists
        os.makedirs(os.path.dirname(SIGNAL_FILE), exist_ok=True)
        
        with open(SIGNAL_FILE, 'w') as f:
            json.dump(signal_data, f, indent=4)
        
        print(f" [OK] Rich signal written successfully!")
        print("Please check your MT5 Dashboard for Indicators data.")
    except Exception as e:
        print(f" [ERROR] Could not write file: {e}")
        print("Tip: Make sure the path exists or run as Administrator if writing to C:\\ root.")

if __name__ == "__main__":
    test_rich_signal()
