"""Desktop tests; Linux: xvfb-run -a python tests/gui_smoke.py."""
import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
suite=unittest.defaultTestLoader.discover(str(Path(__file__).parent),pattern='test_ui_review.py')
result=unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(0 if result.wasSuccessful() and not result.skipped else 1)
