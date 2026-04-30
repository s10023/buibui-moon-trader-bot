"""Signal package — split from analytics/signal_lib.py.

signal-1 (this PR) lands the lightweight type dataclasses moved from
signals/alert_formatter.py. signal-2 will extract scanner leaves
(_common, gates, resolvers, bt_cache, stats_context, cofire). signal-3
will move the scanner itself.
"""

from analytics.signal.types import ConfluenceData, SignalEvent, StatsContext

__all__ = ["ConfluenceData", "SignalEvent", "StatsContext"]
