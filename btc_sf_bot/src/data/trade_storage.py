"""
Trade Storage Module
บันทึกทุก Trade ลงไฟล์ JSON/CSV เพื่อวิเคราะห์ย้อนหลัง
"""
import json
import csv
import os
import numpy as np
from typing import Dict, List, Optional
from datetime import datetime, timezone
from pathlib import Path

from ..utils.logger import get_logger

logger = get_logger(__name__)


def convert_to_native(obj):
    """Convert numpy types to native Python types for JSON serialization."""
    if isinstance(obj, dict):
        return {k: convert_to_native(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [convert_to_native(v) for v in obj]
    elif isinstance(obj, (np.integer, np.int64, np.int32)):
        return int(obj)
    elif isinstance(obj, (np.floating, np.float64, np.float32)):
        return float(obj)
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    return obj


class TradeStorage:
    """
    Store all trades for performance analysis
    """
    
    def __init__(self, storage_dir: str = "data/trades"):
        """
        Initialize trade storage
        
        Args:
            storage_dir: Directory to store trade data
        """
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        
        # Files
        self.trades_json = self.storage_dir / "all_trades.jsonl"
        self.trades_csv = self.storage_dir / "all_trades.csv"
        self.daily_summary = self.storage_dir / "daily_summary.json"
        
        # Initialize CSV if not exists
        self._init_csv()
        
        logger.info(f"TradeStorage initialized: {self.storage_dir}")
    
    def _init_csv(self):
        """Initialize CSV file with headers"""
        if not self.trades_csv.exists():
            headers = [
                'timestamp', 'pattern_type', 'direction', 'entry_price', 
                'stop_loss', 'take_profit', 'score', 'status', 'pnl'
            ]
            with open(self.trades_csv, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(headers)
    
    def record_trade_opened(self, trade_data: Dict):
        """
        Record when a trade is opened
        
        Args:
            trade_data: Trade information including pattern, entry, SL, TP
        """
        # Convert numpy types to native Python types
        trade_data = convert_to_native(trade_data)
        
        record = {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'type': 'OPEN',
            **trade_data
        }
        
        # Append to JSONL
        try:
            with open(self.trades_json, 'a') as f:
                f.write(json.dumps(record) + '\n')
        except Exception as e:
            logger.error(f"Error recording trade open: {e}")
        
        # Append to CSV
        try:
            with open(self.trades_csv, 'a', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    record['timestamp'],
                    trade_data.get('pattern_type', 'UNKNOWN'),
                    trade_data.get('direction', ''),
                    trade_data.get('entry_price', 0),
                    trade_data.get('stop_loss', 0),
                    trade_data.get('take_profit', 0),
                    trade_data.get('score', 0),
                    'OPEN',
                    0
                ])
        except Exception as e:
            logger.error(f"Error writing to CSV: {e}")
        
        logger.info(f"💾 Trade recorded: {trade_data.get('pattern_type')} {trade_data.get('direction')} @ {trade_data.get('entry_price')}")
    
    def record_trade_closed(self, trade_id: str, close_data: Dict):
        """
        Record when a trade is closed
        
        Args:
            trade_id: Trade identifier
            close_data: Close information including exit price, P&L
        """
        # Convert numpy types to native Python types
        close_data = convert_to_native(close_data)
        
        record = {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'type': 'CLOSE',
            'trade_id': trade_id,
            **close_data
        }
        
        # Append to JSONL
        try:
            with open(self.trades_json, 'a') as f:
                f.write(json.dumps(record) + '\n')
        except Exception as e:
            logger.error(f"Error recording trade close: {e}")
        
        logger.info(f"💾 Trade closed: {trade_id} P&L=${close_data.get('pnl', 0):.2f}")
    
    def get_trades_by_pattern(self, pattern_type: str, limit: int = 100) -> List[Dict]:
        """
        Get trades filtered by pattern type
        
        Args:
            pattern_type: 'SWEEP', 'WALL', or 'ZONE'
            limit: Maximum number of trades to return
            
        Returns:
            List of trade records
        """
        trades = []
        
        if not self.trades_json.exists():
            return trades
        
        try:
            with open(self.trades_json, 'r') as f:
                for line in f:
                    try:
                        record = json.loads(line.strip())
                        if record.get('pattern_type') == pattern_type:
                            trades.append(record)
                            if len(trades) >= limit:
                                break
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            logger.error(f"Error reading trades: {e}")
        
        return trades
    
    def get_today_trades(self) -> List[Dict]:
        """Get all trades from today"""
        today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        trades = []
        
        if not self.trades_json.exists():
            return trades
        
        try:
            with open(self.trades_json, 'r') as f:
                for line in f:
                    try:
                        record = json.loads(line.strip())
                        trade_date = record.get('timestamp', '')[:10]
                        if trade_date == today:
                            trades.append(record)
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            logger.error(f"Error reading today's trades: {e}")
        
        return trades
    
    def export_to_csv(self, output_file: str = None):
        """Export all trades to a single CSV file"""
        if output_file is None:
            output_file = self.storage_dir / f"export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        
        if not self.trades_json.exists():
            logger.warning("No trades to export")
            return
        
        try:
            with open(self.trades_json, 'r') as f_in, open(output_file, 'w', newline='') as f_out:
                # Read all records
                records = []
                for line in f_in:
                    try:
                        record = json.loads(line.strip())
                        records.append(record)
                    except json.JSONDecodeError:
                        continue
                
                # Get all unique keys
                all_keys = set()
                for record in records:
                    all_keys.update(record.keys())
                
                # Write CSV
                writer = csv.DictWriter(f_out, fieldnames=sorted(all_keys))
                writer.writeheader()
                writer.writerows(records)
            
            logger.info(f"Exported {len(records)} trades to {output_file}")
        except Exception as e:
            logger.error(f"Error exporting trades: {e}")
    
    def get_mode_performance(self) -> Dict:
        """
        Get performance statistics per mode (pattern type)
        
        Returns:
            Dict with mode as key and stats (trades, win_rate, avg_rr, avg_score) as value
        """
        if not self.trades_json.exists():
            return {}
        
        mode_stats = {}
        
        try:
            with open(self.trades_json, 'r') as f:
                for line in f:
                    try:
                        record = json.loads(line.strip())
                        if record.get('type') != 'CLOSE':
                            continue
                        
                        pattern = record.get('pattern_type', 'UNKNOWN')
                        if pattern not in mode_stats:
                            mode_stats[pattern] = {
                                'trades': 0,
                                'wins': 0,
                                'losses': 0,
                                'total_rr': 0,
                                'total_score': 0
                            }
                        
                        stats = mode_stats[pattern]
                        stats['trades'] += 1
                        
                        pnl = record.get('pnl', 0)
                        if pnl > 0:
                            stats['wins'] += 1
                        else:
                            stats['losses'] += 1
                        
                        rr = record.get('rr_actual', 0)
                        if rr:
                            stats['total_rr'] += rr
                        
                        score = record.get('score', 0)
                        if score:
                            stats['total_score'] += score
                            
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            logger.error(f"Error calculating mode performance: {e}")
        
        result = {}
        for mode, stats in mode_stats.items():
            trades = stats['trades']
            if trades > 0:
                result[mode] = {
                    'trades': trades,
                    'win_rate': round(stats['wins'] / trades * 100, 1),
                    'avg_rr': round(stats['total_rr'] / trades, 2),
                    'avg_score': round(stats['total_score'] / trades, 1)
                }
        
        return result
    
    def get_session_performance(self) -> Dict:
        """
        Get performance statistics per trading session
        
        Returns:
            Dict with session as key and stats (trades, win_rate) as value
        """
        if not self.trades_json.exists():
            return {}
        
        session_stats = {}
        
        try:
            with open(self.trades_json, 'r') as f:
                for line in f:
                    try:
                        record = json.loads(line.strip())
                        if record.get('type') != 'CLOSE':
                            continue
                        
                        session = record.get('session', 'UNKNOWN')
                        if session not in session_stats:
                            session_stats[session] = {'trades': 0, 'wins': 0}
                        
                        stats = session_stats[session]
                        stats['trades'] += 1
                        
                        if record.get('pnl', 0) > 0:
                            stats['wins'] += 1
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            logger.error(f"Error calculating session performance: {e}")
        
        result = {}
        for session, stats in session_stats.items():
            if stats['trades'] > 0:
                result[session] = {
                    'trades': stats['trades'],
                    'win_rate': round(stats['wins'] / stats['trades'] * 100, 1)
                }
        
        return result
