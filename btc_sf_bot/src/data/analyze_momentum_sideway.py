import sqlite3
from collections import defaultdict

conn = sqlite3.connect('D:/CODING WORKS/SMC_AI_Project/btc_sf_bot/data/trades.db')
cursor = conn.cursor()

print('=' * 70)
print('MOMENTUM IN SIDEWAY ANALYSIS')
print('=' * 70)

# 1. Get all trades with their regime and signal_type (JOIN outcomes with telemetry)
cursor.execute('''
    SELECT s.regime, o.signal_type, o.result, COUNT(*) as cnt
    FROM trade_outcomes o
    JOIN signal_telemetry s ON o.signal_id = s.signal_id
    WHERE o.result IN ('WIN', 'LOSS')
    GROUP BY s.regime, o.signal_type, o.result
    ORDER BY s.regime, o.signal_type
''')

print('\n=== TRADES BY REGIME + SIGNAL TYPE ===')
regime_signal = defaultdict(lambda: {'WIN': 0, 'LOSS': 0})
for regime, stype, result, cnt in cursor.fetchall():
    key = f"{regime}|{stype}"
    regime_signal[key][result] = cnt

print(f'\n{"Regime":<12} | {"Signal Type":<15} | {"WIN":>5} | {"LOSS":>5} | {"Total":>6} | {"WR":>6}')
print('-' * 65)
for key in sorted(regime_signal.keys()):
    wins = regime_signal[key]['WIN']
    losses = regime_signal[key]['LOSS']
    total = wins + losses
    wr = wins / total * 100 if total > 0 else 0
    regime, stype = key.split('|')
    print(f'{regime:<12} | {stype:<15} | {wins:>5} | {losses:>5} | {total:>6} | {wr:>5.1f}%')

# 2. Focus on MOMENTUM in different regimes
print('\n' + '=' * 70)
print('MOMENTUM SIGNALS BY REGIME')
print('=' * 70)

cursor.execute('''
    SELECT s.regime, o.result, COUNT(*) as cnt
    FROM trade_outcomes o
    JOIN signal_telemetry s ON o.signal_id = s.signal_id
    WHERE o.result IN ('WIN', 'LOSS') AND o.signal_type = 'MOMENTUM'
    GROUP BY s.regime, o.result
    ORDER BY s.regime
''')

momentum_by_regime = defaultdict(lambda: {'WIN': 0, 'LOSS': 0})
for regime, result, cnt in cursor.fetchall():
    momentum_by_regime[regime][result] = cnt

print(f'\n{"Regime":<12} | {"WIN":>5} | {"LOSS":>5} | {"Total":>6} | {"WR":>6}')
print('-' * 45)
for regime in sorted(momentum_by_regime.keys()):
    wins = momentum_by_regime[regime]['WIN']
    losses = momentum_by_regime[regime]['LOSS']
    total = wins + losses
    wr = wins / total * 100 if total > 0 else 0
    print(f'{regime:<12} | {wins:>5} | {losses:>5} | {total:>6} | {wr:>5.1f}%')

# 3. Compare: Should we block MOMENTUM in RANGING/CHOPPY?
print('\n' + '=' * 70)
print('SHOULD WE BLOCK MOMENTUM IN SIDEWAY?')
print('=' * 70)

trending_regimes = ['TRENDING', 'VOLATILE', 'WEAKENING']
sideway_regimes = ['RANGING', 'CHOPPY', 'DEAD']

trending_wins = sum(momentum_by_regime[r].get('WIN', 0) for r in trending_regimes if r in momentum_by_regime)
trending_losses = sum(momentum_by_regime[r].get('LOSS', 0) for r in trending_regimes if r in momentum_by_regime)
sideway_wins = sum(momentum_by_regime[r].get('WIN', 0) for r in sideway_regimes if r in momentum_by_regime)
sideway_losses = sum(momentum_by_regime[r].get('LOSS', 0) for r in sideway_regimes if r in momentum_by_regime)

trending_total = trending_wins + trending_losses
sideway_total = sideway_wins + sideway_losses

trending_wr = trending_wins / trending_total * 100 if trending_total > 0 else 0
sideway_wr = sideway_wins / sideway_total * 100 if sideway_total > 0 else 0

print(f'\nTRENDING regimes (TRENDING/VOLATILE): {trending_wins}W/{trending_losses}L = {trending_wr:.1f}% WR ({trending_total} trades)')
print(f'SIDEWAY regimes (RANGING/CHOPPY):      {sideway_wins}W/{sideway_losses}L = {sideway_wr:.1f}% WR ({sideway_total} trades)')

if sideway_wr < trending_wr:
    diff = trending_wr - sideway_wr
    print(f'\nYES - Block MOMENTUM in SIDEWAY: WR is {diff:.1f}% lower')
else:
    print(f'\nNO - MOMENTUM works in both regimes equally')

# 4. Check gate blocks for MOMENTUM in sideway
print('\n' + '=' * 70)
print('GATE BLOCKS: MOMENTUM IN SIDEWAY REGIMES')
print('=' * 70)

cursor.execute('''
    SELECT gate_reason, regime, COUNT(*) as cnt
    FROM gate_blocks
    WHERE signal_type = 'MOMENTUM' AND regime IN ('RANGING', 'CHOPPY')
    GROUP BY gate_reason, regime
    ORDER BY cnt DESC
    LIMIT 15
''')

print(f'\n{"Gate Reason":<45} | {"Regime":<10} | {"Count":>5}')
print('-' * 65)
for reason, regime, cnt in cursor.fetchall():
    print(f'{reason:<45} | {regime:<10} | {cnt:>5}')

# 5. Total MOMENTUM blocks by regime
print('\n' + '=' * 70)
print('TOTAL MOMENTUM GATE BLOCKS BY REGIME')
print('=' * 70)

cursor.execute('''
    SELECT regime, COUNT(*) as cnt
    FROM gate_blocks
    WHERE signal_type = 'MOMENTUM'
    GROUP BY regime
    ORDER BY cnt DESC
''')

print(f'\n{"Regime":<12} | {"Blocks":>6}')
print('-' * 20)
for regime, cnt in cursor.fetchall():
    print(f'{regime:<12} | {cnt:>6}')

# 6. Overall gate blocks by regime
print('\n' + '=' * 70)
print('ALL SIGNAL GATE BLOCKS BY REGIME')
print('=' * 70)

cursor.execute('''
    SELECT regime, COUNT(*) as cnt
    FROM gate_blocks
    GROUP BY regime
    ORDER BY cnt DESC
''')

print(f'\n{"Regime":<12} | {"Blocks":>6}')
print('-' * 20)
for regime, cnt in cursor.fetchall():
    print(f'{regime:<12} | {cnt:>6}')

conn.close()

print('\n' + '=' * 70)
print('ANALYSIS COMPLETE')
print('=' * 70)
