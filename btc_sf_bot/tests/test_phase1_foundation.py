"""
Unit Tests for PHASE 1 Foundation Components
Test: MarketRegimeDetector, InstitutionalSLTPCalculator, SignalBuilder, SignalGate
"""
import sys
import os
from pathlib import Path

# Add src to path
src_path = str(Path(__file__).parent.parent / 'src')
if src_path not in sys.path:
    sys.path.insert(0, src_path)

import pytest
import pandas as pd
import numpy as np

# Direct import from module files (bypass __init__.py which has broken imports)
import importlib.util
from datetime import datetime, timezone, timedelta

def import_from_file(filepath):
    """Import a module directly from file path, bypassing __init__.py"""
    spec = importlib.util.spec_from_file_location(os.path.basename(filepath).replace('.py',''), filepath)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module.__name__] = module
    spec.loader.exec_module(module)
    return module

# Import PHASE 1 components directly
base = Path(__file__).parent.parent / 'src'
market_regime = import_from_file(base / 'analysis' / 'market_regime.py')
sl_tp = import_from_file(base / 'signals' / 'sl_tp_calculator.py')
signal_builder = import_from_file(base / 'signals' / 'signal_builder.py')
signal_gate_mod = import_from_file(base / 'signals' / 'signal_gate.py')

MarketRegimeDetector = market_regime.MarketRegimeDetector
RegimeResult = market_regime.RegimeResult
InstitutionalSLTPCalculator = sl_tp.InstitutionalSLTPCalculator
SLTPRESult = sl_tp.SLTPRESult
SignalBuilder = signal_builder.SignalBuilder
IPAResult = signal_builder.IPAResult
IOFResult = signal_builder.IOFResult
SignalGate = signal_gate_mod.SignalGate
GateResult = signal_gate_mod.GateResult
AccountState = signal_gate_mod.AccountState
PositionInfo = signal_gate_mod.PositionInfo


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def sample_candles_m5():
    """Generate 200 M5 candles for testing."""
    np.random.seed(42)
    n = 200
    base = 70000.0
    close = base + np.cumsum(np.random.randn(n) * 100)
    high = close + np.abs(np.random.randn(n) * 50)
    low = close - np.abs(np.random.randn(n) * 50)
    open_price = np.roll(close, 1)
    open_price[0] = close[0]
    volume = np.random.rand(n) * 100 + 50

    df = pd.DataFrame({
        'open': open_price,
        'high': high,
        'low': low,
        'close': close,
        'volume': volume
    }, index=pd.date_range('2026-03-19', periods=n, freq='5min'))
    return df


@pytest.fixture
def sample_candles_h1():
    """Generate 50 H1 candles for testing."""
    np.random.seed(42)
    n = 50
    base = 70000.0
    close = base + np.cumsum(np.random.randn(n) * 200)
    high = close + np.abs(np.random.randn(n) * 100)
    low = close - np.abs(np.random.randn(n) * 100)
    open_price = np.roll(close, 1)
    open_price[0] = close[0]
    volume = np.random.rand(n) * 500 + 200

    df = pd.DataFrame({
        'open': open_price,
        'high': high,
        'low': low,
        'close': close,
        'volume': volume
    }, index=pd.date_range('2026-03-17', periods=n, freq='1h'))
    return df


@pytest.fixture
def regime_detector():
    return MarketRegimeDetector()


@pytest.fixture
def sl_tp_calc():
    return InstitutionalSLTPCalculator()


@pytest.fixture
def signal_builder():
    return SignalBuilder()


@pytest.fixture
def signal_gate():
    return SignalGate()


# ============================================================================
# MarketRegimeDetector Tests
# ============================================================================

class TestMarketRegimeDetector:
    def test_detect_returns_regime_result(self, regime_detector, sample_candles_m5, sample_candles_h1):
        result = regime_detector.detect(sample_candles_m5, sample_candles_h1)
        assert isinstance(result, RegimeResult)
        assert result.regime in ('TRENDING', 'RANGING', 'VOLATILE', 'DEAD')
        assert 0 < result.atr_m5 < 10000
        assert 0 < result.adx_h1 < 100
        assert isinstance(result.is_ipa_suitable, bool)
        assert isinstance(result.is_iof_suitable, bool)

    def test_indicator_values_reasonable(self, regime_detector, sample_candles_m5, sample_candles_h1):
        result = regime_detector.detect(sample_candles_m5, sample_candles_h1)
        assert 10 < result.atr_m5 < 1000, f"ATR_M5 {result.atr_m5} out of reasonable range"
        assert 10 < result.atr_h1 < 10000, f"ATR_H1 {result.atr_h1} out of reasonable range"
        assert 0 < result.adx_h1 <= 100, f"ADX_H1 {result.adx_h1} out of range"
        assert 0 < result.bb_width < 10, f"BB_width {result.bb_width} out of range"

    def test_empty_candles_returns_defaults(self, regime_detector):
        empty_m5 = pd.DataFrame(columns=['open', 'high', 'low', 'close', 'volume'])
        empty_h1 = pd.DataFrame(columns=['open', 'high', 'low', 'close', 'volume'])
        result = regime_detector.detect(empty_m5, empty_h1)
        assert result.regime == 'RANGING'  # Safe default
        assert result.atr_m5 == 100.0

    def test_session_thresholds(self, regime_detector):
        asia = regime_detector.get_session_thresholds('ASIA')
        assert asia['volume_mult'] == 1.2
        assert asia['atr_mult'] == 0.8

        ny = regime_detector.get_session_thresholds('NY')
        assert ny['volume_mult'] == 1.3
        assert ny['atr_mult'] == 1.0

        unknown = regime_detector.get_session_thresholds('UNKNOWN')
        assert unknown == regime_detector.get_session_thresholds('LONDON')  # Falls back


# ============================================================================
# InstitutionalSLTPCalculator Tests
# ============================================================================

class TestInstitutionalSLTPCalculator:
    def test_ipa_sl_tp_calculation_long(self, sl_tp_calc, sample_candles_m5):
        """Test IPA LONG SL/TP calculation."""
        atr_m5 = 300.0  # $300 ATR (volatile conditions)
        entry = 70000.0

        result = sl_tp_calc.calculate_ipa(
            entry_price=entry,
            direction='LONG',
            ob_high=70050.0,
            ob_low=69950.0,
            atr_m5=atr_m5,
            swing_highs=[70300.0, 70400.0, 70500.0],  # Far enough for RR >= 1.8
            swing_lows=[69900.0, 69850.0],
        )

        assert result is not None, "TP should be found with swing highs present"
        assert result.stop_loss < entry, "LONG SL should be below entry"
        assert result.take_profit > entry, "LONG TP should be above entry"
        assert result.actual_rr >= 1.8, f"RR {result.actual_rr} should be >= 1.8"
        assert result.sl_pct <= 0.005, "SL should be within 0.5% ceiling"
        assert result.sl_pct > 0.002, "SL should be above 0.2% floor"

    def test_ipa_sl_tp_calculation_short(self, sl_tp_calc, sample_candles_m5):
        """Test IPA SHORT SL/TP calculation."""
        atr_m5 = 300.0
        entry = 70000.0

        result = sl_tp_calc.calculate_ipa(
            entry_price=entry,
            direction='SHORT',
            ob_high=70050.0,
            ob_low=69950.0,
            atr_m5=atr_m5,
            swing_highs=[70150.0, 70200.0],
            swing_lows=[69700.0, 69600.0, 69500.0],  # Far enough for RR >= 1.8
        )

        assert result is not None
        assert result.stop_loss > entry, "SHORT SL should be above entry"
        assert result.take_profit < entry, "SHORT TP should be below entry"
        assert result.actual_rr >= 1.8

    def test_ipa_returns_none_when_no_tp(self, sl_tp_calc):
        """IPA should return None when no TP meets RR >= 1.8."""
        entry = 70000.0
        atr_m5 = 300.0  # Wider ATR

        result = sl_tp_calc.calculate_ipa(
            entry_price=entry,
            direction='LONG',
            ob_high=None,
            ob_low=69900.0,
            atr_m5=atr_m5,
            swing_highs=[70100.0],  # Too close to entry
            swing_lows=[],
        )

        # Should return None because RR < 1.8
        assert result is None

    def test_iof_sl_tp_calculation(self, sl_tp_calc):
        """Test IOF SL/TP calculation (tighter than IPA)."""
        atr_m5 = 200.0
        entry = 70000.0
        wall_price = 69950.0  # Wall below entry for LONG

        result = sl_tp_calc.calculate_iof(
            entry_price=entry,
            direction='LONG',
            wall_price=wall_price,
            atr_m5=atr_m5,
            session='LONDON',
        )

        assert result is not None
        assert result.stop_loss < entry, "LONG SL should be below entry"
        assert result.stop_loss > wall_price * 0.99, "SL should be close to wall"
        assert result.sl_pct <= 0.004, "IOF SL should be within 0.4% ceiling"

    def test_iof_session_rr_targets(self, sl_tp_calc):
        """ASIA should have lower RR target than NY."""
        atr_m5 = 200.0
        entry = 70000.0
        wall = 69950.0

        asia_result = sl_tp_calc.calculate_iof(
            entry, 'LONG', wall, atr_m5, session='ASIA'
        )
        ny_result = sl_tp_calc.calculate_iof(
            entry, 'LONG', wall, atr_m5, session='NY'
        )

        assert asia_result is not None and ny_result is not None
        # ASIA has lower RR target, so TP should be closer
        tp_distance_asia = asia_result.take_profit - entry
        tp_distance_ny = ny_result.take_profit - entry
        assert tp_distance_asia <= tp_distance_ny, "ASIA TP should be closer than NY"


# ============================================================================
# SignalBuilder Tests
# ============================================================================

class TestSignalBuilder:
    def test_build_basic_signal(self, signal_builder, sl_tp_calc):
        """Test basic signal building."""
        sl_tp = sl_tp_calc.calculate_ipa(
            entry_price=70000.0,
            direction='LONG',
            ob_high=70050.0,
            ob_low=69950.0,
            atr_m5=300.0,  # Volatile conditions
            swing_highs=[70300.0, 70500.0],
            swing_lows=[69900.0],
        )
        assert sl_tp is not None, "SL/TP should be calculable"

        signal = signal_builder.build(
            mode='IPA',
            direction='LONG',
            entry_price=70000.0,
            sl_tp=sl_tp,
            session='LONDON',
            score=14,
            institutional_grade=True,
        )

        assert signal['mode'] == 'IPA'
        assert signal['direction'] == 'LONG'
        assert signal['entry_price'] == 70000.0
        assert signal['score'] == 14
        assert 'signal_id' in signal
        assert signal['signal_id'].startswith('IPA_')
        assert 'required_rr' in signal
        assert 'institutional_grade' in signal
        assert signal['session'] == 'LONDON'

    def test_build_iof_signal(self, signal_builder, sl_tp_calc):
        """Test IOF signal building."""
        sl_tp = sl_tp_calc.calculate_iof(
            entry_price=70000.0,
            direction='SHORT',
            wall_price=70050.0,
            atr_m5=200.0,
            session='NY',
        )
        assert sl_tp is not None

        signal = signal_builder.build(
            mode='IOF',
            direction='SHORT',
            entry_price=70000.0,
            sl_tp=sl_tp,
            session='NY',
            score=13,
        )

        assert signal['mode'] == 'IOF'
        assert signal['signal_id'].startswith('IOF_')

    def test_signal_id_unique(self, signal_builder, sl_tp_calc):
        """Each build should produce unique signal_id."""
        signals = []
        for i in range(5):
            sl_tp = sl_tp_calc.calculate_iof(
                entry_price=70000.0 + i * 10,
                direction='LONG',
                wall_price=69950.0 + i * 10,
                atr_m5=200.0,
                session='LONDON',
            )
            assert sl_tp is not None, f"IOF SL/TP should be calculable for i={i}"
            sig = signal_builder.build(
                mode='IOF',
                direction='LONG',
                entry_price=70000.0 + i * 10,
                sl_tp=sl_tp,
                session='LONDON',
                score=12,
            )
            signals.append(sig['signal_id'])

        assert len(signals) == len(set(signals)), "All signal_ids should be unique"

    def test_json_serialization(self, signal_builder, sl_tp_calc):
        """Test signal to JSON round-trip."""
        sl_tp = sl_tp_calc.calculate_iof(
            entry_price=70000.0, direction='LONG',
            wall_price=69950.0, atr_m5=200.0, session='LONDON',
        )
        assert sl_tp is not None
        signal = signal_builder.build(
            mode='IOF', direction='LONG', entry_price=70000.0,
            sl_tp=sl_tp, session='LONDON', score=12,
        )
        json_str = signal_builder.to_json_string(signal)
        parsed = signal_builder.from_json_string(json_str)
        assert parsed['signal_id'] == signal['signal_id']
        assert parsed['mode'] == signal['mode']


# ============================================================================
# SignalGate Tests
# ============================================================================

class TestSignalGate:
    def test_score_threshold_blocks_low(self, signal_gate, sl_tp_calc):
        """Gate should block IPA signal with score < 12."""
        sl_tp = sl_tp_calc.calculate_ipa(
            entry_price=70000.0, direction='LONG',
            ob_high=70050.0, ob_low=69950.0, atr_m5=150.0,
            swing_highs=[70200.0], swing_lows=[],
        )
        signal = {
            'mode': 'IPA', 'direction': 'LONG',
            'entry_price': 70000.0, 'score': 10,  # Below threshold
            'required_rr': 2.0, 'signal_id': 'test_001',
        }
        account = AccountState.empty()
        result = signal_gate.check(signal, account, [])

        assert not result.passed
        assert 'SCORE_TOO_LOW' in result.reason

    def test_score_threshold_passes_high(self, signal_gate, sl_tp_calc):
        """Gate should pass IPA signal with score >= 12."""
        sl_tp = sl_tp_calc.calculate_ipa(
            entry_price=70000.0, direction='LONG',
            ob_high=70050.0, ob_low=69950.0, atr_m5=150.0,
            swing_highs=[70200.0], swing_lows=[],
        )
        signal = {
            'mode': 'IPA', 'direction': 'LONG',
            'entry_price': 70000.0, 'score': 14,
            'required_rr': 2.0, 'signal_id': 'test_002',
        }
        account = AccountState.empty()
        result = signal_gate.check(signal, account, [])

        assert result.passed

    def test_max_positions_per_mode(self, signal_gate, sl_tp_calc):
        """Gate should block when 1 position already open in same mode."""
        sl_tp = sl_tp_calc.calculate_ipa(
            entry_price=70000.0, direction='LONG',
            ob_high=70050.0, ob_low=69950.0, atr_m5=150.0,
            swing_highs=[70200.0], swing_lows=[],
        )
        signal = {
            'mode': 'IPA', 'direction': 'LONG',
            'entry_price': 70000.0, 'score': 14,
            'required_rr': 2.0, 'signal_id': 'test_003',
        }
        account = AccountState.empty()

        # One IPA position already open
        existing_pos = PositionInfo(
            ticket=12345, symbol='BTCUSDT', direction='LONG',
            mode='IPA', open_time=None, entry_price=69500.0,
        )
        result = signal_gate.check(signal, account, [existing_pos])

        assert not result.passed
        assert 'MAX_POSITIONS' in result.reason

    def test_different_modes_allowed(self, signal_gate, sl_tp_calc):
        """Gate should allow IOF signal even if IPA position is open."""
        sl_tp_iof = sl_tp_calc.calculate_iof(
            entry_price=70000.0, direction='LONG',
            wall_price=69950.0, atr_m5=150.0, session='LONDON',
        )
        signal = {
            'mode': 'IOF', 'direction': 'LONG',
            'entry_price': 70000.0, 'score': 12,
            'required_rr': 1.8, 'signal_id': 'test_004',
        }
        account = AccountState.empty()

        # IPA position open
        existing_pos = PositionInfo(
            ticket=12345, symbol='BTCUSDT', direction='LONG',
            mode='IPA', open_time=None, entry_price=69500.0,
        )
        result = signal_gate.check(signal, account, [existing_pos])

        assert result.passed, f"Different modes should be allowed: {result.reason}"

    def test_hard_lock_blocks_rapid_signals(self, signal_gate, sl_tp_calc):
        """Gate should block signals within 30s of last signal."""
        sl_tp = sl_tp_calc.calculate_ipa(
            entry_price=70000.0, direction='LONG',
            ob_high=70050.0, ob_low=69950.0, atr_m5=150.0,
            swing_highs=[70200.0], swing_lows=[],
        )
        signal = {
            'mode': 'IPA', 'direction': 'LONG',
            'entry_price': 70000.0, 'score': 14,
            'required_rr': 2.0, 'signal_id': 'test_005',
        }
        account = AccountState.empty()

        # Simulate last signal was 10s ago
        signal_gate._last_signal_time = datetime.now(timezone.utc) - timedelta(seconds=10)

        result = signal_gate.check(signal, account, [])

        assert not result.passed
        assert 'HARD_LOCK' in result.reason

    def test_duplicate_blocks(self, signal_gate, sl_tp_calc):
        """Gate should block duplicate signal_id."""
        signal = {
            'mode': 'IPA', 'direction': 'LONG',
            'entry_price': 70000.0, 'score': 14,
            'required_rr': 2.0, 'signal_id': 'DUP_TEST_001',
        }
        account = AccountState.empty()

        # Mark as sent first
        signal_gate.mark_sent(signal)

        # Reset hard lock time so DUPLICATE gate fires first
        signal_gate._last_signal_time = None

        # Try same signal again
        result = signal_gate.check(signal, account, [])

        assert not result.passed
        assert 'DUPLICATE' in result.reason

    def test_mark_sent_updates_state(self, signal_gate, sl_tp_calc):
        """mark_sent should update internal state."""
        signal = {
            'mode': 'IPA', 'direction': 'LONG',
            'entry_price': 70000.0, 'score': 14,
            'required_rr': 2.0, 'signal_id': 'MARK_TEST_001',
        }

        assert len(signal_gate._sent_signal_ids) == 0
        signal_gate.mark_sent(signal)
        assert 'MARK_TEST_001' in signal_gate._sent_signal_ids
        assert signal_gate._last_signal_time is not None

    def test_reset_clears_state(self, signal_gate):
        """reset() should clear all internal state."""
        signal_gate._sent_signal_ids.add('test')
        signal_gate._last_signal_time = None

        signal_gate.reset()

        assert len(signal_gate._sent_signal_ids) == 0
        assert signal_gate._last_signal_time is None

    def test_daily_loss_limit(self, signal_gate, sl_tp_calc):
        """Gate should block when daily loss exceeds 3%."""
        sl_tp = sl_tp_calc.calculate_ipa(
            entry_price=70000.0, direction='LONG',
            ob_high=70050.0, ob_low=69950.0, atr_m5=150.0,
            swing_highs=[70200.0], swing_lows=[],
        )
        signal = {
            'mode': 'IPA', 'direction': 'LONG',
            'entry_price': 70000.0, 'score': 14,
            'required_rr': 2.0, 'signal_id': 'test_loss',
        }

        # Account at 4% daily loss
        account = AccountState(
            daily_pnl=-2800.0,
            daily_loss_pct=4.0,  # > 3% limit
            equity=67000.0,
            balance=70000.0,
            open_positions=[],
        )
        result = signal_gate.check(signal, account, [])

        assert not result.passed
        assert 'DAILY_LOSS' in result.reason


# ============================================================================
# Run
# ============================================================================

if __name__ == '__main__':
    pytest.main([__file__, '-v'])
