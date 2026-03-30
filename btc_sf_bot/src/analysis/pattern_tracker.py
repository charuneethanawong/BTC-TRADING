"""
Pattern Performance Tracker Module
เก็บสถิติและวิเคราะห์ประสิทธิภาพของแต่ละ Pattern
"""
import json
import os
from typing import Dict, List, Optional
from datetime import datetime, timezone
from pathlib import Path

from ..utils.logger import get_logger
from src.utils.decorators import log_errors, retry, circuit_breaker
from src.utils.metrics import timed_metric

logger = get_logger(__name__)


class PatternPerformanceTracker:
    """
    Track performance statistics for each trading pattern (SWEEP, WALL, ZONE)
    """
    
    def __init__(self, storage_dir: str = "data/performance"):
        """
        Initialize tracker
        
        Args:
            storage_dir: Directory to store performance data
        """
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        
        # Stats file
        self.stats_file = self.storage_dir / "pattern_stats.json"
        self.trades_file = self.storage_dir / "trade_history.json"
        
        # Initialize stats structure
        self.stats = self._load_stats()
        self.pending_trades = {}  # Track open trades
        
        logger.info(f"PatternPerformanceTracker initialized: {self.storage_dir}")
    
    def _load_stats(self) -> Dict:
        """Load existing stats or create new"""
        if self.stats_file.exists():
            try:
                with open(self.stats_file, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Error loading stats: {e}")
        
        # Default stats structure
        return {
            'SWEEP': self._create_empty_stats(),
            'WALL': self._create_empty_stats(),
            'ZONE': self._create_empty_stats(),
            'last_updated': datetime.now(timezone.utc).isoformat()
        }
    
    def _create_empty_stats(self) -> Dict:
        """Create empty stats template"""
        return {
            'total_trades': 0,
            'wins': 0,
            'losses': 0,
            'total_profit': 0.0,
            'total_loss': 0.0,
            'avg_score': 0.0,
            'max_profit': 0.0,
            'max_loss': 0.0,
            'avg_holding_time': 0,  # seconds
            'current_streak': 0,  # positive = win streak, negative = loss streak
            'max_win_streak': 0,
            'max_loss_streak': 0
        }
    
    @log_errors
    @timed_metric("PatternPerformanceTracker.record_signal")
    @retry(max_attempts=3, delay=0.1, backoff=2.0, exceptions=(Exception,))
    @circuit_breaker(failure_threshold=5, timeout=30.0, expected_exception=Exception)
    def record_signal(self, pattern_type: str, signal_data: Dict):
        """
        Record when a signal is generated
        
        Args:
            pattern_type: 'SWEEP', 'WALL', or 'ZONE'
            signal_data: Signal information
        """
        trade_id = f"{pattern_type}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{signal_data.get('score', 0)}"
        
        self.pending_trades[trade_id] = {
            'pattern_type': pattern_type,
            'signal_data': signal_data,
            'entry_time': datetime.now(timezone.utc).isoformat(),
            'status': 'OPEN'
        }
        
        logger.debug(f"Recorded {pattern_type} signal: {trade_id}")
        return trade_id
    
    def record_trade_result(self, trade_id: str, result: Dict):
        """
        Record trade result when position closes
        
        Args:
            trade_id: Trade ID from record_signal
            result: Trade result with pnl, exit_price, etc.
        """
        if trade_id not in self.pending_trades:
            logger.warning(f"Trade {trade_id} not found in pending trades")
            return
        
        trade = self.pending_trades[trade_id]
        pattern_type = trade['pattern_type']
        
        # Calculate metrics
        pnl = result.get('pnl', 0)
        exit_time = datetime.now(timezone.utc)
        entry_time = datetime.fromisoformat(trade['entry_time'])
        holding_time = (exit_time - entry_time).total_seconds()
        
        # Update stats
        stats = self.stats[pattern_type]
        stats['total_trades'] += 1
        
        if pnl > 0:
            stats['wins'] += 1
            stats['total_profit'] += pnl
            stats['max_profit'] = max(stats['max_profit'], pnl)
            stats['current_streak'] = max(1, stats['current_streak'] + 1)
            stats['max_win_streak'] = max(stats['max_win_streak'], stats['current_streak'])
        else:
            stats['losses'] += 1
            stats['total_loss'] += abs(pnl)
            stats['max_loss'] = max(stats['max_loss'], abs(pnl))
            stats['current_streak'] = min(-1, stats['current_streak'] - 1)
            stats['max_loss_streak'] = max(stats['max_loss_streak'], abs(stats['current_streak']))
        
        # Update averages
        signal_score = trade['signal_data'].get('score', 0)
        stats['avg_score'] = ((stats['avg_score'] * (stats['total_trades'] - 1)) + signal_score) / stats['total_trades']
        stats['avg_holding_time'] = ((stats['avg_holding_time'] * (stats['total_trades'] - 1)) + holding_time) / stats['total_trades']
        
        # Update timestamp
        stats['last_trade_time'] = exit_time.isoformat()
        self.stats['last_updated'] = exit_time.isoformat()
        
        # Save to file
        self._save_stats()
        self._append_trade_history(trade, result, pnl, holding_time)
        
        # Remove from pending
        del self.pending_trades[trade_id]
        
        logger.info(f"📊 {pattern_type} trade recorded: P&L=${pnl:.2f}, Total: {stats['total_trades']}")
    
    def _save_stats(self):
        """Save stats to file"""
        try:
            with open(self.stats_file, 'w') as f:
                json.dump(self.stats, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving stats: {e}")
    
    def _append_trade_history(self, trade: Dict, result: Dict, pnl: float, holding_time: float):
        """Append trade to history file"""
        history_entry = {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'pattern_type': trade['pattern_type'],
            'signal_data': trade['signal_data'],
            'result': result,
            'pnl': pnl,
            'holding_time': holding_time
        }
        
        try:
            # Append to file
            with open(self.trades_file, 'a') as f:
                f.write(json.dumps(history_entry) + '\n')
        except Exception as e:
            logger.error(f"Error saving trade history: {e}")
    
    def get_performance_report(self) -> Dict:
        """
        Generate performance report for all patterns
        
        Returns:
            Dictionary with performance metrics per pattern
        """
        report = {}
        
        for pattern in ['SWEEP', 'WALL', 'ZONE']:
            stats = self.stats[pattern]
            total = stats['total_trades']
            
            if total > 0:
                win_rate = (stats['wins'] / total) * 100
                profit_factor = stats['total_profit'] / stats['total_loss'] if stats['total_loss'] > 0 else float('inf')
                avg_profit = stats['total_profit'] / stats['wins'] if stats['wins'] > 0 else 0
                avg_loss = stats['total_loss'] / stats['losses'] if stats['losses'] > 0 else 0
                expectancy = (win_rate/100 * avg_profit) - ((100-win_rate)/100 * avg_loss)
                net_profit = stats['total_profit'] - stats['total_loss']
                
                report[pattern] = {
                    'total_trades': total,
                    'win_rate': f"{win_rate:.1f}%",
                    'profit_factor': f"{profit_factor:.2f}",
                    'net_profit': f"${net_profit:.2f}",
                    'avg_profit': f"${avg_profit:.2f}",
                    'avg_loss': f"${avg_loss:.2f}",
                    'expectancy': f"${expectancy:.2f}",
                    'avg_score': f"{stats['avg_score']:.1f}",
                    'max_profit': f"${stats['max_profit']:.2f}",
                    'max_loss': f"${stats['max_loss']:.2f}",
                    'current_streak': stats['current_streak'],
                    'avg_holding_time': f"{stats['avg_holding_time']/60:.1f}m"
                }
            else:
                report[pattern] = {
                    'total_trades': 0,
                    'status': 'No trades yet'
                }
        
        return report
    
    def get_best_pattern(self) -> Optional[str]:
        """Return the best performing pattern based on expectancy"""
        best_pattern = None
        best_expectancy = float('-inf')
        
        for pattern in ['SWEEP', 'WALL', 'ZONE']:
            stats = self.stats[pattern]
            if stats['total_trades'] >= 5:  # Minimum sample size
                win_rate = stats['wins'] / stats['total_trades']
                avg_profit = stats['total_profit'] / stats['wins'] if stats['wins'] > 0 else 0
                avg_loss = stats['total_loss'] / stats['losses'] if stats['losses'] > 0 else 0
                expectancy = (win_rate * avg_profit) - ((1-win_rate) * avg_loss)
                
                if expectancy > best_expectancy:
                    best_expectancy = expectancy
                    best_pattern = pattern
        
        return best_pattern
    
    def suggest_threshold_adjustments(self) -> Dict:
        """
        Suggest threshold adjustments based on performance
        
        Returns:
            Dictionary with suggested adjustments per pattern
        """
        suggestions = {}
        
        for pattern in ['SWEEP', 'WALL', 'ZONE']:
            stats = self.stats[pattern]
            
            if stats['total_trades'] < 10:
                suggestions[pattern] = "Insufficient data (need 10+ trades)"
                continue
            
            win_rate = stats['wins'] / stats['total_trades']
            avg_score = stats['avg_score']
            
            # Calculate expectancy
            avg_profit = stats['total_profit'] / stats['wins'] if stats['wins'] > 0 else 0
            avg_loss = stats['total_loss'] / stats['losses'] if stats['losses'] > 0 else 0
            expectancy = (win_rate * avg_profit) - ((1-win_rate) * avg_loss)
            
            if expectancy < 0:
                suggestions[pattern] = f"🔴 INCREASE threshold (losing money, Expectancy: ${expectancy:.2f})"
            elif win_rate < 0.4:
                suggestions[pattern] = f"🟡 INCREASE threshold (low win rate: {win_rate:.1%})"
            elif win_rate > 0.6 and expectancy > 20:
                suggestions[pattern] = f"🟢 DECREASE threshold (performing well, can trade more often)"
            else:
                suggestions[pattern] = f"✅ KEEP current threshold (balanced performance)"
        
        return suggestions
