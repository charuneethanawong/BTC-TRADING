"""
Bot State Manager Module
Tracks current market structure state for v3.0 2-Phase Signal Generation
"""
from datetime import datetime, timezone
from typing import Dict, Optional

from ..utils.logger import get_logger
from ..enums import TrendState, BOSStatus

import json
import os
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
        self.last_confirmed_low = float('inf')
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
        
        Returns:
            Dictionary with current state
        """
        return {
            'trend': self.trend.value,
            'structure_quality': self.structure_quality,
            'last_confirmed_high': self.last_confirmed_high,
            'last_confirmed_low': self.last_confirmed_low,
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
    
    def save_state(self, filepath: str = "data/bot_state.json") -> bool:
        """Save current state to JSON file."""
        try:
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
            state = self.get_state_dict()
            with open(filepath, 'w') as f:
                json.dump(state, f, indent=4)
            return True
        except Exception as e:
            logger.error(f"Error saving BotState: {e}")
            return False

    def load_state(self, filepath: str = "data/bot_state.json") -> bool:
        """Load state from JSON file."""
        if not os.path.exists(filepath):
            return False
            
        try:
            with open(filepath, 'r') as f:
                state = json.load(f)
            
            self.trend = TrendState(state.get('trend', 'RANGE'))
            self.structure_quality = state.get('structure_quality', 0)
            self.last_confirmed_high = state.get('last_confirmed_high', 0.0)
            self.last_confirmed_low = state.get('last_confirmed_low', float('inf'))
            
            last_bos_time_str = state.get('last_bos_time')
            if last_bos_time_str:
                self.last_bos_time = datetime.fromisoformat(last_bos_time_str)
            
            self.last_bos_score = state.get('last_bos_score', 0)
            
            last_bos_status_str = state.get('last_bos_status')
            if last_bos_status_str:
                self.last_bos_status = BOSStatus(last_bos_status_str)
            
            self.looking_for = state.get('looking_for')
            
            logger.info(f"BotState loaded from {filepath}: {self.trend.value} | Quality: {self.structure_quality}")
            return True
        except Exception as e:
            logger.error(f"Error loading BotState: {e}")
            return False

    def __repr__(self) -> str:
        return (
            f"BotState(trend={self.trend.value}, "
            f"quality={self.structure_quality}, "
            f"looking_for={self.looking_for})"
        )
