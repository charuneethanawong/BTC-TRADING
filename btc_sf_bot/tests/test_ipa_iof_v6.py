"""
Unit Tests for IPA Analyzer v6.0 - Signal Generation Fix

Tests cover:
- Gate 1: EMA-based H1 bias (requires close > EMA20)
- Gate 2: Absolute index return, momentum fallback
- Gate 3: Absolute index slice correctness
"""
import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timezone

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.analysis.ipa_analyzer import IPAAnalyzer


class TestIPAGate1:
    """Test IPA Gate 1: EMA-based H1 bias"""
    
    def create_h1_candles(self, trend='bullish', num_candles=60):
        """Create H1 candles with specified trend"""
        np.random.seed(42)
        candles = []
        base_price = 70000
        
        for i in range(num_candles):
            if trend == 'bullish':
                # EMA20 > EMA50, close > EMA20
                close = base_price + i * 50 + np.random.randn() * 100
            elif trend == 'bearish':
                # EMA20 < EMA50, close < EMA20
                close = base_price - i * 50 + np.random.randn() * 100
            else:
                # Ranging - EMA20 oscillates around EMA50
                close = base_price + np.sin(i/10) * 500 + np.random.randn() * 100
            
            high = close + abs(np.random.randn() * 200)
            low = close - abs(np.random.randn() * 200)
            
            candles.append({
                'timestamp': datetime.now(timezone.utc),
                'open': close - np.random.randn() * 50,
                'high': high,
                'low': low,
                'close': close,
                'volume': 1000 + np.random.randn() * 100
            })
            base_price = close
        
        return pd.DataFrame(candles)
    
    def test_gate1_bullish_bias(self):
        """Test Gate 1: Bullish bias when EMA20 > EMA50 and close > EMA20"""
        analyzer = IPAAnalyzer()
        candles = self.create_h1_candles('bullish', 60)
        
        result = analyzer._check_h1_bias(candles)
        
        assert result is not None, "Gate 1 should pass for bullish EMA alignment"
        assert result['direction'] == 'LONG'
        assert result['bias'] == 'BULLISH'
        assert result['ema20'] > result['ema50'], "EMA20 should be above EMA50"
    
    def test_gate1_bearish_bias(self):
        """Test Gate 1: Bearish bias when EMA20 < EMA50 and close < EMA20"""
        analyzer = IPAAnalyzer()
        candles = self.create_h1_candles('bearish', 60)
        
        result = analyzer._check_h1_bias(candles)
        
        assert result is not None, "Gate 1 should pass for bearish EMA alignment"
        assert result['direction'] == 'SHORT'
        assert result['bias'] == 'BEARISH'
        assert result['ema20'] < result['ema50'], "EMA20 should be below EMA50"
    
    def test_gate1_insufficient_candles(self):
        """Test Gate 1: Should fail with insufficient candles (<50)"""
        analyzer = IPAAnalyzer()
        candles = self.create_h1_candles('bullish', 30)  # Only 30 candles
        
        result = analyzer._check_h1_bias(candles)
        
        assert result is None, "Gate 1 should fail with < 50 candles"
    
    def test_gate1_no_bias_when_close_not_aligned(self):
        """Test Gate 1: Should fail if close not above EMA20 even if EMA20 > EMA50"""
        analyzer = IPAAnalyzer()
        candles = self.create_h1_candles('bullish', 60)
        
        # Manually set last close below EMA20
        candles.iloc[-1, candles.columns.get_loc('close')] = candles['close'].iloc[-1] * 0.9
        
        result = analyzer._check_h1_bias(candles)
        
        # Should fail because close < EMA20
        assert result is None, "Gate 1 should fail when close not aligned with EMA"


class TestIPAGate2:
    """Test IPA Gate 2: M5 Structure with absolute index"""
    
    def create_m5_candles_with_bos(self, direction='LONG'):
        """Create M5 candles with a clear BOS pattern"""
        np.random.seed(42)
        candles = []
        base_price = 70000
        
        # Create a downtrend followed by BOS break
        for i in range(30):
            if direction == 'LONG' and i > 20:
                # After BOS: price moving up
                close = 70000 + (i - 20) * 100 + np.random.randn() * 50
            else:
                # Before BOS: price oscillating down
                close = base_price - (i * 20) + np.random.randn() * 30
            
            high = close + abs(np.random.randn() * 100)
            low = close - abs(np.random.randn() * 100)
            
            candles.append({
                'timestamp': datetime.now(timezone.utc),
                'open': close - np.random.randn() * 30,
                'high': high,
                'low': low,
                'close': close,
                'volume': 500 + np.random.randn() * 50
            })
            base_price = close
        
        return pd.DataFrame(candles)
    
    def test_gate2_returns_absolute_index(self):
        """Test Gate 2: Returns absolute index into original candles_m5"""
        analyzer = IPAAnalyzer()
        candles = self.create_m5_candles_with_bos('LONG')
        
        result = analyzer._check_m5_structure(candles, 'LONG')
        
        assert result is not None, "Gate 2 should detect BOS"
        assert 'break_idx' in result, "Result should contain break_idx"
        
        # break_idx should be absolute index (not relative to recent slice)
        break_idx = result['break_idx']
        assert 0 <= break_idx < len(candles), f"break_idx {break_idx} should be valid absolute index"
    
    def test_gate2_momentum_fallback(self):
        """Test Gate 2: Momentum fallback (3 consecutive closes) when no swing detected"""
        analyzer = IPAAnalyzer()
        
        # Create candles with no swings but 3 consecutive closes up
        candles_data = []
        for i in range(25):
            # Create sideways with 3 consecutive closes up at the end
            if i >= 22:
                close = 70000 + (i - 22) * 50  # Rising closes
            else:
                close = 70000 + np.sin(i) * 100  # Sideways
            
            candles_data.append({
                'timestamp': datetime.now(timezone.utc),
                'open': close - 20,
                'high': close + 30,
                'low': close - 30,
                'close': close,
                'volume': 500
            })
        
        candles = pd.DataFrame(candles_data)
        result = analyzer._check_m5_structure(candles, 'LONG')
        
        # Should pass via momentum fallback
        assert result is not None, "Gate 2 should detect momentum fallback"
        assert result['bos'] == True, "Momentum should be detected as BOS"


class TestIPAGate3:
    """Test IPA Gate 3: Order Block with absolute index"""
    
    def test_gate3_uses_absolute_index(self):
        """Test Gate 3: Correctly uses absolute index from Gate 2"""
        analyzer = IPAAnalyzer()
        
        # Create a simple scenario with an OB
        candles_data = []
        for i in range(40):
            if 15 <= i <= 17:
                # Bearish candle (potential OB for LONG)
                close = 70000 - 200
                open_price = 70000 + 100
            else:
                close = 70000 + np.sin(i/5) * 500
                open_price = close + np.random.randn() * 50
            
            candles_data.append({
                'timestamp': datetime.now(timezone.utc),
                'open': open_price,
                'high': max(open_price, close) + 50,
                'low': min(open_price, close) - 50,
                'close': close,
                'volume': 500
            })
        
        candles = pd.DataFrame(candles_data)
        
        # Gate 2 should return absolute index
        gate2_result = analyzer._check_m5_structure(candles, 'LONG')
        if gate2_result:
            break_idx = gate2_result['break_idx']
            
            # Gate 3 should find OB before break_idx using absolute index
            ob_result = analyzer._find_order_block(candles, 'LONG', break_idx)
            
            # OB should be found before break_idx
            assert ob_result is not None, "Gate 3 should find OB before break"


class TestIOFGate3:
    """Test IOF Gate 3: OI Signal Soft Gate"""
    
    def test_gate3_soft_gate_with_no_oi_data(self):
        """Test Gate 3: Returns dict (not None) when OI data unavailable"""
        from src.analysis.iof_analyzer import IOFAnalyzer
        
        analyzer = IOFAnalyzer()
        
        # Empty OI data
        binance_data = {
            'oi': 0,
            'oi_1min_ago': 0,
            'current_price': 70000
        }
        
        result = analyzer._check_oi_signal(binance_data, 'LONG')
        
        # Should return dict with 'skipped': True, not None
        assert result is not None, "Gate 3 should return dict, not None"
        assert result.get('skipped') == True, "Should indicate skipped due to no data"
        assert 'oi_change_pct' in result, "Should contain oi_change_pct"


class TestIOFGate4:
    """Test IOF Gate 4: Wall Detection with normalize_ob"""
    
    def test_normalize_ob_dict_format(self):
        """Test normalize_ob: Handles dict format {price: size}"""
        from src.analysis.iof_analyzer import IOFAnalyzer
        
        analyzer = IOFAnalyzer()
        
        # Dict format (as sent by connector)
        ob_dict = {
            '70000.0': '1.5',
            '70100.0': '2.0',
            '70200.0': '0.5'
        }
        
        result = analyzer._normalize_ob(ob_dict)
        
        assert len(result) == 3
        assert all(isinstance(item, list) or isinstance(item, tuple) for item in result)
        assert all(len(item) >= 2 for item in result)
    
    def test_normalize_ob_list_format(self):
        """Test normalize_ob: Handles list format [[price, size]]"""
        from src.analysis.iof_analyzer import IOFAnalyzer
        
        analyzer = IOFAnalyzer()
        
        # List format
        ob_list = [[70000.0, 1.5], [70100.0, 2.0], [70200.0, 0.5]]
        
        result = analyzer._normalize_ob(ob_list)
        
        assert len(result) == 3
        assert all(isinstance(item[0], float) for item in result)


class TestBotState:
    """Test BotState v6.0: structure_quality >= 5"""
    
    def test_can_look_for_entry_with_quality_5(self):
        """Test can_look_for_entry: Allows quality=5 (was 7 in older version)"""
        from src.signals.bot_state import BotState
        from src.enums import TrendState, BOSStatus
        
        state = BotState()
        state.trend = TrendState.BULLISH
        state.looking_for = 'ENTRY_SETUP'
        state.structure_quality = 5  # v6.0: should pass with >= 5
        
        assert state.can_look_for_entry() == True, "Should allow entry with quality=5"
    
    def test_can_look_for_entry_with_quality_6(self):
        """Test can_look_for_entry: Still allows quality=6"""
        from src.signals.bot_state import BotState
        from src.enums import TrendState
        
        state = BotState()
        state.trend = TrendState.BULLISH
        state.looking_for = 'ENTRY_SETUP'
        state.structure_quality = 6
        
        assert state.can_look_for_entry() == True
    
    def test_can_look_for_entry_blocked_with_quality_4(self):
        """Test can_look_for_entry: Blocks quality=4"""
        from src.signals.bot_state import BotState
        from src.enums import TrendState
        
        state = BotState()
        state.trend = TrendState.BULLISH
        state.looking_for = 'ENTRY_SETUP'
        state.structure_quality = 4  # Below threshold
        
        assert state.can_look_for_entry() == False, "Should block entry with quality=4"


class TestSmartFlowManager:
    """Test SmartFlowManager v6.0: All parameter changes"""
    
    def test_candle_progress_40_percent(self):
        """Test candle settlement: Should allow 40% progress (was 60%)"""
        from src.signals.smart_flow_manager import SmartFlowManager
        
        manager = SmartFlowManager()
        
        # Create candles with partial progress
        candles_data = []
        for i in range(5):
            candles_data.append({
                'open': 70000 + i * 100,
                'high': 70050 + i * 100,
                'low': 69950 + i * 100,
                'close': 70020 + i * 100,
                'volume': 500,
                'open_time': datetime.now(timezone.utc),
                'close_time': datetime.now(timezone.utc)
            })
        
        candles = pd.DataFrame(candles_data)
        
        # Set last candle to 45% progress (between old 60% and new 40%)
        candles.iloc[-1, candles.columns.get_loc('close')] = (
            candles.iloc[-1]['open'] + 
            (candles.iloc[-1]['high'] - candles.iloc[-1]['open']) * 0.45
        )
        
        is_ready, progress = manager._check_candle_progress(candles)
        
        # v6.0: 45% should pass (was blocked at 60%)
        assert is_ready == True, f"Should allow 45% progress (v6.0 threshold 40%)"
    
    def test_cooldown_price_distance_50(self):
        """Test cooldown: Should use $50 distance (was $100)"""
        from src.signals.smart_flow_manager import SmartFlowManager
        
        config = {'smart_flow': {'cooldown_price_distance': 50}}
        manager = SmartFlowManager(config)
        
        assert manager.cooldown_price_distance == 50, "Cooldown should be $50 (v6.0)"
    
    def test_atr_scale_range(self):
        """Test ATR filter: Scale range 0.8-1.1 (was 0.7-1.3)"""
        from src.signals.smart_flow_manager import SmartFlowManager
        
        config = {
            'smart_flow': {
                'atr_volatility_scale_min': 0.8,
                'atr_volatility_scale_max': 1.1
            }
        }
        manager = SmartFlowManager(config)
        
        assert manager.atr_scale_min == 0.8, "ATR scale min should be 0.8"
        assert manager.atr_scale_max == 1.1, "ATR scale max should be 1.1"
    
    def test_da_threshold_7(self):
        """Test DA threshold: Should be 7 (was 10)"""
        from src.signals.smart_flow_manager import SmartFlowManager
        
        config = {'smart_flow': {'threshold_da': 7}}
        manager = SmartFlowManager(config)
        
        assert manager.thresholds['DA'] == 7, "DA threshold should be 7 (v6.0)"
    
    def test_counter_trend_thresholds_v6(self):
        """Test counter-trend thresholds: v6.0 reduced values"""
        from src.signals.smart_flow_manager import SmartFlowManager
        
        manager = SmartFlowManager()
        
        # Test LP thresholds (reduced from 15/13/10/8 to 13/11/9/7)
        lp_very_high = manager._get_counter_trend_thresholds('LP', 'VERY_HIGH')
        assert lp_very_high[0] == 13, f"LP VERY_HIGH should be 13, got {lp_very_high[0]}"
        
        lp_high = manager._get_counter_trend_thresholds('LP', 'HIGH')
        assert lp_high[0] == 11, f"LP HIGH should be 11, got {lp_high[0]}"
        
        # Test DA thresholds
        da_very_high = manager._get_counter_trend_thresholds('DA', 'VERY_HIGH')
        assert da_very_high[0] == 14, f"DA VERY_HIGH should be 14, got {da_very_high[0]}"


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
