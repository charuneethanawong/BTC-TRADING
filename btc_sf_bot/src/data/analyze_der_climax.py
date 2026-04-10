import sqlite3
from collections import defaultdict

conn = sqlite3.connect('D:/CODING WORKS/SMC_AI_Project/btc_sf_bot/data/trades.db')
cursor = conn.cursor()

print('=' * 70)
print('DER_CLIMAX GATE ANALYSIS')
print('=' * 70)

# 1. Get all DER_CLIMAX gate blocks
print('\n=== DER_CLIMAX GATE BLOCKS ===')
cursor.execute('''
    SELECT gate_reason, COUNT(*) as cnt
    FROM gate_blocks
    WHERE gate_reason LIKE 'DER_CLIMAX%'
    GROUP BY gate_reason
    ORDER BY cnt DESC
''')

der_blocks = cursor.fetchall()
total_der_blocks = sum(cnt for _, cnt in der_blocks)
print(f'Total DER_CLIMAX blocks: {total_der_blocks}\n')

for reason, cnt in der_blocks:
    pct = cnt / total_der_blocks * 100
    print(f'{cnt:3d} ({pct:5.1f}%) | {reason}')

# 2. Get DER persistence distribution in gate_blocks
print('\n=== DER PERSISTENCE DISTRIBUTION (Gate Blocks) ===')
cursor.execute('''
    SELECT der_persistence, COUNT(*) as cnt
    FROM gate_blocks
    WHERE gate_reason LIKE 'DER_CLIMAX%'
    GROUP BY der_persistence
    ORDER BY der_persistence DESC
''')

print(f'{"der_persistence":<20} | {"count":>5}')
print('-' * 30)
for der_pers, cnt in cursor.fetchall():
    print(f'{der_pers:<20} | {cnt:>5}')

# 3. Compare with trades that WERE executed (JOIN outcomes with telemetry)
print('\n' + '=' * 70)
print('DER PERSISTENCE IN EXECUTED TRADES')
print('=' * 70)

cursor.execute('''
    SELECT s.der_persistence, o.result, COUNT(*) as cnt
    FROM trade_outcomes o
    JOIN signal_telemetry s ON o.signal_id = s.signal_id
    WHERE o.result IN ('WIN', 'LOSS') AND s.der_persistence IS NOT NULL
    GROUP BY s.der_persistence, o.result
    ORDER BY s.der_persistence DESC
''')

der_trades = defaultdict(lambda: {'WIN': 0, 'LOSS': 0})
for der_pers, result, cnt in cursor.fetchall():
    der_trades[der_pers][result] = cnt

print(f'\n{"der_persistence":<15} | {"WIN":>5} | {"LOSS":>5} | {"WR":>6} | {"Total":>5}')
print('-' * 45)
for der_pers in sorted(der_trades.keys(), reverse=True):
    wins = der_trades[der_pers]['WIN']
    losses = der_trades[der_pers]['LOSS']
    total = wins + losses
    wr = wins / total * 100 if total > 0 else 0
    print(f'{der_pers:<15} | {wins:>5} | {losses:>5} | {wr:>5.1f}% | {total:>5}')

# 4. DER value analysis (JOIN outcomes with telemetry)
print('\n' + '=' * 70)
print('DER VALUE IN EXECUTED TRADES')
print('=' * 70)

cursor.execute('''
    SELECT
        CASE
            WHEN s.der IS NULL THEN 'NULL'
            WHEN s.der = 0 THEN '0'
            WHEN s.der < 0 THEN 'NEGATIVE'
            WHEN s.der < 50 THEN '0-50'
            WHEN s.der < 100 THEN '50-100'
            WHEN s.der < 200 THEN '100-200'
            ELSE '>200'
        END as der_range,
        o.result,
        COUNT(*) as cnt
    FROM trade_outcomes o
    JOIN signal_telemetry s ON o.signal_id = s.signal_id
    WHERE o.result IN ('WIN', 'LOSS')
    GROUP BY der_range, o.result
    ORDER BY der_range, o.result
''')

der_value_stats = defaultdict(lambda: {'WIN': 0, 'LOSS': 0})
for der_range, result, cnt in cursor.fetchall():
    der_value_stats[der_range][result] = cnt

print(f'\n{"DER Range":<12} | {"WIN":>5} | {"LOSS":>5} | {"Total":>6} | {"WR":>6}')
print('-' * 40)
for der_range in ['NULL', 'NEGATIVE', '0', '0-50', '50-100', '100-200', '>200']:
    if der_range in der_value_stats:
        wins = der_value_stats[der_range]['WIN']
        losses = der_value_stats[der_range]['LOSS']
        total = wins + losses
        wr = wins / total * 100 if total > 0 else 0
        print(f'{der_range:<12} | {wins:>5} | {losses:>5} | {total:>6} | {wr:>5.1f}%')

# 5. DER direction analysis (JOIN outcomes with telemetry)
print('\n' + '=' * 70)
print('DER DIRECTION IN EXECUTED TRADES')
print('=' * 70)

cursor.execute('''
    SELECT s.der_direction, o.result, COUNT(*) as cnt
    FROM trade_outcomes o
    JOIN signal_telemetry s ON o.signal_id = s.signal_id
    WHERE o.result IN ('WIN', 'LOSS') AND s.der_direction IS NOT NULL
    GROUP BY s.der_direction, o.result
    ORDER BY s.der_direction
''')

der_dir_stats = defaultdict(lambda: {'WIN': 0, 'LOSS': 0})
for der_dir, result, cnt in cursor.fetchall():
    der_dir_stats[der_dir][result] = cnt

print(f'\n{"DER Direction":<15} | {"WIN":>5} | {"LOSS":>5} | {"Total":>6} | {"WR":>6}')
print('-' * 45)
for der_dir in sorted(der_dir_stats.keys()):
    wins = der_dir_stats[der_dir]['WIN']
    losses = der_dir_stats[der_dir]['LOSS']
    total = wins + losses
    wr = wins / total * 100 if total > 0 else 0
    print(f'{der_dir:<15} | {wins:>5} | {losses:>5} | {total:>6} | {wr:>5.1f}%')

# 6. Check DER=0 trades (Gateway 16) — JOIN outcomes with telemetry
print('\n' + '=' * 70)
print('DER = 0 TRADES (Gateway 16 - DER_ZERO Block)')
print('=' * 70)

cursor.execute('''
    SELECT o.signal_id, o.timestamp_closed, o.mode, o.direction, o.signal_type,
           s.der, o.result, o.pnl
    FROM trade_outcomes o
    JOIN signal_telemetry s ON o.signal_id = s.signal_id
    WHERE o.result IN ('WIN', 'LOSS') AND (s.der IS NULL OR s.der = 0)
    ORDER BY o.timestamp_closed DESC
''')

der_zero_trades = cursor.fetchall()

if der_zero_trades:
    print(f'\n{"ID":<20} | {"Mode":<6} | {"Dir":<6} | {"Signal":<12} | {"DER":<8} | {"Result":<6} | {"PnL":<8}')
    print('-' * 80)
    for row in der_zero_trades:
        sid, ts, mode, direction, stype, der, result, pnl = row
        der_str = str(der) if der is not None else 'NULL'
        sid_short = sid[:18] if sid else ''
        print(f'{sid_short:<20} | {mode or "":<6} | {direction:<6} | {stype:<12} | {der_str:<8} | {result:<6} | {pnl:<8.2f}')
else:
    print('\nNo trades with DER=0 were executed (all blocked by Gate 16)')

# 7. What if DER_CLIMAX trades were executed?
print('\n' + '=' * 70)
print('HYPOTHETICAL: WHAT IF DER_CLIMAX WERE EXECUTED?')
print('=' * 70)

# Get WR for trades with der_persistence < 3
cursor.execute('''
    SELECT o.result, COUNT(*) as cnt
    FROM trade_outcomes o
    JOIN signal_telemetry s ON o.signal_id = s.signal_id
    WHERE o.result IN ('WIN', 'LOSS') AND s.der_persistence IS NOT NULL AND s.der_persistence < 3
    GROUP BY o.result
''')

low_der_stats = {'WIN': 0, 'LOSS': 0}
for result, cnt in cursor.fetchall():
    low_der_stats[result] = cnt

low_der_total = low_der_stats['WIN'] + low_der_stats['LOSS']
low_der_wr = low_der_stats['WIN'] / low_der_total * 100 if low_der_total > 0 else 0

print(f'\nTrades with der_persistence < 3 (BELOW threshold):')
print(f'  WIN: {low_der_stats["WIN"]}, LOSS: {low_der_stats["LOSS"]}')
print(f'  WR: {low_der_wr:.1f}%')

# 8. DER_CLIMAX specific analysis (JOIN outcomes with telemetry)
print('\n' + '=' * 70)
print('DER PERSISTENCE vs WIN RATE (Detailed)')
print('=' * 70)

cursor.execute('''
    SELECT s.der_persistence, o.result, COUNT(*) as cnt
    FROM trade_outcomes o
    JOIN signal_telemetry s ON o.signal_id = s.signal_id
    WHERE o.result IN ('WIN', 'LOSS') AND s.der_persistence IS NOT NULL
    GROUP BY s.der_persistence, o.result
''')

der_pers_detail = defaultdict(lambda: {'WIN': 0, 'LOSS': 0})
for der_pers, result, cnt in cursor.fetchall():
    der_pers_detail[der_pers][result] = cnt

print(f'\n{"der_pers":<12} | {"WIN":>5} | {"LOSS":>5} | {"Total":>6} | {"WR":>6} | {"Conclusion"}')
print('-' * 65)
for pers in sorted(der_pers_detail.keys()):
    wins = der_pers_detail[pers]['WIN']
    losses = der_pers_detail[pers]['LOSS']
    total = wins + losses
    wr = wins / total * 100 if total > 0 else 0

    if pers >= 3:
        conclusion = 'BLOCK (was blocked)'
    elif wr >= 50:
        conclusion = 'OK'
    else:
        conclusion = 'Risky'

    print(f'{pers:<12} | {wins:>5} | {losses:>5} | {total:>6} | {wr:>5.1f}% | {conclusion}')

# 9. Mode analysis for DER related (JOIN outcomes with telemetry)
print('\n' + '=' * 70)
print('DER BY MODE (Executed Trades)')
print('=' * 70)

cursor.execute('''
    SELECT o.mode, AVG(s.der) as avg_der, COUNT(*) as cnt
    FROM trade_outcomes o
    JOIN signal_telemetry s ON o.signal_id = s.signal_id
    WHERE o.result IN ('WIN', 'LOSS') AND s.der IS NOT NULL
    GROUP BY o.mode
    ORDER BY cnt DESC
''')

print(f'\n{"Mode":<12} | {"Avg DER":>10} | {"Count":>6}')
print('-' * 35)
for mode, avg_der, cnt in cursor.fetchall():
    print(f'{mode or "":<12} | {avg_der:>10.1f} | {cnt:>6}')

conn.close()

print('\n' + '=' * 70)
print('ANALYSIS COMPLETE')
print('=' * 70)
