"""
Performance Analyzer
"""
from typing import Dict, List, Optional
from datetime import datetime
import pandas as pd
import numpy as np

from ..utils.logger import get_logger

logger = get_logger(__name__)


class PerformanceAnalyzer:
    """Analyze backtest performance."""
    
    def __init__(self, results: Dict):
        """
        Initialize analyzer with backtest results.
        
        Args:
            results: Dictionary from BacktestEngine.run()
        """
        self.results = results
        self.summary = results.get('summary', {})
        self.trades = results.get('trades', [])
        self.equity_curve = results.get('equity_curve', [])
        self.score_analysis = results.get('score_analysis', {})
    
    def print_summary(self):
        """Print performance summary."""
        if not self.summary:
            print("No results to analyze")
            return
        
        print("\n" + "="*60)
        print("               BACKTEST RESULTS SUMMARY")
        print("="*60)
        
        print(f"\n📊 TRADE STATISTICS")
        print(f"   Total Trades:      {self.summary.get('total_trades', 0)}")
        print(f"   Winning Trades:    {self.summary.get('winning_trades', 0)}")
        print(f"   Losing Trades:     {self.summary.get('losing_trades', 0)}")
        print(f"   Win Rate:          {self.summary.get('win_rate', 0):.2f}%")
        
        print(f"\n💰 PROFIT/LOSS")
        print(f"   Total PnL:         ${self.summary.get('total_pnl', 0):,.2f}")
        print(f"   Final Balance:     ${self.summary.get('final_balance', 0):,.2f}")
        print(f"   Return:            {self.summary.get('return_pct', 0):.2f}%")
        print(f"   Avg Win:           ${self.summary.get('avg_win', 0):,.2f}")
        print(f"   Avg Loss:          ${self.summary.get('avg_loss', 0):,.2f}")
        
        print(f"\n📈 RISK METRICS")
        print(f"   Profit Factor:     {self.summary.get('profit_factor', 0):.2f}")
        print(f"   Expectancy:        ${self.summary.get('expectancy', 0):,.2f}")
        print(f"   Sharpe Ratio:      {self.summary.get('sharpe_ratio', 0):.2f}")
        print(f"   Max Drawdown:      {self.summary.get('max_drawdown', 0):.2f}%")
        
        if self.score_analysis:
            print(f"\n🎯 SCORE ANALYSIS")
            print(f"   {'Score':<8} {'Trades':<8} {'Wins':<8} {'Losses':<8} {'Win Rate':<10}")
            print(f"   {'-'*42}")
            for score in sorted(self.score_analysis.keys()):
                data = self.score_analysis[score]
                print(f"   {score:<8} {data['total']:<8} {data['wins']:<8} {data['losses']:<8} {data['win_rate']:.1f}%")
        
        print("\n" + "="*60)
    
    def get_monthly_returns(self) -> pd.DataFrame:
        """Calculate monthly returns."""
        if not self.trades:
            return pd.DataFrame()
        
        df = pd.DataFrame(self.trades)
        df['exit_time'] = pd.to_datetime(df['exit_time'])
        df['month'] = df['exit_time'].dt.to_period('M')
        
        monthly = df.groupby('month').agg({
            'pnl': 'sum',
            'direction': 'count'
        }).rename(columns={'direction': 'trades'})
        
        monthly['cumulative_pnl'] = monthly['pnl'].cumsum()
        
        return monthly
    
    def get_daily_returns(self) -> pd.DataFrame:
        """Calculate daily returns."""
        if not self.trades:
            return pd.DataFrame()
        
        df = pd.DataFrame(self.trades)
        df['exit_time'] = pd.to_datetime(df['exit_time'])
        df['date'] = df['exit_time'].dt.date
        
        daily = df.groupby('date').agg({
            'pnl': 'sum',
            'direction': 'count'
        }).rename(columns={'direction': 'trades'})
        
        daily['cumulative_pnl'] = daily['pnl'].cumsum()
        
        return daily
    
    def get_hourly_distribution(self) -> pd.DataFrame:
        """Get trade distribution by hour."""
        if not self.trades:
            return pd.DataFrame()
        
        df = pd.DataFrame(self.trades)
        df['exit_time'] = pd.to_datetime(df['exit_time'])
        df['hour'] = df['exit_time'].dt.hour
        
        hourly = df.groupby('hour').agg({
            'pnl': ['sum', 'mean', 'count']
        })
        hourly.columns = ['total_pnl', 'avg_pnl', 'trades']
        
        winning = df[df['pnl'] > 0].groupby('hour').size()
        hourly['wins'] = winning
        hourly['win_rate'] = (hourly['wins'] / hourly['trades'] * 100).fillna(0)
        
        return hourly
    
    def get_direction_analysis(self) -> Dict:
        """Analyze performance by trade direction."""
        if not self.trades:
            return {}
        
        df = pd.DataFrame(self.trades)
        
        longs = df[df['direction'] == 'LONG']
        shorts = df[df['direction'] == 'SHORT']
        
        def analyze_direction(trades_df, name):
            if trades_df.empty:
                return None
            
            wins = trades_df[trades_df['pnl'] > 0]
            losses = trades_df[trades_df['pnl'] <= 0]
            
            return {
                'name': name,
                'total': len(trades_df),
                'wins': len(wins),
                'losses': len(losses),
                'win_rate': (len(wins) / len(trades_df) * 100) if len(trades_df) > 0 else 0,
                'total_pnl': trades_df['pnl'].sum(),
                'avg_pnl': trades_df['pnl'].mean(),
                'avg_win': wins['pnl'].mean() if not wins.empty else 0,
                'avg_loss': losses['pnl'].mean() if not losses.empty else 0
            }
        
        return {
            'LONG': analyze_direction(longs, 'LONG'),
            'SHORT': analyze_direction(shorts, 'SHORT')
        }
    
    def get_exit_reason_analysis(self) -> pd.DataFrame:
        """Analyze performance by exit reason."""
        if not self.trades:
            return pd.DataFrame()
        
        df = pd.DataFrame(self.trades)
        
        exit_analysis = df.groupby('exit_reason').agg({
            'pnl': ['sum', 'mean', 'count']
        })
        exit_analysis.columns = ['total_pnl', 'avg_pnl', 'count']
        
        return exit_analysis.sort_values('count', ascending=False)
    
    def get_risk_reward_analysis(self) -> Dict:
        """Analyze actual vs expected R:R."""
        if not self.trades:
            return {}
        
        df = pd.DataFrame(self.trades)
        
        positive_rr = df[df['rr_ratio'] > 0]
        negative_rr = df[df['rr_ratio'] <= 0]
        
        return {
            'avg_rr': df['rr_ratio'].mean(),
            'median_rr': df['rr_ratio'].median(),
            'best_rr': df['rr_ratio'].max(),
            'worst_rr': df['rr_ratio'].min(),
            'rr_above_1': len(df[df['rr_ratio'] >= 1]),
            'rr_above_2': len(df[df['rr_ratio'] >= 2]),
            'rr_above_3': len(df[df['rr_ratio'] >= 3])
        }
    
    def get_drawdown_analysis(self) -> Dict:
        """Analyze drawdown periods."""
        if not self.equity_curve:
            return {}
        
        df = pd.DataFrame(self.equity_curve)
        df['cummax'] = df['equity'].cummax()
        df['drawdown'] = (df['equity'] - df['cummax']) / df['cummax'] * 100
        df['in_drawdown'] = df['drawdown'] < 0
        
        drawdown_periods = []
        current_dd_start = None
        
        for i, row in df.iterrows():
            if row['in_drawdown'] and current_dd_start is None:
                current_dd_start = row['time']
            elif not row['in_drawdown'] and current_dd_start is not None:
                drawdown_periods.append({
                    'start': current_dd_start,
                    'end': row['time'],
                    'duration_bars': i - df[df['time'] == current_dd_start].index[0] if current_dd_start in df['time'].values else 0
                })
                current_dd_start = None
        
        return {
            'max_drawdown': df['drawdown'].min(),
            'avg_drawdown': df[df['in_drawdown']]['drawdown'].mean() if df['in_drawdown'].any() else 0,
            'drawdown_periods': len(drawdown_periods),
            'max_drawdown_duration': max([p['duration_bars'] for p in drawdown_periods]) if drawdown_periods else 0
        }
    
    def get_streak_analysis(self) -> Dict:
        """Analyze winning/losing streaks."""
        if not self.trades:
            return {}
        
        df = pd.DataFrame(self.trades)
        df['is_win'] = df['pnl'] > 0
        
        streaks = []
        current_streak = 0
        current_type = None
        
        for is_win in df['is_win']:
            if current_type is None:
                current_type = is_win
                current_streak = 1
            elif is_win == current_type:
                current_streak += 1
            else:
                streaks.append({
                    'type': 'WIN' if current_type else 'LOSS',
                    'length': current_streak
                })
                current_type = is_win
                current_streak = 1
        
        if current_streak > 0:
            streaks.append({
                'type': 'WIN' if current_type else 'LOSS',
                'length': current_streak
            })
        
        win_streaks = [s for s in streaks if s['type'] == 'WIN']
        loss_streaks = [s for s in streaks if s['type'] == 'LOSS']
        
        return {
            'max_win_streak': max([s['length'] for s in win_streaks]) if win_streaks else 0,
            'max_loss_streak': max([s['length'] for s in loss_streaks]) if loss_streaks else 0,
            'avg_win_streak': np.mean([s['length'] for s in win_streaks]) if win_streaks else 0,
            'avg_loss_streak': np.mean([s['length'] for s in loss_streaks]) if loss_streaks else 0,
            'total_streaks': len(streaks)
        }
    
    def get_confluence_analysis(self) -> Dict:
        """Analyze which confluence factors perform best."""
        if not self.trades:
            return {}
        
        df = pd.DataFrame(self.trades)
        
        factor_performance = {}
        
        for idx, trade in df.iterrows():
            reason = trade.get('reason', '')
            if not reason:
                continue
            
            factors = reason.split('.')
            pnl = trade['pnl']
            
            for factor in factors:
                if factor not in factor_performance:
                    factor_performance[factor] = {
                        'trades': 0,
                        'wins': 0,
                        'total_pnl': 0
                    }
                factor_performance[factor]['trades'] += 1
                factor_performance[factor]['total_pnl'] += pnl
                if pnl > 0:
                    factor_performance[factor]['wins'] += 1
        
        for factor in factor_performance:
            data = factor_performance[factor]
            data['win_rate'] = (data['wins'] / data['trades'] * 100) if data['trades'] > 0 else 0
            data['avg_pnl'] = data['total_pnl'] / data['trades'] if data['trades'] > 0 else 0
        
        return factor_performance
    
    def generate_full_report(self) -> str:
        """Generate a comprehensive text report."""
        report = []
        report.append("\n" + "="*70)
        report.append("              COMPREHENSIVE BACKTEST REPORT")
        report.append("="*70)
        
        self.summary.get('total_trades', 0)
        
        report.append("\n📊 OVERALL PERFORMANCE")
        report.append("-"*40)
        report.append(f"Total Trades:      {self.summary.get('total_trades', 0)}")
        report.append(f"Win Rate:          {self.summary.get('win_rate', 0):.2f}%")
        report.append(f"Profit Factor:     {self.summary.get('profit_factor', 0):.2f}")
        report.append(f"Total PnL:         ${self.summary.get('total_pnl', 0):,.2f}")
        report.append(f"Return:            {self.summary.get('return_pct', 0):.2f}%")
        report.append(f"Max Drawdown:      {self.summary.get('max_drawdown', 0):.2f}%")
        
        direction = self.get_direction_analysis()
        if direction.get('LONG') or direction.get('SHORT'):
            report.append("\n📈 DIRECTION ANALYSIS")
            report.append("-"*40)
            for dir_name, data in direction.items():
                if data:
                    report.append(f"\n{dir_name}:")
                    report.append(f"  Trades: {data['total']} | Win Rate: {data['win_rate']:.1f}%")
                    report.append(f"  Total PnL: ${data['total_pnl']:,.2f} | Avg: ${data['avg_pnl']:,.2f}")
        
        rr = self.get_risk_reward_analysis()
        if rr:
            report.append("\n⚖️ RISK/REWARD ANALYSIS")
            report.append("-"*40)
            report.append(f"Average R:R:       {rr.get('avg_rr', 0):.2f}")
            report.append(f"Median R:R:        {rr.get('median_rr', 0):.2f}")
            report.append(f"Trades >= 1R:      {rr.get('rr_above_1', 0)}")
            report.append(f"Trades >= 2R:      {rr.get('rr_above_2', 0)}")
            report.append(f"Trades >= 3R:      {rr.get('rr_above_3', 0)}")
        
        streaks = self.get_streak_analysis()
        if streaks:
            report.append("\n🔥 STREAK ANALYSIS")
            report.append("-"*40)
            report.append(f"Max Win Streak:    {streaks.get('max_win_streak', 0)}")
            report.append(f"Max Loss Streak:   {streaks.get('max_loss_streak', 0)}")
            report.append(f"Avg Win Streak:    {streaks.get('avg_win_streak', 0):.1f}")
            report.append(f"Avg Loss Streak:   {streaks.get('avg_loss_streak', 0):.1f}")
        
        if self.score_analysis:
            report.append("\n🎯 SCORE DISTRIBUTION")
            report.append("-"*40)
            report.append(f"{'Score':<8} {'Trades':<8} {'Win Rate':<12} {'Total PnL':<12}")
            for score in sorted(self.score_analysis.keys()):
                data = self.score_analysis[score]
                report.append(f"{score:<8} {data['total']:<8} {data['win_rate']:.1f}%{'':<6} ${data.get('total_pnl', 0):,.2f}")
        
        report.append("\n" + "="*70)
        
        return "\n".join(report)
    
    def export_to_csv(self, filepath: str):
        """Export trades to CSV."""
        if not self.trades:
            logger.warning("No trades to export")
            return
        
        df = pd.DataFrame(self.trades)
        df.to_csv(filepath, index=False)
        logger.info(f"Exported {len(df)} trades to {filepath}")
    
    def export_equity_to_csv(self, filepath: str):
        """Export equity curve to CSV."""
        if not self.equity_curve:
            logger.warning("No equity curve to export")
            return
        
        df = pd.DataFrame(self.equity_curve)
        df.to_csv(filepath, index=False)
        logger.info(f"Exported equity curve to {filepath}")
