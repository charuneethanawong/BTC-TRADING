"""
Backtest Data Loader
"""
import os
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
import pandas as pd
import numpy as np

from ..utils.logger import get_logger

logger = get_logger(__name__)


class BacktestDataLoader:
    """Load and prepare historical data for backtesting."""
    
    def __init__(self, symbol: str = 'BTC/USDT:USDT', timeframe: str = '5m'):
        """
        Initialize data loader.
        
        Args:
            symbol: Trading pair
            timeframe: Candle timeframe
        """
        self.symbol = symbol
        self.timeframe = timeframe
        self.data: Optional[pd.DataFrame] = None
        self.htf_data: Optional[pd.DataFrame] = None
    
    def load_from_binance(
        self,
        days: int = 30,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> pd.DataFrame:
        """
        Load historical data from Binance.
        
        Args:
            days: Number of days to load
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)
        
        Returns:
            DataFrame with OHLCV data
        """
        try:
            import ccxt
            
            exchange = ccxt.binance({
                'enableRateLimit': True,
                'options': {'defaultType': 'future'}
            })
            
            if start_date and end_date:
                since = exchange.parse8601(f"{start_date}T00:00:00Z")
                end_ts = exchange.parse8601(f"{end_date}T23:59:59Z")
            else:
                end_ts = exchange.milliseconds()
                since = end_ts - (days * 24 * 60 * 60 * 1000)
            
            all_candles = []
            current_ts = since
            limit = 1000
            
            timeframe_ms = exchange.parse_timeframe(self.timeframe) * 1000
            
            while current_ts < end_ts:
                candles = exchange.fetch_ohlcv(
                    self.symbol,
                    self.timeframe,
                    since=current_ts,
                    limit=limit
                )
                
                if not candles:
                    break
                
                all_candles.extend(candles)
                current_ts = candles[-1][0] + timeframe_ms
                
                if len(candles) < limit:
                    break
            
            df = pd.DataFrame(
                all_candles,
                columns=['timestamp', 'open', 'high', 'low', 'close', 'volume']
            )
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            df.set_index('timestamp', inplace=True)
            
            self.data = df
            logger.info(f"Loaded {len(df)} candles from {df.index[0]} to {df.index[-1]}")
            
            return df
            
        except Exception as e:
            logger.error(f"Error loading data from Binance: {e}")
            raise
    
    def load_from_csv(self, filepath: str) -> pd.DataFrame:
        """
        Load historical data from CSV file.
        
        Args:
            filepath: Path to CSV file
        
        Returns:
            DataFrame with OHLCV data
        """
        df = pd.read_csv(filepath, parse_dates=['timestamp'])
        df.set_index('timestamp', inplace=True)
        
        required_cols = ['open', 'high', 'low', 'close', 'volume']
        missing = [col for col in required_cols if col not in df.columns]
        if missing:
            raise ValueError(f"Missing columns: {missing}")
        
        self.data = df
        logger.info(f"Loaded {len(df)} candles from {filepath}")
        
        return df
    
    def load_htf_data(self, htf_timeframe: str = '1h') -> pd.DataFrame:
        """
        Load higher timeframe data for HTF analysis.
        
        Args:
            htf_timeframe: Higher timeframe (e.g., '1h')
        
        Returns:
            DataFrame with HTF OHLCV data
        """
        if self.data is None:
            raise ValueError("No data loaded. Call load_from_binance() first.")
        
        try:
            import ccxt
            
            exchange = ccxt.binance({
                'enableRateLimit': True,
                'options': {'defaultType': 'future'}
            })
            
            start_ts = int(self.data.index[0].timestamp() * 1000)
            end_ts = int(self.data.index[-1].timestamp() * 1000)
            
            all_candles = []
            current_ts = start_ts
            limit = 1000
            timeframe_ms = exchange.parse_timeframe(htf_timeframe) * 1000
            
            while current_ts < end_ts:
                candles = exchange.fetch_ohlcv(
                    self.symbol,
                    htf_timeframe,
                    since=current_ts,
                    limit=limit
                )
                
                if not candles:
                    break
                
                all_candles.extend(candles)
                current_ts = candles[-1][0] + timeframe_ms
                
                if len(candles) < limit:
                    break
            
            df = pd.DataFrame(
                all_candles,
                columns=['timestamp', 'open', 'high', 'low', 'close', 'volume']
            )
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            df.set_index('timestamp', inplace=True)
            
            self.htf_data = df
            logger.info(f"Loaded {len(df)} HTF candles ({htf_timeframe})")
            
            return df
            
        except Exception as e:
            logger.error(f"Error loading HTF data: {e}")
            raise
    
    def generate_synthetic_order_flow(self) -> pd.DataFrame:
        """
        Generate synthetic order flow data from OHLCV.
        
        Since we don't have historical order book data, we approximate:
        - Delta: Based on candle direction and volume
        - Imbalance: Based on candle body vs wicks
        - CVD: Cumulative delta
        
        Returns:
            DataFrame with synthetic order flow data
        """
        if self.data is None:
            raise ValueError("No data loaded. Call load_from_binance() first.")
        
        df = self.data.copy()
        
        df['body'] = abs(df['close'] - df['open'])
        df['range'] = df['high'] - df['low']
        df['upper_wick'] = df['high'] - df[['open', 'close']].max(axis=1)
        df['lower_wick'] = df[['open', 'close']].min(axis=1) - df['low']
        
        df['is_bullish'] = (df['close'] > df['open']).astype(int)
        df['is_bearish'] = (df['close'] < df['open']).astype(int)
        
        df['delta'] = np.where(
            df['is_bullish'] == 1,
            df['volume'] * (df['body'] / df['range']).fillna(0.5),
            -df['volume'] * (df['body'] / df['range']).fillna(0.5)
        )
        
        df['cvd'] = df['delta'].cumsum()
        
        df['imbalance_ratio'] = np.where(
            df['is_bullish'] == 1,
            1 + (df['body'] / df['range']).fillna(0.5),
            1 / (1 + (df['body'] / df['range']).fillna(0.5))
        )
        
        df['buy_volume'] = np.where(df['delta'] > 0, df['delta'], 0)
        df['sell_volume'] = np.where(df['delta'] < 0, abs(df['delta']), 0)
        df['total_flow'] = df['buy_volume'] + df['sell_volume']
        df['buy_pct'] = (df['buy_volume'] / df['total_flow'] * 100).fillna(50)
        df['sell_pct'] = (df['sell_volume'] / df['total_flow'] * 100).fillna(50)
        
        logger.info("Generated synthetic order flow data")
        
        return df
    
    def get_ohlcv_slice(
        self,
        start_idx: int,
        end_idx: int
    ) -> pd.DataFrame:
        """
        Get a slice of OHLCV data.
        
        Args:
            start_idx: Start index
            end_idx: End index
        
        Returns:
            DataFrame slice
        """
        if self.data is None:
            raise ValueError("No data loaded.")
        
        return self.data.iloc[start_idx:end_idx].copy()
    
    def get_order_book_snapshot(self, idx: int) -> Dict:
        """
        Generate synthetic order book snapshot for a candle.
        
        Args:
            idx: Candle index
        
        Returns:
            Dictionary with bids and asks
        """
        if self.data is None:
            raise ValueError("No data loaded.")
        
        row = self.data.iloc[idx]
        current_price = row['close']
        
        buy_pct = getattr(row, 'buy_pct', 50)
        sell_pct = 100 - buy_pct
        
        total_vol = row['volume']
        
        bids = {}
        asks = {}
        
        spread = current_price * 0.0001
        
        for i in range(5):
            bid_price = current_price - spread * (i + 1)
            ask_price = current_price + spread * (i + 1)
            
            bid_vol = total_vol * (buy_pct / 100) / (i + 1)
            ask_vol = total_vol * (sell_pct / 100) / (i + 1)
            
            bids[bid_price] = bid_vol
            asks[ask_price] = ask_vol
        
        return {'bids': bids, 'asks': asks}
    
    def get_trades_snapshot(self, idx: int) -> List[Dict]:
        """
        Generate synthetic trades for a candle.
        
        Args:
            idx: Candle index
        
        Returns:
            List of trade dictionaries
        """
        if self.data is None:
            raise ValueError("No data loaded.")
        
        row = self.data.iloc[idx]
        
        delta = getattr(row, 'delta', 0)
        volume = row['volume']
        
        num_trades = max(10, int(volume / 10))
        trades = []
        
        buy_vol = (volume + delta) / 2
        sell_vol = (volume - delta) / 2
        
        for i in range(num_trades):
            is_buy = np.random.random() < (buy_vol / volume) if volume > 0 else True
            
            trade_vol = volume / num_trades
            
            trades.append({
                'price': row['close'],
                'volume': trade_vol,
                'is_buyer_maker': not is_buy,
                'time': row.name
            })
        
        return trades
    
    def prepare_backtest_data(
        self,
        days: int = 30,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        load_htf: bool = True
    ) -> Tuple[pd.DataFrame, Optional[pd.DataFrame]]:
        """
        Prepare all data needed for backtesting.
        
        Args:
            days: Number of days to load
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)
            load_htf: Whether to load HTF data
        
        Returns:
            Tuple of (main_data, htf_data)
        """
        self.load_from_binance(days=days, start_date=start_date, end_date=end_date)
        
        self.data = self.generate_synthetic_order_flow()
        
        htf_data = None
        if load_htf:
            htf_data = self.load_htf_data('1h')
        
        return self.data, htf_data
