"""Make the package importable from a source checkout.

``tests`` runs before any install in CI smoke jobs and for contributors who
have not run ``pip install -e .`` yet. The real installation path is still
exercised by the console-script smoke step in CI.
"""

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
