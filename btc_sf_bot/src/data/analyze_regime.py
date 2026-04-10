import sqlite3
from collections import Counter, defaultdict

conn = sqlite3.connect('D:/CODING WORKS/SMC_AI_Project/btc_sf_bot/data/trades.db')
cursor = conn.cursor()

print('=' * 70)
print('REGIME SNAPSHOTS ANALYSIS')
print('=' * 70)

# 1. Get regime_snapshots table structure
cursor.execute('PRAGMA table_info(regime_snapshots)')
print('\n=== REGIME SNAPSHOTS TABLE STRUCTURE ===')
for row in cursor.fetchall():
    print(f'{row[1]:<25} | {row[2]:<15}')

# 2. Get overall regime distribution
cursor.execute('''
    SELECT regime, COUNT(*) as cnt
    FROM regime_snapshots
    GROUP BY regime
    ORDER BY cnt DESC
''')

print('\n=== REGIME DISTRIBUTION ===')
total_snapshots = 0
regime_counts = {}
for regime, cnt in cursor.fetchall():
    regime_counts[regime] = cnt
    total_snapshots += cnt

for regime, cnt in sorted(regime_counts.items(), key=lambda x: x[1], reverse=True):
    pct = cnt / total_snapshots * 100
    print(f'{regime:<15} | {cnt:>6} ({pct:5.1f}%)')

# 3. Get regime confidence distribution
cursor.execute('''
    SELECT regime_confidence, COUNT(*) as cnt
    FROM regime_snapshots
    GROUP BY regime_confidence
    ORDER BY cnt DESC
''')

print('\n=== REGIME CONFIDENCE DISTRIBUTION ===')
for conf, cnt in cursor.fetchall():
    pct = cnt / total_snapshots * 100
    print(f'{conf:<15} | {cnt:>6} ({pct:5.1f}%)')

# 4. Cross-tab: Regime vs Confidence
print('\n' + '=' * 70)
print('REGIME vs CONFIDENCE CROSS-TAB')
print('=' * 70)

cursor.execute('''
    SELECT regime, regime_confidence, COUNT(*) as cnt
    FROM regime_snapshots
    GROUP BY regime, regime_confidence
    ORDER BY regime, cnt DESC
''')

regime_conf = defaultdict(lambda: defaultdict(int))
for regime, conf, cnt in cursor.fetchall():
    regime_conf[regime][conf] = cnt

print(f'\n{"Regime":<15} | {"HIGH":>8} | {"MEDIUM":>8} | {"LOW":>8} | {"TOTAL":>8}')
print('-' * 60)
for regime in sorted(regime_conf.keys()):
    high = regime_conf[regime].get('HIGH', 0)
    medium = regime_conf[regime].get('MEDIUM', 0)
    low = regime_conf[regime].get('LOW', 0)
    total = high + medium + low
    print(f'{regime:<15} | {high:>8} | {medium:>8} | {low:>8} | {total:>8}')

# 5. Compare regime in snapshots vs trades (JOIN outcomes with telemetry)
print('\n' + '=' * 70)
print('REGIME: SNAPSHOTS vs TRADES COMPARISON')
print('=' * 70)

# Get regime distribution from trade outcomes joined with telemetry
cursor.execute('''
    SELECT s.regime, COUNT(*) as cnt
    FROM trade_outcomes o
    JOIN signal_telemetry s ON o.signal_id = s.signal_id
    WHERE o.result IN ('WIN', 'LOSS') AND s.regime IS NOT NULL
    GROUP BY s.regime
    ORDER BY cnt DESC
''')

trade_regimes = {}
trade_total = 0
for regime, cnt in cursor.fetchall():
    trade_regimes[regime] = cnt
    trade_total += cnt

print(f'\n{"Regime":<15} | {"Snapshots":>12} | {"Trades":>8} | {"Snap %":>8} | {"Trade %":>8}')
print('-' * 60)
for regime in set(list(regime_counts.keys()) + list(trade_regimes.keys())):
    snap_cnt = regime_counts.get(regime, 0)
    trade_cnt = trade_regimes.get(regime, 0)
    snap_pct = snap_cnt / total_snapshots * 100 if total_snapshots > 0 else 0
    trade_pct = trade_cnt / trade_total * 100 if trade_total > 0 else 0
    print(f'{regime:<15} | {snap_cnt:>12} | {trade_cnt:>8} | {snap_pct:>7.1f}% | {trade_pct:>7.1f}%')

# 6. Regime accuracy - did regime predict win/loss? (JOIN outcomes with telemetry)
print('\n' + '=' * 70)
print('REGIME PREDICTION ACCURACY')
print('=' * 70)

cursor.execute('''
    SELECT s.regime, o.result, COUNT(*) as cnt
    FROM trade_outcomes o
    JOIN signal_telemetry s ON o.signal_id = s.signal_id
    WHERE o.result IN ('WIN', 'LOSS') AND s.regime IS NOT NULL
    GROUP BY s.regime, o.result
    ORDER BY s.regime
''')

regime_accuracy = defaultdict(lambda: {'WIN': 0, 'LOSS': 0})
for regime, result, cnt in cursor.fetchall():
    regime_accuracy[regime][result] = cnt

print(f'\n{"Regime":<15} | {"WIN":>5} | {"LOSS":>5} | {"Total":>6} | {"WR":>6}')
print('-' * 45)
for regime in sorted(regime_accuracy.keys()):
    wins = regime_accuracy[regime]['WIN']
    losses = regime_accuracy[regime]['LOSS']
    total = wins + losses
    wr = wins / total * 100 if total > 0 else 0
    print(f'{regime:<15} | {wins:>5} | {losses:>5} | {total:>6} | {wr:>5.1f}%')

# 7. Regime confidence vs win/loss (JOIN outcomes with telemetry)
print('\n' + '=' * 70)
print('REGIME CONFIDENCE vs WIN/LOSS')
print('=' * 70)

cursor.execute('''
    SELECT s.regime_confidence, o.result, COUNT(*) as cnt
    FROM trade_outcomes o
    JOIN signal_telemetry s ON o.signal_id = s.signal_id
    WHERE o.result IN ('WIN', 'LOSS') AND s.regime_confidence IS NOT NULL
    GROUP BY s.regime_confidence, o.result
    ORDER BY s.regime_confidence
''')

conf_accuracy = defaultdict(lambda: {'WIN': 0, 'LOSS': 0})
for conf, result, cnt in cursor.fetchall():
    conf_accuracy[conf][result] = cnt

print(f'\n{"Confidence":<15} | {"WIN":>5} | {"LOSS":>5} | {"Total":>6} | {"WR":>6}')
print('-' * 45)
for conf in ['HIGH', 'MEDIUM', 'LOW', 'NONE']:
    if conf in conf_accuracy:
        wins = conf_accuracy[conf]['WIN']
        losses = conf_accuracy[conf]['LOSS']
        total = wins + losses
        wr = wins / total * 100 if total > 0 else 0
        print(f'{conf:<15} | {wins:>5} | {losses:>5} | {total:>6} | {wr:>5.1f}%')

# 8. Look at specific regime indicators from regime_snapshots
print('\n' + '=' * 70)
print('REGIME SNAPSHOT SAMPLE DATA')
print('=' * 70)

cursor.execute('''
    SELECT timestamp, regime, regime_confidence, price, adx, bb_width
    FROM regime_snapshots
    ORDER BY timestamp DESC
    LIMIT 10
''')

print(f'\n{"Timestamp":<25} | {"Regime":<10} | {"Conf":<8} | {"Price":>10} | {"ADX":>6} | {"BB Width":>10}')
print('-' * 85)
for row in cursor.fetchall():
    ts, regime, conf, price, adx, bb_width = row
    ts_short = ts[:19] if ts else ''
    print(f'{ts_short:<25} | {regime:<10} | {conf:<8} | {price:>10.1f} | {adx:>6.1f} | {bb_width:>10.2f}')

# 9. Analyze ADX values per regime
print('\n' + '=' * 70)
print('ADX VALUES BY REGIME')
print('=' * 70)

cursor.execute('''
    SELECT regime,
           COUNT(*) as cnt,
           AVG(adx) as avg_adx,
           MIN(adx) as min_adx,
           MAX(adx) as max_adx
    FROM regime_snapshots
    WHERE adx IS NOT NULL
    GROUP BY regime
    ORDER BY avg_adx DESC
''')

print(f'\n{"Regime":<15} | {"Count":>6} | {"Avg ADX":>8} | {"Min ADX":>8} | {"Max ADX":>8}')
print('-' * 55)
for row in cursor.fetchall():
    regime, cnt, avg_adx, min_adx, max_adx = row
    print(f'{regime:<15} | {cnt:>6} | {avg_adx:>8.2f} | {min_adx:>8.2f} | {max_adx:>8.2f}')

# 10. Check for regime changes and trade outcomes
print('\n' + '=' * 70)
print('REGIME STABILITY ANALYSIS')
print('=' * 70)

cursor.execute('''
    SELECT COUNT(DISTINCT regime) as unique_regimes
    FROM regime_snapshots
''')
unique_regimes = cursor.fetchone()[0]

cursor.execute('''
    SELECT COUNT(*) / COUNT(DISTINCT timestamp) as regimes_per_timestamp
    FROM regime_snapshots
''')
regimes_per_ts = cursor.fetchone()[0]

print(f'\nUnique regimes in snapshots: {unique_regimes}')
print(f'Avg regimes per timestamp: {regimes_per_ts:.2f}')

# Check regime changes frequency
cursor.execute('''
    SELECT timestamp, regime
    FROM regime_snapshots
    ORDER BY timestamp DESC
    LIMIT 100
''')

regime_list = [row[1] for row in cursor.fetchall()]
regime_changes = sum(1 for i in range(1, len(regime_list)) if regime_list[i] != regime_list[i-1])
print(f'Regime changes in last 100 snapshots: {regime_changes} ({regime_changes/99*100:.1f}% unstable)')

conn.close()

print('\n' + '=' * 70)
print('ANALYSIS COMPLETE')
print('=' * 70)
