import pytest
from src.signals.signal_manager import SignalManager

def test_make_short_reason():
    sm = SignalManager()
    
    # Test case 1: Bullish Imbalance + Discount
    long_reason = "BULLISH_IMBALANCE + ICT:DISCOUNT"
    short_reason = sm._make_short_reason(long_reason)
    assert "BULL" in short_reason
    assert "IB" in short_reason
    assert "DISC" in short_reason
    assert "+" not in short_reason
    assert len(short_reason) <= 31

    # Test case 2: Bearish Order Block + Premium + Divergence
    short_reason_long = "BEARISH_ORDER_BLOCK + ICT:PREMIUM + DIVERGENCE"
    short_result = sm._make_short_reason(short_reason_long)
    assert "BEAR" in short_result
    assert "OB" in short_result
    assert "PREM" in short_result
    assert "DIV" in short_result
    assert len(short_result) <= 31

    # Test case 3: Liquidity Sweep + Absorption
    liq_reason = "LIQUIDITY_SWEEP_LOW + ABSORPTION"
    liq_short = sm._make_short_reason(liq_reason)
    assert "LIQ" in liq_short
    assert "SW_L" in liq_short
    assert "ABS" in liq_short
    assert len(liq_short) <= 31

def test_signal_to_dict_shorthand():
    from src.signals.signal_manager import Signal
    
    sig = Signal(
        direction="LONG",
        entry_price=60000,
        stop_loss=59000,
        take_profit=62000,
        confidence=80,
        reason="BULLISH_IMBALANCE + ICT:DISCOUNT",
        metadata={'short_reason': "BULL_IB,ICT:DISC"}
    )
    
    d = sig.to_dict()
    assert d['short_reason'] == "BULL_IB,ICT:DISC"
    assert "short_reason" in d
