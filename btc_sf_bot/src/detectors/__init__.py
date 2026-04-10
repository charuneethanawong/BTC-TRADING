"""
Signal Detectors — v51.0 (MOD-38)

Unified IPA detector (v51.0):
- IPA_OB + IPA_FVG → IPA (single detector)
- IPA_EMA deleted (no structural edge)

11 independent signal detectors:
- Order Flow (EVERY_60S): MOMENTUM, ABSORPTION, REVERSAL_OB, REVERSAL_OS
- IPA (CANDLE_CLOSE): IPA (unified)
- MEAN_REVERT: MEAN_REVERT
- VP (CANDLE_CLOSE): VP_BOUNCE
- VP (EVERY_60S): VP_BREAKOUT, VP_ABSORB, VP_REVERT, VP_POC
"""
from src.detectors.base import BaseDetector, SignalResult, DetectionContext
from src.detectors.ipa_shared import IPAShared, IPAContext
from src.detectors.momentum import MomentumDetector
from src.detectors.mean_revert import MeanRevertDetector
from src.detectors.absorption import AbsorptionDetector
from src.detectors.reversal import ReversalOBDetector, ReversalOSDetector
from src.detectors.ipa import IPADetector  # v51.0: unified IPA
from src.detectors.vp_bounce import VPBounceDetector
from src.detectors.vp_breakout import VPBreakoutDetector
from src.detectors.vp_absorb import VPAbsorbDetector
from src.detectors.vp_revert import VPRevertDetector
from src.detectors.vp_poc import VPPOCDetector

# Registry of all detector classes (v60.0: 11 detectors)
ALL_DETECTORS = [
    MomentumDetector,
    MeanRevertDetector,
    AbsorptionDetector,
    ReversalOBDetector,
    ReversalOSDetector,
    IPADetector,  # v51.0: unified IPA
    VPBounceDetector,
    VPBreakoutDetector,
    VPAbsorbDetector,
    VPRevertDetector,
    VPPOCDetector,
]

# Signal type constants (v60.0: 11 types)
SIGNAL_TYPES = [
    'MOMENTUM',
    'MEAN_REVERT',
    'ABSORPTION',
    'REVERSAL_OB',
    'REVERSAL_OS',
    'IPA',  # v51.0: unified (was IPA_OB, IPA_FVG, IPA_EMA)
    'VP_BOUNCE',
    'VP_BREAKOUT',
    'VP_ABSORB',
    'VP_REVERT',
    'VP_POC',
]

__all__ = [
    'BaseDetector',
    'SignalResult',
    'DetectionContext',
    'IPAShared',
    'IPAContext',
    'MomentumDetector',
    'MeanRevertDetector',
    'AbsorptionDetector',
    'ReversalOBDetector',
    'ReversalOSDetector',
    'IPADetector',  # v51.0: unified IPA
    'VPBounceDetector',
    'VPBreakoutDetector',
    'VPAbsorbDetector',
    'VPRevertDetector',
    'VPPOCDetector',
    'ALL_DETECTORS',
    'SIGNAL_TYPES',
]
