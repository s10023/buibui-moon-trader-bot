"""Legacy import shim. Real implementation lives in analytics/stats/.

Kept so existing callers (web routers, tests, signal_lib) continue to work
without edits.
"""

from analytics.stats import *  # noqa: F401,F403
from analytics.stats import __all__  # noqa: F401
