"""Top-level pytest config: make src/ importable and load the ReaProof plugin.

The plugin (auto-repeat/quarantine, report emission, mutation tracking) must be
registered from the rootdir conftest; when the package is pip-installed it also loads
via the ``pytest11`` entry point.
"""
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

pytest_plugins = ["reaproof.runner.pytest_plugin", "pytester"]
