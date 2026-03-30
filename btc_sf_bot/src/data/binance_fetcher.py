"""
Binance Market Data Module - Phase 1
Fetches: OI, Order Book, Recent Trades for market condition analysis
"""
import asyncio
import aiohttp
import json
from typing import Dict, Optional
from datetime import datetime, timezone, timedelta
import pandas as pd
import numpy as np
from collections import deque

from ..utils.logger import get_logger

logger = get_logger(__name__)


class BinanceDataFetcher:
    """
    Fetches and caches Binance market data for trading analysis.
    Phase 1: OI, Order Book, Recent Trades (CVD)
    """
    
    def __init__(self, symbol: str = 'BTCUSDT', testnet: bool = False):
        self.symbol = symbol
        self.testnet = testnet
        
        # Base URLs
        if testnet:
            self.base_url = 'https://testnet.binancefuture.com'
            self.ws_url = 'wss://stream.binancefuture.com/ws'
        else:
            self.base_url = 'https://fapi.binance.com'
            self.ws_url = 'wss://fstream.binance.com/ws'
        
        # Cache storage
        self._oi_cache = {'data': None, 'timestamp': None}
        self._oi_history = deque(maxlen=60)  # Last 60 OI updates (approx 1-5 mins)
        self._orderbook_cache = {'data': None, 'timestamp': None}
        self._trades_buffer = deque(maxlen=1000)  # Last 1000 trades
        
        # Tiered Cache Expiry (seconds) - Section 22.4 Task 3
        # OI, Walls, Volume: 2 seconds cache (updates frequently ~5-8s)
        # Liquidations, Long/Short Ratio: 10 seconds cache (less frequent)
        # AggTrades (Whales): 5 seconds cache
        self.OI_CACHE_EXPIRY = 2
        self.ORDERBOOK_CACHE_EXPIRY = 2
        self.VOLUME_CACHE_EXPIRY = 2
        self.LIQUIDATIONS_CACHE_EXPIRY = 10
        self.LONG_SHORT_CACHE_EXPIRY = 10
        self.WHALES_CACHE_EXPIRY = 5
        self.FUNDING_CACHE_EXPIRY = 2
        
        # Individual caches for tiered data
        self._liquidations_cache = {'data': None, 'timestamp': None}
        self._whales_cache = {'data': None, 'timestamp': None}
        self._long_short_cache = {'data': None, 'timestamp': None}
        self._volume_cache = {'data': None, 'timestamp': None}
        self._funding_cache = {'data': None, 'timestamp': None}
        
        # CVD calculation
        self.cvd_data = {
            'cumulative': 0,
            'last_reset': datetime.now(),
            'period_seconds': 300  # Reset every 5 minutes
        }
        
        # Session for HTTP requests
        self._session: Optional[aiohttp.ClientSession] = None
        
    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={'Content-Type': 'application/json'}
            )
        return self._session
    
    async def fetch_open_interest(self, force_refresh: bool = False) -> Optional[Dict]:
        """
        Fetch Open Interest from Binance.
        Updates every 5 minutes (cached).
        
        Returns:
            {
                'openInterest': float,
                'openInterestChange': float,  # % change
                'timestamp': datetime
            }
        """
        try:
            # Check cache
            if not force_refresh and self._oi_cache['data'] is not None:
                time_since = (datetime.now() - self._oi_cache['timestamp']).total_seconds()
                if time_since < self.OI_CACHE_EXPIRY:
                    return self._oi_cache['data']
            
            # Fetch from API
            session = await self._get_session()
            url = f"{self.base_url}/fapi/v1/openInterest"
            
            async with session.get(url, params={'symbol': self.symbol}) as response:
                if response.status != 200:
                    logger.error(f"Failed to fetch OI: {response.status}")
                    return self._oi_cache['data']  # Return stale data if available
                
                data = await response.json()
                curr_oi = float(data['openInterest'])
                
                # Calculate change if we have previous data
                oi_change = 0
                if self._oi_cache['data'] is not None:
                    prev_oi = float(self._oi_cache['data']['openInterest'])
                    oi_change = ((curr_oi - prev_oi) / prev_oi) * 100 if prev_oi > 0 else 0
                
                # Update history
                self._oi_history.append(curr_oi)
                
                result = {
                    'openInterest': curr_oi,
                    'openInterestChange': oi_change,
                    'oi_history': list(self._oi_history),
                    'timestamp': datetime.now()
                }
                
                # Update cache
                self._oi_cache = {
                    'data': result,
                    'timestamp': datetime.now()
                }
                
                # Log only significant changes to reduce noise
                if abs(oi_change) > 0.05:
                    logger.debug(f"OI fetched: {result['openInterest']:,.0f} ({result['openInterestChange']:+.2f}%)")
                return result
                
        except Exception as e:
            logger.error(f"Error fetching OI: {e}")
            return self._oi_cache['data']  # Return stale data if available
    
    async def fetch_order_book(self, limit: int = 1000, force_refresh: bool = False) -> Optional[Dict]:
        """
        Fetch Order Book from Binance.
        Updates every 2 seconds (cached).
        
        Returns:
            {
                'bids': [[price, qty], ...],  # Top bids
                'asks': [[price, qty], ...],  # Top asks
                'bid_walls': [{'price': float, 'size': float}, ...],
                'ask_walls': [{'price': float, 'size': float}, ...],
                'timestamp': datetime
            }
        """
        try:
            # Check cache
            if not force_refresh and self._orderbook_cache['data'] is not None:
                time_since = (datetime.now() - self._orderbook_cache['timestamp']).total_seconds()
                if time_since < self.ORDERBOOK_CACHE_EXPIRY:
                    return self._orderbook_cache['data']
            
            # Fetch from API
            session = await self._get_session()
            url = f"{self.base_url}/fapi/v1/depth"
            
            async with session.get(url, params={'symbol': self.symbol, 'limit': limit}) as response:
                if response.status != 200:
                    logger.error(f"Failed to fetch order book: {response.status}")
                    return self._orderbook_cache['data']
                
                data = await response.json()
                
                # Parse order book (Section 26.2: Increased depth to 500 for better wall detection)
                bids_all = [[float(b[0]), float(b[1])] for b in data['bids'][:500]]
                asks_all = [[float(a[0]), float(a[1])] for a in data['asks'][:500]]
                
                bids_top = bids_all[:20]  # Keep top 20 for basic spread/best price
                asks_top = asks_all[:20]
                
                # Find walls (Section 27.3: Statistical threshold from order book distribution)
                # Calculate threshold dynamically from order book statistics
                all_orders = bids_all + asks_all
                if all_orders:
                    volumes = [o[1]for o in all_orders]
                    avg_vol = np.mean(volumes)
                    std_vol = np.std(volumes)
                    # Use mean + 2*std as threshold (statistically significant)
                    # This adaptive approach finds walls relative to current book state
                    wall_threshold =max(10, avg_vol + 2 * std_vol)
                else:
                    wall_threshold = 20  # Fallback: lower threshold to catch more walls
                
                bid_walls = [{'price': b[0], 'size': b[1]} for b in bids_all if b[1] >= wall_threshold]
                ask_walls = [{'price': a[0], 'size': a[1]} for a in asks_all if a[1] >= wall_threshold]
                
                result = {
                    'bids': bids_top,
                    'asks': asks_top,
                    'bid_walls': bid_walls,
                    'ask_walls': ask_walls,
                    'best_bid': bids_top[0][0] if bids_top else 0,
                    'best_ask': asks_top[0][0] if asks_top else 0,
                    'spread': asks_top[0][0] - bids_top[0][0] if bids_top and asks_top else 0,
                    'timestamp': datetime.now()
                }
                
                # Update cache
                self._orderbook_cache = {
                    'data': result,
                    'timestamp': datetime.now()
                }
                
                return result
                
        except Exception as e:
            logger.error(f"Error fetching order book: {e}")
            return self._orderbook_cache['data']
    
    def process_trade(self, trade: Dict):
        """
        Process a single trade for CVD calculation.
        Called by WebSocket handler.
        
        Args:
            trade: {'price': float, 'qty': float, 'isBuyerMaker': bool}
                isBuyerMaker=False = Market Buy (Taker Buy)
                isBuyerMaker=True = Market Sell (Taker Sell)
        """
        try:
            price = float(trade['price'])
            qty = float(trade['qty'])
            is_buyer_maker = trade.get('isBuyerMaker', False)
            
            # Calculate delta
            # isBuyerMaker=False: Market Buy (aggressive buyer) = +delta
            # isBuyerMaker=True: Market Sell (aggressive seller) = -delta
            if not is_buyer_maker:
                delta = qty * price  # Buy
            else:
                delta = -qty * price  # Sell
            
            # Update cumulative
            self.cvd_data['cumulative'] += delta
            
            # Store in buffer
            self._trades_buffer.append({
                'price': price,
                'qty': qty,
                'delta': delta,
                'timestamp': datetime.now(),
                'is_buyer_maker': is_buyer_maker
            })
            
            # Reset CVD every 5 minutes
            time_since_reset = (datetime.now() - self.cvd_data['last_reset']).total_seconds()
            if time_since_reset > self.cvd_data['period_seconds']:
                self.cvd_data['cumulative'] = 0
                self.cvd_data['last_reset'] = datetime.now()
                logger.debug("CVD reset for new period")
                
        except Exception as e:
            logger.error(f"Error processing trade: {e}")
    
    def get_cvd_metrics(self) -> Dict:
        """
        Get CVD (Cumulative Volume Delta) metrics.
        
        Returns:
            {
                'cvd': float,              # Cumulative value
                'cvd_delta': float,        # Change in last period
                'buy_volume': float,       # Aggressive buy volume
                'sell_volume': float,      # Aggressive sell volume
                'imbalance': float,        # Buy/Sell ratio
                'period_seconds': int      # Time period
            }
        """
        try:
            # Calculate from trades buffer
            buy_volume = 0
            sell_volume = 0
            
            for trade in self._trades_buffer:
                if trade['delta'] > 0:
                    buy_volume += trade['delta']
                else:
                    sell_volume += abs(trade['delta'])
            
            total_volume = buy_volume + sell_volume
            imbalance = buy_volume / sell_volume if sell_volume > 0 else 999
            
            return {
                'cvd': self.cvd_data['cumulative'],
                'cvd_delta': self.cvd_data['cumulative'],  # Since last reset
                'buy_volume': buy_volume,
                'sell_volume': sell_volume,
                'imbalance': imbalance,
                'period_seconds': self.cvd_data['period_seconds'],
                'trade_count': len(self._trades_buffer)
            }
            
        except Exception as e:
            logger.error(f"Error calculating CVD: {e}")
            return {
                'cvd': 0,
                'cvd_delta': 0,
                'buy_volume': 0,
                'sell_volume': 0,
                'imbalance': 1.0,
                'period_seconds': self.cvd_data['period_seconds'],
                'trade_count': 0
            }
    
    def get_liquidity_walls(self) -> Dict:
        """
        Get detected liquidity walls from order book.
        
        Returns:
            {
                'bid_walls': [{'price': float, 'size': float}, ...],
                'ask_walls': [{'price': float, 'size': float}, ...],
                'strongest_bid': dict or None,
                'strongest_ask': dict or None
            }
        """
        if self._orderbook_cache['data'] is None:
            return {'bid_walls': [], 'ask_walls': [], 'strongest_bid': None, 'strongest_ask': None}
        
        data = self._orderbook_cache['data']
        bid_walls = data.get('bid_walls', [])
        ask_walls = data.get('ask_walls', [])
        
        # Find strongest walls
        strongest_bid = max(bid_walls, key=lambda x: x['size']) if bid_walls else None
        strongest_ask = max(ask_walls, key=lambda x: x['size']) if ask_walls else None
        
        return {
            'bid_walls': bid_walls,
            'ask_walls': ask_walls,
            'strongest_bid': strongest_bid,
            'strongest_ask': strongest_ask
        }
    
    # =========================================================================
    # Section 22.4 Task 1: Data Layer Decoupling
    # Moved from SmartFlowManager to BinanceDataFetcher for better latency
    # =========================================================================
    
    async def fetch_liquidations(self, force_refresh: bool = False) -> Dict:
        """
        Fetch recent liquidation orders from Binance.
        Cache for 10 seconds.
        """
        try:
            # Check cache (10 second expiry for liquidations)
            if not force_refresh and self._liquidations_cache['data'] is not None:
                time_since = (datetime.now(timezone.utc) - self._liquidations_cache['timestamp']).total_seconds()
                if time_since < self.LIQUIDATIONS_CACHE_EXPIRY:
                    return self._liquidations_cache['data']
            
            session = await self._get_session()
            url = f"{self.base_url}/fapi/v1/allForceOrders"
            
            # Fetch last 50 liquidations
            async with session.get(url, params={'symbol': self.symbol, 'limit': 50}) as response:
                if response.status != 200:
                    return self._liquidations_cache['data'] or {'buy_liquidation': [], 'sell_liquidation': [], 'total_usd': 0}
                
                liquidations = await response.json()
                
                buy_liq = []
                sell_liq = []
                total_usd = 0
                
                # Liquidations are "Force Orders"
                # side: SELL means a LONG was liquidated (Sell is the liquidation action)
                # side: BUY means a SHORT was liquidated
                for order in liquidations:
                    price = float(order.get('price', 0))
                    orig_qty = float(order.get('origQty', 0))
                    side = order.get('side', '')
                    value_usd = price * orig_qty
                    event_time = datetime.fromtimestamp(order.get('time', 0) / 1000, tz=timezone.utc)
                    
                    # Only keep liquidations from the last 5 minutes
                    if (datetime.now(timezone.utc) - event_time).total_seconds() > 300:
                        continue
                        
                    liq_item = {
                        'price': price,
                        'qty': orig_qty,
                        'value_usd': value_usd,
                        'time': event_time.isoformat()
                    }
                    
                    if side == 'BUY':  # Short Liquidation
                        buy_liq.append(liq_item)
                    else:  # Sell Liquidation (Long)
                        sell_liq.append(liq_item)
                    
                    total_usd += value_usd
                
                result = {
                    'buy_liquidation': buy_liq,
                    'sell_liquidation': sell_liq,
                    'total_usd': total_usd,
                    'timestamp': datetime.now(timezone.utc)
                }
                
                # Update cache
                self._liquidations_cache = {
                    'data': result,
                    'timestamp': datetime.now(timezone.utc)
                }
                
                if total_usd > 0:
                    logger.debug(f"🔥 Liquidations: BUY_SHORT=${len(buy_liq)} deals, SELL_LONG=${len(sell_liq)} deals")
                return result
                
        except Exception as e:
            logger.error(f"Error fetching liquidations: {e}")
            return self._liquidations_cache['data'] or {'buy_liquidation': [], 'sell_liquidation': [], 'total_usd': 0}
    
    async def fetch_aggtrades(self, force_refresh: bool = False) -> Dict:
        """
        Fetch aggregated trades (whales) from Binance.
        Filter for trades > $100K (configurable).
        Cache for 5 seconds.
        
        Returns:
            {
                'buy_whales': [{'price': float, 'size': float, 'value_usd': float, 'time': str}, ...],
                'sell_whales': [{'price': float, 'size': float, 'value_usd': float, 'time': str}, ...],
                'total_buy': float,
                'total_sell': float,
                'timestamp': datetime
            }
        """
        try:
            # Check cache (5 second expiry for whales)
            if not force_refresh and self._whales_cache['data'] is not None:
                time_since = (datetime.now(timezone.utc) - self._whales_cache['timestamp']).total_seconds()
                if time_since < self.WHALES_CACHE_EXPIRY:
                    return self._whales_cache['data']
            
            session = await self._get_session()
            url = f"{self.base_url}/fapi/v1/aggTrades"
            
            async with session.get(url, params={'symbol': self.symbol, 'limit': 100}) as response:
                if response.status != 200:
                    logger.error(f"Failed to fetch aggTrades: {response.status}")
                    return self._whales_cache['data'] or {'buy_whales': [], 'sell_whales': [], 'total_buy': 0.0, 'total_sell': 0.0}
                
                agg_trades = await response.json()
                
                buy_whales = []
                sell_whales = []
                total_buy = 0.0
                total_sell = 0.0
                
                for trade in agg_trades:
                    price = float(trade.get('p', 0))
                    size = float(trade.get('q', 0))
                    value_usd = price * size
                    
                    # Filter: only trades > $500,000 (M5 Strategy: High Conviction Whale)
                    if value_usd >= 500_000:
                        is_buyer_maker = trade.get('m', False)  # True = sell whale
                        trade_time = datetime.fromtimestamp(trade.get('T', 0) / 1000, tz=timezone.utc)
                        
                        if is_buyer_maker:
                            sell_whales.append({
                                'price': price,
                                'size': size,
                                'value_usd': value_usd,
                                'time': trade_time.isoformat()
                            })
                            total_sell += value_usd
                        else:
                            buy_whales.append({
                                'price': price,
                                'size': size,
                                'value_usd': value_usd,
                                'time': trade_time.isoformat()
                            })
                            total_buy += value_usd
                
                result = {
                    'buy_whales': buy_whales,
                    'sell_whales': sell_whales,
                    'total_buy': total_buy,
                    'total_sell': total_sell,
                    'timestamp': datetime.now(timezone.utc)
                }
                
                # Update cache
                self._whales_cache = {
                    'data': result,
                    'timestamp': datetime.now(timezone.utc)
                }
                
                logger.debug(f"📊 Whales: BUY=${total_buy/1000:.0f}K, SELL=${total_sell/1000:.0f}K")
                return result
                
        except Exception as e:
            logger.error(f"Error fetching aggTrades: {e}")
            return self._whales_cache['data'] or {'buy_whales': [], 'sell_whales': [], 'total_buy': 0.0, 'total_sell': 0.0}
    
    async def fetch_long_short_ratio(self, force_refresh: bool = False) -> Dict:
        """
        Fetch Long/Short ratio from Binance (Global Accounts).
        Cache for 10 seconds.
        """
        try:
            # Check cache
            if not force_refresh and self._long_short_cache['data'] is not None:
                time_since = (datetime.now(timezone.utc) - self._long_short_cache['timestamp']).total_seconds()
                if time_since < self.LONG_SHORT_CACHE_EXPIRY:
                    return self._long_short_cache['data']
            
            session = await self._get_session()
            url = f"{self.base_url}/futures/data/globalLongShortAccountRatio"
            
            async with session.get(url, params={'symbol': self.symbol, 'period': '5m', 'limit': 1}) as response:
                if response.status != 200:
                    # Try alternative endpoint if first one fails
                    url_alt = f"{self.base_url}/fapi/v1/globalLongShortAccountRatio" # Some regions/API versions
                    async with session.get(url_alt, params={'symbol': self.symbol, 'period': '5m', 'limit': 1}) as resp_alt:
                        if resp_alt.status != 200:
                            logger.warning(f"Failed to fetch Long/Short ratio: {resp_alt.status}")
                            return self._long_short_cache['data'] or {'long_ratio': 0.5, 'short_ratio': 0.5}
                        data = await resp_alt.json()
                else:
                    data = await response.json()
                
                if data and isinstance(data, list) and len(data) > 0:
                    latest = data[0]
                    long_ratio = float(latest.get('longAccount', 0.5))
                    short_ratio = float(latest.get('shortAccount', 0.5))
                    
                    result = {
                        'long_ratio': long_ratio,
                        'short_ratio': short_ratio,
                        'timestamp': datetime.now(timezone.utc).isoformat()
                    }
                    
                    # Update cache
                    self._long_short_cache = {
                        'data': result,
                        'timestamp': datetime.now(timezone.utc)
                    }
                    return result
                
                return {'long_ratio': 0.5, 'short_ratio': 0.5}
                
        except Exception as e:
            logger.error(f"Error fetching Long/Short ratio: {e}")
            return self._long_short_cache['data'] or {'long_ratio': 0.5, 'short_ratio': 0.5}
    
    async def fetch_volume(self, force_refresh: bool = False) -> Dict:
        """
        Fetch volume data from Binance klines.
        Cache for 2 seconds.
        
        Returns:
            {
                'current_volume': float,
                'avg_volume': float,
                'volume_ratio': float
            }
        """
        try:
            # Check cache (2 second expiry for volume)
            if not force_refresh and self._volume_cache['data'] is not None:
                time_since = (datetime.now(timezone.utc) - self._volume_cache['timestamp']).total_seconds()
                if time_since < self.VOLUME_CACHE_EXPIRY:
                    return self._volume_cache['data']
            
            session = await self._get_session()
            url = f"{self.base_url.replace('fapi', 'api')}/api/v3/klines"
            
            async with session.get(url, params={'symbol': self.symbol, 'interval': '1m', 'limit': 100}) as response:
                if response.status != 200:
                    logger.error(f"Failed to fetch volume: {response.status}")
                    return self._volume_cache['data'] or {'current_volume': 0, 'avg_volume': 0, 'volume_ratio': 1}
                
                klines = await response.json()
                
                if klines and len(klines) >= 20:
                    # Get current candle volume (quote asset volume = USDT)
                    current_volume = float(klines[-1][7]) if len(klines[-1]) > 7 else 0
                    
                    # Calculate average volume (last 20 candles)
                    volumes = [float(k[7]) for k in klines[-20:-1] if len(k) > 7]
                    avg_volume = sum(volumes) / len(volumes) if volumes else 1
                    
                    # Calculate volume ratio
                    volume_ratio = current_volume / avg_volume if avg_volume > 0 else 1
                    
                    result = {
                        'current_volume': current_volume,
                        'avg_volume': avg_volume,
                        'volume_ratio': volume_ratio
                    }
                else:
                    result = {'current_volume': 0, 'avg_volume': 0, 'volume_ratio': 1}
                
                # Update cache
                self._volume_cache = {
                    'data': result,
                    'timestamp': datetime.now(timezone.utc)
                }
                
                return result
                
        except Exception as e:
            logger.error(f"Error fetching volume: {e}")
            return self._volume_cache['data'] or {'current_volume': 0, 'avg_volume': 0, 'volume_ratio': 1}
    
    async def fetch_funding_rate(self, force_refresh: bool = False) -> float:
        """
        Fetch current funding rate from Binance.
        Cache for 2 seconds.
        
        Returns:
            float: Funding rate
        """
        try:
            # Check cache (2 second expiry for funding)
            if not force_refresh and self._funding_cache['data'] is not None:
                time_since = (datetime.now(timezone.utc) - self._funding_cache['timestamp']).total_seconds()
                if time_since < self.FUNDING_CACHE_EXPIRY:
                    return self._funding_cache['data']
            
            session = await self._get_session()
            url = f"{self.base_url}/fapi/v1/premiumIndex"
            
            async with session.get(url, params={'symbol': self.symbol}) as response:
                if response.status != 200:
                    logger.error(f"Failed to fetch funding rate: {response.status}")
                    return self._funding_cache['data'] or 0
                
                fr_data = await response.json()
                funding_rate = float(fr_data.get('lastFundingRate', 0))
                
                # Update cache
                self._funding_cache = {
                    'data': funding_rate,
                    'timestamp': datetime.now(timezone.utc)
                }
                
                return funding_rate
                
        except Exception as e:
            logger.error(f"Error fetching funding rate: {e}")
            return self._funding_cache['data'] or 0
    
    async def get_all_market_data(self) -> Dict:
        """
        Get all Phase 1 market data in one call.
        Includes all data types with tiered caching.
        
        Returns:
            {
                'oi': Open Interest data,
                'orderbook': Order book data,
                'cvd': CVD metrics,
                'walls': Liquidity walls,
                'liquidations': Liquidation data (10s cache),
                'whales': Whale trade data (5s cache),
                'long_short_ratio': Long/Short ratio (10s cache),
                'volume': Volume data (2s cache),
                'funding_rate': Funding rate (2s cache),
                'timestamp': datetime
            }
        """
        # Fetch all data in parallel (with tiered caching built into each method)
        oi_task = self.fetch_open_interest()
        ob_task = self.fetch_order_book()
        liq_task = self.fetch_liquidations()
        whale_task = self.fetch_aggtrades()
        ls_task = self.fetch_long_short_ratio()
        vol_task = self.fetch_volume()
        funding_task = self.fetch_funding_rate()
        
        oi_data, ob_data, liq_data, whale_data, ls_data, vol_data, funding_data = await asyncio.gather(
            oi_task, ob_task, liq_task, whale_task, ls_task, vol_task, funding_task,
            return_exceptions=True
        )
        
        # Handle errors - return cached/stale data
        if isinstance(oi_data, Exception):
            logger.error(f"OI fetch failed: {oi_data}")
            cached_oi = self._oi_cache['data']
            if cached_oi:
                oi_data = cached_oi
            else:
                oi_data = {'openInterest': 0, 'openInterestChange': 0, 'oi_history': list(self._oi_history)}
        
        if isinstance(ob_data, Exception):
            logger.error(f"Order book fetch failed: {ob_data}")
            ob_data = self._orderbook_cache['data'] or {'bids': [], 'asks': []}
        
        if isinstance(liq_data, Exception):
            logger.error(f"Liquidations fetch failed: {liq_data}")
            liq_data = self._liquidations_cache['data'] or {'buy_liquidation': [], 'sell_liquidation': []}
        
        if isinstance(whale_data, Exception):
            logger.error(f"Whales fetch failed: {whale_data}")
            whale_data = self._whales_cache['data'] or {'buy_whales': [], 'sell_whales': [], 'total_buy': 0.0, 'total_sell': 0.0}
        
        if isinstance(ls_data, Exception):
            logger.error(f"Long/Short fetch failed: {ls_data}")
            ls_data = self._long_short_cache['data'] or {'long_ratio': 0.5, 'short_ratio': 0.5}
        
        if isinstance(vol_data, Exception):
            logger.error(f"Volume fetch failed: {vol_data}")
            vol_data = self._volume_cache['data'] or {'current_volume': 0, 'avg_volume': 0, 'volume_ratio': 1}
        
        if isinstance(funding_data, Exception):
            logger.error(f"Funding rate fetch failed: {funding_data}")
            funding_data = self._funding_cache['data'] or 0
        
        return {
            'oi': oi_data,
            'orderbook': ob_data,
            'cvd': self.get_cvd_metrics(),
            'walls': self.get_liquidity_walls(),
            'liquidations': liq_data,
            'whales': whale_data,
            'long_short_ratio': ls_data,
            'volume': vol_data,
            'funding_rate': funding_data,
            'timestamp': datetime.now(timezone.utc)
        }
    
    async def close(self):
        """Close HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()


# Global instance for singleton pattern
_binance_fetcher: Optional[BinanceDataFetcher] = None


def get_binance_fetcher(symbol: str = 'BTCUSDT', testnet: bool = False) -> BinanceDataFetcher:
    """Get or create BinanceDataFetcher instance."""
    global _binance_fetcher
    if _binance_fetcher is None:
        _binance_fetcher = BinanceDataFetcher(symbol=symbol, testnet=testnet)
    return _binance_fetcher


async def test_binance_fetcher():
    """Test the Binance data fetcher."""
    fetcher = BinanceDataFetcher(symbol='BTCUSDT', testnet=True)
    
    try:
        print("Testing Binance Data Fetcher...")
        
        # Test OI
        print("\n1. Fetching Open Interest...")
        oi = await fetcher.fetch_open_interest()
        print(f"   OI: {oi['openInterest']:,.0f} ({oi['openInterestChange']:+.2f}%)")
        
        # Test Order Book
        print("\n2. Fetching Order Book...")
        ob = await fetcher.fetch_order_book(limit=100)
        print(f"   Best Bid: {ob['best_bid']:.2f}")
        print(f"   Best Ask: {ob['best_ask']:.2f}")
        print(f"   Spread: {ob['spread']:.2f}")
        print(f"   Bid Walls: {len(ob['bid_walls'])}")
        print(f"   Ask Walls: {len(ob['ask_walls'])}")
        
        # Test CVD (simulate some trades)
        print("\n3. Testing CVD calculation...")
        test_trades = [
            {'price': 70200, 'qty': 0.5, 'isBuyerMaker': False},  # Buy
            {'price': 70201, 'qty': 0.3, 'isBuyerMaker': True},   # Sell
            {'price': 70202, 'qty': 1.0, 'isBuyerMaker': False},  # Buy
        ]
        for trade in test_trades:
            fetcher.process_trade(trade)
        
        cvd = fetcher.get_cvd_metrics()
        print(f"   CVD: {cvd['cvd']:.2f}")
        print(f"   Imbalance: {cvd['imbalance']:.2f}")
        
        # Test combined
        print("\n4. Fetching all data...")
        all_data = await fetcher.get_all_market_data()
        print("   Success!")
        
    except Exception as e:
        print(f"Error: {e}")
    finally:
        await fetcher.close()


if __name__ == '__main__':
    asyncio.run(test_binance_fetcher())
