"""Signal package — leaves split in signal-2; scanner moves in signal-3.

signal-1 landed the lightweight type dataclasses moved from signals/alert_formatter.py.
signal-2 (this PR) extracts the scanner leaves (`_common`, `gates`, `resolvers`,
`bt_cache`, `stats_context`, `cofire`). signal-3 will move `scan_symbol` /
`run_scan_cycle` into `scanner.py` and reduce `signal_lib.py` to a re-export shim.
"""

from analytics.signal._common import (
    _CANDLE_CLOSE_BUFFER_SECS,
    _SCAN_WINDOW,
    _bt_mem_cache,
    _fmt_hold,
    _reset_bt_cache,
    parse_timeframe_secs,
    secs_until_next_boundary,
)
from analytics.signal.bt_cache import _backtest_summary, _compute_backtest
from analytics.signal.cofire import (
    _find_cross_tf_cofire,
    _find_live_cofire,
    _parse_htf_ltf_pairs,
)
from analytics.signal.gates import _filter_signals_by_adr, _is_adr_exempt
from analytics.signal.resolvers import (
    _resolve_atr_sl_multiplier,
    _resolve_sl_pct,
    _resolve_tp_r,
    _resolve_volume_spike_boost,
    _resolve_volume_spike_boost_long,
    _resolve_volume_spike_boost_short,
    _resolve_volume_suppress,
    _resolve_volume_suppress_long,
    _resolve_volume_suppress_short,
)
from analytics.signal.stats_context import _compute_stats_context
from analytics.signal.types import ConfluenceData, SignalEvent, StatsContext

__all__ = [
    "ConfluenceData",
    "SignalEvent",
    "StatsContext",
    "_CANDLE_CLOSE_BUFFER_SECS",
    "_SCAN_WINDOW",
    "_backtest_summary",
    "_bt_mem_cache",
    "_compute_backtest",
    "_compute_stats_context",
    "_filter_signals_by_adr",
    "_find_cross_tf_cofire",
    "_find_live_cofire",
    "_fmt_hold",
    "_is_adr_exempt",
    "_parse_htf_ltf_pairs",
    "_reset_bt_cache",
    "_resolve_atr_sl_multiplier",
    "_resolve_sl_pct",
    "_resolve_tp_r",
    "_resolve_volume_spike_boost",
    "_resolve_volume_spike_boost_long",
    "_resolve_volume_spike_boost_short",
    "_resolve_volume_suppress",
    "_resolve_volume_suppress_long",
    "_resolve_volume_suppress_short",
    "parse_timeframe_secs",
    "secs_until_next_boundary",
]
