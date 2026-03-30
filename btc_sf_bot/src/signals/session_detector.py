"""
Session Detector — v4.9 M5 Upgrade

Detects the current trading session based on UTC time.

Sessions:
  - ASIA:       00:00–08:00 UTC  (Quiet, low volume)
  - LONDON:     08:00–13:00 UTC  (Active, London open)
  - LONDON-NY:  13:00–16:00 UTC  (Most active, overlap)
  - NY:         16:00–21:00 UTC  (Active, NY open)
  - ASIA-LATE:  21:00–24:00 UTC  (Quiet, fading volume)

Used for:
  - Volume threshold adjustments
  - Wall size thresholds
  - RR target adjustments
  - Cooldown distance settings
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Any

from src.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class SessionInfo:
    """Information about the current trading session."""
    name: str                    # 'ASIA' | 'LONDON' | 'LONDON-NY' | 'NY' | 'ASIA-LATE'
    start_hour: int             # Start hour in UTC
    end_hour: int               # End hour in UTC
    is_active: bool             # Whether this session is currently active
    volume_mult: float          # Volume multiplier for this session
    atr_mult: float             # ATR multiplier for this session
    cooldown_distance: float    # Cooldown price distance in USD

    def __str__(self) -> str:
        return f"Session: {self.name} | Active: {self.is_active} | Vol: {self.volume_mult}x"


# Session definitions (all times in UTC)
SESSIONS: Dict[str, Dict[str, Any]] = {
    'ASIA': {
        'start': 0,     # 00:00 UTC
        'end': 8,       # 08:00 UTC
        'volume_mult': 1.2,
        'atr_mult': 0.8,
        'cooldown': 50,     # $50 — quiet market
    },
    'LONDON': {
        'start': 8,    # 08:00 UTC
        'end': 13,     # 13:00 UTC
        'volume_mult': 1.3,
        'atr_mult': 1.0,
        'cooldown': 80,     # $80
    },
    'LONDON-NY': {
        'start': 13,   # 13:00 UTC
        'end': 16,     # 16:00 UTC
        'volume_mult': 1.3,
        'atr_mult': 1.0,
        'cooldown': 80,
    },
    'NY': {
        'start': 16,   # 16:00 UTC
        'end': 21,     # 21:00 UTC
        'volume_mult': 1.3,
        'atr_mult': 1.0,
        'cooldown': 80,
    },
    'ASIA-LATE': {
        'start': 21,   # 21:00 UTC
        'end': 24,     # 24:00 UTC
        'volume_mult': 1.1,
        'atr_mult': 0.7,
        'cooldown': 50,
    },
}


class SessionDetector:
    """
    Detects the current trading session based on UTC time.

    Usage:
        detector = SessionDetector()
        session = detector.get_current_session()
        print(f"Current session: {session.name}")

        # Or get full info
        info = detector.get_session_info()
        print(f"Volume mult: {info.volume_mult}")
    """

    def __init__(self, config: dict = None):
        self.config = config or {}
        self._current_session: str = 'LONDON'  # Default
        self._last_update_hour: int = -1

    def get_current_session(self) -> str:
        """
        Get the current session name.
        Caches result per hour to avoid repeated datetime calls.
        """
        now_utc = datetime.now(timezone.utc)
        current_hour = now_utc.hour

        # Only recalculate if hour changed
        if current_hour != self._last_update_hour:
            self._current_session = self._detect_session(current_hour)
            self._last_update_hour = current_hour
            logger.debug(f"[Session] Detected: {self._current_session} (UTC {current_hour}:00)")

        return self._current_session

    def get_session_info(self) -> SessionInfo:
        """Get full SessionInfo for the current session."""
        session_name = self.get_current_session()
        session_def = SESSIONS.get(session_name, SESSIONS['LONDON'])

        now_utc = datetime.now(timezone.utc)
        current_hour = now_utc.hour

        # Check if session is currently active
        start = session_def['start']
        end = session_def['end']
        is_active = start <= current_hour < end

        return SessionInfo(
            name=session_name,
            start_hour=start,
            end_hour=end,
            is_active=is_active,
            volume_mult=session_def['volume_mult'],
            atr_mult=session_def['atr_mult'],
            cooldown_distance=session_def['cooldown'],
        )

    def _detect_session(self, utc_hour: int) -> str:
        """
        Detect session from UTC hour.
        Order matters: check ASIA-LATE first (21-24), then others.
        """
        if 21 <= utc_hour < 24:
            return 'ASIA-LATE'
        elif 0 <= utc_hour < 8:
            return 'ASIA'
        elif 8 <= utc_hour < 13:
            return 'LONDON'
        elif 13 <= utc_hour < 16:
            return 'LONDON-NY'
        elif 16 <= utc_hour < 21:
            return 'NY'
        else:
            return 'LONDON'  # Default fallback

    def get_session_thresholds(self, session: str = None) -> Dict[str, Any]:
        """
        Get threshold adjustments for a session.
        Used by analyzers and signal gate.
        """
        session_name = session or self.get_current_session()
        return SESSIONS.get(session_name, SESSIONS['LONDON'])

    def is_kill_zone(self) -> bool:
        """
        Check if current time is within a kill zone.
        Kill zones are the most volatile periods of each session.
        """
        now_utc = datetime.now(timezone.utc)
        hour = now_utc.hour

        # London kill zone: 08:00-10:00 UTC
        if 8 <= hour < 10:
            return True
        # NY kill zone: 13:00-15:00 UTC (LONDON-NY overlap)
        if 13 <= hour < 15:
            return True
        return False

    def get_next_session_change(self) -> Dict[str, Any]:
        """
        Get information about when the next session change occurs.
        Useful for logging and monitoring.
        """
        now_utc = datetime.now(timezone.utc)
        current_hour = now_utc.hour
        current_session = self.get_current_session()
        session_def = SESSIONS.get(current_session, SESSIONS['LONDON'])

        # Calculate hours until next session
        end_hour = session_def['end']

        if current_hour < end_hour:
            hours_remaining = end_hour - current_hour
        else:
            # Wrapped to next day
            hours_remaining = (24 - current_hour) + end_hour

        return {
            'current_session': current_session,
            'next_session': self._get_next_session_name(current_session),
            'hours_remaining': hours_remaining,
            'is_kill_zone': self.is_kill_zone(),
        }

    def _get_next_session_name(self, current: str) -> str:
        """Get the next session name after the current one."""
        order = ['ASIA', 'LONDON', 'LONDON-NY', 'NY', 'ASIA-LATE']
        try:
            idx = order.index(current)
            return order[(idx + 1) % len(order)]
        except ValueError:
            return 'LONDON'

    def to_dict(self) -> Dict[str, Any]:
        """Serialize current session state to dict."""
        info = self.get_session_info()
        return {
            'current_session': info.name,
            'is_active': info.is_active,
            'volume_mult': info.volume_mult,
            'atr_mult': info.atr_mult,
            'cooldown_distance': info.cooldown_distance,
            'is_kill_zone': self.is_kill_zone(),
            'next_change': self.get_next_session_change(),
        }
