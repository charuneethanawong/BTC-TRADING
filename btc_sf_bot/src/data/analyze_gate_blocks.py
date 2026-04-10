import sqlite3
from collections import Counter, defaultdict

conn = sqlite3.connect('D:/CODING WORKS/SMC_AI_Project/btc_sf_bot/data/trades.db')
cursor = conn.cursor()

print('=' * 70)
print('GATE BLOCKS ANALYSIS')
print('=' * 70)

# 1. Overall Gate Block Stats
cursor.execute('SELECT COUNT(*) FROM gate_blocks')
total_blocks = cursor.fetchone()[0]
print(f'\nTotal Gate Blocks: {total_blocks}')

# 2. Gate Blocks by Reason (Top 20)
print('\n' + '=' * 50)
print('GATE BLOCKS BY REASON (Top 20)')
print('=' * 50)

cursor.execute('''
    SELECT gate_reason, COUNT(*) as cnt
    FROM gate_blocks
    GROUP BY gate_reason
    ORDER BY cnt DESC
    LIMIT 20
''')

total = 0
for reason, cnt in cursor.fetchall():
    total += cnt
    pct = cnt / total_blocks * 100
    print(f'{cnt:4d} ({pct:5.1f}%) | {reason}')

# 3. Gate Blocks by Mode
print('\n' + '=' * 50)
print('GATE BLOCKS BY MODE')
print('=' * 50)

cursor.execute('''
    SELECT mode, COUNT(*) as cnt
    FROM gate_blocks
    GROUP BY mode
    ORDER BY cnt DESC
''')

for mode, cnt in cursor.fetchall():
    pct = cnt / total_blocks * 100
    print(f'{cnt:4d} ({pct:5.1f}%) | {mode}')

# 4. Gate Blocks by Direction
print('\n' + '=' * 50)
print('GATE BLOCKS BY DIRECTION')
print('=' * 50)

cursor.execute('''
    SELECT direction, COUNT(*) as cnt
    FROM gate_blocks
    GROUP BY direction
    ORDER BY cnt DESC
''')

for direction, cnt in cursor.fetchall():
    pct = cnt / total_blocks * 100
    print(f'{cnt:4d} ({pct:5.1f}%) | {direction}')

# 5. Gate Blocks by Signal Type
print('\n' + '=' * 50)
print('GATE BLOCKS BY SIGNAL TYPE')
print('=' * 50)

cursor.execute('''
    SELECT signal_type, COUNT(*) as cnt
    FROM gate_blocks
    GROUP BY signal_type
    ORDER BY cnt DESC
    LIMIT 10
''')

for stype, cnt in cursor.fetchall():
    pct = cnt / total_blocks * 100
    print(f'{cnt:4d} ({pct:5.1f}%) | {stype}')

# 6. Gate Blocks by M5 State
print('\n' + '=' * 50)
print('GATE BLOCKS BY M5 STATE')
print('=' * 50)

cursor.execute('''
    SELECT m5_state, COUNT(*) as cnt
    FROM gate_blocks
    WHERE m5_state IS NOT NULL
    GROUP BY m5_state
    ORDER BY cnt DESC
''')

for state, cnt in cursor.fetchall():
    pct = cnt / total_blocks * 100
    print(f'{cnt:4d} ({pct:5.1f}%) | {state}')

# 7. Group by Gate Category
print('\n' + '=' * 50)
print('GATE BLOCKS BY CATEGORY')
print('=' * 50)

cursor.execute('SELECT gate_reason FROM gate_blocks')
reasons = [row[0] for row in cursor.fetchall()]

categories = defaultdict(int)
for reason in reasons:
    if 'DER' in reason:
        categories['DER'] += 1
    elif 'EMA' in reason:
        categories['EMA'] += 1
    elif 'H1' in reason:
        categories['H1'] += 1
    elif 'WALL' in reason:
        categories['WALL'] += 1
    elif 'M5' in reason:
        categories['M5_STATE'] += 1
    elif 'HARD' in reason:
        categories['HARD_LOCK'] += 1
    elif 'DELTA' in reason:
        categories['DELTA'] += 1
    else:
        categories['OTHER'] += 1

for cat, cnt in sorted(categories.items(), key=lambda x: x[1], reverse=True):
    pct = cnt / total_blocks * 100
    print(f'{cnt:4d} ({pct:5.1f}%) | {cat}')

# 8. Analyze specific gate patterns
print('\n' + '=' * 50)
print('DETAILED ANALYSIS: DER PATTERNS')
print('=' * 50)

cursor.execute('''
    SELECT gate_reason, COUNT(*) as cnt
    FROM gate_blocks
    WHERE gate_reason LIKE 'DER%'
    GROUP BY gate_reason
    ORDER BY cnt DESC
''')

for reason, cnt in cursor.fetchall():
    pct = cnt / total_blocks * 100
    print(f'{cnt:4d} ({pct:5.1f}%) | {reason}')

print('\n' + '=' * 50)
print('DETAILED ANALYSIS: WALL PATTERNS')
print('=' * 50)

cursor.execute('''
    SELECT gate_reason, COUNT(*) as cnt
    FROM gate_blocks
    WHERE gate_reason LIKE 'WALL%'
    GROUP BY gate_reason
    ORDER BY cnt DESC
''')

for reason, cnt in cursor.fetchall():
    pct = cnt / total_blocks * 100
    print(f'{cnt:4d} ({pct:5.1f}%) | {reason}')

# 9. Wall Direction Analysis
print('\n' + '=' * 50)
print('WALL CONTRA ANALYSIS (Direction vs Wall)')
print('=' * 50)

cursor.execute('''
    SELECT gate_reason, direction, COUNT(*) as cnt
    FROM gate_blocks
    WHERE gate_reason LIKE 'WALL_CONTRA%'
    GROUP BY gate_reason, direction
    ORDER BY cnt DESC
    LIMIT 10
''')

for reason, direction, cnt in cursor.fetchall():
    print(f'{cnt:4d} | {direction:6s} | {reason}')

# 10. Time-based analysis
print('\n' + '=' * 50)
print('GATE BLOCKS BY TIME (Recent)')
print('=' * 50)

cursor.execute('''
    SELECT timestamp, gate_reason, mode
    FROM gate_blocks
    ORDER BY timestamp DESC
    LIMIT 10
''')

for ts, reason, mode in cursor.fetchall():
    print(f'{ts[:19]} | {mode:6s} | {reason[:50]}')

conn.close()

print('\n' + '=' * 70)
print('ANALYSIS COMPLETE')
print('=' * 70)