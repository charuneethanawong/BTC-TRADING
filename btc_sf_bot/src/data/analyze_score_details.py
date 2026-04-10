import sqlite3
import pandas as pd
from pathlib import Path
import json
from collections import defaultdict

def analyze_score_details_v2():
    # Correct path to DB
    db_path = Path("D:/CODING WORKS/SMC_AI_Project/btc_sf_bot/data/trades.db")

    if not db_path.exists():
        print(f"DB not found at {db_path}")
        return

    conn = sqlite3.connect(db_path)

    print("\n" + "="*60)
    print("DETAILED SCORE PARAMETER ANALYSIS (REAL DB DATA)")
    print("="*60)

    # Dictionary to store stats: {signal_type: {component: {'wins': [], 'losses': [], 'blocks': []}}}
    stats = defaultdict(lambda: defaultdict(lambda: {'wins': [], 'losses': [], 'blocks': []}))

    # 1. Extract from Completed Trades (Win/Loss) — JOIN outcomes with telemetry for breakdown
    query_trades = """
        SELECT o.signal_type, s.breakdown, o.result
        FROM trade_outcomes o
        JOIN signal_telemetry s ON o.signal_id = s.signal_id
        WHERE o.result IN ('WIN', 'LOSS') AND s.breakdown IS NOT NULL
    """
    df_trades = pd.read_sql_query(query_trades, conn)

    for _, row in df_trades.iterrows():
        sig_type = row['signal_type']
        status_key = 'wins' if row['result'] == 'WIN' else 'losses'
        try:
            bd = json.loads(row['breakdown'])
            for comp, score in bd.items():
                if isinstance(score, (int, float)):
                    stats[sig_type][comp][status_key].append(score)
        except: continue

    # 2. Extract from Gate Blocks (to see what parameters are failing)
    query_blocks = "SELECT signal_type, breakdown FROM gate_blocks WHERE breakdown IS NOT NULL"
    df_blocks = pd.read_sql_query(query_blocks, conn)

    for _, row in df_blocks.iterrows():
        sig_type = row['signal_type']
        try:
            bd = json.loads(row['breakdown'])
            for comp, score in bd.items():
                if isinstance(score, (int, float)):
                    stats[sig_type][comp]['blocks'].append(score)
        except: continue

    if not stats:
        print("No breakdown data found in either 'trade_outcomes+signal_telemetry' or 'gate_blocks' tables.")
        conn.close()
        return

    # 3. Report Results
    for sig_type, components in stats.items():
        print(f"\n>>> SIGNAL TYPE: {sig_type}")
        print(f"{'Parameter':<20} | {'WIN Avg':<10} | {'LOSS Avg':<10} | {'BLOCK Avg':<10} | {'Status'}")
        print("-" * 75)

        for comp, vals in components.items():
            win_avg = sum(vals['wins']) / len(vals['wins']) if vals['wins'] else 0
            loss_avg = sum(vals['losses']) / len(vals['losses']) if vals['losses'] else 0
            block_avg = sum(vals['blocks']) / len(vals['blocks']) if vals['blocks'] else 0

            # Impact analysis
            if win_avg > loss_avg and win_avg > 0:
                impact = "STRONG"
            elif win_avg > 0 and abs(win_avg - loss_avg) < 0.2:
                impact = "NEUTRAL"
            elif loss_avg > win_avg and loss_avg > 0:
                impact = "MISLEADING"
            else:
                impact = "N/A"

            print(f"{comp:<20} | {win_avg:<10.2f} | {loss_avg:<10.2f} | {block_avg:<10.2f} | {impact}")

    conn.close()

if __name__ == "__main__":
    analyze_score_details_v2()
