"""
Binance Connector Module
"""
import ccxt
import pandas as pd
from typing import Dict, List, Optional, Any
from datetime import datetime

from ..utils.logger import get_logger

logger = get_logger(__name__)


class BinanceConnector:
    """Binance exchange connector using CCXT."""
    
    def __init__(self, api_key: str = None, secret: str = None, testnet: bool = False):
        """
        Initialize Binance connector.
        
        Args:
            api_key: Binance API key
            secret: Binance API secret
            testnet: Use testnet or not
        """
        self.testnet = testnet
        self.api_key = api_key
        self.secret = secret
        
        # Initialize exchange
        if testnet:
            self.exchange = ccxt.binance({
                'apiKey': api_key,
                'secret': secret,
                'enableRateLimit': True,
                'options': {
                    'defaultType': 'future',
                    'testnet': True
                }
            })
            self.exchange.set_sandbox_mode(True)
        else:
            self.exchange = ccxt.binance({
                'apiKey': api_key,
                'secret': secret,
                'enableRateLimit': True,
                'options': {
                    'defaultType': 'future'
                }
            })
        
        logger.info(f"Binance connector initialized (testnet={testnet})")
    
    def connect(self) -> bool:
        """Test connection to exchange."""
        try:
            self.exchange.fetch_time()
            logger.info("Connected to Binance successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to Binance: {e}")
            return False
    
    def get_ticker(self, symbol: str) -> Dict[str, Any]:
        """
        Get current ticker for symbol.
        
        Args:
            symbol: Trading symbol (e.g., 'BTC/USDT:USDT')
        
        Returns:
            Ticker data
        """
        try:
            return self.exchange.fetch_ticker(symbol)
        except Exception as e:
            logger.error(f"Failed to fetch ticker: {e}")
            return {}
    
    def get_price(self, symbol: str) -> float:
        """Get current price."""
        ticker = self.get_ticker(symbol)
        return ticker.get('last', 0)
    
    def get_order_book(self, symbol: str, limit: int = 20) -> Dict[str, List]:
        """
        Get order book.
        
        Args:
            symbol: Trading symbol
            limit: Number of levels
        
        Returns:
            Order book with 'bids' and 'asks'
        """
        try:
            return self.exchange.fetch_order_book(symbol, limit)
        except Exception as e:
            logger.error(f"Failed to fetch order book: {e}")
            return {'bids': [], 'asks': []}
    
    def get_order_book_imbalance(self, symbol: str, limit: int = 20) -> float:
        """
        Calculate order book imbalance.
        
        Args:
            symbol: Trading symbol
            limit: Number of levels
        
        Returns:
            Imbalance ratio (bid_vol / ask_vol)
        """
        order_book = self.get_order_book(symbol, limit)
        
        bids = order_book.get('bids', [])
        asks = order_book.get('asks', [])
        
        if not bids or not asks:
            return 1.0
        
        bid_vol = sum(float(b[1]) for b in bids)
        ask_vol = sum(float(a[1]) for a in asks)
        
        if ask_vol == 0:
            return 10.0  # Max bullish
        
        return bid_vol / ask_vol
    
    def get_recent_trades(self, symbol: str, limit: int = 100) -> List[Dict]:
        """
        Get recent trades.
        
        Args:
            symbol: Trading symbol
            limit: Number of trades
        
        Returns:
            List of trades
        """
        try:
            return self.exchange.fetch_trades(symbol, limit=limit)
        except Exception as e:
            logger.error(f"Failed to fetch trades: {e}")
            return []
    
    def get_ohlcv(self, symbol: str, timeframe: str = '5m', limit: int = 300) -> pd.DataFrame:
        """
        Get OHLCV data.
        
        Args:
            symbol: Trading symbol
            timeframe: Timeframe (1m, 5m, 15m, 1h, etc.)
            limit: Number of candles (default 300 = 25h for 5m)
        
        Returns:
            DataFrame with OHLCV data
        """
        try:
            ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
            
            df = pd.DataFrame(ohlcv, columns=[
                'timestamp', 'open', 'high', 'low', 'close', 'volume'
            ])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            df.set_index('timestamp', inplace=True)
            
            return df
        except Exception as e:
            logger.error(f"Failed to fetch OHLCV: {e}")
            return pd.DataFrame()
    
    def get_funding_rate(self, symbol: str) -> float:
        """Get current funding rate."""
        try:
            ticker = self.get_ticker(symbol)
            return ticker.get('fundingRate', 0)
        except Exception:
            return 0
    
    def get_available_balance(self) -> float:
        """Get available balance."""
        try:
            balance = self.exchange.fetch_balance()
            return float(balance.get('USDT', {}).get('free', 0))
        except Exception as e:
            logger.error(f"Failed to fetch balance: {e}")
            return 0
    
    def set_leverage(self, symbol: str, leverage: int):
        """Set leverage for symbol."""
        try:
            symbol_raw = symbol.replace('/USDT', '').replace(':USDT', '')
            # Try different CCXT methods
            try:
                self.exchange.fapiPrivatePostLeverage({
                    'symbol': symbol_raw,
                    'leverage': leverage
                })
            except:
                try:
                    self.exchange.set_leverage(leverage, symbol_raw)
                except:
                    pass  # Skip if not available
            logger.info(f"Leverage set to {leverage}x for {symbol}")
        except Exception as e:
            logger.warning(f"Failed to set leverage: {e}")
    
    def get_market_price_precision(self, symbol: str) -> int:
        """Get price precision for symbol."""
        try:
            market = self.exchange.load_markets()[symbol]
            return market.get('precision', {}).get('price', 2)
        except Exception:
            return 2
    
    def get_contract_size(self, symbol: str) -> float:
        """Get contract size for symbol."""
        try:
            market = self.exchange.load_markets()[symbol]
            return market.get('contractSize', 1)
        except Exception:
            return 1

    def get_open_interest(self, symbol: str) -> float:
        """
        Get current Open Interest for symbol.
        
        Args:
            symbol: Trading symbol (e.g., 'BTC/USDT:USDT')
            
        Returns:
            Open Interest value
        """
        try:
            # CCXT usually supports fetch_open_interest or similar
            # For Binance Futures, it's specific API
            # Fix: BTC/USDT:USDT -> BTCUSDT (not just BTC)
            symbol_raw = symbol.replace('/USDT', '').replace(':USDT', '').replace('USDT', '')
            # Ensure we have proper format: BTCUSDT
            if not symbol_raw.endswith('USDT'):
                symbol_raw = symbol_raw + 'USDT'
            response = self.exchange.fapiPublicGetOpenInterest({'symbol': symbol_raw})
            return float(response.get('openInterest', 0))
        except Exception as e:
            logger.warning(f"Failed to fetch open interest: {e}")
            return 0
