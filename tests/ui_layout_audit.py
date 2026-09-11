"""Minimum-window layout assertion using the same public app constructor."""
import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from test_ui_review import UIReviewTests
suite=unittest.TestSuite([UIReviewTests('test_minimum_layout_and_tabs')])
result=unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(0 if result.wasSuccessful() and not result.skipped else 1)
