import zmq
import json
import time
import requests
import asyncio
import os
from datetime import datetime, timezone
from dotenv import load_dotenv

# Load environment variables
load_dotenv("d:/CODING WORKS/SMC_AI_Project/btc_sf_bot/config/.env")

def get_btc_price():
    """Fetch current BTC price from Binance API."""
    try:
        response = requests.get("https://api.binance.com/api/v3/ticker/price", params={"symbol": "BTCUSDT"})
        data = response.json()
        return float(data['price'])
    except Exception as e:
        print(f"⚠️ Warning: Could not fetch live price: {e}")
        return 65000.0

async def send_telegram_signal(signal):
    """Send signal to Telegram using the real bot token and chat ID."""
    bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
    chat_id = os.getenv('TELEGRAM_CHAT_ID')
    
    if not bot_token or not chat_id:
        print("❌ Telegram credentials missing in .env file.")
        return False
        
    direction = signal['direction']
    price = signal['entry_price']
    sl = signal['stop_loss']
    tp = signal['take_profit']
    score = signal['score']
    reason = signal['reason']
    
    emoji = "🟢" if direction == "LONG" else "🔴"
    rr_ratio = abs(tp - price) / abs(price - sl) if abs(price - sl) > 0 else 0
    
    formatted_reason = "• " + reason.replace('.', '\n• ')
    
    message = f"""
{emoji} <b>NEW TEST SIGNAL: {direction}</b>

💰 <b>Entry:</b> ${price:,.2f}
🛡️ <b>SL:</b> ${sl:,.2f}
🎯 <b>TP:</b> ${tp:,.2f}
⚖️ <b>RR:</b> 1:{rr_ratio:.1f}

📊 <b>ANALYSIS</b>
🔥 <b>Confidence:</b> 95%
⭐ <b>Score:</b> {score}/10
📈 <b>HTF Trend:</b> BULLISH
🏗️ <b>Structure:</b> BULLISH_BOS

📝 <b>FULL REASON:</b>
{formatted_reason}

#BTC #TestSignal #SmartFlow
    """
    
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    data = {"chat_id": chat_id, "text": message, "parse_mode": "HTML"}
    
    try:
        import httpx
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=data, timeout=10.0)
            if response.status_code == 200:
                print("✅ Telegram Alert Sent!")
                return True
            else:
                print(f"❌ Telegram Error: {response.text}")
                return False
    except Exception as e:
        print(f"❌ Telegram Request Failed: {e}")
        return False

def send_mt5_signal(signal):
    """Send signal to MT5 via ZeroMQ."""
    context = zmq.Context()
    socket = context.socket(zmq.PUB)
    
    host = "127.0.0.1"
    port = 5555
    try:
        socket.bind(f"tcp://{host}:{port}")
    except Exception as e:
        print(f"❌ Could not bind ZMQ: {e}. Is another test script running?")
        return
        
    print(f"🚀 ZMQ Publisher active on {host}:{port}")
    print("⏳ Waiting 2s for MT5 to sync...")
    time.sleep(2)
    
    topic = "signal"
    payload = f"{topic} {json.dumps(signal)}"
    
    print(f"📡 Sending Signal to MT5: {signal['direction']} @ ${signal['entry_price']:.2f}")
    
    for i in range(5):
        socket.send_string(payload)
        time.sleep(0.5)
    
    # Also send indicator to keep dash active
    indicator = {
        "direction": "NONE",
        "status": "ACTIVE",
        "indicators": {
            "delta": 350.0,
            "htf_trend": "BULLISH",
            "structure": "BULLISH_BOS",
            "trend": "BULLISH",
            "zone_context": "DISCOUNT"
        }
    }
    socket.send_string(f"indicator {json.dumps(indicator)}")
    
    print("✅ MT5 Signals Sent!")
    socket.close()
    context.term()

async def main():
    print("🔔 Starting Integrated System Test (Real Flow)")
    print("-" * 40)
    
    price = get_btc_price()
    signal = {
        "signal_id": f"REAL_TEST_{int(time.time())}",
        "direction": "LONG",
        "entry_price": price,
        "stop_loss": price * 0.995,
        "take_profit": price * 1.015,
        "score": 9,
        "reason": "Integrated test. Real-time BTC price used. HTF Bullish confirms structure BOS.",
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    
    # Send to Telegram
    await send_telegram_signal(signal)
    
    # Send to MT5
    send_mt5_signal(signal)
    
    print("-" * 40)
    print("🏁 Test Finished. Check your Telegram and MT5 Experts tab.")

if __name__ == "__main__":
    asyncio.run(main())
