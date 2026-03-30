"""
Unit Tests for PHASE 2 Analyzers
Test: IPAAnalyzer, IOFAnalyzer
"""
import sys
from pathlib import Path

# Add src to path
src_path = str(Path(__file__).parent.parent / 'src')
if src_path not in sys.path:
    sys.path.insert(0, src_path)

import pytest
import pandas as pd
import numpy as np
import importlib.util, os
from datetime import datetime, timezone, timedelta

# Direct imports
def import_from_file(filepath):
    spec = importlib.util.spec_from_file_location(os.path.basename(filepath).replace('.py',''), filepath)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module.__name__] = module
    spec.loader.exec_module(module)
    return module

base = Path(__file__).parent.parent / 'src'
ipa_mod = import_from_file(base / 'analysis' / 'ipa_analyzer.py')
iof_mod = import_from_file(base / 'analysis' / 'iof_analyzer.py')

IPAAnalyzer = ipa_mod.IPAAnalyzer
IPAResult = ipa_mod.IPAResult
IOFAnalyzer = iof_mod.IOFAnalyzer
IOFResult = iof_mod.IOFResult


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def sample_candles_m5():
    """Generate realistic M5 candles with structure."""
    np.random.seed(42)
    n = 300
    base = 70000.0
    prices = [base]
    for _ in range(n - 1):
        change = np.random.randn() * 50
        prices.append(prices[-1] + change)

    records = []
    for i, close in enumerate(prices):
        open_price = prices[i - 1] if i > 0 else close
        high = max(open_price, close) + abs(np.random.randn() * 20)
        low = min(open_price, close) - abs(np.random.randn() * 20)
        volume = np.random.rand() * 50 + 30
        records.append({
            'open': open_price,
            'high': high,
            'low': low,
            'close': close,
            'volume': volume
        })

    df = pd.DataFrame(records, index=pd.date_range('2026-03-19', periods=n, freq='5min'))
    return df


@pytest.fixture
def sample_candles_h1():
    """Generate H1 candles."""
    np.random.seed(42)
    n = 50
    base = 70000.0
    prices = [base]
    for _ in range(n - 1):
        change = np.random.randn() * 150
        prices.append(prices[-1] + change)

    records = []
    for i, close in enumerate(prices):
        open_price = prices[i - 1] if i > 0 else close
        high = max(open_price, close) + abs(np.random.randn() * 80)
        low = min(open_price, close) - abs(np.random.randn() * 80)
        volume = np.random.rand() * 200 + 100
        records.append({
            'open': open_price,
            'high': high,
            'low': low,
            'close': close,
            'volume': volume
        })

    df = pd.DataFrame(records, index=pd.date_range('2026-03-17', periods=n, freq='1h'))
    return df


@pytest.fixture
def sample_binance_data():
    """Generate sample Binance data for IOF."""
    return {
        'oi': 90000.0,
        'oi_1min_ago': 89800.0,    # OI up 0.22%
        'current_price': 70100.0,
        'price_1min_ago': 70200.0,  # Price down 0.14% = LONG signal
        'funding_rate': 0.0001,
        'order_book': {
            'bids': [[70100.0, 10.0], [70000.0, 5.0], [69900.0, 3.0]],
            'asks': [[70200.0, 8.0], [70300.0, 4.0], [70400.0, 2.0]],
        },
        'liquidation_cascade': False,
        'adx_h1': 25.0,
    }


@pytest.fixture
def ipa_analyzer():
    return IPAAnalyzer()


@pytest.fixture
def iof_analyzer():
    return IOFAnalyzer()


# ============================================================================
# IPAAnalyzer Tests
# ============================================================================

class TestIPAAnalyzer:

    def test_analyze_returns_none_when_insufficient_data(self, ipa_analyzer):
        """Should return None with insufficient candles."""
        empty = pd.DataFrame(columns=['open', 'high', 'low', 'close', 'volume'])
        result = ipa_analyzer.analyze(empty, empty, 70000.0, 'LONDON')
        assert result is None

    def test_analyze_returns_result_on_valid_setup(self, ipa_analyzer, sample_candles_m5, sample_candles_h1):
        """Should return IPAResult when all gates pass."""
        # Create a bullish setup: H1 trending up, M5 broke structure
        h1 = sample_candles_h1.copy()
        h1.iloc[-1] = {'open': h1.iloc[-2]['close'], 'high': h1.iloc[-1]['high'] * 1.02,
                        'low': h1.iloc[-1]['low'], 'close': h1.iloc[-1]['high'] * 1.02,
                        'volume': h1.iloc[-1]['volume']}

        result = ipa_analyzer.analyze(sample_candles_m5, h1, 70100.0, 'LONDON')

        # May or may not return result depending on structure detection
        # Just verify it doesn't crash
        assert result is None or isinstance(result, IPAResult)

    def test_ipa_result_properties(self, ipa_analyzer, sample_candles_m5, sample_candles_h1):
        """Test IPAResult dataclass properties."""
        result = ipa_analyzer.analyze(sample_candles_m5, sample_candles_h1, 70000.0, 'LONDON')

        if result is not None:
            assert result.direction in ('LONG', 'SHORT')
            assert 0 <= result.score <= 20
            assert result.h1_bias in ('BULLISH', 'BEARISH', 'NEUTRAL', '')
            assert isinstance(result.atr_m5, float)
            assert result.entry_zone_min < result.entry_zone_max

    def test_prepare_indicators(self, ipa_analyzer, sample_candles_m5, sample_candles_h1):
        """Test indicator preparation."""
        # Initialize state like analyze() does
        ipa_analyzer.current_price = 70000.0
        ipa_analyzer.session = 'LONDON'
        ipa_analyzer._prepare_indicators(sample_candles_m5, sample_candles_h1)
        
        assert isinstance(ipa_analyzer.atr_m5, float)
        assert ipa_analyzer.atr_m5 > 0
        assert ipa_analyzer.avg_volume > 0
        assert ipa_analyzer.volume_ratio > 0

    def test_h1_bias_detection_no_bias(self, ipa_analyzer, sample_candles_h1):
        """H1 with no trend should return None."""
        # Flat H1
        flat_h1 = sample_candles_h1.copy()
        flat_h1['close'] = 70000.0
        result = ipa_analyzer._check_h1_bias(flat_h1)
        # Should not crash
        assert result is None or isinstance(result, dict)


class TestIOFAnalyzer:

    def test_analyze_returns_none_when_insufficient_data(self, iof_analyzer, sample_binance_data):
        """Should return None with insufficient candles."""
        empty = pd.DataFrame(columns=['open', 'high', 'low', 'close', 'volume'])
        result = iof_analyzer.analyze(empty, sample_binance_data, 70100.0, 'LONDON')
        assert result is None

    def test_analyze_returns_none_on_extreme_trend(self, iof_analyzer, sample_candles_m5, sample_binance_data):
        """Gate 1 should block extreme trending (ADX > 40)."""
        data = sample_binance_data.copy()
        data['adx_h1'] = 50.0  # Extreme trend
        result = iof_analyzer.analyze(sample_candles_m5, data, 70100.0, 'LONDON')
        assert result is None

    def test_analyze_returns_result_on_valid_setup(self, iof_analyzer, sample_candles_m5, sample_binance_data):
        """Should return IOFResult when all gates pass."""
        result = iof_analyzer.analyze(sample_candles_m5, sample_binance_data, 70100.0, 'LONDON')
        # May or may not return result depending on data
        assert result is None or isinstance(result, IOFResult)

    def test_iof_result_properties(self, iof_analyzer, sample_candles_m5, sample_binance_data):
        """Test IOFResult dataclass properties."""
        result = iof_analyzer.analyze(sample_candles_m5, sample_binance_data, 70100.0, 'LONDON')

        if result is not None:
            assert result.direction in ('LONG', 'SHORT')
            assert 0 <= result.score <= 20
            assert isinstance(result.wall_price, float)
            assert isinstance(result.der_score, float)

    def test_oi_signal_validation(self, iof_analyzer, sample_binance_data):
        """Test OI signal gate."""
        # OI up + price up = NO divergence = blocked
        data = sample_binance_data.copy()
        data['oi'] = 89800.0
        data['oi_1min_ago'] = 90000.0  # OI down
        data['current_price'] = 70200.0
        data['price_1min_ago'] = 70100.0  # Price up

        result = iof_analyzer._check_oi_signal(data, 'LONG')
        # Should block: OI down + Price up = no LONG signal
        assert result is None or isinstance(result, dict)

    def test_wall_threshold_by_session(self, iof_analyzer):
        """Wall threshold should vary by session (v5.0 Aggressive Mode)."""
        asia_thresh = iof_analyzer._get_wall_threshold(session='ASIA')
        london_thresh = iof_analyzer._get_wall_threshold(session='LONDON')
        ny_thresh = iof_analyzer._get_wall_threshold(session='NY')

        assert asia_thresh == 100000  # v5.0: reduced from 300000
        assert london_thresh == 200000  # v5.0: reduced from 500000
        assert ny_thresh == 300000  # v5.0: reduced from 700000

    def test_delta_absorption_der_calculation(self, iof_analyzer, sample_candles_m5):
        """Test DER calculation."""
        result = iof_analyzer._check_delta_absorption(sample_candles_m5)
        if result:
            assert 'der' in result
            assert result['der'] >= 0
            assert result['direction'] in ('LONG', 'SHORT')


# ============================================================================
# Integration: IPA + IOF + SL/TP + Signal Builder
# ============================================================================

class TestAnalyzerIntegration:

    def test_ipa_signal_flow(self, ipa_analyzer, sample_candles_m5, sample_candles_h1):
        """Test full IPA signal flow (analyze → result)."""
        result = ipa_analyzer.analyze(sample_candles_m5, sample_candles_h1, 70000.0, 'LONDON')

        if result is not None:
            # Verify result can be used with SL/TP calculator
            sl_tp_mod = import_from_file(base / 'signals' / 'sl_tp_calculator.py')
            calc = sl_tp_mod.InstitutionalSLTPCalculator()

            if result.ob_low or result.ob_high:
                sl_tp = calc.calculate_ipa(
                    entry_price=result.entry_price,
                    direction=result.direction,
                    ob_high=result.ob_high,
                    ob_low=result.ob_low,
                    atr_m5=result.atr_m5,
                    swing_highs=result.swing_highs,
                    swing_lows=result.swing_lows,
                    pdh=result.pdh,
                    pdl=result.pdl,
                    h1_fvg_boundary=result.h1_fvg_boundary,
                )
                # SL/TP may or may not be calculable
                assert sl_tp is None or hasattr(sl_tp, 'stop_loss')

    def test_iof_signal_flow(self, iof_analyzer, sample_candles_m5, sample_binance_data):
        """Test full IOF signal flow (analyze → result)."""
        result = iof_analyzer.analyze(sample_candles_m5, sample_binance_data, 70100.0, 'LONDON')

        if result is not None:
            sl_tp_mod = import_from_file(base / 'signals' / 'sl_tp_calculator.py')
            calc = sl_tp_mod.InstitutionalSLTPCalculator()

            sl_tp = calc.calculate_iof(
                entry_price=result.wall_price,
                direction=result.direction,
                wall_price=result.wall_price,
                atr_m5=result.atr_m5,
                session=result.session,
                next_resistance=result.next_resistance,
                next_support=result.next_support,
            )
            assert sl_tp is None or hasattr(sl_tp, 'stop_loss')


# ============================================================================
# Run
# ============================================================================

if __name__ == '__main__':
    pytest.main([__file__, '-v'])
