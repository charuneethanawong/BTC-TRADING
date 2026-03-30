import asyncio
import os
import sys
import json
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.data.websocket import WebSocketHandler

async def test_websocket():
    print("--- Testing Binance WebSocket Connection ---")
    
    # Initialize with BTCUSDT
    ws = WebSocketHandler("btcusdt")
    
    print(f"[*] Connecting to: {ws.ws_url}...")
    
    connected = await ws.connect()
    if not connected:
        print("[ERROR] Failed to connect to WebSocket. Check your internet or Binance availability.")
        return

    print("[OK] Connected successfully!")
    print("[*] Waiting for 5 trade/depth messages...")
    
    msg_count = 0
    
    async def trade_callback(data):
        nonlocal msg_count
        msg_count += 1
        print(f" [TRADE] Price: {data['price']}, Vol: {data['volume']}")
        
    async def ob_callback(data):
        nonlocal msg_count
        msg_count += 1
        bids = list(data['bids'].keys())[:3]
        print(f" [DEPTH] Top 3 Bids: {bids}")

    ws.register_callback('trade', trade_callback)
    ws.register_callback('order_book', ob_callback)
    
    # Start listening in a task
    listen_task = asyncio.create_task(ws.listen())
    
    # Wait until we get some messages
    start_time = asyncio.get_event_loop().time()
    while msg_count < 5:
        await asyncio.sleep(1)
        if asyncio.get_event_loop().time() - start_time > 15:
            print("[TIMEOUT] No messages received within 15 seconds.")
            break
            
    print(f"\n[DONE] Received {msg_count} messages.")
    
    # Clean up
    listen_task.cancel()
    await ws.disconnect()
    print("--- WebSocket Test Finished ---")

if __name__ == "__main__":
    asyncio.run(test_websocket())
