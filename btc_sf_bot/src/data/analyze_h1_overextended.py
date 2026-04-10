import sqlite3
from collections import defaultdict

conn = sqlite3.connect('D:/CODING WORKS/SMC_AI_Project/btc_sf_bot/data/trades.db')
cursor = conn.cursor()

print('=' * 70)
print('H1_OVEREXTENDED GATE ANALYSIS')
print('=' * 70)

# 1. Get all H1_OVEREXTENDED gate blocks
print('\n=== H1_OVEREXTENDED GATE BLOCKS ===')
cursor.execute('''
    SELECT gate_reason, COUNT(*) as cnt
    FROM gate_blocks
    WHERE gate_reason LIKE 'H1_OVEREXTENDED%'
    GROUP BY gate_reason
    ORDER BY cnt DESC
''')

h1_blocks = cursor.fetchall()
total_h1_blocks = sum(cnt for _, cnt in h1_blocks)
print(f'Total H1_OVEREXTENDED blocks: {total_h1_blocks}\n')

for reason, cnt in h1_blocks:
    pct = cnt / total_h1_blocks * 100
    print(f'{cnt:3d} ({pct:5.1f}%) | {reason}')

# 2. Get H1 distance distribution in gate_blocks
print('\n=== H1 DISTANCE DISTRIBUTION (Gate Blocks) ===')
cursor.execute('''
    SELECT h1_dist, COUNT(*) as cnt
    FROM gate_blocks
    WHERE gate_reason LIKE 'H1_OVEREXTENDED%'
    GROUP BY h1_dist
    ORDER BY h1_dist DESC
''')

print(f'{"h1_dist":<10} | {"count":>5}')
print('-' * 25)
for h1_dist, cnt in cursor.fetchall():
    print(f'{h1_dist:<10} | {cnt:>5}')

# 3. Compare with trades that WERE executed (JOIN outcomes with telemetry)
print('\n=== H1 DISTANCE IN EXECUTED TRADES ===')
cursor.execute('''
    SELECT s.h1_dist_pct, o.result, COUNT(*) as cnt
    FROM trade_outcomes o
    JOIN signal_telemetry s ON o.signal_id = s.signal_id
    WHERE o.result IN ('WIN', 'LOSS') AND s.h1_dist_pct IS NOT NULL
    GROUP BY s.h1_dist_pct, o.result
    ORDER BY s.h1_dist_pct DESC
''')

h1_trades = defaultdict(lambda: {'WIN': 0, 'LOSS': 0})
for h1_dist, result, cnt in cursor.fetchall():
    h1_trades[h1_dist][result] = cnt

print(f'{"h1_dist":<10} | {"WIN":>5} | {"LOSS":>5} | {"WR":>6} | {"Total":>5}')
print('-' * 40)
for h1_dist in sorted(h1_trades.keys(), reverse=True):
    wins = h1_trades[h1_dist]['WIN']
    losses = h1_trades[h1_dist]['LOSS']
    total = wins + losses
    wr = wins / total * 100 if total > 0 else 0
    print(f'{h1_dist:<10} | {wins:>5} | {losses:>5} | {wr:>5.1f}% | {total:>5}')

# 4. What if H1_OVEREXTENDED trades were executed?
print('\n' + '=' * 70)
print('HYPOTHETICAL: WHAT IF H1_OVEREXTENDED WERE EXECUTED?')
print('=' * 70)

# Get average WR for trades with h1_dist <= 1.0% (the threshold)
cursor.execute('''
    SELECT o.result, COUNT(*) as cnt
    FROM trade_outcomes o
    JOIN signal_telemetry s ON o.signal_id = s.signal_id
    WHERE o.result IN ('WIN', 'LOSS') AND s.h1_dist_pct IS NOT NULL AND s.h1_dist_pct <= 1.0
    GROUP BY o.result
''')

low_h1_stats = {'WIN': 0, 'LOSS': 0}
for result, cnt in cursor.fetchall():
    low_h1_stats[result] = cnt

low_h1_total = low_h1_stats['WIN'] + low_h1_stats['LOSS']
low_h1_wr = low_h1_stats['WIN'] / low_h1_total * 100 if low_h1_total > 0 else 0

print(f'\nTrades with h1_dist <= 1.0% (BELOW threshold):')
print(f'  WIN: {low_h1_stats["WIN"]}, LOSS: {low_h1_stats["LOSS"]}')
print(f'  WR: {low_h1_wr:.1f}%')

# Get WR for trades with h1_dist > 1.0%
cursor.execute('''
    SELECT o.result, COUNT(*) as cnt
    FROM trade_outcomes o
    JOIN signal_telemetry s ON o.signal_id = s.signal_id
    WHERE o.result IN ('WIN', 'LOSS') AND s.h1_dist_pct IS NOT NULL AND s.h1_dist_pct > 1.0
    GROUP BY o.result
''')

high_h1_stats = {'WIN': 0, 'LOSS': 0}
for result, cnt in cursor.fetchall():
    high_h1_stats[result] = cnt

high_h1_total = high_h1_stats['WIN'] + high_h1_stats['LOSS']
high_h1_wr = high_h1_stats['WIN'] / high_h1_total * 100 if high_h1_total > 0 else 0

print(f'\nTrades with h1_dist > 1.0% (ABOVE threshold - like gate blocks):')
print(f'  WIN: {high_h1_stats["WIN"]}, LOSS: {high_h1_stats["LOSS"]}')
print(f'  WR: {high_h1_wr:.1f}%')

# 5. Statistical significance
print('\n' + '=' * 70)
print('STATISTICAL SIGNIFICANCE')
print('=' * 70)

if high_h1_total > 0 and low_h1_total > 0:
    diff = low_h1_wr - high_h1_wr
    print(f'\nWR difference: {diff:.1f}%')
    print(f'Blocks prevented: {total_h1_blocks}')

    # Estimate how many losses would have occurred
    estimated_losses_prevented = total_h1_blocks * (high_h1_wr / 100)
    print(f'\nEstimated losses prevented: ~{estimated_losses_prevented:.0f}')
    print(f'(If H1_OVEREXTENDED trades had {high_h1_wr:.1f}% WR like executed trades)')
else:
    print('\nInsufficient data for statistical analysis')

# 6. Check trades with h1_dist > threshold that WERE executed
print('\n' + '=' * 70)
print('TRADES WITH HIGH H1_DIST THAT WERE EXECUTED')
print('=' * 70)

cursor.execute('''
    SELECT o.signal_id, o.timestamp_closed, o.direction, o.signal_type,
           s.m5_state, s.h1_dist_pct, o.result, o.pnl
    FROM trade_outcomes o
    JOIN signal_telemetry s ON o.signal_id = s.signal_id
    WHERE o.result IN ('WIN', 'LOSS') AND s.h1_dist_pct IS NOT NULL AND s.h1_dist_pct > 1.0
    ORDER BY s.h1_dist_pct DESC
''')

print(f'\n{"ID":<20} | {"h1_dist":<10} | {"Dir":<6} | {"Signal Type":<15} | {"M5 State":<12} | {"Result":<6} | {"PnL":<8}')
print('-' * 90)
for row in cursor.fetchall():
    sid, ts, direction, stype, m5_state, h1_dist, result, pnl = row
    sid_short = sid[:18] if sid else ''
    print(f'{sid_short:<20} | {h1_dist:<10.2f} | {direction:<6} | {stype:<15} | {m5_state or "":<12} | {result:<6} | {pnl:<8.2f}')

# 7. Overall H1 distribution
print('\n' + '=' * 70)
print('H1 DISTRIBUTION - ALL TRADES')
print('=' * 70)

cursor.execute('''
    SELECT
        CASE
            WHEN s.h1_dist_pct IS NULL THEN 'NULL'
            WHEN s.h1_dist_pct <= 0.5 THEN '<=0.5%'
            WHEN s.h1_dist_pct <= 1.0 THEN '0.5-1.0%'
            WHEN s.h1_dist_pct <= 1.5 THEN '1.0-1.5%'
            ELSE '>1.5%'
        END as h1_range,
        o.result,
        COUNT(*) as cnt
    FROM trade_outcomes o
    JOIN signal_telemetry s ON o.signal_id = s.signal_id
    WHERE o.result IN ('WIN', 'LOSS')
    GROUP BY h1_range, o.result
    ORDER BY h1_range, o.result
''')

h1_range_stats = defaultdict(lambda: {'WIN': 0, 'LOSS': 0})
for h1_range, result, cnt in cursor.fetchall():
    h1_range_stats[h1_range][result] = cnt

print(f'\n{"H1 Range":<12} | {"WIN":>5} | {"LOSS":>5} | {"Total":>6} | {"WR":>6}')
print('-' * 40)
for h1_range in ['NULL', '<=0.5%', '0.5-1.0%', '1.0-1.5%', '>1.5%']:
    if h1_range in h1_range_stats:
        wins = h1_range_stats[h1_range]['WIN']
        losses = h1_range_stats[h1_range]['LOSS']
        total = wins + losses
        wr = wins / total * 100 if total > 0 else 0
        print(f'{h1_range:<12} | {wins:>5} | {losses:>5} | {total:>6} | {wr:>5.1f}%')
    else:
        print(f'{h1_range:<12} | {"-":>5} | {"-":>5} | {"-":>6} | {"-":>6}')

conn.close()

print('\n' + '=' * 70)
print('ANALYSIS COMPLETE')
print('=' * 70)
