import sqlite3
import json
from collections import Counter, defaultdict

conn = sqlite3.connect('D:/CODING WORKS/SMC_AI_Project/btc_sf_bot/data/trades.db')
cursor = conn.cursor()

# 2. Analyze retrace ratio from breakdown (if stored) or estimate from entry_quality components
print('=== Breakdown Component Analysis ===\n')

# Get all breakdowns (JOIN outcomes with telemetry)
cursor.execute('''
    SELECT o.result, s.breakdown, o.mode, o.signal_type
    FROM trade_outcomes o
    JOIN signal_telemetry s ON o.signal_id = s.signal_id
    WHERE s.breakdown IS NOT NULL
    AND s.breakdown != ''
    AND o.result IN ('WIN', 'LOSS')
''')

component_analysis = defaultdict(lambda: {'WIN': [], 'LOSS': []})

for result, breakdown, mode, stype in cursor.fetchall():
    try:
        bd = json.loads(breakdown)

        # Analyze each component's impact
        for key, value in bd.items():
            if key != 'total' and isinstance(value, (int, float)):
                component_analysis[key][result].append(value)
    except:
        pass

print('Component Impact on Win/Loss:')
print('-' * 70)

results = []
for component, data in sorted(component_analysis.items()):
    wins = len(data['WIN'])
    losses = len(data['LOSS'])
    if wins + losses >= 3:
        wr = wins / (wins + losses) * 100 if wins + losses > 0 else 0
        avg_win = sum(data['WIN']) / len(data['WIN']) if wins else 0
        avg_loss = sum(data['LOSS']) / len(data['LOSS']) if losses else 0
        diff = avg_win - avg_loss
        results.append((component, wins+losses, wr, avg_win, avg_loss, diff))

# Sort by difference
results.sort(key=lambda x: abs(x[5]), reverse=True)

for comp, cnt, wr, aw, al, diff in results:
    print(f'{comp:30s} | {cnt:2d} trades | WR: {wr:5.1f}% | WIN avg: {aw:+5.2f} | LOSS avg: {al:+5.2f} | Diff: {diff:+5.2f}')

# Now check the key EQS components: retrace is NOT stored separately, but we can infer from entry_quality
# Let's look at volume and other proxies
print('\n=== Key Factors Analysis ===\n')

# Rejection (another quality indicator) — from telemetry breakdown
cursor.execute('''
    SELECT o.result, s.breakdown
    FROM trade_outcomes o
    JOIN signal_telemetry s ON o.signal_id = s.signal_id
    WHERE s.breakdown IS NOT NULL
    AND s.breakdown != ''
    AND o.result IN ('WIN', 'LOSS')
''')

rejection_wins = 0
rejection_losses = 0
vol_wins = 0
vol_losses = 0
for result, breakdown in cursor.fetchall():
    try:
        bd = json.loads(breakdown)
        if bd.get('rejection', 0) > 0:
            if result == 'WIN':
                rejection_wins += 1
            else:
                rejection_losses += 1
        if bd.get('volume_surge', bd.get('volume', bd.get('vol', 0))) > 0:
            if result == 'WIN':
                vol_wins += 1
            else:
                vol_losses += 1
    except:
        pass

print('Volume Surge:')
print(f'  WIN with volume_surge: {vol_wins}')
print(f'  LOSS with volume_surge: {vol_losses}')
if vol_wins + vol_losses > 0:
    print(f'  WR: {vol_wins/(vol_wins+vol_losses)*100:.1f}%')

print('\nRejection (wick rejection):')
print(f'  WIN with rejection: {rejection_wins}')
print(f'  LOSS with rejection: {rejection_losses}')
if rejection_wins + rejection_losses > 0:
    print(f'  WR: {rejection_wins/(rejection_wins+rejection_losses)*100:.1f}%')

# Check if there's retrace data in breakdown
print('\n=== Looking for Retrace Data ===')
cursor.execute('''
    SELECT breakdown FROM signal_telemetry WHERE breakdown IS NOT NULL LIMIT 5
''')
for row in cursor.fetchall():
    bd = json.loads(row[0])
    print(f'Keys: {list(bd.keys())}')

conn.close()
