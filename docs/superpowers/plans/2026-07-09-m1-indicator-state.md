# M1 Indicator-State Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add seven pure indicator-state components (EMA state, range state,
Monday-range state, yesterday candle pattern, PA character, BB/AVWAP,
volume-profile POC/VAH/VAL) to the daily brief's per-symbol panel — additive
everywhere, spec `docs/superpowers/specs/2026-07-09-m1-indicator-state-design.md`.

**Architecture:** Pure primitives at `analytics/` top level
(`volume_profile.py`, `indicators.py` — the `reference_levels.py` precedent),
one thin adapter `analytics/brief/indicators.py` building an `IndicatorState`
block consumed by `bundle.py`, rendered by `render.py`, and surfaced through
the API models and `Brief.svelte`.

**Tech Stack:** Python 3.11 / pandas / numpy (already deps), pytest,
Pydantic (web models), Svelte 5 (UI). No new dependencies.

## Global Constraints

- mypy strict: every function fully annotated (`-> None` for test methods).
- Formatting/lint: ruff (`make lint-py` must pass after every task).
- Read-only: no DB writes, no schema changes, no new tables.
- Determinism: every compute consumes completed bars only; anchors derive
  from `as_of_ms` (UTC). Same `as_of` ⇒ byte-identical markdown.
- Additive only: no existing field renamed/removed; `make test-regression`
  goldens must stay UNMOVED (nothing on the backtest path changes).
- A-priori display constants (never tune them): EMA 20/50/200, slope
  lookback 5, PA n=10 / ER≥0.40 / speed≥0.8, BB 20/2.0 / pctile window 180
  (min 60) / squeeze ≤0.10, profile 60d / 100 bins / VA 70%.
- Per-task gate: `make lint-py && make typecheck` plus the task's pytest
  command, all green before commit.
- Conventional commits (`feat:`/`test:`/`docs:`); never `--no-verify`.
- Task 8 (Svelte) must load `/frontend-design` then `/frontend-svelte`
  before editing UI files.

---

### Task 1: `analytics/volume_profile.py` primitive

**Files:**

- Create: `analytics/volume_profile.py`
- Test: `tests/test_volume_profile.py`

**Interfaces:**

- Consumes: nothing (pure; pandas + stdlib only).
- Produces (Task 5 depends on these exact names):
  - `VolumeProfile` frozen dataclass: `bin_edges: tuple[float, ...]`
    (len `n_bins+1`, ascending), `volumes: tuple[float, ...]` (len `n_bins`).
  - `build_profile(hourly_df: pd.DataFrame, n_bins: int = 100) ->
    VolumeProfile | None` — None on empty frame, zero price span, or zero
    total volume.
  - `value_area(profile: VolumeProfile, pct: float = 0.70) ->
    tuple[float, float, float]` — `(poc, vah, val)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_volume_profile.py`:

```python
"""Tests for analytics/volume_profile.py (pure volume-at-price math)."""

from __future__ import annotations

import pandas as pd

from analytics.volume_profile import VolumeProfile, build_profile, value_area


def _bar(open_time: int, low: float, high: float, volume: float) -> dict[str, object]:
    return {
        "open_time": open_time,
        "open": low,
        "high": high,
        "low": low,
        "close": high,
        "volume": volume,
    }


class TestBuildProfile:
    def test_single_bar_spreads_volume_uniformly(self) -> None:
        # One bar spanning [100, 110] with volume 100 into 10 bins of width 1
        # -> every bin gets 10.
        df = pd.DataFrame([_bar(0, 100.0, 110.0, 100.0)])
        prof = build_profile(df, n_bins=10)
        assert prof is not None
        assert len(prof.bin_edges) == 11
        assert prof.bin_edges[0] == 100.0
        assert prof.bin_edges[-1] == 110.0
        for v in prof.volumes:
            assert abs(v - 10.0) < 1e-9

    def test_partial_overlap_is_proportional(self) -> None:
        # Bins over [100, 110] (10 bins). Second bar spans [100, 102] with
        # volume 50 -> 25 into bin 0 and 25 into bin 1.
        df = pd.DataFrame(
            [_bar(0, 100.0, 110.0, 0.0), _bar(1, 100.0, 102.0, 50.0)]
        )
        prof = build_profile(df, n_bins=10)
        assert prof is not None
        assert abs(prof.volumes[0] - 25.0) < 1e-9
        assert abs(prof.volumes[1] - 25.0) < 1e-9
        assert abs(sum(prof.volumes) - 50.0) < 1e-9

    def test_zero_width_bar_lands_in_containing_bin(self) -> None:
        # high == low: all volume into the single bin containing that price.
        df = pd.DataFrame(
            [_bar(0, 100.0, 110.0, 0.0), _bar(1, 104.5, 104.5, 30.0)]
        )
        prof = build_profile(df, n_bins=10)
        assert prof is not None
        assert abs(prof.volumes[4] - 30.0) < 1e-9

    def test_zero_width_bar_at_top_edge_lands_in_last_bin(self) -> None:
        df = pd.DataFrame(
            [_bar(0, 100.0, 110.0, 0.0), _bar(1, 110.0, 110.0, 5.0)]
        )
        prof = build_profile(df, n_bins=10)
        assert prof is not None
        assert abs(prof.volumes[9] - 5.0) < 1e-9

    def test_empty_frame_returns_none(self) -> None:
        df = pd.DataFrame(columns=["open_time", "open", "high", "low", "close", "volume"])
        assert build_profile(df) is None

    def test_zero_span_returns_none(self) -> None:
        df = pd.DataFrame([_bar(0, 100.0, 100.0, 10.0)])
        assert build_profile(df) is None

    def test_zero_total_volume_returns_none(self) -> None:
        df = pd.DataFrame([_bar(0, 100.0, 110.0, 0.0)])
        assert build_profile(df) is None


class TestValueArea:
    def test_known_poc_and_va(self) -> None:
        # 5 bins [0,5), volumes [10, 20, 40, 20, 10] (total 100).
        # POC = bin 2 (center 2.5). VA at 70%: start 40, add larger
        # neighbor (tie 20/20 -> prefer UPPER bin 3) -> 60, then bin 1 -> 80
        # >= 70 -> VA bins {1,2,3}: val = 1.0, vah = 4.0.
        prof = VolumeProfile(
            bin_edges=(0.0, 1.0, 2.0, 3.0, 4.0, 5.0),
            volumes=(10.0, 20.0, 40.0, 20.0, 10.0),
        )
        poc, vah, val = value_area(prof, pct=0.70)
        assert poc == 2.5
        assert vah == 4.0
        assert val == 1.0

    def test_poc_tie_prefers_lowest_price_bin(self) -> None:
        prof = VolumeProfile(
            bin_edges=(0.0, 1.0, 2.0, 3.0),
            volumes=(30.0, 10.0, 30.0),
        )
        poc, _, _ = value_area(prof, pct=0.5)
        assert poc == 0.5  # lowest-price max bin wins

    def test_full_pct_covers_all_bins(self) -> None:
        prof = VolumeProfile(
            bin_edges=(0.0, 1.0, 2.0, 3.0),
            volumes=(10.0, 10.0, 10.0),
        )
        _, vah, val = value_area(prof, pct=1.0)
        assert vah == 3.0
        assert val == 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_volume_profile.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'analytics.volume_profile'`

- [ ] **Step 3: Write the implementation**

Create `analytics/volume_profile.py`:

```python
"""Volume-at-price profile from OHLCV — pure math, no DB/brief imports.

The M1 volume-profile primitive (spec
docs/superpowers/specs/2026-07-09-m1-indicator-state-design.md): each bar's
volume is spread over the price bins its [low, high] range overlaps,
proportional to overlap. Reusable outside the brief (F2, state-tag research).
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class VolumeProfile:
    """Uniform price bins (``bin_edges`` ascending, len = len(volumes)+1)."""

    bin_edges: tuple[float, ...]
    volumes: tuple[float, ...]


def build_profile(hourly_df: pd.DataFrame, n_bins: int = 100) -> VolumeProfile | None:
    """Composite profile over the frame; None when it cannot be built.

    None cases: empty frame, zero price span (all bars at one price), or
    zero total volume. A zero-width bar (high == low) drops all its volume
    into the single bin containing that price (top edge -> last bin).
    """
    if hourly_df.empty:
        return None
    lows = hourly_df["low"].astype(float)
    highs = hourly_df["high"].astype(float)
    vols = hourly_df["volume"].astype(float)
    span_lo = float(lows.min())
    span_hi = float(highs.max())
    if span_hi <= span_lo or float(vols.sum()) <= 0.0:
        return None
    width = (span_hi - span_lo) / n_bins
    edges = [span_lo + i * width for i in range(n_bins + 1)]
    volumes = [0.0] * n_bins
    for low, high, vol in zip(lows, highs, vols, strict=True):
        if vol <= 0.0:
            continue
        if high <= low:
            idx = min(int((low - span_lo) / width), n_bins - 1)
            volumes[idx] += float(vol)
            continue
        bar_span = high - low
        first = max(0, min(int((low - span_lo) / width), n_bins - 1))
        last = max(0, min(int((high - span_lo) / width), n_bins - 1))
        for i in range(first, last + 1):
            overlap = min(high, edges[i + 1]) - max(low, edges[i])
            if overlap > 0:
                volumes[i] += float(vol) * (overlap / bar_span)
    return VolumeProfile(bin_edges=tuple(edges), volumes=tuple(volumes))


def value_area(
    profile: VolumeProfile, pct: float = 0.70
) -> tuple[float, float, float]:
    """(poc, vah, val) — greedy expansion around the POC bin to ``pct``.

    Deterministic tie-breaks: equal-volume POC candidates -> lowest-price
    bin (first max); equal-volume neighbors during expansion -> upper bin.
    """
    vols = profile.volumes
    edges = profile.bin_edges
    total = sum(vols)
    poc_idx = max(range(len(vols)), key=lambda i: (vols[i], -i))
    lo = hi = poc_idx
    covered = vols[poc_idx]
    while covered < pct * total and (lo > 0 or hi < len(vols) - 1):
        up = vols[hi + 1] if hi < len(vols) - 1 else float("-inf")
        down = vols[lo - 1] if lo > 0 else float("-inf")
        if up >= down:  # tie prefers the upper bin
            hi += 1
            covered += vols[hi]
        else:
            lo -= 1
            covered += vols[lo]
    poc = (edges[poc_idx] + edges[poc_idx + 1]) / 2.0
    return poc, edges[hi + 1], edges[lo]
```

Note the POC tie-break: `max(..., key=lambda i: (vols[i], -i))` picks the
**lowest** index among equal maxima (first max = lowest-price bin), per spec.

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_volume_profile.py -v`
Expected: all PASS

- [ ] **Step 5: Gate + commit**

```bash
make lint-py && make typecheck
git add analytics/volume_profile.py tests/test_volume_profile.py
git commit -m "feat(analytics): volume-at-price profile primitive (POC/VAH/VAL)"
```

---

### Task 2: `analytics/indicators.py` primitives (AVWAP / BB / ER / PA)

**Files:**

- Create: `analytics/indicators.py`
- Test: `tests/test_indicators.py`

**Interfaces:**

- Consumes: nothing (pure; pandas + stdlib only).
- Produces (Task 5 depends on these exact names):
  - `anchored_vwap(hourly_df: pd.DataFrame, anchor_ms: int) -> float | None`
    — Σ(typical×vol)/Σ(vol) over rows with `open_time >= anchor_ms`;
    typical = (high+low+close)/3; None on no rows or zero total volume.
  - `BollingerRead` frozen dataclass: `pct_b: float`, `bandwidth: float`,
    `bw_pctile: float | None`, `squeeze: bool | None`.
  - `bollinger_state(close: pd.Series, ref_price: float, period: int = 20,
    k: float = 2.0, pctile_window: int = 180, pctile_min: int = 60) ->
    BollingerRead | None` — None when len(close) < period, sd == 0, or
    middle == 0.
  - `efficiency_ratio(close: pd.Series, n: int) -> float` — 0.0 when the
    denominator is 0 or len < n+1.
  - `PaRead` frozen dataclass: `label: str`, `er: float`, `speed_atr: float`.
  - `pa_character(close: pd.Series, atr14: float, n: int = 10,
    er_threshold: float = 0.40, speed_threshold: float = 0.8) ->
    PaRead | None` — None when len(close) < n+1 or atr14 <= 0.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_indicators.py`:

```python
"""Tests for analytics/indicators.py (AVWAP / Bollinger / ER / PA character)."""

from __future__ import annotations

import pandas as pd

from analytics.indicators import (
    anchored_vwap,
    bollinger_state,
    efficiency_ratio,
    pa_character,
)


def _hbar(open_time: int, price: float, volume: float) -> dict[str, object]:
    return {
        "open_time": open_time,
        "open": price,
        "high": price,
        "low": price,
        "close": price,
        "volume": volume,
    }


class TestAnchoredVwap:
    def test_hand_math(self) -> None:
        # typical == price here (flat bars). VWAP = (100*10 + 110*30)/40 = 107.5
        df = pd.DataFrame([_hbar(0, 90.0, 99.0), _hbar(10, 100.0, 10.0), _hbar(20, 110.0, 30.0)])
        assert anchored_vwap(df, anchor_ms=10) == 107.5

    def test_anchor_boundary_is_inclusive(self) -> None:
        df = pd.DataFrame([_hbar(10, 100.0, 10.0)])
        assert anchored_vwap(df, anchor_ms=10) == 100.0

    def test_no_rows_past_anchor_returns_none(self) -> None:
        df = pd.DataFrame([_hbar(0, 100.0, 10.0)])
        assert anchored_vwap(df, anchor_ms=999) is None

    def test_zero_volume_returns_none(self) -> None:
        df = pd.DataFrame([_hbar(10, 100.0, 0.0)])
        assert anchored_vwap(df, anchor_ms=0) is None


class TestEfficiencyRatio:
    def test_straight_line_is_one(self) -> None:
        close = pd.Series([float(i) for i in range(11)])
        assert abs(efficiency_ratio(close, 10) - 1.0) < 1e-9

    def test_round_trip_is_zero(self) -> None:
        # up 5 then back down 5: net 0, gross 10 -> ER 0.
        close = pd.Series([0.0, 1, 2, 3, 4, 5, 4, 3, 2, 1, 0])
        assert efficiency_ratio(close, 10) == 0.0

    def test_flat_series_denominator_zero(self) -> None:
        close = pd.Series([5.0] * 11)
        assert efficiency_ratio(close, 10) == 0.0

    def test_too_short_returns_zero(self) -> None:
        assert efficiency_ratio(pd.Series([1.0, 2.0]), 10) == 0.0


class TestPaCharacter:
    def test_impulse_up(self) -> None:
        # +1.0/bar over 10 bars, ATR 1.0 -> ER 1.0, speed 1.0 >= 0.8.
        close = pd.Series([float(i) for i in range(11)])
        read = pa_character(close, atr14=1.0)
        assert read is not None
        assert read.label == "impulse_up"
        assert abs(read.er - 1.0) < 1e-9
        assert abs(read.speed_atr - 1.0) < 1e-9

    def test_grind_down(self) -> None:
        # -0.5/bar, ATR 1.0 -> ER 1.0 directional, speed 0.5 < 0.8.
        close = pd.Series([10.0 - 0.5 * i for i in range(11)])
        read = pa_character(close, atr14=1.0)
        assert read is not None
        assert read.label == "grind_down"

    def test_chop(self) -> None:
        close = pd.Series([0.0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0])
        read = pa_character(close, atr14=1.0)
        assert read is not None
        assert read.label == "chop"

    def test_boundary_thresholds_inclusive(self) -> None:
        # ER exactly 0.40 counts as directional; speed exactly 0.8 as impulse.
        # net +4 over gross 10 -> ER 0.4; mean |d| = 1.0; ATR 1.25 -> speed 0.8.
        close = pd.Series([0.0, 1, 2, 3, 4, 5, 6, 7, 6, 5, 4])
        read = pa_character(close, atr14=1.25)
        assert read is not None
        assert abs(read.er - 0.40) < 1e-9
        assert abs(read.speed_atr - 0.8) < 1e-9
        assert read.label == "impulse_up"

    def test_short_series_returns_none(self) -> None:
        assert pa_character(pd.Series([1.0, 2.0]), atr14=1.0) is None

    def test_zero_atr_returns_none(self) -> None:
        close = pd.Series([float(i) for i in range(11)])
        assert pa_character(close, atr14=0.0) is None


class TestBollingerState:
    def test_constant_price_returns_none(self) -> None:
        # sd == 0 -> bands collapse -> None.
        assert bollinger_state(pd.Series([100.0] * 30), ref_price=100.0) is None

    def test_short_series_returns_none(self) -> None:
        assert bollinger_state(pd.Series([100.0, 101.0]), ref_price=100.0) is None

    def test_pct_b_and_bandwidth_hand_math(self) -> None:
        # Alternating 99/101 over 20 bars: mean 100, population sd 1.
        # upper = 102, lower = 98, bandwidth = 4/100 = 0.04.
        # ref 101 -> %B = (101-98)/4 = 0.75. Short history -> pctile None.
        close = pd.Series([99.0, 101.0] * 10)
        read = bollinger_state(close, ref_price=101.0)
        assert read is not None
        assert abs(read.pct_b - 0.75) < 1e-9
        assert abs(read.bandwidth - 0.04) < 1e-9
        assert read.bw_pctile is None
        assert read.squeeze is None

    def test_pctile_with_enough_history(self) -> None:
        # 100 flat-ish bars then 60 alternating: enough bandwidth history
        # (>= 60 valid bandwidth values) -> pctile is not None and in [0, 1].
        close = pd.Series(
            [100.0 + (0.1 if i % 2 else -0.1) for i in range(100)]
            + [99.0, 101.0] * 30
        )
        read = bollinger_state(close, ref_price=100.0)
        assert read is not None
        assert read.bw_pctile is not None
        assert 0.0 <= read.bw_pctile <= 1.0
        assert read.squeeze is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_indicators.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'analytics.indicators'`

- [ ] **Step 3: Write the implementation**

Create `analytics/indicators.py`:

```python
"""Pure indicator math: anchored VWAP, Bollinger read, ER / PA character.

M1 primitives (spec docs/superpowers/specs/2026-07-09-m1-indicator-state-design.md).
No DB, no brief imports — reusable outside the brief (F2, research). All
thresholds are a-priori display constants, never fitted.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class BollingerRead:
    pct_b: float
    bandwidth: float
    bw_pctile: float | None
    squeeze: bool | None


@dataclass(frozen=True)
class PaRead:
    label: str  # impulse_up | impulse_down | grind_up | grind_down | chop
    er: float
    speed_atr: float


def anchored_vwap(hourly_df: pd.DataFrame, anchor_ms: int) -> float | None:
    """Volume-weighted typical price over bars with open_time >= anchor_ms."""
    if hourly_df.empty:
        return None
    window = hourly_df[hourly_df["open_time"] >= anchor_ms]
    if window.empty:
        return None
    vol = window["volume"].astype(float)
    total = float(vol.sum())
    if total <= 0.0:
        return None
    typical = (
        window["high"].astype(float)
        + window["low"].astype(float)
        + window["close"].astype(float)
    ) / 3.0
    return float((typical * vol).sum() / total)


def bollinger_state(
    close: pd.Series,
    ref_price: float,
    period: int = 20,
    k: float = 2.0,
    pctile_window: int = 180,
    pctile_min: int = 60,
) -> BollingerRead | None:
    """%B of ref_price + bandwidth + bandwidth percentile vs trailing window.

    Population std (ddof=0), the trading-platform BB convention. Percentile =
    fraction of the trailing ``pctile_window`` bandwidth values (including the
    current one) that are <= current; None under ``pctile_min`` valid values.
    """
    series = close.astype(float)
    if len(series) < period:
        return None
    mid = series.rolling(period).mean()
    sd = series.rolling(period).std(ddof=0)
    middle = float(mid.iloc[-1])
    dev = float(sd.iloc[-1])
    if dev <= 0.0 or middle == 0.0:
        return None
    upper = middle + k * dev
    lower = middle - k * dev
    pct_b = (ref_price - lower) / (upper - lower)
    bw_series = (2.0 * k * sd / mid).dropna()
    bandwidth = float(bw_series.iloc[-1])
    tail = bw_series.tail(pctile_window)
    if len(tail) < pctile_min:
        return BollingerRead(
            pct_b=float(pct_b), bandwidth=bandwidth, bw_pctile=None, squeeze=None
        )
    pctile = float((tail <= bandwidth).mean())
    return BollingerRead(
        pct_b=float(pct_b),
        bandwidth=bandwidth,
        bw_pctile=pctile,
        squeeze=pctile <= 0.10,
    )


def efficiency_ratio(close: pd.Series, n: int) -> float:
    """Kaufman ER over the last ``n`` steps; 0.0 when undefined."""
    series = close.astype(float)
    if len(series) < n + 1:
        return 0.0
    window = series.iloc[-(n + 1) :]
    gross = float(window.diff().abs().sum())
    if gross <= 0.0:
        return 0.0
    net = abs(float(window.iloc[-1]) - float(window.iloc[0]))
    return net / gross


def pa_character(
    close: pd.Series,
    atr14: float,
    n: int = 10,
    er_threshold: float = 0.40,
    speed_threshold: float = 0.8,
) -> PaRead | None:
    """Impulse/grind/chop label from ER x ATR-normalised speed (spec §5)."""
    series = close.astype(float)
    if len(series) < n + 1 or atr14 <= 0.0:
        return None
    window = series.iloc[-(n + 1) :]
    er = efficiency_ratio(series, n)
    speed = float(window.diff().abs().mean()) / atr14
    if er < er_threshold:
        label = "chop"
    else:
        direction = "up" if float(window.iloc[-1]) >= float(window.iloc[0]) else "down"
        kind = "impulse" if speed >= speed_threshold else "grind"
        label = f"{kind}_{direction}"
    return PaRead(label=label, er=er, speed_atr=speed)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_indicators.py -v`
Expected: all PASS

- [ ] **Step 5: Gate + commit**

```bash
make lint-py && make typecheck
git add analytics/indicators.py tests/test_indicators.py
git commit -m "feat(analytics): indicator primitives — anchored VWAP, Bollinger read, ER, PA character"
```

---

### Task 3: Brief types + adapter scaffold (EMA / range / Monday states)

**Files:**

- Modify: `analytics/brief/types.py` (add 8 sub-dataclasses + `IndicatorState`;
  add `indicators` field to `SymbolPanel` and `error_panel`)
- Create: `analytics/brief/indicators.py`
- Test: `tests/test_brief_indicators.py`

**Interfaces:**

- Consumes: `analytics.strategies._shared.compute_ema(series, span)`,
  `analytics.reference_levels.compute_levels(daily_ohlcv, entry_ts_ms)`
  (returns `dict[str, float | None]` with keys incl. `"MonH"`/`"MonL"`,
  both None on a Monday entry), `analytics.brief._common.day_ahead_dow`.
- Produces (Tasks 4–8 depend on these exact names):
  - In `types.py`: `EmaState`, `RangeState`, `MondayState`, `CandleHit`,
    `PaState`, `BbState`, `VwapState`, `ProfileState`, `IndicatorState`
    (fields below), `SymbolPanel.indicators: IndicatorState | None`.
  - In `brief/indicators.py`:
    `build_indicator_state(completed_1d: pd.DataFrame, completed_1h:
    pd.DataFrame, regime_series_1d: pd.Series, ref_close: float,
    atr14: float, as_of_ms: int) -> tuple[IndicatorState | None, list[str]]`
    — notes are UNPREFIXED (`"indicator ema failed (...)"`); the bundle
    prefixes the symbol. Also internal builders `_ema_state`,
    `_range_state`, `_monday_state` (this task) and stubs for Tasks 4–5.

- [ ] **Step 1: Add the dataclasses to `analytics/brief/types.py`**

Insert directly after the `SeasonalityStrip` dataclass (before `SymbolPanel`):

```python
@dataclass(frozen=True)
class EmaState:
    above_20: bool | None
    above_50: bool | None
    above_200: bool | None
    stack: str | None  # "bullish" | "bearish" | "mixed"
    slope_200: str | None  # "rising" | "falling"


@dataclass(frozen=True)
class RangeState:
    label: str  # regime label of the current run
    since_ms: int  # open_time of the run's first bar
    bars: int
    range_low: float | None  # only when label == "range"
    range_high: float | None
    pos: float | None  # ref position in the range, clipped [0, 1]


@dataclass(frozen=True)
class MondayState:
    state: str  # "above" | "inside" | "below" | "forming"
    pos: float | None  # fraction inside MonL..MonH, only for "inside"


@dataclass(frozen=True)
class CandleHit:
    pattern: str  # detector name, e.g. "engulfing"
    direction: str  # "long" | "short"


@dataclass(frozen=True)
class PaState:
    label: str  # impulse_up | impulse_down | grind_up | grind_down | chop
    er: float
    speed_atr: float


@dataclass(frozen=True)
class BbState:
    pct_b: float
    bandwidth: float
    bw_pctile: float | None
    squeeze: bool | None


@dataclass(frozen=True)
class VwapState:
    weekly_price: float | None
    weekly_dist_atr: float | None  # (ref - vwap) / atr: + = price above
    monthly_price: float | None
    monthly_dist_atr: float | None


@dataclass(frozen=True)
class ProfileState:
    poc: float
    vah: float
    val: float
    vs_value: str  # "above" | "inside" | "below"
    poc_dist_atr: float  # (poc - ref) / atr: + = POC above price


@dataclass(frozen=True)
class IndicatorState:
    ema: EmaState | None
    range_state: RangeState | None
    monday: MondayState | None
    candles: list[CandleHit] | None  # [] = no patterns (valid); None = failed
    pa: PaState | None
    bb: BbState | None
    vwap: VwapState | None
    profile: ProfileState | None
```

Then add the field to `SymbolPanel` between `seasonality` and `error`:

```python
    seasonality: SeasonalityStrip | None
    indicators: IndicatorState | None
    error: str | None
```

And in `error_panel(...)` add `indicators=None,` on the line before
`error=message,`.

- [ ] **Step 2: Write the failing adapter tests**

Create `tests/test_brief_indicators.py`:

```python
"""Tests for analytics/brief/indicators.py (per-symbol indicator states)."""

from __future__ import annotations

import math

import pandas as pd

from analytics.brief.indicators import (
    _ema_state,
    _monday_state,
    _range_state,
    build_indicator_state,
)

DAY_MS = 86_400_000
# 2024-01-01 00:00 UTC — a Monday (matches tests/_brief_fixtures.py).
START_MS = 1_704_067_200_000


def _daily_frame(n_days: int, base: float = 100.0) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for i in range(n_days):
        o = base + 10.0 * math.sin(i / 7.0)
        c = o * (1.0 + 0.01 * math.sin(i / 3.0))
        rows.append(
            {
                "open_time": START_MS + i * DAY_MS,
                "open": o,
                "high": max(o, c) * 1.01,
                "low": min(o, c) * 0.99,
                "close": c,
                "volume": 1000.0,
            }
        )
    return pd.DataFrame(rows)


class TestEmaState:
    def test_full_history_has_all_spans(self) -> None:
        df = _daily_frame(250)
        state = _ema_state(df, ref_close=200.0)
        assert state is not None
        assert state.above_20 is True
        assert state.above_50 is True
        assert state.above_200 is True
        assert state.stack in ("bullish", "bearish", "mixed")
        assert state.slope_200 in ("rising", "falling")

    def test_warmup_shorter_than_span_is_none(self) -> None:
        df = _daily_frame(30)  # >= 20, < 50, < 200
        state = _ema_state(df, ref_close=1.0)
        assert state is not None
        assert state.above_20 is False
        assert state.above_50 is None
        assert state.above_200 is None
        assert state.stack is None
        assert state.slope_200 is None

    def test_empty_frame_is_none(self) -> None:
        assert _ema_state(_daily_frame(0), ref_close=1.0) is None


class TestRangeState:
    def test_run_length_and_bounds(self) -> None:
        df = _daily_frame(10)
        regime = pd.Series(["trend"] * 6 + ["range"] * 4)
        state = _range_state(df, regime, ref_close=float(df["close"].iloc[-1]))
        assert state is not None
        assert state.label == "range"
        assert state.bars == 4
        assert state.since_ms == START_MS + 6 * DAY_MS
        assert state.range_low is not None and state.range_high is not None
        assert state.range_low == float(df["low"].astype(float).tail(4).min())
        assert state.range_high == float(df["high"].astype(float).tail(4).max())
        assert state.pos is not None and 0.0 <= state.pos <= 1.0

    def test_trend_run_has_no_bounds(self) -> None:
        df = _daily_frame(10)
        regime = pd.Series(["range"] * 5 + ["trend"] * 5)
        state = _range_state(df, regime, ref_close=100.0)
        assert state is not None
        assert state.label == "trend"
        assert state.bars == 5
        assert state.range_low is None and state.pos is None

    def test_empty_regime_is_none(self) -> None:
        assert _range_state(_daily_frame(0), pd.Series(dtype=object), 1.0) is None


class TestMondayState:
    def test_forming_on_monday(self) -> None:
        df = _daily_frame(15)
        monday_as_of = START_MS + 14 * DAY_MS + 3_600_000  # Mon 01:00 UTC
        state = _monday_state(df, ref_close=100.0, as_of_ms=monday_as_of)
        assert state is not None
        assert state.state == "forming"
        assert state.pos is None

    def test_inside_has_position(self) -> None:
        df = _daily_frame(16)
        tuesday_as_of = START_MS + 15 * DAY_MS + 3_600_000  # Tue 01:00 UTC
        mon_high = float(df["high"].iloc[14])
        mon_low = float(df["low"].iloc[14])
        mid = (mon_high + mon_low) / 2.0
        state = _monday_state(df, ref_close=mid, as_of_ms=tuesday_as_of)
        assert state is not None
        assert state.state == "inside"
        assert state.pos is not None and abs(state.pos - 0.5) < 1e-9

    def test_above_and_below(self) -> None:
        df = _daily_frame(16)
        tuesday_as_of = START_MS + 15 * DAY_MS + 3_600_000
        assert _monday_state(df, 10_000.0, tuesday_as_of).state == "above"  # type: ignore[union-attr]
        assert _monday_state(df, 1.0, tuesday_as_of).state == "below"  # type: ignore[union-attr]


class TestBuildIndicatorState:
    def test_scaffold_builds_first_three_states(self) -> None:
        df = _daily_frame(250)
        regime = pd.Series(["trend"] * 250)
        tuesday_as_of = START_MS + 250 * DAY_MS  # frame is fully completed
        state, notes = build_indicator_state(
            completed_1d=df,
            completed_1h=pd.DataFrame(
                columns=["open_time", "open", "high", "low", "close", "volume"]
            ),
            regime_series_1d=regime,
            ref_close=100.0,
            atr14=2.0,
            as_of_ms=tuesday_as_of,
        )
        assert state is not None
        assert state.ema is not None
        assert state.range_state is not None
        assert notes == []
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `poetry run pytest tests/test_brief_indicators.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'analytics.brief.indicators'`

- [ ] **Step 4: Write the adapter**

Create `analytics/brief/indicators.py`:

```python
"""Per-symbol indicator states for the brief panel (M1 adapter).

Thin adapter over the pure primitives (analytics/indicators.py,
analytics/volume_profile.py) + existing helpers (compute_ema,
reference_levels, regime series). Every sub-block computes independently:
one failure -> that sub-block None + an UNPREFIXED note (the bundle adds
the symbol); only all sub-blocks None collapses the whole state to None.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import pandas as pd

from analytics.brief._common import day_ahead_dow
from analytics.brief.types import (
    BbState,
    CandleHit,
    EmaState,
    IndicatorState,
    MondayState,
    PaState,
    ProfileState,
    RangeState,
    VwapState,
)
from analytics.reference_levels import compute_levels
from analytics.strategies._shared import compute_ema

logger = logging.getLogger(__name__)

_EMA_SPANS = (20, 50, 200)
_SLOPE_LOOKBACK = 5


def _ema_value(close: pd.Series, span: int) -> pd.Series | None:
    """EMA series when history covers the span, else None (spec §1)."""
    if len(close) < span:
        return None
    return compute_ema(close, span)


def _ema_state(completed_1d: pd.DataFrame, ref_close: float) -> EmaState | None:
    if completed_1d.empty:
        return None
    close = completed_1d["close"].astype(float)
    series = {span: _ema_value(close, span) for span in _EMA_SPANS}
    values = {
        span: (float(s.iloc[-1]) if s is not None else None)
        for span, s in series.items()
    }
    above = {
        span: (ref_close > v if v is not None else None)
        for span, v in values.items()
    }
    e20, e50, e200 = values[20], values[50], values[200]
    stack: str | None = None
    if e20 is not None and e50 is not None and e200 is not None:
        if e20 > e50 > e200:
            stack = "bullish"
        elif e200 > e50 > e20:
            stack = "bearish"
        else:
            stack = "mixed"
    slope: str | None = None
    s200 = series[200]
    if s200 is not None and len(s200) > _SLOPE_LOOKBACK:
        slope = (
            "rising"
            if float(s200.iloc[-1]) > float(s200.iloc[-1 - _SLOPE_LOOKBACK])
            else "falling"
        )
    return EmaState(
        above_20=above[20],
        above_50=above[50],
        above_200=above[200],
        stack=stack,
        slope_200=slope,
    )


def _range_state(
    completed_1d: pd.DataFrame, regime_series_1d: pd.Series, ref_close: float
) -> RangeState | None:
    if regime_series_1d.empty or completed_1d.empty:
        return None
    labels = [str(v) for v in regime_series_1d.tolist()]
    label = labels[-1]
    bars = 1
    for prev in reversed(labels[:-1]):
        if prev != label:
            break
        bars += 1
    since_ms = int(completed_1d["open_time"].iloc[len(completed_1d) - bars])
    if label != "range":
        return RangeState(
            label=label,
            since_ms=since_ms,
            bars=bars,
            range_low=None,
            range_high=None,
            pos=None,
        )
    window = completed_1d.iloc[-bars:]
    low = float(window["low"].astype(float).min())
    high = float(window["high"].astype(float).max())
    pos: float | None = None
    if high > low:
        pos = min(max((ref_close - low) / (high - low), 0.0), 1.0)
    return RangeState(
        label=label,
        since_ms=since_ms,
        bars=bars,
        range_low=low,
        range_high=high,
        pos=pos,
    )


def _monday_state(
    completed_1d: pd.DataFrame, ref_close: float, as_of_ms: int
) -> MondayState | None:
    levels = compute_levels(completed_1d, as_of_ms)
    mon_high = levels.get("MonH")
    mon_low = levels.get("MonL")
    if mon_high is None or mon_low is None:
        if day_ahead_dow(as_of_ms) == "Mon":
            return MondayState(state="forming", pos=None)
        return None
    if ref_close > mon_high:
        return MondayState(state="above", pos=None)
    if ref_close < mon_low:
        return MondayState(state="below", pos=None)
    pos: float | None = None
    if mon_high > mon_low:
        pos = (ref_close - mon_low) / (mon_high - mon_low)
    return MondayState(state="inside", pos=pos)


def _candle_hits(completed_1d: pd.DataFrame) -> list[CandleHit]:
    raise NotImplementedError  # Task 4


def _pa_state(completed_1d: pd.DataFrame, atr14: float) -> PaState | None:
    raise NotImplementedError  # Task 4


def _bb_state(completed_1d: pd.DataFrame, ref_close: float) -> BbState | None:
    raise NotImplementedError  # Task 5


def _vwap_state(
    completed_1h: pd.DataFrame, ref_close: float, atr14: float, as_of_ms: int
) -> VwapState | None:
    raise NotImplementedError  # Task 5


def _profile_state(
    completed_1h: pd.DataFrame, ref_close: float, atr14: float, as_of_ms: int
) -> ProfileState | None:
    raise NotImplementedError  # Task 5


def build_indicator_state(
    completed_1d: pd.DataFrame,
    completed_1h: pd.DataFrame,
    regime_series_1d: pd.Series,
    ref_close: float,
    atr14: float,
    as_of_ms: int,
) -> tuple[IndicatorState | None, list[str]]:
    """(IndicatorState | None, notes) — independent sub-blocks (spec).

    Notes are unprefixed ("indicator ema failed (...)"); the bundle adds
    the symbol. NotImplementedError from a not-yet-built sub-block (staged
    Tasks 4-5) is treated as "absent", not "failed" — no note.
    """
    notes: list[str] = []

    def run(name: str, fn: Callable[[], object]) -> object:
        try:
            return fn()
        except NotImplementedError:
            return None
        except Exception as exc:  # independence contract
            logger.warning("brief indicators: %s failed: %s", name, exc)
            notes.append(f"indicator {name} failed ({exc})")
            return None

    ema = run("ema", lambda: _ema_state(completed_1d, ref_close))
    range_state = run(
        "range", lambda: _range_state(completed_1d, regime_series_1d, ref_close)
    )
    monday = run("monday", lambda: _monday_state(completed_1d, ref_close, as_of_ms))
    candles = run("candle", lambda: _candle_hits(completed_1d))
    pa = run("pa", lambda: _pa_state(completed_1d, atr14))
    bb = run("bb", lambda: _bb_state(completed_1d, ref_close))
    vwap = run(
        "vwap", lambda: _vwap_state(completed_1h, ref_close, atr14, as_of_ms)
    )
    profile = run(
        "profile", lambda: _profile_state(completed_1h, ref_close, atr14, as_of_ms)
    )
    state = IndicatorState(
        ema=ema if isinstance(ema, EmaState) else None,
        range_state=range_state if isinstance(range_state, RangeState) else None,
        monday=monday if isinstance(monday, MondayState) else None,
        candles=candles if isinstance(candles, list) else None,
        pa=pa if isinstance(pa, PaState) else None,
        bb=bb if isinstance(bb, BbState) else None,
        vwap=vwap if isinstance(vwap, VwapState) else None,
        profile=profile if isinstance(profile, ProfileState) else None,
    )
    if all(
        v is None
        for v in (
            state.ema,
            state.range_state,
            state.monday,
            state.candles,
            state.pa,
            state.bb,
            state.vwap,
            state.profile,
        )
    ):
        return None, notes
    return state, notes
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `poetry run pytest tests/test_brief_indicators.py tests/test_brief_types.py -v`
Expected: all PASS (existing types tests still green — the new field is
keyword-constructed everywhere).

Note: other brief tests that construct `SymbolPanel` directly (if any) will
fail with "missing keyword argument 'indicators'" — run
`poetry run pytest tests/ -k brief -v` and add `indicators=None,` to any
direct constructions that break.

- [ ] **Step 6: Gate + commit**

```bash
make lint-py && make typecheck && poetry run pytest tests/ -k brief -q
git add analytics/brief/types.py analytics/brief/indicators.py tests/test_brief_indicators.py
git commit -m "feat(brief): IndicatorState types + adapter scaffold (EMA/range/Monday states)"
```

---

### Task 4: Candle-pattern + PA character states

**Files:**

- Modify: `analytics/brief/indicators.py` (replace the two
  `NotImplementedError` stubs `_candle_hits`, `_pa_state`)
- Test: `tests/test_brief_indicators.py` (append)

**Interfaces:**

- Consumes: the six anatomy detectors (each `(df, ...defaults) ->
  DataFrame` with columns incl. `open_time` int-ms and `direction`):
  `analytics.strategies.engulfing.detect_engulfing`,
  `analytics.strategies.pin_bar.detect_pin_bar`,
  `analytics.strategies.doji.detect_doji`,
  `analytics.strategies.inside_bar.detect_inside_bar`,
  `analytics.strategies.hammer_hanging_man.detect_hammer_hanging_man`,
  `analytics.strategies.morning_evening_star.detect_morning_evening_star`;
  plus `analytics.indicators.pa_character` (Task 2).
- Produces: working `_candle_hits(completed_1d) -> list[CandleHit]`
  (sorted by (pattern, direction)) and
  `_pa_state(completed_1d, atr14) -> PaState | None` — consumed by the
  Task 3 orchestrator unchanged.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_brief_indicators.py`:

```python
class TestCandleHits:
    def test_bullish_engulfing_on_last_bar(self) -> None:
        from analytics.brief.indicators import _candle_hits

        # Bar 0 bearish (100 -> 98), bar 1 bullish engulfing (97 -> 101).
        df = pd.DataFrame(
            [
                {
                    "open_time": START_MS,
                    "open": 100.0,
                    "high": 100.5,
                    "low": 97.5,
                    "close": 98.0,
                    "volume": 1000.0,
                },
                {
                    "open_time": START_MS + DAY_MS,
                    "open": 97.0,
                    "high": 101.5,
                    "low": 96.5,
                    "close": 101.0,
                    "volume": 1000.0,
                },
            ]
        )
        hits = _candle_hits(df)
        assert ("engulfing", "long") in [(h.pattern, h.direction) for h in hits]

    def test_pattern_on_earlier_bar_is_ignored(self) -> None:
        from analytics.brief.indicators import _candle_hits

        # Same engulfing pair, then a plain drifting third bar: engulfing
        # fired on bar 1, which is no longer the last bar -> not reported.
        df = pd.DataFrame(
            [
                {
                    "open_time": START_MS,
                    "open": 100.0,
                    "high": 100.5,
                    "low": 97.5,
                    "close": 98.0,
                    "volume": 1000.0,
                },
                {
                    "open_time": START_MS + DAY_MS,
                    "open": 97.0,
                    "high": 101.5,
                    "low": 96.5,
                    "close": 101.0,
                    "volume": 1000.0,
                },
                {
                    "open_time": START_MS + 2 * DAY_MS,
                    "open": 101.0,
                    "high": 101.6,
                    "low": 100.8,
                    "close": 101.5,
                    "volume": 1000.0,
                },
            ]
        )
        hits = _candle_hits(df)
        assert ("engulfing", "long") not in [(h.pattern, h.direction) for h in hits]

    def test_no_patterns_is_empty_list_not_none(self) -> None:
        from analytics.brief.indicators import _candle_hits

        hits = _candle_hits(_daily_frame(30))
        assert isinstance(hits, list)


class TestPaState:
    def test_labels_flow_through(self) -> None:
        from analytics.brief.indicators import _pa_state

        rows = [
            {
                "open_time": START_MS + i * DAY_MS,
                "open": 100.0 + i,
                "high": 101.0 + i,
                "low": 99.0 + i,
                "close": 100.0 + i,
                "volume": 1000.0,
            }
            for i in range(15)
        ]
        state = _pa_state(pd.DataFrame(rows), atr14=1.0)
        assert state is not None
        assert state.label == "impulse_up"
        assert state.er > 0.99

    def test_zero_atr_is_none(self) -> None:
        from analytics.brief.indicators import _pa_state

        assert _pa_state(_daily_frame(15), atr14=0.0) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_brief_indicators.py -v`
Expected: the four new tests FAIL with `NotImplementedError`; earlier tests PASS.

- [ ] **Step 3: Implement the two builders**

In `analytics/brief/indicators.py`, add imports at the top (after the
existing `from analytics.strategies._shared import compute_ema`):

```python
from analytics.indicators import pa_character
from analytics.strategies.doji import detect_doji
from analytics.strategies.engulfing import detect_engulfing
from analytics.strategies.hammer_hanging_man import detect_hammer_hanging_man
from analytics.strategies.inside_bar import detect_inside_bar
from analytics.strategies.morning_evening_star import detect_morning_evening_star
from analytics.strategies.pin_bar import detect_pin_bar
```

Add the module constant next to `_EMA_SPANS` (`marubozu_retest` is
deliberately excluded — it fires on the retest bar, not the pattern bar):

```python
_CANDLE_DETECTORS: tuple[tuple[str, Callable[[pd.DataFrame], pd.DataFrame]], ...] = (
    ("doji", detect_doji),
    ("engulfing", detect_engulfing),
    ("hammer_hanging_man", detect_hammer_hanging_man),
    ("inside_bar", detect_inside_bar),
    ("morning_evening_star", detect_morning_evening_star),
    ("pin_bar", detect_pin_bar),
)

_PA_LOOKBACK = 10
```

Replace the `_candle_hits` stub:

```python
def _candle_hits(completed_1d: pd.DataFrame) -> list[CandleHit]:
    """Anatomy-detector signals landing on the LAST completed 1d bar."""
    if completed_1d.empty:
        return []
    last_open = int(completed_1d["open_time"].iloc[-1])
    hits: list[CandleHit] = []
    for name, detect in _CANDLE_DETECTORS:
        signals = detect(completed_1d)
        if signals.empty:
            continue
        on_last = signals[signals["open_time"] == last_open]
        for direction in on_last["direction"]:
            hits.append(CandleHit(pattern=name, direction=str(direction)))
    return sorted(hits, key=lambda h: (h.pattern, h.direction))
```

Replace the `_pa_state` stub:

```python
def _pa_state(completed_1d: pd.DataFrame, atr14: float) -> PaState | None:
    read = pa_character(
        completed_1d["close"].astype(float), atr14=atr14, n=_PA_LOOKBACK
    )
    if read is None:
        return None
    return PaState(label=read.label, er=read.er, speed_atr=read.speed_atr)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_brief_indicators.py -v`
Expected: all PASS

- [ ] **Step 5: Gate + commit**

```bash
make lint-py && make typecheck
git add analytics/brief/indicators.py tests/test_brief_indicators.py
git commit -m "feat(brief): yesterday-candle + PA-character indicator states"
```

---

### Task 5: BB / AVWAP / volume-profile states + failure isolation

**Files:**

- Modify: `analytics/brief/indicators.py` (replace the three remaining
  stubs; add anchor helpers)
- Test: `tests/test_brief_indicators.py` (append)

**Interfaces:**

- Consumes: `analytics.indicators.anchored_vwap` / `bollinger_state`
  (Task 2), `analytics.volume_profile.build_profile` / `value_area`
  (Task 1).
- Produces: working `_bb_state`, `_vwap_state`, `_profile_state`; the
  orchestrator is feature-complete — consumed by Task 6's bundle wiring.
- Sign conventions (spec): VWAP dist = `(ref - vwap) / atr` (+ = price
  above VWAP); POC dist = `(poc - ref) / atr` (+ = POC above price, the
  levels-gauge convention).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_brief_indicators.py`:

```python
H1_MS = 3_600_000


def _hourly_frame(n_hours: int, price: float = 100.0) -> pd.DataFrame:
    rows = [
        {
            "open_time": START_MS + i * H1_MS,
            "open": price,
            "high": price * 1.001,
            "low": price * 0.999,
            "close": price,
            "volume": 100.0,
        }
        for i in range(n_hours)
    ]
    return pd.DataFrame(rows)


class TestVwapState:
    def test_weekly_and_monthly_anchor(self) -> None:
        from analytics.brief.indicators import _vwap_state

        # START_MS is Mon 2024-01-01 00:00 UTC: week + month anchor coincide.
        hourly = _hourly_frame(48)
        as_of = START_MS + 2 * DAY_MS
        state = _vwap_state(hourly, ref_close=102.0, atr14=2.0, as_of_ms=as_of)
        assert state is not None
        assert state.weekly_price is not None
        assert abs(state.weekly_price - 100.0) < 0.2  # flat 100 bars
        assert state.weekly_dist_atr is not None
        assert state.weekly_dist_atr > 0  # price above VWAP -> positive
        assert state.monthly_price is not None

    def test_no_bars_past_anchor_is_none(self) -> None:
        from analytics.brief.indicators import _vwap_state

        # as_of in the NEXT week/month with no 1h bars after the anchors.
        hourly = _hourly_frame(24)
        as_of = START_MS + 40 * DAY_MS  # 2024-02-10, anchors past the data
        assert _vwap_state(hourly, 100.0, 2.0, as_of) is None

    def test_zero_atr_is_none(self) -> None:
        from analytics.brief.indicators import _vwap_state

        assert _vwap_state(_hourly_frame(24), 100.0, 0.0, START_MS + DAY_MS) is None


class TestProfileState:
    def test_inside_value_area(self) -> None:
        from analytics.brief.indicators import _profile_state

        hourly = _hourly_frame(24 * 10)
        as_of = START_MS + 10 * DAY_MS
        state = _profile_state(hourly, ref_close=100.0, atr14=2.0, as_of_ms=as_of)
        assert state is not None
        assert state.vs_value == "inside"
        assert state.val <= state.poc <= state.vah

    def test_above_value_area(self) -> None:
        from analytics.brief.indicators import _profile_state

        hourly = _hourly_frame(24 * 10)
        as_of = START_MS + 10 * DAY_MS
        state = _profile_state(hourly, ref_close=200.0, atr14=2.0, as_of_ms=as_of)
        assert state is not None
        assert state.vs_value == "above"
        assert state.poc_dist_atr < 0  # POC far below price

    def test_empty_window_is_none(self) -> None:
        from analytics.brief.indicators import _profile_state

        # All bars older than the 60d window.
        hourly = _hourly_frame(24)
        as_of = START_MS + 100 * DAY_MS
        assert _profile_state(hourly, 100.0, 2.0, as_of) is None


class TestBbStateAdapter:
    def test_flows_through(self) -> None:
        from analytics.brief.indicators import _bb_state

        state = _bb_state(_daily_frame(60), ref_close=100.0)
        assert state is not None
        assert isinstance(state.pct_b, float)


class TestFailureIsolation:
    def test_poisoned_hourly_frame_fails_only_hourly_blocks(self) -> None:
        # 1h frame with rows but NO volume column: vwap + profile raise
        # KeyError inside the adapter -> their sub-blocks None + 2 notes;
        # the 1d-based sub-blocks survive.
        df = _daily_frame(250)
        regime = pd.Series(["trend"] * 250)
        # open_time must land INSIDE the week/month anchor windows and the
        # 60d profile window, otherwise vwap/profile return None (empty
        # window) instead of raising, and no note is emitted.
        bad_hourly = pd.DataFrame(
            [
                {
                    "open_time": START_MS + 249 * DAY_MS,
                    "open": 1.0,
                    "high": 1.0,
                    "low": 1.0,
                    "close": 1.0,
                }
            ]
        )
        state, notes = build_indicator_state(
            completed_1d=df,
            completed_1h=bad_hourly,
            regime_series_1d=regime,
            ref_close=100.0,
            atr14=2.0,
            as_of_ms=START_MS + 250 * DAY_MS,
        )
        assert state is not None
        assert state.ema is not None
        assert state.pa is not None
        assert state.vwap is None
        assert state.profile is None
        assert sum("failed" in n for n in notes) == 2
        assert any(n.startswith("indicator vwap failed") for n in notes)
        assert any(n.startswith("indicator profile failed") for n in notes)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_brief_indicators.py -v`
Expected: new tests FAIL with `NotImplementedError` (and the isolation test
sees missing sub-blocks); earlier tests PASS.

- [ ] **Step 3: Implement the three builders + anchors**

In `analytics/brief/indicators.py`, extend the imports:

```python
from analytics.brief._common import DAY_MS, day_ahead_dow
from analytics.indicators import anchored_vwap, bollinger_state, pa_character
from analytics.volume_profile import build_profile, value_area
```

(replacing the existing `from analytics.brief._common import day_ahead_dow`
and `from analytics.indicators import pa_character` lines).

Add the anchor helpers + profile constant next to `_PA_LOOKBACK`:

```python
_PROFILE_DAYS = 60


def _week_anchor_ms(as_of_ms: int) -> int:
    """Monday 00:00 UTC of the week containing as_of (reference_levels rule)."""
    ts = pd.Timestamp(as_of_ms, unit="ms", tz="UTC").normalize()
    monday = ts - pd.Timedelta(days=int(ts.weekday()))
    return int(monday.value // 1_000_000)


def _month_anchor_ms(as_of_ms: int) -> int:
    """First of the month, 00:00 UTC."""
    ts = pd.Timestamp(as_of_ms, unit="ms", tz="UTC").normalize().replace(day=1)
    return int(ts.value // 1_000_000)
```

Replace the three stubs:

```python
def _bb_state(completed_1d: pd.DataFrame, ref_close: float) -> BbState | None:
    if completed_1d.empty:
        return None
    read = bollinger_state(completed_1d["close"].astype(float), ref_price=ref_close)
    if read is None:
        return None
    return BbState(
        pct_b=read.pct_b,
        bandwidth=read.bandwidth,
        bw_pctile=read.bw_pctile,
        squeeze=read.squeeze,
    )


def _vwap_state(
    completed_1h: pd.DataFrame, ref_close: float, atr14: float, as_of_ms: int
) -> VwapState | None:
    if atr14 <= 0.0:
        return None
    weekly = anchored_vwap(completed_1h, _week_anchor_ms(as_of_ms))
    monthly = anchored_vwap(completed_1h, _month_anchor_ms(as_of_ms))
    if weekly is None and monthly is None:
        return None
    return VwapState(
        weekly_price=weekly,
        weekly_dist_atr=((ref_close - weekly) / atr14) if weekly is not None else None,
        monthly_price=monthly,
        monthly_dist_atr=(
            (ref_close - monthly) / atr14 if monthly is not None else None
        ),
    )


def _profile_state(
    completed_1h: pd.DataFrame, ref_close: float, atr14: float, as_of_ms: int
) -> ProfileState | None:
    if atr14 <= 0.0 or completed_1h.empty:
        return None
    window = completed_1h[
        completed_1h["open_time"] >= as_of_ms - _PROFILE_DAYS * DAY_MS
    ]
    profile = build_profile(window)
    if profile is None:
        return None
    poc, vah, val = value_area(profile)
    if ref_close > vah:
        vs_value = "above"
    elif ref_close < val:
        vs_value = "below"
    else:
        vs_value = "inside"
    return ProfileState(
        poc=poc,
        vah=vah,
        val=val,
        vs_value=vs_value,
        poc_dist_atr=(poc - ref_close) / atr14,
    )
```

Also remove the `except NotImplementedError: return None` branch from the
orchestrator's `run()` helper (all sub-blocks now exist) and delete this
line from its docstring: "NotImplementedError from a not-yet-built
sub-block (staged Tasks 4-5) is treated as 'absent', not 'failed' — no
note."

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_brief_indicators.py tests/test_indicators.py tests/test_volume_profile.py -v`
Expected: all PASS

- [ ] **Step 5: Gate + commit**

```bash
make lint-py && make typecheck
git add analytics/brief/indicators.py tests/test_brief_indicators.py
git commit -m "feat(brief): BB/AVWAP/volume-profile indicator states + failure isolation"
```

---

### Task 6: Bundle wiring (fetch window, shared regime series, notes)

**Files:**

- Modify: `analytics/brief/bundle.py`
- Test: `tests/test_brief_bundle.py` (append)

**Interfaces:**

- Consumes: `build_indicator_state(...)` (Tasks 3–5),
  `classify_series(df, timeframe)` from `analytics.regime`.
- Produces: `SymbolPanel.indicators` populated for every non-error panel;
  indicator failure notes surface in `bundle.health.notes` prefixed
  `"{symbol}: "`. Tasks 7–8 rely on `panel.indicators` being non-None on
  healthy fixture data.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_brief_bundle.py` — the file already imports
`make_conn` / `seed_symbol` / `START_MS` / `DAY_MS` from
`tests/_brief_fixtures.py` and defines `AS_OF = START_MS + 60 * DAY_MS`
plus a `_cfg(symbols)` helper; reuse them verbatim:

```python
def test_panel_has_indicator_state() -> None:
    conn = make_conn()
    seed_symbol(conn, "BTCUSDT", START_MS, 60)
    bundle = compute_brief(conn, _cfg(("BTCUSDT",)))
    panel = bundle.panels[0]
    assert panel.error is None
    assert panel.indicators is not None
    # 60 seeded days: EMA20/50 available, EMA200 not.
    assert panel.indicators.ema is not None
    assert panel.indicators.ema.above_20 is not None
    assert panel.indicators.ema.above_200 is None
    assert panel.indicators.range_state is not None
    assert panel.indicators.candles is not None
    assert panel.indicators.vwap is not None
    assert panel.indicators.profile is not None


def test_indicators_deterministic() -> None:
    conn = make_conn()
    seed_symbol(conn, "BTCUSDT", START_MS, 60)
    first = compute_brief(conn, _cfg(("BTCUSDT",)))
    second = compute_brief(conn, _cfg(("BTCUSDT",)))
    assert first.panels[0].indicators == second.panels[0].indicators
```

(`AS_OF` falls exactly on 2024-03-01 00:00 UTC, a Friday: the monthly VWAP
anchor coincides with `as_of` so `monthly_price` is None, but the weekly
anchor — Monday of that week — has four seeded days behind it, so
`VwapState` itself is non-None.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_brief_bundle.py -v`
Expected: the two new tests FAIL (`indicators` is None — bundle never calls
the adapter); existing tests PASS.

- [ ] **Step 3: Wire the bundle**

In `analytics/brief/bundle.py`:

1. Add the import:

   ```python
   from analytics.brief.indicators import build_indicator_state
   ```

2. Change the fetch constant (keep the comment accurate):

   ```python
   _H1_FETCH_DAYS = 62  # 60d volume profile + monthly AVWAP + 2d margin
   ```

3. In `_compute_panel`, compute the 1d regime series ONCE and share it.
   Replace the two lines inside the `SymbolPanel(...)` construction that
   read `regime_1d=_regime_label(completed_1d, "1d"),` by first inserting,
   after the `zones_above, zones_below = build_zone_rows(...)` call:

   ```python
   regime_series_1d = classify_series(completed_1d, "1d")
   indicators, ind_notes = build_indicator_state(
       completed_1d=completed_1d,
       completed_1h=completed_1h,
       regime_series_1d=regime_series_1d,
       ref_close=ref_close,
       atr14=atr,
       as_of_ms=as_of,
   )
   notes.extend(f"{symbol}: {n}" for n in ind_notes)
   ```

   and then in the `SymbolPanel(...)` construction:

   ```python
       regime_1d=str(regime_series_1d.iloc[-1]),
       regime_4h=_regime_label(frames.get("4h", pd.DataFrame()), "4h"),
       ...
       seasonality=build_strip(conn, symbol, as_of, cfg.stats_days),
       indicators=indicators,
       error=None,
   ```

   (`_regime_label` stays — the 4h read still uses it. `completed_1d` is
   never empty here — the `_MIN_DAILY_BARS` guard raised earlier — so
   `regime_series_1d.iloc[-1]` is safe and byte-identical to the old
   `_regime_label(completed_1d, "1d")`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_brief_bundle.py tests/test_web_brief.py tests/test_cli_brief.py -v`
Expected: all PASS (`bundle_to_dict` is `asdict`-based, so the new nested
dataclasses serialise without changes; the web tests may not assert on the
new key yet — that lands in Task 8).

- [ ] **Step 5: Gate + commit**

```bash
make lint-py && make typecheck && poetry run pytest tests/ -k brief -q
git add analytics/brief/bundle.py tests/test_brief_bundle.py
git commit -m "feat(brief): wire IndicatorState into the bundle (62d 1h fetch, shared regime series)"
```

---

### Task 7: Renderer block + determinism

**Files:**

- Modify: `analytics/brief/render.py`
- Test: `tests/test_brief_render.py` (append)

**Interfaces:**

- Consumes: `IndicatorState` and sub-dataclasses (Task 3),
  `panel.indicators` (Task 6).
- Produces: `_indicator_lines(state: IndicatorState | None) -> list[str]`
  inserted in `_panel_lines` after the "Last …" header line, before
  "Levels". Exact line formats below are FROZEN (tests assert them).

Line formats (9-char left label column via `f"{'EMA':<9}"`, matching the
existing `Levels` gauge lines):

```text
EMA      ▲20 ▲50 ▼200 · stack mixed · 200 falling
State    range since 2024-02-19 (18 bars) · 105,200–112,800 · 62%
Monday   inside (43%)
Candle   engulfing·long, doji·short
PA       grind_up · ER 0.55 · 0.40 ATR/bar
BB       %B 0.71 · bw 8.3% (p23) | AVWAP W +0.40 · M −1.20
VP60d    POC 108,400 (−0.30) · VA 104,100–113,900 · inside
```

Rules: unavailable EMA span renders `—{span}`; stack/slope absent →
`stack n/a` / `200 n/a`; non-range State omits bounds; Monday
above/below/forming have no `(pos)`; empty candles → `Candle   none`;
BB pctile absent → no `(p..)` bit; squeeze → `(p8 squeeze)`; the BB|AVWAP
line renders only the surviving half (drops entirely when both are None);
minus signs are the ASCII `-` from `fmt_dist` (the sketch's `−` is
typographic only). A None sub-block drops its line; `indicators is None`
drops the whole block.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_brief_render.py` (follow the file's existing import
style; add the new dataclass imports from `analytics.brief.types`):

```python
from analytics.brief.render import _indicator_lines
from analytics.brief.types import (
    BbState,
    CandleHit,
    EmaState,
    IndicatorState,
    MondayState,
    PaState,
    ProfileState,
    RangeState,
    VwapState,
)


def _full_state() -> IndicatorState:
    return IndicatorState(
        ema=EmaState(
            above_20=True, above_50=True, above_200=False,
            stack="mixed", slope_200="falling",
        ),
        range_state=RangeState(
            label="range", since_ms=1_708_300_800_000,  # 2024-02-19 UTC
            bars=18, range_low=105200.0, range_high=112800.0, pos=0.62,
        ),
        monday=MondayState(state="inside", pos=0.43),
        candles=[
            CandleHit(pattern="doji", direction="short"),
            CandleHit(pattern="engulfing", direction="long"),
        ],
        pa=PaState(label="grind_up", er=0.55, speed_atr=0.4),
        bb=BbState(pct_b=0.71, bandwidth=0.083, bw_pctile=0.23, squeeze=False),
        vwap=VwapState(
            weekly_price=101.0, weekly_dist_atr=0.4,
            monthly_price=110.0, monthly_dist_atr=-1.2,
        ),
        profile=ProfileState(
            poc=108400.0, vah=113900.0, val=104100.0,
            vs_value="inside", poc_dist_atr=-0.3,
        ),
    )


class TestIndicatorLines:
    def test_full_block(self) -> None:
        lines = _indicator_lines(_full_state())
        assert lines == [
            "EMA      ▲20 ▲50 ▼200 · stack mixed · 200 falling",
            "State    range since 2024-02-19 (18 bars) · 105,200–112,800 · 62%",
            "Monday   inside (43%)",
            "Candle   doji·short, engulfing·long",
            "PA       grind_up · ER 0.55 · 0.40 ATR/bar",
            "BB       %B 0.71 · bw 8.3% (p23) | AVWAP W +0.40 · M -1.20",
            "VP60d    POC 108,400 (-0.30) · VA 104,100–113,900 · inside",
        ]

    def test_none_state_is_empty(self) -> None:
        assert _indicator_lines(None) == []

    def test_failed_blocks_drop_lines(self) -> None:
        state = IndicatorState(
            ema=None, range_state=None, monday=None,
            candles=[], pa=None, bb=None, vwap=None, profile=None,
        )
        assert _indicator_lines(state) == ["Candle   none"]

    def test_bb_half_survives_alone(self) -> None:
        state = IndicatorState(
            ema=None, range_state=None, monday=None, candles=None,
            pa=None,
            bb=BbState(pct_b=0.5, bandwidth=0.02, bw_pctile=None, squeeze=None),
            vwap=None, profile=None,
        )
        assert _indicator_lines(state) == ["BB       %B 0.50 · bw 2.0%"]

    def test_vwap_half_survives_alone_with_squeeze_variants(self) -> None:
        state = IndicatorState(
            ema=None, range_state=None, monday=None, candles=None, pa=None,
            bb=None,
            vwap=VwapState(
                weekly_price=None, weekly_dist_atr=None,
                monthly_price=100.0, monthly_dist_atr=0.8,
            ),
            profile=None,
        )
        assert _indicator_lines(state) == ["AVWAP    M +0.80"]

    def test_ema_warmup_and_trend_state(self) -> None:
        state = IndicatorState(
            ema=EmaState(
                above_20=True, above_50=None, above_200=None,
                stack=None, slope_200=None,
            ),
            range_state=RangeState(
                label="trend", since_ms=1_708_300_800_000, bars=5,
                range_low=None, range_high=None, pos=None,
            ),
            monday=MondayState(state="forming", pos=None),
            candles=None, pa=None, bb=None, vwap=None, profile=None,
        )
        assert _indicator_lines(state) == [
            "EMA      ▲20 —50 —200 · stack n/a · 200 n/a",
            "State    trend since 2024-02-19 (5 bars)",
            "Monday   forming",
        ]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_brief_render.py -v`
Expected: new tests FAIL — `ImportError: cannot import name '_indicator_lines'`

- [ ] **Step 3: Implement the renderer block**

In `analytics/brief/render.py`, extend the types import with
`BbState, CandleHit, EmaState, IndicatorState, MondayState, PaState,
ProfileState, RangeState, VwapState` (alphabetical within the existing
import block), then add after `_ref_price_label`:

```python
def _ema_bit(above: bool | None, span: int) -> str:
    if above is None:
        return f"—{span}"
    return f"{'▲' if above else '▼'}{span}"


def _ema_line(ema: EmaState) -> str:
    spans = " ".join(
        _ema_bit(a, s)
        for a, s in ((ema.above_20, 20), (ema.above_50, 50), (ema.above_200, 200))
    )
    stack = f"stack {ema.stack}" if ema.stack is not None else "stack n/a"
    slope = f"200 {ema.slope_200}" if ema.slope_200 is not None else "200 n/a"
    return f"{'EMA':<9}{spans} · {stack} · {slope}"


def _state_line(rs: RangeState) -> str:
    since = pd.Timestamp(rs.since_ms, unit="ms", tz="UTC").strftime("%Y-%m-%d")
    head = f"{'State':<9}{rs.label} since {since} ({rs.bars} bars)"
    if rs.range_low is None or rs.range_high is None:
        return head
    bounds = f"{fmt_price(rs.range_low)}–{fmt_price(rs.range_high)}"
    pos = f" · {fmt_frac(rs.pos)}" if rs.pos is not None else ""
    return f"{head} · {bounds}{pos}"


def _monday_line(monday: MondayState) -> str:
    pos = f" ({fmt_frac(monday.pos)})" if monday.pos is not None else ""
    return f"{'Monday':<9}{monday.state}{pos}"


def _candle_line(candles: list[CandleHit]) -> str:
    bits = ", ".join(f"{c.pattern}·{c.direction}" for c in candles) or "none"
    return f"{'Candle':<9}{bits}"


def _pa_line(pa: PaState) -> str:
    return (
        f"{'PA':<9}{pa.label} · ER {pa.er:.2f} · {pa.speed_atr:.2f} ATR/bar"
    )


def _bb_bit(bb: BbState) -> str:
    bits = f"%B {bb.pct_b:.2f} · bw {bb.bandwidth * 100:.1f}%"
    if bb.bw_pctile is not None:
        squeeze = " squeeze" if bb.squeeze else ""
        bits += f" (p{round(bb.bw_pctile * 100)}{squeeze})"
    return bits


def _vwap_bit(vwap: VwapState) -> str:
    parts: list[str] = []
    if vwap.weekly_dist_atr is not None:
        parts.append(f"W {fmt_dist(vwap.weekly_dist_atr)}")
    if vwap.monthly_dist_atr is not None:
        parts.append(f"M {fmt_dist(vwap.monthly_dist_atr)}")
    return " · ".join(parts)


def _profile_line(profile: ProfileState) -> str:
    return (
        f"{'VP60d':<9}POC {fmt_price(profile.poc)} ({fmt_dist(profile.poc_dist_atr)})"
        f" · VA {fmt_price(profile.val)}–{fmt_price(profile.vah)}"
        f" · {profile.vs_value}"
    )


def _indicator_lines(state: IndicatorState | None) -> list[str]:
    """One line per surviving sub-block; failed blocks drop silently."""
    if state is None:
        return []
    lines: list[str] = []
    if state.ema is not None:
        lines.append(_ema_line(state.ema))
    if state.range_state is not None:
        lines.append(_state_line(state.range_state))
    if state.monday is not None:
        lines.append(_monday_line(state.monday))
    if state.candles is not None:
        lines.append(_candle_line(state.candles))
    if state.pa is not None:
        lines.append(_pa_line(state.pa))
    bb_bit = _bb_bit(state.bb) if state.bb is not None else None
    vwap_bit = _vwap_bit(state.vwap) if state.vwap is not None else None
    if vwap_bit == "":
        vwap_bit = None
    if bb_bit is not None and vwap_bit is not None:
        lines.append(f"{'BB':<9}{bb_bit} | AVWAP {vwap_bit}")
    elif bb_bit is not None:
        lines.append(f"{'BB':<9}{bb_bit}")
    elif vwap_bit is not None:
        lines.append(f"{'AVWAP':<9}{vwap_bit}")
    if state.profile is not None:
        lines.append(_profile_line(state.profile))
    return lines
```

Then wire it into `_panel_lines` — after the `lines.append(f"Last ...")`
block (the one ending with `{adr}"` `)`) and before the
`above = " · ".join(...)` line, insert:

```python
    lines.extend(_indicator_lines(panel.indicators))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_brief_render.py tests/test_brief_bundle.py tests/test_cli_brief.py -v`
Expected: all PASS. If any existing render/CLI test asserts a full panel
text block, update its expectation to include the new indicator lines —
that is an intended, reviewed change to brief output (NOT a regression
golden; `make test-regression` stays untouched).

- [ ] **Step 5: Gate + commit**

```bash
make lint-py && make typecheck && poetry run pytest tests/ -k brief -q
git add analytics/brief/render.py tests/test_brief_render.py
git commit -m "feat(brief): render the indicator-state block"
```

---

### Task 8: API models + Svelte indicators section + legend

**Files:**

- Modify: `web/api/models/brief.py`
- Modify: `web/ui/src/api.ts` (brief interfaces, around line 631)
- Modify: `web/ui/src/pages/Brief.svelte`
- Test: `tests/test_web_brief.py` (append one assertion-level test)

**Interfaces:**

- Consumes: the `indicators` key emitted by `bundle_to_dict` (Task 6);
  dataclass field names from Task 3 (they ARE the JSON keys).
- Produces: API + UI surface; nothing downstream consumes this.

**IMPORTANT:** load `/frontend-design` then `/frontend-svelte` before
touching `web/ui/` files (repo rule).

- [ ] **Step 1: Write the failing API test**

Append to `tests/test_web_brief.py` — the file already defines a
`_client(conn)` helper (FastAPI TestClient with `get_db`/`require_token`
overridden) and `AS_OF_ISO = "2024-03-01T00:00:00Z"`, and imports
`START_MS` / `make_conn` / `seed_symbol` from `tests/_brief_fixtures.py`;
reuse them verbatim:

```python
def test_get_brief_panel_includes_indicators() -> None:
    conn = make_conn()
    seed_symbol(conn, "BTCUSDT", START_MS, 60)
    client = _client(conn)
    res = client.get(
        "/api/brief",
        params={"symbols": "BTCUSDT", "days": 60, "as_of": AS_OF_ISO},
    )
    assert res.status_code == 200
    panel = res.json()["panels"][0]
    assert panel["indicators"] is not None
    assert panel["indicators"]["ema"]["above_20"] is not None
    assert panel["indicators"]["profile"]["vs_value"] in (
        "above",
        "inside",
        "below",
    )
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `poetry run pytest tests/test_web_brief.py -v`
Expected: FAIL — the response model strips the unknown `indicators` key
(Pydantic drops fields not declared on `SymbolPanelModel`).

- [ ] **Step 3: Add the Pydantic models**

In `web/api/models/brief.py`, insert after `SeasonalityStripModel`:

```python
class EmaStateModel(BaseModel):
    above_20: bool | None
    above_50: bool | None
    above_200: bool | None
    stack: str | None
    slope_200: str | None


class RangeStateModel(BaseModel):
    label: str
    since_ms: int
    bars: int
    range_low: float | None
    range_high: float | None
    pos: float | None


class MondayStateModel(BaseModel):
    state: str
    pos: float | None


class CandleHitModel(BaseModel):
    pattern: str
    direction: str


class PaStateModel(BaseModel):
    label: str
    er: float
    speed_atr: float


class BbStateModel(BaseModel):
    pct_b: float
    bandwidth: float
    bw_pctile: float | None
    squeeze: bool | None


class VwapStateModel(BaseModel):
    weekly_price: float | None
    weekly_dist_atr: float | None
    monthly_price: float | None
    monthly_dist_atr: float | None


class ProfileStateModel(BaseModel):
    poc: float
    vah: float
    val: float
    vs_value: str
    poc_dist_atr: float


class IndicatorStateModel(BaseModel):
    ema: EmaStateModel | None
    range_state: RangeStateModel | None
    monday: MondayStateModel | None
    candles: list[CandleHitModel] | None
    pa: PaStateModel | None
    bb: BbStateModel | None
    vwap: VwapStateModel | None
    profile: ProfileStateModel | None
```

And in `SymbolPanelModel`, between `seasonality` and `error`:

```python
    indicators: IndicatorStateModel | None
```

- [ ] **Step 4: Run the API test to verify it passes**

Run: `poetry run pytest tests/test_web_brief.py -v`
Expected: all PASS

- [ ] **Step 5: Add the TypeScript interfaces**

In `web/ui/src/api.ts`, insert before `export interface BriefSymbolPanel`
(≈ line 631):

```typescript
export interface BriefEmaState {
  above_20: boolean | null;
  above_50: boolean | null;
  above_200: boolean | null;
  stack: string | null;
  slope_200: string | null;
}

export interface BriefRangeState {
  label: string;
  since_ms: number;
  bars: number;
  range_low: number | null;
  range_high: number | null;
  pos: number | null;
}

export interface BriefMondayState {
  state: string;
  pos: number | null;
}

export interface BriefCandleHit {
  pattern: string;
  direction: string;
}

export interface BriefPaState {
  label: string;
  er: number;
  speed_atr: number;
}

export interface BriefBbState {
  pct_b: number;
  bandwidth: number;
  bw_pctile: number | null;
  squeeze: boolean | null;
}

export interface BriefVwapState {
  weekly_price: number | null;
  weekly_dist_atr: number | null;
  monthly_price: number | null;
  monthly_dist_atr: number | null;
}

export interface BriefProfileState {
  poc: number;
  vah: number;
  val: number;
  vs_value: string;
  poc_dist_atr: number;
}

export interface BriefIndicatorState {
  ema: BriefEmaState | null;
  range_state: BriefRangeState | null;
  monday: BriefMondayState | null;
  candles: BriefCandleHit[] | null;
  pa: BriefPaState | null;
  bb: BriefBbState | null;
  vwap: BriefVwapState | null;
  profile: BriefProfileState | null;
}
```

And add to `BriefSymbolPanel` between `seasonality` and `error`:

```typescript
  indicators: BriefIndicatorState | null;
```

- [ ] **Step 6: Add the Svelte indicators section + legend entries**

In `web/ui/src/pages/Brief.svelte`:

1. After the `.panel-meta` div (the `ATR14 … · ADR …` block, ends ≈ line
   157) and before the `.ladder` div, insert:

   ```svelte
   {#if panel.indicators}
     {@const ind = panel.indicators}
     <div class="indicators muted">
       {#if ind.ema}
         <div class="ind-row">
           <span class="ind-label">EMA</span>
           <span>
             {#each [[ind.ema.above_20, 20], [ind.ema.above_50, 50], [ind.ema.above_200, 200]] as [above, span]}
               <span
                 class="ema-bit"
                 class:pos={above === true}
                 class:neg={above === false}
               >
                 {above === null ? "—" : above ? "▲" : "▼"}{span}
               </span>
             {/each}
             {#if ind.ema.stack}· stack {ind.ema.stack}{/if}
             {#if ind.ema.slope_200}· 200 {ind.ema.slope_200}{/if}
           </span>
         </div>
       {/if}
       {#if ind.range_state}
         <div class="ind-row">
           <span class="ind-label">State</span>
           <span>
             {ind.range_state.label} · {ind.range_state.bars} bars
             {#if ind.range_state.range_low !== null && ind.range_state.range_high !== null}
               · {fmtPrice(ind.range_state.range_low)}–{fmtPrice(ind.range_state.range_high)}
               {#if ind.range_state.pos !== null}· {fmtPct(ind.range_state.pos)}{/if}
             {/if}
           </span>
         </div>
       {/if}
       {#if ind.monday}
         <div class="ind-row">
           <span class="ind-label">Monday</span>
           <span>
             {ind.monday.state}{#if ind.monday.pos !== null}&nbsp;({fmtPct(ind.monday.pos)}){/if}
           </span>
         </div>
       {/if}
       {#if ind.candles}
         <div class="ind-row">
           <span class="ind-label">Candle</span>
           <span>
             {ind.candles.length
               ? ind.candles.map((c) => `${c.pattern}·${c.direction}`).join(", ")
               : "none"}
           </span>
         </div>
       {/if}
       {#if ind.pa}
         <div class="ind-row">
           <span class="ind-label">PA</span>
           <span>
             {ind.pa.label} · ER {ind.pa.er.toFixed(2)} · {ind.pa.speed_atr.toFixed(2)} ATR/bar
           </span>
         </div>
       {/if}
       {#if ind.bb || ind.vwap}
         <div class="ind-row">
           <span class="ind-label">BB/VWAP</span>
           <span>
             {#if ind.bb}
               %B {ind.bb.pct_b.toFixed(2)} · bw {(ind.bb.bandwidth * 100).toFixed(1)}%
               {#if ind.bb.bw_pctile !== null}
                 (p{Math.round(ind.bb.bw_pctile * 100)}{ind.bb.squeeze ? " squeeze" : ""})
               {/if}
             {/if}
             {#if ind.vwap}
               {#if ind.bb}·{/if}
               {#if ind.vwap.weekly_dist_atr !== null}W {fmtDist(ind.vwap.weekly_dist_atr)}{/if}
               {#if ind.vwap.monthly_dist_atr !== null}M {fmtDist(ind.vwap.monthly_dist_atr)}{/if}
             {/if}
           </span>
         </div>
       {/if}
       {#if ind.profile}
         <div class="ind-row">
           <span class="ind-label">VP 60d</span>
           <span>
             POC {fmtPrice(ind.profile.poc)} ({fmtDist(ind.profile.poc_dist_atr)})
             · VA {fmtPrice(ind.profile.val)}–{fmtPrice(ind.profile.vah)}
             · {ind.profile.vs_value}
           </span>
         </div>
       {/if}
     </div>
   {/if}
   ```

2. Add the styles at the end of the `<style>` block (before the closing
   `</style>`):

   ```css
   /* ── Indicator states ─────────────────────────────────────────────────── */
   .indicators {
     display: grid;
     gap: 2px;
     font-size: 11px;
     margin-bottom: 10px;
     font-variant-numeric: tabular-nums;
   }

   .ind-row {
     display: grid;
     grid-template-columns: 56px 1fr;
     gap: 8px;
     padding: 1px 4px;
   }

   .ind-label {
     font-size: 10px;
     letter-spacing: 0.04em;
     color: var(--text-dim);
   }

   .ema-bit { margin-right: 4px; }
   ```

   (`.pos`/`.neg` already exist and color the ▲/▼ bits.)

3. Extend the legend `<dl>` (after the `Seasonality` `<dd>`, before the
   `Pundit board` `<dt>`):

   ```svelte
   <dt>EMA</dt>
   <dd>
     Price vs the 20/50/200-day EMAs (▲ above / ▼ below / — not enough
     history), the stack order (bullish 20&gt;50&gt;200), and the 200's
     5-day slope.
   </dd>
   <dt>State</dt>
   <dd>
     Current 1D regime and how many bars it has held; when ranging, the
     run's high–low band and where price sits inside it.
   </dd>
   <dt>Monday</dt>
   <dd>
     Price vs this week's Monday range (above / inside / below); "forming"
     on Mondays while the range is still being set.
   </dd>
   <dt>Candle</dt>
   <dd>
     Anatomy patterns detected on yesterday's completed daily candle
     (engulfing, pin bar, doji, inside bar, hammer, star).
   </dd>
   <dt>PA</dt>
   <dd>
     Price-action character over the last 10 days: impulse (fast
     directional) vs grind (slow directional) vs chop, from efficiency
     ratio × ATR-normalised speed.
   </dd>
   <dt>BB/VWAP</dt>
   <dd>
     Bollinger(20,2σ) %B and bandwidth (p = squeeze percentile), plus
     price distance in ATRs from the weekly (W) and monthly (M) anchored
     VWAPs.
   </dd>
   <dt>VP 60d</dt>
   <dd>
     60-day volume profile from our own 1h data: point of control and 70%
     value area, with price above / inside / below value.
   </dd>
   ```

- [ ] **Step 7: Build + full gate + commit**

```bash
make web-build
make lint-py && make typecheck && make test
git add web/api/models/brief.py web/ui/src/api.ts web/ui/src/pages/Brief.svelte tests/test_web_brief.py
git commit -m "feat(brief): indicator states on the API + Brief page (+ legend entries)"
```

Expected: `make web-build` clean (the pre-existing Backtest.svelte warning
is known and unrelated); full pytest green.

---

### Task 9: Docs sync + final verification

**Files:**

- Modify: `CLAUDE.md` (the `brief/` bullet in Project Structure)
- Modify: `README.md` (the daily-brief feature mention)

**Interfaces:**

- Consumes: everything shipped in Tasks 1–8.
- Produces: docs in sync; final whole-branch gate evidence.

- [ ] **Step 1: Update `CLAUDE.md`**

In the `analytics/` section's `brief/` bullet, append after "Four stats
computes gained additive keyword-only `end_ms` (default `None` = unchanged)
for --as-of determinism.":

```text
M1 indicator-state layer: pure primitives `analytics/volume_profile.py`
(volume-at-price POC/VAH/VAL) + `analytics/indicators.py` (anchored VWAP,
Bollinger read, ER, PA character) adapted by `brief/indicators.py` into an
additive `SymbolPanel.indicators` block (EMA state · regime run-length +
range bounds · Monday-range state · yesterday candle patterns · PA
impulse/grind/chop · BB/AVWAP · 60d volume profile); 1d-only, completed
bars, independent sub-blocks (one failure → health note, not an error);
`_H1_FETCH_DAYS` 62.
```

- [ ] **Step 2: Update `README.md`**

Find the daily market brief feature description (search for "brief") and
extend it with one sentence:

```text
The brief also carries an indicator-state block per symbol: EMA 20/50/200
stack, regime run-length with range bounds, Monday-range position,
yesterday's candle patterns, PA character (impulse/grind/chop),
Bollinger + weekly/monthly anchored-VWAP reads, and a 60-day
volume-profile POC/VAH/VAL — all computed from our own OHLCV.
```

- [ ] **Step 3: Full Definition-of-Done gate**

```bash
make lint-py && make typecheck && make test
make test-regression
make web-build
make lint-md
```

Expected: everything green; regression goldens UNMOVED (report the actual
output — if goldens moved, STOP: something touched the backtest path,
which this plan must not do).

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md README.md
git commit -m "docs: sync CLAUDE.md + README for the M1 indicator-state layer"
```

---

## Verification (whole branch)

1. `poetry run pytest tests/ -q` — full suite green.
2. `make test-regression` — goldens unmoved.
3. `make web-build` — clean.
4. Manual smoke (optional, needs a synced local `analytics.db`):
   `PYTHONPATH=. poetry run python buibui.py brief` — every panel shows the
   indicator block; `--as-of` twice produces byte-identical output.
