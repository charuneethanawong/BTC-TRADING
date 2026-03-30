
import asyncio
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Add project root to path
ROOT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT_DIR))

from src.main import BTCSFBot
from src.utils.logger import setup_logger

async def test_signal_flow():
    # Load env
    env_path = Path(__file__).parent.parent / "config" / ".env"
    load_dotenv(env_path)
    
    # Setup logger
    logger = setup_logger('test_flow', level='INFO')
    
    # Create bot
    bot = BTCSFBot()
    
    # Initialize
    print("--- Initializing Bot ---")
    if not bot.initialize():
        print("Failed to initialize bot")
        return
    
    print("--- Running analyze_and_trade once ---")
    try:
        # Run one loop
        await bot.analyze_and_trade()
        
        # Verify indicators
        import pandas as pd
        candles = bot.connector.get_ohlcv('BTC/USDT:USDT', '1m', limit=500)
        order_book = bot.cache.get_order_book()
        oi = bot.connector.get_open_interest('BTC/USDT:USDT')
        order_book['open_interest'] = oi
        order_book['prev_oi'] = bot.prev_oi
        trades = bot.cache.get_trades(100)
        avg_volume = bot.cache.get_average_volume(20)
        
        indicators = bot._get_indicators_data(candles, order_book, trades, bot.current_price, avg_volume)
        print("\n--- Indicators Data ---")
        import json
        print(json.dumps(indicators['indicators'], indent=2))
        
        print("\n--- analyze_and_trade completed ---")
    except Exception as e:
        print(f"Error during analyze_and_trade: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_signal_flow())
