"""Locating the repository ``data/`` directory.

The frozen evidence (stock master, snapshots, truth ledgers) lives next to the
source tree, not inside the wheel: it is input for development and for the
offline tests, not a runtime resource. A source checkout and an editable
install therefore find it automatically; any other installation is told to
point ``--data-dir`` at a checkout instead of silently using empty data.

Development convenience must not collapse into guessing: there is exactly one
fallback (the checkout containing this package) and it is verified by the file
being looked for.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from .errors import UsageError
from .paths import Paths

ENV_DATA = "CNWATCH_DATA"
MASTER_RELPATH = Path("fixtures") / "stock-master.csv"
SNAPSHOTS_RELPATH = Path("snapshots")


def checkout_data_dir() -> Path:
    """``<checkout>/data`` for a source tree or an editable install."""

    return Path(__file__).resolve().parents[2] / "data"


def _candidates(explicit: Optional[str], home: Paths) -> list:
    candidates = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    env = os.environ.get(ENV_DATA)
    if env and env.strip():
        candidates.append(Path(env).expanduser())
    candidates.append(home.data_dir)
    candidates.append(checkout_data_dir())
    return candidates


def resolve_data_dir(
    explicit: Optional[str] = None,
    home: Optional[Paths] = None,
    require: Optional[Path] = None,
) -> Path:
    """First candidate that exists, optionally requiring a specific relative file."""

    paths = home if home is not None else Paths(home=Path.cwd())
    probe = require if require is not None else MASTER_RELPATH
    tried = []
    for candidate in _candidates(explicit, paths):
        resolved = candidate if candidate.is_absolute() else (Path.cwd() / candidate)
        resolved = resolved.resolve()
        tried.append(str(resolved))
        if (resolved / probe).is_file():
            return resolved
    raise UsageError(
        "cannot find the data directory: none of the candidates contains %s" % probe,
        hint="pass --data-dir <checkout>/data or set %s" % ENV_DATA,
    )


def stock_master_path(data_dir: Path) -> Path:
    return Path(data_dir) / MASTER_RELPATH


def snapshots_dir(data_dir: Path) -> Path:
    return Path(data_dir) / SNAPSHOTS_RELPATH
