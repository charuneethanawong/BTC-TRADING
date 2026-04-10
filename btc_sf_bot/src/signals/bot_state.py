"""
Bot State Manager Module
Tracks current market structure state for v3.0 2-Phase Signal Generation
v36.2: Migrated from JSON file to SQLite
"""
from datetime import datetime, timezone
from typing import Dict, Optional

from ..utils.logger import get_logger
from ..enums import TrendState, BOSStatus
from ..data.db_manager import get_db

import json
import math
from pathlib import Path

logger = get_logger(__name__)


class BotState:
    """
    Tracks the current market structure state.
    
    Attributes:
        trend: Current trend direction (RANGE/BULLISH/BEARISH)
        structure_quality: Quality score of last confirmed BOS (0-10)
        last_confirmed_high: Last confirmed Swing High level
        last_confirmed_low: Last confirmed Swing Low level
        last_bos_time: Timestamp of last confirmed BOS
        last_bos_score: Score of last confirmed BOS
        last_bos_status: Status of last BOS validation
        looking_for: What the bot is looking for (ENTRY_SETUP or None)
    """
    
    def __init__(self):
        self.trend = TrendState.RANGE
        self.structure_quality = 0
        self.last_confirmed_high = 0.0
        self.last_confirmed_low = 0.0  # v34.0: Changed from float('inf') to prevent Infinity bug
        self.last_bos_time: Optional[datetime] = None
        self.last_bos_score = 0
        self.last_bos_status: Optional[BOSStatus] = None
        self.looking_for: Optional[str] = None
        
        self.pending_bos: Optional[Dict] = None
        self.pending_since: Optional[datetime] = None
        
        self.state_history: list = []
        self.max_history = 100
    
    def update_trend(
        self,
        new_trend: TrendState,
        score: int,
        level: float,
        direction: str,
        status: BOSStatus = BOSStatus.CONFIRMED
    ) -> None:
        """
        Update trend state when BOS is confirmed.
        
        Args:
            new_trend: New trend direction
            score: Structure validation score (0-10)
            level: The swing level that was broken
            direction: BULLISH or BEARISH
            status: BOS validation status
        """
        old_trend = self.trend
        
        self.trend = new_trend
        self.structure_quality = score
        self.last_bos_time = datetime.now(timezone.utc)
        self.last_bos_score = score
        self.last_bos_status = status
        
        if direction == 'BULLISH':
            self.last_confirmed_high = level
        else:
            self.last_confirmed_low = level
        
        if status == BOSStatus.CONFIRMED or score >= 7:
            self.looking_for = 'ENTRY_SETUP'
            if status == BOSStatus.CONFIRMED:
                self.pending_bos = None
                self.pending_since = None
        else:
            self.looking_for = None
        
        self._record_history({
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'old_trend': old_trend.value,
            'new_trend': new_trend.value,
            'score': score,
            'level': level,
            'direction': direction,
            'status': status.value
        })
        
        logger.info(
            f"BotState updated: {old_trend.value} → {new_trend.value} | "
            f"Score: {score} | Level: {level} | Status: {status.value}"
        )
        self.save_state()
    
    def check_staleness(self, current_price: float) -> bool:
        """
        v34.0: Check if structure is stale (>20% from current price) and reset if needed.
        
        Args:
            current_price: Current market price
            
        Returns:
            True if structure was reset due to staleness
        """
        if current_price <= 0:
            return False
            
        staleness_threshold = 0.20  # 20%
        reset_needed = False
        
        # Check high staleness
        if self.last_confirmed_high > 0:
            high_distance = abs(current_price - self.last_confirmed_high) / current_price
            if high_distance > staleness_threshold:
                logger.info(f"Structure staleness: high {self.last_confirmed_high:.0f} is {high_distance*100:.1f}% from current {current_price:.0f}")
                self.last_confirmed_high = 0.0
                reset_needed = True
        
        # Check low staleness
        if self.last_confirmed_low > 0:
            low_distance = abs(current_price - self.last_confirmed_low) / current_price
            if low_distance > staleness_threshold:
                logger.info(f"Structure staleness: low {self.last_confirmed_low:.0f} is {low_distance*100:.1f}% from current {current_price:.0f}")
                self.last_confirmed_low = 0.0
                reset_needed = True
        
        if reset_needed:
            self.trend = TrendState.RANGE
            self.structure_quality = 0
            self.looking_for = None
            self.save_state()
            logger.info("Structure reset due to staleness")
            
        return reset_needed
    
    def set_pending_bos(self, validation_result: Dict, level: float, direction: str) -> None:
        """
        Set a pending BOS that needs more confirmation.
        
        Args:
            validation_result: The validation result dictionary
            level: The swing level that was broken
            direction: BULLISH or BEARISH
        """
        self.pending_bos = {
            'validation': validation_result,
            'level': level,
            'direction': direction,
            'score': validation_result.get('score', 0)
        }
        self.pending_since = datetime.now(timezone.utc)
        logger.info(f"Pending BOS set: {direction} @ {level} | Score: {validation_result.get('score', 0)}")
    
    def clear_pending_bos(self) -> None:
        """Clear pending BOS."""
        self.pending_bos = None
        self.pending_since = None
    
    def confirm_pending_bos(self) -> bool:
        """
        Confirm pending BOS if it exists.
        
        Returns:
            True if pending BOS was confirmed, False otherwise
        """
        if not self.pending_bos:
            return False
        
        pending = self.pending_bos
        direction = pending['direction']
        level = pending['level']
        score = pending['score']
        
        new_trend = TrendState.BULLISH if direction == 'BULLISH' else TrendState.BEARISH
        
        self.update_trend(
            new_trend=new_trend,
            score=score,
            level=level,
            direction=direction,
            status=BOSStatus.CONFIRMED
        )
        
        return True
    
    def is_pending_expired(self, max_age_seconds: int = 300) -> bool:
        """
        Check if pending BOS has expired.
        
        Args:
            max_age_seconds: Maximum age in seconds (default 5 minutes)
        
        Returns:
            True if expired, False otherwise
        """
        if not self.pending_since:
            return False
        
        age = (datetime.now(timezone.utc) - self.pending_since).total_seconds()
        return age > max_age_seconds
    
    def reset_to_neutral(self, reason: str = "Manual reset") -> None:
        """
        Reset state to neutral.
        
        Args:
            reason: Reason for reset
        """
        old_trend = self.trend
        
        self.trend = TrendState.RANGE
        self.structure_quality = 0
        self.last_bos_time = None
        self.last_bos_score = 0
        self.last_bos_status = None
        self.looking_for = None
        self.pending_bos = None
        self.pending_since = None
        
        self._record_history({
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'action': 'RESET',
            'old_trend': old_trend.value,
            'reason': reason
        })
        
        logger.warning(f"BotState reset to RANGE | Reason: {reason}")
    
    def can_look_for_entry(self) -> bool:
        """
        Check if bot can look for entry setups.
        
        v6.0: Reduced structure_quality threshold from 7 to 5
        (allows PENDING-level entries to proceed faster while still
        requiring trend direction and BOS confirmation)
        
        Returns:
            True if bot should look for entries, False otherwise
        """
        return (
            self.trend != TrendState.RANGE and
            self.looking_for == 'ENTRY_SETUP' and
            self.structure_quality >= 5  # v6.0: was 7
        )
    
    def get_entry_direction(self) -> Optional[str]:
        """
        Get the direction to look for entries.
        
        Returns:
            'LONG', 'SHORT', or None
        """
        if not self.can_look_for_entry():
            return None
        
        if self.trend == TrendState.BULLISH:
            return 'LONG'
        elif self.trend == TrendState.BEARISH:
            return 'SHORT'
        
        return None
    
    def is_bullish(self) -> bool:
        """Check if current state is bullish."""
        return self.trend == TrendState.BULLISH
    
    def is_bearish(self) -> bool:
        """Check if current state is bearish."""
        return self.trend == TrendState.BEARISH
    
    def is_neutral(self) -> bool:
        """Check if current state is neutral."""
        return self.trend == TrendState.RANGE
    
    def get_state_dict(self) -> Dict:
        """
        Get current state as dictionary.
        v34.0: Added validation to prevent Infinity/NaN values.
        
        Returns:
            Dictionary with current state
        """
        # v34.0: Validate and fix Infinity/NaN values before saving
        last_confirmed_high = self.last_confirmed_high
        last_confirmed_low = self.last_confirmed_low
        
        # Check for infinity
        if not isinstance(last_confirmed_high, (int, float)) or not math.isfinite(last_confirmed_high):
            logger.warning(f"Invalid last_confirmed_high: {self.last_confirmed_high}, resetting to 0.0")
            last_confirmed_high = 0.0
            
        if not isinstance(last_confirmed_low, (int, float)) or not math.isfinite(last_confirmed_low):
            logger.warning(f"Invalid last_confirmed_low: {self.last_confirmed_low}, resetting to 0.0")
            last_confirmed_low = 0.0
        
        return {
            'trend': self.trend.value,
            'structure_quality': self.structure_quality,
            'last_confirmed_high': last_confirmed_high,
            'last_confirmed_low': last_confirmed_low,
            'last_bos_time': self.last_bos_time.isoformat() if self.last_bos_time else None,
            'last_bos_score': self.last_bos_score,
            'last_bos_status': self.last_bos_status.value if self.last_bos_status else None,
            'looking_for': self.looking_for,
            'can_look_for_entry': self.can_look_for_entry(),
            'entry_direction': self.get_entry_direction(),
            'has_pending_bos': self.pending_bos is not None,
            'pending_bos_age': (
                (datetime.now(timezone.utc) - self.pending_since).total_seconds()
                if self.pending_since else None
            )
        }
    
    def _record_history(self, record: Dict) -> None:
        """
        Record state change in history.
        
        Args:
            record: Record dictionary
        """
        self.state_history.append(record)
        
        if len(self.state_history) > self.max_history:
            self.state_history = self.state_history[-self.max_history:]
    
    def get_recent_history(self, count: int = 10) -> list:
        """
        Get recent state history.
        
        Args:
            count: Number of records to return
        
        Returns:
            List of recent state changes
        """
        return self.state_history[-count:]
    
    def save_state(self) -> bool:
        """Save current state to database (v36.2)."""
        try:
            db = get_db()
            state = self.get_state_dict()
            
            # Save entire state as single JSON object
            db.set_state('market_structure', state)
            
            return True
        except Exception as e:
            logger.error(f"Error saving BotState to DB: {e}")
            return False

    def load_state(self) -> bool:
        """Load state from database (v36.2)."""
        try:
            db = get_db()
            
            state = db.get_state('market_structure')
            if not state:
                return False
            
            self.trend = TrendState(state.get('trend', 'RANGE'))
            self.structure_quality = state.get('structure_quality', 0)
            self.last_confirmed_high = state.get('last_confirmed_high', 0.0)
            self.last_confirmed_low = state.get('last_confirmed_low', 0.0)
            
            last_bos_time_str = state.get('last_bos_time')
            if last_bos_time_str:
                self.last_bos_time = datetime.fromisoformat(last_bos_time_str)
            
            self.last_bos_score = state.get('last_bos_score', 0)
            
            last_bos_status_str = state.get('last_bos_status')
            if last_bos_status_str:
                self.last_bos_status = BOSStatus(last_bos_status_str)
            
            self.looking_for = state.get('looking_for')
            
            logger.info(f"BotState loaded from DB: {self.trend.value} | Quality: {self.structure_quality}")
            return True
        except Exception as e:
            logger.error(f"Error loading BotState from DB: {e}")
            return False

    def __repr__(self) -> str:
        return (
            f"BotState(trend={self.trend.value}, "
            f"quality={self.structure_quality}, "
            f"looking_for={self.looking_for})"
        )
