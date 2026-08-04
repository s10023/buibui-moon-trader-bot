"""H15 USD/JPY carry-unwind state tag — pure library.

Spec: docs/superpowers/specs/2026-08-04-h15-usdjpy-carry-unwind-design.md

PURE: no DB, no network, no file IO. Every threshold here is a-priori and was
written down in the spec before any conditional outcome was computed.

Three traps this module exists to contain, all measured 2026-08-04:

1. Yahoo bars are anchored to Europe/London midnight, so a bar's UTC date is
   off by one for ~58% of the sample and the error flips with DST. Everything
   is keyed by ``london_trading_date``.
2. Yen strength is USD/JPY going DOWN, so ``label_magnitude_states`` maps a
   NEGATIVE z-score to ``YEN_STRONG``. Inverting this flips the verdict and
   nothing downstream would notice.
3. A week is only known after its last bar closes. ``build_weekly_from_daily``
   publishes ``known_at_ms``, and ``expand_to_days`` admits a week only once
   ``known_at_ms <= day_start``.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from analytics.state_audit import DAY_MS, causal_zscore

_LONDON = ZoneInfo("Europe/London")

RUN_LE1 = "run_le1"
RUN_EQ2 = "run_eq2"
RUN_GE3 = "run_ge3"

YEN_STRONG = "yen_strong"
MAG_NEUTRAL = "mag_neutral"
YEN_WEAK = "yen_weak"

MAG_WINDOW = 52
MAG_SPAN = 4
MAG_THRESHOLD = 1.0

_RUN_STATES = frozenset({RUN_LE1, RUN_EQ2, RUN_GE3})
_MAG_STATES = frozenset({YEN_STRONG, MAG_NEUTRAL, YEN_WEAK})


def london_trading_date(ts_ms: int) -> str:
    """The bar's Europe/London calendar date — the true FX trading day."""
    return datetime.fromtimestamp(ts_ms / 1000, _LONDON).strftime("%Y-%m-%d")


def iso_week_key(date_str: str) -> str:
    """``YYYY-Www`` ISO-8601 week key — Monday-anchored and year-boundary safe."""
    iso = date.fromisoformat(date_str).isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def build_weekly_from_daily(daily: pd.DataFrame) -> pd.DataFrame:
    """Collapse London-dated daily bars into ISO weeks (spec Sec.4).

    Weekly bars are derived here rather than taken from Yahoo's ``1wk`` feed
    because that feed inherits the London-midnight anchoring — the two
    anchorings disagree by a whole run step on the thesis's headline event.

    ``known_at_ms`` is one full day after the week's last bar OPEN, i.e. the
    moment that bar has closed. This is the timestamp ``expand_to_days`` gates
    on; using the bar's open would leak up to 24h of look-ahead.
    """
    cols = ["week_key", "close", "known_at_ms", "end_date"]
    if daily.empty:
        return pd.DataFrame({c: pd.Series(dtype="object") for c in cols})
    df = daily.sort_values("open_time").reset_index(drop=True).copy()
    df["end_date"] = [london_trading_date(int(t)) for t in df["open_time"]]
    df["week_key"] = [iso_week_key(d) for d in df["end_date"]]
    last = df.groupby("week_key", as_index=False).last()
    last["known_at_ms"] = last["open_time"].astype("int64") + DAY_MS
    return last.sort_values("week_key").reset_index(drop=True)[cols]


def yen_strength_runs(close: pd.Series) -> pd.Series:
    """Consecutive weeks in which USD/JPY closed BELOW the prior week.

    The first element is NaN — it has no predecessor, so its run is undefined
    rather than 0. Warm-up NaN is preserved throughout this module so a
    warm-up row can never masquerade as a real ``run_le1`` observation.
    """
    prior = close.shift(1)
    down = close < prior
    runs: list[float] = []
    current = 0
    for i in range(len(close)):
        if bool(prior.isna().iloc[i]):
            runs.append(float("nan"))
            continue
        current = current + 1 if bool(down.iloc[i]) else 0
        runs.append(float(current))
    return pd.Series(runs, index=close.index, dtype="float64")


def label_run_states(runs: pd.Series) -> pd.Series:
    """Pre-registered buckets: <=1, exactly 2, >=3 (spec Sec.5)."""
    out: pd.Series = pd.Series(
        np.where(runs >= 3, RUN_GE3, np.where(runs == 2, RUN_EQ2, RUN_LE1)),
        index=runs.index,
    )
    return out.where(runs.notna(), other=np.nan)


def label_magnitude_states(close: pd.Series) -> pd.Series:
    """Causal z of the trailing ``MAG_SPAN``-week USD/JPY log return (spec Sec.5).

    SIGN: yen strength is USD/JPY FALLING, so a NEGATIVE z is ``YEN_STRONG``.
    This is the single easiest thing in H15 to invert, and an inversion would
    flip the verdict silently — ``test_magnitude_labels_falling_usdjpy_as_yen_
    strong`` is the guard.
    """
    ret = close.astype("float64").apply(np.log).diff(MAG_SPAN)
    z = causal_zscore(ret, window=MAG_WINDOW)
    out: pd.Series = pd.Series(
        np.where(
            z <= -MAG_THRESHOLD,
            YEN_STRONG,
            np.where(z >= MAG_THRESHOLD, YEN_WEAK, MAG_NEUTRAL),
        ),
        index=z.index,
    )
    return out.where(z.notna(), other=np.nan)


def expand_to_days(weekly: pd.DataFrame, state_col: str, days: pd.Index) -> pd.Series:
    """Map each UTC day number to the last week KNOWN before that day started.

    ``days`` holds day numbers (``ms // DAY_MS``). A day is tagged with the most
    recent week whose ``known_at_ms <= day * DAY_MS``; days preceding any known
    week are NaN and get dropped by the caller.

    ``searchsorted(..., side="right") - 1`` is the causality guarantee. Do not
    replace it with a positional ``shift`` — the day index has gaps, and a
    positional shift slides VALUES across a gap, silently borrowing a
    neighbouring week's state.
    """
    usable = weekly.dropna(subset=[state_col]).sort_values("known_at_ms")
    if usable.empty or len(days) == 0:
        return pd.Series([np.nan] * len(days), index=days, dtype="object")
    known = usable["known_at_ms"].to_numpy(dtype="int64")
    states = usable[state_col].to_numpy(dtype=object)
    day_start = days.to_numpy(dtype="int64") * DAY_MS
    pos = np.searchsorted(known, day_start, side="right") - 1
    clipped = np.clip(pos, 0, None)
    tagged = [
        states[i] if p >= 0 else None
        for p, i in zip(pos.tolist(), clipped.tolist(), strict=True)
    ]
    out: pd.Series = pd.Series(tagged, index=days, dtype="object")
    return out.where(out.notna(), other=np.nan)


def carry_family_key(label: str) -> tuple[str, str]:
    """``"state|direction"`` -> ``(axis, direction)`` DSR/PBO sub-family.

    The two axes are NEVER crossed (spec Sec.5) — crossing takes the family
    from 6 cells to 9 plus complements, which this repo's own H14 spec names a
    multiple-testing machine.
    """
    state, direction = label.rsplit("|", 1)
    if state in _RUN_STATES:
        return "run", direction
    if state in _MAG_STATES:
        return "magnitude", direction
    raise ValueError(f"unrecognized state token {state!r} in label {label!r}")
