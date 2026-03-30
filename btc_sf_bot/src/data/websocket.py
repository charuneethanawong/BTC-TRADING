"""
WebSocket Handler Module
"""
import asyncio
import json
import websockets
from typing import Dict, List, Callable, Optional
from datetime import datetime

from ..utils.logger import get_logger

logger = get_logger(__name__)


class WebSocketHandler:
    """Real-time WebSocket handler for Binance."""
    
    def __init__(self, symbol: str = "btcusdt"):
        """
        Initialize WebSocket handler.
        
        Args:
            symbol: Trading symbol (lowercase, e.g., 'btcusdt')
        """
        self.symbol = symbol.lower()
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.is_connected = False
        self.callbacks: Dict[str, Callable] = {}
        self.trade_buffer: List[Dict] = []
        self.order_book_buffer: Dict = {'bids': {}, 'asks': {}}
        
        # WebSocket URL (Testnet)
        self.ws_url = f"wss://stream.binancefuture.com/stream?streams={self.symbol}@aggTrade/{self.symbol}@depth20@100ms"
    
    async def connect(self) -> bool:
        """Connect to WebSocket."""
        try:
            logger.info(f"Connecting to {self.ws_url}...")
            # Increase ping interval and timeout to prevent 1011 keepalive timeout errors
            # Default is 20s for both. Setting to 30s/60s for better resilience.
            self.ws = await websockets.connect(
                self.ws_url,
                ping_interval=30,
                ping_timeout=60,
                close_timeout=10
            )
            self.is_connected = True
            logger.info(f"WebSocket connected successfully for {self.symbol.upper()}")
            logger.info(f"📡 Data stream active: Listening to trades and depth...")
            return True
        except websockets.exceptions.ConnectionClosed as e:
            logger.error(f"WebSocket connection closed (Code: {e.code}): {e.reason}")
            self.is_connected = False
            return False
        except Exception as e:
            logger.error(f"WebSocket connection failed: {e}")
            self.is_connected = False
            return False
    
    async def disconnect(self):
        """Disconnect from WebSocket."""
        if self.ws is not None:
            await self.ws.close()
            self.ws = None
            self.is_connected = False
            logger.info("WebSocket disconnected")
    
    def register_callback(self, event: str, callback: Callable):
        """
        Register callback for event.
        
        Args:
            event: Event name ('trade', 'order_book', 'ticker')
            callback: Callback function
        """
        self.callbacks[event] = callback
    
    async def handle_message(self, message: str):
        """Handle incoming WebSocket message."""
        try:
            data = json.loads(message)
            
            # Handle stream data
            if 'data' in data:
                stream_data = data['data']
                stream_name = data.get('stream', '').lower()
                
                if 'trade' in stream_name:
                    # logger.debug(f"📥 Received trade from {stream_name}") # Silenced per user request
                    await self._handle_trade(stream_data)
                elif 'depth' in stream_name:
                    # logger.debug(f"📥 Received depth from {stream_name}") # Silenced per user request
                    await self._handle_order_book(stream_data)
                else:
                    logger.debug(f"❓ Unknown stream: {stream_name}")
                    
        except Exception as e:
            logger.error(f"Error handling message: {e}")
    
    async def _handle_trade(self, trade_data: Dict):
        """Handle trade data."""
        trade = {
            'id': trade_data.get('a') or trade_data.get('t'),
            'price': float(trade_data.get('p', 0)),
            'volume': float(trade_data.get('q', 0)),
            'time': trade_data.get('T'),
            'is_buyer_maker': trade_data.get('m', False),  # True = sell, False = buy
        }
        
        # Add to buffer
        self.trade_buffer.append(trade)
        
        # Keep only last 1000 trades
        if len(self.trade_buffer) > 1000:
            self.trade_buffer = list(self.trade_buffer)[-1000:]
        
        # Call callback
        if 'trade' in self.callbacks:
            await self.callbacks['trade'](trade)
    
    async def _handle_order_book(self, order_book_data: Dict):
        """Handle order book data."""
        bids = {}
        asks = {}
        
        for price, volume in order_book_data.get('b', []):
            bids[float(price)] = float(volume)
        
        for price, volume in order_book_data.get('a', []):
            asks[float(price)] = float(volume)
        
        self.order_book_buffer = {'bids': bids, 'asks': asks}
        
        # Call callback
        if 'order_book' in self.callbacks:
            await self.callbacks['order_book'](self.order_book_buffer)
    
    async def listen(self):
        """Start listening to WebSocket."""
        logger.debug("👂 listen() entered")
        try:
            if self.ws is not None:
                logger.debug("🔊 Starting message loop...")
                async for message in self.ws:
                    await self.handle_message(str(message))
            else:
                logger.error("❌ listen() called but self.ws is None")
        except websockets.exceptions.ConnectionClosed as e:
            logger.warning(f"WebSocket connection closed (Code: {e.code}): {e.reason}")
        except Exception as e:
            logger.error(f"WebSocket execution error: {e}")
        finally:
            self.is_connected = False
            if self.ws:
                await self.ws.close()
                self.ws = None
    
    async def start(self):
        """Start WebSocket in background with auto-reconnection."""
        logger.info(f"Starting WebSocket manager for {self.symbol.upper()}...")
        while True:
            try:
                if not self.is_connected:
                    success = await self.connect()
                    if not success:
                        await asyncio.sleep(10)
                        continue
                
                await self.listen()
                
            except Exception as e:
                logger.error(f"Unexpected error in WebSocket loop: {e}")
                self.is_connected = False
                await asyncio.sleep(5)
    
    def get_recent_trades(self, count: int = 100) -> List[Dict]:
        """Get recent trades from buffer."""
        return list(self.trade_buffer)[-count:]
    
    def get_order_book(self) -> Dict:
        """Get current order book from buffer."""
        return self.order_book_buffer
    
    def calculate_delta(self, count: int = 50) -> float:
        """
        Calculate delta from recent trades.
        
        Args:
            count: Number of trades to calculate
        
        Returns:
            Delta (positive = buying, negative = selling)
        """
        trades = self.get_recent_trades(count)
        
        buy_volume = sum(t['volume'] for t in trades if not t['is_buyer_maker'])
        sell_volume = sum(t['volume'] for t in trades if t['is_buyer_maker'])
        
        return buy_volume - sell_volume
    
    def calculate_cumulative_delta(self, count: int = 100) -> List[float]:
        """
        Calculate cumulative delta over time.
        
        Args:
            count: Number of trades
        
        Returns:
            List of cumulative delta values
        """
        trades = self.get_recent_trades(count)
        
        cumulative = []
        delta = 0
        
        for trade in trades:
            if trade['is_buyer_maker']:
                delta -= trade['volume']
            else:
                delta += trade['volume']
            cumulative.append(delta)
        
        return cumulative
    
    def get_imbalance(self) -> float:
        """
        Calculate order book imbalance.
        
        Returns:
            Imbalance ratio (bid_vol / ask_vol)
        """
        ob = self.order_book_buffer
        
        bid_vol = sum(ob['bids'].values())
        ask_vol = sum(ob['asks'].values())
        
        if ask_vol == 0:
            return 10.0
        
        return bid_vol / ask_vol


class WebSocketManager:
    """Manager for multiple WebSocket connections."""
    
    def __init__(self):
        self.connections: Dict[str, WebSocketHandler] = {}
    
    def add_connection(self, name: str, symbol: str):
        """Add a new WebSocket connection."""
        self.connections[name] = WebSocketHandler(symbol)
    
    def get_connection(self, name: str) -> Optional[WebSocketHandler]:
        """Get WebSocket connection by name."""
        return self.connections.get(name)
    
    async def start_all(self):
        """Start all WebSocket connections."""
        tasks = []
        for ws in self.connections.values():
            tasks.append(asyncio.create_task(ws.start()))
        
        await asyncio.gather(*tasks)
