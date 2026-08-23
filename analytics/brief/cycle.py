"""ST54 — the bundle-level bear score (a NUMBER, never a gate).

The score counts how many of six long-horizon moving averages BTC's completed
daily close sits below, 0-6. Evidence is SoT ST53: the effect is a **CLIFF at
6, not a gradient** — scores 1-5 are noise, while score 6 reads -3.25% mean /
-5.14% median / 70% win over n=73 dates (Welch t=-4.76).

⚠ **Those 73 dates span roughly three distinct bear markets, so n_eff is ~3.**
That is why this module only ever produces a number to display: nothing here
may gate, size or suppress anything, and no caller should make it do so.

⚠ **The four WEEKLY averages are resampled from 1d on purpose, and the reason is
LOOK-AHEAD, not staleness.** The resample drops the in-progress week, which IS
the no-look-ahead property — `tests/test_brief_cycle.py` pins it with an injected
leaky resampler as the specificity control, because a guard with only a
"nothing changed" assertion cannot tell clean from blind.

⚠ **Do not "simplify" this to read the `1w` table now that it is fresh.** Those
bars were stale since 2026-06-08 (ST53's side finding) until ST61a put every
timeframe on the routine sync on 2026-08-23, so the old second reason is gone —
but the first one is stronger than ever: `sync` stores the FORMING bar on
purpose and `ohlcv_all` carries no `is_closed` column, so the newest `1w` row is
an in-progress week for up to seven days and nothing in the schema says so.
Reading it would hand this module exactly the look-ahead the resample exists to
remove.
"""

from __future__ import annotations

import logging
import math

import pandas as pd

from analytics.brief.types import CycleState

logger = logging.getLogger(__name__)

#: The six averages, in the order ST53 measured them. Display order too — the
#: renderer reads this tuple rather than restating the names.
_CYCLE_MAS: tuple[str, ...] = (
    "50W SMA",
    "50W EMA",
    "200D EMA",
    "200D SMA",
    "20W SMA",
    "21W EMA",
)

#: A 50-week average needs 50 CLOSED weeks (~350 days) plus EMA warmup, so the
#: brief's shared 500-day daily window is too thin. 1d bars are cheap.
CYCLE_FETCH_DAYS = 365 * 4

_WEEK = pd.Timedelta(days=7)


def _closed_weekly_closes(daily: pd.DataFrame, as_of_ms: int) -> pd.Series:
    """Weekly closes resampled from 1d, EXCLUDING the in-progress week.

    Buckets run Mon 00:00 (inclusive) to the following Mon (exclusive) and are
    labelled by their Monday, hence ``label="left", closed="left"``. A bucket is
    kept only once its own week has fully elapsed by ``as_of_ms`` — dropping
    that filter is exactly the look-ahead the tests mutate in.
    """
    ts = pd.to_datetime(daily["open_time"], unit="ms", utc=True)
    weekly = (
        daily.assign(_ts=ts)
        .set_index("_ts")["close"]
        .astype(float)
        .resample("W-MON", label="left", closed="left")
        .last()
        .dropna()
    )
    as_of = pd.Timestamp(as_of_ms, unit="ms", tz="UTC")
    return weekly[weekly.index + _WEEK <= as_of]


def _pick_trigger(
    close: float, mas: dict[str, float]
) -> tuple[str, float, float] | None:
    """The nearest average BELOW price — the level whose break adds one point.

    ST53's worked example is the contract: BTC at 69,600 sits above three
    averages and the trigger is the HIGHEST of them (21W EMA, 68,767, -1.20%),
    because that is the one a close can cross to move the score by exactly one.

    Returns ``None`` at both ends. At the top of the range no average sits below
    price at all; at the bottom every average does, and ST54 pins that end as
    "no trigger" too, so the two extremes read alike.
    """
    below = {name: value for name, value in mas.items() if value < close}
    if not below or len(below) == len(mas):
        return None
    name = max(below, key=lambda key: below[key])
    price = below[name]
    return name, price, (price - close) / close * 100.0


def _ma_frame(daily: pd.DataFrame, as_of_ms: int) -> tuple[pd.DataFrame, pd.Series]:
    """(per-day levels for all six averages, the aligned daily close).

    Both the current reading and ``days_at_score`` come off this one frame, so
    the printed score and the run length cannot disagree about a boundary day.
    """
    ts = pd.to_datetime(daily["open_time"], unit="ms", utc=True)
    close = pd.Series(daily["close"].astype(float).to_numpy(), index=ts)

    weekly = _closed_weekly_closes(daily, as_of_ms)
    # A week's average is knowable only once that week has CLOSED, so its value
    # takes effect from the following Monday. Without this shift a Wednesday
    # would read an average containing Wednesday's own close.
    effective = weekly.index + _WEEK

    columns: dict[str, pd.Series] = {
        "200D SMA": close.rolling(200).mean(),
        "200D EMA": close.ewm(span=200, adjust=False).mean(),
    }
    weekly_specs: tuple[tuple[str, pd.Series], ...] = (
        ("50W SMA", weekly.rolling(50).mean()),
        ("50W EMA", weekly.ewm(span=50, adjust=False).mean()),
        ("20W SMA", weekly.rolling(20).mean()),
        ("21W EMA", weekly.ewm(span=21, adjust=False).mean()),
    )
    for name, series in weekly_specs:
        shifted = pd.Series(series.to_numpy(), index=effective)
        columns[name] = shifted.reindex(close.index, method="ffill")

    # An EMA is defined from its first observation, which would let a 50-week
    # average report off three weeks. Blank it until the span is genuinely met.
    for name, span, source in (
        ("200D EMA", 200, close),
        ("50W EMA", 50, weekly),
        ("21W EMA", 21, weekly),
    ):
        if len(source) < span:
            columns[name] = pd.Series(float("nan"), index=close.index)

    return pd.DataFrame({name: columns[name] for name in _CYCLE_MAS}), close


def _trailing_run(scores: pd.Series) -> int | None:
    """How many consecutive completed days have carried the current score."""
    valid = scores.dropna()
    if valid.empty:
        return None
    current = valid.iloc[-1]
    run = 0
    for value in reversed(valid.to_numpy().tolist()):
        if value != current:
            break
        run += 1
    return run


def build_cycle_state(
    daily: pd.DataFrame, as_of_ms: int
) -> tuple[CycleState | None, list[str]]:
    """(CycleState | None, notes) — mirrors ``build_indicator_state``'s shape.

    Returns ``None`` rather than a partial score when any of the six averages
    is unavailable: a "3" computed from four averages is a different statistic
    wearing the same label, and the reader cannot tell them apart.
    """
    if daily is None or daily.empty:
        return None, ["cycle: no daily bars"]

    try:
        frame, close = _ma_frame(daily, as_of_ms)
    except Exception as exc:  # independence contract — never kill the brief
        logger.warning("brief cycle: failed: %s", exc)
        return None, [f"cycle failed ({exc})"]

    available = frame.notna().all(axis=1)
    if available.empty or not bool(available.iloc[-1]):
        missing = [name for name in _CYCLE_MAS if pd.isna(frame[name].iloc[-1])]
        return None, [f"cycle: not enough history for {', '.join(missing)}"]

    levels = {name: float(frame[name].iloc[-1]) for name in _CYCLE_MAS}
    last_close = float(close.iloc[-1])
    if not math.isfinite(last_close):
        return None, ["cycle: non-finite daily close"]

    below = tuple(name for name in _CYCLE_MAS if last_close < levels[name])
    trigger = _pick_trigger(last_close, levels)
    scores = frame.lt(close, axis=0).sum(axis=1).where(available)

    return (
        CycleState(
            score=len(below),
            total=len(_CYCLE_MAS),
            below=below,
            close=last_close,
            trigger_name=None if trigger is None else trigger[0],
            trigger_price=None if trigger is None else trigger[1],
            trigger_dist_pct=None if trigger is None else trigger[2],
            days_at_score=_trailing_run(scores),
        ),
        [],
    )
