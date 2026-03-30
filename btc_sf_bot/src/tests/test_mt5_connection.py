import json
import os
import time
from datetime import datetime, timezone

# Path to MT5 Common Files
SIGNAL_FILE = r"C:\Users\asus\AppData\Roaming\MetaQuotes\Terminal\Common\Files\signal.json"

def send_test_signal():
    """Create a fake signal for testing MT5 connection."""
    signal = {
        "action": "BUY",
        "entry": 65000.0,
        "sl": 64800.0,
        "tp": 65500.0,
        "confidence": 85,
        "reason": "TEST SIGNAL - Order Flow Imbalance",
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    
    print(f"Creating test signal: {signal['action']} @ {signal['entry']}")
    
    # Ensure directory exists
    os.makedirs(os.path.dirname(SIGNAL_FILE), exist_ok=True)
    
    with open(SIGNAL_FILE, 'w') as f:
        json.dump(signal, f)
        
    print(f"Success! Signal written to: {SIGNAL_FILE}")
    print("Please check your MT5 Expert tab for '>>> New Signal Detected'")

if __name__ == "__main__":
    send_test_signal()
