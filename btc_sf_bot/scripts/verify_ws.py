import asyncio
import sys
import os

# Add src to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.data.websocket import WebSocketHandler

async def verify_websocket():
    print("Starting WebSocket Verification...")
    handler = WebSocketHandler("btcusdt")
    
    # Register a simple callback to see data
    async def on_trade(data):
        print(f"Received trade: Price {data['price']}, Volume {data['volume']}")
    
    handler.register_callback('trade', on_trade)
    
    # Start the handler in a task
    task = asyncio.create_task(handler.start())
    
    print("Waiting for data (30 seconds)...")
    await asyncio.sleep(30)
    
    if handler.is_connected:
        print("✅ Connection is stable.")
    else:
        print("❌ Connection failed or dropped.")
    
    # Cancel the task
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    
    await handler.disconnect()
    print("Verification complete.")

if __name__ == "__main__":
    asyncio.run(verify_websocket())
