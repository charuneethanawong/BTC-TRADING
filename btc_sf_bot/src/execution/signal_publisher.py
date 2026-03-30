"""
Hybrid Signal Publisher - ZeroMQ + File Fallback
Real-time communication with MT5 EA

Priority:
1. ZeroMQ (Real-time, <10ms)
2. File (Fallback, 1-2s)
"""
import json
import hashlib
import math
import os
import time
from typing import Dict, Optional, List, Tuple
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv

# v13.8: Defensive .env loading - ensure env vars are loaded before use
env_path = Path(__file__).parent.parent.parent / "config" / ".env"
load_dotenv(dotenv_path=env_path)

from ..utils.logger import get_logger

logger = get_logger(__name__)


def sanitize_payload(data: Dict) -> Dict:
    """
    Architecture Plan Section 36.1: JSON Sanitization
    
    Sanitize payload before sending via ZeroMQ to prevent EA crashes.
    Replaces NaN, Inf, and None values with safe defaults.
    
    Args:
        data: Dictionary to sanitize
        
    Returns:
        Sanitized dictionary safe for JSON serialization
    """
    if not isinstance(data, dict):
        return {}
    
    def sanitize_value(value):
        """Recursively sanitize a single value."""
        if value is None:
            return 0
        
        if isinstance(value, float):
            # Handle NaN and Inf
            if math.isnan(value) or math.isinf(value):
                return 0.0
            return value
        
        if isinstance(value, dict):
            return sanitize_payload(value)
        
        if isinstance(value, list):
            return [sanitize_value(item) for item in value]
        
        if isinstance(value, str):
            # Replace problematic strings
            if value in ('nan', 'NaN', 'inf', 'Inf', 'INF', 'None', 'null'):
                return ''
            return value
        
        # For other types (int, bool), return as-is
        return value
    
    return {k: sanitize_value(v) for k, v in data.items()}


class MT5JSONEncoder(json.JSONEncoder):
    """Custom JSON encoder that rounds floats for MT5 compatibility."""
    
    def encode(self, obj):
        def round_floats(o):
            if isinstance(o, float):
                # Round to 2 decimal places for prices, 3 for lot sizes
                if o < 1:  # Likely lot size
                    return round(o, 3)
                else:  # Likely price
                    return round(o, 2)
            elif isinstance(o, dict):
                return {k: round_floats(v) for k, v in o.items()}
            elif isinstance(o, list):
                return [round_floats(item) for item in o]
            return o
        
        return super().encode(round_floats(obj))


class ZeroMQPublisher:
    """
    ZeroMQ PUB socket for real-time signal publishing.
    """
    
    def __init__(self):
        """Initialize ZeroMQ publisher."""
        self.enabled = os.getenv('ZEROMQ_ENABLED', 'true').lower() == 'true'
        self.host = os.getenv('ZEROMQ_HOST', '127.0.0.1')
        self.port = int(os.getenv('ZEROMQ_PUBLISHER_PORT', 5555))
        
        self.context = None
        self.socket = None
        self._connected = False
        self._last_error = None
        
        if self.enabled:
            self._init_zeromq()
    
    def _init_zeromq(self):
        """Initialize ZeroMQ socket."""
        try:
            import zmq
            
            self.context = zmq.Context()
            self.socket = self.context.socket(zmq.PUB)
            
            # Set high water mark (buffer size)
            self.socket.set_hwm(10)
            
            # Bind to address
            bind_addr = f"tcp://{self.host}:{self.port}"
            self.socket.bind(bind_addr)
            self._connected = True
            
            # Allow time for subscribers to connect
            time.sleep(0.1)
            
            logger.info(f"ZeroMQ Publisher started on {bind_addr}")
            
        except ImportError:
            logger.warning("⚠️ pyzmq not installed. Run: pip install pyzmq")
            self.enabled = False
            self._connected = False
            
        except Exception as e:
            logger.error(f"❌ ZeroMQ initialization failed: {e}")
            self.enabled = False
            self._connected = False
            self._last_error = str(e)
    
    def is_connected(self) -> bool:
        """Check if ZeroMQ is connected."""
        return self.enabled and self._connected
    
    def publish(self, topic: str, message: Dict) -> Tuple[bool, str]:
        """
        Publish message to topic.
        
        Architecture Plan Section 36.1: JSON Sanitization
        Sanitize payload before sending to prevent EA crashes.
        
        Args:
            topic: Topic name (e.g., 'signal')
            message: Message dictionary
        
        Returns:
            Tuple of (success, message)
        """
        if not self.is_connected():
            return False, "ZeroMQ not connected"
        
        try:
            # Section 36.1: Sanitize payload - replace NaN, Inf, None with safe values
            sanitized_message = sanitize_payload(message)
            
            # Serialize message with MT5-compatible float rounding
            json_msg = json.dumps(sanitized_message, cls=MT5JSONEncoder, default=str)
            
            # Send with topic
            self.socket.send_string(f"{topic} {json_msg}")
            
            return True, f"Published to {topic}"
            
        except Exception as e:
            self._last_error = str(e)
            logger.error(f"ZeroMQ publish error: {e}")
            return False, str(e)
    
    def close(self):
        """Close ZeroMQ socket."""
        try:
            if self.socket:
                self.socket.close(linger=0)
            if self.context:
                self.context.term()
        except:
            pass
        
        self._connected = False
        logger.info("ZeroMQ Publisher closed")


class FilePublisher:
    """
    File-based signal publisher (fallback).
    """
    
    def __init__(self):
        """Initialize file publisher."""
        file_path = os.getenv('MT5_SIGNAL_FILE', '')
        logger.debug(f"MT5_SIGNAL_FILE env var: '{file_path}'")
        
        # Default to signal.json in current directory if not set
        if not file_path:
            file_path = 'signal.json'
            logger.warning("MT5_SIGNAL_FILE not set, using default: signal.json")
        
        # Ensure absolute path
        if not os.path.isabs(file_path):
            abs_path = str(Path.cwd() / file_path)
            logger.debug(f"Converting to absolute path: {file_path} -> {abs_path}")
            file_path = abs_path
        
        self.file_path = file_path
        
        # Ensure directory exists
        dir_path = os.path.dirname(self.file_path)
        logger.debug(f"Directory path: '{dir_path}'")
        
        if dir_path:
            try:
                os.makedirs(dir_path, exist_ok=True)
                logger.debug(f"Created/verified directory: {dir_path}")
            except Exception as e:
                logger.warning(f"Could not create directory {dir_path}: {e}")
        
        logger.info(f"📁 File Publisher initialized: {self.file_path}")
    
    def publish(self, message: Dict) -> Tuple[bool, str]:
        """
        Save message to file.
        
        Architecture Plan Section 36.1: JSON Sanitization
        Sanitize payload before saving to prevent EA crashes.
        
        Args:
            message: Message dictionary
        
        Returns:
            Tuple of (success, message)
        """
        try:
            # Create directory if needed
            dir_path = os.path.dirname(self.file_path)
            if dir_path:
                os.makedirs(dir_path, exist_ok=True)
            
            # Section 36.1: Sanitize payload - replace NaN, Inf, None with safe values
            sanitized_message = sanitize_payload(message)
            
            # Write to temp file first (atomic write)
            temp_path = self.file_path + '.tmp'
            
            with open(temp_path, 'w', encoding='utf-8') as f:
                json.dump(sanitized_message, f, indent=2, cls=MT5JSONEncoder, default=str)
            
            # Rename to final path (atomic on most systems)
            if os.path.exists(self.file_path):
                os.remove(self.file_path)
            os.rename(temp_path, self.file_path)
            
            return True, f"Saved to {self.file_path}"
            
        except Exception as e:
            logger.error(f"❌ ZeroMQ publishing failed: {e}")
            return False, str(e)


class ZeroMQSubscriber:
    """
    ZeroMQ SUB socket for receiving data from MT5 EA.
    """
    
    def __init__(self):
        """Initialize ZeroMQ subscriber."""
        self.enabled = os.getenv('ZEROMQ_ENABLED', 'true').lower() == 'true'
        self.host = os.getenv('ZEROMQ_HOST', '127.0.0.1')
        self.port = int(os.getenv('ZEROMQ_SUBSCRIBER_PORT', 5556)) # MT5 Publish port
        
        self.context = None
        self.socket = None
        self._connected = False
        
        if self.enabled:
            self._init_zeromq()
    
    def _init_zeromq(self):
        """Initialize ZeroMQ subscriber socket."""
        try:
            import zmq
            
            self.context = zmq.Context()
            self.socket = self.context.socket(zmq.SUB)
            
            # Connect to MT5 publisher
            connect_addr = f"tcp://{self.host}:{self.port}"
            self.socket.connect(connect_addr)
            
            # Subscribe to topics
            self.socket.setsockopt_string(zmq.SUBSCRIBE, "account_info")
            self.socket.setsockopt_string(zmq.SUBSCRIBE, "position_info")
            self.socket.setsockopt_string(zmq.SUBSCRIBE, "trade_confirm")  # v23.0: EA trade confirmation
            
            self._connected = True
            logger.info(f"📡 ZeroMQ Subscriber connected to {connect_addr}")
            
        except Exception as e:
            logger.error(f"❌ ZeroMQ subscriber failed: {e}")
            self._connected = False
    
    def receive(self, timeout_ms=10) -> Optional[Tuple[str, Dict]]:
        """
        Receive message from subscriber.
        """
        if not self._connected: return None
        
        try:
            import zmq
            if self.socket.poll(timeout_ms, zmq.POLLIN):
                message = self.socket.recv_string(zmq.NOBLOCK)
                parts = message.split(' ', 1)
                if len(parts) == 2:
                    topic, json_str = parts
                    return topic, json.loads(json_str)
        except Exception:
            pass
        return None


# Singleton instances
_publisher: Optional['HybridSignalSender'] = None
_subscriber: Optional[ZeroMQSubscriber] = None


def get_signal_sender() -> 'HybridSignalSender':
    """Get or create singleton Signal Sender."""
    global _publisher
    if _publisher is None:
        _publisher = HybridSignalSender()
    return _publisher


def get_data_subscriber() -> ZeroMQSubscriber:
    """Get or create singleton ZMQ Subscriber."""
    global _subscriber
    if _subscriber is None:
        _subscriber = ZeroMQSubscriber()
    return _subscriber


class HybridSignalSender:
    """
    Hybrid signal sender with ZeroMQ + File fallback.
    
    Priority:
    1. ZeroMQ (if available)
    2. File (always as fallback/backup)
    """
    
    def __init__(self):
        """Initialize hybrid sender."""
        self.zeromq = ZeroMQPublisher()
        self.file = FilePublisher()
        
        # Signal history (prevent duplicates)
        self.sent_signals: Dict[str, Dict] = {}
        self.max_history = 1000
        
        # Statistics
        self.stats = {
            'total': 0,
            'zeromq_success': 0,
            'zeromq_failed': 0,
            'file_success': 0,
            'file_failed': 0,
            'duplicates_skipped': 0
        }
    
    def generate_signal_id(self, signal_data: Dict) -> str:
        """
        Generate unique Signal ID from signal characteristics.
        
        Uses SHA256 hash of:
        - Direction
        - Entry price (rounded)
        - Stop Loss (rounded)
        - Take Profit (rounded)
        - Timestamp (minute precision)
        - Score
        - Setup ID (if available)
        """
        components = [
            signal_data.get('direction', ''),
            f"{signal_data.get('entry_price', 0):.2f}",
            f"{signal_data.get('stop_loss', 0):.2f}",
            f"{signal_data.get('take_profit', 0):.2f}",
            signal_data.get('timestamp', '')[:16] if signal_data.get('timestamp') else '',
            str(signal_data.get('score', 0)),
            signal_data.get('setup_id', '')
]
        
        hash_input = "|".join(str(c) for c in components)
        return hashlib.sha256(hash_input.encode()).hexdigest()[:16].upper()
    
    def is_duplicate(self, signal_id: str, max_age_seconds: int = 300) -> bool:
        """
        Check if signal is duplicate.
        
        Args:
            signal_id: Signal ID to check
            max_age_seconds: Maximum age to consider as duplicate
        
        Returns:
            True if duplicate
        """
        if signal_id not in self.sent_signals:
            return False
        
        last_sent = self.sent_signals[signal_id]
        time_diff = (datetime.now(timezone.utc) - last_sent['time']).total_seconds()
        
        return time_diff < max_age_seconds
    
    def record_sent(self, signal_id: str, signal_data: Dict):
        """Record sent signal."""
        self.sent_signals[signal_id] = {
            'time': datetime.now(timezone.utc),
            'direction': signal_data.get('direction'),
            'entry': signal_data.get('entry_price'),
            'method': signal_data.get('_delivery_method')
        }
        
        # Cleanup old entries
        if len(self.sent_signals) > self.max_history:
            oldest_keys = list(self.sent_signals.keys())[:self.max_history // 2]
            for key in oldest_keys:
                del self.sent_signals[key]
    
    def send_signal(self, signal_data: Dict) -> Dict:
        """
        Send signal using hybrid method.
        
        Args:
            signal_data: Signal dictionary
        
        Returns:
            Dictionary with delivery status
        """
        # Generate signal ID if not present
        if 'signal_id' not in signal_data:
            signal_data['signal_id'] = self.generate_signal_id(signal_data)
        
        signal_id = signal_data['signal_id']
        
        # Check for duplicate
        if self.is_duplicate(signal_id):
            logger.warning(f"⚠️ Duplicate signal skipped: {signal_id}")
            self.stats['duplicates_skipped'] += 1
            return {
                'sent': False,
                'reason': 'duplicate',
                'signal_id': signal_id
            }
        
        self.stats['total'] += 1
        
        result = {
            'sent': False,
            'signal_id': signal_id,
            'methods': {},
            'primary_method': None
        }
        
# Add delivery timestamp
        signal_data['_sent_at'] = datetime.now(timezone.utc).isoformat()
        
        # Section 36.1: Sanitize payload before sending
        sanitized_data = sanitize_payload(signal_data)
        
        # Method 1: Try ZeroMQ (if enabled)
        if self.zeromq.is_connected():
            success, msg = self.zeromq.publish('signal', sanitized_data)
            result['methods']['zeromq'] = {
                'success': success,
                'message': msg
            }
            
            if success:
                self.stats['zeromq_success'] += 1
                result['sent'] = True
                result['primary_method'] = 'zeromq'
                signal_data['_delivery_method'] = 'zeromq'
                logger.info(f"Signal {signal_id} sent via ZeroMQ")
            else:
                self.stats['zeromq_failed'] += 1
                logger.warning(f"⚠️ ZeroMQ failed: {msg}")
        
        # Method 2: Always save to file (backup)
        success, msg = self.file.publish(sanitized_data)
        result['methods']['file'] = {
            'success': success,
            'message': msg
        }
        
        if success:
            self.stats['file_success'] += 1
            if not result['sent']:
                result['sent'] = True
                result['primary_method'] = 'file'
                signal_data['_delivery_method'] = 'file'
                logger.info(f"Signal {signal_id} sent via File")
        else:
            self.stats['file_failed'] += 1
            logger.error(f"❌ File save failed: {msg}")
        
        # Record as sent
        if result['sent']:
            self.record_sent(signal_id, signal_data)
        
        return result
    
    def get_stats(self) -> Dict:
        """Get delivery statistics."""
        return {
            **self.stats,
            'zeromq_enabled': self.zeromq.enabled,
            'zeromq_connected': self.zeromq.is_connected(),
            'file_path': self.file.file_path
        }
    
    def close(self):
        """Close all connections."""
        self.zeromq.close()


# Global instance
_signal_sender: Optional[HybridSignalSender] = None


def get_signal_sender() -> HybridSignalSender:
    """Get or create global signal sender instance."""
    global _signal_sender
    if _signal_sender is None:
        _signal_sender = HybridSignalSender()
    return _signal_sender


def send_signal(signal_data: Dict) -> Dict:
    """
    Convenience function to send signal.
    
    Args:
        signal_data: Signal dictionary
    
    Returns:
        Dictionary with delivery status
    """
    sender = get_signal_sender()
    return sender.send_signal(signal_data)



