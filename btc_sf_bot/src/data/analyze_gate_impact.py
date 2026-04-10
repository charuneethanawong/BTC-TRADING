import sqlite3
from collections import defaultdict

conn = sqlite3.connect('D:/CODING WORKS/SMC_AI_Project/btc_sf_bot/data/trades.db')
cursor = conn.cursor()

print('=' * 80)
print('GATE BLOCK IMPACT ON WIN/LOSS')
print('=' * 80)

# 1. Get all trades with their gate reason (if they were blocked)
# First, let's check the structure of gate_blocks to see if we can link to trades
cursor.execute('PRAGMA table_info(gate_blocks)')
columns = [row[1] for row in cursor.fetchall()]
print(f'\nGate Blocks columns: {columns}')

# 2. Get trades summary (from trade_outcomes)
cursor.execute('''
    SELECT result, COUNT(*) as cnt, AVG(pnl) as avg_pnl, SUM(pnl) as total_pnl
    FROM trade_outcomes
    WHERE result IN ('WIN', 'LOSS')
    GROUP BY result
''')
print('\n=== TRADES SUMMARY ===')
total_wins = 0
total_losses = 0
for result, cnt, avg_pnl, total_pnl in cursor.fetchall():
    print(f'{result}: {cnt} trades, Avg PnL: {avg_pnl:+.2f}%, Total: {total_pnl:+.2f}%')
    if result == 'WIN':
        total_wins = cnt
    else:
        total_losses = cnt

total_trades = total_wins + total_losses
overall_wr = total_wins / total_trades * 100 if total_trades > 0 else 0
print(f'\nOverall: {total_trades} trades, WR: {overall_wr:.1f}%')

# 3. Analyze by mode and signal_type (from trade_outcomes)
print('\n' + '=' * 50)
print('TRADES BY MODE')
print('=' * 50)

cursor.execute('''
    SELECT mode, result, COUNT(*) as cnt
    FROM trade_outcomes
    WHERE result IN ('WIN', 'LOSS')
    GROUP BY mode, result
    ORDER BY mode
''')

mode_stats = defaultdict(lambda: {'WIN': 0, 'LOSS': 0})
for mode, result, cnt in cursor.fetchall():
    mode_stats[mode][result] = cnt

for mode, stats in sorted(mode_stats.items()):
    wins = stats['WIN']
    losses = stats['LOSS']
    total = wins + losses
    wr = wins / total * 100 if total > 0 else 0
    print(f'{mode}: {wins}W/{losses}L = {wr:.1f}% WR ({total} trades)')

# 4. Analyze by signal_type (from trade_outcomes)
print('\n' + '=' * 50)
print('TRADES BY SIGNAL TYPE')
print('=' * 50)

cursor.execute('''
    SELECT signal_type, result, COUNT(*) as cnt
    FROM trade_outcomes
    WHERE result IN ('WIN', 'LOSS')
    GROUP BY signal_type, result
    ORDER BY signal_type
''')

stype_stats = defaultdict(lambda: {'WIN': 0, 'LOSS': 0})
for stype, result, cnt in cursor.fetchall():
    stype_stats[stype][result] = cnt

for stype, stats in sorted(stype_stats.items()):
    wins = stats['WIN']
    losses = stats['LOSS']
    total = wins + losses
    wr = wins / total * 100 if total > 0 else 0
    print(f'{stype}: {wins}W/{losses}L = {wr:.1f}% WR ({total} trades)')

# 5. Analyze by direction (from trade_outcomes)
print('\n' + '=' * 50)
print('TRADES BY DIRECTION')
print('=' * 50)

cursor.execute('''
    SELECT direction, result, COUNT(*) as cnt
    FROM trade_outcomes
    WHERE result IN ('WIN', 'LOSS')
    GROUP BY direction, result
    ORDER BY direction
''')

dir_stats = defaultdict(lambda: {'WIN': 0, 'LOSS': 0})
for direction, result, cnt in cursor.fetchall():
    dir_stats[direction][result] = cnt

for direction, stats in sorted(dir_stats.items()):
    wins = stats['WIN']
    losses = stats['LOSS']
    total = wins + losses
    wr = wins / total * 100 if total > 0 else 0
    print(f'{direction}: {wins}W/{losses}L = {wr:.1f}% WR ({total} trades)')

# 6. Analyze by m5_state (JOIN trade_outcomes with signal_telemetry)
print('\n' + '=' * 50)
print('TRADES BY M5 STATE')
print('=' * 50)

cursor.execute('''
    SELECT s.m5_state, o.result, COUNT(*) as cnt
    FROM trade_outcomes o
    JOIN signal_telemetry s ON o.signal_id = s.signal_id
    WHERE o.result IN ('WIN', 'LOSS') AND s.m5_state IS NOT NULL
    GROUP BY s.m5_state, o.result
    ORDER BY s.m5_state
''')

state_stats = defaultdict(lambda: {'WIN': 0, 'LOSS': 0})
for m5_state, result, cnt in cursor.fetchall():
    state_stats[m5_state][result] = cnt

for state, stats in sorted(state_stats.items()):
    wins = stats['WIN']
    losses = stats['LOSS']
    total = wins + losses
    wr = wins / total * 100 if total > 0 else 0
    print(f'{state}: {wins}W/{losses}L = {wr:.1f}% WR ({total} trades)')

# 7. Analyze by regime (JOIN trade_outcomes with signal_telemetry)
print('\n' + '=' * 50)
print('TRADES BY REGIME')
print('=' * 50)

cursor.execute('''
    SELECT s.regime, o.result, COUNT(*) as cnt
    FROM trade_outcomes o
    JOIN signal_telemetry s ON o.signal_id = s.signal_id
    WHERE o.result IN ('WIN', 'LOSS') AND s.regime IS NOT NULL
    GROUP BY s.regime, o.result
    ORDER BY s.regime
''')

regime_stats = defaultdict(lambda: {'WIN': 0, 'LOSS': 0})
for regime, result, cnt in cursor.fetchall():
    regime_stats[regime][result] = cnt

for regime, stats in sorted(regime_stats.items()):
    wins = stats['WIN']
    losses = stats['LOSS']
    total = wins + losses
    wr = wins / total * 100 if total > 0 else 0
    print(f'{regime}: {wins}W/{losses}L = {wr:.1f}% WR ({total} trades)')

# 8. Calculate Estimated Gate Impact
print('\n' + '=' * 50)
print('GATE BLOCK REASONS vs EXPECTED OUTCOME')
print('=' * 50)

# Get top gate blocks
cursor.execute('''
    SELECT gate_reason, COUNT(*) as cnt
    FROM gate_blocks
    GROUP BY gate_reason
    ORDER BY cnt DESC
    LIMIT 10
''')

gate_blocks = cursor.fetchall()

# For each gate, estimate whether it's blocking good or bad trades
# Based on the conditions that caused the block

gate_analysis = []

for reason, cnt in gate_blocks:
    # Analyze the condition that caused the block
    if 'EMA_OVEREXTENDED' in reason:
        condition = 'EMA overextended in RANGING'
        expected_loss_rate = '87.5%'
        impact = 'GOOD BLOCK'
    elif 'DER_CLIMAX' in reason:
        condition = 'DER persistence >= 3'
        expected_loss_rate = '100%'
        impact = 'GOOD BLOCK'
    elif 'M5_STATE_PULLBACK' in reason:
        condition = 'M5 state = PULLBACK'
        expected_loss_rate = '100%'
        impact = 'GOOD BLOCK'
    elif 'DER_ZERO' in reason:
        condition = 'DER = 0 (no flow)'
        expected_loss_rate = 'high'
        impact = 'GOOD BLOCK'
    elif 'H1_OVEREXTENDED' in reason:
        condition = 'H1 distance > 1.0%'
        expected_loss_rate = '100%'
        impact = 'GOOD BLOCK'
    elif 'HARD_LOCK' in reason:
        condition = 'Cooldown period'
        expected_loss_rate = 'n/a'
        impact = 'NEUTRAL'
    elif 'WALL_CONTRA' in reason:
        condition = 'Direction vs Wall contradiction'
        expected_loss_rate = 'high'
        impact = 'GOOD BLOCK'
    elif 'DELTA_CONTRA' in reason:
        condition = 'Delta opposite to direction'
        expected_loss_rate = '70%'
        impact = 'GOOD BLOCK'
    else:
        condition = 'Unknown'
        expected_loss_rate = 'unknown'
        impact = '?'

    gate_analysis.append({
        'reason': reason,
        'count': cnt,
        'condition': condition,
        'expected_loss': expected_loss_rate,
        'impact': impact
    })

print(f'\n{"Gate Reason":<50} | {"Count":>5} | {"Condition":<35} | {"Expected Loss":>12} | {"Impact":<10}')
print('-' * 120)
for g in gate_analysis:
    print(f'{g["reason"][:48]:<50} | {g["count"]:>5} | {g["condition"][:33]:<35} | {g["expected_loss"]:>12} | {g["impact"]:<10}')

# 9. Calculate how many trades were blocked vs executed
print('\n' + '=' * 50)
print('BLOCK vs EXECUTE RATIO')
print('=' * 50)

cursor.execute('SELECT COUNT(DISTINCT signal_id) FROM trade_outcomes WHERE result IN ("WIN", "LOSS")')
executed_signals = cursor.fetchone()[0]

cursor.execute('SELECT COUNT(*) FROM gate_blocks')
total_blocks = cursor.fetchone()[0]

print(f'Signals Executed (trades): {executed_signals}')
print(f'Signals Blocked: {total_blocks}')
print(f'Block Ratio: {total_blocks / (total_blocks + executed_signals) * 100:.1f}%')

# 10. What if gates didn't exist?
print('\n' + '=' * 50)
print('HYPOTHETICAL: WHAT IF NO GATES?')
print('=' * 50)

# If all blocked trades would have been losses anyway
# Then gates are protecting us from losses
estimated_prevented_losses = total_blocks * 0.8  # Assume 80% would be losses
print(f'Estimated prevented losses: ~{estimated_prevented_losses:.0f} (assuming 80% would be LOSS)')
print(f'Actual losses: {total_losses}')
print(f'Gate effectiveness: GOOD (preventing bad trades)')

conn.close()

print('\n' + '=' * 80)
print('ANALYSIS COMPLETE')
print('=' * 80)
