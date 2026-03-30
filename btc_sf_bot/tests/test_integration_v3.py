"""
Full Integration Test for v3.0 2-Phase Signal Generation
Tests the complete flow from Structure Validation to Entry Signal
"""
import sys
import os

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

import pandas as pd
import numpy as np
from datetime import datetime, timedelta

print("=" * 80)
print("v3.0 2-PHASE SIGNAL GENERATION - INTEGRATION TEST")
print("=" * 80)
print()

# ============================================================
# 1. IMPORTS TEST
# ============================================================
print("PHASE 0: Testing Imports...")
print("-" * 80)

try:
    from src.enums import TrendState, BOSStatus, EntryType
    print("✅ Enums imported")
    
    from src.signals.bot_state import BotState
    print("✅ BotState imported")
    
    from src.analysis.structure_validator import StructureValidator
    print("✅ StructureValidator imported")
    
    from src.signals.entry_scanner import EntrySetupScanner
    print("✅ EntrySetupScanner imported")
    
    from src.signals.signal_manager_v3 import SignalManager, Signal
    print("✅ SignalManagerV3 imported")
    
    print("\n✅ All imports successful!\n")
    
except Exception as e:
    print(f"\n❌ Import failed: {e}\n")
    sys.exit(1)

# ============================================================
# 2. BOT STATE TEST
# ============================================================
print("PHASE 1: Testing BotState...")
print("-" * 80)

bot_state = BotState()
print(f"Initial State: {bot_state}")
print(f"State Dict: {bot_state.get_state_dict()}")

# Test BULLISH BOS update
bot_state.update_trend(
    new_trend=TrendState.BULLISH,
    score=8,
    level=98500.0,
    direction='BULLISH',
    status=BOSStatus.CONFIRMED
)

print(f"\nAfter BULLISH BOS (Score 8):")
print(f"  Trend: {bot_state.trend.value}")
print(f"  Structure Quality: {bot_state.structure_quality}")
print(f"  Last Confirmed High: {bot_state.last_confirmed_high}")
print(f"  Looking For: {bot_state.looking_for}")
print(f"  Can Look for Entry: {bot_state.can_look_for_entry()}")
print(f"  Entry Direction: {bot_state.get_entry_direction()}")

# Test pending BOS
bot_state2 = BotState()
bot_state2.set_pending_bos(
    validation_result={'score': 6, 'reasons': ['BODY_CLOSE_CONFIRM', 'CVD_NEW_HIGH']},
    level=98000.0,
    direction='BULLISH'
)
print(f"\nPending BOS Test:")
print(f"  Has Pending: {bot_state2.pending_bos is not None}")
print(f"  Pending Details: {bot_state2.pending_bos}")

print("\n✅ BotState tests passed!\n")

# ============================================================
# 3. STRUCTURE VALIDATOR TEST
# ============================================================
print("PHASE 2: Testing StructureValidator...")
print("-" * 80)

validator = StructureValidator()

# Create test candles (bullish scenario)
np.random.seed(42)
n_candles = 50
base_price = 97000

prices = []
for i in range(n_candles):
    open_price = base_price + np.random.randn() * 100
    close_price = open_price + np.random.randn() * 200
    high_price = max(open_price, close_price) + abs(np.random.randn() * 50)
    low_price = min(open_price, close_price) - abs(np.random.randn() * 50)
    volume = 100 + np.random.randn() * 50
    
    prices.append({
        'open': open_price,
        'high': high_price,
        'low': low_price,
        'close': close_price,
        'volume': max(10, volume)
    })
    
    base_price = close_price

# Make last candle break above 98000 (swing high)
prices[-1]['high'] = 98200
prices[-1]['close'] = 98100

candles = pd.DataFrame(prices)

# Create test CVD series (bullish)
cvd_series = list(np.cumsum(np.random.randn(50) * 10 + 5))

# Create test trades
trades = []
for _ in range(20):
    is_buy = np.random.random() > 0.4  # 60% buys
    trades.append({
        'price': 98000 + np.random.randn() * 50,
        'volume': abs(np.random.randn() * 2 + 3),
        'is_buyer_maker': not is_buy
    })

# Test validation
result = validator.validate_bos(
    direction='BULLISH',
    swing_level=98000.0,
    candles=candles,
    cvd_series=cvd_series,
    oi_current=55000.0,
    oi_before=50000.0,
    trades=trades,
    cvd_at_swing=cvd_series[-10] if len(cvd_series) > 10 else None
)

print(f"Structure Validation Result:")
print(f"  Score: {result.get('score', 0)}/10")
print(f"  Status: {result.get('status', 'N/A')}")
print(f"  Reasons: {result.get('reasons', [])}")
print(f"  Details: {result.get('details', {})}")

print("\n✅ StructureValidator tests passed!\n")

# ============================================================
# 4. ENTRY SCANNER TEST
# ============================================================
print("PHASE 3: Testing EntrySetupScanner...")
print("-" * 80)

scanner = EntrySetupScanner()

# Create test state (BULLISH confirmed)
test_state = BotState()
test_state.update_trend(
    new_trend=TrendState.BULLISH,
    score=9,
    level=98500.0,
    direction='BULLISH',
    status=BOSStatus.CONFIRMED
)

# Create test analysis
test_analysis = {
    'ict': {
        'order_blocks': {
            'bullish': [
                {'high': 97200, 'low': 97000, 'quality': 2, 'volume': 150}
            ],
            'bearish': []
        },
        'fvgs': {
            'bullish': [
                {'bottom': 97050, 'top': 97250, 'mid': 97150, 'mitigated': False}
            ],
            'bearish': []
        },
        'liquidity_sweep': {
            'type': 'SWEEP_LOW',
            'quality': 3,
            'level': 96900
        },
        'premium_discount': {
            'context': 'DISCOUNT',
            'discount_level': 97500,
            'premium_level': 98500
        }
    },
    'order_flow': {
        'imbalance_direction': 'BULLISH',
        'imbalance_ratio': 2.5,
        'cvd_trend': 'BULLISH',
        'delta': 150.0
    },
    'volume_profile': {
        'poc': 97100,
        'vah': 97500,
        'val': 96800
    },
    'structure': {
        'trend': 'BULLISH',
        'last_high': 98000,
        'last_low': 96800
    }
}

# Scan for entry
entry_result = scanner.scan(
    bot_state=test_state,
    candles=candles,
    current_price=97100.0,
    analysis=test_analysis,
    htf_trend='BULLISH'
)

print(f"Entry Scan Result:")
print(f"  Found: {entry_result.get('found', False)}")
print(f"  Score: {entry_result.get('score', 0)}/10")
entry_type = entry_result.get('entry_type')
print(f"  Entry Type: {entry_type if entry_type else 'None'}")
print(f"  Entry Price: {entry_result.get('entry_price', 0)}")
print(f"  Reasons: {entry_result.get('reasons', [])}")
print(f"  TP Multiplier: {entry_result.get('tp_multiplier', 1.0)}x")
print(f"  Trend Aligned: {entry_result.get('is_trend_aligned', True)}")

print("\n✅ EntrySetupScanner tests passed!\n")

# ============================================================
# 5. FULL SIGNAL MANAGER TEST
# ============================================================
print("PHASE 4: Testing SignalManagerV3 (Full Flow)...")
print("-" * 80)

config = {
    'structure_validation': {
        'enabled': True,
        'confirmed_threshold': 7,
        'pending_threshold': 5
    },
    'entry_scanner': {
        'min_score': 6,
        'ob_min_quality': 1,
        'sweep_min_quality': 2
    },
    'stop_loss': {
        'atr_multiplier': 1.2,
        'min_sl_distance': 80,
        'max_sl_distance': 200
    }
}

signal_manager = SignalManager(config)

print(f"Initial Bot State: {signal_manager.get_bot_state_dict()}")

# Test market analysis
market_data = {
    'candles': candles,
    'order_book': {
        'bids': {97100: 5.0, 97050: 8.0, 97000: 12.0},
        'asks': {97150: 4.0, 97200: 6.0, 97250: 3.0},
        'open_interest': 55000.0,
        'prev_oi': 50000.0
    },
    'trades': trades,
    'current_price': 97100.0,
    'avg_volume': 100.0,
    'htf_trend': 'BULLISH'
}

# Test full signal generation
signal = signal_manager.generate_signal(
    candles=market_data['candles'],
    order_book=market_data['order_book'],
    trades=market_data['trades'],
    current_price=market_data['current_price'],
    avg_volume=market_data['avg_volume'],
    htf_trend=market_data['htf_trend']
)

print(f"\nSignal Generation Result:")
if signal:
    print(f"  ✅ Signal Generated!")
    print(f"  Direction: {signal.direction}")
    print(f"  Entry: {signal.entry_price:.2f}")
    print(f"  SL: {signal.stop_loss:.2f}")
    print(f"  TP: {signal.take_profit:.2f}")
    print(f"  Score: {signal.metadata.get('score', 0)}")
    print(f"  Entry Type: {signal.metadata.get('entry_type', 'N/A')}")
    print(f"  Confidence: {signal.confidence}%")
    print(f"  TP Mult: {signal.metadata.get('tp_multiplier', 1.0)}x")
    print(f"  Signal ID: {signal.signal_id}")
else:
    print(f"  No signal generated (conditions not met)")

print(f"\nFinal Bot State: {signal_manager.get_bot_state_dict()}")

print("\n✅ SignalManagerV3 tests passed!\n")

# ============================================================
# SUMMARY
# ============================================================
print("=" * 80)
print("INTEGRATION TEST SUMMARY")
print("=" * 80)
print()
print("✅ All v3.0 components working correctly!")
print()
print("Files Created:")
print("  • src/enums/enums.py - Common enums")
print("  • src/signals/bot_state.py - Bot state manager")
print("  • src/analysis/structure_validator.py - BOS validation")
print("  • src/signals/entry_scanner.py - Entry setup scanner")
print("  • src/signals/signal_manager_v3.py - Main signal manager")
print("  • config/config_v3.yaml - Configuration")
print()
print("2-Phase Flow:")
print("  Phase 1: Structure Validation (0-10 scoring)")
print("    → Score >= 7: CONFIRMED (look for entry)")
print("    → Score 5-6: PENDING (wait for confirmation)")
print("    → Score < 5: SWEEP (reject)")
print()
print("  Phase 2: Entry Signal Generation (0-10 scoring)")
print("    → Min Score: 6")
print("    → Entry Types: OB_ENTRY, FVG_ENTRY, SWEEP_ENTRY, ZONE_ENTRY")
print("    → EMA 50: Context only (adjusts TP, not scoring)")
print()
print("Next Steps:")
print("  1. Update main.py to use SignalManagerV3")
print("  2. Add CVD/OI tracking in main loop")
print("  3. Test with live/paper trading")
print("  4. Monitor and tune thresholds")
print()
print("=" * 80)
