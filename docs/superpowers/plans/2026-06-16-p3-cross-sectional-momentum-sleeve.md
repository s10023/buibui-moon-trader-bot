# P3 Cross-Sectional Momentum Sleeve Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standalone, read-only cross-sectional momentum (XS-mom) sleeve that ranks the N3 universe by the demeaned EWMAC forecast and runs a dollar-neutral, vol-scaled, causal long-short book — to measure whether expressing alt breadth as *relative strength* beats parallel absolute trend, with a de-biased DSR/PBO/bootstrap verdict.

**Architecture:** New sibling package `analytics/xsmom/` mirroring `analytics/forecast/`'s shape (`book` / `replay` / `report`). Each day: take the existing capped multi-speed EWMAC forecast per instrument (`combine_forecasts`), subtract the cross-sectional mean over active instruments (dollar-neutral), `.shift(1)` for causality, then vol-target each leg and aggregate to a portfolio book governed by the existing 20%-vol governor. Reuses `ForecastConfig`, `ewmac`, `vol`, `forecast.replay.load_daily_inputs`, `portfolio.metrics`, and `research_guards` unchanged — additive, no engine/schema/golden touch.

**Tech Stack:** Python 3.11+, pandas, numpy, duckdb, pytest. Pure functions over a DuckDB connection (read-only); Carver continuous-forecast conventions.

---

## File Structure

- Create `analytics/xsmom/__init__.py` — eager re-exports (public entry).
- Create `analytics/xsmom/book.py` — `XSBookResult`, `xs_forecasts`, `xs_demeaned_forecasts`, `xs_leverage`, `run_xs_backtest`, `equity_curve`. The causal core + portfolio aggregation.
- Create `analytics/xsmom/replay.py` — `replay_xs`, `replay_xs_trials`. The read-only DB front door (reuses `forecast.replay.load_daily_inputs`).
- Create `analytics/xsmom/report.py` — `XSReport`, `evaluate_xs`. Headline metrics + DSR/PBO/boot-CI/MinTRL + `corr_to_trend` / `trend_sharpe`.
- Create `tools/xsmom_audit.py` — read-only driver (`build_xs_report_row`, `main`).
- Modify `Makefile` — add `buibui-xsmom-audit` target + `.PHONY` line.
- Create `tests/xsmom/__init__.py` (empty) + `tests/xsmom/test_book.py`, `tests/xsmom/test_replay.py`, `tests/xsmom/test_report.py`, `tests/xsmom/test_audit_cli.py`.
- Create `docs/audits/2026-06-16-p3-xsmom-sleeve.md` — the verdict (Task 7).
- Modify `CLAUDE.md`, `README.md`, the MEMORY index (Task 8).

Conventions to match (verified against `analytics/forecast/`): `from __future__ import annotations` at top of every module; full type annotations (mypy strict); `_per_period_sharpe`/`_ann_sharpe` are 3-line module-private helpers re-implemented per module (the forecast package does the same rather than cross-importing privates).

---

### Task 1: Package skeleton + forecast matrix + cross-sectional demean

**Files:**

- Create: `analytics/xsmom/__init__.py`
- Create: `analytics/xsmom/book.py`
- Create: `tests/xsmom/__init__.py`
- Test: `tests/xsmom/test_book.py`

- [ ] **Step 1: Create the empty test package marker**

Create `tests/xsmom/__init__.py` with a single newline (empty file).

- [ ] **Step 2: Create a minimal `analytics/xsmom/__init__.py`**

```python
"""Cross-sectional momentum sleeve (P3) — demeaned EWMAC relative-strength book."""

from __future__ import annotations

from analytics.xsmom.book import (
    XSBookResult,
    equity_curve,
    run_xs_backtest,
    xs_demeaned_forecasts,
    xs_forecasts,
    xs_leverage,
)

__all__ = [
    "XSBookResult",
    "equity_curve",
    "run_xs_backtest",
    "xs_demeaned_forecasts",
    "xs_forecasts",
    "xs_leverage",
]
```

(`replay`/`report` symbols are added to `__all__` in Task 6 — this keeps Task 1 importable on its own; the full export list lands once those modules exist.)

- [ ] **Step 3: Write the failing test**

```python
# tests/xsmom/test_book.py
from __future__ import annotations

import numpy as np
import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.xsmom.book import xs_demeaned_forecasts, xs_forecasts


def _closes() -> dict[str, pd.Series]:
    idx = pd.date_range("2021-01-01", periods=500, freq="D")
    return {
        "STRONG": pd.Series(np.linspace(100.0, 400.0, 500), index=idx),
        "FLAT": pd.Series(np.full(500, 200.0), index=idx),
        "WEAK": pd.Series(np.linspace(400.0, 100.0, 500), index=idx),
    }


def test_xs_forecasts_aligned_to_union_index() -> None:
    closes = _closes()
    f = xs_forecasts(closes, ForecastConfig())
    assert list(f.columns) == ["STRONG", "FLAT", "WEAK"]
    assert len(f) == 500
    # warmed-up tail is fully populated (all three instruments share the index)
    assert f.iloc[-1].notna().all()


def test_demeaned_forecast_rows_sum_to_zero_over_active() -> None:
    closes = _closes()
    g = xs_demeaned_forecasts(closes, ForecastConfig())
    warm = g.dropna(how="any")
    assert len(warm) > 0
    # dollar-neutral by construction: each active row sums to ~0
    np.testing.assert_allclose(warm.sum(axis=1).to_numpy(), 0.0, atol=1e-9)
    # the strong uptrend carries the highest (positive) demeaned forecast,
    # the weak downtrend the lowest (negative)
    last = g.iloc[-1]
    assert last["STRONG"] > last["FLAT"] > last["WEAK"]
```

- [ ] **Step 4: Run test to verify it fails**

Run: `PYTHONPATH=. poetry run pytest tests/xsmom/test_book.py -v`
Expected: FAIL — `ImportError: cannot import name 'xs_forecasts'` (and `xs_demeaned_forecasts`).

- [ ] **Step 5: Write the minimal implementation**

```python
# analytics/xsmom/book.py
"""Per-instrument cross-sectional forecasts, leverage, and portfolio aggregation.

All sizing is causal: the position held during day `d` is sized from information
through day `d-1` only. The cross-sectional demean is a same-day reduction over
causal forecasts; the `.shift(1)` is applied AFTER demeaning, BEFORE sizing.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.forecast.ewmac import combine_forecasts
from analytics.forecast.vol import ew_return_vol


def _union_index(closes: dict[str, pd.Series]) -> pd.DatetimeIndex:
    union = pd.DatetimeIndex([])
    for s in closes.values():
        union = union.union(pd.DatetimeIndex(s.index))
    return union.sort_values()


def xs_forecasts(closes: dict[str, pd.Series], cfg: ForecastConfig) -> pd.DataFrame:
    """Raw combined EWMAC forecasts per instrument, aligned to the union daily index.

    Columns = symbols, index = sorted union of all instrument dates. NaN where an
    instrument has no (or not-yet-warmed-up) forecast on a given day. Causal: each
    column is `combine_forecasts(...)`, which uses only closes through each day.
    """
    union = _union_index(closes)
    cols: dict[str, pd.Series] = {}
    for sym, close in closes.items():
        f = combine_forecasts(
            close, cfg.speeds, cfg.fdm, cfg.vol_span, cfg.cap, weights=cfg.weights
        )
        cols[sym] = f.reindex(union)
    return pd.DataFrame(cols, index=union)


def xs_demeaned_forecasts(
    closes: dict[str, pd.Series], cfg: ForecastConfig
) -> pd.DataFrame:
    """Cross-sectionally demeaned forecasts (relative strength).

    `g_i(d) = f_i(d) - mean_{j in active(d)} f_j(d)`; the row mean skips NaN so it
    is taken over the active instruments only. Each active row sums to ~0
    (dollar-neutral). Not yet shifted — see `xs_leverage`.
    """
    f = xs_forecasts(closes, cfg)
    return f.sub(f.mean(axis=1), axis=0)


@dataclass(frozen=True)
class XSBookResult:
    daily_index: pd.DatetimeIndex
    portfolio_return: np.ndarray  # net, post-governor (NaN-free; warm-up = 0.0)
    pre_governor_return: np.ndarray
    governor: np.ndarray  # NaN for the first gov_window warm-up bars
    active_count: np.ndarray
    per_instrument_net: dict[str, pd.Series]
```

- [ ] **Step 6: Run test to verify it passes**

Run: `PYTHONPATH=. poetry run pytest tests/xsmom/test_book.py -v`
Expected: PASS (both tests).

- [ ] **Step 7: Commit**

```bash
git add analytics/xsmom/__init__.py analytics/xsmom/book.py tests/xsmom/__init__.py tests/xsmom/test_book.py
git commit -m "feat(xsmom): cross-sectional forecast matrix + demean core"
```

---

### Task 2: Causal vol-parity leverage matrix

**Files:**

- Modify: `analytics/xsmom/book.py`
- Test: `tests/xsmom/test_book.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/xsmom/test_book.py`:

```python
def test_xs_leverage_sign_long_strong_short_weak() -> None:
    from analytics.xsmom.book import xs_leverage

    lev = xs_leverage(_closes(), ForecastConfig())
    last = lev.iloc[-1]
    # strong uptrend held long, weak downtrend held short
    assert last["STRONG"] > 0.0
    assert last["WEAK"] < 0.0


def test_xs_leverage_is_causal_no_lookahead() -> None:
    from analytics.xsmom.book import xs_leverage

    closes = _closes()
    base = xs_leverage(closes, ForecastConfig())

    # Perturb a MIDDLE bar of ONE instrument. Leverage at index k is sized from
    # demeaned forecasts through k-1, so close[k] must not affect leverage[:k+1]
    # for ANY column (the cross-sectional demean couples instruments).
    k = 250
    bumped = {s: c.copy() for s, c in closes.items()}
    bumped["STRONG"].iloc[k] *= 1.5
    after = xs_leverage(bumped, ForecastConfig())

    pd.testing.assert_frame_equal(
        base.iloc[: k + 1], after.iloc[: k + 1], check_names=False
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. poetry run pytest tests/xsmom/test_book.py -k leverage -v`
Expected: FAIL — `ImportError: cannot import name 'xs_leverage'`.

- [ ] **Step 3: Write the implementation**

Append to `analytics/xsmom/book.py` (after `xs_demeaned_forecasts`):

```python
def xs_leverage(closes: dict[str, pd.Series], cfg: ForecastConfig) -> pd.DataFrame:
    """Causal cross-sectional (demeaned) vol-parity leverage matrix.

    Demean the forecast across active instruments (dollar-neutral), shift one day
    (position on day `d` uses info through `d-1`), then vol-target each leg:
    `leverage_i = (g_i_shifted / 10) * (vol_target / vol_ann_i)`. The `/10` mirrors
    the trend sleeve so magnitudes are comparable; the absolute level is governed
    downstream. Columns = symbols, index = union daily index.
    """
    demeaned = xs_demeaned_forecasts(closes, cfg)
    demeaned_shifted = demeaned.shift(1)
    union = pd.DatetimeIndex(demeaned.index)
    ann = np.sqrt(cfg.annualization_days)

    lev_cols: dict[str, pd.Series] = {}
    for sym, close in closes.items():
        vol_ann = ew_return_vol(close, cfg.vol_span).mul(ann).reindex(union)
        lev = (demeaned_shifted[sym] / 10.0) * (cfg.vol_target_annual / vol_ann)
        lev_cols[sym] = lev.replace([np.inf, -np.inf], np.nan)
    return pd.DataFrame(lev_cols, index=union)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. poetry run pytest tests/xsmom/test_book.py -v`
Expected: PASS (all four tests).

- [ ] **Step 5: Commit**

```bash
git add analytics/xsmom/book.py tests/xsmom/test_book.py
git commit -m "feat(xsmom): causal vol-parity leverage matrix (demean->shift->vol-target)"
```

---

### Task 3: Portfolio book + costs + governor

**Files:**

- Modify: `analytics/xsmom/book.py`
- Test: `tests/xsmom/test_book.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/xsmom/test_book.py`:

```python
def _fundings(closes: dict[str, pd.Series]) -> dict[str, pd.Series]:
    return {s: pd.Series(0.0, index=c.index) for s, c in closes.items()}


def test_run_xs_backtest_long_winner_short_loser_nets_positive() -> None:
    from analytics.xsmom.book import equity_curve, run_xs_backtest

    closes = _closes()
    res = run_xs_backtest(closes, _fundings(closes), ForecastConfig())
    assert res.portfolio_return.shape[0] == 500
    assert not np.isnan(res.portfolio_return).any()
    # long the strong / short the weak over a clean cross-section compounds up
    assert equity_curve(res).iloc[-1] > 1.0
    assert res.active_count.max() == 3


def test_run_xs_backtest_short_leg_receives_funding() -> None:
    from analytics.xsmom.book import run_xs_backtest

    closes = _closes()
    pos_fund = {s: pd.Series(0.001, index=c.index) for s, c in closes.items()}
    res = run_xs_backtest(closes, pos_fund, ForecastConfig())
    # WEAK is held short; positive funding on a short is a CREDIT -> net funding
    # cost on that leg is negative over the warmed-up tail.
    weak_net = res.per_instrument_net["WEAK"].dropna()
    no_fund = run_xs_backtest(closes, _fundings(closes), ForecastConfig())
    weak_net_nf = no_fund.per_instrument_net["WEAK"].dropna()
    # with positive funding the short leg nets HIGHER than with zero funding
    assert weak_net.sum() > weak_net_nf.sum()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. poetry run pytest tests/xsmom/test_book.py -k "backtest or funding" -v`
Expected: FAIL — `ImportError: cannot import name 'run_xs_backtest'`.

- [ ] **Step 3: Write the implementation**

Append to `analytics/xsmom/book.py`:

```python
def run_xs_backtest(
    closes: dict[str, pd.Series],
    fundings: dict[str, pd.Series],
    cfg: ForecastConfig,
) -> XSBookResult:
    """Causal dollar-neutral long-short book over the demeaned forecast.

    Per instrument: gross = leverage * return; honest costs = turnover
    `|Δlev|*(fee+slip)` + funding `leverage*funding` (shorts receive funding).
    Aggregate = SUM of legs (long-short portfolio P&L; the level is set by the
    causal 20%-vol governor, so sum-vs-mean is only a scale it absorbs).
    """
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
        fund = (
            fundings.get(sym, pd.Series(0.0, index=close.index))
            .reindex(union)
            .fillna(0.0)
        )
        funding_cost = lev * fund
        net = gross - turnover - funding_cost
        per_net[sym] = net
        net_cols.append(net)

    net_mat = pd.concat(net_cols, axis=1)
    active = net_mat.notna().sum(axis=1)
    pre = net_mat.sum(axis=1)  # all-NaN warm-up rows -> 0.0 (skipna)

    ann = np.sqrt(cfg.annualization_days)
    trailing_vol = (
        pre.rolling(cfg.gov_window, min_periods=cfg.gov_window).std().shift(1) * ann
    )
    g = (cfg.vol_target_annual / trailing_vol).clip(cfg.g_min, cfg.g_max)
    port = g.fillna(0.0) * pre

    return XSBookResult(
        daily_index=union,
        portfolio_return=port.to_numpy(dtype=np.float64),
        pre_governor_return=pre.to_numpy(dtype=np.float64),
        governor=g.to_numpy(dtype=np.float64),
        active_count=active.to_numpy(dtype=np.int64),
        per_instrument_net=per_net,
    )


def equity_curve(result: XSBookResult) -> pd.Series:
    """Compounding equity curve (starts at 1.0) for portfolio.metrics."""
    r = pd.Series(result.portfolio_return, index=result.daily_index)
    return (1.0 + r).cumprod()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. poetry run pytest tests/xsmom/test_book.py -v`
Expected: PASS (all six tests).

- [ ] **Step 5: Commit**

```bash
git add analytics/xsmom/book.py tests/xsmom/test_book.py
git commit -m "feat(xsmom): dollar-neutral long-short book with honest costs + vol governor"
```

---

### Task 4: Read-only DB front door (replay)

**Files:**

- Create: `analytics/xsmom/replay.py`
- Test: `tests/xsmom/test_replay.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/xsmom/test_replay.py
from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.store import init_schema
from analytics.store.market_data import upsert_ohlcv
from analytics.xsmom.book import XSBookResult
from analytics.xsmom.replay import replay_xs, replay_xs_trials

_DAY = 86_400_000


def _seed(conn: duckdb.DuckDBPyConnection, symbol: str, slope: float) -> None:
    t0 = 1_600_000_000_000
    rows = [
        {
            "symbol": symbol,
            "timeframe": "1d",
            "open_time": t0 + i * _DAY,
            "open": 100.0 + slope * i,
            "high": 101.0 + slope * i,
            "low": 99.0 + slope * i,
            "close": 100.0 + slope * i,
            "volume": 1000.0,
            "taker_buy_volume": 500.0,
        }
        for i in range(320)
    ]
    upsert_ohlcv(conn, pd.DataFrame(rows))


def test_replay_xs_returns_book_result() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    _seed(conn, "AAAUSDT", 1.0)
    _seed(conn, "BBBUSDT", -0.5)
    res = replay_xs(conn, ForecastConfig(), symbols=["AAAUSDT", "BBBUSDT"])
    assert isinstance(res, XSBookResult)
    assert res.portfolio_return.shape[0] > 0
    assert not np.isnan(res.portfolio_return).any()


def test_replay_xs_trials_has_per_speed_plus_combined() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    _seed(conn, "AAAUSDT", 1.0)
    _seed(conn, "BBBUSDT", -0.5)
    trials = replay_xs_trials(conn, ForecastConfig(), symbols=["AAAUSDT", "BBBUSDT"])
    assert set(trials) == {"s8_32", "s16_64", "s32_128", "s64_256", "combined"}
    for v in trials.values():
        assert isinstance(v, np.ndarray)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. poetry run pytest tests/xsmom/test_replay.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'analytics.xsmom.replay'`.

- [ ] **Step 3: Write the implementation**

```python
# analytics/xsmom/replay.py
"""Read-only DuckDB front door for the cross-sectional momentum sleeve.

Reuses the trend sleeve's `load_daily_inputs` (1d closes + summed daily funding)
and runs the XS book. The only module in `analytics/xsmom/` that touches the DB;
never writes.
"""

from __future__ import annotations

import dataclasses

import duckdb
import numpy as np

from analytics.forecast.config import ForecastConfig
from analytics.forecast.replay import load_daily_inputs
from analytics.universe import load_universe
from analytics.xsmom.book import XSBookResult, run_xs_backtest


def replay_xs(
    conn: duckdb.DuckDBPyConnection,
    cfg: ForecastConfig,
    symbols: list[str] | None = None,
) -> XSBookResult:
    """Load the universe's 1d inputs and run the XS book (read-only)."""
    syms = symbols if symbols is not None else load_universe()
    closes, fundings = load_daily_inputs(conn, syms)
    return run_xs_backtest(closes, fundings, cfg)


def replay_xs_trials(
    conn: duckdb.DuckDBPyConnection,
    cfg: ForecastConfig,
    symbols: list[str] | None = None,
) -> dict[str, np.ndarray]:
    """Daily XS portfolio returns per single-speed sleeve + the combined book.

    The honest multiple-testing family for DSR/PBO. Keys:
    `s{fast}_{slow}` per speed in `cfg.speeds`, plus `combined`.
    """
    syms = symbols if symbols is not None else load_universe()
    closes, fundings = load_daily_inputs(conn, syms)

    trials: dict[str, np.ndarray] = {}
    for fast, slow, scalar in cfg.speeds:
        single_cfg = dataclasses.replace(cfg, speeds=((fast, slow, scalar),))
        result = run_xs_backtest(closes, fundings, single_cfg)
        trials[f"s{fast}_{slow}"] = result.portfolio_return

    combined = run_xs_backtest(closes, fundings, cfg)
    trials["combined"] = combined.portfolio_return
    return trials
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. poetry run pytest tests/xsmom/test_replay.py -v`
Expected: PASS (both tests).

- [ ] **Step 5: Commit**

```bash
git add analytics/xsmom/replay.py tests/xsmom/test_replay.py
git commit -m "feat(xsmom): read-only replay front door (replay_xs + trials family)"
```

---

### Task 5: Verdict report (DSR/PBO/boot-CI/MinTRL + corr-to-trend)

**Files:**

- Create: `analytics/xsmom/report.py`
- Test: `tests/xsmom/test_report.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/xsmom/test_report.py
"""Tests for analytics.xsmom.report — XSReport + evaluate_xs."""

from __future__ import annotations

import numpy as np
import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.xsmom.book import XSBookResult
from analytics.xsmom.report import XSReport, evaluate_xs


def _result(returns: np.ndarray) -> XSBookResult:
    idx = pd.date_range("2021-01-01", periods=len(returns), freq="D")
    return XSBookResult(
        daily_index=idx,
        portfolio_return=returns,
        pre_governor_return=returns,
        governor=np.ones(len(returns)),
        active_count=np.full(len(returns), 2, dtype=np.int64),
        per_instrument_net={"AAA": pd.Series(returns, index=idx)},
    )


def test_report_shape_and_corr_to_trend() -> None:
    rng = np.random.default_rng(0)
    r = 0.001 + 0.01 * rng.standard_normal(800)
    res = _result(r)
    trials = {"combined": r, "s8_32": r * 1.1, "s64_256": r * 0.2}
    trend = 0.0008 + 0.01 * rng.standard_normal(800)
    rep = evaluate_xs(res, ForecastConfig(), trial_returns=trials, trend_returns=trend)
    assert isinstance(rep, XSReport)
    assert rep.n_obs == 800
    assert rep.boot_lo <= rep.sharpe_annual <= rep.boot_hi
    assert 0.0 <= rep.pbo <= 1.0
    assert -1.0 <= rep.corr_to_trend <= 1.0
    assert rep.trend_sharpe != 0.0


def test_corr_to_trend_identical_is_one() -> None:
    rng = np.random.default_rng(1)
    r = 0.001 + 0.01 * rng.standard_normal(400)
    res = _result(r)
    rep = evaluate_xs(
        res, ForecastConfig(), trial_returns={"combined": r}, trend_returns=r
    )
    assert rep.corr_to_trend > 0.99


def test_corr_to_trend_anticorrelated_is_negative() -> None:
    rng = np.random.default_rng(2)
    r = 0.001 + 0.01 * rng.standard_normal(400)
    res = _result(r)
    rep = evaluate_xs(
        res, ForecastConfig(), trial_returns={"combined": r}, trend_returns=-r
    )
    assert rep.corr_to_trend < -0.99


def test_flat_returns_degenerate_to_zero() -> None:
    res = _result(np.zeros(500))
    rep = evaluate_xs(
        res,
        ForecastConfig(),
        trial_returns={"combined": np.zeros(500)},
        trend_returns=np.zeros(500),
    )
    assert rep.sharpe_annual == 0.0
    assert rep.corr_to_trend == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. poetry run pytest tests/xsmom/test_report.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'analytics.xsmom.report'`.

- [ ] **Step 3: Write the implementation**

```python
# analytics/xsmom/report.py
"""Assemble the cross-sectional momentum verdict: headline metrics + guards.

Pure over an XSBookResult plus the candidate trials' daily returns (the honest
multiple-testing family for DSR/PBO) and the trend sleeve's daily returns (for
the diversification read). Mirrors `analytics.forecast.report` and adds
`corr_to_trend` / `trend_sharpe`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from analytics.forecast.config import ForecastConfig
from analytics.research_guards import (
    block_bootstrap_ci,
    cscv_pbo,
    deflated_sharpe_ratio,
    min_track_record_length,
)
from analytics.xsmom.book import XSBookResult, equity_curve
from portfolio import metrics


def _per_period_sharpe(r: npt.NDArray[np.float64]) -> float:
    if len(r) < 2:
        return 0.0
    sd = float(np.std(r, ddof=1))
    if sd < 1e-12:
        return 0.0
    return float(np.mean(r) / sd)


def _ann_sharpe(r: npt.NDArray[np.float64], ann: float) -> float:
    return _per_period_sharpe(r) * ann


def _aligned_corr(
    a: npt.NDArray[np.float64], b: npt.NDArray[np.float64]
) -> float:
    """Pearson corr over the common tail, excluding joint dead warm-up (0, 0)."""
    n = min(len(a), len(b))
    if n < 2:
        return 0.0
    x = np.asarray(a[-n:], dtype=np.float64)
    y = np.asarray(b[-n:], dtype=np.float64)
    live = ~((x == 0.0) & (y == 0.0))
    x, y = x[live], y[live]
    if len(x) < 2 or float(np.std(x)) < 1e-12 or float(np.std(y)) < 1e-12:
        return 0.0
    return float(np.corrcoef(x, y)[0, 1])


@dataclass(frozen=True)
class XSReport:
    """Headline metrics + guards + the diversification read for the XS verdict."""

    sharpe_annual: float
    sortino_annual: float
    max_dd: float
    calmar: float
    annual_return: float
    annual_vol: float
    n_obs: int
    dsr: float
    pbo: float
    boot_lo: float
    boot_hi: float
    min_trl: float
    corr_to_trend: float
    trend_sharpe: float


def evaluate_xs(
    result: XSBookResult,
    cfg: ForecastConfig,
    trial_returns: dict[str, npt.NDArray[np.float64]],
    trend_returns: npt.NDArray[np.float64],
) -> XSReport:
    """Compute all XS metrics + research-guard stamps + trend diversification.

    `trial_returns` is the honest multiple-testing family (per-speed XS sleeves +
    combined) — the same set `replay_xs_trials` produces. `trend_returns` is the
    trend sleeve's daily portfolio returns on the same universe/window.
    """
    r = result.portfolio_return
    curve = equity_curve(result)
    ann = math.sqrt(cfg.annualization_days)

    sr_d = _per_period_sharpe(r)
    trial_srs = [_per_period_sharpe(v) for v in trial_returns.values()]

    min_len = min((len(v) for v in trial_returns.values()), default=0)
    if min_len >= 28 and len(trial_returns) >= 2:
        mat = np.column_stack([v[-min_len:] for v in trial_returns.values()])
        pbo = cscv_pbo(mat).pbo
    else:
        pbo = float("nan")

    if sr_d != 0.0:

        def _stat_fn(x: npt.NDArray[np.float64]) -> float:
            return _ann_sharpe(x, ann)

        boot = block_bootstrap_ci(r, stat_fn=_stat_fn, seed=7)
        boot_lo, boot_hi = boot.lo, boot.hi
        dsr = deflated_sharpe_ratio(sr_d, len(r), trial_srs=trial_srs)
        min_trl = min_track_record_length(sr_d, target_sr=1.0 / ann, confidence=0.95)
    else:
        boot_lo = boot_hi = dsr = 0.0
        min_trl = float("inf")

    if len(trend_returns) >= 2:
        trend_curve = (1.0 + pd.Series(trend_returns)).cumprod()
        trend_sharpe = metrics.sharpe(trend_curve)
    else:
        trend_sharpe = 0.0
    corr_to_trend = _aligned_corr(r, trend_returns)

    return XSReport(
        sharpe_annual=metrics.sharpe(curve),
        sortino_annual=metrics.sortino(curve),
        max_dd=metrics.max_drawdown(curve),
        calmar=metrics.calmar(curve),
        annual_return=metrics.annual_return(curve),
        annual_vol=metrics.annual_vol(curve),
        n_obs=len(r),
        dsr=dsr,
        pbo=pbo,
        boot_lo=boot_lo,
        boot_hi=boot_hi,
        min_trl=min_trl,
        corr_to_trend=corr_to_trend,
        trend_sharpe=trend_sharpe,
    )
```

Add the missing `import pandas as pd` at the top of `report.py` (used for the trend curve). Final import block:

```python
import math
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
import pandas as pd
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. poetry run pytest tests/xsmom/test_report.py -v`
Expected: PASS (all four tests).

- [ ] **Step 5: Commit**

```bash
git add analytics/xsmom/report.py tests/xsmom/test_report.py
git commit -m "feat(xsmom): XS verdict report (DSR/PBO/boot-CI/MinTRL + corr-to-trend)"
```

---

### Task 6: Audit driver + Makefile + package exports

**Files:**

- Modify: `analytics/xsmom/__init__.py`
- Create: `tools/xsmom_audit.py`
- Modify: `Makefile`
- Test: `tests/xsmom/test_audit_cli.py`

- [ ] **Step 1: Extend `analytics/xsmom/__init__.py` exports**

Replace the file with the full export set now that `replay`/`report` exist:

```python
"""Cross-sectional momentum sleeve (P3) — demeaned EWMAC relative-strength book."""

from __future__ import annotations

from analytics.xsmom.book import (
    XSBookResult,
    equity_curve,
    run_xs_backtest,
    xs_demeaned_forecasts,
    xs_forecasts,
    xs_leverage,
)
from analytics.xsmom.replay import replay_xs, replay_xs_trials
from analytics.xsmom.report import XSReport, evaluate_xs

__all__ = [
    "XSBookResult",
    "XSReport",
    "equity_curve",
    "evaluate_xs",
    "replay_xs",
    "replay_xs_trials",
    "run_xs_backtest",
    "xs_demeaned_forecasts",
    "xs_forecasts",
    "xs_leverage",
]
```

- [ ] **Step 2: Write the failing test**

```python
# tests/xsmom/test_audit_cli.py
from __future__ import annotations

import duckdb
import pandas as pd

from analytics.store import init_schema
from analytics.store.market_data import upsert_ohlcv
from tools.xsmom_audit import build_xs_report_row

_DAY = 86_400_000


def _seed(conn: duckdb.DuckDBPyConnection, symbol: str, slope: float) -> None:
    t0 = 1_600_000_000_000
    rows = [
        {
            "symbol": symbol,
            "timeframe": "1d",
            "open_time": t0 + i * _DAY,
            "open": 100.0 + slope * i,
            "high": 101.0 + slope * i,
            "low": 99.0 + slope * i,
            "close": 100.0 + slope * i,
            "volume": 1000.0,
            "taker_buy_volume": 500.0,
        }
        for i in range(320)
    ]
    upsert_ohlcv(conn, pd.DataFrame(rows))


def test_build_xs_report_row_returns_dict() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    _seed(conn, "AAAUSDT", 1.0)
    _seed(conn, "BBBUSDT", -0.5)
    row = build_xs_report_row(
        conn, "label", symbols=["AAAUSDT", "BBBUSDT"], slippage_bps=2.0
    )
    assert row["label"] == "label"
    for col in ("sharpe", "max_dd", "pbo", "dsr", "corr_to_trend", "trend_sharpe"):
        assert col in row
```

- [ ] **Step 3: Run test to verify it fails**

Run: `PYTHONPATH=. poetry run pytest tests/xsmom/test_audit_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.xsmom_audit'`.

- [ ] **Step 4: Write the driver**

```python
# tools/xsmom_audit.py
"""Cross-sectional momentum sleeve audit (P3) — read-only verdict.

Replays the demeaned-EWMAC relative-strength book across the N3 universe (1d) and
prints: a breadth contrast (universe vs majors-only), a cost-sensitivity sweep,
the per-speed XS Sharpes, and the diversification read (correlation to the trend
sleeve) — each with DSR/PBO/bootstrap-CI/MinTRL stamps.

Read-only — no writes, no schema changes.

Usage::

    PYTHONPATH=. poetry run python tools/xsmom_audit.py
    PYTHONPATH=. poetry run python tools/xsmom_audit.py --majors BTCUSDT,ETHUSDT,SOLUSDT
"""

from __future__ import annotations

import argparse
import dataclasses
import math
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from analytics.forecast import ForecastConfig, replay_universe
from analytics.store import DEFAULT_DB_PATH
from analytics.universe import load_universe
from analytics.xsmom import evaluate_xs, replay_xs, replay_xs_trials

_MAJORS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]


def build_xs_report_row(
    conn: duckdb.DuckDBPyConnection,
    label: str,
    symbols: list[str],
    slippage_bps: float,
) -> dict[str, object]:
    cfg = dataclasses.replace(ForecastConfig(), slippage_pct=slippage_bps / 10_000.0)
    result = replay_xs(conn, cfg, symbols=symbols)
    trials = replay_xs_trials(conn, cfg, symbols=symbols)
    trend = replay_universe(conn, cfg, symbols=symbols).portfolio_return
    rep = evaluate_xs(result, cfg, trial_returns=trials, trend_returns=trend)
    return {
        "label": label,
        "n_inst": len(result.per_instrument_net),
        "days": rep.n_obs,
        "sharpe": rep.sharpe_annual,
        "sortino": rep.sortino_annual,
        "max_dd": rep.max_dd,
        "ann_ret": rep.annual_return,
        "ann_vol": rep.annual_vol,
        "dsr": rep.dsr,
        "pbo": rep.pbo,
        "boot_lo": rep.boot_lo,
        "boot_hi": rep.boot_hi,
        "min_trl": rep.min_trl,
        "corr_to_trend": rep.corr_to_trend,
        "trend_sharpe": rep.trend_sharpe,
    }


def _per_speed_xs_sharpes(
    conn: duckdb.DuckDBPyConnection, symbols: list[str]
) -> pd.DataFrame:
    cfg = ForecastConfig()
    ann = math.sqrt(cfg.annualization_days)
    trials = replay_xs_trials(conn, cfg, symbols=symbols)
    rows = []
    for name, r in trials.items():
        sd = float(np.std(r, ddof=1)) if len(r) > 1 else 0.0
        sr = (float(np.mean(r)) / sd * ann) if sd > 1e-12 else 0.0
        rows.append({"trial": name, "sharpe": sr})
    return pd.DataFrame(rows)


def _print_df(title: str, df: pd.DataFrame) -> None:
    print(f"\n=== {title} ===")
    if df.empty:
        print("(no rows)")
        return
    print(df.to_string(index=False, float_format=lambda x: f"{x:+.3f}"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="DuckDB path")
    parser.add_argument(
        "--majors",
        type=str,
        default=",".join(_MAJORS),
        help="comma-separated majors-only contrast set",
    )
    args = parser.parse_args()

    conn = duckdb.connect(str(args.db), read_only=True)
    print(f"DB: {args.db}")

    universe = load_universe()
    majors = [s.strip().upper() for s in args.majors.split(",") if s.strip()]

    rows = [
        build_xs_report_row(conn, "universe @2bps", universe, 2.0),
        build_xs_report_row(conn, "majors @2bps", majors, 2.0),
    ]
    _print_df("Gate G3 — XS breadth contrast", pd.DataFrame(rows))

    sweep = [
        build_xs_report_row(conn, f"universe @{b:g}bps", universe, b)
        for b in (0.0, 2.0, 8.0, 16.0)
    ]
    _print_df("Cost sensitivity (universe)", pd.DataFrame(sweep))

    _print_df("Per-speed XS Sharpe", _per_speed_xs_sharpes(conn, universe))

    print(
        "\nG3 read: is the XS sleeve positive, cost-robust, DSR/PBO-survivable, AND "
        "low-correlated to trend (corr_to_trend near 0)? A modest-Sharpe XS sleeve "
        "uncorrelated with trend is a real combine win (P3 IDM layer). Read "
        "corr_to_trend + boot_lo + pbo alongside the headline before calling it."
    )


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Add the Makefile target**

In `Makefile`, append `buibui-xsmom-audit` to the long `.PHONY` line on line 14 (after `buibui-forecast-weight-study`), then add this block after the `buibui-forecast-weight-study` target (around line 268):

```makefile
.PHONY: buibui-xsmom-audit
buibui-xsmom-audit:  ## P3: read-only cross-sectional momentum sleeve audit over the N3 universe
    PYTHONPATH=. poetry run python tools/xsmom_audit.py
```

(The recipe line above must be indented with a real **TAB**, not spaces — shown as
spaces here only to keep this plan markdownlint-clean.)

- [ ] **Step 6: Run the tests to verify they pass**

Run: `PYTHONPATH=. poetry run pytest tests/xsmom/ -v`
Expected: PASS (all xsmom tests). Then verify the Makefile target is wired:
Run: `make -n buibui-xsmom-audit`
Expected: prints `PYTHONPATH=. poetry run python tools/xsmom_audit.py`.

- [ ] **Step 7: Commit**

```bash
git add analytics/xsmom/__init__.py tools/xsmom_audit.py Makefile tests/xsmom/test_audit_cli.py
git commit -m "feat(xsmom): read-only audit driver + make target + package exports"
```

---

### Task 7: Run the audit + write the G3 verdict

**Files:**

- Create: `docs/audits/2026-06-16-p3-xsmom-sleeve.md`

- [ ] **Step 1: Run the full DoD gate first**

Run: `make lint-py && make typecheck && make test`
Expected: all green; `make test` count up by the new xsmom tests.
Run: `make test-regression`
Expected: goldens UNMOVED (additive read-only package — no engine/schema touch). If they moved, STOP and investigate — nothing in this plan should change backtest output.

- [ ] **Step 2: Run the audit against the live analytics DB**

Run: `make buibui-xsmom-audit`
Expected: three tables (breadth contrast, cost sensitivity, per-speed XS Sharpe) + the G3 read line. Capture the full output.

- [ ] **Step 3: Write the verdict doc**

Create `docs/audits/2026-06-16-p3-xsmom-sleeve.md` with the captured numbers. Structure (fill every bracket with the actual run output — no placeholders survive into the committed doc):

```markdown
# P3 — Cross-Sectional Momentum Sleeve vs Gate G3 (verdict)

**Date:** 2026-06-16
**Driver:** `tools/xsmom_audit.py` (`make buibui-xsmom-audit`), read-only over `analytics.db`.
**Spec:** `docs/superpowers/specs/2026-06-16-p3-cross-sectional-momentum-sleeve-design.md`

## What was built

A standalone cross-sectional momentum sleeve: the existing capped multi-speed
EWMAC forecast per instrument, cross-sectionally demeaned each day (dollar-
neutral), `.shift(1)`'d for causality, vol-parity sized, and run through a
dollar-neutral long-short book with honest costs (turnover + funding; shorts
receive funding) and the 20%-vol portfolio governor. Evaluated universe-wide with
DSR / PBO / block-bootstrap-CI / MinTRL, plus correlation to the trend sleeve.

## Result (net of costs, 20% vol target)

### Breadth contrast (@ 2 bps/leg)

[paste the universe vs majors table]

### Cost sensitivity (universe)

[paste the 0/2/8/16 bps table]

### Per-speed XS Sharpe

[paste the per-speed table]

## Verdict: [CLEARED / NOT CLEARED] on the G3 bar

The G3 read is: positive + cost-robust + DSR/PBO-survivable + **low-correlated to
trend**. [State the universe XS Sharpe, its boot CI, DSR, PBO, and the
corr_to_trend, then the call.]

## The findings that matter

1. [Is XS positive at all? cost-robust to 16 bps?]
2. [corr_to_trend — diversifying vs trend, or just a re-labelled trend?]
3. [Does fast carry XS too, like it did for trend? Per-speed read.]

## Caveats

- Dollar-neutral only — no beta-neutralization (BTC-beta residual is a noted
  refinement, deferred). No IDM / correlation combine with trend (Task 2).
- [Bootstrap CI vs DSR interpretation, sample caveats as relevant.]

## DoD

lint-py ✓ · typecheck ✓ · test ✓ ([N]) · test-regression goldens UNMOVED ✓.
```

- [ ] **Step 4: Lint the doc and commit**

Run: `make lint-md`
Expected: 0 errors (every code fence has a language; tables spaced `| --- |`).

```bash
git add docs/audits/2026-06-16-p3-xsmom-sleeve.md
git commit -m "docs(xsmom): P3 cross-sectional momentum G3 verdict"
```

---

### Task 8: Docs sync (CLAUDE.md / README / MEMORY)

**Files:**

- Modify: `CLAUDE.md`
- Modify: `README.md`
- Modify: `~/.claude-personal/projects/-home-kng-repo-buibui-moon-trader-bot/memory/MEMORY.md` (+ a project memory file if the verdict warrants one)

- [ ] **Step 1: Add the `analytics/xsmom/` bullet to CLAUDE.md**

Under the analytics package list, after the `forecast/` bullet, add a parallel
`xsmom/` bullet describing the package (`book` / `replay` / `report` modules,
`tools/xsmom_audit.py` + `make buibui-xsmom-audit`, the demean→shift→vol-target
causal core, the G3 verdict path) — mirror the density and shape of the existing
`forecast/` bullet. Reference the verdict doc once it exists.

- [ ] **Step 2: Add the tool + make target to CLAUDE.md's tools list and README**

Add `tools/xsmom_audit.py` to the `tools/` section of CLAUDE.md (read-only P3
driver) and add a one-line entry for `make buibui-xsmom-audit` wherever the
README lists the forecast audit targets.

- [ ] **Step 3: Update the MEMORY index Current State**

Per the Session Memory Protocol, update the **Current State** section in
`MEMORY.md`: last-session line (P3 XS-mom sleeve shipped, the G3 verdict in one
clause), and the NEXT pointer (Task 2 IDM/correlation combine, or whatever the
verdict implies). Convert any relative dates to absolute.

- [ ] **Step 4: Lint and commit the docs**

Run: `make lint-md`
Expected: 0 errors.

```bash
git add CLAUDE.md README.md
git commit -m "docs(xsmom): sync CLAUDE.md + README for P3 cross-sectional sleeve"
```

(The MEMORY.md update is outside the repo — write it but it is not part of the git commit.)

---

## Self-Review

**Spec coverage:**

- Signal = demeaned EWMAC forecast → Task 1 (`xs_demeaned_forecasts`) + Task 2 (`xs_leverage`). ✓
- Continuous demeaned, dollar-neutral, vol-parity → Tasks 1–3. ✓
- Honest costs (turnover + funding, shorts receive) → Task 3 + `test_run_xs_backtest_short_leg_receives_funding`. ✓
- Causal (.shift(1) after demean) + perturbation test → Task 2 `test_xs_leverage_is_causal_no_lookahead`. ✓
- Module layout `book`/`replay`/`report` + reuse `ForecastConfig`/`ewmac`/`vol`/`load_daily_inputs`/`metrics`/`research_guards` → Tasks 1/4/5. ✓
- 5-trial DSR/PBO family (per-speed + combined) → Task 4 `replay_xs_trials` + `test_replay_xs_trials_has_per_speed_plus_combined`. ✓
- `XSReport` = G2 fields + `corr_to_trend` + `trend_sharpe`; `evaluate_xs(result, cfg, trial_returns, trend_returns)` → Task 5. ✓
- Driver + `make buibui-xsmom-audit`, read-only → Task 6. ✓
- Verdict doc `docs/audits/2026-06-16-p3-xsmom-sleeve.md` → Task 7. ✓
- DoD: lint/typecheck/test/test-regression goldens UNMOVED → Task 7 Step 1. ✓
- Deferred (beta-neutral, IDM combine, raw-return signals) → not implemented, noted in Task 7 caveats. ✓

**Placeholder scan:** No "TBD/TODO" in implementation tasks. The only brackets are
in Task 7's verdict-doc template, which is intentionally filled from live run
output at execution time (the procedure is explicit) — not a plan placeholder.

**Type consistency:** `XSBookResult` fields are identical across Tasks 1/3/4/5/6.
`xs_forecasts`/`xs_demeaned_forecasts`/`xs_leverage`/`run_xs_backtest`/
`equity_curve` signatures match between book.py definitions and all consumers.
`evaluate_xs(result, cfg, trial_returns, trend_returns)` and `XSReport` fields are
consistent between Task 5 and the Task 6 driver (`build_xs_report_row` reads
`rep.corr_to_trend`/`rep.trend_sharpe`). `replay_xs`/`replay_xs_trials` signatures
match between Task 4 and Task 6. Trials keys `s8_32/s16_64/s32_128/s64_256/
combined` are consistent between `replay_xs_trials` and its test.
