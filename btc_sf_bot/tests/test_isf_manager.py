"""
Tests for IntegratedFlowManager.

NOTE: IntegratedFlowManager has been deleted as part of v4.9 M5 upgrade.
It was replaced by the new IPAAnalyzer and IOFAnalyzer in PHASE 1/2.
This test file is kept as a historical reference.
"""
import sys
import os
import unittest

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)


class TestIntegratedFlowManagerSkipped(unittest.TestCase):
    """IntegratedFlowManager has been deprecated — see IPAAnalyzer and IOFAnalyzer."""

    def test_deprecated_class(self):
        """This test is skipped because IntegratedFlowManager was deleted."""
        self.skipTest(
            "IntegratedFlowManager was removed in v4.9 M5 upgrade (PHASE 5). "
            "Use IPAAnalyzer (src/analysis/ipa_analyzer.py) or "
            "IOFAnalyzer (src/analysis/iof_analyzer.py) instead."
        )


if __name__ == '__main__':
    unittest.main()
