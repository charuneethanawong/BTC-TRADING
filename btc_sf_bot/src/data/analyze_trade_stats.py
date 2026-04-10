import sqlite3
import pandas as pd
from pathlib import Path
import json

def analyze_db():
    db_path = Path(__file__).resolve().parent.parent.parent / 'data' / 'trades.db'
    if not db_path.exists():
        print(f"DB not found at {db_path}")
        return

    conn = sqlite3.connect(db_path)

    # 1. Overall Win Rate by Signal Type (from trade_outcomes)
    query_base = """
    SELECT
        signal_type,
        COUNT(*) as total,
        SUM(CASE WHEN result = 'WIN' THEN 1 ELSE 0 END) as wins,
        ROUND(AVG(CASE WHEN result = 'WIN' THEN 1 ELSE 0 END) * 100, 1) as win_rate_pct,
        ROUND(AVG(pnl), 2) as avg_pnl
    FROM trade_outcomes
    WHERE result IN ('WIN', 'LOSS')
    GROUP BY signal_type
    HAVING total >= 5
    ORDER BY win_rate_pct DESC
    """
    df_overall = pd.read_sql_query(query_base, conn)
    print("\n=== OVERALL WIN RATE BY SIGNAL TYPE ===")
    print(df_overall.to_string(index=False))

    # 2. MOMENTUM Analysis: Regime + H1 Dist (JOIN outcomes with telemetry)
    query_momentum = """
    SELECT
        s.regime,
        CASE WHEN s.h1_dist_pct > 0.85 THEN 'HIGH (>85%)' ELSE 'LOW (<85%)' END as h1_pos,
        COUNT(*) as total,
        ROUND(AVG(CASE WHEN o.result = 'WIN' THEN 1 ELSE 0 END) * 100, 1) as win_rate_pct
    FROM trade_outcomes o
    JOIN signal_telemetry s ON o.signal_id = s.signal_id
    WHERE o.signal_type = 'MOMENTUM' AND o.result IN ('WIN', 'LOSS')
    GROUP BY s.regime, h1_pos
    """
    df_mom = pd.read_sql_query(query_momentum, conn)
    print("\n=== MOMENTUM: REGIME & H1 POSITION IMPACT ===")
    print(df_mom.to_string(index=False))

    # 3. ABSORPTION Analysis: M5 State (JOIN outcomes with telemetry)
    query_absorb = """
    SELECT
        s.m5_state,
        COUNT(*) as total,
        ROUND(AVG(CASE WHEN o.result = 'WIN' THEN 1 ELSE 0 END) * 100, 1) as win_rate_pct
    FROM trade_outcomes o
    JOIN signal_telemetry s ON o.signal_id = s.signal_id
    WHERE o.signal_type LIKE '%ABSORB%' AND o.result IN ('WIN', 'LOSS')
    GROUP BY s.m5_state
    """
    df_abs = pd.read_sql_query(query_absorb, conn)
    print("\n=== ABSORPTION: M5 STATE IMPACT ===")
    print(df_abs.to_string(index=False))

    # 4. REVERSAL Analysis: H1 Bias (JOIN outcomes with telemetry)
    query_reversal = """
    SELECT
        s.h1_bias_level,
        COUNT(*) as total,
        ROUND(AVG(CASE WHEN o.result = 'WIN' THEN 1 ELSE 0 END) * 100, 1) as win_rate_pct
    FROM trade_outcomes o
    JOIN signal_telemetry s ON o.signal_id = s.signal_id
    WHERE o.signal_type LIKE 'REVERSAL%' AND o.result IN ('WIN', 'LOSS')
    GROUP BY s.h1_bias_level
    """
    df_rev = pd.read_sql_query(query_reversal, conn)
    print("\n=== REVERSAL: H1 BIAS IMPACT ===")
    print(df_rev.to_string(index=False))

    conn.close()

if __name__ == "__main__":
    analyze_db()
