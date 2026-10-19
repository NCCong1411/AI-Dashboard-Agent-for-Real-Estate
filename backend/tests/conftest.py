"""Allow standalone backend test paths to run from the repository root."""

from pathlib import Path
import sys

backend = str(Path(__file__).resolve().parents[1])
if backend not in sys.path:
    sys.path.insert(0, backend)
