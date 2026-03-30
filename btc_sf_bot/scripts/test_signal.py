import zmq
import json
import time
import requests
from datetime import datetime, timezone

def get_btc_price():
    """Fetch current BTC price from Binance API."""
    try:
        response = requests.get("https://api.binance.com/api/v3/ticker/price", params={"symbol": "BTCUSDT"})
        data = response.json()
        return float(data['price'])
    except Exception as e:
        print(f"⚠️ Warning: Could not fetch live price: {e}")
        return 65000.0  # Default fallback

def send_test_signal():
    """Send a mock LONG signal via ZeroMQ."""
    context = zmq.Context()
    socket = context.socket(zmq.PUB)
    
    # Connect/Bind - MT5 is SUB, Python is PUB
    # The EA is connecting to 127.0.0.1:5555
    host = "127.0.0.1"
    port = 5555
    socket.bind(f"tcp://{host}:{port}")
    
    print(f"🚀 Started Test Signal Publisher on {host}:{port}")
    print("⏳ Waiting 2 seconds for MT5 to connect...")
    time.sleep(2)
    
    price = get_btc_price()
    
    # Create Signal JSON (Matching EA's ProcessSignal logic)
    signal = {
        "signal_id": f"TEST_SIGNAL_{int(time.time())}",
        "direction": "LONG",
        "entry_price": price,
        "stop_loss": price * 0.99,
        "take_profit": price * 1.02,
        "score": 95,
        "reason": "Test signal from Python script",
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    
    topic = "signal"
    payload = f"{topic} {json.dumps(signal)}"
    
    print(f"📡 Sending Signal: {signal['direction']} @ ${price:.2f}")
    
    # Send multiple times to ensure SUB catches it (ZMQ PUB/SUB sync)
    for i in range(5):
        socket.send_string(payload)
        time.sleep(0.5)
    
    print("✅ Signals Sent! Check MT5 'Experts' tab.")
    
    # Also send an indicator update to keep Dashboard Active
    indicator = {
        "direction": "NONE",
        "status": "WAITING",
        "indicators": {
            "delta": 250.0,
            "htf_trend": "BULLISH",
            "structure": "BULLISH_BOS",
            "trend": "BULLISH",
            "zone_context": "DISCOUNT"
        }
    }
    
    indicator_payload = f"indicator {json.dumps(indicator)}"
    socket.send_string(indicator_payload)
    print("📡 Sent Indicator update to refresh Dashboard.")
    
    time.sleep(1)
    socket.close()
    context.term()

if __name__ == "__main__":
    send_test_signal()
