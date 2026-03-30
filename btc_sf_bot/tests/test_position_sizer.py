import pytest
import os
from src.risk.position_sizer import PositionSizer

def test_calculate_position_size():
    # Set mock env variables
    os.environ['ACCOUNT_BALANCE'] = '10000'
    os.environ['ACCOUNT_LEVERAGE'] = '10'
    
    ps = PositionSizer()
    
    # Entry 60000, SL 59000, Risk 0.5% of 10000 = $50
    # Risk per unit = 1000
    # Contracts = 50 / 1000 = 0.05
    # Leverage 10x = 0.5 contracts
    size = ps.calculate_position_size(60000, 59000)
    assert size == 0.5

def test_risk_reward_calculation():
    ps = PositionSizer()
    rr = ps.calculate_risk_reward(60000, 59000, 62000, "LONG")
    assert rr == 2.0
    
    rr_short = ps.calculate_risk_reward(60000, 61000, 58000, "SHORT")
    assert rr_short == 2.0

def test_can_open_position():
    ps = PositionSizer()
    ps.max_positions = 2
    ps.trades_today = []
    
    allowed, reason = ps.can_open_position()
    assert allowed == True
    
    # Add 2 trades
    ps.trades_today = [1, 2]
    allowed_full, reason_full = ps.can_open_position()
    assert allowed_full == False
    assert "Max positions" in reason_full

def test_daily_loss_limit():
    ps = PositionSizer()
    ps.max_risk_per_day = 3.0
    ps.daily_loss = 3.1
    
    allowed, reason = ps.can_open_position()
    assert allowed == False
    assert "Daily loss" in reason
