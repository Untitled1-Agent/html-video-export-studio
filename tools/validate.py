"""Validation gate. Run with xvfb-run -a on Linux to include desktop checks."""
import ast, compileall, sys, unittest, threading
uncaught=[]
sys.unraisablehook=lambda error: uncaught.append(repr(error.exc_value))
threading.excepthook=lambda error: uncaught.append(repr(error.exc_value))
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
if not compileall.compile_dir(ROOT, quiet=1, rx=__import__('re').compile(r'[\\/](?:[.]venv|build|dist)[\\/]')):
    raise SystemExit('Compilation failed.')
suite=unittest.defaultTestLoader.discover(str(ROOT/'tests'))
result=unittest.TextTestRunner(verbosity=2).run(suite)
if not result.wasSuccessful() or result.skipped or uncaught:
    if uncaught: print("Unhandled exceptions:", uncaught, file=sys.stderr)
    raise SystemExit('Validation failed or skipped required tests. Use a desktop display / xvfb-run and install Chromium.')
print(f'VALIDATED: {result.testsRun} tests; no skips.')
