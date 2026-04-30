"""Legacy import shim. Real implementation lives in analytics/backtest/."""

from analytics.backtest import *  # noqa: F401,F403
from analytics.backtest import __all__  # noqa: F401

# De-facto-public underscore helpers (imported externally by signal_lib + tests):
from analytics.backtest.combo import _find_cofire_signals  # noqa: F401
from analytics.backtest.cross_tf import _find_cross_tf_signals  # noqa: F401
from analytics.backtest.engine import _compute_atr14  # noqa: F401
from analytics.backtest.gates import _is_low_volume, _is_volume_spike  # noqa: F401
