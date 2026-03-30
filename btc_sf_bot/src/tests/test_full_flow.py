import asyncio
import os
import json
import sys
from pathlib import Path
from datetime import datetime, timezone
from dotenv import load_dotenv

# Add parent to path to import local modules
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.execution.telegram_alert import TelegramAlert

# Path to MT5 Common Files
SIGNAL_FILE = r"C:\Users\asus\AppData\Roaming\MetaQuotes\Terminal\Common\Files\signal.json"

async def test_full_signal_flow():
    """Test both Telegram and MT5 signal file."""
    # Load environment variables
    env_path = Path(__file__).parent.parent.parent / 'config' / '.env'
    load_dotenv(dotenv_path=env_path)
    
    print("--- Starting Full Signal Test ---")
    
    # 1. Prepare Signal Data
    price = 68500.0
    sl = 68200.0
    tp = 69500.0
    rr = abs(tp - price) / abs(price - sl)
    
    signal_data = {
        "direction": "LONG",
        "entry_price": price,
        "stop_loss": sl,
        "take_profit": tp,
        "confidence": 92,
        "reason": f"SMC Bullish OB + Order Flow Volume Spike (RR 1:{rr:.1f}) (TEST)",
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    
    # 2. Test MT5 File Connection
    print(f"[*] Writing to MT5 Common folder...")
    try:
        os.makedirs(os.path.dirname(SIGNAL_FILE), exist_ok=True)
        with open(SIGNAL_FILE, 'w') as f:
            json.dump(signal_data, f)
        print(f" [OK] Signal written to: {SIGNAL_FILE}")
    except Exception as e:
        print(f" [ERROR] Failed to write MT5 file: {e}")

    # 3. Test Telegram Alert
    print(f"[*] Sending Telegram Alert...")
    telegram = TelegramAlert()
    if telegram.enabled:
        success = await telegram.send_signal_alert(signal_data)
        if success:
            print(" [OK] Telegram alert sent successfully!")
        else:
            print(" [ERROR] Telegram alert failed to send. Check your Token/ChatID.")
    else:
        print(" [SKIP] Telegram is not configured in .env")

    print("\n--- Test Finished ---")
    print("Please check:")
    print("1. Your Telegram bot for the 'NEW SIGNAL' message.")
    print("2. Your MT5 Experts tab for '>>> New Signal Detected'.")

if __name__ == "__main__":
    asyncio.run(test_full_signal_flow())
