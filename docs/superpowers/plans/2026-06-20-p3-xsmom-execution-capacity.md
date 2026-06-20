# XS-momentum Execution-Realism Capacity Stress Test — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the XS book's flat 2 bps/leg slippage with a per-instrument, size-aware cost (a-priori tiered half-spread + √ market-impact vs trailing dollar-ADV) and sweep target capital to find the AUM where the +1.375 edge decays below the de-biased gate.

**Architecture:** Pure/causal/read-only/additive/default-off. A new `analytics/xsmom/execution.py` builds the per-(instrument, day) cost-rate matrix; `run_xs_backtest` gains one optional keyword-only `turnover_cost_rate` arg (None ⇒ byte-identical to today); `replay.py` adds a dollar-volume loader + a capacity replay; `report.py` adds a capacity-table evaluator reusing `evaluate_xs`; a read-only driver prints the capacity sweep + sensitivities. No schema change, no golden change, no live-daemon contact.

**Tech Stack:** Python 3.11, pandas, numpy, DuckDB (read-only), pytest, the existing `analytics/forecast` + `analytics/xsmom` + `analytics/research_guards` + `portfolio.metrics` packages.

---

## File structure

- **Create** `analytics/xsmom/execution.py` — `ExecutionCostConfig` + `dollar_adv` + `turnover_cost_rate` + `run_xs_with_costs`. Pure, no DB.
- **Modify** `analytics/xsmom/book.py` — add keyword-only `turnover_cost_rate` arg to `run_xs_backtest` (default-off byte-identical).
- **Modify** `analytics/xsmom/replay.py` — add `load_daily_dollar_volumes` + `replay_xs_capacity`.
- **Modify** `analytics/xsmom/report.py` — add `evaluate_xs_capacity`.
- **Modify** `analytics/xsmom/__init__.py` — export the new public symbols.
- **Create** `tools/xsmom_capacity_audit.py` — read-only driver.
- **Modify** `Makefile` — add `buibui-xsmom-capacity-audit` target.
- **Create** `tests/test_xsmom_execution.py` — execution-module unit tests.
- **Modify** `tests/test_xsmom_book.py` (or create if absent) — byte-identical default-off test.
- **Create** `tests/test_xsmom_capacity_replay.py` — DB-seeded replay test.
- **Create** `docs/audits/2026-06-20-p3-xsmom-capacity.md` — verdict (Task 7).

---

## Task 1: `ExecutionCostConfig` + causal `dollar_adv`

**Files:**

- Create: `analytics/xsmom/execution.py`
- Test: `tests/test_xsmom_execution.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_xsmom_execution.py
from __future__ import annotations

import numpy as np
import pandas as pd

from analytics.xsmom.execution import ExecutionCostConfig, dollar_adv


def _idx(n: int) -> pd.DatetimeIndex:
    return pd.date_range("2021-01-01", periods=n, freq="D", tz="UTC")


def test_execution_cost_config_defaults() -> None:
    cfg = ExecutionCostConfig()
    assert cfg.capital == 1_000_000.0
    assert cfg.impact == "sqrt"
    assert cfg.adv_window == 30
    # tiers: major tightest, alt widest
    assert cfg.major_bps < cfg.mid_bps < cfg.alt_bps


def test_dollar_adv_trailing_median_and_causal_shift() -> None:
    # dollar volume = 10 for first 5 days, then 100 thereafter.
    idx = _idx(10)
    dv = pd.Series([10.0] * 5 + [100.0] * 5, index=idx)
    adv = dollar_adv({"X": dv}, window=3)["X"]
    # window=3 median, then shift(1): row d uses days d-3..d-1.
    # First 3 rows are NaN (no full prior window after shift).
    assert adv.iloc[:3].isna().all()
    # Day index 3 uses days 0,1,2 -> median(10,10,10) = 10.
    assert adv.iloc[3] == 10.0
    # Day index 4 uses days 1,2,3 -> 10.
    assert adv.iloc[4] == 10.0


def test_dollar_adv_is_causal_to_same_day_volume() -> None:
    idx = _idx(10)
    dv = pd.Series(np.linspace(1.0, 10.0, 10), index=idx)
    base = dollar_adv({"X": dv}, window=3)["X"]
    bumped = dv.copy()
    bumped.iloc[5] *= 100.0  # perturb day 5
    out = dollar_adv({"X": bumped}, window=3)["X"]
    # Day 5's own ADV must be unchanged (uses days 2,3,4 via shift).
    assert out.iloc[5] == base.iloc[5]
    # The change only shows up from day 6 onward.
    assert out.iloc[6] != base.iloc[6]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_xsmom_execution.py -v`
Expected: FAIL with `ModuleNotFoundError: analytics.xsmom.execution`

- [ ] **Step 3: Write the minimal implementation**

```python
# analytics/xsmom/execution.py
"""Size-aware execution cost model for the cross-sectional momentum sleeve.

Replaces the book's flat per-leg slippage with a per-(instrument, day) rate:

    cost_rate_i(d) = fee_pct + half_spread_i(d) + k * impact(|Δlev_i(d)| * C / ADV_i(d))

`half_spread_i(d)` is an a-priori bps tier keyed by trailing dollar-ADV; the
impact term carries the size-dependence. Pure and causal: ADV is a trailing
median shifted one day, so day-`d` cost uses liquidity through `d-1` only.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class ExecutionCostConfig:
    """A-priori, size-aware turnover-cost parameters (all swept, none fit)."""

    capital: float = 1_000_000.0  # target AUM (USD); the swept axis
    k: float = 0.1  # impact coefficient (dimensionless under sqrt)
    impact: str = "sqrt"  # "sqrt" (headline) or "linear" (robustness)
    adv_window: int = 30  # trailing window (days) for dollar-ADV
    fee_pct: float = 0.0005  # size-independent maker/taker fee (matches ForecastConfig)
    # half-spread tiers (bps) by trailing dollar-ADV (USD) cutoffs
    major_bps: float = 1.0
    mid_bps: float = 3.0
    alt_bps: float = 8.0
    major_cutoff: float = 1_000_000_000.0  # >= $1B ADV -> major tier
    mid_cutoff: float = 100_000_000.0  # >= $100M ADV -> mid tier


def dollar_adv(
    dollar_volumes: dict[str, pd.Series], window: int
) -> dict[str, pd.Series]:
    """Causal trailing-median dollar ADV per instrument.

    `dollar_volumes[sym]` is the per-day dollar volume (`volume * close`),
    day-indexed. Returns the trailing-`window` median shifted one day so the
    value at row `d` uses only days through `d-1` (no same-day leak).
    """
    out: dict[str, pd.Series] = {}
    for sym, dv in dollar_volumes.items():
        out[sym] = dv.rolling(window).median().shift(1)
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_xsmom_execution.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
ruff format analytics/xsmom/execution.py tests/test_xsmom_execution.py
ruff check --fix analytics/xsmom/execution.py tests/test_xsmom_execution.py
git add analytics/xsmom/execution.py tests/test_xsmom_execution.py
git commit -m "feat(xsmom): ExecutionCostConfig + causal dollar_adv (capacity stress test)"
git log --oneline -1
```

---

## Task 2: `turnover_cost_rate` + `run_xs_with_costs`

**Files:**

- Modify: `analytics/xsmom/execution.py`
- Test: `tests/test_xsmom_execution.py`

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_xsmom_execution.py
from analytics.xsmom.execution import turnover_cost_rate


def test_turnover_cost_rate_tiers_and_sqrt_impact() -> None:
    idx = _idx(2)
    # Two instruments: one major-liquid, one thin alt.
    leverage = pd.DataFrame(
        {"BIG": [0.0, 0.5], "THIN": [0.0, 0.5]}, index=idx
    )
    adv = {
        "BIG": pd.Series([np.nan, 2_000_000_000.0], index=idx),  # major tier
        "THIN": pd.Series([np.nan, 1_000_000.0], index=idx),  # alt tier
    }
    cfg = ExecutionCostConfig(capital=10_000_000.0, k=0.1, impact="sqrt")
    rate = turnover_cost_rate(leverage, adv, cfg)
    # Row 0: leverage diff 0 but ADV NaN -> impact NaN -> rate NaN.
    assert rate.loc[idx[0]].isna().all()
    # Row 1, BIG: fee + major half-spread + k*sqrt(0.5*10e6 / 2e9)
    exp_big = 0.0005 + 1.0 / 1e4 + 0.1 * np.sqrt(0.5 * 10_000_000.0 / 2_000_000_000.0)
    assert rate.loc[idx[1], "BIG"] == pytest_approx(exp_big)
    # THIN pays the alt spread AND far more impact (tiny ADV).
    exp_thin = 0.0005 + 8.0 / 1e4 + 0.1 * np.sqrt(0.5 * 10_000_000.0 / 1_000_000.0)
    assert rate.loc[idx[1], "THIN"] == pytest_approx(exp_thin)
    assert rate.loc[idx[1], "THIN"] > rate.loc[idx[1], "BIG"]


def test_turnover_cost_rate_collapses_to_flat_at_zero_impact() -> None:
    idx = _idx(2)
    leverage = pd.DataFrame({"X": [0.0, 0.3]}, index=idx)
    adv = {"X": pd.Series([1e9, 1e9], index=idx)}
    # k=0 and all tiers equal -> a flat per-leg constant.
    cfg = ExecutionCostConfig(
        k=0.0, major_bps=2.0, mid_bps=2.0, alt_bps=2.0, fee_pct=0.0005
    )
    rate = turnover_cost_rate(leverage, adv, cfg)
    assert np.allclose(rate["X"].to_numpy(), 0.0005 + 2.0 / 1e4)


def test_turnover_cost_rate_monotonic_in_capital() -> None:
    idx = _idx(2)
    leverage = pd.DataFrame({"X": [0.0, 0.4]}, index=idx)
    adv = {"X": pd.Series([5e7, 5e7], index=idx)}  # alt tier, finite ADV
    lo = turnover_cost_rate(leverage, adv, ExecutionCostConfig(capital=1e6, k=0.1))
    hi = turnover_cost_rate(leverage, adv, ExecutionCostConfig(capital=1e8, k=0.1))
    assert hi.loc[idx[1], "X"] > lo.loc[idx[1], "X"]
```

Add this import at the top of the test file:

```python
from pytest import approx as pytest_approx
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_xsmom_execution.py -k turnover -v`
Expected: FAIL with `ImportError: cannot import name 'turnover_cost_rate'`

- [ ] **Step 3: Write the implementation**

```python
# append to analytics/xsmom/execution.py
def turnover_cost_rate(
    leverage: pd.DataFrame,
    adv: dict[str, pd.Series],
    cfg: ExecutionCostConfig,
) -> pd.DataFrame:
    """Per-(instrument, day) turnover cost rate (fraction of capital per unit |Δlev|).

    `rate = fee + half_spread(ADV-tier) + k * impact(|Δlev| * capital / ADV)`.
    `impact` is `sqrt` (headline) or `linear`. NaN ADV (warm-up) -> NaN rate, so
    those cells drop out of the book's net (same skipna semantics as leverage
    warm-up). inf (zero-ADV) is mapped to NaN for the same reason.
    """
    idx = leverage.index
    adv_df = pd.DataFrame(
        {sym: adv.get(sym, pd.Series(np.nan, index=idx)).reindex(idx) for sym in leverage.columns},
        index=idx,
    )

    # A-priori half-spread tiers (bps -> fraction). NaN ADV falls to the alt
    # default but the impact term below makes the whole rate NaN there anyway.
    conds = [adv_df >= cfg.major_cutoff, adv_df >= cfg.mid_cutoff]
    choices = [cfg.major_bps, cfg.mid_bps]
    half_spread = pd.DataFrame(
        np.select(conds, choices, default=cfg.alt_bps) / 1e4,
        index=idx,
        columns=leverage.columns,
    )

    dlev = (leverage - leverage.shift(1).fillna(0.0)).abs()
    participation = (dlev * cfg.capital / adv_df).replace([np.inf, -np.inf], np.nan)
    if cfg.impact == "sqrt":
        impact = cfg.k * np.sqrt(participation)
    elif cfg.impact == "linear":
        impact = cfg.k * participation
    else:  # pragma: no cover - guarded by config construction in practice
        raise ValueError(f"unknown impact form: {cfg.impact!r}")

    return cfg.fee_pct + half_spread + impact
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_xsmom_execution.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
ruff format analytics/xsmom/execution.py tests/test_xsmom_execution.py
ruff check --fix analytics/xsmom/execution.py tests/test_xsmom_execution.py
git add analytics/xsmom/execution.py tests/test_xsmom_execution.py
git commit -m "feat(xsmom): turnover_cost_rate — tiered half-spread + sqrt/linear impact"
git log --oneline -1
```

---

## Task 3: `run_xs_backtest` optional `turnover_cost_rate` (default-off byte-identical)

**Files:**

- Modify: `analytics/xsmom/book.py:101` (`run_xs_backtest`)
- Test: `tests/test_xsmom_execution.py`

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_xsmom_execution.py
import dataclasses

from analytics.forecast.config import ForecastConfig
from analytics.xsmom.book import run_xs_backtest


def _synth_inputs(n: int = 400, seed: int = 0) -> tuple[dict, dict]:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2021-01-01", periods=n, freq="D", tz="UTC")
    closes: dict[str, pd.Series] = {}
    fundings: dict[str, pd.Series] = {}
    for i, sym in enumerate(["A", "B", "C"]):
        steps = rng.normal(0.0, 0.02, n) + 0.0005 * (i - 1)
        closes[sym] = pd.Series(100.0 * np.exp(np.cumsum(steps)), index=idx)
        fundings[sym] = pd.Series(0.0, index=idx)
    return closes, fundings


def test_run_xs_backtest_default_off_is_byte_identical() -> None:
    closes, fundings = _synth_inputs()
    cfg = dataclasses.replace(ForecastConfig(), speeds=((8, 32, 5.3),))
    base = run_xs_backtest(closes, fundings, cfg)
    again = run_xs_backtest(closes, fundings, cfg, turnover_cost_rate=None)
    assert np.array_equal(
        base.portfolio_return, again.portfolio_return, equal_nan=True
    )


def test_run_xs_backtest_constant_rate_matches_scalar_path() -> None:
    closes, fundings = _synth_inputs()
    cfg = dataclasses.replace(ForecastConfig(), speeds=((8, 32, 5.3),))
    base = run_xs_backtest(closes, fundings, cfg)  # scalar cost = fee + slip
    from analytics.xsmom.book import xs_leverage

    lev = xs_leverage(closes, cfg)
    flat = cfg.fee_pct + cfg.slippage_pct
    const_rate = pd.DataFrame(flat, index=lev.index, columns=lev.columns)
    out = run_xs_backtest(closes, fundings, cfg, turnover_cost_rate=const_rate)
    assert np.allclose(
        base.portfolio_return, out.portfolio_return, equal_nan=True
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_xsmom_execution.py -k "default_off or constant_rate" -v`
Expected: FAIL with `TypeError: run_xs_backtest() got an unexpected keyword argument 'turnover_cost_rate'`

- [ ] **Step 3: Modify `run_xs_backtest`**

In `analytics/xsmom/book.py`, change the signature and the per-instrument turnover line. The current body is:

```python
def run_xs_backtest(
    closes: dict[str, pd.Series],
    fundings: dict[str, pd.Series],
    cfg: ForecastConfig,
) -> XSBookResult:
    ...
    leverage = xs_leverage(closes, cfg)
    union = pd.DatetimeIndex(leverage.index)
    cost = cfg.fee_pct + cfg.slippage_pct

    per_net: dict[str, pd.Series] = {}
    net_cols: list[pd.Series] = []
    for sym, close in closes.items():
        lev = leverage[sym]
        r = close.pct_change().reindex(union)
        gross = lev * r
        turnover = (lev - lev.shift(1).fillna(0.0)).abs() * cost
```

Replace with:

```python
def run_xs_backtest(
    closes: dict[str, pd.Series],
    fundings: dict[str, pd.Series],
    cfg: ForecastConfig,
    *,
    turnover_cost_rate: pd.DataFrame | None = None,
) -> XSBookResult:
    """Causal dollar-neutral long-short book over the demeaned forecast.

    Per instrument: gross = leverage * return; honest costs = turnover
    `|Δlev| * rate` + funding `leverage*funding` (shorts receive funding).
    `rate` defaults to the flat scalar `fee_pct + slippage_pct`; when
    ``turnover_cost_rate`` (a per-instrument, per-day DataFrame) is supplied,
    each leg uses its own size-aware rate instead (the capacity stress test).
    Passing ``None`` is byte-identical to the flat path.
    """
    leverage = xs_leverage(closes, cfg)
    union = pd.DatetimeIndex(leverage.index)
    cost = cfg.fee_pct + cfg.slippage_pct
    rate_df = (
        turnover_cost_rate.reindex(index=union)
        if turnover_cost_rate is not None
        else None
    )

    per_net: dict[str, pd.Series] = {}
    net_cols: list[pd.Series] = []
    for sym, close in closes.items():
        lev = leverage[sym]
        r = close.pct_change().reindex(union)
        gross = lev * r
        dlev = (lev - lev.shift(1).fillna(0.0)).abs()
        if rate_df is not None and sym in rate_df.columns:
            turnover = dlev * rate_df[sym]
        else:
            turnover = dlev * cost
```

Leave the rest of the loop and the governor block unchanged.

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_xsmom_execution.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Run the full xsmom suite to confirm no behavioural drift**

Run: `poetry run pytest tests/ -k xsmom -v`
Expected: PASS (all existing xsmom tests still green)

- [ ] **Step 6: Commit**

```bash
ruff format analytics/xsmom/book.py tests/test_xsmom_execution.py
ruff check --fix analytics/xsmom/book.py tests/test_xsmom_execution.py
git add analytics/xsmom/book.py tests/test_xsmom_execution.py
git commit -m "feat(xsmom): run_xs_backtest optional per-day turnover_cost_rate (default-off byte-identical)"
git log --oneline -1
```

---

## Task 4: `load_daily_dollar_volumes` + `replay_xs_capacity` + `run_xs_with_costs`

**Files:**

- Modify: `analytics/xsmom/execution.py` (add `run_xs_with_costs`)
- Modify: `analytics/xsmom/replay.py`
- Test: `tests/test_xsmom_capacity_replay.py`

- [ ] **Step 1: Add `run_xs_with_costs` to `execution.py`**

```python
# append to analytics/xsmom/execution.py
from analytics.forecast.config import ForecastConfig
from analytics.xsmom.book import XSBookResult, run_xs_backtest, xs_leverage


def run_xs_with_costs(
    closes: dict[str, pd.Series],
    fundings: dict[str, pd.Series],
    cfg: ForecastConfig,
    exec_cfg: ExecutionCostConfig,
    adv: dict[str, pd.Series],
) -> XSBookResult:
    """Run the XS book under the size-aware cost model.

    Builds the leverage matrix once to derive `|Δlev|`, computes the per-day
    cost-rate, and feeds it to `run_xs_backtest`. `adv` is precomputed (it does
    not depend on `cfg.speeds`) so the caller can reuse it across trials.
    """
    leverage = xs_leverage(closes, cfg)
    rate = turnover_cost_rate(leverage, adv, exec_cfg)
    return run_xs_backtest(closes, fundings, cfg, turnover_cost_rate=rate)
```

- [ ] **Step 2: Write the failing replay test**

```python
# tests/test_xsmom_capacity_replay.py
from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd

from analytics.store.market_data import upsert_ohlcv
from analytics.store.schema import init_schema
from analytics.xsmom.execution import ExecutionCostConfig
from analytics.forecast.config import ForecastConfig
from analytics.xsmom.replay import load_daily_dollar_volumes, replay_xs_capacity


def _seed(conn: duckdb.DuckDBPyConnection, n: int = 400) -> list[str]:
    rng = np.random.default_rng(1)
    start_ms = 1_609_459_200_000  # 2021-01-01 UTC
    day_ms = 86_400_000
    syms = ["AAAUSDT", "BBBUSDT", "CCCUSDT"]
    for i, sym in enumerate(syms):
        steps = rng.normal(0.0, 0.02, n) + 0.0005 * (i - 1)
        close = 100.0 * np.exp(np.cumsum(steps))
        rows = pd.DataFrame(
            {
                "symbol": sym,
                "timeframe": "1d",
                "open_time": [start_ms + k * day_ms for k in range(n)],
                "open": close,
                "high": close * 1.01,
                "low": close * 0.99,
                "close": close,
                # thinner volume on the later symbols (more impact)
                "volume": rng.uniform(5e5, 1e6, n) / (i + 1),
                "taker_buy_volume": None,
            }
        )
        upsert_ohlcv(conn, rows)
    return syms


def test_load_daily_dollar_volumes_returns_volume_times_close() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    syms = _seed(conn, n=50)
    dvol = load_daily_dollar_volumes(conn, syms)
    assert set(dvol) == set(syms)
    # dollar volume is strictly positive and finite.
    for s in syms:
        assert (dvol[s] > 0).all()


def test_replay_xs_capacity_structure_and_cost_monotonicity() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    syms = _seed(conn)
    cfg = ForecastConfig()  # full 4-speed combined
    exec_cfg = ExecutionCostConfig(k=0.5)
    capitals = [1e5, 1e9]
    runs = replay_xs_capacity(conn, cfg, exec_cfg, capitals, symbols=syms)
    assert set(runs) == set(capitals)
    for C in capitals:
        assert "result" in runs[C] and "trials" in runs[C]
        assert "combined" in runs[C]["trials"]
    # More capital => more impact => weakly lower cumulative net return.
    lo = float(np.nansum(runs[1e5]["result"].portfolio_return))
    hi = float(np.nansum(runs[1e9]["result"].portfolio_return))
    assert hi <= lo
```

- [ ] **Step 3: Run test to verify it fails**

Run: `poetry run pytest tests/test_xsmom_capacity_replay.py -v`
Expected: FAIL with `ImportError: cannot import name 'load_daily_dollar_volumes'`

- [ ] **Step 4: Implement the replay additions**

```python
# add to analytics/xsmom/replay.py imports
import pandas as pd

from analytics.store.market_data import get_ohlcv
from analytics.xsmom.execution import (
    ExecutionCostConfig,
    dollar_adv,
    run_xs_with_costs,
)

_FAR_PAST = 0
_FAR_FUTURE = 9_999_999_999_999
```

```python
# add to analytics/xsmom/replay.py
def load_daily_dollar_volumes(
    conn: duckdb.DuckDBPyConnection,
    symbols: list[str],
) -> dict[str, pd.Series]:
    """Per-symbol daily dollar volume (`volume * close`), day-indexed.

    Read-only sibling of `load_daily_inputs`; the impact term's ADV source.
    Symbols with no OHLCV are silently skipped.
    """
    out: dict[str, pd.Series] = {}
    for sym in symbols:
        bars = get_ohlcv(conn, sym, "1d", _FAR_PAST, _FAR_FUTURE)
        if bars.empty:
            continue
        idx = pd.to_datetime(bars["open_time"], unit="ms", utc=True).dt.normalize()
        dv = pd.Series(
            (bars["volume"].to_numpy(dtype=float) * bars["close"].to_numpy(dtype=float)),
            index=idx,
        )
        out[sym] = dv[~dv.index.duplicated(keep="last")].sort_index()
    return out


def replay_xs_capacity(
    conn: duckdb.DuckDBPyConnection,
    cfg: ForecastConfig,
    exec_cfg: ExecutionCostConfig,
    capitals: list[float],
    symbols: list[str] | None = None,
) -> dict[float, dict[str, object]]:
    """Run the XS book + its DSR/PBO trial family under size-aware costs per capital.

    For each target capital `C`: rebuild each trial's own cost-rate (cost depends
    on that trial's `|Δlev|`), run the headline combined book and every
    single-speed sleeve. Returns `{C: {"result": XSBookResult, "trials": {...}}}`.
    The dollar-ADV is independent of `C`, so it is computed once.
    """
    syms = symbols if symbols is not None else load_universe()
    closes, fundings = load_daily_inputs(conn, syms)
    dvol = load_daily_dollar_volumes(conn, syms)
    adv = dollar_adv(dvol, exec_cfg.adv_window)

    out: dict[float, dict[str, object]] = {}
    for capital in capitals:
        ec = dataclasses.replace(exec_cfg, capital=capital)
        result = run_xs_with_costs(closes, fundings, cfg, ec, adv)
        trials: dict[str, np.ndarray] = {}
        for fast, slow, scalar in cfg.speeds:
            single = dataclasses.replace(cfg, speeds=((fast, slow, scalar),))
            trials[f"s{fast}_{slow}"] = run_xs_with_costs(
                closes, fundings, single, ec, adv
            ).portfolio_return
        trials["combined"] = result.portfolio_return
        out[capital] = {"result": result, "trials": trials}
    return out
```

- [ ] **Step 5: Run test to verify it passes**

Run: `poetry run pytest tests/test_xsmom_capacity_replay.py -v`
Expected: PASS (2 tests)

- [ ] **Step 6: Commit**

```bash
ruff format analytics/xsmom/execution.py analytics/xsmom/replay.py tests/test_xsmom_capacity_replay.py
ruff check --fix analytics/xsmom/execution.py analytics/xsmom/replay.py tests/test_xsmom_capacity_replay.py
git add analytics/xsmom/execution.py analytics/xsmom/replay.py tests/test_xsmom_capacity_replay.py
git commit -m "feat(xsmom): load_daily_dollar_volumes + replay_xs_capacity (per-capital cost re-scoring)"
git log --oneline -1
```

---

## Task 5: `evaluate_xs_capacity` report + `__init__` exports

**Files:**

- Modify: `analytics/xsmom/report.py`
- Modify: `analytics/xsmom/__init__.py`
- Test: `tests/test_xsmom_capacity_replay.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_xsmom_capacity_replay.py
from analytics.xsmom.report import evaluate_xs_capacity


def test_evaluate_xs_capacity_table_shape_and_gate() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    syms = _seed(conn)
    cfg = ForecastConfig()
    exec_cfg = ExecutionCostConfig(k=0.5)
    runs = replay_xs_capacity(conn, cfg, exec_cfg, [1e5, 1e9], symbols=syms)
    table = evaluate_xs_capacity(runs, cfg)
    assert list(table["capital"]) == [1e5, 1e9]
    for col in ("sharpe", "dsr", "pbo", "boot_lo", "boot_hi", "min_trl", "gate"):
        assert col in table.columns
    assert table["gate"].dtype == bool
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_xsmom_capacity_replay.py -k evaluate_xs_capacity -v`
Expected: FAIL with `ImportError: cannot import name 'evaluate_xs_capacity'`

- [ ] **Step 3: Implement `evaluate_xs_capacity`**

```python
# add to analytics/xsmom/report.py
def evaluate_xs_capacity(
    capacity_runs: dict[float, dict[str, object]],
    cfg: ForecastConfig,
) -> pd.DataFrame:
    """Capacity table: gate stats per target capital.

    Reuses `evaluate_xs` per capital (same DSR/PBO/boot-CI/MinTRL machinery).
    The `gate` column is the de-biased verdict
    `DSR >= 0.95 ∧ PBO <= 0.5 ∧ boot_lo > 0`. The diversification read is
    irrelevant here, so `trend_returns` is empty. Rows preserve insertion order
    of `capacity_runs`, so the headline = the largest capital with `gate=True`.
    """
    empty_trend: npt.NDArray[np.float64] = np.array([], dtype=np.float64)
    rows: list[dict[str, object]] = []
    for capital, payload in capacity_runs.items():
        result = payload["result"]  # type: ignore[assignment]
        trials = payload["trials"]  # type: ignore[assignment]
        rep = evaluate_xs(result, cfg, trial_returns=trials, trend_returns=empty_trend)  # type: ignore[arg-type]
        rows.append(
            {
                "capital": capital,
                "sharpe": rep.sharpe_annual,
                "dsr": rep.dsr,
                "pbo": rep.pbo,
                "boot_lo": rep.boot_lo,
                "boot_hi": rep.boot_hi,
                "min_trl": rep.min_trl,
                "gate": bool(
                    rep.dsr >= 0.95 and rep.pbo <= 0.5 and rep.boot_lo > 0.0
                ),
            }
        )
    return pd.DataFrame(rows)
```

- [ ] **Step 4: Add exports to `analytics/xsmom/__init__.py`**

Add the imports and `__all__` entries (keep `__all__` alphabetically sorted, as it currently is):

```python
from analytics.xsmom.execution import (
    ExecutionCostConfig,
    dollar_adv,
    run_xs_with_costs,
    turnover_cost_rate,
)
from analytics.xsmom.replay import (
    load_daily_dollar_volumes,
    replay_xs,
    replay_xs_capacity,
    replay_xs_trials,
)
from analytics.xsmom.report import XSReport, evaluate_xs, evaluate_xs_capacity
```

Add to `__all__`: `"ExecutionCostConfig"`, `"dollar_adv"`, `"evaluate_xs_capacity"`, `"load_daily_dollar_volumes"`, `"replay_xs_capacity"`, `"run_xs_with_costs"`, `"turnover_cost_rate"` (sorted into place).

- [ ] **Step 5: Run test + the xsmom suite**

Run: `poetry run pytest tests/test_xsmom_capacity_replay.py tests/test_xsmom_execution.py -v`
Expected: PASS (all)

- [ ] **Step 6: Commit**

```bash
ruff format analytics/xsmom/report.py analytics/xsmom/__init__.py tests/test_xsmom_capacity_replay.py
ruff check --fix analytics/xsmom/report.py analytics/xsmom/__init__.py tests/test_xsmom_capacity_replay.py
git add analytics/xsmom/report.py analytics/xsmom/__init__.py tests/test_xsmom_capacity_replay.py
git commit -m "feat(xsmom): evaluate_xs_capacity table + package exports"
git log --oneline -1
```

---

## Task 6: read-only driver + Makefile target

**Files:**

- Create: `tools/xsmom_capacity_audit.py`
- Modify: `Makefile`

- [ ] **Step 1: Write the driver**

```python
# tools/xsmom_capacity_audit.py
"""XS-momentum execution-realism capacity audit (P3) — read-only verdict.

Re-scores the XS sleeve's fixed (causal) position path under a per-instrument,
size-aware cost model (a-priori tiered half-spread + sqrt/linear market impact
vs trailing dollar-ADV) across a grid of target capital, and prints the AUM at
which the +1.375 edge decays below the de-biased gate, plus impact-k, spread-tier
and sqrt-vs-linear sensitivities.

Read-only — no writes, no schema changes.

Usage::

    PYTHONPATH=. poetry run python tools/xsmom_capacity_audit.py
"""

from __future__ import annotations

import argparse
import dataclasses
from pathlib import Path

import duckdb
import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.store import DEFAULT_DB_PATH
from analytics.universe import load_universe
from analytics.xsmom import (
    ExecutionCostConfig,
    evaluate_xs_capacity,
    replay_xs_capacity,
)

_CAPITALS = [1e5, 1e6, 5e6, 1e7, 2.5e7, 5e7, 1e8]


def _print_df(title: str, df: pd.DataFrame) -> None:
    print(f"\n=== {title} ===")
    if df.empty:
        print("(no rows)")
        return
    print(df.to_string(index=False))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="DuckDB path")
    args = parser.parse_args()

    conn = duckdb.connect(str(args.db), read_only=True)
    print(f"DB: {args.db}")
    syms = load_universe()
    cfg = ForecastConfig()

    # 1. Headline capacity sweep (base k, sqrt impact).
    base = ExecutionCostConfig()
    runs = replay_xs_capacity(conn, cfg, base, _CAPITALS, symbols=syms)
    _print_df("Capacity sweep (base k, sqrt)", evaluate_xs_capacity(runs, cfg))

    # 2. Impact-k sensitivity.
    for k in (0.05, 0.1, 0.2):
        ec = dataclasses.replace(base, k=k)
        runs_k = replay_xs_capacity(conn, cfg, ec, _CAPITALS, symbols=syms)
        _print_df(f"Capacity sweep (k={k})", evaluate_xs_capacity(runs_k, cfg))

    # 3. Spread-tier sensitivity (tighter / wider).
    tight = dataclasses.replace(base, major_bps=0.5, mid_bps=1.5, alt_bps=4.0)
    wide = dataclasses.replace(base, major_bps=2.0, mid_bps=6.0, alt_bps=16.0)
    _print_df(
        "Capacity sweep (tight spreads)",
        evaluate_xs_capacity(replay_xs_capacity(conn, cfg, tight, _CAPITALS, symbols=syms), cfg),
    )
    _print_df(
        "Capacity sweep (wide spreads)",
        evaluate_xs_capacity(replay_xs_capacity(conn, cfg, wide, _CAPITALS, symbols=syms), cfg),
    )

    # 4. sqrt vs linear impact form.
    lin = dataclasses.replace(base, impact="linear")
    _print_df(
        "Capacity sweep (linear impact)",
        evaluate_xs_capacity(replay_xs_capacity(conn, cfg, lin, _CAPITALS, symbols=syms), cfg),
    )

    conn.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Add the Makefile target**

Insert after the `buibui-xsmom-audit` target (around `Makefile:273`). **The recipe line
must be indented with a real TAB, not spaces** (shown here with spaces to satisfy the doc
linter):

```text
.PHONY: buibui-xsmom-capacity-audit
buibui-xsmom-capacity-audit:  ## P3: read-only XS execution-realism capacity stress test
    PYTHONPATH=. poetry run python tools/xsmom_capacity_audit.py
```

- [ ] **Step 3: Smoke-run the driver (read-only)**

Run: `make buibui-xsmom-capacity-audit`
Expected: prints the capacity sweep tables without error. (If `analytics.db` is absent locally, run `PYTHONPATH=. poetry run python tools/xsmom_capacity_audit.py --db <path>`; this is read-only and never writes.)

- [ ] **Step 4: Commit**

```bash
ruff format tools/xsmom_capacity_audit.py
ruff check --fix tools/xsmom_capacity_audit.py
git add tools/xsmom_capacity_audit.py Makefile
git commit -m "feat(xsmom): read-only capacity-audit driver + make target"
git log --oneline -1
```

---

## Task 7: run the audit, write the verdict, sync docs, DoD gate

**Files:**

- Create: `docs/audits/2026-06-20-p3-xsmom-capacity.md`
- Modify: `CLAUDE.md` (the `analytics/xsmom/` + `tools/` bullets)

- [ ] **Step 1: Run the full DoD gate**

```bash
make lint-py
make typecheck
make test
make test-regression
```

Expected: lint ✓, mypy ✓, pytest green, regression goldens **UNMOVED** (this change touches no existing behavioural path — the default-off byte-identical test guards `run_xs_backtest`).

- [ ] **Step 2: Run the capacity audit and capture the tables**

Run: `make buibui-xsmom-capacity-audit > /tmp/xsmom-capacity.txt 2>&1; cat /tmp/xsmom-capacity.txt`

- [ ] **Step 3: Write the verdict doc**

Create `docs/audits/2026-06-20-p3-xsmom-capacity.md` with the actual numbers from Step 2. Structure (fill in real values, do not invent):

```markdown
# P3 — XS-momentum execution-realism capacity stress test

**Date:** 2026-06-20
**Tool:** `make buibui-xsmom-capacity-audit` (`tools/xsmom_capacity_audit.py`), read-only.

## Question

Does the +1.375 XS edge survive realistic execution at size, and up to what AUM?

## Method

[Per-instrument size-aware cost: fee + a-priori tiered half-spread + k·√(|Δlev|·C/ADV);
ADV = trailing-30d-median(volume×close), causal. Capital swept; one fixed causal
position path re-scored per capital. Gate = DSR≥0.95 ∧ PBO≤0.5 ∧ boot_lo>0.]

## Result

[Paste the capacity sweep table. State the headline capacity = max AUM clearing the gate.]

## Sensitivities

[impact-k low/base/high; tight/wide spreads; sqrt vs linear — does the capacity verdict hold?]

## Verdict

[CLEARS to ~$X / FAILS at deployable size. Decision: green-light live wiring (sub-project #3)
OR re-scope. Tie back to the deploy thesis.]
```

- [ ] **Step 4: Update `CLAUDE.md`**

Append to the `analytics/xsmom/` bullet a note that `execution.py` (size-aware cost model) + `replay_xs_capacity` + `evaluate_xs_capacity` exist, and add a `tools/xsmom_capacity_audit.py` entry under `tools/` mirroring the `xsmom_audit.py` entry, citing the verdict doc.

- [ ] **Step 5: Lint markdown + commit**

```bash
npx markdownlint-cli2 "docs/audits/2026-06-20-p3-xsmom-capacity.md"
git add docs/audits/2026-06-20-p3-xsmom-capacity.md CLAUDE.md
git commit -m "docs(xsmom): capacity stress-test verdict + CLAUDE.md sync"
git log --oneline -1
```

---

## Self-review notes (already applied)

- **Spec coverage:** cost model (Tasks 1–3) · capacity replay (Task 4) · capacity report (Task 5) · driver + sensitivities (Task 6) · verdict + DoD + docs (Task 7). All spec sections map to a task.
- **Causality invariant:** `dollar_adv` shift tested (Task 1, `test_dollar_adv_is_causal_to_same_day_volume`); positions never change with capital (only net return is re-scored) — asserted structurally by the byte-identical default-off path (Task 3) and the per-capital re-scoring design (Task 4).
- **Default-off byte-identical:** Task 3 `test_run_xs_backtest_default_off_is_byte_identical` + `test_run_xs_backtest_constant_rate_matches_scalar_path`.
- **Type consistency:** `ExecutionCostConfig`, `dollar_adv`, `turnover_cost_rate`, `run_xs_with_costs`, `load_daily_dollar_volumes`, `replay_xs_capacity`, `evaluate_xs_capacity` names are used identically across tasks and `__init__` exports.
