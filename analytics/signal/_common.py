"""Signal package common helpers — constants, in-memory backtest cache, time helpers.

The `_bt_mem_cache` dict is defined exactly once here. Every consumer must
`from analytics.signal._common import _bt_mem_cache` and mutate in place
(`.clear()`, `[k] = v`, `del [k]`). NEVER `_bt_mem_cache = {}` after import —
that re-binds a local and breaks cache coherence.
"""

import math
import time

from analytics.backtest_lib import BacktestResult
from analytics.data_store import BacktestSnapshot

_CANDLE_CLOSE_BUFFER_SECS = 10

# Detectors only need recent candles to check the latest signal (max lookback = 100).
# Slicing to this window before scan_symbol drastically reduces Phase 2 time
# (e.g. 15m/200d = 19,200 rows → 200 rows = ~96× less data for pandas ops).
# The full OHLCV window is preserved in ohlcv_map for _compute_backtest in Phase 3.
_SCAN_WINDOW = 200

# --- per-timeframe override (SoT N8) -----------------------------------------
# The window is not only a performance knob — it is the REACH of `--catch-up`,
# and therefore the hard ceiling on N8 boundary recovery.
#
# The three configs partition the week with no overlap, so a candle is only
# scannable on a day whose config admits its weekday. The wait between a missed
# day and its next scannable day runs 3 days (Fri→Mon) to 6 days (Sun→next Sat).
# A candle that ages out of the window in the meantime can never be replayed, no
# matter how the watermark is keyed.
#
# At a flat 200 bars: 1h reaches 8.3d, 4h 33d, 1d 200d — all clear of the 6-day
# worst case. **15m reaches 2.1 days**, short of even the 3-day minimum, so every
# 15m boundary candle was structurally unrecoverable. 15m is 64.4% of the live
# ledger (2,846 of 4,422 rows), so scoping the watermark without this would have
# fixed 35.6% of the affected volume while presenting as a complete fix.
#
# 600 bars = 6.25 days, just past the worst gap. Measured cost 2026-08-07:
# 46ms→107ms per symbol (2.31×), and only BTC/ETH/SOL carry 15m at all, so the
# whole-cycle delta is ~0.2s against a ~20s median cycle in a 900s budget.
# Other timeframes are deliberately NOT widened — they already reach, and width
# they do not need is data loaded every cycle for nothing.
_SCAN_WINDOW_BY_TF: dict[str, int] = {"15m": 600}


def scan_window(timeframe: str) -> int:
    """Bars to slice for detectors on this timeframe.

    Sized by catch-up REACH, not by detector lookback — see above.
    """
    return _SCAN_WINDOW_BY_TF.get(timeframe, _SCAN_WINDOW)


# Two-layer backtest cache: L1 (module dict, fast) backed by L2 (DuckDB, survives restarts).
# Keys are 24-char hex strings from _make_bt_cache_key(run_id, last_candle_ts).
_bt_mem_cache: dict[str, BacktestResult | BacktestSnapshot | None] = {}


def _reset_bt_cache() -> None:
    """Clear L1 memory cache. Call in test fixtures to prevent state bleed."""
    _bt_mem_cache.clear()


def _fmt_hold(hours: float) -> str:
    """Format median hold time: '~4h', '~3d'."""
    if hours >= 48:
        return f"~{hours / 24:.0f}d"
    return f"~{hours:.0f}h"


def parse_timeframe_secs(tf: str) -> int:
    """Convert a timeframe string to seconds (e.g. '4h' → 14400, '15m' → 900)."""
    units = {"m": 60, "h": 3600, "d": 86400}
    return int(tf[:-1]) * units[tf[-1]]


def secs_until_next_boundary(timeframes: list[str]) -> tuple[float, float]:
    """Return (sleep_seconds, wakeup_unix_timestamp) for the next candle close.

    Wakes at the earliest upcoming boundary + a small buffer so Binance has
    time to finalise the candle (e.g. 04:00:10, not 04:00:00).
    """
    now = time.time()
    next_wakeups = []
    for tf in timeframes:
        interval = parse_timeframe_secs(tf)
        next_close = math.ceil(now / interval) * interval
        next_wakeups.append(next_close + _CANDLE_CLOSE_BUFFER_SECS)
    wake_ts = min(next_wakeups)
    return max(0.0, wake_ts - now), wake_ts


def realised_rr(
    *, entry: float, sl_price: float, tp_price: float, fallback: float
) -> float:
    """R multiple the stored `tp_price` actually pays against the stored risk.

    SoT ST39. The requested `tp_r` only equals this when the TP was derived from
    it; a structural TP is taken from the detector's own level, so storing the
    request leaves `rr_ratio` describing a target the row does not carry. Both
    the ledger writer and the resolver read this so they cannot disagree.
    """
    risk = abs(entry - sl_price)
    if risk <= 0.0:
        return fallback
    return abs(tp_price - entry) / risk
