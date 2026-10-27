"""Make `import stitch` work however pytest was invoked.

The package is not installed site-wide during development, so the
stitch-python/ directory must be on sys.path. Running the suite from
inside stitch-python/ works because that directory lands on the path;
running `python -m pytest stitch-python/tests/` from the repository
root used to fail collection with 9 "No module named 'stitch'" errors
(cold-test finding, 2026-10-27). This conftest puts the package's
parent directory on sys.path explicitly, so both invocations work.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
