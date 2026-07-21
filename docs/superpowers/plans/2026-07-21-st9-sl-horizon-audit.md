# ST9 / H11 SL-Horizon Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a read-only audit that decides whether six candle detectors' negative
verdicts are an artifact of their hard-coded flat 2% stop-loss, by re-resolving the same
signals under an ATR-scaled stop grid and emitting a pre-committed verdict.

**Architecture:** One pure library (`analytics/sl_horizon.py`, no DB/IO) plus one thin
driver (`tools/sl_horizon_audit.py`, the only module that touches DuckDB). The library
reuses `analytics.exits.replay.replay_exits` as its sole resolver and
`analytics.audit_guard.evaluate_audit_cells` as its sole statistical gate — neither is
modified. Mirrors the existing `structural_touch.py` + `tools/structural_touch_decay_audit.py`
split.

**Tech Stack:** Python 3.11+, pandas, numpy, DuckDB (read-only), pytest, ruff, mypy strict.

**Spec:** `docs/superpowers/specs/2026-07-21-st9-sl-horizon-audit-design.md` — read it
before starting. This plan implements it; where they differ, the spec wins.

## Global Constraints

- **Read-only.** No DB writes, no schema change, no config edit, no change to any file
  under `analytics/strategies/`, `analytics/backtest/`, `analytics/signal/`,
  `analytics/exits/`, or `analytics/audit_guard.py`. This audit only adds files.
- **Definition of Done for every task:** `make lint-py` ✓, `make typecheck` ✓ (mypy
  strict — every function needs annotations including `-> None`), `make test` green.
  State each result plainly. If a step was skipped or failed, say so.
- **No network in tests.** Analytics tests use `duckdb.connect(":memory:")`.
- **The `k` grid is a-priori:** `(0.5, 1.0, 1.5, 2.0, 3.0)`. Never widen, narrow, or
  re-centre it in response to results.
- **`tp_r` is pinned**, never swept.
- **Verdict vocabulary is fixed:** `SUSPECT` / `CONFIRMED-BAD` / `NO-DIFFERENCE` /
  `INSUFFICIENT`. Do not invent other strings.
- **The six detectors** are exactly: `doji`, `engulfing`, `hammer_hanging_man`,
  `inside_bar`, `morning_evening_star`, `pin_bar`.
- Markdown you write must pass `make lint-md`: every fence gets a language, table
  delimiter rows are spaced `| --- |`.
- Conventional commits (`feat:`, `test:`, `docs:`). Branch is already
  `docs/st9-sl-horizon-audit`; keep working on it.

## The historical defect this plan must not repeat

The two substrates use **different entry conventions**, and getting this wrong produces
a plausible-looking audit whose numbers are silently wrong:

| Substrate | Entry price | Forward window |
| --- | --- | --- |
| Backtest (`engine.py:954-1061`) | `opens[sig_idx + 1]` — the **open of the bar after** the signal bar | `highs[entry_idx:]` — **inclusive** of the entry bar, so a trade can stop out on its own entry bar |
| Live (`outcome_backfill.py`) | the stored `entry_price` column | bars **strictly after** `candle_ts_ms` |

If you hard-code one convention, the fidelity check (Task 6) fails with a systematic
offset that looks like a modelling error but is actually a bar-alignment bug. The
resolver therefore takes an explicit `convention` argument. This is the single most
likely way to waste a day on this plan.

## File Structure

| File | Responsibility |
| --- | --- |
| `analytics/sl_horizon.py` (create) | Pure library: config, level math, ATR lookup, arm resolution, paired table, descriptive horizon, verdict rule. No DB, no IO. |
| `tools/sl_horizon_audit.py` (create) | Driver: DuckDB read-only front door, signal loaders for both substrates, fidelity checks, markdown report. |
| `tests/test_sl_horizon.py` (create) | Unit tests for the pure library. |
| `tests/test_sl_horizon_audit.py` (create) | Tests for the driver's loaders and fidelity comparison, against in-memory DuckDB. |
| `Makefile` (modify) | Add `buibui-sl-horizon-audit` target and register it in `.PHONY`. |
| `docs/audits/2026-07-21-st9-sl-horizon.md` (create, Task 7) | The verdict document, written after the first real run. |
| `CLAUDE.md`, `README.md` (modify, Task 7) | Document the new module and tool. |

---

### Task 1: Config and counterfactual level math

**Files:**

- Create: `analytics/sl_horizon.py`
- Create: `tests/test_sl_horizon.py`

**Interfaces:**

- Consumes: `analytics.signal.outcome_backfill.DEFAULT_MAX_HOLD_BARS` (a
  `dict[str, int]` — `{"15m": 96, "1h": 48, "4h": 30, "1d": 14}`).
- Produces: `SLGridConfig`, `DEFAULT_MULTIPLIERS`, `BASELINE_ARM`, `arm_label`,
  `levels_from_sl_dist`, `baseline_levels`, `counterfactual_levels`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_sl_horizon.py`:

```python
"""Unit tests for the ST9/H11 SL-horizon audit library."""

from __future__ import annotations

import pytest

from analytics.sl_horizon import (
    BASELINE_ARM,
    DEFAULT_MULTIPLIERS,
    SLGridConfig,
    arm_label,
    baseline_levels,
    counterfactual_levels,
)


def test_default_grid_is_the_a_priori_one() -> None:
    assert DEFAULT_MULTIPLIERS == (0.5, 1.0, 1.5, 2.0, 3.0)


def test_config_defaults_match_live_hold_horizons() -> None:
    cfg = SLGridConfig()
    assert cfg.max_hold_bars_by_tf["15m"] == 96
    assert cfg.max_hold_bars_by_tf["1h"] == 48
    assert cfg.max_hold_bars_by_tf["4h"] == 30
    assert cfg.max_hold_bars_by_tf["1d"] == 14
    assert cfg.baseline_pct == 0.02


def test_config_rejects_empty_grid() -> None:
    with pytest.raises(ValueError, match="multipliers"):
        SLGridConfig(multipliers=())


def test_config_rejects_non_positive_multiplier() -> None:
    with pytest.raises(ValueError, match="multipliers"):
        SLGridConfig(multipliers=(1.0, 0.0))


def test_arm_label_is_stable_and_distinct() -> None:
    # `:g` drops the trailing zero, so 1.0 -> "atr_1". Task 5's _k_from_arm
    # inverts this, and Task 4's fixtures use these exact strings.
    assert arm_label(1.0) == "atr_1"
    assert arm_label(0.5) == "atr_0.5"
    assert arm_label(2.0) == "atr_2"
    assert arm_label(1.0) != BASELINE_ARM


def test_baseline_levels_long_is_two_percent_below_entry() -> None:
    sl, tp = baseline_levels(100.0, "long", baseline_pct=0.02, tp_r=3.0)
    assert sl == pytest.approx(98.0)
    assert tp == pytest.approx(106.0)


def test_baseline_levels_short_mirrors_long() -> None:
    sl, tp = baseline_levels(100.0, "short", baseline_pct=0.02, tp_r=3.0)
    assert sl == pytest.approx(102.0)
    assert tp == pytest.approx(94.0)


def test_counterfactual_levels_scale_with_atr_and_k() -> None:
    sl, tp = counterfactual_levels(100.0, "long", atr=2.0, k=1.5, tp_r=3.0)
    assert sl == pytest.approx(97.0)  # 100 - 1.5 * 2.0
    assert tp == pytest.approx(109.0)  # 100 + 3.0 * 3.0


def test_counterfactual_tp_tracks_tp_r_times_sl_distance() -> None:
    for tp_r in (1.0, 2.5, 4.0):
        sl, tp = counterfactual_levels(100.0, "short", atr=1.0, k=2.0, tp_r=tp_r)
        sl_dist = abs(100.0 - sl)
        assert abs(100.0 - tp) == pytest.approx(tp_r * sl_dist)


def test_counterfactual_rejects_unknown_direction() -> None:
    with pytest.raises(ValueError, match="direction"):
        counterfactual_levels(100.0, "sideways", atr=1.0, k=1.0, tp_r=3.0)


def test_counterfactual_rejects_non_positive_risk() -> None:
    with pytest.raises(ValueError, match="sl_dist"):
        counterfactual_levels(100.0, "long", atr=0.0, k=1.0, tp_r=3.0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_sl_horizon.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'analytics.sl_horizon'`

- [ ] **Step 3: Write minimal implementation**

Create `analytics/sl_horizon.py`:

```python
"""ST9 / H11 SL-horizon audit — pure library.

Six candle detectors (`doji`, `engulfing`, `hammer_hanging_man`, `inside_bar`,
`morning_evening_star`, `pin_bar`) hard-code ``sl_pct = 0.02`` at every
timeframe, while every other active detector derives a TF-adaptive structural
SL. This module re-resolves the same signals under an ATR-scaled stop grid so a
pre-committed verdict can say whether the family's graveyard is an artifact of a
dimensionally wrong stop.

Pure: no DB, no IO, no network. The DB front door is
``tools/sl_horizon_audit.py``. Design:
``docs/superpowers/specs/2026-07-21-st9-sl-horizon-audit-design.md``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from analytics.signal.outcome_backfill import DEFAULT_MAX_HOLD_BARS

# A-priori and fixed. Brackets the current effective ratio at 1h (~3.6), 4h
# (~1.7) and 1d (~0.6); sits entirely below 15m (~7.5), where every arm is a
# tightening. Never re-centred in response to results.
DEFAULT_MULTIPLIERS: tuple[float, ...] = (0.5, 1.0, 1.5, 2.0, 3.0)

#: Column/arm name for the unmodified flat-2% arm.
BASELINE_ARM = "flat_2pct"

#: The detectors this audit covers.
FAMILY: tuple[str, ...] = (
    "doji",
    "engulfing",
    "hammer_hanging_man",
    "inside_bar",
    "morning_evening_star",
    "pin_bar",
)


def arm_label(k: float) -> str:
    """Stable column name for the ``k × ATR14`` arm."""
    return f"atr_{k:g}"


@dataclass(frozen=True)
class SLGridConfig:
    """A-priori parameters for one audit run. Frozen; never tuned mid-run."""

    multipliers: tuple[float, ...] = DEFAULT_MULTIPLIERS
    baseline_pct: float = 0.02
    max_hold_bars_by_tf: Mapping[str, int] = field(
        default_factory=lambda: dict(DEFAULT_MAX_HOLD_BARS)
    )
    fee_pct: float = 0.0005
    slippage_bps: float = 2.0
    bar: float = 0.05
    alpha: float = 0.05
    min_n: int = 30
    n_boot: int = 2000
    seed: int = 12345

    def __post_init__(self) -> None:
        if not self.multipliers:
            raise ValueError("multipliers must be non-empty")
        if any(k <= 0.0 for k in self.multipliers):
            raise ValueError(f"multipliers must all be > 0, got {self.multipliers}")
        if self.baseline_pct <= 0.0:
            raise ValueError(f"baseline_pct must be > 0, got {self.baseline_pct}")
        if self.min_n < 2:
            raise ValueError(f"min_n must be >= 2, got {self.min_n}")

    @property
    def round_trip_cost_pct(self) -> float:
        """Round-trip cost as a fraction of notional (both legs, fee + slippage)."""
        return 2.0 * self.fee_pct + 2.0 * (self.slippage_bps / 10_000.0)


def levels_from_sl_dist(
    entry: float, direction: str, *, sl_dist: float, tp_r: float
) -> tuple[float, float]:
    """Return ``(sl_price, tp_price)`` for a stop ``sl_dist`` away from ``entry``."""
    if sl_dist <= 0.0:
        raise ValueError(f"sl_dist must be > 0, got {sl_dist}")
    if tp_r <= 0.0:
        raise ValueError(f"tp_r must be > 0, got {tp_r}")
    if direction == "long":
        return entry - sl_dist, entry + tp_r * sl_dist
    if direction == "short":
        return entry + sl_dist, entry - tp_r * sl_dist
    raise ValueError(f"unknown direction: {direction!r}")


def baseline_levels(
    entry: float, direction: str, *, baseline_pct: float, tp_r: float
) -> tuple[float, float]:
    """The unmodified flat-percentage arm the six detectors ship today."""
    return levels_from_sl_dist(
        entry, direction, sl_dist=entry * baseline_pct, tp_r=tp_r
    )


def counterfactual_levels(
    entry: float, direction: str, *, atr: float, k: float, tp_r: float
) -> tuple[float, float]:
    """The ``k × ATR14`` arm. ``tp_r`` is pinned by the caller, never swept."""
    return levels_from_sl_dist(entry, direction, sl_dist=k * atr, tp_r=tp_r)
```

- [ ] **Step 4: Run tests and lint**

Run: `poetry run pytest tests/test_sl_horizon.py -v && make lint-py && make typecheck`
Expected: all tests PASS, ruff clean, mypy clean.

- [ ] **Step 5: Commit**

```bash
git add analytics/sl_horizon.py tests/test_sl_horizon.py
git commit -m "feat(sl-horizon): a-priori config + counterfactual level math (ST9/H11)"
```

---

### Task 2: ATR14 lookup keyed by signal bar

**Files:**

- Modify: `analytics/sl_horizon.py`
- Modify: `tests/test_sl_horizon.py`

**Interfaces:**

- Consumes: `analytics.backtest.engine._compute_atr14(highs, lows, closes, idx) -> float | None`
  — takes `np.float64` arrays and a positional index; returns `None` when `idx < 1` or ATR is zero.
- Produces: `atr_by_open_time(ohlcv, open_times) -> dict[int, float | None]`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_sl_horizon.py`:

```python
import numpy as np
import pandas as pd

from analytics.sl_horizon import atr_by_open_time


def _ramp_ohlcv(n: int = 40, step: float = 1.0) -> pd.DataFrame:
    """Deterministic OHLCV with a constant 2.0-wide bar and a `step` drift."""
    open_times = [1_000 + i * 100 for i in range(n)]
    closes = [100.0 + i * step for i in range(n)]
    return pd.DataFrame(
        {
            "open_time": open_times,
            "open": [c - 0.5 for c in closes],
            "high": [c + 1.0 for c in closes],
            "low": [c - 1.0 for c in closes],
            "close": closes,
            "volume": [10.0] * n,
        }
    )


def test_atr_by_open_time_returns_a_value_per_known_bar() -> None:
    df = _ramp_ohlcv()
    got = atr_by_open_time(df, [1_000 + 20 * 100, 1_000 + 30 * 100])
    assert set(got) == {3_000, 4_000}
    assert all(v is not None and v > 0.0 for v in got.values())


def test_atr_by_open_time_is_none_for_unknown_open_time() -> None:
    df = _ramp_ohlcv()
    assert atr_by_open_time(df, [999_999]) == {999_999: None}


def test_atr_by_open_time_is_none_at_the_first_bar() -> None:
    # _compute_atr14 needs a prior close, so idx 0 has no ATR.
    df = _ramp_ohlcv()
    assert atr_by_open_time(df, [1_000]) == {1_000: None}


def test_atr_by_open_time_matches_the_engine_primitive() -> None:
    from analytics.backtest.engine import _compute_atr14

    df = _ramp_ohlcv()
    idx = 25
    expected = _compute_atr14(
        df["high"].to_numpy(dtype=np.float64),
        df["low"].to_numpy(dtype=np.float64),
        df["close"].to_numpy(dtype=np.float64),
        idx,
    )
    got = atr_by_open_time(df, [int(df["open_time"].iloc[idx])])
    assert got[int(df["open_time"].iloc[idx])] == pytest.approx(expected)


def test_atr_by_open_time_handles_empty_frame() -> None:
    empty = pd.DataFrame(columns=["open_time", "open", "high", "low", "close", "volume"])
    assert atr_by_open_time(empty, [1_000]) == {1_000: None}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_sl_horizon.py -k atr -v`
Expected: FAIL — `ImportError: cannot import name 'atr_by_open_time'`

- [ ] **Step 3: Write minimal implementation**

Add to the imports at the top of `analytics/sl_horizon.py`:

```python
from collections.abc import Iterable, Mapping

import numpy as np
import pandas as pd

from analytics.backtest.engine import _compute_atr14
```

Append to `analytics/sl_horizon.py`:

```python
def atr_by_open_time(
    ohlcv: pd.DataFrame, open_times: Iterable[int]
) -> dict[int, float | None]:
    """ATR14 at each requested signal bar, keyed by that bar's ``open_time``.

    Delegates to the engine's ``_compute_atr14`` so the audit and the live path
    cannot disagree on what ATR14 means. Returns ``None`` for an ``open_time``
    absent from ``ohlcv`` and for the first bar (no prior close for a true
    range) — callers drop those signals rather than substituting a value.
    """
    wanted = [int(t) for t in open_times]
    if ohlcv is None or ohlcv.empty:
        return dict.fromkeys(wanted)

    highs = ohlcv["high"].to_numpy(dtype=np.float64)
    lows = ohlcv["low"].to_numpy(dtype=np.float64)
    closes = ohlcv["close"].to_numpy(dtype=np.float64)
    position = {
        int(t): i for i, t in enumerate(ohlcv["open_time"].to_numpy())
    }

    out: dict[int, float | None] = {}
    for t in wanted:
        idx = position.get(t)
        out[t] = None if idx is None else _compute_atr14(highs, lows, closes, idx)
    return out
```

- [ ] **Step 4: Run tests and lint**

Run: `poetry run pytest tests/test_sl_horizon.py -v && make lint-py && make typecheck`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add analytics/sl_horizon.py tests/test_sl_horizon.py
git commit -m "feat(sl-horizon): ATR14 lookup keyed by signal bar open_time"
```

---

### Task 3: Arm resolution with honest costs

**Files:**

- Modify: `analytics/sl_horizon.py`
- Modify: `tests/test_sl_horizon.py`

**Interfaces:**

- Consumes:
  - `analytics.exits.policies.fixed(*, tp_r: float, max_hold_bars: int) -> ExitPolicyConfig`
  - `analytics.exits.replay.replay_exits(highs, lows, closes, *, direction, entry, sl_price, policy) -> ExitOutcome`
    where `ExitOutcome` has fields `outcome: str`, `realized_r: float`, `exit_bar: int`,
    `partial_taken: bool`. Raises `ValueError` on zero risk or an empty window.
- Produces: `ENTRY_CONVENTIONS`, `window_for_signal`, `ArmResult`, `resolve_arm`.

**Why `convention` exists:** see "The historical defect this plan must not repeat"
above. The backtest engine enters at the open of `sig_idx + 1` and scans forward
*including* that bar; the live ledger uses a stored entry price with a window strictly
after the signal candle. Both must be expressible or the fidelity checks in Task 6 fail.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_sl_horizon.py`:

```python
from analytics.sl_horizon import ArmResult, resolve_arm, window_for_signal


def test_window_engine_convention_starts_at_the_bar_after_signal() -> None:
    df = _ramp_ohlcv(n=20)
    entry, highs, lows, closes = window_for_signal(
        df, sig_idx=5, convention="engine", max_hold_bars=4
    )
    # Engine enters at the OPEN of sig_idx + 1 and scans from that bar inclusive.
    assert entry == pytest.approx(float(df["open"].iloc[6]))
    assert len(highs) == 4
    assert highs[0] == pytest.approx(float(df["high"].iloc[6]))


def test_window_live_convention_starts_strictly_after_signal() -> None:
    df = _ramp_ohlcv(n=20)
    entry, highs, lows, closes = window_for_signal(
        df, sig_idx=5, convention="live", max_hold_bars=4
    )
    # Live uses the signal candle's close as entry, window strictly after it.
    assert entry == pytest.approx(float(df["close"].iloc[5]))
    assert len(highs) == 4
    assert highs[0] == pytest.approx(float(df["high"].iloc[6]))


def test_window_returns_empty_when_no_forward_bars() -> None:
    df = _ramp_ohlcv(n=8)
    _, highs, _, _ = window_for_signal(
        df, sig_idx=7, convention="engine", max_hold_bars=4
    )
    assert len(highs) == 0


def test_window_rejects_unknown_convention() -> None:
    df = _ramp_ohlcv(n=20)
    with pytest.raises(ValueError, match="convention"):
        window_for_signal(df, sig_idx=5, convention="nope", max_hold_bars=4)


def _flat_window(n: int, price: float = 100.0) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """A window that never moves — guarantees an expiry, not a win or loss."""
    arr = np.full(n, price, dtype=np.float64)
    return arr.copy(), arr.copy(), arr.copy()


def test_resolve_arm_deducts_cost_from_realized_r() -> None:
    highs, lows, closes = _flat_window(10)
    res = resolve_arm(
        highs,
        lows,
        closes,
        direction="long",
        entry=100.0,
        sl_price=98.0,
        tp_r=3.0,
        max_hold_bars=10,
        round_trip_cost_pct=0.0014,
        funding_r=0.0,
    )
    assert isinstance(res, ArmResult)
    # risk = 2.0; cost = 0.0014 * 100 / 2.0 = 0.07R
    assert res.cost_r == pytest.approx(0.07)
    assert res.net_r == pytest.approx(res.realized_r - 0.07)
    assert res.net_r < res.realized_r


def test_resolve_arm_zero_cost_leaves_realized_r_untouched() -> None:
    highs, lows, closes = _flat_window(10)
    res = resolve_arm(
        highs,
        lows,
        closes,
        direction="long",
        entry=100.0,
        sl_price=98.0,
        tp_r=3.0,
        max_hold_bars=10,
        round_trip_cost_pct=0.0,
        funding_r=0.0,
    )
    assert res.cost_r == pytest.approx(0.0)
    assert res.net_r == pytest.approx(res.realized_r)


def test_resolve_arm_cost_in_r_grows_as_the_stop_tightens() -> None:
    highs, lows, closes = _flat_window(10)
    wide = resolve_arm(
        highs, lows, closes, direction="long", entry=100.0, sl_price=98.0,
        tp_r=3.0, max_hold_bars=10, round_trip_cost_pct=0.0014, funding_r=0.0,
    )
    tight = resolve_arm(
        highs, lows, closes, direction="long", entry=100.0, sl_price=99.5,
        tp_r=3.0, max_hold_bars=10, round_trip_cost_pct=0.0014, funding_r=0.0,
    )
    # Same cash cost, four times the risk denominator -> four times the R cost.
    assert tight.cost_r > wide.cost_r
    assert tight.cost_r == pytest.approx(4.0 * wide.cost_r)


def test_resolve_arm_subtracts_funding() -> None:
    highs, lows, closes = _flat_window(10)
    res = resolve_arm(
        highs, lows, closes, direction="long", entry=100.0, sl_price=98.0,
        tp_r=3.0, max_hold_bars=10, round_trip_cost_pct=0.0, funding_r=0.03,
    )
    assert res.net_r == pytest.approx(res.realized_r - 0.03)


def test_resolve_arm_returns_none_on_empty_window() -> None:
    empty = np.array([], dtype=np.float64)
    assert (
        resolve_arm(
            empty, empty, empty, direction="long", entry=100.0, sl_price=98.0,
            tp_r=3.0, max_hold_bars=10, round_trip_cost_pct=0.0, funding_r=0.0,
        )
        is None
    )


def test_resolve_arm_returns_none_on_zero_risk() -> None:
    highs, lows, closes = _flat_window(10)
    assert (
        resolve_arm(
            highs, lows, closes, direction="long", entry=100.0, sl_price=100.0,
            tp_r=3.0, max_hold_bars=10, round_trip_cost_pct=0.0, funding_r=0.0,
        )
        is None
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_sl_horizon.py -k "window or resolve_arm" -v`
Expected: FAIL — `ImportError: cannot import name 'ArmResult'`

- [ ] **Step 3: Write minimal implementation**

Add to the imports at the top of `analytics/sl_horizon.py`:

```python
from typing import Literal

import numpy.typing as npt

from analytics.exits.policies import fixed as fixed_policy
from analytics.exits.replay import replay_exits
```

Append to `analytics/sl_horizon.py`:

```python
EntryConvention = Literal["engine", "live"]

#: Entry/window conventions. These differ between substrates and the difference
#: is load-bearing — see the fidelity checks in `tools/sl_horizon_audit.py`.
ENTRY_CONVENTIONS: tuple[str, ...] = ("engine", "live")


def window_for_signal(
    ohlcv: pd.DataFrame,
    *,
    sig_idx: int,
    convention: str,
    max_hold_bars: int,
) -> tuple[
    float,
    npt.NDArray[np.float64],
    npt.NDArray[np.float64],
    npt.NDArray[np.float64],
]:
    """Return ``(entry_price, highs, lows, closes)`` for one signal's forward window.

    ``convention``:

    * ``"engine"`` — mirrors ``analytics/backtest/engine.py``: entry is the OPEN
      of bar ``sig_idx + 1`` and the scan window starts at that same bar, so a
      trade can stop out on its own entry bar.
    * ``"live"`` — mirrors ``analytics/signal/outcome_backfill.py``: entry is the
      CLOSE of the signal bar and the window is the bars strictly after it.

    The window is truncated to ``max_hold_bars``; expiry then falls out of window
    exhaustion, so no explicit time-stop policy is needed.
    """
    if convention not in ENTRY_CONVENTIONS:
        raise ValueError(
            f"unknown convention {convention!r}; expected one of {ENTRY_CONVENTIONS}"
        )

    highs = ohlcv["high"].to_numpy(dtype=np.float64)
    lows = ohlcv["low"].to_numpy(dtype=np.float64)
    closes = ohlcv["close"].to_numpy(dtype=np.float64)
    opens = ohlcv["open"].to_numpy(dtype=np.float64)

    start = sig_idx + 1
    if convention == "engine":
        entry = float(opens[start]) if start < len(opens) else float("nan")
    else:
        entry = float(closes[sig_idx])

    stop = start + max_hold_bars
    return entry, highs[start:stop], lows[start:stop], closes[start:stop]


@dataclass(frozen=True)
class ArmResult:
    """One signal resolved under one arm, net of costs."""

    outcome: str
    realized_r: float
    exit_bar: int
    sl_dist_pct: float
    cost_r: float
    funding_r: float
    net_r: float


def resolve_arm(
    highs: npt.NDArray[np.float64],
    lows: npt.NDArray[np.float64],
    closes: npt.NDArray[np.float64],
    *,
    direction: str,
    entry: float,
    sl_price: float,
    tp_r: float,
    max_hold_bars: int,
    round_trip_cost_pct: float,
    funding_r: float,
) -> ArmResult | None:
    """Resolve one signal under one arm and net out costs.

    Returns ``None`` when the signal is unresolvable (empty forward window or
    zero risk) — the caller drops it from **every** arm so the paired comparison
    stays row-aligned.

    ``net_r = realized_r − cost_r − funding_r``, matching the P0b honest-cost
    convention in ``analytics/signal/outcome_backfill.py``. ``cost_r`` converts a
    cash cost into R by dividing by the risk, so a tighter stop is charged more
    R for the same trade — which is exactly the effect this audit must not hide.
    """
    risk = abs(entry - sl_price)
    if risk <= 0.0 or len(highs) == 0 or not np.isfinite(entry):
        return None

    try:
        outcome = replay_exits(
            highs,
            lows,
            closes,
            direction=direction,
            entry=entry,
            sl_price=sl_price,
            policy=fixed_policy(tp_r=tp_r, max_hold_bars=max_hold_bars),
        )
    except ValueError:
        return None

    cost_r = round_trip_cost_pct * entry / risk
    return ArmResult(
        outcome=outcome.outcome,
        realized_r=outcome.realized_r,
        exit_bar=outcome.exit_bar,
        sl_dist_pct=risk / entry,
        cost_r=cost_r,
        funding_r=funding_r,
        net_r=outcome.realized_r - cost_r - funding_r,
    )
```

- [ ] **Step 4: Run tests and lint**

Run: `poetry run pytest tests/test_sl_horizon.py -v && make lint-py && make typecheck`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add analytics/sl_horizon.py tests/test_sl_horizon.py
git commit -m "feat(sl-horizon): arm resolution via exits.replay with honest costs"
```

---

### Task 4: Paired table and descriptive horizon

**Files:**

- Modify: `analytics/sl_horizon.py`
- Modify: `tests/test_sl_horizon.py`

**Interfaces:**

- Consumes: `BASELINE_ARM`, `arm_label` (Task 1); `ArmResult` (Task 3).
- Produces: `SIGNAL_KEY`, `build_paired_table`, `describe_horizon`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_sl_horizon.py`:

```python
from analytics.sl_horizon import SIGNAL_KEY, build_paired_table, describe_horizon


def _arm_rows() -> pd.DataFrame:
    """Two signals × two arms, plus one signal missing an arm."""
    return pd.DataFrame(
        [
            # signal A — complete
            {"symbol": "BTCUSDT", "tf": "1h", "strategy": "pin_bar",
             "direction": "long", "open_time": 1, "arm": "flat_2pct",
             "net_r": -1.0, "outcome": "loss", "exit_bar": 3, "sl_dist_pct": 0.02},
            {"symbol": "BTCUSDT", "tf": "1h", "strategy": "pin_bar",
             "direction": "long", "open_time": 1, "arm": "atr_1",
             "net_r": 0.5, "outcome": "expired", "exit_bar": 9, "sl_dist_pct": 0.01},
            # signal B — complete
            {"symbol": "BTCUSDT", "tf": "1h", "strategy": "pin_bar",
             "direction": "long", "open_time": 2, "arm": "flat_2pct",
             "net_r": 3.0, "outcome": "win", "exit_bar": 5, "sl_dist_pct": 0.02},
            {"symbol": "BTCUSDT", "tf": "1h", "strategy": "pin_bar",
             "direction": "long", "open_time": 2, "arm": "atr_1",
             "net_r": 1.0, "outcome": "win", "exit_bar": 2, "sl_dist_pct": 0.01},
            # signal C — MISSING the atr_1 arm, must be dropped entirely
            {"symbol": "BTCUSDT", "tf": "1h", "strategy": "pin_bar",
             "direction": "long", "open_time": 3, "arm": "flat_2pct",
             "net_r": -1.0, "outcome": "loss", "exit_bar": 1, "sl_dist_pct": 0.02},
        ]
    )


def test_signal_key_is_the_documented_tuple() -> None:
    assert SIGNAL_KEY == ["symbol", "tf", "strategy", "direction", "open_time"]


def test_build_paired_table_pivots_one_row_per_signal() -> None:
    wide = build_paired_table(_arm_rows(), arms=["flat_2pct", "atr_1"])
    assert len(wide) == 2
    assert set(wide["open_time"]) == {1, 2}


def test_build_paired_table_drops_signals_missing_any_arm() -> None:
    wide = build_paired_table(_arm_rows(), arms=["flat_2pct", "atr_1"])
    # Signal C resolved under the baseline only; dropping it (rather than
    # zero-filling) is what keeps the paired difference honest.
    assert 3 not in set(wide["open_time"])


def test_build_paired_table_preserves_net_r_per_arm() -> None:
    wide = build_paired_table(_arm_rows(), arms=["flat_2pct", "atr_1"])
    row = wide[wide["open_time"] == 1].iloc[0]
    assert row["flat_2pct"] == pytest.approx(-1.0)
    assert row["atr_1"] == pytest.approx(0.5)


def test_build_paired_table_empty_input_returns_empty_frame() -> None:
    empty = pd.DataFrame(
        columns=[*SIGNAL_KEY, "arm", "net_r", "outcome", "exit_bar", "sl_dist_pct"]
    )
    assert build_paired_table(empty, arms=["flat_2pct"]).empty


def test_describe_horizon_reports_expiry_rate_and_median_bars() -> None:
    got = describe_horizon(_arm_rows(), arm="flat_2pct")
    row = got.iloc[0]
    assert row["strategy"] == "pin_bar"
    assert row["tf"] == "1h"
    assert row["n"] == 3
    # one win, two losses, no expiries in the baseline arm
    assert row["expiry_rate"] == pytest.approx(0.0)
    assert row["median_bars"] == pytest.approx(3.0)


def test_describe_horizon_computes_sl_in_atr_units() -> None:
    rows = _arm_rows()
    rows["atr_pct"] = 0.005  # ATR is 0.5% of price
    got = describe_horizon(rows, arm="flat_2pct")
    # 2% stop / 0.5% ATR = 4 ATR-widths
    assert got.iloc[0]["median_sl_atr"] == pytest.approx(4.0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_sl_horizon.py -k "paired or horizon or signal_key" -v`
Expected: FAIL — `ImportError: cannot import name 'SIGNAL_KEY'`

- [ ] **Step 3: Write minimal implementation**

Append to `analytics/sl_horizon.py`:

```python
#: Identity of one signal. Every arm resolves the same set of these.
SIGNAL_KEY: list[str] = ["symbol", "tf", "strategy", "direction", "open_time"]


def build_paired_table(arm_rows: pd.DataFrame, *, arms: Sequence[str]) -> pd.DataFrame:
    """Pivot long arm rows to one row per signal with one ``net_r`` column per arm.

    A signal that failed to resolve under **any** arm is dropped from **all**
    arms. Zero-filling instead would silently credit the missing arm with a
    flat outcome and bias the paired difference.
    """
    if arm_rows.empty:
        return pd.DataFrame(columns=[*SIGNAL_KEY, *arms])

    wide = arm_rows.pivot_table(
        index=SIGNAL_KEY, columns="arm", values="net_r", aggfunc="first"
    )
    missing = [a for a in arms if a not in wide.columns]
    for arm in missing:
        wide[arm] = np.nan
    wide = wide[list(arms)].dropna(how="any")
    return wide.reset_index()


def describe_horizon(arm_rows: pd.DataFrame, *, arm: str = BASELINE_ARM) -> pd.DataFrame:
    """Per (strategy, tf) descriptive horizon table for one arm.

    Columns: ``strategy``, ``tf``, ``n``, ``avg_r``, ``median_bars``,
    ``expiry_rate``, ``median_sl_pct``, and ``median_sl_atr`` when the caller
    supplied an ``atr_pct`` column (ATR14 as a fraction of entry price).
    """
    subset = arm_rows[arm_rows["arm"] == arm]
    if subset.empty:
        return pd.DataFrame(
            columns=[
                "strategy", "tf", "n", "avg_r", "median_bars",
                "expiry_rate", "median_sl_pct", "median_sl_atr",
            ]
        )

    records: list[dict[str, object]] = []
    for (strategy, tf), grp in subset.groupby(["strategy", "tf"], sort=True):
        sl_atr = float("nan")
        if "atr_pct" in grp.columns:
            ratio = grp["sl_dist_pct"] / grp["atr_pct"]
            sl_atr = float(ratio.median())
        records.append(
            {
                "strategy": strategy,
                "tf": tf,
                "n": int(len(grp)),
                "avg_r": float(grp["net_r"].mean()),
                "median_bars": float(grp["exit_bar"].median()),
                "expiry_rate": float((grp["outcome"] == "expired").mean()),
                "median_sl_pct": float(grp["sl_dist_pct"].median()),
                "median_sl_atr": sl_atr,
            }
        )
    return pd.DataFrame(records)
```

Add `Sequence` to the `collections.abc` import line at the top:

```python
from collections.abc import Iterable, Mapping, Sequence
```

- [ ] **Step 4: Run tests and lint**

Run: `poetry run pytest tests/test_sl_horizon.py -v && make lint-py && make typecheck`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add analytics/sl_horizon.py tests/test_sl_horizon.py
git commit -m "feat(sl-horizon): paired arm table + descriptive horizon rollup"
```

---

### Task 5: The pre-committed verdict rule

**Files:**

- Modify: `analytics/sl_horizon.py`
- Modify: `tests/test_sl_horizon.py`

**Interfaces:**

- Consumes:
  - `analytics.audit_guard.AuditCell(label: str, supp_r: Sequence[float], kept_r: Sequence[float] = [])`
  - `analytics.audit_guard.evaluate_audit_cells(cells, *, bar, alpha, min_n, haircut_method, n_boot, boot_method, seed, enable_concentrate) -> list[CellVerdict]`
    — `CellVerdict` has `decision`, `n_supp`, `supp_avg`, `ci_lo`, `ci_hi`, `adj_pvalue`,
    `n_tests`, `reasons`. `decision == "ENABLE"` means the CI cleared `+bar`.
  - `analytics.research_guards.deflated_sharpe_ratio(sr, n_obs, *, trial_srs=None, ...) -> float`
  - `analytics.research_guards.cscv_pbo(perf_matrix, n_splits=14, metric=None) -> PBOResult`
    (`PBOResult` exposes `.pbo`)
- Produces: `SLVerdict`, `DECISION_SUSPECT`, `DECISION_CONFIRMED_BAD`,
  `DECISION_NO_DIFFERENCE`, `DECISION_INSUFFICIENT`, `evaluate_sl_grid`.

**The rule (from spec §6) — do not deviate:**

- One `evaluate_audit_cells` call over **every** `(strategy × TF × k)` cell in the run,
  so the Holm family is shared across the whole substrate. Never call it per cell.
- Candidate `k` = those whose `CellVerdict.decision == "ENABLE"` (CI cleared `+bar`).
- Winning `k` = largest `supp_avg` among candidates; **ties break toward the larger `k`**.
- `SUSPECT` also requires `dsr >= 0.95` and `pbo <= 0.5` over the k-grid.
- `INSUFFICIENT` is checked first, on `n < min_n`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_sl_horizon.py`:

```python
from analytics.sl_horizon import (
    DECISION_CONFIRMED_BAD,
    DECISION_INSUFFICIENT,
    DECISION_NO_DIFFERENCE,
    DECISION_SUSPECT,
    SLVerdict,
    evaluate_sl_grid,
)


def _paired(
    n: int, baseline: float, lifts: dict[str, float], *, noise: float = 0.01
) -> pd.DataFrame:
    """A paired table with a controlled per-arm lift over the baseline."""
    rng = np.random.default_rng(7)
    data: dict[str, object] = {
        "symbol": ["BTCUSDT"] * n,
        "tf": ["1h"] * n,
        "strategy": ["pin_bar"] * n,
        "direction": ["long"] * n,
        "open_time": list(range(n)),
        BASELINE_ARM: rng.normal(baseline, noise, n),
    }
    for arm, lift in lifts.items():
        data[arm] = np.asarray(data[BASELINE_ARM]) + rng.normal(lift, noise, n)
    return pd.DataFrame(data)


def test_insufficient_when_below_min_n() -> None:
    wide = _paired(10, -0.2, {"atr_1": 0.5})
    got = evaluate_sl_grid(wide, arms=["atr_1"], cfg=SLGridConfig(min_n=30))
    assert len(got) == 1
    assert got[0].decision == DECISION_INSUFFICIENT
    assert got[0].n == 10


def test_suspect_when_an_arm_clearly_beats_the_baseline() -> None:
    wide = _paired(400, -0.2, {"atr_1": 0.5})
    got = evaluate_sl_grid(wide, arms=["atr_1"], cfg=SLGridConfig())
    assert got[0].decision == DECISION_SUSPECT
    assert got[0].best_k == pytest.approx(1.0)
    assert got[0].best_lift is not None and got[0].best_lift > 0.05


def test_confirmed_bad_when_no_arm_helps_and_baseline_is_negative() -> None:
    wide = _paired(400, -0.2, {"atr_1": 0.0})
    got = evaluate_sl_grid(wide, arms=["atr_1"], cfg=SLGridConfig())
    assert got[0].decision == DECISION_CONFIRMED_BAD


def test_no_difference_when_no_arm_helps_but_baseline_is_positive() -> None:
    wide = _paired(400, 0.3, {"atr_1": 0.0})
    got = evaluate_sl_grid(wide, arms=["atr_1"], cfg=SLGridConfig())
    assert got[0].decision == DECISION_NO_DIFFERENCE


def test_ties_break_toward_the_larger_k() -> None:
    # Two arms with an identical lift: the wider, cheaper-to-trade stop wins.
    wide = _paired(400, -0.2, {"atr_1": 0.5})
    wide["atr_2"] = wide["atr_1"]
    got = evaluate_sl_grid(wide, arms=["atr_1", "atr_2"], cfg=SLGridConfig())
    assert got[0].best_k == pytest.approx(2.0)


def test_verdicts_are_returned_per_strategy_tf_cell() -> None:
    a = _paired(400, -0.2, {"atr_1": 0.5})
    b = _paired(400, -0.2, {"atr_1": 0.0})
    b["tf"] = "4h"
    got = evaluate_sl_grid(pd.concat([a, b]), arms=["atr_1"], cfg=SLGridConfig())
    assert {v.tf for v in got} == {"1h", "4h"}
    assert all(isinstance(v, SLVerdict) for v in got)


def test_empty_input_returns_no_verdicts() -> None:
    empty = pd.DataFrame(columns=[*SIGNAL_KEY, BASELINE_ARM, "atr_1"])
    assert evaluate_sl_grid(empty, arms=["atr_1"], cfg=SLGridConfig()) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_sl_horizon.py -k "suspect or confirmed or no_difference or insufficient or ties" -v`
Expected: FAIL — `ImportError: cannot import name 'DECISION_SUSPECT'`

- [ ] **Step 3: Write minimal implementation**

Add to the imports at the top of `analytics/sl_horizon.py`:

```python
from analytics.audit_guard import AuditCell, CellVerdict, evaluate_audit_cells
from analytics.research_guards import cscv_pbo, deflated_sharpe_ratio
```

Append to `analytics/sl_horizon.py`:

```python
DECISION_SUSPECT = "SUSPECT"
DECISION_CONFIRMED_BAD = "CONFIRMED-BAD"
DECISION_NO_DIFFERENCE = "NO-DIFFERENCE"
DECISION_INSUFFICIENT = "INSUFFICIENT"

_DSR_FLOOR = 0.95
_PBO_CEILING = 0.5


@dataclass(frozen=True)
class SLVerdict:
    """Pre-committed verdict for one (strategy × TF) cell."""

    strategy: str
    tf: str
    decision: str
    n: int
    baseline_avg_r: float
    best_k: float | None
    best_lift: float | None
    ci_lo: float | None
    ci_hi: float | None
    adj_pvalue: float | None
    dsr: float | None
    pbo: float | None
    reasons: list[str]


def _k_from_arm(arm: str) -> float:
    """Inverse of :func:`arm_label`."""
    return float(arm.removeprefix("atr_"))


def _sharpe(arr: npt.NDArray[np.float64]) -> float:
    if arr.size < 2:
        return 0.0
    sd = float(np.std(arr, ddof=1))
    return 0.0 if sd == 0.0 else float(np.mean(arr)) / sd


def evaluate_sl_grid(
    paired: pd.DataFrame, *, arms: Sequence[str], cfg: SLGridConfig
) -> list[SLVerdict]:
    """Pre-committed SUSPECT / CONFIRMED-BAD / NO-DIFFERENCE / INSUFFICIENT verdicts.

    The statistic is the **paired** per-signal lift ``net_r[arm] − net_r[baseline]``.
    Because both arms ran on the identical signal set, this is far better powered
    than a two-sample comparison and is immune to the signal population's own
    quality. Feeding the difference series to ``evaluate_audit_cells`` as
    ``supp_r`` turns its ±bar + Holm machinery into a paired test with no change
    to ``analytics/audit_guard.py``.

    One ``evaluate_audit_cells`` call covers every (strategy × TF × k) cell in
    the run, so the Holm family is shared across the whole substrate — one family
    per substrate, never pooled across substrates.
    """
    if paired.empty:
        return []

    groups = list(paired.groupby(["strategy", "tf"], sort=True))

    # Build one AuditCell per (strategy, tf, arm); the whole list is one family.
    cells: list[AuditCell] = []
    index: list[tuple[int, str]] = []  # (group position, arm)
    for gi, (_, grp) in enumerate(groups):
        for arm in arms:
            diff = (grp[arm] - grp[BASELINE_ARM]).to_numpy(dtype=np.float64)
            cells.append(AuditCell(label=f"g{gi}|{arm}", supp_r=diff))
            index.append((gi, arm))

    verdicts_flat = evaluate_audit_cells(
        cells,
        bar=cfg.bar,
        alpha=cfg.alpha,
        min_n=cfg.min_n,
        n_boot=cfg.n_boot,
        seed=cfg.seed,
        enable_concentrate=False,
    )

    by_group: dict[int, list[tuple[str, CellVerdict]]] = {}
    for (gi, arm), cv in zip(index, verdicts_flat, strict=True):
        by_group.setdefault(gi, []).append((arm, cv))

    out: list[SLVerdict] = []
    for gi, ((strategy, tf), grp) in enumerate(groups):
        n = int(len(grp))
        baseline_avg = float(grp[BASELINE_ARM].mean())
        reasons: list[str] = []

        if n < cfg.min_n:
            out.append(
                SLVerdict(
                    strategy=str(strategy), tf=str(tf),
                    decision=DECISION_INSUFFICIENT, n=n,
                    baseline_avg_r=baseline_avg, best_k=None, best_lift=None,
                    ci_lo=None, ci_hi=None, adj_pvalue=None, dsr=None, pbo=None,
                    reasons=[f"n={n} < min_n={cfg.min_n}"],
                )
            )
            continue

        # Candidates: arms whose CI cleared +bar (audit_guard's ENABLE branch).
        candidates = [
            (arm, cv) for arm, cv in by_group[gi] if cv.decision == "ENABLE"
        ]

        if not candidates:
            decision = (
                DECISION_CONFIRMED_BAD if baseline_avg <= 0.0 else DECISION_NO_DIFFERENCE
            )
            reasons.append("no arm cleared the +bar CI test")
            reasons.append(f"baseline avg_r={baseline_avg:.4f}")
            out.append(
                SLVerdict(
                    strategy=str(strategy), tf=str(tf), decision=decision, n=n,
                    baseline_avg_r=baseline_avg, best_k=None, best_lift=None,
                    ci_lo=None, ci_hi=None, adj_pvalue=None, dsr=None, pbo=None,
                    reasons=reasons,
                )
            )
            continue

        # Winning k: largest mean lift; ties break toward the LARGER k.
        best_arm, best_cv = max(
            candidates,
            key=lambda pair: (float(pair[1].supp_avg or 0.0), _k_from_arm(pair[0])),
        )

        # DSR / PBO over the k-grid family for this cell.
        diffs = {arm: (grp[arm] - grp[BASELINE_ARM]).to_numpy(dtype=np.float64) for arm in arms}
        trial_srs = [_sharpe(v) for v in diffs.values()]
        dsr = deflated_sharpe_ratio(
            _sharpe(diffs[best_arm]), n, trial_srs=trial_srs
        )
        pbo: float | None
        if len(arms) < 2:
            pbo = None
            reasons.append("PBO skipped — needs >= 2 arms")
        else:
            matrix = np.column_stack([diffs[a] for a in arms])
            pbo = float(cscv_pbo(matrix).pbo)

        gates_ok = dsr >= _DSR_FLOOR and (pbo is None or pbo <= _PBO_CEILING)
        decision = DECISION_SUSPECT if gates_ok else DECISION_NO_DIFFERENCE
        if not gates_ok:
            reasons.append(
                f"lift cleared the bar but overfit gates failed "
                f"(dsr={dsr:.3f} < {_DSR_FLOOR} or pbo={pbo} > {_PBO_CEILING})"
            )

        out.append(
            SLVerdict(
                strategy=str(strategy), tf=str(tf), decision=decision, n=n,
                baseline_avg_r=baseline_avg,
                best_k=_k_from_arm(best_arm),
                best_lift=float(best_cv.supp_avg or 0.0),
                ci_lo=best_cv.ci_lo,
                ci_hi=best_cv.ci_hi,
                adj_pvalue=best_cv.adj_pvalue,
                dsr=dsr, pbo=pbo, reasons=reasons,
            )
        )
    return out
```

- [ ] **Step 4: Run tests and lint**

Run: `poetry run pytest tests/test_sl_horizon.py -v && make lint-py && make typecheck`
Expected: all PASS.

If `test_suspect_when_an_arm_clearly_beats_the_baseline` fails on the PBO gate because a
single-arm grid cannot form a CSCV matrix, confirm the `len(arms) < 2` branch is being
taken — do **not** relax `_DSR_FLOOR` or `_PBO_CEILING` to make a test pass.

- [ ] **Step 5: Commit**

```bash
git add analytics/sl_horizon.py tests/test_sl_horizon.py
git commit -m "feat(sl-horizon): pre-committed paired-lift verdict rule"
```

---

### Task 6: DB loaders and the fidelity gate

**Files:**

- Create: `tools/sl_horizon_audit.py`
- Create: `tests/test_sl_horizon_audit.py`

**Interfaces:**

- Consumes:
  - `analytics.store.DEFAULT_DB_PATH`, `analytics.store.market_data.get_ohlcv(conn, symbol, timeframe, start, end)`
  - `analytics.universe.load_universe(path="config/universe.toml") -> list[str]`
  - `analytics.strategies._registry.DETECTOR_REGISTRY` — `dict[str, Callable[[pd.DataFrame], pd.DataFrame]]`,
    keyed by strategy name; each detector returns a DataFrame with `SIGNAL_COLUMNS =
    ["open_time", "direction", "reason", "sl_price", "context", "low_volume", "tp_price"]`
  - `analytics.signal_config.load_signal_config(path) -> SignalWatchConfig`
  - `analytics.signal.resolvers._resolve_tp_r(strategy_params, strategy, symbol, tf, global_tp_r, direction)`
  - Everything from `analytics.sl_horizon` (Tasks 1–5).
- Produces: `TF_MS`, `load_backtest_signals`, `load_live_signals`, `resolve_all_arms`,
  `FidelityReport`, `check_fidelity`.

**The fidelity gate (spec §7) is the acceptance test for this whole audit.** Tolerance,
fixed in advance: per (strategy × TF), `|avg_r_replayed − avg_r_stored| <= 0.02` **and**
matched-trade outcome agreement `>= 0.95`. If it fails with a *systematic* offset, the
first thing to check is the entry convention (see the top of this plan), not the
hypothesis.

- [ ] **Step 1: Write the failing test**

Create `tests/test_sl_horizon_audit.py`:

```python
"""Tests for the ST9/H11 SL-horizon audit driver."""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from analytics.store.schema import init_schema  # noqa: E402
from tools.sl_horizon_audit import (  # noqa: E402
    TF_MS,
    FidelityReport,
    check_fidelity,
    load_live_signals,
)


@pytest.fixture()
def conn() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect(":memory:")
    init_schema(c)
    return c


def test_tf_ms_covers_the_four_audited_timeframes() -> None:
    assert TF_MS["15m"] == 15 * 60_000
    assert TF_MS["1h"] == 60 * 60_000
    assert TF_MS["4h"] == 4 * 60 * 60_000
    assert TF_MS["1d"] == 24 * 60 * 60_000


def test_load_live_signals_returns_only_family_rows_with_resolved_outcomes(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    conn.execute(
        """
        INSERT INTO signal_alert_outcomes
            (signal_id, symbol, tf, strategy, direction, fired_at_ms,
             candle_ts_ms, entry_price, sl_price, tp_price, rr_ratio,
             confidence_at_fire, tags, outcome, outcome_r, outcome_filled_at_ms)
        VALUES
            ('a', 'BTCUSDT', '1h', 'pin_bar',   'long', 1, 1000, 100.0, 98.0, 106.0,
             3.0, 3, NULL, 'win',  3.0, 2000),
            ('b', 'BTCUSDT', '1h', 'fvg',       'long', 1, 1000, 100.0, 98.0, 106.0,
             3.0, 3, NULL, 'win',  3.0, 2000),
            ('c', 'BTCUSDT', '1h', 'engulfing', 'long', 1, 1000, 100.0, 98.0, 106.0,
             3.0, 3, NULL, 'open', NULL, NULL)
        """
    )
    got = load_live_signals(conn)
    assert list(got["signal_id"]) == ["a"]


def test_check_fidelity_passes_when_replay_matches_stored() -> None:
    replayed = pd.DataFrame(
        {
            "strategy": ["pin_bar"] * 4,
            "tf": ["1h"] * 4,
            "key": [1, 2, 3, 4],
            "net_r": [1.0, -1.0, 1.0, -1.0],
            "outcome": ["win", "loss", "win", "loss"],
        }
    )
    stored = replayed.rename(columns={"net_r": "stored_r", "outcome": "stored_outcome"})
    report = check_fidelity(replayed, stored, tolerance_r=0.02, min_agreement=0.95)
    assert isinstance(report, FidelityReport)
    assert report.passed is True


def test_check_fidelity_fails_on_a_systematic_offset() -> None:
    replayed = pd.DataFrame(
        {
            "strategy": ["pin_bar"] * 4,
            "tf": ["1h"] * 4,
            "key": [1, 2, 3, 4],
            "net_r": [1.5, -0.5, 1.5, -0.5],
            "outcome": ["win", "loss", "win", "loss"],
        }
    )
    stored = pd.DataFrame(
        {
            "strategy": ["pin_bar"] * 4,
            "tf": ["1h"] * 4,
            "key": [1, 2, 3, 4],
            "stored_r": [1.0, -1.0, 1.0, -1.0],
            "stored_outcome": ["win", "loss", "win", "loss"],
        }
    )
    report = check_fidelity(replayed, stored, tolerance_r=0.02, min_agreement=0.95)
    assert report.passed is False
    assert any("avg_r" in r for r in report.reasons)


def test_check_fidelity_fails_on_outcome_disagreement() -> None:
    replayed = pd.DataFrame(
        {
            "strategy": ["pin_bar"] * 4,
            "tf": ["1h"] * 4,
            "key": [1, 2, 3, 4],
            "net_r": [1.0, -1.0, 1.0, -1.0],
            "outcome": ["win", "win", "loss", "loss"],
        }
    )
    stored = pd.DataFrame(
        {
            "strategy": ["pin_bar"] * 4,
            "tf": ["1h"] * 4,
            "key": [1, 2, 3, 4],
            "stored_r": [1.0, -1.0, 1.0, -1.0],
            "stored_outcome": ["win", "loss", "win", "loss"],
        }
    )
    report = check_fidelity(replayed, stored, tolerance_r=0.02, min_agreement=0.95)
    assert report.passed is False
    assert any("agreement" in r for r in report.reasons)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_sl_horizon_audit.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.sl_horizon_audit'`

- [ ] **Step 3: Write minimal implementation**

Create `tools/sl_horizon_audit.py`:

```python
#!/usr/bin/env python
"""ST9 / H11 SL-horizon audit (read-only).

Six candle detectors hard-code ``sl_pct = 0.02`` at every timeframe. This tool
re-resolves the same signals under an a-priori ATR-multiplier grid and emits a
pre-committed SUSPECT / CONFIRMED-BAD / NO-DIFFERENCE / INSUFFICIENT verdict per
(strategy x timeframe), via :mod:`analytics.sl_horizon`.

Substrate roles (pre-committed): LIVE ``signal_alert_outcomes`` is the GATE;
``backtest_trades`` corroborates. One Holm family per substrate, never pooled.

Read-only: no DB writes, no config edits, no engine change.

Run: ``PYTHONPATH=. poetry run python tools/sl_horizon_audit.py``
(wrapped by ``make buibui-sl-horizon-audit``).
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from analytics.sl_horizon import (  # noqa: E402
    BASELINE_ARM,
    FAMILY,
    SIGNAL_KEY,
    SLGridConfig,
    arm_label,
    atr_by_open_time,
    baseline_levels,
    counterfactual_levels,
    resolve_arm,
    window_for_signal,
)
from analytics.store import DEFAULT_DB_PATH  # noqa: E402
from analytics.store.market_data import get_ohlcv  # noqa: E402
from analytics.strategies._registry import DETECTOR_REGISTRY  # noqa: E402

TF_MS: dict[str, int] = {
    "15m": 15 * 60_000,
    "1h": 60 * 60_000,
    "4h": 4 * 60 * 60_000,
    "1d": 24 * 60 * 60_000,
}


@dataclass(frozen=True)
class FidelityReport:
    """Result of comparing a replayed baseline arm against a stored substrate."""

    passed: bool
    n_matched: int
    agreement: float
    worst_avg_r_delta: float
    reasons: list[str] = field(default_factory=list)


def load_live_signals(conn: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Resolved live alerts for the six audited detectors.

    Only rows with a non-NULL ``outcome_r`` are returned — unresolved alerts have
    no stored result to compare against and cannot anchor the live fidelity check.
    """
    placeholders = ", ".join("?" for _ in FAMILY)
    return conn.execute(
        f"""
        SELECT signal_id, symbol, tf, strategy, direction, candle_ts_ms,
               entry_price, sl_price, tp_price, rr_ratio, outcome, outcome_r
        FROM signal_alert_outcomes
        WHERE strategy IN ({placeholders})
          AND outcome_r IS NOT NULL
        ORDER BY candle_ts_ms
        """,
        list(FAMILY),
    ).df()


def load_backtest_signals(
    conn: duckdb.DuckDBPyConnection,
    *,
    symbols: list[str],
    timeframes: list[str],
) -> pd.DataFrame:
    """Re-detect the six detectors over full OHLCV history.

    Returns one row per detected signal with columns
    ``SIGNAL_KEY + ["sig_idx", "detector_sl_price"]``. Signals are re-detected
    rather than read from ``backtest_trades`` because the stored runs cover only
    3 symbols over ~10 months, which leaves the 1d cell unpowered.
    """
    records: list[dict[str, object]] = []
    for tf in timeframes:
        for symbol in symbols:
            ohlcv = get_ohlcv(conn, symbol, tf, 0, 2**62)
            if ohlcv.empty:
                continue
            position = {int(t): i for i, t in enumerate(ohlcv["open_time"].to_numpy())}
            for strategy in FAMILY:
                detector = DETECTOR_REGISTRY[strategy]
                signals = detector(ohlcv)
                if signals.empty:
                    continue
                for _, sig in signals.iterrows():
                    open_time = int(sig["open_time"])
                    sig_idx = position.get(open_time)
                    if sig_idx is None:
                        continue
                    records.append(
                        {
                            "symbol": symbol,
                            "tf": tf,
                            "strategy": strategy,
                            "direction": str(sig["direction"]),
                            "open_time": open_time,
                            "sig_idx": sig_idx,
                            "detector_sl_price": float(sig["sl_price"]),
                        }
                    )
    return pd.DataFrame(records)


def resolve_all_arms(
    signals: pd.DataFrame,
    ohlcv_by_key: dict[tuple[str, str], pd.DataFrame],
    *,
    cfg: SLGridConfig,
    convention: str,
    tp_r_for: TpRLookup,
) -> pd.DataFrame:
    """Resolve every signal under the baseline arm and each ``k`` arm.

    Returns long-format rows (one per signal x arm) carrying ``net_r``,
    ``outcome``, ``exit_bar``, ``sl_dist_pct`` and ``atr_pct``, ready for
    ``build_paired_table`` and ``describe_horizon``.
    """
    rows: list[dict[str, object]] = []
    for (symbol, tf), grp in signals.groupby(["symbol", "tf"], sort=True):
        ohlcv = ohlcv_by_key.get((str(symbol), str(tf)))
        if ohlcv is None or ohlcv.empty:
            continue
        max_hold = cfg.max_hold_bars_by_tf.get(str(tf), 48)
        atr_map = atr_by_open_time(ohlcv, grp["open_time"].tolist())

        for _, sig in grp.iterrows():
            open_time = int(sig["open_time"])
            atr = atr_map.get(open_time)
            if atr is None or atr <= 0.0:
                continue
            direction = str(sig["direction"])
            strategy = str(sig["strategy"])
            tp_r = tp_r_for(strategy, str(symbol), str(tf), direction)

            entry, highs, lows, closes = window_for_signal(
                ohlcv,
                sig_idx=int(sig["sig_idx"]),
                convention=convention,
                max_hold_bars=max_hold,
            )
            if len(highs) == 0 or not np.isfinite(entry):
                continue

            arms: list[tuple[str, tuple[float, float]]] = [
                (
                    BASELINE_ARM,
                    baseline_levels(
                        entry, direction, baseline_pct=cfg.baseline_pct, tp_r=tp_r
                    ),
                )
            ]
            for k in cfg.multipliers:
                arms.append(
                    (
                        arm_label(k),
                        counterfactual_levels(
                            entry, direction, atr=atr, k=k, tp_r=tp_r
                        ),
                    )
                )

            for arm, (sl_price, _tp_price) in arms:
                res = resolve_arm(
                    highs,
                    lows,
                    closes,
                    direction=direction,
                    entry=entry,
                    sl_price=sl_price,
                    tp_r=tp_r,
                    max_hold_bars=max_hold,
                    round_trip_cost_pct=cfg.round_trip_cost_pct,
                    funding_r=0.0,
                )
                if res is None:
                    continue
                rows.append(
                    {
                        "symbol": symbol,
                        "tf": tf,
                        "strategy": strategy,
                        "direction": direction,
                        "open_time": open_time,
                        "arm": arm,
                        "net_r": res.net_r,
                        "realized_r": res.realized_r,
                        "outcome": res.outcome,
                        "exit_bar": res.exit_bar,
                        "sl_dist_pct": res.sl_dist_pct,
                        "cost_r": res.cost_r,
                        "atr_pct": atr / entry,
                    }
                )
    return pd.DataFrame(rows)


def check_fidelity(
    replayed: pd.DataFrame,
    stored: pd.DataFrame,
    *,
    tolerance_r: float,
    min_agreement: float,
) -> FidelityReport:
    """Compare a replayed baseline arm against the stored substrate.

    Both conditions must hold, per (strategy x tf):
    ``|avg_r_replayed - avg_r_stored| <= tolerance_r`` AND matched-trade outcome
    agreement ``>= min_agreement``. The mean alone would hide offsetting
    per-trade errors; the agreement rate alone would hide a uniform shift.
    """
    merged = replayed.merge(stored, on=["strategy", "tf", "key"], how="inner")
    if merged.empty:
        return FidelityReport(
            passed=False, n_matched=0, agreement=0.0, worst_avg_r_delta=float("nan"),
            reasons=["no rows matched between replayed and stored substrates"],
        )

    reasons: list[str] = []
    worst = 0.0
    for (strategy, tf), grp in merged.groupby(["strategy", "tf"], sort=True):
        delta = abs(float(grp["net_r"].mean()) - float(grp["stored_r"].mean()))
        worst = max(worst, delta)
        if delta > tolerance_r:
            reasons.append(
                f"{strategy} {tf}: avg_r delta {delta:.4f} > tolerance {tolerance_r}"
            )

    agreement = float((merged["outcome"] == merged["stored_outcome"]).mean())
    if agreement < min_agreement:
        reasons.append(
            f"outcome agreement {agreement:.3f} < required {min_agreement}"
        )

    return FidelityReport(
        passed=not reasons,
        n_matched=int(len(merged)),
        agreement=agreement,
        worst_avg_r_delta=worst,
        reasons=reasons,
    )
```

Add this type alias just below `TF_MS`:

```python
from collections.abc import Callable  # add to the stdlib imports at the top

#: (strategy, symbol, tf, direction) -> pinned tp_r
TpRLookup = Callable[[str, str, str, str], float]
```

- [ ] **Step 4: Run tests and lint**

Run: `poetry run pytest tests/test_sl_horizon_audit.py -v && make lint-py && make typecheck`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add tools/sl_horizon_audit.py tests/test_sl_horizon_audit.py
git commit -m "feat(sl-horizon): DB loaders + pre-committed fidelity gate"
```

---

### Task 7: The live leg and a runnable fidelity gate

**Files:**

- Modify: `tools/sl_horizon_audit.py`
- Modify: `tests/test_sl_horizon_audit.py`

**Interfaces:**

- Consumes: `load_live_signals`, `resolve_all_arms`, `check_fidelity`, `FidelityReport`
  (Task 6); `SLGridConfig`, `BASELINE_ARM`, `arm_label`, `atr_by_open_time`,
  `counterfactual_levels`, `resolve_arm`, `window_for_signal` (Tasks 1–3).
- Produces: `NO_TIME_STOP_BARS`, `load_stored_backtest_trades`, `resolve_live_arms`,
  `backtest_fidelity`, `live_fidelity`.

**Why this task exists.** Task 6 built the fidelity *comparison*; nothing yet *runs* it,
and the live leg — which the spec makes the gating substrate — has no resolver path.
Without both, the tool would emit verdicts nobody has any reason to believe.

**Two conventions, again.** `resolve_live_arms` passes `convention="live"` and uses the
alert's **stored** `entry_price`; the backtest fidelity replay passes
`convention="engine"`. Re-read the table at the top of this plan before writing either.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_sl_horizon_audit.py`:

```python
from tools.sl_horizon_audit import (
    NO_TIME_STOP_BARS,
    load_stored_backtest_trades,
    resolve_live_arms,
)
from analytics.sl_horizon import BASELINE_ARM, SLGridConfig, arm_label


def test_no_time_stop_bars_is_large_enough_to_never_bind() -> None:
    # The engine has no expiry; the fidelity replay must not introduce one.
    assert NO_TIME_STOP_BARS >= 100_000


def test_load_stored_backtest_trades_dedups_across_runs(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    rows = [
        ("r1", "BTCUSDT", "1h", "pin_bar", "long", 1000, 1100, 100.0, 98.0, 106.0,
         1200, 106.0, "win", 3.0),
        # same signal, later run_id — only this one should survive
        ("r2", "BTCUSDT", "1h", "pin_bar", "long", 1000, 1100, 100.0, 98.0, 106.0,
         1200, 98.0, "loss", -1.0),
    ]
    for r in rows:
        conn.execute(
            "INSERT INTO backtest_trades (run_id, symbol, timeframe, strategy, "
            "direction, signal_time, entry_time, entry_price, sl_price, tp_price, "
            "exit_time, exit_price, outcome, pnl_r) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            list(r),
        )
    got = load_stored_backtest_trades(conn, symbols=["BTCUSDT"], timeframes=["1h"])
    assert len(got) == 1
    assert got.iloc[0]["stored_outcome"] == "loss"
    assert set(["strategy", "tf", "key", "stored_r", "stored_outcome"]) <= set(got.columns)


def test_load_stored_backtest_trades_key_is_unique_per_signal(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    for sig_time in (1000, 2000):
        conn.execute(
            "INSERT INTO backtest_trades (run_id, symbol, timeframe, strategy, "
            "direction, signal_time, entry_time, entry_price, sl_price, tp_price, "
            "exit_time, exit_price, outcome, pnl_r) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ["r1", "BTCUSDT", "1h", "pin_bar", "long", sig_time, sig_time + 100,
             100.0, 98.0, 106.0, sig_time + 200, 106.0, "win", 3.0],
        )
    got = load_stored_backtest_trades(conn, symbols=["BTCUSDT"], timeframes=["1h"])
    assert got["key"].nunique() == 2


def test_resolve_live_arms_produces_one_row_per_alert_per_arm() -> None:
    cfg = SLGridConfig(multipliers=(1.0, 2.0))
    alerts = pd.DataFrame(
        [
            {
                "signal_id": "a", "symbol": "BTCUSDT", "tf": "1h",
                "strategy": "pin_bar", "direction": "long",
                "candle_ts_ms": 2_000, "entry_price": 100.0,
                "sl_price": 98.0, "tp_price": 106.0, "rr_ratio": 3.0,
                "outcome": "win", "outcome_r": 3.0,
            }
        ]
    )
    n = 40
    ohlcv = pd.DataFrame(
        {
            "open_time": [1_000 * i for i in range(n)],
            "open": [100.0] * n,
            "high": [101.0] * n,
            "low": [99.0] * n,
            "close": [100.0] * n,
            "volume": [10.0] * n,
        }
    )
    rows = resolve_live_arms(
        alerts,
        {("BTCUSDT", "1h"): ohlcv},
        cfg=cfg,
        tp_r_for=lambda *_: 3.0,
    )
    assert set(rows["arm"]) == {BASELINE_ARM, arm_label(1.0), arm_label(2.0)}
    assert len(rows) == 3


def test_resolve_live_arms_baseline_uses_the_stored_sl() -> None:
    """The live baseline must reproduce the alert, so it uses the STORED sl_price."""
    cfg = SLGridConfig(multipliers=(1.0,))
    alerts = pd.DataFrame(
        [
            {
                "signal_id": "a", "symbol": "BTCUSDT", "tf": "1h",
                "strategy": "pin_bar", "direction": "long",
                "candle_ts_ms": 2_000, "entry_price": 100.0,
                "sl_price": 97.0, "tp_price": 109.0, "rr_ratio": 3.0,
                "outcome": "win", "outcome_r": 3.0,
            }
        ]
    )
    n = 40
    ohlcv = pd.DataFrame(
        {
            "open_time": [1_000 * i for i in range(n)],
            "open": [100.0] * n, "high": [101.0] * n,
            "low": [99.0] * n, "close": [100.0] * n, "volume": [10.0] * n,
        }
    )
    rows = resolve_live_arms(
        alerts, {("BTCUSDT", "1h"): ohlcv}, cfg=cfg, tp_r_for=lambda *_: 3.0
    )
    baseline = rows[rows["arm"] == BASELINE_ARM].iloc[0]
    # stored SL is 3% away, not the nominal 2% — the replay must honour it.
    assert baseline["sl_dist_pct"] == pytest.approx(0.03)
```

**Note on the `backtest_trades` INSERT above:** the column list is explicit, but if
`trade_id` is NOT NULL without a default in the live schema, add it to the INSERT.
Read `analytics/store/schema.py` and adapt — the test's *intent* (two runs, same
signal, later `run_id` wins) is what matters, not the literal column list.

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_sl_horizon_audit.py -k "live_arms or stored_backtest or time_stop" -v`
Expected: FAIL — `ImportError: cannot import name 'NO_TIME_STOP_BARS'`

- [ ] **Step 3: Write minimal implementation**

Append to `tools/sl_horizon_audit.py`:

```python
#: The backtest engine has no expiry, so the fidelity replay must not impose one.
#: Larger than any realistic OHLCV history, so the window is never truncated.
NO_TIME_STOP_BARS = 10_000_000


def load_stored_backtest_trades(
    conn: duckdb.DuckDBPyConnection,
    *,
    symbols: list[str],
    timeframes: list[str],
) -> pd.DataFrame:
    """Stored engine trades for the family, deduped across saved runs.

    Keeps the lexicographically-latest ``run_id`` per
    ``(symbol, tf, strategy, direction, signal_time)`` — the same dedup
    ``tools/warning_value_audit.py`` uses. Returns the columns
    ``check_fidelity`` expects: ``strategy``, ``tf``, ``key``, ``stored_r``,
    ``stored_outcome``.
    """
    fam = ", ".join("?" for _ in FAMILY)
    sym = ", ".join("?" for _ in symbols)
    tfs = ", ".join("?" for _ in timeframes)
    raw = conn.execute(
        f"""
        SELECT run_id, symbol, timeframe AS tf, strategy, direction,
               signal_time, pnl_r, outcome
        FROM backtest_trades
        WHERE strategy IN ({fam})
          AND symbol IN ({sym})
          AND timeframe IN ({tfs})
          AND pnl_r IS NOT NULL
        """,
        [*FAMILY, *symbols, *timeframes],
    ).df()
    if raw.empty:
        return pd.DataFrame(columns=["strategy", "tf", "key", "stored_r", "stored_outcome"])

    # Sort then drop_duplicates in pandas — DuckDB window functions have
    # segfaulted on this table before (see feedback_duckdb_window_functions).
    raw = raw.sort_values("run_id")
    deduped = raw.drop_duplicates(
        subset=["symbol", "tf", "strategy", "direction", "signal_time"], keep="last"
    )
    deduped = deduped.assign(
        key=(
            deduped["symbol"].astype(str)
            + "|" + deduped["direction"].astype(str)
            + "|" + deduped["signal_time"].astype("int64").astype(str)
        )
    )
    return deduped.rename(columns={"pnl_r": "stored_r", "outcome": "stored_outcome"})[
        ["strategy", "tf", "key", "stored_r", "stored_outcome"]
    ]


def resolve_live_arms(
    alerts: pd.DataFrame,
    ohlcv_by_key: dict[tuple[str, str], pd.DataFrame],
    *,
    cfg: SLGridConfig,
    tp_r_for: TpRLookup,
) -> pd.DataFrame:
    """Resolve live alerts under the baseline and each ``k`` arm.

    The baseline arm uses the alert's **stored** ``sl_price``, not a recomputed
    2%, so it reproduces what actually fired — that is what makes the stored
    ``outcome_r`` a usable second fidelity anchor. The ``k`` arms replace the
    stop with ``k × ATR14`` at the signal candle.
    """
    rows: list[dict[str, object]] = []
    for (symbol, tf), grp in alerts.groupby(["symbol", "tf"], sort=True):
        ohlcv = ohlcv_by_key.get((str(symbol), str(tf)))
        if ohlcv is None or ohlcv.empty:
            continue
        max_hold = cfg.max_hold_bars_by_tf.get(str(tf), 48)
        position = {int(t): i for i, t in enumerate(ohlcv["open_time"].to_numpy())}
        atr_map = atr_by_open_time(ohlcv, grp["candle_ts_ms"].tolist())

        for _, alert in grp.iterrows():
            candle_ts = int(alert["candle_ts_ms"])
            sig_idx = position.get(candle_ts)
            atr = atr_map.get(candle_ts)
            if sig_idx is None or atr is None or atr <= 0.0:
                continue

            direction = str(alert["direction"])
            strategy = str(alert["strategy"])
            entry = float(alert["entry_price"])
            tp_r = tp_r_for(strategy, str(symbol), str(tf), direction)

            _entry_unused, highs, lows, closes = window_for_signal(
                ohlcv, sig_idx=sig_idx, convention="live", max_hold_bars=max_hold
            )
            if len(highs) == 0:
                continue

            arms: list[tuple[str, float]] = [(BASELINE_ARM, float(alert["sl_price"]))]
            for k in cfg.multipliers:
                sl_price, _tp = counterfactual_levels(
                    entry, direction, atr=atr, k=k, tp_r=tp_r
                )
                arms.append((arm_label(k), sl_price))

            for arm, sl_price in arms:
                res = resolve_arm(
                    highs, lows, closes,
                    direction=direction, entry=entry, sl_price=sl_price,
                    tp_r=tp_r, max_hold_bars=max_hold,
                    round_trip_cost_pct=cfg.round_trip_cost_pct, funding_r=0.0,
                )
                if res is None:
                    continue
                rows.append(
                    {
                        "symbol": symbol, "tf": tf, "strategy": strategy,
                        "direction": direction, "open_time": candle_ts,
                        "arm": arm, "net_r": res.net_r,
                        "realized_r": res.realized_r, "outcome": res.outcome,
                        "exit_bar": res.exit_bar, "sl_dist_pct": res.sl_dist_pct,
                        "cost_r": res.cost_r, "atr_pct": atr / entry,
                        "key": f"{symbol}|{direction}|{candle_ts}",
                    }
                )
    return pd.DataFrame(rows)


def backtest_fidelity(
    conn: duckdb.DuckDBPyConnection,
    signals: pd.DataFrame,
    ohlcv_by_key: dict[tuple[str, str], pd.DataFrame],
    *,
    cfg: SLGridConfig,
    tp_r_for: TpRLookup,
) -> FidelityReport:
    """Spec §7a — replay the baseline arm with NO time stop and compare to stored.

    A systematic offset here almost always means the entry convention is wrong,
    not that the model is wrong. Check `window_for_signal(convention="engine")`
    against `analytics/backtest/engine.py:954-1061` before anything else.
    """
    no_expiry = SLGridConfig(
        multipliers=cfg.multipliers,
        baseline_pct=cfg.baseline_pct,
        max_hold_bars_by_tf={tf: NO_TIME_STOP_BARS for tf in TF_MS},
        fee_pct=cfg.fee_pct,
        slippage_bps=cfg.slippage_bps,
    )
    replayed = resolve_all_arms(
        signals, ohlcv_by_key, cfg=no_expiry, convention="engine", tp_r_for=tp_r_for
    )
    if replayed.empty:
        return FidelityReport(
            passed=False, n_matched=0, agreement=0.0, worst_avg_r_delta=float("nan"),
            reasons=["baseline replay produced no rows"],
        )
    baseline = replayed[replayed["arm"] == BASELINE_ARM].copy()
    baseline["key"] = (
        baseline["symbol"].astype(str)
        + "|" + baseline["direction"].astype(str)
        + "|" + baseline["open_time"].astype("int64").astype(str)
    )
    stored = load_stored_backtest_trades(
        conn,
        symbols=sorted({str(s) for s in signals["symbol"].unique()}),
        timeframes=sorted({str(t) for t in signals["tf"].unique()}),
    )
    return check_fidelity(baseline, stored, tolerance_r=0.02, min_agreement=0.95)


def live_fidelity(live_rows: pd.DataFrame, alerts: pd.DataFrame) -> FidelityReport:
    """Spec §7b — the re-resolved live baseline vs the stored ``outcome_r``."""
    if live_rows.empty:
        return FidelityReport(
            passed=False, n_matched=0, agreement=0.0, worst_avg_r_delta=float("nan"),
            reasons=["live replay produced no rows"],
        )
    baseline = live_rows[live_rows["arm"] == BASELINE_ARM].copy()
    stored = alerts.assign(
        key=(
            alerts["symbol"].astype(str)
            + "|" + alerts["direction"].astype(str)
            + "|" + alerts["candle_ts_ms"].astype("int64").astype(str)
        ),
        tf=alerts["tf"],
    ).rename(columns={"outcome_r": "stored_r", "outcome": "stored_outcome"})[
        ["strategy", "tf", "key", "stored_r", "stored_outcome"]
    ]
    return check_fidelity(baseline, stored, tolerance_r=0.02, min_agreement=0.95)
```

- [ ] **Step 4: Run tests and lint**

Run: `poetry run pytest tests/test_sl_horizon_audit.py -v && make lint-py && make typecheck`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add tools/sl_horizon_audit.py tests/test_sl_horizon_audit.py
git commit -m "feat(sl-horizon): live-leg resolver + runnable fidelity gate"
```

---

### Task 8: CLI, report, Makefile, and docs

**Files:**

- Modify: `tools/sl_horizon_audit.py`
- Modify: `Makefile`
- Modify: `CLAUDE.md`
- Modify: `README.md`

**Interfaces:**

- Consumes: everything from Tasks 1–7.
- Produces: `build_tp_r_lookup`, `render_report`, `main`; the
  `make buibui-sl-horizon-audit` target.

**Do not run the full audit as part of this task's tests.** The real run happens after
this task lands, and its output becomes `docs/audits/2026-07-21-st9-sl-horizon.md`.

- [ ] **Step 1: Add the tp_r lookup and report renderer**

Append to `tools/sl_horizon_audit.py`:

```python
from analytics.signal.resolvers import _resolve_tp_r  # noqa: E402
from analytics.signal_config import load_signal_config  # noqa: E402
from analytics.sl_horizon import (  # noqa: E402
    SLVerdict,
    build_paired_table,
    describe_horizon,
    evaluate_sl_grid,
)
from analytics.universe import load_universe  # noqa: E402

DEFAULT_CONFIG = "config/signal_watch.toml"
DEFAULT_OUT = REPO_ROOT / "docs" / "audits" / "2026-07-21-st9-sl-horizon.md"
MAJORS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")


def build_tp_r_lookup(config_path: str) -> TpRLookup:
    """Pin tp_r per (strategy, symbol, tf, direction) from a live config.

    tp_r is PINNED, never swept — the audit has exactly one free axis (k).
    """
    cfg = load_signal_config(config_path)

    def lookup(strategy: str, symbol: str, tf: str, direction: str) -> float:
        return _resolve_tp_r(
            cfg.strategy_params, strategy, symbol, tf, cfg.tp_r, direction
        )

    return lookup


def _fmt(value: float | None, digits: int = 3) -> str:
    return "—" if value is None or not np.isfinite(value) else f"{value:.{digits}f}"


def render_report(
    *,
    horizon: pd.DataFrame,
    verdicts_live: list[SLVerdict],
    verdicts_backtest: list[SLVerdict],
    fidelity_backtest: FidelityReport,
    fidelity_live: FidelityReport,
    cfg: SLGridConfig,
) -> str:
    """Render the markdown audit report (markdownlint-conformant)."""
    lines: list[str] = [
        "# ST9 / H11 — SL-horizon audit",
        "",
        "Read-only. Live `signal_alert_outcomes` GATES the verdict;",
        "`backtest_trades` corroborates. One Holm family per substrate.",
        "",
        f"Grid (a-priori, never tuned): `k in {list(cfg.multipliers)}` x ATR14, "
        f"baseline `{cfg.baseline_pct:.0%}` flat. `tp_r` pinned, never swept.",
        "",
        "## Fidelity gate",
        "",
        "| Substrate | Passed | Matched | Agreement | Worst avg_r delta |",
        "| --- | --- | --- | --- | --- |",
        f"| backtest | {fidelity_backtest.passed} | {fidelity_backtest.n_matched} | "
        f"{_fmt(fidelity_backtest.agreement)} | {_fmt(fidelity_backtest.worst_avg_r_delta)} |",
        f"| live | {fidelity_live.passed} | {fidelity_live.n_matched} | "
        f"{_fmt(fidelity_live.agreement)} | {_fmt(fidelity_live.worst_avg_r_delta)} |",
        "",
    ]
    for report, name in ((fidelity_backtest, "backtest"), (fidelity_live, "live")):
        for reason in report.reasons:
            lines.append(f"- **{name} fidelity:** {reason}")
    if fidelity_backtest.reasons or fidelity_live.reasons:
        lines.append("")

    lines += [
        "## Descriptive horizon (baseline arm)",
        "",
        "| Strategy | TF | n | avg_r | median bars | expiry rate | SL % | SL in ATR |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for _, row in horizon.iterrows():
        lines.append(
            f"| {row['strategy']} | {row['tf']} | {int(row['n'])} | "
            f"{_fmt(row['avg_r'])} | {_fmt(row['median_bars'], 1)} | "
            f"{_fmt(row['expiry_rate'])} | {_fmt(row['median_sl_pct'], 4)} | "
            f"{_fmt(row['median_sl_atr'], 2)} |"
        )
    lines.append("")

    for verdicts, name in ((verdicts_live, "LIVE (gate)"), (verdicts_backtest, "Backtest")):
        lines += [
            f"## Verdicts — {name}",
            "",
            "| Strategy | TF | Decision | n | baseline avg_r | best k | lift | "
            "CI lo | CI hi | adj p | DSR | PBO |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for v in verdicts:
            lines.append(
                f"| {v.strategy} | {v.tf} | **{v.decision}** | {v.n} | "
                f"{_fmt(v.baseline_avg_r)} | {_fmt(v.best_k, 1)} | {_fmt(v.best_lift)} | "
                f"{_fmt(v.ci_lo)} | {_fmt(v.ci_hi)} | {_fmt(v.adj_pvalue)} | "
                f"{_fmt(v.dsr)} | {_fmt(v.pbo)} |"
            )
        lines.append("")

    cost = cfg.round_trip_cost_pct
    lines += [
        "## Cost context",
        "",
        f"Round-trip cost is `{cost:.4%}` of notional "
        f"(`2 x fee {cfg.fee_pct:.4f}` + `2 x slippage {cfg.slippage_bps} bps`).",
        "In R terms this scales inversely with stop width, so a tight stop is",
        "charged more R for the same trade. Read every lift against the",
        "`SL in ATR` column above before calling it an edge.",
        "",
    ]
    return "\n".join(lines)
```

- [ ] **Step 2: Add the CLI entry point**

Append to `tools/sl_horizon_audit.py`:

```python
def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Read-only; writes only the markdown report."""
    parser = argparse.ArgumentParser(description="ST9/H11 SL-horizon audit (read-only)")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument(
        "--timeframes", nargs="+", default=["1h", "4h", "1d"],
        help="15m is majors-only (no universe OHLCV at 15m).",
    )
    parser.add_argument("--min-n", type=int, default=30)
    parser.add_argument(
        "--majors-only", action="store_true",
        help="Restrict to BTC/ETH/SOL for a like-for-like cross-TF read.",
    )
    args = parser.parse_args(argv)

    cfg = SLGridConfig(min_n=args.min_n)
    arms = [arm_label(k) for k in cfg.multipliers]
    conn = duckdb.connect(args.db, read_only=True)
    try:
        symbols = list(MAJORS) if args.majors_only else load_universe()
        tp_r_for = build_tp_r_lookup(args.config)

        ohlcv_by_key = {
            (str(sym), str(tf)): get_ohlcv(conn, str(sym), str(tf), 0, 2**62)
            for tf in args.timeframes
            for sym in symbols
        }

        # --- backtest leg (corroboration) ---
        signals = load_backtest_signals(
            conn, symbols=symbols, timeframes=list(args.timeframes)
        )
        bt_rows = resolve_all_arms(
            signals, ohlcv_by_key, cfg=cfg, convention="engine", tp_r_for=tp_r_for
        )
        fid_bt = backtest_fidelity(
            conn, signals, ohlcv_by_key, cfg=cfg, tp_r_for=tp_r_for
        )

        # --- live leg (the GATE) ---
        alerts = load_live_signals(conn)
        live_ohlcv = {
            (str(sym), str(tf)): get_ohlcv(conn, str(sym), str(tf), 0, 2**62)
            for tf in sorted({str(t) for t in alerts["tf"].unique()})
            for sym in sorted({str(s) for s in alerts["symbol"].unique()})
        } if not alerts.empty else {}
        live_rows = resolve_live_arms(
            alerts, live_ohlcv, cfg=cfg, tp_r_for=tp_r_for
        )
        fid_live = live_fidelity(live_rows, alerts)
    finally:
        conn.close()

    bt_verdicts = evaluate_sl_grid(
        build_paired_table(bt_rows, arms=[BASELINE_ARM, *arms]), arms=arms, cfg=cfg
    )
    live_verdicts = evaluate_sl_grid(
        build_paired_table(live_rows, arms=[BASELINE_ARM, *arms]), arms=arms, cfg=cfg
    )

    report = render_report(
        horizon=describe_horizon(bt_rows, arm=BASELINE_ARM),
        verdicts_live=live_verdicts,
        verdicts_backtest=bt_verdicts,
        fidelity_backtest=fid_bt,
        fidelity_live=fid_live,
        cfg=cfg,
    )
    Path(args.out).write_text(report, encoding="utf-8")
    print(report)

    # The fidelity gate is an ACCEPTANCE condition, not a warning. A non-zero
    # exit makes a drifting harness impossible to ignore in CI or a make run.
    if not (fid_bt.passed and fid_live.passed):
        print(
            "\nFIDELITY GATE FAILED — verdicts above are NOT trustworthy.\n"
            "Check the entry convention first (engine enters at opens[sig_idx+1] "
            "and scans inclusive; live uses the stored entry with a window "
            "strictly after the signal candle).",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 3: Add the Makefile target**

In `Makefile`, append `buibui-sl-horizon-audit` to the long `.PHONY` list on line 14
(after `buibui-warning-value-audit`), then add this target next to
`buibui-warning-value-audit` near line 328. **The recipe line must start with a real
tab**, not spaces — make rejects spaces there:

<!-- markdownlint-disable MD010 -->

```makefile
.PHONY: buibui-sl-horizon-audit
buibui-sl-horizon-audit:  ## ST9/H11: read-only SL-horizon audit (flat 2% vs ATR-scaled stops)
	PYTHONPATH=. poetry run python tools/sl_horizon_audit.py
```

<!-- markdownlint-enable MD010 -->

- [ ] **Step 4: Verify the tool runs end to end**

Run: `make buibui-sl-horizon-audit`
Expected: a markdown report printed and written to
`docs/audits/2026-07-21-st9-sl-horizon.md`. This is a real run over the universe and
may take several minutes.

Then: `make lint-md`
Expected: `0 issues`.

**The command exits 1 when the fidelity gate fails, so `make` will report an error.**
That is the design, not a bug — a drifting harness must be impossible to ignore. If it
happens: stop, report it, and **do not interpret the verdict tables**. Check the entry
convention first (see the table at the top of this plan); a uniform per-cell offset in
`avg_r` is its signature. Never widen `tolerance_r` to get a pass.

- [ ] **Step 5: Update the docs**

In `CLAUDE.md`, under the `analytics/` bullet list, add a `sl_horizon.py` entry
describing the pure library, and under `tools/`, add a `sl_horizon_audit.py` entry
naming the `make` target and linking the spec. In `README.md`, add the new `make`
target wherever the other `buibui-*-audit` targets are listed.

- [ ] **Step 6: Full gate and commit**

Run: `make lint-py && make typecheck && make test && make test-regression`
Expected: all green; regression goldens **unmoved** (this change is additive and
read-only, so any golden movement means something non-additive was touched — stop
and investigate rather than regenerating).

```bash
git add tools/sl_horizon_audit.py Makefile CLAUDE.md README.md
git commit -m "feat(sl-horizon): CLI, report renderer, make target, docs"
```

---

## After the plan

The audit document `docs/audits/2026-07-21-st9-sl-horizon.md` is produced by the first
real run, not by this plan. Interpreting it is a separate step with its own gate:

- A `SUSPECT` verdict yields a **recommendation** for a targeted `atr_sl_multiplier`
  change, never a TOML edit. Applying it is separately gated.
- A `CONFIRMED-BAD` verdict closes those cells permanently — record it in the memory
  file `project_flat_sl_pct_defect.md` and in `project_todo_master.md`.
- Either way, update the `flat-sl-pct-defect` memory from "not yet tested" to the
  verdict, and refresh `docs/plans/next-conversation-prompt.md`.
