"""Per-series OHLCV staleness — the guard that would have caught ST61a on day one.

Measured 2026-08-23: **22 of 25 universe symbols were frozen on 1h (17.9 days),
4h (61.2 days) and 1w (76.2 days)**, all of them `TRADING`. Nothing was broken;
nothing was watching. The routine refresh is split across two callers that each
cover a different slice, and the union had holes:

| caller | symbols | timeframes |
| --- | --- | --- |
| `signal-watch` (15-min timer) | `coins.json` majors | 15m / 1h / 4h |
| `make buibui-universe-sync` | the 25-symbol universe | **1d only** |
| nothing at all | the universe | **1h · 4h · 1w** |

`daily_check.py`'s existing coverage line asks only about the universe's **1d**
bars — the shape of the 2026-07-23 failure it was written for — so it stayed
green for the eleven weeks the other three timeframes sat frozen.

Two properties decide whether a check of this kind is worth anything, and
both are why this module is not three lines of SQL in the caller:

- **Staleness is only meaningful in BAR units.** Three days behind is healthy on
  1w and 72 bars behind on 1h. A single wall-clock threshold silently picks a
  timeframe to be wrong about, which is how a guard ends up either mute or
  permanently red.
- **The newest stored bar is normally IN PROGRESS.** `analytics.data_sync.sync`
  re-fetches from `latest` *inclusive* so a mid-formation candle is later
  overwritten with its final values, and `ohlcv_all` carries no `is_closed`
  column to tell the two apart. A healthy series therefore always trails by
  under one bar, and a guard that reads "not yet closed" as "stale" reds
  everything forever.

This lives in `tools/` rather than inside `docs/plans/daily_check.py` for the
reason `tools/media_probe.py` states: that file is gitignored, so logic buried
there reaches no reclone, no CI and no review surface. → ST28's sixth
powered-null site sat in exactly such a file.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

# Bar length per timeframe. Mirrors `analytics/store/market_data.py`'s map; kept
# local so this module stays importable with no DB or project session.
BAR_MS: dict[str, int] = {
    "15m": 900_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
    "1w": 604_800_000,
}


@dataclass(frozen=True)
class Series:
    """The newest bar held for one (symbol, timeframe)."""

    symbol: str
    timeframe: str
    newest_open_time: int


@dataclass(frozen=True)
class Staleness:
    """A series trailing further behind than the tolerance allows.

    `age_bars` is None when the timeframe has no known bar length — reported
    rather than skipped, so "we do not know how to check this" can never render
    the same as "this is fresh".
    """

    symbol: str
    timeframe: str
    newest_open_time: int
    age_bars: float | None


def stale_series(
    rows: list[Series],
    *,
    now_ms: int,
    tolerance_bars: float = 2.0,
    ignore_symbols: frozenset[str] = frozenset(),
) -> list[Staleness]:
    """Series trailing more than `tolerance_bars` behind `now_ms`, worst first.

    A series absent from `rows` is never reported: you cannot be stale on a
    series you have never held, and 15m exists for three symbols BY DESIGN.
    Coverage is a different question from freshness, and conflating them here
    would red the 22 symbols that correctly hold no 15m bars.
    """
    out: list[Staleness] = []
    for row in rows:
        if row.symbol in ignore_symbols:
            continue
        bar_ms = BAR_MS.get(row.timeframe)
        if bar_ms is None:
            out.append(Staleness(row.symbol, row.timeframe, row.newest_open_time, None))
            continue
        age_bars = (now_ms - row.newest_open_time) / bar_ms
        if age_bars > tolerance_bars:
            out.append(
                Staleness(row.symbol, row.timeframe, row.newest_open_time, age_bars)
            )
    # Unknown timeframes sort first: an unmeasurable series is the more urgent
    # finding, since nothing can say how far behind it is.
    return sorted(out, key=lambda s: (s.age_bars is not None, -(s.age_bars or 0.0)))


# Scans `ohlcv`, the VIEW — deliberately the opposite choice to
# `FABRICATED_CVD_SQL`, which scans `ohlcv_all` because it guards history across
# every venue and the view would blind it. This check asks a different question:
# *is what consumers actually read fresh?* Consumers read the view, so a fresh
# tail written under a venue the view does not surface is not freshness — it is
# a series that still looks frozen to everything downstream.
COVERAGE_SQL: str = """
SELECT symbol, timeframe, max(open_time)
FROM ohlcv
GROUP BY 1, 2
"""


def series_from_rows(rows: Iterable[tuple[str, str, int]]) -> list[Series]:
    """Adapt `COVERAGE_SQL` result rows into `Series`."""
    return [Series(str(sym), str(tf), int(newest)) for sym, tf, newest in rows]
