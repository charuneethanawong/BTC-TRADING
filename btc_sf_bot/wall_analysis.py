import sqlite3
from pathlib import Path

db_path = Path(r"D:\CODING WORKS\SMC_AI_Project\btc_sf_bot\data\trades.db")
conn = sqlite3.connect(str(db_path))
c = conn.cursor()

# Win/Loss by wall presence
c.execute("""
    SELECT 
        CASE WHEN wall_info = 'NONE' OR wall_info IS NULL OR wall_info = '' THEN 'NO_WALL' ELSE 'HAS_WALL' END as wall_status,
        COUNT(*) as total,
        SUM(CASE WHEN status = 'WIN' THEN 1 ELSE 0 END) as wins,
        SUM(CASE WHEN status = 'LOSS' THEN 1 ELSE 0 END) as losses,
        ROUND(CAST(SUM(CASE WHEN status = 'WIN' THEN 1 ELSE 0 END) AS FLOAT) / NULLIF(COUNT(*),0) * 100, 1) as win_rate,
        ROUND(SUM(pnl), 2) as total_pnl,
        ROUND(AVG(CASE WHEN pnl IS NOT NULL THEN pnl END), 3) as avg_pnl
    FROM trades 
    WHERE status IN ('WIN', 'LOSS')
    GROUP BY wall_status
""")
print(f"=== Win/Loss by Wall Presence ===")
print(f"{'Wall':<12} {'Total':<8} {'Wins':<8} {'Losses':<8} {'WR%':<8} {'PnL':<10} {'AvgPnL':<10}")
for row in c.fetchall():
    print(f"{row[0]:<12} {row[1]:<8} {row[2]:<8} {row[3]:<8} {row[4]:<8} {row[5]:<10} {row[6]:<10}")

# Win/Loss by wall direction (ASK vs BID)
c.execute("""
    SELECT 
        CASE 
            WHEN wall_info LIKE 'ASK%' THEN 'ASK_WALL'
            WHEN wall_info LIKE 'BID%' THEN 'BID_WALL'
            ELSE 'NO_WALL'
        END as wall_dir,
        COUNT(*) as total,
        SUM(CASE WHEN status = 'WIN' THEN 1 ELSE 0 END) as wins,
        SUM(CASE WHEN status = 'LOSS' THEN 1 ELSE 0 END) as losses,
        ROUND(CAST(SUM(CASE WHEN status = 'WIN' THEN 1 ELSE 0 END) AS FLOAT) / NULLIF(COUNT(*),0) * 100, 1) as win_rate,
        ROUND(SUM(pnl), 2) as total_pnl
    FROM trades 
    WHERE status IN ('WIN', 'LOSS')
    GROUP BY wall_dir
""")
print(f"\n=== Win/Loss by Wall Direction ===")
print(f"{'Wall Dir':<12} {'Total':<8} {'Wins':<8} {'Losses':<8} {'WR%':<8} {'PnL':<10}")
for row in c.fetchall():
    print(f"{row[0]:<12} {row[1]:<8} {row[2]:<8} {row[3]:<8} {row[4]:<8} {row[5]:<10}")

# Win/Loss by wall ratio bucket
c.execute("""
    SELECT 
        CASE 
            WHEN wall_info = 'NONE' OR wall_info IS NULL OR wall_info = '' THEN 'NO_WALL'
            WHEN CAST(REPLACE(SUBSTR(wall_info, INSTR(wall_info, ' ')+1, LENGTH(wall_info)-1), 'x', '') AS FLOAT) < 2.0 THEN 'WEAK_<2x'
            WHEN CAST(REPLACE(SUBSTR(wall_info, INSTR(wall_info, ' ')+1, LENGTH(wall_info)-1), 'x', '') AS FLOAT) < 5.0 THEN 'MED_2-5x'
            WHEN CAST(REPLACE(SUBSTR(wall_info, INSTR(wall_info, ' ')+1, LENGTH(wall_info)-1), 'x', '') AS FLOAT) < 10.0 THEN 'STR_5-10x'
            ELSE 'VSTR_10x+'
        END as wall_bucket,
        COUNT(*) as total,
        SUM(CASE WHEN status = 'WIN' THEN 1 ELSE 0 END) as wins,
        SUM(CASE WHEN status = 'LOSS' THEN 1 ELSE 0 END) as losses,
        ROUND(CAST(SUM(CASE WHEN status = 'WIN' THEN 1 ELSE 0 END) AS FLOAT) / NULLIF(COUNT(*),0) * 100, 1) as win_rate,
        ROUND(SUM(pnl), 2) as total_pnl,
        ROUND(AVG(CASE WHEN pnl IS NOT NULL THEN pnl END), 3) as avg_pnl
    FROM trades 
    WHERE status IN ('WIN', 'LOSS')
    GROUP BY wall_bucket
    ORDER BY wall_bucket
""")
print(f"\n=== Win/Loss by Wall Ratio Bucket ===")
print(f"{'Bucket':<12} {'Total':<8} {'Wins':<8} {'Losses':<8} {'WR%':<8} {'PnL':<10} {'AvgPnL':<10}")
for row in c.fetchall():
    print(f"{row[0]:<12} {row[1]:<8} {row[2]:<8} {row[3]:<8} {row[4]:<8} {row[5]:<10} {row[6]:<10}")

# Wall direction vs trade direction alignment
c.execute("""
    SELECT 
        CASE 
            WHEN direction = 'LONG' AND wall_info LIKE 'BID%' THEN 'LONG+BID_WALL_aligned'
            WHEN direction = 'LONG' AND wall_info LIKE 'ASK%' THEN 'LONG+ASK_WALL_contra'
            WHEN direction = 'SHORT' AND wall_info LIKE 'ASK%' THEN 'SHORT+ASK_WALL_aligned'
            WHEN direction = 'SHORT' AND wall_info LIKE 'BID%' THEN 'SHORT+BID_WALL_contra'
            ELSE 'NO_WALL'
        END as alignment,
        COUNT(*) as total,
        SUM(CASE WHEN status = 'WIN' THEN 1 ELSE 0 END) as wins,
        SUM(CASE WHEN status = 'LOSS' THEN 1 ELSE 0 END) as losses,
        ROUND(CAST(SUM(CASE WHEN status = 'WIN' THEN 1 ELSE 0 END) AS FLOAT) / NULLIF(COUNT(*),0) * 100, 1) as win_rate,
        ROUND(SUM(pnl), 2) as total_pnl,
        ROUND(AVG(CASE WHEN pnl IS NOT NULL THEN pnl END), 3) as avg_pnl
    FROM trades 
    WHERE status IN ('WIN', 'LOSS') AND wall_info != 'NONE' AND wall_info IS NOT NULL AND wall_info != ''
    GROUP BY alignment
    ORDER BY alignment
""")
print(f"\n=== Wall Direction vs Trade Direction Alignment ===")
print(f"{'Alignment':<30} {'Total':<8} {'Wins':<8} {'Losses':<8} {'WR%':<8} {'PnL':<10} {'AvgPnL':<10}")
for row in c.fetchall():
    print(f"{row[0]:<30} {row[1]:<8} {row[2]:<8} {row[3]:<8} {row[4]:<8} {row[5]:<10} {row[6]:<10}")

# By mode + wall
c.execute("""
    SELECT 
        mode,
        CASE WHEN wall_info LIKE 'BID%' THEN 'BID' WHEN wall_info LIKE 'ASK%' THEN 'ASK' ELSE 'NONE' END as wall_side,
        COUNT(*) as total,
        SUM(CASE WHEN status = 'WIN' THEN 1 ELSE 0 END) as wins,
        SUM(CASE WHEN status = 'LOSS' THEN 1 ELSE 0 END) as losses,
        ROUND(CAST(SUM(CASE WHEN status = 'WIN' THEN 1 ELSE 0 END) AS FLOAT) / NULLIF(COUNT(*),0) * 100, 1) as win_rate,
        ROUND(SUM(pnl), 2) as total_pnl
    FROM trades 
    WHERE status IN ('WIN', 'LOSS')
    GROUP BY mode, wall_side
    ORDER BY mode, wall_side
""")
print(f"\n=== Win/Loss by Mode + Wall Side ===")
print(f"{'Mode':<10} {'Wall':<8} {'Total':<8} {'Wins':<8} {'Losses':<8} {'WR%':<8} {'PnL':<10}")
for row in c.fetchall():
    print(f"{row[0]:<10} {row[1]:<8} {row[2]:<8} {row[3]:<8} {row[4]:<8} {row[5]:<8} {row[6]:<10}")

# Detailed trade list with wall info
c.execute("""
    SELECT 
        signal_id, mode, direction, signal_type, session, status, pnl, wall_info,
        CASE WHEN direction = 'LONG' AND wall_info LIKE 'BID%' THEN 'ALIGNED'
             WHEN direction = 'SHORT' AND wall_info LIKE 'ASK%' THEN 'ALIGNED'
             WHEN direction = 'LONG' AND wall_info LIKE 'ASK%' THEN 'CONTRA'
             WHEN direction = 'SHORT' AND wall_info LIKE 'BID%' THEN 'CONTRA'
             ELSE 'NONE' END as alignment
    FROM trades 
    WHERE status IN ('WIN', 'LOSS')
    ORDER BY timestamp
""")
print(f"\n=== All Trades Detail ===")
print(f"{'Signal_ID':<30} {'Mode':<8} {'Dir':<7} {'Type':<12} {'Session':<10} {'Status':<6} {'PnL':<8} {'Wall':<16} {'Align':<8}")
for row in c.fetchall():
    print(f"{row[0]:<30} {row[1]:<8} {row[2]:<7} {row[3]:<12} {row[4]:<10} {row[5]:<6} {row[6]:<8} {row[7]:<16} {row[8]:<8}")

conn.close()
