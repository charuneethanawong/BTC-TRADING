"""
Backtest Engine
"""
from typing import Dict, List, Optional, Tuple
from datetime import datetime
import pandas as pd
import numpy as np

from ..utils.logger import get_logger
from ..utils.config import get_config
from ..signals.signal_manager import SignalManager
from ..analysis.order_flow import OrderFlowAnalyzer
from ..analysis.volume_profile import VolumeProfileAnalyzer
from ..analysis.ict import ICTAnalyzer
from .data_loader import BacktestDataLoader

logger = get_logger(__name__)


class BacktestTrade:
    """Represents a single backtest trade."""
    
    def __init__(
        self,
        entry_time: datetime,
        entry_price: float,
        direction: str,
        stop_loss: float,
        take_profit: float,
        tp1: float = 0,
        tp2: float = 0,
        tp3: float = 0,
        score: int = 0,
        reason: str = "",
        metadata: Dict = None
    ):
        self.entry_time = entry_time
        self.entry_price = entry_price
        self.direction = direction
        self.stop_loss = stop_loss
        self.take_profit = take_profit
        self.tp1 = tp1 or take_profit
        self.tp2 = tp2 or take_profit
        self.tp3 = tp3 or take_profit
        self.score = score
        self.reason = reason
        self.metadata = metadata or {}
        
        self.exit_time: Optional[datetime] = None
        self.exit_price: Optional[float] = None
        self.exit_reason: str = ""
        self.pnl: float = 0
        self.pnl_pct: float = 0
        self.rr_ratio: float = 0
        self.status: str = "OPEN"
        self.hold_bars: int = 0
        self.max_favorable: float = 0
        self.max_adverse: float = 0
        self.tp1_hit: bool = False
        self.tp2_hit: bool = False
        self.tp3_hit: bool = False
    
    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return {
            'entry_time': self.entry_time.isoformat() if self.entry_time else None,
            'entry_price': self.entry_price,
            'direction': self.direction,
            'stop_loss': self.stop_loss,
            'take_profit': self.take_profit,
            'tp1': self.tp1,
            'tp2': self.tp2,
            'tp3': self.tp3,
            'exit_time': self.exit_time.isoformat() if self.exit_time else None,
            'exit_price': self.exit_price,
            'exit_reason': self.exit_reason,
            'pnl': self.pnl,
            'pnl_pct': self.pnl_pct,
            'rr_ratio': self.rr_ratio,
            'status': self.status,
            'hold_bars': self.hold_bars,
            'max_favorable': self.max_favorable,
            'max_adverse': self.max_adverse,
            'score': self.score,
            'reason': self.reason,
            'tp1_hit': self.tp1_hit,
            'tp2_hit': self.tp2_hit,
            'tp3_hit': self.tp3_hit
        }


class BacktestEngine:
    """Main backtest engine."""
    
    def __init__(
        self,
        config_path: str = None,
        initial_balance: float = 10000,
        risk_per_trade: float = 0.5,
        commission: float = 0.0004,
        slippage: float = 0.0001
    ):
        """
        Initialize backtest engine.
        
        Args:
            config_path: Path to config file
            initial_balance: Starting balance
            risk_per_trade: Risk per trade (%)
            commission: Commission rate (0.04% default)
            slippage: Slippage rate (0.01% default)
        """
        self.config = get_config(config_path)
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.risk_per_trade = risk_per_trade
        self.commission = commission
        self.slippage = slippage
        
        strategy_config = self.config.get_strategy()
        self.signal_manager = SignalManager(strategy_config)
        self.order_flow = OrderFlowAnalyzer(strategy_config.get('order_flow', {}))
        self.volume_profile = VolumeProfileAnalyzer(strategy_config.get('volume_profile', {}))
        self.ict = ICTAnalyzer(strategy_config.get('ict', {}))
        
        self.data_loader = BacktestDataLoader()
        
        self.trades: List[BacktestTrade] = []
        self.open_trades: List[BacktestTrade] = []
        self.equity_curve: List[Dict] = []
        
        self.min_score = strategy_config.get('min_score', 3)
        self.cooldown_bars = 12
        self.bars_since_last_trade = 100
        
        self.total_trades = 0
        self.winning_trades = 0
        self.losing_trades = 0
        
        self.daily_pnl: Dict[str, float] = {}
    
    def run(
        self,
        data: pd.DataFrame,
        htf_data: Optional[pd.DataFrame] = None,
        verbose: bool = True
    ) -> Dict:
        """
        Run backtest on historical data.
        
        Args:
            data: OHLCV DataFrame with synthetic order flow
            htf_data: Higher timeframe data
            verbose: Print progress
        
        Returns:
            Dictionary with backtest results
        """
        logger.info(f"Starting backtest on {len(data)} candles...")
        
        self.data = data
        self.htf_data = htf_data
        
        warmup = 50
        
        for i in range(warmup, len(data)):
            self._process_bar(i, htf_data)
            
            self._update_equity_curve(i)
            
            self.bars_since_last_trade += 1
            
            if verbose and i % 1000 == 0:
                progress = (i / len(data)) * 100
                logger.info(f"Progress: {progress:.1f}% | Balance: ${self.balance:.2f} | Trades: {self.total_trades}")
        
        for trade in self.open_trades:
            self._close_trade(trade, data.iloc[-1]['close'], "END_OF_DATA", len(data) - 1)
        
        results = self._generate_results()
        
        logger.info(f"Backtest complete. Total trades: {self.total_trades}")
        
        return results
    
    def _process_bar(self, idx: int, htf_data: Optional[pd.DataFrame]):
        """Process a single bar."""
        current_candle = self.data.iloc[idx]
        current_price = current_candle['close']
        current_time = self.data.index[idx]
        
        for trade in self.open_trades[:]:
            self._check_trade_exit(trade, idx)
        
        if self.bars_since_last_trade < self.cooldown_bars:
            return
        
        candles = self.data.iloc[max(0, idx-100):idx+1].copy()
        
        order_book = self._generate_order_book(current_candle)
        trades = self._generate_trades(current_candle)
        
        candles_h1 = None
        if htf_data is not None:
            candles_h1 = htf_data[htf_data.index <= current_time].tail(200)
            if len(candles_h1) < 50:
                candles_h1 = None
        
        signal = self.signal_manager.generate_signal(
            candles=candles,
            order_book=order_book,
            trades=trades,
            current_price=current_price,
            avg_volume=candles['volume'].tail(20).mean() if len(candles) >= 20 else 0,
            risk_reward_ratio=2.0,
            candles_h1=candles_h1
        )
        
        if signal:
            self._open_trade(signal, current_time, idx)
    
    def _generate_order_book(self, candle: pd.Series) -> Dict:
        """Generate synthetic order book from candle data."""
        current_price = candle['close']
        volume = candle['volume']
        
        buy_pct = candle.get('buy_pct', 50) if hasattr(candle, 'get') else 50
        
        bids = {}
        asks = {}
        
        spread = current_price * 0.0002
        
        for i in range(5):
            bid_price = current_price - spread * (i + 1)
            ask_price = current_price + spread * (i + 1)
            
            bid_vol = volume * (buy_pct / 100) / (i + 1) * 2
            ask_vol = volume * ((100 - buy_pct) / 100) / (i + 1) * 2
            
            bids[bid_price] = bid_vol
            asks[ask_price] = ask_vol
        
        return {'bids': bids, 'asks': asks}
    
    def _generate_trades(self, candle: pd.Series) -> List[Dict]:
        """Generate synthetic trades from candle data."""
        delta = candle.get('delta', 0) if hasattr(candle, 'get') else 0
        volume = candle['volume']
        current_price = candle['close']
        
        trades = []
        
        buy_vol = (volume + delta) / 2
        sell_vol = (volume - delta) / 2
        
        num_trades = max(5, int(volume / 100))
        
        for i in range(num_trades):
            is_buy = np.random.random() < (buy_vol / volume) if volume > 0 else True
            
            trades.append({
                'price': current_price,
                'volume': volume / num_trades,
                'is_buyer_maker': not is_buy,
                'time': candle.name
            })
        
        return trades
    
    def _open_trade(self, signal, current_time: datetime, idx: int):
        """Open a new trade from signal."""
        slippage_amount = signal.entry_price * self.slippage
        if signal.direction == 'LONG':
            entry_price = signal.entry_price + slippage_amount
        else:
            entry_price = signal.entry_price - slippage_amount
        
        trade = BacktestTrade(
            entry_time=current_time,
            entry_price=entry_price,
            direction=signal.direction,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            tp1=signal.tp1,
            tp2=signal.tp2,
            tp3=signal.tp3,
            score=signal.metadata.get('score', 0),
            reason=signal.reason,
            metadata=signal.metadata
        )
        
        commission_cost = self.balance * self.commission
        self.balance -= commission_cost
        
        self.open_trades.append(trade)
        self.trades.append(trade)
        self.total_trades += 1
        self.bars_since_last_trade = 0
        
        logger.debug(f"Opened {signal.direction} @ {entry_price:.2f} | SL: {signal.stop_loss:.2f} | TP: {signal.take_profit:.2f} | Score: {trade.score}")
    
    def _check_trade_exit(self, trade: BacktestTrade, idx: int):
        """Check if trade should be closed."""
        candle = self.data.iloc[idx]
        high = candle['high']
        low = candle['low']
        close = candle['close']
        
        trade.hold_bars += 1
        
        if trade.direction == 'LONG':
            trade.max_favorable = max(trade.max_favorable, high - trade.entry_price)
            trade.max_adverse = max(trade.max_adverse, trade.entry_price - low)
            
            if low <= trade.stop_loss:
                exit_price = trade.stop_loss - (trade.stop_loss * self.slippage)
                self._close_trade(trade, exit_price, "STOP_LOSS", idx)
                return
            
            if high >= trade.take_profit:
                exit_price = trade.take_profit + (trade.take_profit * self.slippage)
                self._close_trade(trade, exit_price, "TAKE_PROFIT", idx)
                return
            
            if high >= trade.tp1 and not trade.tp1_hit:
                trade.tp1_hit = True
            
            if high >= trade.tp2 and not trade.tp2_hit:
                trade.tp2_hit = True
            
            if high >= trade.tp3 and not trade.tp3_hit:
                trade.tp3_hit = True
        
        else:  # SHORT
            trade.max_favorable = max(trade.max_favorable, trade.entry_price - low)
            trade.max_adverse = max(trade.max_adverse, high - trade.entry_price)
            
            if high >= trade.stop_loss:
                exit_price = trade.stop_loss + (trade.stop_loss * self.slippage)
                self._close_trade(trade, exit_price, "STOP_LOSS", idx)
                return
            
            if low <= trade.take_profit:
                exit_price = trade.take_profit - (trade.take_profit * self.slippage)
                self._close_trade(trade, exit_price, "TAKE_PROFIT", idx)
                return
            
            if low <= trade.tp1 and not trade.tp1_hit:
                trade.tp1_hit = True
            
            if low <= trade.tp2 and not trade.tp2_hit:
                trade.tp2_hit = True
            
            if low <= trade.tp3 and not trade.tp3_hit:
                trade.tp3_hit = True
    
    def _close_trade(self, trade: BacktestTrade, exit_price: float, reason: str, idx: int):
        """Close a trade."""
        trade.exit_price = exit_price
        trade.exit_time = self.data.index[idx]
        trade.exit_reason = reason
        trade.status = "CLOSED"
        
        if trade.direction == 'LONG':
            trade.pnl = exit_price - trade.entry_price
        else:
            trade.pnl = trade.entry_price - exit_price
        
        risk = abs(trade.entry_price - trade.stop_loss)
        trade.rr_ratio = trade.pnl / risk if risk > 0 else 0
        
        trade.pnl_pct = (trade.pnl / trade.entry_price) * 100
        
        self.balance += trade.pnl
        self.balance -= abs(trade.pnl) * self.commission
        
        if trade.pnl > 0:
            self.winning_trades += 1
        else:
            self.losing_trades += 1
        
        date_str = trade.exit_time.strftime('%Y-%m-%d')
        if date_str not in self.daily_pnl:
            self.daily_pnl[date_str] = 0
        self.daily_pnl[date_str] += trade.pnl
        
        self.open_trades.remove(trade)
        
        logger.debug(f"Closed {trade.direction} @ {exit_price:.2f} | PnL: ${trade.pnl:.2f} | Reason: {reason}")
    
    def _update_equity_curve(self, idx: int):
        """Update equity curve."""
        unrealized_pnl = sum(
            t.pnl if t.status == "CLOSED" else 0
            for t in self.open_trades
        )
        
        for trade in self.open_trades:
            if trade.status == "OPEN":
                current_price = self.data.iloc[idx]['close']
                if trade.direction == 'LONG':
                    unrealized_pnl += current_price - trade.entry_price
                else:
                    unrealized_pnl += trade.entry_price - current_price
        
        self.equity_curve.append({
            'time': self.data.index[idx],
            'balance': self.balance,
            'equity': self.balance + unrealized_pnl,
            'open_trades': len(self.open_trades)
        })
    
    def _generate_results(self) -> Dict:
        """Generate backtest results."""
        if not self.trades:
            return {
                'error': 'No trades generated',
                'total_trades': 0,
                'config': {
                    'min_score': self.min_score,
                    'initial_balance': self.initial_balance,
                    'risk_per_trade': self.risk_per_trade
                }
            }
        
        winning = [t for t in self.trades if t.pnl > 0]
        losing = [t for t in self.trades if t.pnl <= 0]
        
        win_rate = (len(winning) / len(self.trades) * 100) if self.trades else 0
        
        avg_win = np.mean([t.pnl for t in winning]) if winning else 0
        avg_loss = np.mean([t.pnl for t in losing]) if losing else 0
        
        profit_factor = (sum(t.pnl for t in winning) / abs(sum(t.pnl for t in losing))) if losing and sum(t.pnl for t in losing) != 0 else 0
        
        expectancy = (win_rate/100 * avg_win) + ((1 - win_rate/100) * avg_loss)
        
        score_distribution = {}
        for trade in self.trades:
            score = trade.score
            if score not in score_distribution:
                score_distribution[score] = {'wins': 0, 'losses': 0, 'total': 0}
            score_distribution[score]['total'] += 1
            if trade.pnl > 0:
                score_distribution[score]['wins'] += 1
            else:
                score_distribution[score]['losses'] += 1
        
        for score in score_distribution:
            total = score_distribution[score]['total']
            wins = score_distribution[score]['wins']
            score_distribution[score]['win_rate'] = (wins / total * 100) if total > 0 else 0
        
        equity_df = pd.DataFrame(self.equity_curve)
        if not equity_df.empty:
            equity_df['returns'] = equity_df['equity'].pct_change()
            sharpe = (equity_df['returns'].mean() / equity_df['returns'].std() * np.sqrt(252 * 288)) if equity_df['returns'].std() > 0 else 0
            
            equity_df['cummax'] = equity_df['equity'].cummax()
            equity_df['drawdown'] = (equity_df['equity'] - equity_df['cummax']) / equity_df['cummax']
            max_drawdown = equity_df['drawdown'].min() * 100
        else:
            sharpe = 0
            max_drawdown = 0
        
        return {
            'summary': {
                'total_trades': len(self.trades),
                'winning_trades': len(winning),
                'losing_trades': len(losing),
                'win_rate': win_rate,
                'profit_factor': profit_factor,
                'expectancy': expectancy,
                'avg_win': avg_win,
                'avg_loss': avg_loss,
                'total_pnl': sum(t.pnl for t in self.trades),
                'final_balance': self.balance,
                'return_pct': ((self.balance - self.initial_balance) / self.initial_balance) * 100,
                'sharpe_ratio': sharpe,
                'max_drawdown': max_drawdown
            },
            'score_analysis': score_distribution,
            'trades': [t.to_dict() for t in self.trades],
            'equity_curve': self.equity_curve,
            'config': {
                'min_score': self.min_score,
                'initial_balance': self.initial_balance,
                'risk_per_trade': self.risk_per_trade,
                'commission': self.commission,
                'slippage': self.slippage
            }
        }
    
    def get_trades_dataframe(self) -> pd.DataFrame:
        """Get trades as DataFrame."""
        if not self.trades:
            return pd.DataFrame()
        
        return pd.DataFrame([t.to_dict() for t in self.trades])
    
    def get_equity_dataframe(self) -> pd.DataFrame:
        """Get equity curve as DataFrame."""
        if not self.equity_curve:
            return pd.DataFrame()
        
        return pd.DataFrame(self.equity_curve)
