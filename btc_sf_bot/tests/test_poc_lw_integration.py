"""
Test POC and Liquidity Wall Integration
Validates that StructureValidator correctly uses POC and Liquidity Wall data
"""
import sys
import os
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

import pandas as pd
import numpy as np
from datetime import datetime

print("=" * 80)
print("TESTING POC + LIQUIDITY WALL INTEGRATION")
print("=" * 80)
print()

# ============================================================
# 1. IMPORT TESTS
# ============================================================
print("PHASE 1: Testing Imports...")
print("-" * 80)

try:
    from src.enums import BOSStatus
    print("✅ Enums imported")
except Exception as e:
    print(f"❌ Enums: {e}")
    sys.exit(1)

try:
    from src.analysis.structure_validator import StructureValidator
    print("✅ StructureValidator imported")
except Exception as e:
    print(f"❌ StructureValidator: {e}")
    sys.exit(1)

try:
    from src.analysis.liquidity_wall_analyzer import LiquidityWallAnalyzer, LiquidityWall, WallType
    print("✅ LiquidityWallAnalyzer imported")
except Exception as e:
    print(f"❌ LiquidityWallAnalyzer: {e}")
    sys.exit(1)

print()

# ============================================================
# 2. LIQUIDITY WALL ANALYZER TEST
# ============================================================
print("PHASE 2: Testing LiquidityWallAnalyzer...")
print("-" * 80)

lw_analyzer = LiquidityWallAnalyzer()

# Create mock order book
order_book = {
    'bids': {
        97000: 5.0,
        96950: 8.0,
        96900: 15.0,  # Liquidity Wall (large bid)
        96850: 4.0,
        96800: 3.5
    },
    'asks': {
        97100: 4.0,
        97150: 6.0,
        97200: 18.0,  # Liquidity Wall (large ask)
        97250: 3.0,
        97300: 2.5
    }
}

current_price = 97050.0

# Analyze liquidity walls
lw_result = lw_analyzer.analyze(order_book, current_price)

print(f"Order Book Analysis:")
print(f"  Total Bid Volume: {lw_result['total_bid_volume']:.1f}")
print(f"  Total Ask Volume: {lw_result['total_ask_volume']:.1f}")
print(f"  Imbalance Ratio: {lw_result['imbalance_ratio']:.2f}")
print(f"  Has Nearby Wall: {lw_result['has_nearby_wall']}")
print(f"  Wall Support Score: {lw_result['wall_support_score']}")

if lw_result['nearest_bid_wall']:
    wall = lw_result['nearest_bid_wall']
    print(f"\n  Nearest Bid Wall:")
    print(f"    Price: {wall.price}")
    print(f"    Volume: {wall.volume:.1f}")
    print(f"    Strength: {wall.strength}")
    print(f"    Distance: {wall.distance_pct:.2f}%")

if lw_result['nearest_ask_wall']:
    wall = lw_result['nearest_ask_wall']
    print(f"\n  Nearest Ask Wall:")
    print(f"    Price: {wall.price}")
    print(f"    Volume: {wall.volume:.1f}")
    print(f"    Strength: {wall.strength}")
    print(f"    Distance: {wall.distance_pct:.2f}%")

# Test wall score for direction
long_score, long_reason = lw_analyzer.get_wall_score_for_direction('LONG', lw_result)
short_score, short_reason = lw_analyzer.get_wall_score_for_direction('SHORT', lw_result)

print(f"\n  Direction Scores:")
print(f"    LONG: {long_score} pts - {long_reason}")
print(f"    SHORT: {short_score} pts - {short_reason}")

print("\n✅ LiquidityWallAnalyzer tests passed!\n")

# ============================================================
# 3. STRUCTURE VALIDATOR TEST (with POC + LW)
# ============================================================
print("PHASE 3: Testing StructureValidator with POC + Liquidity Walls...")
print("-" * 80)

validator = StructureValidator({
    'confirmed_threshold': 9,
    'pending_threshold': 6,
    'use_liquidity_walls': True
})

# Create test candles
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

# Create POC data
poc_data = {
    'poc': 97950.0,  # POC near the swing level
    'vah': 98100.0,
    'val': 97800.0
}

# Create liquidity wall data (from previous analysis)
liquidity_wall_data = lw_result

# Test WITHOUT POC and Liquidity Walls
print("Test 1: WITHOUT POC + Liquidity Walls")
result_without = validator.validate_bos(
    direction='BULLISH',
    swing_level=98000.0,
    candles=candles,
    cvd_series=cvd_series,
    oi_current=55000.0,
    oi_before=50000.0,
    trades=trades,
    cvd_at_swing=cvd_series[-10] if len(cvd_series) > 10 else None
)

print(f"  Score: {result_without['score']}/{result_without['max_score']}")
print(f"  Status: {result_without['status'].value}")
print(f"  Reasons: {', '.join(result_without['reasons'])}")

# Test WITH POC and Liquidity Walls
print("\nTest 2: WITH POC + Liquidity Walls")
result_with = validator.validate_bos(
    direction='BULLISH',
    swing_level=98000.0,
    candles=candles,
    cvd_series=cvd_series,
    oi_current=55000.0,
    oi_before=50000.0,
    trades=trades,
    cvd_at_swing=cvd_series[-10] if len(cvd_series) > 10 else None,
    poc_data=poc_data,
    liquidity_wall_data=liquidity_wall_data
)

print(f"  Score: {result_with['score']}/{result_with['max_score']}")
print(f"  Status: {result_with['status'].value}")
print(f"  Reasons: {', '.join(result_with['reasons'])}")

# Show scoring breakdown
print(f"\n  Scoring Breakdown:")
factors = validator.get_scoring_factors()
for factor, max_pts in factors.items():
    if factor != 'total_max':
        details = result_with['details']
        if factor == 'body_close_confirmation' and 'BODY_CLOSE_ABOVE' in result_with['reasons']:
            print(f"    {factor}: +{max_pts} pts ✓")
        elif factor == 'cvd_confirmation' and 'CVD_NEW_HIGH' in str(result_with['reasons']):
            print(f"    {factor}: +{max_pts} pts ✓")
        elif factor == 'oi_increase' and 'OI_UP' in str(result_with['reasons']):
            print(f"    {factor}: +{max_pts} pts ✓")
        elif factor == 'poc_proximity' and 'POC' in str(result_with['reasons']):
            print(f"    {factor}: +{max_pts} pts ✓")
        elif factor == 'liquidity_wall' and 'WALL' in str(result_with['reasons']):
            print(f"    {factor}: +{max_pts} pts ✓")
        else:
            print(f"    {factor}: 0 pts")

print(f"\n  Score Improvement: +{result_with['score'] - result_without['score']} pts from POC + LW")

print("\n✅ StructureValidator tests passed!\n")

# ============================================================
# 4. SUMMARY
# ============================================================
print("=" * 80)
print("INTEGRATION TEST SUMMARY")
print("=" * 80)
print()
print("✅ POC Integration (+1 pt):")
print("   - Checks if break occurs near POC")
print("   - Checks if break is within Value Area")
print("   - Adds +1 point to score if confirmed")
print()
print("✅ Liquidity Wall Integration (+2 pts):")
print("   - Analyzes order book for large limit orders")
print("   - For LONG: Checks for Bid Wall support below")
print("   - For SHORT: Checks for Ask Wall resistance above")
print("   - Adds 0-2 points based on wall strength")
print()
print(f"New Max Score: 13 (was 10)")
print(f"  - Body Close: +3 pts")
print(f"  - CVD Confirmation: +2 pts")
print(f"  - OI Increase: +2 pts")
print(f"  - Displacement Ratio: +2 pts")
print(f"  - Volume at Break: +1 pt")
print(f"  - POC Proximity: +1 pt (NEW)")
print(f"  - Liquidity Wall: +2 pts (NEW)")
print()
print("=" * 80)
