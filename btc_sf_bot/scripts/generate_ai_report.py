#!/usr/bin/env python
"""
Generate AI Analytics Report
Usage:
    python -m btc_sf_bot.scripts.generate_ai_report --days 30
    python -m btc_sf_bot.scripts.generate_ai_report --export
    python -m btc_sf_bot.scripts.generate_ai_report --prompt
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import argparse
from datetime import datetime


def main():
    parser = argparse.ArgumentParser(
        description='Generate AI analytics report from trade records'
    )
    parser.add_argument(
        '--days', 
        type=int, 
        default=30, 
        help='Number of days to analyze (default: 30)'
    )
    parser.add_argument(
        '--export', 
        action='store_true', 
        help='Export report to data/stats/ai_report_YYYYMMDD.json'
    )
    parser.add_argument(
        '--prompt', 
        action='store_true', 
        help='Generate AI prompt with report data'
    )
    args = parser.parse_args()
    
    from src.data.ai_report_generator import AIReportGenerator
    
    print(f"Generating AI analytics report for last {args.days} days...")
    print("-" * 50)
    
    generator = AIReportGenerator()
    report = generator.generate_report(days=args.days)
    
    if 'error' in report:
        print(f"Error: {report['error']}")
        return
    
    if args.export:
        path = generator.export_to_file(report)
        print(f"\n✅ Report exported to: {path}")
        
    if args.prompt:
        print("\n" + "=" * 50)
        print("AI PROMPT:")
        print("=" * 50)
        print(generator.get_ai_prompt(report))
    else:
        print("\n📊 MODE PERFORMANCE:")
        print("-" * 30)
        for mode, stats in report.get('mode_performance', {}).items():
            print(f"  {mode}:")
            print(f"    Trades: {stats['trades']}")
            print(f"    Win Rate: {stats['win_rate']}%")
            print(f"    Avg RR: {stats['avg_rr']}")
            print(f"    Avg Score: {stats['avg_score']}")
            print()
        
        print("\n📈 SESSION PERFORMANCE:")
        print("-" * 30)
        for session, stats in report.get('session_performance', {}).items():
            print(f"  {session}: {stats['win_rate']}% win rate ({stats['trades']} trades)")
        
        if report.get('score_vs_winrate'):
            print("\n🎯 SCORE vs WIN RATE:")
            print("-" * 30)
            for bucket, stats in report['score_vs_winrate'].items():
                print(f"  Score {bucket}: {stats['win_rate']}% win rate ({stats['trades']} trades)")
        
        print(f"\n📋 Full report has {len(json.dumps(report))} characters")
        print(f"   Use --export to save to file")
        print(f"   Use --prompt to get AI analysis prompt")


if __name__ == "__main__":
    main()
