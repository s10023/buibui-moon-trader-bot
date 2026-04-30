"""Legacy import shim. Real implementation lives in analytics/store/.

Kept so 30+ existing callers (web routers, signal_lib, backtest_lib, runners,
tests) continue to work without edits.
"""

from analytics.store import *  # noqa: F401,F403
from analytics.store import __all__  # noqa: F401

# Explicit re-exports for underscore-prefixed names (skipped by `import *`).
from analytics.store._common import _upsert  # noqa: F401
from analytics.store.backtest_cache import _make_bt_cache_key  # noqa: F401
from analytics.store.backtest_runs import _backtest_run_id  # noqa: F401
from analytics.store.signals import _OUTCOME_COLUMNS  # noqa: F401
