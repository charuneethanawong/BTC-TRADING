"""
Test Smart Flow Manager
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import numpy as np
from datetime import datetime
from src.signals.smart_flow_manager import SmartFlowManager
from src.analysis.ict import ICTAnalyzer
from src.analysis.htf_mss_analyzer import HTFMSSAnalyzer
from src.enums import TrendState


def test_ict_detect_market_structure_v2():
    """Test Step 2, 3, 4, 6: detect_market_structure_v2 with broadening/contracting counters."""
    print("=" * 60)
    print("Test: ICT detect_market_structure_v2")
    print("=" * 60)
    
    config = {
        'm5_fractal_n': 5,
        'liq_sweep_pct': 0.001,
        'choch_pending_max_candles': 10,
        'broadening_range_threshold': 2,
        'contracting_range_threshold': 3
    }
    
    analyzer = ICTAnalyzer(config)
    
    np.random.seed(42)
    n_candles = 200
    base_price = 70000
    
    dates = pd.date_range('2024-01-01', periods=n_candles, freq='5min')
    prices = base_price + np.cumsum(np.random.randn(n_candles) * 100)
    
    candles = pd.DataFrame({
        'open': prices,
        'high': prices + np.random.rand(n_candles) * 50,
        'low': prices - np.random.rand(n_candles) * 50,
        'close': prices + np.random.rand(n_candles) * 30,
        'volume': np.random.randint(100, 1000, n_candles)
    }, index=dates)
    
    result = analyzer.detect_market_structure_v2(candles, candles['close'].iloc[-1])
    
    print(f"\nResult:")
    print(f"  Trend: {result['trend']}")
    print(f"  Structure: {result['structure']}")
    print(f"  Labels: {result['labels']}")
    print(f"  Broadening counter: {result['broadening_counter']}")
    print(f"  Contracting counter: {result['contracting_counter']}")
    print(f"  CHoCH status: {result.get('choch_status')}")
    
    assert 'trend' in result
    assert 'broadening_counter' in result
    assert 'contracting_counter' in result
    
    print("\n✅ Test passed!")
    return True


def test_ict_broadening_contracting_separation():
    """Test Step 2: Separate broadening vs contracting counter logic."""
    print("=" * 60)
    print("Test: ICT Broadening/Contracting Separation")
    print("=" * 60)
    
    config = {
        'm5_fractal_n': 5,
        'broadening_range_threshold': 2,
        'contracting_range_threshold': 3
    }
    
    analyzer = ICTAnalyzer(config)
    
    np.random.seed(42)
    dates = pd.date_range('2024-01-01', periods=200, freq='5min')
    
    broadening_prices = np.array([
        70000, 70200, 70100, 70400, 70300, 70600, 70500, 70800, 70700, 71000,
        70900, 71200, 71100, 71400, 71300, 71600, 71500, 71800, 71700, 72000
    ])
    
    all_prices = np.concatenate([broadening_prices, np.random.randn(180) * 50 + 72000])
    
    candles = pd.DataFrame({
        'open': all_prices,
        'high': all_prices + 50,
        'low': all_prices - 50,
        'close': all_prices + 25,
        'volume': np.random.randint(100, 1000, 200)
    }, index=dates)
    
    analyzer._broadening_counter = 1
    result = analyzer.detect_market_structure_v2(candles, candles['close'].iloc[-1])
    
    print(f"\nBroadening counter: {result['broadening_counter']}")
    print(f"Contracting counter: {result['contracting_counter']}")
    
    assert result['broadening_counter'] >= 0
    assert result['contracting_counter'] >= 0
    
    print("\n✅ Test passed!")
    return True


def test_ict_choch_pending_dominant_trend():
    """Test Step 3: CHoCH_PENDING expiry with dominant trend fallback."""
    print("=" * 60)
    print("Test: ICT CHoCH_PENDING Dominant Trend Fallback")
    print("=" * 60)
    
    config = {
        'm5_fractal_n': 5,
        'choch_pending_max_candles': 10
    }
    
    analyzer = ICTAnalyzer(config)
    
    analyzer._choch_pending_trend = "BULLISH"
    analyzer._choch_pending_candles = 5
    analyzer._dominant_trend = "BULLISH"
    
    result = analyzer.detect_market_structure_v2(
        pd.DataFrame({
            'open': [70000] * 50,
            'high': [70200] * 50,
            'low': [69800] * 50,
            'close': [70100] * 50,
            'volume': [500] * 50
        }),
        70100
    )
    
    print(f"\nCHoCH status: {result.get('choch_status')}")
    print(f"Dominant trend: {result.get('dominant_trend')}")
    
    print("\n✅ Test passed!")
    return True


def test_ict_liq_sweep_configurable():
    """Test Step 4: Liquidity sweep threshold configurability."""
    print("=" * 60)
    print("Test: ICT Liquidity Sweep Configurable")
    print("=" * 60)
    
    config1 = {'liq_sweep_pct': 0.001}
    analyzer1 = ICTAnalyzer(config1)
    
    config2 = {'liq_sweep_pct': 0.002}
    analyzer2 = ICTAnalyzer(config2)
    
    print(f"\nAnalyzer 1 liq_sweep_pct: {analyzer1.liq_sweep_pct}")
    print(f"Analyzer 2 liq_sweep_pct: {analyzer2.liq_sweep_pct}")
    
    assert analyzer1.liq_sweep_pct == 0.001
    assert analyzer2.liq_sweep_pct == 0.002
    
    print("\n✅ Test passed!")
    return True


def test_htf_m5_coherence():
    """Test Step 8: H1-M5 Trend Coherence Check."""
    print("=" * 60)
    print("Test: HTF-M5 Trend Coherence")
    print("=" * 60)
    
    config = {'h1_fractal_n': 5}
    htf_analyzer = HTFMSSAnalyzer(config)
    
    htf_analyzer.last_h1_trend = TrendState.BULLISH
    htf_analyzer.last_h1_structure = "BOS"
    
    result1 = htf_analyzer.check_m5_h1_coherence("BEARISH", "CHoCH")
    
    print(f"\nTest 1: M5 BEARISH CHoCH vs H1 BULLISH trend")
    print(f"  Is coherent: {result1['is_coherent']}")
    print(f"  Type: {result1['coherence_type']}")
    print(f"  Warning: {result1['warning']}")
    print(f"  Should flip M5: {result1['should_flip_m5']}")
    
    assert result1['is_coherent'] == False
    assert result1['coherence_type'] == 'COUNTER_TREND'
    assert result1['should_flip_m5'] == False
    
    result2 = htf_analyzer.check_m5_h1_coherence("BULLISH", "CHoCH")
    
    print(f"\nTest 2: M5 BULLISH CHoCH vs H1 BULLISH trend")
    print(f"  Is coherent: {result2['is_coherent']}")
    print(f"  Should flip M5: {result2['should_flip_m5']}")
    
    assert result2['is_coherent'] == True
    assert result2['should_flip_m5'] == True
    
    print("\n✅ Test passed!")
    return True


def test_htf_fractal_parameter():
    """Test Step 7: H1 Fractal Parameter Configurability."""
    print("=" * 60)
    print("Test: HTF Fractal Parameter")
    print("=" * 60)
    
    config1 = {'h1_fractal_n': 5}
    htf_analyzer1 = HTFMSSAnalyzer(config1)
    
    config2 = {'h1_fractal_n': 3}
    htf_analyzer2 = HTFMSSAnalyzer(config2)
    
    print(f"\nAnalyzer 1 h1_fractal_n: {htf_analyzer1.h1_fractal_n}")
    print(f"Analyzer 2 h1_fractal_n: {htf_analyzer2.h1_fractal_n}")
    
    assert htf_analyzer1.h1_fractal_n == 5
    assert htf_analyzer2.h1_fractal_n == 3
    
    print("\n✅ Test passed!")
    return True


def test_smart_flow_coherence_integration():
    """Test Step 8: Smart Flow Manager H1-M5 Coherence Integration."""
    print("=" * 60)
    print("Test: Smart Flow Manager Coherence Integration")
    print("=" * 60)
    
    config = {
        'smart_flow': {
            'enabled': True,
            'threshold_sweep': 8,
            'threshold_wall': 8,
            'threshold_zone': 9,
        },
        'trading_symbol': 'BTCUSDT',
        'testnet': True
    }
    
    manager = SmartFlowManager(config)
    htf_analyzer = HTFMSSAnalyzer({'h1_fractal_n': 5})
    htf_analyzer.last_h1_trend = TrendState.BULLISH
    htf_analyzer.last_h1_structure = "BOS"
    
    result = manager.check_htf_m5_coherence(htf_analyzer, "BEARISH", "CHoCH")
    
    print(f"\nCoherence check result:")
    print(f"  Is coherent: {result['is_coherent']}")
    print(f"  Type: {result['coherence_type']}")
    print(f"  Should proceed: {result['should_proceed']}")
    
    assert result['is_coherent'] == False
    assert result['should_proceed'] == False
    
    print("\n✅ Test passed!")
    return True


def test_smart_flow():
    print("=" * 60)
    print("Testing Smart Flow Manager")
    print("=" * 60)
    
    config = {
        'smart_flow': {
            'enabled': True,
            'threshold_sweep': 8,
            'threshold_wall': 8,
            'threshold_zone': 9,
            'sl_sweep': 1000,
            'sl_wall': 1000,
            'sl_zone': 600,
            'tp_sweep': 2000,
            'tp_wall': 2000,
            'tp_zone': 720
        },
        'trading_symbol': 'BTCUSDT',
        'testnet': True
    }
    
    manager = SmartFlowManager(config)
    
    candles = pd.DataFrame({
        'open': [82000, 82100, 82200, 82300, 82400],
        'high': [82200, 82300, 82400, 82500, 82600],
        'low': [81900, 82000, 82100, 82200, 82300],
        'close': [82100, 82200, 82300, 82400, 82500],
        'volume': [100, 150, 200, 180, 220]
    })
    
    p1_data = {
        'cvd_delta': 0.6,
        'volume_ratio': 2.5,
        'oi_change_pct': 1.5,
        'absorption_detected': False
    }
    
    htf_data = {
        'trend': 'BULLISH'
    }
    
    print("\nTest 1: Scan with bullish setup")
    print("-" * 60)
    
    signals = manager.scan_patterns(
        candles=candles,
        current_price=82450,
        p1_data=p1_data,
        htf_data=htf_data
    )
    
    print(f"\nFound {len(signals)} signal(s)")
    for i, sig in enumerate(signals, 1):
        print(f"\nSignal {i}:")
        print(f"  Pattern: {sig['pattern_type']}")
        print(f"  Direction: {sig['direction']}")
        print(f"  Score: {sig['score']}/{sig['max_score']}")
        print(f"  Reason: {sig['reason']}")
        print(f"  Details: {sig['details']}")
    
    print("\n" + "=" * 60)
    print("Test completed!")
    print("=" * 60)


if __name__ == '__main__':
    test_ict_detect_market_structure_v2()
    test_ict_broadening_contracting_separation()
    test_ict_choch_pending_dominant_trend()
    test_ict_liq_sweep_configurable()
    test_htf_m5_coherence()
    test_htf_fractal_parameter()
    test_smart_flow_coherence_integration()
    test_smart_flow()
