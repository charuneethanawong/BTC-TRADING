import sqlite3
import json

conn = sqlite3.connect('btc_sf_bot/data/trades.db')
cursor = conn.cursor()

# 1. Total trades + outcomes
cursor.execute("SELECT COUNT(*) FROM trades")
print(f"Active trades: {cursor.fetchone()[0]}")
cursor.execute("SELECT COUNT(*) FROM trade_outcomes")
print(f"Trade outcomes: {cursor.fetchone()[0]}")
cursor.execute("SELECT COUNT(*) FROM signal_telemetry")
print(f"Signal telemetry: {cursor.fetchone()[0]}")

# 2. Non-empty breakdowns in telemetry
cursor.execute("SELECT COUNT(*) FROM signal_telemetry WHERE breakdown IS NOT NULL AND breakdown != '{}' AND breakdown != ''")
print(f"Telemetry with breakdown: {cursor.fetchone()[0]}")

# 3. Signal type stats (v50.5: from trade_outcomes)
cursor.execute("""
    SELECT signal_type, COUNT(*),
           ROUND(AVG(mfe), 2), ROUND(AVG(mae), 2),
           ROUND(SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1) as WR
    FROM trade_outcomes
    WHERE result IN ('WIN', 'LOSS')
    GROUP BY signal_type
""")
rows = cursor.fetchall()
print("\nSignal Type Analysis:")
for r in rows:
    print(f"{r[0]:<15} | Count: {r[1]:>3} | WR: {r[4]:>5.1f}% | Avg MFE: {r[2]:>6.2f} | Avg MAE: {r[3]:>6.2f}")

# 4. Parameters by Signal Type (v50.5: JOIN outcomes with telemetry)
print("\nParameter Correlation (Wins vs Losses):")
for sig_type in [r[0] for r in rows]:
    print(f"\n--- {sig_type} ---")
    cursor.execute("""
        SELECT o.result, AVG(s.der), AVG(s.delta), AVG(s.h1_dist_pct), AVG(s.score_total)
        FROM trade_outcomes o
        JOIN signal_telemetry s ON o.signal_id = s.signal_id
        WHERE o.signal_type = ? AND o.result IN ('WIN', 'LOSS')
        GROUP BY o.result
    """, (sig_type,))
    p_rows = cursor.fetchall()
    for p in p_rows:
        der = p[1] or 0
        delta = p[2] or 0
        h1dist = p[3] or 0
        score = p[4] or 0
        print(f"Result: {p[0]:<5} | DER: {der:.3f} | Delta: {delta:.1f} | H1Dist: {h1dist:.2f}% | Score: {score:.1f}")

conn.close()
