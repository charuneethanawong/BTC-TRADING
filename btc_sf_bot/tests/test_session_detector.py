"""
Unit Tests for Session Detector (PHASE 3)
"""
import sys
from pathlib import Path

src_path = str(Path(__file__).parent.parent / 'src')
if src_path not in sys.path:
    sys.path.insert(0, src_path)

import pytest
import importlib.util, os

def import_from_file(filepath):
    spec = importlib.util.spec_from_file_location(os.path.basename(filepath).replace('.py',''), filepath)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module.__name__] = module
    spec.loader.exec_module(module)
    return module

base = Path(__file__).parent.parent / 'src'
sess_mod = import_from_file(base / 'signals' / 'session_detector.py')

SessionDetector = sess_mod.SessionDetector
SessionInfo = sess_mod.SessionInfo


class TestSessionDetector:
    def test_detector_initialization(self):
        detector = SessionDetector()
        session = detector.get_current_session()
        assert session in ('ASIA', 'LONDON', 'LONDON-NY', 'NY', 'ASIA-LATE')

    def test_session_info(self):
        detector = SessionDetector()
        info = detector.get_session_info()
        assert isinstance(info, SessionInfo)
        assert info.name in ('ASIA', 'LONDON', 'LONDON-NY', 'NY', 'ASIA-LATE')
        assert 0.8 <= info.volume_mult <= 1.3
        assert info.cooldown_distance in (50, 80)

    def test_session_thresholds(self):
        detector = SessionDetector()
        asia = detector.get_session_thresholds('ASIA')
        assert asia['volume_mult'] == 1.2
        assert asia['cooldown'] == 50

        london = detector.get_session_thresholds('LONDON')
        assert london['volume_mult'] == 1.3
        assert london['cooldown'] == 80

        ny = detector.get_session_thresholds('NY')
        assert ny['volume_mult'] == 1.3

    def test_to_dict(self):
        detector = SessionDetector()
        d = detector.to_dict()
        assert 'current_session' in d
        assert 'is_kill_zone' in d
        assert 'cooldown_distance' in d
        assert 'next_change' in d

    def test_next_session_change(self):
        detector = SessionDetector()
        next_change = detector.get_next_session_change()
        assert 'current_session' in next_change
        assert 'next_session' in next_change
        assert 'hours_remaining' in next_change


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
