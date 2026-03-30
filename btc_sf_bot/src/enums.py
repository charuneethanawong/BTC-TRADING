"""
Common Enums for v3.0 Signal Generation
"""
from enum import Enum


class TrendState(Enum):
    """Trend direction state."""
    RANGE = "RANGE"
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"


class BOSStatus(Enum):
    """Break of Structure validation status."""
    CONFIRMED = "CONFIRMED"
    PENDING = "PENDING"
    SWEEP = "SWEEP"


class EntryType(Enum):
    """Entry signal type."""
    OB_ENTRY = "OB_ENTRY"
    FVG_ENTRY = "FVG_ENTRY"
    SWEEP_ENTRY = "SWEEP_ENTRY"
    ZONE_ENTRY = "ZONE_ENTRY"


class StructureStrength(Enum):
    """Market structure strength classification."""
    WEAK = "WEAK"
    MODERATE = "MODERATE"
    STRONG = "STRONG"


class StructureEvent(Enum):
    """Market structure event types."""
    BOS = "BOS"
    CHoCH = "CHoCH"
    CHoCH_INTERNAL = "CHoCH_INTERNAL"
    SWEEP = "SWEEP"
    RETEST = "RETEST"
    NONE = "NONE"
