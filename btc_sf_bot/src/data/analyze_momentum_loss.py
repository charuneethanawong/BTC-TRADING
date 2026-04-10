import sqlite3
import json
from collections import defaultdict

conn = sqlite3.connect('D:/CODING WORKS/SMC_AI_Project/btc_sf_bot/data/trades.db')
cursor = conn.cursor()

print('=' * 70)
print('MOMENTUM LOSS ANALYSIS - WHAT CAUSES FAILURE?')
print('=' * 70)

# 1. Get all MOMENTUM trades with breakdown (JOIN outcomes with telemetry)
cursor.execute('''
    SELECT o.result, s.breakdown, s.m5_state, s.regime, o.direction, o.pnl
    FROM trade_outcomes o
    JOIN signal_telemetry s ON o.signal_id = s.signal_id
    WHERE o.result IN ('WIN', 'LOSS') AND o.signal_type = 'MOMENTUM'
''')

momentum_trades = []
for result, breakdown, m5_state, regime, direction, pnl in cursor.fetchall():
    bd = json.loads(breakdown) if breakdown else {}
    momentum_trades.append({
        'status': result,
        'breakdown': bd,
        'm5_state': m5_state,
        'regime': regime,
        'direction': direction,
        'pnl': pnl
    })

print(f'\nTotal MOMENTUM trades: {len(momentum_trades)}')

# 2. Separate WIN and LOSS
win_trades = [t for t in momentum_trades if t['status'] == 'WIN']
loss_trades = [t for t in momentum_trades if t['status'] == 'LOSS']

print(f'\nWIN: {len(win_trades)} trades')
print(f'LOSS: {len(loss_trades)} trades')

# 3. Analyze breakdown components
print('\n' + '=' * 70)
print('BREAKDOWN COMPONENTS: WIN vs LOSS')
print('=' * 70)

all_keys = set()
for t in momentum_trades:
    all_keys.update(t['breakdown'].keys())

print(f'\n{"Component":<30} | {"WIN Avg":>8} | {"LOSS Avg":>8} | {"Diff":>8}')
print('-' * 65)

component_analysis = {}
for key in all_keys:
    if key == 'total':
        continue

    win_vals = [t['breakdown'].get(key, 0) for t in win_trades if t['breakdown'].get(key, 0) != 0]
    loss_vals = [t['breakdown'].get(key, 0) for t in loss_trades if t['breakdown'].get(key, 0) != 0]

    if win_vals or loss_vals:
        win_avg = sum(win_vals) / len(win_vals) if win_vals else 0
        loss_avg = sum(loss_vals) / len(loss_vals) if loss_vals else 0
        diff = win_avg - loss_avg
        component_analysis[key] = {'win': win_avg, 'loss': loss_avg, 'diff': diff}

for key in sorted(component_analysis.keys(), key=lambda x: abs(component_analysis[x]['diff']), reverse=True):
    data = component_analysis[key]
    print(f'{key:<30} | {data["win"]:>8.2f} | {data["loss"]:>8.2f} | {data["diff"]:>+8.2f}')

# 4. Analyze by direction
print('\n' + '=' * 70)
print('MOMENTUM BY DIRECTION')
print('=' * 70)

dir_stats = defaultdict(lambda: {'WIN': 0, 'LOSS': 0})
for t in momentum_trades:
    dir_stats[t['direction']][t['status']] += 1

print(f'\n{"Direction":<10} | {"WIN":>5} | {"LOSS":>5} | {"Total":>6} | {"WR":>6}')
print('-' * 40)
for direction in sorted(dir_stats.keys()):
    w = dir_stats[direction]['WIN']
    l = dir_stats[direction]['LOSS']
    total = w + l
    wr = w / total * 100 if total > 0 else 0
    print(f'{direction:<10} | {w:>5} | {l:>5} | {total:>6} | {wr:>5.1f}%')

# 5. Analyze by M5 state
print('\n' + '=' * 70)
print('MOMENTUM BY M5 STATE')
print('=' * 70)

state_stats = defaultdict(lambda: {'WIN': 0, 'LOSS': 0})
for t in momentum_trades:
    state_stats[t['m5_state']][t['status']] += 1

print(f'\n{"M5 State":<15} | {"WIN":>5} | {"LOSS":>5} | {"Total":>6} | {"WR":>6}')
print('-' * 45)
for state in sorted(state_stats.keys()):
    w = state_stats[state]['WIN']
    l = state_stats[state]['LOSS']
    total = w + l
    wr = w / total * 100 if total > 0 else 0
    print(f'{state:<15} | {w:>5} | {l:>5} | {total:>6} | {wr:>5.1f}%')

# 6. LOSS trades details
print('\n' + '=' * 70)
print('LOSS TRADES - DETAILED BREAKDOWN')
print('=' * 70)

for i, t in enumerate(loss_trades):
    print(f'\n--- LOSS #{i+1} ---')
    print(f'  Regime: {t["regime"]} | M5: {t["m5_state"]} | Dir: {t["direction"]} | PnL: {t["pnl"]:.2f}%')
    print(f'  Breakdown: {t["breakdown"]}')

# 7. KEY FINDINGS (dynamic)
print('\n' + '=' * 70)
print('KEY FINDINGS - WHY MOMENTUM LOSSES?')
print('=' * 70)

print('\n1. DIRECTION:')
for direction in sorted(dir_stats.keys()):
    w = dir_stats[direction]['WIN']
    l = dir_stats[direction]['LOSS']
    total = w + l
    wr = w / total * 100 if total > 0 else 0
    status_icon = 'OK' if wr >= 50 else 'BAD'
    print(f'   - {direction}: {wr:.1f}% WR ({w}W/{l}L) [{status_icon}]')

print('\n2. M5 STATE:')
for state in sorted(state_stats.keys()):
    w = state_stats[state]['WIN']
    l = state_stats[state]['LOSS']
    total = w + l
    wr = w / total * 100 if total > 0 else 0
    status_icon = 'OK' if wr >= 50 else 'BAD'
    print(f'   - {state}: {wr:.1f}% WR ({w}W/{l}L) [{status_icon}]')

print('\n3. TOP BREAKDOWN DIFFERENTIATORS:')
for i, key in enumerate(sorted(component_analysis.keys(), key=lambda x: abs(component_analysis[x]['diff']), reverse=True)[:5]):
    data = component_analysis[key]
    print(f'   - {key}: WIN avg={data["win"]:.2f}, LOSS avg={data["loss"]:.2f} (diff={data["diff"]:+.2f})')

conn.close()

print('\n' + '=' * 70)
print('ANALYSIS COMPLETE')
print('=' * 70)
