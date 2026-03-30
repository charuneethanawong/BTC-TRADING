"""
AI Report Generator - สร้าง AI-ready analytics report จาก trade records
"""
import json
import os
from typing import Dict, List, Optional
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import defaultdict

from ..utils.logger import get_logger
from .trade_storage import TradeStorage

logger = get_logger(__name__)


class AIReportGenerator:
    """สร้าง AI-ready analytics report จาก trade records"""

    def __init__(self, storage: TradeStorage = None, config: dict = None):
        self.storage = storage or TradeStorage()
        self.config = config or {}
        self.stats_dir = Path("data/stats")
        self.stats_dir.mkdir(parents=True, exist_ok=True)

    def generate_report(self, days: int = 30) -> dict:
        """
        สร้าง full analytics report ตาม format ใน architecture_plan.md section 9.2
        
        Returns:
            dict ที่มีโครงสร้างตาม AI Report Format
        """
        trades = self._load_trades(days)
        
        if not trades:
            logger.warning(f"No trades found in the last {days} days")
            return {"error": "No trades found", "days": days}
        
        closed_trades = [t for t in trades if t.get('type') == 'CLOSE']
        
        report = {
            "report_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "total_trades": len(closed_trades),
            "period_days": days,
            "mode_performance": self._mode_performance(closed_trades),
            "factor_contribution": self._factor_contribution(closed_trades),
            "score_vs_winrate": self._score_vs_winrate_buckets(closed_trades),
            "market_condition_matrix": self._market_condition_matrix(closed_trades),
            "session_performance": self._session_performance(closed_trades),
            "current_config": self._get_current_config(),
            "trend_filter_effectiveness": self._htf_trend_effectiveness(closed_trades)
        }
        
        return report
    
    def _load_trades(self, days: int) -> List[dict]:
        """Load trades from the specified period"""
        trades = []
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        
        trades_json = self.storage.trades_json
        if not trades_json.exists():
            return trades
        
        try:
            with open(trades_json, 'r') as f:
                for line in f:
                    try:
                        record = json.loads(line.strip())
                        record_time = datetime.fromisoformat(
                            record.get('timestamp', '').replace('Z', '+00:00')
                        )
                        if record_time >= cutoff:
                            trades.append(record)
                    except (json.JSONDecodeError, ValueError):
                        continue
        except Exception as e:
            logger.error(f"Error loading trades: {e}")
        
        return trades
    
    def _mode_performance(self, trades: List[dict]) -> dict:
        """คำนวณ performance แยกตาม mode/pattern"""
        mode_stats = defaultdict(lambda: {
            'trades': 0, 'wins': 0, 'losses': 0, 
            'total_rr': 0, 'total_score': 0, 'total_score_pct': 0
        })
        
        for trade in trades:
            mode = trade.get('pattern_type', 'UNKNOWN')
            stats = mode_stats[mode]
            
            stats['trades'] += 1
            
            pnl = trade.get('pnl', 0)
            if pnl > 0:
                stats['wins'] += 1
            else:
                stats['losses'] += 1
            
            rr = trade.get('rr_actual', 0)
            if rr:
                stats['total_rr'] += rr
            
            score = trade.get('score', 0)
            if score:
                stats['total_score'] += score
                
            max_score = trade.get('max_score', 20)
            if max_score > 0:
                stats['total_score_pct'] += (score / max_score) * 100
        
        result = {}
        for mode, stats in mode_stats.items():
            trades_count = stats['trades']
            if trades_count > 0:
                result[mode] = {
                    'trades': trades_count,
                    'win_rate': round(stats['wins'] / trades_count * 100, 1),
                    'avg_rr': round(stats['total_rr'] / trades_count, 2),
                    'avg_score': round(stats['total_score'] / trades_count, 1),
                    'avg_score_pct': round(stats['total_score_pct'] / trades_count, 1)
                }
        
        return result
    
    def _factor_contribution(self, trades: List[dict]) -> dict:
        """
        คำนวณ win rate เมื่อแต่ละ factor ได้ score สูงสุด
        """
        factor_stats = defaultdict(lambda: {'max_score_trades': 0, 'wins_when_max': 0})
        
        for trade in trades:
            breakdown = trade.get('score_breakdown', {})
            if not breakdown:
                continue
            
            mode = trade.get('pattern_type', 'UNKNOWN')
            
            for factor, score in breakdown.items():
                factor_key = f"{mode}.{factor}"
                max_score = self._get_factor_max_score(factor)
                
                if score >= max_score * 0.8:
                    factor_stats[factor_key]['max_score_trades'] += 1
                    if trade.get('pnl', 0) > 0:
                        factor_stats[factor_key]['wins_when_max'] += 1
        
        result = {}
        for factor, stats in factor_stats.items():
            if stats['max_score_trades'] > 0:
                result[factor] = {
                    'avg_score': self._get_factor_max_score(factor.split('.')[-1]),
                    'win_rate_when_max': round(
                        stats['wins_when_max'] / stats['max_score_trades'] * 100, 1
                    ),
                    'count': stats['max_score_trades']
                }
        
        return result
    
    def _get_factor_max_score(self, factor: str) -> int:
        """คืนค่า max score ของแต่ละ factor"""
        factor_max = {
            'zone_quality': 5,
            'cvd_aligned': 3,
            'entry_position': 3,
            'binance_confirm': 3,
            'sweep_quality': 5,
            'oi_surge': 3,
            'wall_confluence': 4,
            'context_aligned': 3,
            'full_alignment': 2
        }
        return factor_max.get(factor, 2)
    
    def _score_vs_winrate_buckets(self, trades: List[dict]) -> dict:
        """แบ่ง score เป็น bucket แล้วคำนวณ win rate ต่อ bucket"""
        buckets = defaultdict(lambda: {'trades': 0, 'wins': 0})
        
        bucket_ranges = [
            (0, 6), (7, 9), (10, 12), (13, 15), (16, 18), (19, 20)
        ]
        
        for trade in trades:
            score = trade.get('score', 0)
            
            for low, high in bucket_ranges:
                if low <= score <= high:
                    bucket_key = f"{low}-{high}"
                    buckets[bucket_key]['trades'] += 1
                    if trade.get('pnl', 0) > 0:
                        buckets[bucket_key]['wins'] += 1
                    break
        
        result = {}
        for bucket, stats in sorted(buckets.items()):
            trades_count = stats['trades']
            if trades_count > 0:
                result[bucket] = {
                    'trades': trades_count,
                    'win_rate': round(stats['wins'] / trades_count * 100, 1)
                }
        
        return result
    
    def _market_condition_matrix(self, trades: List[dict]) -> dict:
        """cross-tab: mode × market_condition → win rate"""
        matrix = defaultdict(lambda: defaultdict(lambda: {'trades': 0, 'wins': 0}))
        
        for trade in trades:
            mode = trade.get('pattern_type', 'UNKNOWN')
            condition = trade.get('market_condition', 'UNKNOWN')
            
            matrix[condition][mode]['trades'] += 1
            if trade.get('pnl', 0) > 0:
                matrix[condition][mode]['wins'] += 1
        
        result = {}
        for condition, modes in matrix.items():
            result[condition] = {}
            for mode, stats in modes.items():
                trades_count = stats['trades']
                if trades_count > 0:
                    result[condition][mode] = {
                        'trades': trades_count,
                        'win_rate': round(stats['wins'] / trades_count * 100, 1)
                    }
        
        return result
    
    def _session_performance(self, trades: List[dict]) -> dict:
        """คำนวณ performance แยกตาม session"""
        session_stats = defaultdict(lambda: {'trades': 0, 'wins': 0, 'modes': defaultdict(int)})
        
        for trade in trades:
            session = trade.get('session', 'UNKNOWN')
            mode = trade.get('pattern_type', 'UNKNOWN')
            
            stats = session_stats[session]
            stats['trades'] += 1
            stats['modes'][mode] += 1
            
            if trade.get('pnl', 0) > 0:
                stats['wins'] += 1
        
        result = {}
        for session, stats in session_stats.items():
            trades_count = stats['trades']
            if trades_count > 0:
                best_mode = max(stats['modes'].items(), key=lambda x: x[1])[0] if stats['modes'] else 'UNKNOWN'
                result[session] = {
                    'trades': trades_count,
                    'win_rate': round(stats['wins'] / trades_count * 100, 1),
                    'best_mode': best_mode
                }
        
        return result
    
    def _htf_trend_effectiveness(self, trades: List[dict]) -> dict:
        """วิเคราะห์ HTF trend filter ว่ามีประสิทธิภาพแค่ไหน"""
        htf_stats = defaultdict(lambda: {'trades': 0, 'wins': 0})
        
        for trade in trades:
            h1_trend = trade.get('h1_trend', 'UNKNOWN')
            direction = trade.get('direction', 'UNKNOWN')
            
            if h1_trend == 'UNKNOWN' or direction == 'UNKNOWN':
                continue
            
            is_aligned = (h1_trend == 'BULLISH' and direction == 'LONG') or \
                        (h1_trend == 'BEARISH' and direction == 'SHORT')
            
            key = 'aligned' if is_aligned else 'counter_trend'
            htf_stats[key]['trades'] += 1
            if trade.get('pnl', 0) > 0:
                htf_stats[key]['wins'] += 1
        
        result = {}
        for key, stats in htf_stats.items():
            if stats['trades'] > 0:
                result[key] = {
                    'trades': stats['trades'],
                    'win_rate': round(stats['wins'] / stats['trades'] * 100, 1)
                }
        
        return result
    
    def _get_current_config(self) -> dict:
        """ดึง current config values"""
        try:
            from ..utils.config import load_config
            config = load_config()
            sf = config.get('smart_flow', {})
            return {
                'SWEEP_threshold': sf.get('thresholds', {}).get('SWEEP', 8),
                'WALL_threshold': sf.get('thresholds', {}).get('WALL', 8),
                'ZONE_threshold': sf.get('thresholds', {}).get('ZONE', 7),
                'poc_distance_threshold': config.get('entry_scanner', {}).get('poc_distance_threshold', 0.5),
                'cvd_min_magnitude': 0.15
            }
        except Exception as e:
            logger.warning(f"Could not load config: {e}")
            return {}
    
    def export_to_file(self, report: dict = None, output_path: str = None) -> str:
        """บันทึก report ไปยัง data/stats/"""
        if report is None:
            report = self.generate_report()
        
        if output_path is None:
            date_str = datetime.now().strftime("%Y%m%d")
            output_path = self.stats_dir / f"ai_report_{date_str}.json"
        
        try:
            with open(output_path, 'w') as f:
                json.dump(report, f, indent=2)
            logger.info(f"AI report exported to {output_path}")
            return str(output_path)
        except Exception as e:
            logger.error(f"Error exporting report: {e}")
            return ""
    
    def get_ai_prompt(self, report: dict = None) -> str:
        """สร้าง prompt พร้อม report data สำหรับส่งให้ AI วิเคราะห์"""
        if report is None:
            report = self.generate_report()
        
        prompt = """You are a quantitative trading system analyst. Analyze the following BTC trading bot statistics
and provide specific, actionable improvements to increase win rate and profitability.

## Bot Statistics Report
```json
"""
        prompt += json.dumps(report, indent=2)
        prompt += """
```

## Analysis Tasks:
1. **Threshold Optimization**: Based on score_vs_winrate data, recommend optimal score thresholds
   for each mode (SWEEP/WALL/ZONE) that maximize win rate while maintaining at least 20+ trades/month.

2. **Factor Effectiveness**: From factor_contribution data, identify which factors are most
   predictive of winning trades. Suggest weight adjustments.

3. **Mode-Market Fit**: Based on market_condition_matrix, create a decision rule:
   "When market_condition=X, enable only modes [Y, Z] with threshold T".

4. **Session Rules**: From session_performance, identify sessions with win_rate < 50%
   and suggest: disable, increase threshold, or reduce position size.

5. **HTF Trend Filter**: From trend_filter_effectiveness, determine if counter-trend trades
   should be blocked or have increased threshold.

6. **Config Changes**: Output a JSON block with exact config parameter changes:
   {
     "smart_flow.thresholds.SWEEP": <new_value>,
     "smart_flow.thresholds.ZONE": <new_value>,
     ...
   }

Be specific and data-driven. Cite exact numbers from the report to justify each recommendation."""
        
        return prompt


def main():
    """CLI entrypoint"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Generate AI analytics report')
    parser.add_argument('--days', type=int, default=30, help='Number of days to analyze')
    parser.add_argument('--export', action='store_true', help='Export to file')
    parser.add_argument('--prompt', action='store_true', help='Generate AI prompt')
    args = parser.parse_args()
    
    generator = AIReportGenerator()
    report = generator.generate_report(days=args.days)
    
    if args.export:
        path = generator.export_to_file(report)
        print(f"Report exported to: {path}")
    elif args.prompt:
        print(generator.get_ai_prompt(report))
    else:
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
