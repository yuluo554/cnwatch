"""Runtime path resolution.

Precedence: explicit ``--home`` > ``CNWATCH_HOME`` > current working
directory. Nothing here reads or writes the user's home directory unless the
caller explicitly points ``home`` at it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

ENV_HOME = "CNWATCH_HOME"
ENV_RESOURCES = "CNWATCH_RESOURCES"

DB_FILENAME = "cnwatch.db"


@dataclass(frozen=True)
class Paths:
    """All runtime paths derived from a single root."""

    home: Path

    @property
    def db_path(self) -> Path:
        return self.home / DB_FILENAME

    @property
    def out_dir(self) -> Path:
        return self.home / "out"

    @property
    def reports_dir(self) -> Path:
        return self.out_dir / "reports"

    @property
    def logs_dir(self) -> Path:
        return self.out_dir / "logs"

    @property
    def data_dir(self) -> Path:
        return self.home / "data"

    @property
    def snapshots_dir(self) -> Path:
        return self.data_dir / "snapshots"

    @property
    def pdf_dir(self) -> Path:
        return self.data_dir / "pdf"

    def ensure_runtime_dirs(self) -> None:
        """Create the directories runtime commands are allowed to write."""

        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)

    def resolve(self, value: str) -> Path:
        """Resolve a user-supplied path against ``home`` when relative."""

        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            candidate = self.home / candidate
        return candidate.resolve()


def resolve_home(
    home: Optional[str] = None,
    env: Optional[Mapping[str, str]] = None,
    cwd: Optional[Path] = None,
) -> Path:
    """Resolve the runtime root without creating anything."""

    environment = os.environ if env is None else env
    base = Path.cwd() if cwd is None else Path(cwd)

    raw = home if home is not None else environment.get(ENV_HOME)
    if raw is not None and str(raw).strip() != "":
        candidate = Path(str(raw)).expanduser()
        if not candidate.is_absolute():
            candidate = base / candidate
        return candidate.resolve()
    return base.resolve()


def paths_for(home: Optional[str] = None) -> Paths:
    return Paths(home=resolve_home(home))
