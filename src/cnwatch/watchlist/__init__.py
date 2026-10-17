"""Watchlist: the user's self-selected stocks and their trusted ``orgId``.

The package never derives an ``orgId`` from a code. It is looked up in the
frozen stock master (``data/fixtures/stock-master.csv``, derived from the M1
snapshot) because the prefix is not predictable: ``300750`` maps to
``GD165627``, ``601398`` to ``jjxt0000019`` and ``002594`` to ``gshk0001211``.
"""

from .models import GROUP_NAMES, StockMaster, WatchlistEntry
from .service import WatchlistService, import_rows_from_csv, load_stock_master

__all__ = [
    "GROUP_NAMES",
    "StockMaster",
    "WatchlistEntry",
    "WatchlistService",
    "import_rows_from_csv",
    "load_stock_master",
]
