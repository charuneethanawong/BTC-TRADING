#!/usr/bin/env python3
"""
Backtest Runner Script

Usage:
    python run_backtest.py --days 30
    python run_backtest.py --start 2024-01-01 --end 2024-02-01
    python run_backtest.py --days 7 --min-score 4
"""
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.backtest.data_loader import BacktestDataLoader
from src.backtest.backtest_engine import BacktestEngine
from src.backtest.performance_analyzer import PerformanceAnalyzer
from src.utils.logger import setup_logger, get_logger

logger = get_logger(__name__)


def run_backtest(
    days: int = 30,
    start_date: str = None,
    end_date: str = None,
    min_score: int = 3,
    initial_balance: float = 10000,
    risk_per_trade: float = 0.5,
    output_dir: str = "backtest_results",
    verbose: bool = True
):
    """
    Run backtest with specified parameters.
    
    Args:
        days: Number of days to backtest
        start_date: Start date (YYYY-MM-DD)
        end_date: End date (YYYY-MM-DD)
        min_score: Minimum score threshold
        initial_balance: Starting balance
        risk_per_trade: Risk per trade (%)
        output_dir: Directory for output files
        verbose: Print progress
    """
    print("\n" + "="*60)
    print("           BTC SMART FLOW BOT - BACKTEST")
    print("="*60)
    
    print(f"\n📋 Parameters:")
    print(f"   Days: {days}")
    print(f"   Start Date: {start_date or 'Auto'}")
    print(f"   End Date: {end_date or 'Auto'}")
    print(f"   Min Score: {min_score}")
    print(f"   Initial Balance: ${initial_balance:,.2f}")
    print(f"   Risk Per Trade: {risk_per_trade}%")
    
    print(f"\n📥 Loading data...")
    loader = BacktestDataLoader(symbol='BTC/USDT:USDT', timeframe='5m')
    
    try:
        data, htf_data = loader.prepare_backtest_data(
            days=days,
            start_date=start_date,
            end_date=end_date,
            load_htf=True
        )
    except Exception as e:
        print(f"❌ Error loading data: {e}")
        return None
    
    print(f"   Loaded {len(data)} candles")
    if htf_data is not None:
        print(f"   Loaded {len(htf_data)} HTF candles")
    
    print(f"\n⚙️ Configuring backtest engine...")
    
    config_path = Path(__file__).parent.parent / "config" / "config.yaml"
    
    engine = BacktestEngine(
        config_path=str(config_path) if config_path.exists() else None,
        initial_balance=initial_balance,
        risk_per_trade=risk_per_trade
    )
    
    engine.min_score = min_score
    
    print(f"\n🚀 Running backtest...")
    print("-"*60)
    
    results = engine.run(data, htf_data, verbose=verbose)
    
    print("-"*60)
    
    print(f"\n📊 Analyzing results...")
    analyzer = PerformanceAnalyzer(results)
    analyzer.print_summary()
    
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    results_file = output_path / f"backtest_{timestamp}.json"
    with open(results_file, 'w') as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\n💾 Results saved to: {results_file}")
    
    trades_file = output_path / f"trades_{timestamp}.csv"
    analyzer.export_to_csv(str(trades_file))
    
    equity_file = output_path / f"equity_{timestamp}.csv"
    analyzer.export_equity_to_csv(str(equity_file))
    
    report = analyzer.generate_full_report()
    report_file = output_path / f"report_{timestamp}.txt"
    with open(report_file, 'w') as f:
        f.write(report)
    print(f"📄 Report saved to: {report_file}")
    
    print(f"\n🎯 KEY FINDINGS:")
    print("-"*40)
    
    summary = results.get('summary', {})
    
    if summary.get('total_trades', 0) > 0:
        win_rate = summary.get('win_rate', 0)
        profit_factor = summary.get('profit_factor', 0)
        
        if win_rate >= 50 and profit_factor >= 1.5:
            print("✅ Strategy looks PROMISING")
        elif win_rate >= 45 or profit_factor >= 1.2:
            print("⚠️ Strategy needs OPTIMIZATION")
        else:
            print("❌ Strategy needs MAJOR REVISION")
        
        print(f"\n   Win Rate: {win_rate:.1f}% (Target: 50%+)")
        print(f"   Profit Factor: {profit_factor:.2f} (Target: 1.5+)")
        print(f"   Total Return: {summary.get('return_pct', 0):.2f}%")
        print(f"   Max Drawdown: {summary.get('max_drawdown', 0):.2f}%")
        
        score_analysis = results.get('score_analysis', {})
        if score_analysis:
            print(f"\n   📈 Score Performance:")
            for score in sorted(score_analysis.keys()):
                data = score_analysis[score]
                print(f"      Score {score}: {data['total']} trades, {data['win_rate']:.1f}% win rate")
    else:
        print("⚠️ No trades generated. Consider lowering min_score or adjusting parameters.")
    
    return results


def main():
    parser = argparse.ArgumentParser(description='Run BTC Smart Flow Bot Backtest')
    
    parser.add_argument(
        '--days', 
        type=int, 
        default=30,
        help='Number of days to backtest (default: 30)'
    )
    
    parser.add_argument(
        '--start',
        type=str,
        default=None,
        help='Start date (YYYY-MM-DD)'
    )
    
    parser.add_argument(
        '--end',
        type=str,
        default=None,
        help='End date (YYYY-MM-DD)'
    )
    
    parser.add_argument(
        '--min-score',
        type=int,
        default=3,
        help='Minimum score threshold (default: 3)'
    )
    
    parser.add_argument(
        '--balance',
        type=float,
        default=10000,
        help='Initial balance (default: 10000)'
    )
    
    parser.add_argument(
        '--risk',
        type=float,
        default=0.5,
        help='Risk per trade %% (default: 0.5)'
    )
    
    parser.add_argument(
        '--output',
        type=str,
        default='backtest_results',
        help='Output directory (default: backtest_results)'
    )
    
    parser.add_argument(
        '--quiet',
        action='store_true',
        help='Suppress progress output'
    )
    
    args = parser.parse_args()
    
    setup_logger('backtest', level='WARNING')
    
    results = run_backtest(
        days=args.days,
        start_date=args.start,
        end_date=args.end,
        min_score=args.min_score,
        initial_balance=args.balance,
        risk_per_trade=args.risk,
        output_dir=args.output,
        verbose=not args.quiet
    )
    
    return results


if __name__ == "__main__":
    main()
