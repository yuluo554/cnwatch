"""Exit-code contract.

The numbers are part of the public CLI contract and are locked by tests.
Never renumber them; retire a code only through the documented process.
"""

EXIT_OK = 0
"""Everything requested completed."""

EXIT_DEGRADED = 1
"""Completed with a visible, reported degradation (e.g. some stocks failed)."""

EXIT_USAGE = 2
"""Bad usage or unusable input. Nothing was produced."""

EXIT_STAGE_NOT_READY = 3
"""The command belongs to a milestone that is not implemented yet.

Temporary semantics: each milestone removes its own entries from
``cnwatch.cli.STAGE_NOT_READY`` when the module lands. When every entry is
gone the constant and this code are retired together.
"""
