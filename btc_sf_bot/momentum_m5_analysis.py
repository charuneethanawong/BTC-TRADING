import sqlite3
from pathlib import Path

db_path = Path(r"D:\CODING WORKS\SMC_AI_Project\btc_sf_bot\data\trades.db")
conn = sqlite3.connect(str(db_path))
c = conn.cursor()

# MOMENTUM trades by M5 State
c.execute("""
    SELECT 
        m5_state,
        COUNT(*) as total,
        SUM(CASE WHEN status = 'WIN' THEN 1 ELSE 0 END) as wins,
        SUM(CASE WHEN status = 'LOSS' THEN 1 ELSE 0 END) as losses,
        ROUND(CAST(SUM(CASE WHEN status = 'WIN' THEN 1 ELSE 0 END) AS FLOAT) / NULLIF(COUNT(*),0) * 100, 1) as win_rate,
        ROUND(SUM(pnl), 2) as total_pnl,
        ROUND(AVG(CASE WHEN pnl IS NOT NULL THEN pnl END), 3) as avg_pnl,
        GROUP_CONCAT(signal_id || ':' || status || '(' || pnl || ')') as trades
    FROM trades 
    WHERE status IN ('WIN', 'LOSS') AND signal_type = 'MOMENTUM'
    GROUP BY m5_state
    ORDER BY total DESC
""")
print("=== MOMENTUM Trades by M5 State ===")
print(f"{'M5 State':<15} {'Total':<8} {'Wins':<8} {'Losses':<8} {'WR%':<8} {'PnL':<10} {'AvgPnL':<10}")
for row in c.fetchall():
    print(f"{row[0]:<15} {row[1]:<8} {row[2]:<8} {row[3]:<8} {row[4]:<8} {row[5]:<10} {row[6]:<10}")
    # Print individual trades
    if row[7]:
        for t in row[7].split(','):
            print(f"    └─ {t}")

# All signal types by M5 State (for comparison)
c.execute("""
    SELECT 
        signal_type,
        m5_state,
        COUNT(*) as total,
        SUM(CASE WHEN status = 'WIN' THEN 1 ELSE 0 END) as wins,
        SUM(CASE WHEN status = 'LOSS' THEN 1 ELSE 0 END) as losses,
        ROUND(CAST(SUM(CASE WHEN status = 'WIN' THEN 1 ELSE 0 END) AS FLOAT) / NULLIF(COUNT(*),0) * 100, 1) as win_rate,
        ROUND(SUM(pnl), 2) as total_pnl
    FROM trades 
    WHERE status IN ('WIN', 'LOSS')
    GROUP BY signal_type, m5_state
    ORDER BY signal_type, m5_state
""")
print(f"\n=== All Signal Types by M5 State ===")
print(f"{'Type':<15} {'M5 State':<15} {'Total':<8} {'Wins':<8} {'Losses':<8} {'WR%':<8} {'PnL':<10}")
for row in c.fetchall():
    print(f"{row[0]:<15} {row[1]:<15} {row[2]:<8} {row[3]:<8} {row[4]:<8} {row[5]:<8} {row[6]:<10}")

# MOMENTUM by Session + M5 State
c.execute("""
    SELECT 
        session,
        m5_state,
        COUNT(*) as total,
        SUM(CASE WHEN status = 'WIN' THEN 1 ELSE 0 END) as wins,
        SUM(CASE WHEN status = 'LOSS' THEN 1 ELSE 0 END) as losses,
        ROUND(CAST(SUM(CASE WHEN status = 'WIN' THEN 1 ELSE 0 END) AS FLOAT) / NULLIF(COUNT(*),0) * 100, 1) as win_rate,
        ROUND(SUM(pnl), 2) as total_pnl
    FROM trades 
    WHERE status IN ('WIN', 'LOSS') AND signal_type = 'MOMENTUM'
    GROUP BY session, m5_state
    ORDER BY session, m5_state
""")
print(f"\n=== MOMENTUM by Session + M5 State ===")
print(f"{'Session':<12} {'M5 State':<15} {'Total':<8} {'Wins':<8} {'Losses':<8} {'WR%':<8} {'PnL':<10}")
for row in c.fetchall():
    print(f"{row[0]:<12} {row[1]:<15} {row[2]:<8} {row[3]:<8} {row[4]:<8} {row[5]:<8} {row[6]:<10}")

conn.close()
