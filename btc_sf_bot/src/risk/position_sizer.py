from typing import Dict, List, Optional
import os
from datetime import datetime, timezone

from ..utils.logger import get_logger

logger = get_logger(__name__)


class PositionSizer:
    """Position size calculator with risk management."""
    
    def __init__(self, config: Dict = None):
        """
        Initialize Position Sizer.
        
        Args:
            config: Configuration dictionary
        """
        self.config = config or {}
        
        # New v3.3 logic: Config can be nested under 'risk' or 'trading'
        # Check root first, then nested sections
        self.risk_per_trade = self.config.get('risk_per_trade')
        if self.risk_per_trade is None:
            self.risk_per_trade = self.config.get('risk', {}).get('risk_per_trade', 
                                  self.config.get('trading', {}).get('risk_per_trade', 0.5))
            
        self.max_risk_per_day = self.config.get('max_daily_loss')
        if self.max_risk_per_day is None:
            self.max_risk_per_day = self.config.get('risk', {}).get('max_daily_loss', 
                                    self.config.get('trading', {}).get('max_daily_loss', 3.0))
            
        self.max_positions = 999 # v3.3: Removed limit, handled by MT5 EA
        if self.max_positions is None:
            self.max_positions = self.config.get('risk', {}).get('max_positions', 
                                 self.config.get('trading', {}).get('max_positions', 2))
        
        # Initialize with 0 or low value until first MT5 update
        self.account_balance = float(os.getenv('ACCOUNT_BALANCE', 0))
        self.leverage = int(os.getenv('ACCOUNT_LEVERAGE', 10))
        self.is_initialized = False # Flag to track if we've received real balance
        
        # Tracking
        self.daily_loss = 0
        self.weekly_loss = 0
        self.consecutive_losses = 0
        self.trades_today = []
        self.last_reset_day = None
    
    def calculate_position_size(
        self,
        entry_price: float,
        stop_loss: float,
        risk_pct: float = None
    ) -> float:
        """
        Calculate position size based on risk.
        
        Args:
            entry_price: Entry price
            stop_loss: Stop loss price
            risk_pct: Risk percentage (optional, uses default)
        
        Returns:
            Position size in lots/contracts
        """
        if risk_pct is None:
            risk_pct = self.risk_per_trade
        
        # Calculate risk amount
        risk_amount = self.account_balance * (risk_pct / 100)
        
        # Calculate price difference (risk per unit)
        price_diff = abs(entry_price - stop_loss)
        
        if price_diff == 0:
            logger.warning("Stop loss same as entry, using minimum size")
            return self.get_min_lot_size()
        
        # Calculate position size
        position_size = risk_amount / price_diff
        
        # Round to appropriate precision
        position_size = self.round_position_size(position_size)
        
        # Check against max
        max_size = self.get_max_lot_size(entry_price)
        position_size = min(position_size, max_size)
        
        return position_size
    
    def calculate_risk_reward(
        self,
        entry: float,
        stop_loss: float,
        take_profit: float,
        direction: str
    ) -> float:
        """
        Calculate Risk/Reward ratio.
        
        Args:
            entry: Entry price
            stop_loss: Stop loss price
            take_profit: Take profit price
            direction: LONG or SHORT
        
        Returns:
            R/R ratio
        """
        risk = abs(entry - stop_loss)
        
        if direction == "LONG":
            reward = take_profit - entry
        else:
            reward = entry - take_profit
        
        if risk == 0:
            return 0
        
        return reward / risk
    
    def can_open_position(self) -> tuple:
        """
        Check if can open new position.
        
        Returns:
            Tuple of (can_open, reason)
        """
        # Check if drawdown protection is disabled (testing mode)
        drawdown_config = self.config.get('drawdown_protection', {})
        if not drawdown_config.get('enabled', True):
            # Skip daily loss and consecutive loss checks
            # v3.3: Removed limit check, handled by MT5 EA
            # if current_pos_count >= self.max_positions:
            #     return False, f"Max positions reached ({self.max_positions})"
            return True, "OK (Drawdown protection disabled)"
        
        # Check daily loss limit
        if self.daily_loss >= self.max_risk_per_day:
            return False, f"Daily loss limit reached ({self.daily_loss}%)"
        
        # Check max positions - v3.3: Removed to allow MT5 management
        # current_pos_count = len(getattr(self, 'open_positions', self.trades_today))
        # if current_pos_count >= self.max_positions:
        #     return False, f"Max positions reached ({self.max_positions})"
        
        # Check consecutive losses
        if self.consecutive_losses >= 5:
            return False, "Too many consecutive losses"
        
        return True, "OK"
    
    def record_external_trade(
        self,
        symbol: str,
        direction: str,
        entry: float,
        exit: float,
        lot_size: float,
        profit: float,
        commission: float = 0
    ):
        """
        Record trade result from an external source (e.g. MT5).
        
        Args:
            symbol: Trading symbol
            direction: BUY or SELL
            entry: Entry price
            exit: Exit price
            lot_size: Position size
            profit: Net profit/loss in money
            commission: Trade commission
        """
        # Calculate net P&L
        net_pnl = profit - commission
        
        # Calculate P&L as percentage of account balance
        pnl_pct = (net_pnl / self.account_balance) * 100 if self.account_balance > 0 else 0
        
        # Record trade
        self.trades_today.append({
            'symbol': symbol,
            'direction': direction,
            'entry': entry,
            'exit': exit,
            'lot_size': lot_size,
            'pnl': net_pnl,
            'pnl_pct': pnl_pct,
            'timestamp': datetime.now(timezone.utc).isoformat()
        })
        
        # Update statistics
        if net_pnl < 0:
            self.daily_loss += abs(pnl_pct)
            self.weekly_loss += abs(pnl_pct)
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0
            # Potentially reduce daily/weekly loss tracking if we want to track net drawdown
            # but usually risk managers focus on peak-to-valley or daily cap
        
        logger.info(f"External trade recorded: {symbol} {direction} Net P&L: ${net_pnl:.2f} ({pnl_pct:.2f}%)")
    
    def reset_daily(self):
        """Reset daily tracking."""
        from datetime import datetime
        
        today = datetime.now(timezone.utc).date()
        
        if self.last_reset_day != today:
            self.daily_loss = 0
            self.trades_today = []
            self.last_reset_day = today
            logger.info("Daily tracking reset")
    
    def get_max_lot_size(self, current_price: float = 50000) -> float:
        """Get maximum lot size based on balance and current price."""
        if self.account_balance <= 0 or current_price <= 0:
            return 0.001
        
        max_balance_per_trade = self.account_balance * 0.2
        
        return max_balance_per_trade / current_price
    
    def get_min_lot_size(self) -> float:
        """Get minimum lot size."""
        return 0.001
    
    def round_position_size(self, size: float) -> float:
        """Round position size to appropriate precision."""
        # Round to 3 decimal places for most cryptos
        return round(size, 3)
    
    def get_available_balance(self) -> float:
        """Get available balance for trading."""
        # Subtract used margin
        # Simplified - should track actual positions
        used_margin = sum(t['lot_size'] * 10000 for t in self.trades_today)  # Rough estimate
        return max(0, self.account_balance - used_margin)
    
    def get_stats(self) -> Dict:
        """Get risk management statistics."""
        return {
            'daily_loss': self.daily_loss,
            'weekly_loss': self.weekly_loss,
            'consecutive_losses': self.consecutive_losses,
            'trades_today': len(self.trades_today),
            'max_positions': self.max_positions,
            'account_balance': self.account_balance,
            'can_open': self.can_open_position()[0]
        }


class RiskManager:
    """Comprehensive risk management."""
    
    def __init__(self, config: Dict = None):
        """
        Initialize Risk Manager.
        
        Args:
            config: Configuration dictionary
        """
        self.config = config or {}
        self.position_sizer = PositionSizer(config)
        
        # Settings
        self.max_daily_loss = self.config.get('max_daily_loss')
        if self.max_daily_loss is None:
            self.max_daily_loss = self.config.get('risk', {}).get('max_daily_loss', 3.0)
            
        self.max_weekly_loss = self.config.get('max_weekly_loss')
        if self.max_weekly_loss is None:
            self.max_weekly_loss = self.config.get('risk', {}).get('max_weekly_loss', 8.0)
            
        self.spread_limit = self.config.get('max_spread')
        if self.spread_limit is None:
            self.spread_limit = self.config.get('risk', {}).get('max_spread', 
                                self.config.get('signal_filters', {}).get('max_spread', 5.0))
        
        # Trading state
        self.is_trading_enabled = True
        self.pause_reason = ""
        self.tier = 0  # P1.1: Initialize tier to prevent AttributeError in check_trading_allowed()
        
        # P3.1: Mode-specific risk multipliers
        self.mode_risk_multipliers = {
            'TRENDING': self.config.get('risk_multiplier_trending', 1.0),
            'RANGING': self.config.get('risk_multiplier_ranging', 0.5),
            'VOLATILE': self.config.get('risk_multiplier_volatile', 0.3),
            'NEUTRAL': self.config.get('risk_multiplier_neutral', 0.7)
        }
        
        # P3.6: Weekend/News filter settings
        self.weekend_reduce_positions = self.config.get('weekend_reduce_positions', True)
        self.weekend_position_multiplier = self.config.get('weekend_position_multiplier', 0.5)
        self.news_filter_enabled = self.config.get('news_filter_enabled', False)
        self.news_buffer_minutes = self.config.get('news_buffer_minutes', 30)
    
    def check_spread(self, spread: float) -> tuple:
        """
        Check if spread is acceptable.
        
        Args:
            spread: Current spread
        
        Returns:
            Tuple of (acceptable, reason)
        """
        if spread > self.spread_limit:
            return False, f"Spread too high ({spread})"
        return True, "OK"
    
    def check_trading_allowed(self) -> tuple:
        """
        Check if trading is allowed.
        
        Returns:
            Tuple of (allowed, reason)
        """
        # If manually disabled or locked by drawdown tier (2/3)
        if not self.is_trading_enabled and self.tier >= 2:
            return False, self.pause_reason
            
        # If disabled for other reasons (e.g. manual toggle)
        if not self.is_trading_enabled:
             return False, self.pause_reason
        
        # Check position sizer (dynamic check: max positions, daily loss, etc.)
        can_open, reason = self.position_sizer.can_open_position()
        if not can_open:
            # Don't set is_trading_enabled = False here, as it's a dynamic limit
            # just return the reason why THIS entry is blocked
            return False, reason
        
        return True, "OK"
    
    def enable_trading(self):
        """Enable trading."""
        self.is_trading_enabled = True
        self.pause_reason = ""
        logger.info("Trading enabled")
    
    def disable_trading(self, reason: str):
        """
        Disable trading.
        
        Args:
            reason: Reason for disabling
        """
        self.is_trading_enabled = False
        self.pause_reason = reason
        logger.warning(f"Trading disabled: {reason}")
    
    def update_positions_state(self, positions: List[Dict]):
        """
        Update risk state based on actual open positions in MT5.
        
        Args:
            positions: List of open position dicts from MT5
        """
        # Sync trades_today with actual open positions
        # This prevents opening extra trades if self.trades_today is inaccurate
        self.position_sizer.open_positions = positions
        
        # update current count for can_open_position check
        if hasattr(self.position_sizer, 'trades_today'):
            # Only count actual open positions toward the limit
            # but preserve the trade history for loss tracking
            pass
            
        # logger.info(f"Positions synced: {len(positions)} open in MT5")
        
    def update_account_state(self, account_data: Dict):
        """
        Update risk parameters based on real-time account state.
        
        Args:
            account_data: Data from MT5 (equity, balance, etc.)
        """
        equity = account_data.get('equity', 0)
        balance = account_data.get('balance', 0)
        
        if balance <= 0: return
        
        # Update balance in position sizer for accurate lot calculation
        self.position_sizer.account_balance = balance
        self.position_sizer.is_initialized = True
        
        # Check if drawdown protection is enabled
        drawdown_config = self.config.get('drawdown_protection', {})
        is_enabled = drawdown_config.get('enabled', True)
        
        # Force disable if thresholds are set very high (>10%)
        tier_3 = drawdown_config.get('tier_3_threshold', 6.0)
        if tier_3 > 10.0:
            is_enabled = False
            
        if not is_enabled:
            # Skip all drawdown checks - ALWAYS
            self.tier = 0
            self.risk_multiplier = 1.0
            self.min_score_boost = 0
            self.is_trading_enabled = True
            self.pause_reason = None
            return
        
        # Calculate Drawdown
        drawdown_pct = ((balance - equity) / balance) * 100 if equity < balance else 0
        
        # Get thresholds from config (with defaults for testing)
        tier_1 = drawdown_config.get('tier_1_threshold', 2.0)
        tier_2 = drawdown_config.get('tier_2_threshold', 4.0)
        tier_3 = drawdown_config.get('tier_3_threshold', 6.0)
        
        # Tiered Risk Logic (using configurable thresholds)
        if drawdown_pct < tier_1:
            self.tier = 0 # Normal
            self.risk_multiplier = 1.0
            self.min_score_boost = 0
            self.is_trading_enabled = True
        elif drawdown_pct < tier_2:
            self.tier = 1 # Defensive
            self.risk_multiplier = 0.5 # Cut risk by half
            self.min_score_boost = 1.5 # Require +1.5 score (Total 8.5)
            self.is_trading_enabled = True
            logger.info(f"🛡️ RISK ALERT: Entering DEFENSIVE MODE (DD: {drawdown_pct:.2f}%, Tier 1: {tier_1}%)")
        elif drawdown_pct < tier_3:
            self.tier = 2 # Lockout
            self.is_trading_enabled = False
            self.pause_reason = f"Equity Lockout (DD: {drawdown_pct:.2f}%)"
            logger.warning(f"⚠️ RISK ALERT: Entering LOCKOUT MODE (DD: {drawdown_pct:.2f}%, Tier 2: {tier_2}%)")
        else:
            self.tier = 3 # Hard Lockout
            self.is_trading_enabled = False
            self.pause_reason = f"Hard Equity Lockout (DD: {drawdown_pct:.2f}%)"
            logger.error(f"🚨 EMERGENCY: HARD EQUITY LOCKOUT (DD: {drawdown_pct:.2f}%, Tier 3: {tier_3}%)")

    def get_risk_multiplier(self) -> float:
        """Get current risk multiplier based on tier."""
        return getattr(self, 'risk_multiplier', 1.0)
    
    def get_mode_risk_multiplier(self, market_mode: str = 'NEUTRAL') -> float:
        """
        P3.1: Get risk multiplier based on market mode.
        
        Args:
            market_mode: Market mode (TRENDING, RANGING, VOLATILE, NEUTRAL)
        
        Returns:
            Risk multiplier for the given mode
        """
        return self.mode_risk_multipliers.get(market_mode.upper(), 1.0)
    
    def get_dynamic_risk_reward(self, structure_type: str = "NONE", is_counter_trend: bool = False) -> float:
        """
        P3.5: Get dynamic Risk/Reward ratio based on structure.
        
        Args:
            structure_type: Structure type (CHoCH, BOS, NONE, etc.)
            is_counter_trend: Whether this is a counter-trend trade
        
        Returns:
            Recommended R:R ratio
        """
        if is_counter_trend:
            return 1.5
        
        structure_type = structure_type.upper()
        if structure_type == "CHOCH":
            return 3.0
        elif structure_type == "BOS":
            return 2.5
        elif structure_type == "CHOCH_INTERNAL":
            return 2.0
        else:
            return 2.0
    
    def is_weekend(self) -> bool:
        """P3.6: Check if current time is weekend (Saturday/Sunday)."""
        from datetime import datetime
        now = datetime.now()
        return now.weekday() >= 5  # 5=Saturday, 6=Sunday
    
    def get_weekend_position_limit(self, normal_max: int) -> int:
        """
        P3.6: Get reduced position limit for weekend.
        
        Args:
            normal_max: Normal maximum positions
        
        Returns:
            Reduced maximum for weekend
        """
        if self.weekend_reduce_positions and self.is_weekend():
            return int(normal_max * self.weekend_position_multiplier)
        return normal_max
        
    def get_min_score_adjustment(self) -> float:
        """Get required score adjustment based on tier."""
        return getattr(self, 'min_score_boost', 0)

    def calculate_sl_tp(
        self,
        entry: float,
        direction: str,
        atr: float = None,
        method: str = "atr",
        atr_multiplier: float = 1.5,
        risk_reward: float = 2.0
    ) -> Dict[str, float]:
        """
        Calculate Stop Loss and Take Profit levels.
        
        Args:
            entry: Entry price
            direction: LONG or SHORT
            atr: Average True Range (optional)
            method: SL calculation method ('atr' or 'fixed')
            atr_multiplier: ATR multiplier for SL
            risk_reward: Risk/Reward ratio
        
        Returns:
            Dictionary with sl and tp levels
        """
        if method == "atr" and atr:
            sl_distance = atr * atr_multiplier
        else:
            # Fixed percentage
            sl_distance = entry * 0.01  # 1% default
        
        # Calculate SL
        if direction == "LONG":
            sl = entry - sl_distance
            tp = entry + (sl_distance * risk_reward)
        else:
            sl = entry + sl_distance
            tp = entry - (sl_distance * risk_reward)
        
        return {
            'entry': entry,
            'sl': sl,
            'tp': tp,
            'sl_distance': sl_distance,
            'tp_distance': sl_distance * risk_reward,
            'risk_reward': risk_reward
        }
    
    def get_risk_summary(self) -> Dict:
        """Get risk management summary."""
        return {
            'trading_enabled': self.is_trading_enabled,
            'pause_reason': self.pause_reason,
            'position_sizer': self.position_sizer.get_stats()
        }
