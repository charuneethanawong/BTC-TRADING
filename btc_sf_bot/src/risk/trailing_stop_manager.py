"""
Dynamic Trailing Stop Manager
Manages trailing stops based on market structure and price action
"""
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
import pandas as pd
import numpy as np

from ..utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class TrailingPosition:
    """Trailing position state."""
    direction: str
    entry_price: float
    initial_sl: float
    current_sl: float
    peak_price: float
    activated: bool = False
    activation_profit_pct: float = 0.0
    trail_count: int = 0
    last_update: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    signal_id: str = ""


class TrailingStopManager:
    """
    Dynamic Trailing Stop Manager.
    
    Trailing Methods:
    1. Structure-Based: Trail to recent swing lows/highs
    2. ATR-Based: Trail at ATR distance from peak
    3. Breakeven: Move to breakeven after X% profit
    4. Risk-Free: Lock in minimum profit after Y% gain
    
    Configuration:
    - activation_profit_pct: Profit % to activate trailing (default 0.3%)
    - trail_atr_mult: ATR multiplier for trailing (default 1.5)
    - breakeven_profit_pct: Profit % to move to breakeven (default 0.5%)
    - min_lock_profit_pct: Minimum profit to lock after trail (default 0.2%)
    - max_trail_distance_pct: Max distance from peak (default 1.0%)
    """
    
    def __init__(self, config: Dict = None):
        self.config = config or {}
        
        self.activation_profit_pct = self.config.get('activation_profit_pct', 0.3)
        self.trail_atr_mult = self.config.get('trail_atr_mult', 1.5)
        self.breakeven_profit_pct = self.config.get('breakeven_profit_pct', 0.4)
        self.breakeven_buffer_pct = self.config.get('breakeven_buffer_pct', 0.1) # Breakeven+ to cover fees
        self.min_lock_profit_pct = self.config.get('min_lock_profit_pct', 0.2)
        self.max_trail_distance_pct = self.config.get('max_trail_distance_pct', 1.0)
        self.structure_trail_enabled = self.config.get('structure_trail_enabled', True)
        
        self.positions: Dict[str, TrailingPosition] = {}
        self.swing_points: Dict[str, List[Dict]] = {'highs': [], 'lows': []}
    
    def register_position(
        self,
        signal_id: str,
        direction: str,
        entry_price: float,
        initial_sl: float
    ) -> TrailingPosition:
        """
        Register a new position for trailing.
        
        Args:
            signal_id: Unique signal ID
            direction: 'LONG' or 'SHORT'
            entry_price: Entry price
            initial_sl: Initial stop loss
        
        Returns:
            TrailingPosition object
        """
        position = TrailingPosition(
            direction=direction,
            entry_price=entry_price,
            initial_sl=initial_sl,
            current_sl=initial_sl,
            peak_price=entry_price,
            signal_id=signal_id
        )
        
        self.positions[signal_id] = position
        logger.debug(f"Registered position for trailing: {signal_id} | {direction} @ {entry_price}")
        
        return position
    
    def update(
        self,
        signal_id: str,
        current_price: float,
        candles: pd.DataFrame = None,
        atr: float = None,
        poc: float = None,
        liquidity_walls: Dict = None
    ) -> Dict:
        """
        Update trailing stop for a position.
        
        Args:
            signal_id: Signal ID to update
            current_price: Current market price
            candles: OHLCV DataFrame for structure-based trailing
            atr: Current ATR value
            poc: Current POC level
            liquidity_walls: Dict of detected liquidity walls
        
        Returns:
            {
                'updated': bool,
                'new_sl': float,
                'old_sl': float,
                'reason': str,
                'profit_pct': float,
                'is_locked': bool
            }
        """
        if signal_id not in self.positions:
            return {'updated': False, 'reason': 'Position not found'}
        
        position = self.positions[signal_id]
        old_sl = position.current_sl
        
        if candles is not None:
            self._update_swing_points(candles)
        
        is_long = position.direction == 'LONG'
        profit_pct = self._calculate_profit_pct(position, current_price, is_long)
        
        position.peak_price = self._update_peak(position.peak_price, current_price, is_long)
        
        if not position.activated:
            if profit_pct >= self.activation_profit_pct:
                position.activated = True
                position.activation_profit_pct = profit_pct
                logger.info(f"Trailing activated for {signal_id} at {profit_pct:.2f}% profit")
        
        if not position.activated:
            return {
                'updated': False,
                'new_sl': old_sl,
                'old_sl': old_sl,
                'reason': f'Not activated yet ({profit_pct:.2f}% < {self.activation_profit_pct}%)',
                'profit_pct': profit_pct,
                'is_locked': False
            }
        
        new_sl = old_sl
        reason = ""
        
        breakeven_sl = self._check_breakeven(position, profit_pct)
        if breakeven_sl is not None:
            if is_long and breakeven_sl > new_sl:
                new_sl = breakeven_sl
                reason = "BREAKEVEN"
            elif not is_long and breakeven_sl < new_sl:
                new_sl = breakeven_sl
                reason = "BREAKEVEN"
        
        atr_sl = self._calculate_atr_trail(position, atr, current_price, is_long)
        if atr_sl is not None:
            if is_long and atr_sl > new_sl:
                new_sl = atr_sl
                reason = "ATR_TRAIL"
            elif not is_long and atr_sl < new_sl:
                new_sl = atr_sl
                reason = "ATR_TRAIL"
        
        if self.structure_trail_enabled and candles is not None:
            structure_sl = self._calculate_structure_trail(position, is_long)
            if structure_sl is not None:
                if is_long and structure_sl > new_sl:
                    new_sl = structure_sl
                    reason = "STRUCTURE_TRAIL"
                elif not is_long and structure_sl < new_sl:
                    new_sl = structure_sl
                    reason = "STRUCTURE_TRAIL"
        
        # 4. POC-Based Trailing
        if poc is not None and poc > 0:
            if is_long and poc > position.entry_price and poc > new_sl:
                new_sl = poc
                reason = "POC_TRAIL"
            elif not is_long and poc < position.entry_price and poc < new_sl:
                new_sl = poc
                reason = "POC_TRAIL"
        
        # 5. Limit Density (Liquidity Wall) Trailing
        if liquidity_walls is not None:
            if is_long:
                bids = liquidity_walls.get('bids', [])
                valid_bids = [b['price'] for b in bids if b['price'] > new_sl and b['price'] < current_price]
                if valid_bids:
                    new_sl = max(valid_bids)
                    reason = "LIMIT_DENSITY_TRAIL"
            else:
                asks = liquidity_walls.get('asks', [])
                valid_asks = [a['price'] for a in asks if a['price'] < new_sl and a['price'] > current_price]
                if valid_asks:
                    new_sl = min(valid_asks)
                    reason = "LIMIT_DENSITY_TRAIL"

        new_sl = self._apply_constraints(position, new_sl, current_price, is_long)
        
        updated = new_sl != old_sl
        if updated:
            position.current_sl = new_sl
            position.trail_count += 1
            position.last_update = datetime.now(timezone.utc)
            
            logger.info(
                f"Trailing stop updated for {signal_id}: {old_sl:.2f} -> {new_sl:.2f} | "
                f"Reason: {reason} | Trail #{position.trail_count}"
            )
        
        is_locked = self._is_profit_locked(position, current_price, is_long)
        
        return {
            'updated': updated,
            'new_sl': new_sl,
            'old_sl': old_sl,
            'reason': reason if updated else "No change",
            'profit_pct': profit_pct,
            'is_locked': is_locked,
            'trail_count': position.trail_count
        }
    
    def _calculate_profit_pct(
        self,
        position: TrailingPosition,
        current_price: float,
        is_long: bool
    ) -> float:
        """Calculate current profit percentage."""
        if is_long:
            return ((current_price - position.entry_price) / position.entry_price) * 100
        else:
            return ((position.entry_price - current_price) / position.entry_price) * 100
    
    def _update_peak(self, peak: float, current: float, is_long: bool) -> float:
        """Update peak price."""
        if is_long:
            return max(peak, current)
        else:
            return min(peak, current)
    
    def _check_breakeven(
        self,
        position: TrailingPosition,
        profit_pct: float
    ) -> Optional[float]:
        """
        Check if should move to breakeven (Institutional Breakeven+).
        Includes a small buffer to cover commissions.
        """
        if profit_pct >= self.breakeven_profit_pct:
            # Calculate Breakeven+ price (Entry + small buffer to cover fees)
            buffer_amount = position.entry_price * (self.breakeven_buffer_pct / 100)
            
            if position.direction == 'LONG':
                return position.entry_price + buffer_amount
            else:
                return position.entry_price - buffer_amount
        return None
    
    def _calculate_atr_trail(
        self,
        position: TrailingPosition,
        atr: Optional[float],
        current_price: float,
        is_long: bool
    ) -> Optional[float]:
        """Calculate ATR-based trailing stop."""
        if atr is None or atr <= 0:
            return None
        
        trail_distance = atr * self.trail_atr_mult
        
        if is_long:
            return current_price - trail_distance
        else:
            return current_price + trail_distance
    
    def _update_swing_points(self, candles: pd.DataFrame):
        """Update swing points from candles."""
        if len(candles) < 5:
            return
        
        self.swing_points = self._find_fractals(candles)
    
    def _find_fractals(self, candles: pd.DataFrame, n: int = 2) -> Dict[str, List[Dict]]:
        """Find fractal swing points."""
        highs = []
        lows = []
        
        for i in range(n, len(candles) - n):
            curr_high = candles.iloc[i]['high']
            curr_low = candles.iloc[i]['low']
            
            is_high = True
            for j in range(1, n + 1):
                if (candles.iloc[i - j]['high'] >= curr_high or 
                    candles.iloc[i + j]['high'] > curr_high):
                    is_high = False
                    break
            
            if is_high:
                highs.append({
                    'level': curr_high,
                    'index': i,
                    'time': candles.index[i] if hasattr(candles.index[i], 'isoformat') else str(candles.index[i])
                })
            
            is_low = True
            for j in range(1, n + 1):
                if (candles.iloc[i - j]['low'] <= curr_low or 
                    candles.iloc[i + j]['low'] < curr_low):
                    is_low = False
                    break
            
            if is_low:
                lows.append({
                    'level': curr_low,
                    'index': i,
                    'time': candles.index[i] if hasattr(candles.index[i], 'isoformat') else str(candles.index[i])
                })
        
        return {'highs': highs, 'lows': lows}
    
    def _calculate_structure_trail(
        self,
        position: TrailingPosition,
        is_long: bool
    ) -> Optional[float]:
        """Calculate structure-based trailing stop."""
        if is_long:
            lows = self.swing_points.get('lows', [])
            if not lows:
                return None
            
            recent_lows = [l['level'] for l in lows[-5:] if l['level'] > position.current_sl]
            if recent_lows:
                return min(recent_lows)
        else:
            highs = self.swing_points.get('highs', [])
            if not highs:
                return None
            
            recent_highs = [h['level'] for h in highs[-5:] if h['level'] < position.current_sl]
            if recent_highs:
                return max(recent_highs)
        
        return None
    
    def _apply_constraints(
        self,
        position: TrailingPosition,
        new_sl: float,
        current_price: float,
        is_long: bool
    ) -> float:
        """Apply constraints to trailing stop."""
        
        if is_long:
            new_sl = max(new_sl, position.current_sl)
            new_sl = max(new_sl, position.initial_sl)
        else:
            new_sl = min(new_sl, position.current_sl)
            new_sl = min(new_sl, position.initial_sl)
        
        max_distance = position.entry_price * (self.max_trail_distance_pct / 100)
        
        if is_long:
            max_sl = position.peak_price - max_distance
            new_sl = min(new_sl, max_sl)
        else:
            min_sl = position.peak_price + max_distance
            new_sl = max(new_sl, min_sl)
        
        min_profit = position.entry_price * (self.min_lock_profit_pct / 100)
        if position.trail_count > 0:
            if is_long:
                min_sl = position.entry_price + min_profit
                new_sl = max(new_sl, min_sl)
            else:
                max_sl = position.entry_price - min_profit
                new_sl = min(new_sl, max_sl)
        
        return new_sl
    
    def _is_profit_locked(
        self,
        position: TrailingPosition,
        current_price: float,
        is_long: bool
    ) -> bool:
        """Check if profit is locked in."""
        if is_long:
            return position.current_sl > position.entry_price
        else:
            return position.current_sl < position.entry_price
    
    def remove_position(self, signal_id: str) -> bool:
        """Remove a position from tracking."""
        if signal_id in self.positions:
            del self.positions[signal_id]
            logger.info(f"Removed position from trailing: {signal_id}")
            return True
        return False
    
    def get_position(self, signal_id: str) -> Optional[TrailingPosition]:
        """Get position by signal ID."""
        return self.positions.get(signal_id)
    
    def get_all_positions(self) -> Dict[str, TrailingPosition]:
        """Get all tracked positions."""
        return self.positions
    
    def get_statistics(self) -> Dict:
        """Get trailing statistics."""
        if not self.positions:
            return {
                'total_positions': 0,
                'active_positions': 0,
                'avg_trail_count': 0
            }
        
        active = [p for p in self.positions.values() if p.activated]
        
        return {
            'total_positions': len(self.positions),
            'active_positions': len(active),
            'avg_trail_count': np.mean([p.trail_count for p in self.positions.values()]) if self.positions else 0
        }
