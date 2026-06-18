# P3 XS-momentum beta-neutral + forward-persistence — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a default-off dollar-neutral re-center to the XS book plus pure beta-attribution and forward-persistence diagnostics, then wire them into the audit, to decide whether the +1.375 XS edge is real cross-sectional alpha (not bull-beta) and persistent.

**Architecture:** One additive flag (`ForecastConfig.xs_dollar_neutral`) re-centers the leverage matrix so each day's active positions sum to zero. A new pure `analytics/xsmom/diagnostics.py` adds a full-sample OLS beta-attribution (reporting the beta-*hedged* return Sharpe, not zero-mean residuals) and a per-year/trailing-window persistence read. `tools/xsmom_audit.py` runs the gate stack on the neutral book beside the original and prints the two new tables. Everything is additive and default-off → existing books and regression goldens are byte-identical.

**Tech Stack:** Python 3.11, pandas, numpy, DuckDB (read-only), pytest, mypy strict, ruff.

**Spec:** `docs/superpowers/specs/2026-06-17-p3-xsmom-beta-neutral-persistence-design.md`

---

## File structure

- Modify `analytics/forecast/config.py` — add `xs_dollar_neutral: bool = False`.
- Modify `analytics/xsmom/book.py` — `xs_leverage` honors the flag (active-set re-center).
- Create `analytics/xsmom/diagnostics.py` — `equal_weight_market_return`, `beta_attribution`/`BetaAttribution`, `subperiod_sharpe`/`PersistenceReport`.
- Modify `analytics/xsmom/__init__.py` — export the diagnostics.
- Modify `tools/xsmom_audit.py` — neutral-book variant + beta-attribution + persistence tables.
- Modify `tests/xsmom/test_book.py` — re-center tests.
- Create `tests/xsmom/test_diagnostics.py` — diagnostics tests.
- Create `docs/audits/2026-06-17-p3-xsmom-beta-neutral-persistence.md` — verdict.
- Sync `CLAUDE.md` `xsmom/` bullet.

---

### Task 1: `xs_dollar_neutral` flag + active-set leverage re-center

**Files:**

- Modify: `analytics/forecast/config.py:37` (after the `weights` field)
- Modify: `analytics/xsmom/book.py:60-79` (`xs_leverage`)
- Test: `tests/xsmom/test_book.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/xsmom/test_book.py` (the module already defines `_closes()`):

```python
def test_xs_leverage_dollar_neutral_rows_sum_to_zero() -> None:
    from analytics.xsmom.book import xs_leverage

    cfg = ForecastConfig(xs_dollar_neutral=True)
    lev = xs_leverage(_closes(), cfg)
    warm = lev.dropna(how="any")
    assert len(warm) > 0
    # Each active day's positions net to zero (truly dollar-neutral).
    np.testing.assert_allclose(warm.sum(axis=1).to_numpy(), 0.0, atol=1e-9)


def test_xs_leverage_dollar_neutral_changes_net_exposure() -> None:
    from analytics.xsmom.book import xs_leverage

    closes = _closes()
    off = xs_leverage(closes, ForecastConfig())
    on = xs_leverage(closes, ForecastConfig(xs_dollar_neutral=True))
    # Off path keeps a residual net exposure; the flag removes it.
    off_net = off.dropna(how="any").sum(axis=1).abs().max()
    on_net = on.dropna(how="any").sum(axis=1).abs().max()
    assert off_net > 1e-6
    assert on_net < 1e-9


def test_xs_leverage_dollar_neutral_active_set_with_staggered_history() -> None:
    from analytics.xsmom.book import xs_leverage

    idx_full = pd.date_range("2021-01-01", periods=500, freq="D")
    idx_late = pd.date_range("2021-06-01", periods=400, freq="D")
    closes = {
        "A": pd.Series(np.linspace(100.0, 400.0, 500), index=idx_full),
        "B": pd.Series(np.linspace(400.0, 100.0, 500), index=idx_full),
        "C": pd.Series(np.linspace(100.0, 300.0, 400), index=idx_late),
    }
    lev = xs_leverage(closes, ForecastConfig(xs_dollar_neutral=True))
    early = lev.loc[lev["C"].isna() & lev[["A", "B"]].notna().all(axis=1)]
    assert len(early) > 0
    # Re-center over the active set only; the absent instrument stays NaN.
    np.testing.assert_allclose(
        early[["A", "B"]].sum(axis=1).to_numpy(), 0.0, atol=1e-9
    )
    assert early["C"].isna().all()


def test_xs_leverage_dollar_neutral_is_causal_no_lookahead() -> None:
    from analytics.xsmom.book import xs_leverage

    closes = _closes()
    cfg = ForecastConfig(xs_dollar_neutral=True)
    base = xs_leverage(closes, cfg)
    k = 250
    bumped = {s: c.copy() for s, c in closes.items()}
    bumped["STRONG"].iloc[k] *= 1.5
    after = xs_leverage(bumped, cfg)
    pd.testing.assert_frame_equal(
        base.iloc[: k + 1], after.iloc[: k + 1], check_names=False
    )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `poetry run pytest tests/xsmom/test_book.py -k dollar_neutral -v`
Expected: FAIL — `TypeError: ... unexpected keyword argument 'xs_dollar_neutral'` (field not yet on `ForecastConfig`).

- [ ] **Step 3: Add the config field**

In `analytics/forecast/config.py`, add the field immediately after `weights` (line 37):

```python
    weights: tuple[float, ...] | None = None
    xs_dollar_neutral: bool = False  # XS-sleeve only; trend sleeve ignores it
```

- [ ] **Step 4: Re-center the leverage matrix in `xs_leverage`**

In `analytics/xsmom/book.py`, replace the final two lines of `xs_leverage` (the `lev_cols[sym] = ...` loop body stays; change the return):

```python
        lev_cols[sym] = lev.replace([np.inf, -np.inf], np.nan)
    lev_df = pd.DataFrame(lev_cols, index=union)
    if cfg.xs_dollar_neutral:
        # Subtract the per-day active-set mean leverage so each day's positions
        # net to zero (dollar-neutral). Same skipna idiom as the forecast demean:
        # NaN cells stay NaN; a same-day op on already-shifted leverage adds no
        # look-ahead.
        lev_df = lev_df.sub(lev_df.mean(axis=1), axis=0)
    return lev_df
```

Update the docstring's last line to note the optional re-center:

```python
    union daily index. When ``cfg.xs_dollar_neutral`` is set, the matrix is
    re-centered so each day's active leverage sums to zero (dollar-neutral).
    """
```

- [ ] **Step 5: Run the new tests + the existing book tests**

Run: `poetry run pytest tests/xsmom/test_book.py -v`
Expected: PASS (new dollar-neutral tests pass; all pre-existing tests still pass — the off path is unchanged).

- [ ] **Step 6: Commit**

```bash
git add analytics/forecast/config.py analytics/xsmom/book.py tests/xsmom/test_book.py
git commit -m "feat(xsmom): default-off dollar-neutral leverage re-center"
```

---

### Task 2: `equal_weight_market_return` + `beta_attribution`

**Files:**

- Create: `analytics/xsmom/diagnostics.py`
- Test: `tests/xsmom/test_diagnostics.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/xsmom/test_diagnostics.py`:

```python
from __future__ import annotations

import math

import numpy as np
import pandas as pd


def test_equal_weight_market_return_active_set_mean() -> None:
    from analytics.xsmom.diagnostics import equal_weight_market_return

    idx = pd.date_range("2021-01-01", periods=4, freq="D")
    closes = {
        "A": pd.Series([100.0, 110.0, 121.0, 133.1], index=idx),  # +10%/day
        "B": pd.Series([100.0, 100.0, 100.0, 100.0], index=idx),  # 0%/day
    }
    mkt = equal_weight_market_return(closes)
    # day 0 is NaN (pct_change); day 1 = mean(+0.10, 0.0) = 0.05
    assert math.isnan(mkt.iloc[0])
    assert mkt.iloc[1] == 0.05


def test_equal_weight_market_return_skips_absent_instrument() -> None:
    from analytics.xsmom.diagnostics import equal_weight_market_return

    idx_full = pd.date_range("2021-01-01", periods=4, freq="D")
    idx_late = pd.date_range("2021-01-03", periods=2, freq="D")
    closes = {
        "A": pd.Series([100.0, 110.0, 121.0, 133.1], index=idx_full),  # +10%/day
        "C": pd.Series([100.0, 200.0], index=idx_late),  # present only days 2-3
    }
    mkt = equal_weight_market_return(closes)
    # day 1: only A present -> 0.10; day 3: A +10% and C +100% -> mean 0.55
    assert mkt.loc[idx_full[1]] == 0.10
    assert mkt.loc[idx_full[3]] == 0.55


def test_beta_attribution_recovers_known_alpha_beta() -> None:
    from analytics.xsmom.diagnostics import beta_attribution

    rng = np.random.default_rng(0)
    n = 2000
    mkt = rng.normal(0.0, 0.02, n)
    noise = rng.normal(0.0, 0.001, n)
    port = 0.0003 + 1.4 * mkt + noise
    ba = beta_attribution(port, mkt, ann_days=365.0)
    assert abs(ba.beta - 1.4) < 0.05
    assert abs(ba.alpha_annual - 0.0003 * 365.0) < 0.02
    assert ba.r_squared > 0.95
    assert ba.alpha_tstat > 2.0
    # hedged stream = alpha + residual: positive mean, small vol -> high Sharpe
    assert ba.beta_hedged_sharpe > 1.0


def test_beta_attribution_degenerate_market_is_safe() -> None:
    from analytics.xsmom.diagnostics import beta_attribution

    port = np.array([0.01, -0.02, 0.03, 0.0, 0.015])
    mkt = np.zeros(5)  # zero-variance market
    ba = beta_attribution(port, mkt, ann_days=365.0)
    assert ba.beta == 0.0
    assert ba.r_squared == 0.0
    # hedged == port when there is no market factor to remove
    assert abs(ba.alpha_annual - float(np.mean(port)) * 365.0) < 1e-9
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `poetry run pytest tests/xsmom/test_diagnostics.py -v`
Expected: FAIL — `ModuleNotFoundError: analytics.xsmom.diagnostics`.

- [ ] **Step 3: Create the diagnostics module**

Create `analytics/xsmom/diagnostics.py`:

```python
"""Pure beta-attribution + forward-persistence diagnostics for the XS sleeve.

No DB/IO; numpy + pandas + stdlib only. Consumed by ``tools/xsmom_audit.py`` to
quantify how much of the headline Sharpe is market beta vs alpha, and whether the
edge persists across calendar years and recent windows.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
import pandas as pd


def equal_weight_market_return(closes: dict[str, pd.Series]) -> pd.Series:
    """Active-set mean of per-instrument daily returns (the 'alt market').

    Aligns each instrument's ``pct_change()`` to the sorted union daily index and
    averages across the present (non-NaN) instruments each day (skipna). Day-0 of
    each instrument is NaN by construction.
    """
    union = pd.DatetimeIndex([])
    for s in closes.values():
        union = union.union(pd.DatetimeIndex(s.index))
    union = union.sort_values()
    rets = [c.pct_change().reindex(union) for c in closes.values()]
    mat = pd.concat(rets, axis=1)
    return mat.mean(axis=1)


@dataclass(frozen=True)
class BetaAttribution:
    alpha_annual: float
    beta: float
    alpha_tstat: float
    beta_hedged_sharpe: float
    r_squared: float


def _ann_sharpe(r: npt.NDArray[np.float64], ann_days: float) -> float:
    if len(r) < 2:
        return 0.0
    sd = float(np.std(r, ddof=1))
    if sd < 1e-12:
        return 0.0
    return float(np.mean(r) / sd) * math.sqrt(ann_days)


def beta_attribution(
    port_ret: npt.ArrayLike, mkt_ret: npt.ArrayLike, ann_days: float = 365.0
) -> BetaAttribution:
    """Full-sample OLS ``r_port = alpha + beta * r_mkt + eps``.

    Reports the annualized *beta-hedged* Sharpe of ``r_port - beta * r_mkt`` (=
    alpha + residual), NOT the zero-mean OLS residual. Aligns on the common tail,
    drops non-finite rows, and is degenerate-safe (zero-variance market -> beta
    0.0, hedged == port).
    """
    x = np.asarray(mkt_ret, dtype=np.float64)
    y = np.asarray(port_ret, dtype=np.float64)
    n = min(len(x), len(y))
    x, y = x[-n:], y[-n:]
    mask = np.isfinite(x) & np.isfinite(y)
    x, y = x[mask], y[mask]

    if len(x) < 2 or float(np.std(x, ddof=1)) < 1e-12:
        return BetaAttribution(
            alpha_annual=(float(np.mean(y)) * ann_days) if len(y) else 0.0,
            beta=0.0,
            alpha_tstat=0.0,
            beta_hedged_sharpe=_ann_sharpe(y, ann_days),
            r_squared=0.0,
        )

    design = np.column_stack([np.ones(len(x)), x])
    coef, *_ = np.linalg.lstsq(design, y, rcond=None)
    alpha_d, beta = float(coef[0]), float(coef[1])
    resid = y - design @ coef

    dof = len(x) - 2
    sigma2 = float(resid @ resid) / dof if dof > 0 else 0.0
    xtx_inv = np.linalg.inv(design.T @ design)
    se_alpha = math.sqrt(sigma2 * float(xtx_inv[0, 0])) if sigma2 > 0 else 0.0
    tstat = alpha_d / se_alpha if se_alpha > 1e-15 else 0.0

    hedged = y - beta * x
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r2 = 1.0 - float(resid @ resid) / ss_tot if ss_tot > 1e-15 else 0.0

    return BetaAttribution(
        alpha_annual=alpha_d * ann_days,
        beta=beta,
        alpha_tstat=tstat,
        beta_hedged_sharpe=_ann_sharpe(hedged, ann_days),
        r_squared=r2,
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `poetry run pytest tests/xsmom/test_diagnostics.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add analytics/xsmom/diagnostics.py tests/xsmom/test_diagnostics.py
git commit -m "feat(xsmom): beta-attribution diagnostic + equal-weight market return"
```

---

### Task 3: `subperiod_sharpe` forward-persistence

**Files:**

- Modify: `analytics/xsmom/diagnostics.py`
- Test: `tests/xsmom/test_diagnostics.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/xsmom/test_diagnostics.py`:

```python
def test_subperiod_sharpe_by_year_and_trailing() -> None:
    from analytics.xsmom.diagnostics import subperiod_sharpe

    idx = pd.date_range("2021-01-01", "2022-12-31", freq="D")
    rng = np.random.default_rng(1)
    # 2021 strongly positive drift, 2022 flat-ish — distinct per-year Sharpe.
    r = np.where(
        idx.year.to_numpy() == 2021,
        rng.normal(0.002, 0.01, len(idx)),
        rng.normal(0.0, 0.01, len(idx)),
    )
    rep = subperiod_sharpe(r, idx, ann_days=365.0)
    assert set(rep.by_year.keys()) == {2021, 2022}
    assert rep.by_year[2021] > rep.by_year[2022]
    assert rep.n_obs == len(idx)
    # trailing 1y window pulls only from 2022 -> close to the 2022 figure
    assert rep.trailing_1y < rep.by_year[2021]


def test_subperiod_sharpe_degenerate_slices_are_zero_not_nan() -> None:
    from analytics.xsmom.diagnostics import subperiod_sharpe

    idx = pd.date_range("2021-01-01", periods=1, freq="D")
    rep = subperiod_sharpe(np.array([0.01]), idx, ann_days=365.0)
    assert rep.by_year[2021] == 0.0  # single obs -> 0.0, not NaN
    assert rep.trailing_1y == 0.0
    assert rep.trailing_2y == 0.0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `poetry run pytest tests/xsmom/test_diagnostics.py -k subperiod -v`
Expected: FAIL — `ImportError: cannot import name 'subperiod_sharpe'`.

- [ ] **Step 3: Add `subperiod_sharpe` + `PersistenceReport`**

Append to `analytics/xsmom/diagnostics.py`:

```python
@dataclass(frozen=True)
class PersistenceReport:
    by_year: dict[int, float]
    trailing_2y: float
    trailing_1y: float
    n_obs: int


def subperiod_sharpe(
    port_ret: npt.ArrayLike,
    index: pd.DatetimeIndex,
    ann_days: float = 365.0,
) -> PersistenceReport:
    """Annualized Sharpe per calendar year + trailing 2y / 1y windows.

    Any sub-slice with < 2 observations or ~0 std returns 0.0 (never NaN).
    """
    s = pd.Series(np.asarray(port_ret, dtype=np.float64), index=pd.DatetimeIndex(index))
    by_year = {
        int(year): _ann_sharpe(grp.to_numpy(dtype=np.float64), ann_days)
        for year, grp in s.groupby(s.index.year)
    }
    last = s.index.max()
    t2 = s[s.index > last - pd.Timedelta(days=730)]
    t1 = s[s.index > last - pd.Timedelta(days=365)]
    return PersistenceReport(
        by_year=by_year,
        trailing_2y=_ann_sharpe(t2.to_numpy(dtype=np.float64), ann_days),
        trailing_1y=_ann_sharpe(t1.to_numpy(dtype=np.float64), ann_days),
        n_obs=len(s),
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `poetry run pytest tests/xsmom/test_diagnostics.py -v`
Expected: PASS (6 tests total).

- [ ] **Step 5: Commit**

```bash
git add analytics/xsmom/diagnostics.py tests/xsmom/test_diagnostics.py
git commit -m "feat(xsmom): per-year + trailing-window persistence diagnostic"
```

---

### Task 4: Export the diagnostics

**Files:**

- Modify: `analytics/xsmom/__init__.py`

- [ ] **Step 1: Add the imports + `__all__` entries**

Replace the import block and `__all__` in `analytics/xsmom/__init__.py` so it also exports the diagnostics (keep existing entries; add the new module):

```python
from analytics.xsmom.book import (
    XSBookResult,
    equity_curve,
    run_xs_backtest,
    xs_demeaned_forecasts,
    xs_forecasts,
    xs_leverage,
)
from analytics.xsmom.diagnostics import (
    BetaAttribution,
    PersistenceReport,
    beta_attribution,
    equal_weight_market_return,
    subperiod_sharpe,
)
from analytics.xsmom.replay import replay_xs, replay_xs_trials
from analytics.xsmom.report import XSReport, evaluate_xs

__all__ = [
    "BetaAttribution",
    "PersistenceReport",
    "XSBookResult",
    "XSReport",
    "beta_attribution",
    "equal_weight_market_return",
    "equity_curve",
    "evaluate_xs",
    "replay_xs",
    "replay_xs_trials",
    "run_xs_backtest",
    "subperiod_sharpe",
    "xs_demeaned_forecasts",
    "xs_forecasts",
    "xs_leverage",
]
```

- [ ] **Step 2: Verify the package imports**

Run: `poetry run python -c "from analytics.xsmom import beta_attribution, subperiod_sharpe, equal_weight_market_return; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 3: Commit**

```bash
git add analytics/xsmom/__init__.py
git commit -m "feat(xsmom): export diagnostics from package init"
```

---

### Task 5: Wire the audit — neutral book + beta-attribution + persistence tables

**Files:**

- Modify: `tools/xsmom_audit.py`

- [ ] **Step 1: Add imports**

In `tools/xsmom_audit.py`, extend the imports (after the existing `from analytics.xsmom import ...` line):

```python
from analytics.forecast.replay import load_daily_inputs
from analytics.xsmom import (
    beta_attribution,
    equal_weight_market_return,
    evaluate_xs,
    replay_xs,
    replay_xs_trials,
    run_xs_backtest,
    subperiod_sharpe,
)
```

(Replace the existing `from analytics.xsmom import evaluate_xs, replay_xs, replay_xs_trials` line with the block above.)

- [ ] **Step 2: Thread the flag through `build_xs_report_row`**

Change the signature and the `cfg` construction in `build_xs_report_row`:

```python
def build_xs_report_row(
    conn: duckdb.DuckDBPyConnection,
    label: str,
    symbols: list[str],
    slippage_bps: float,
    xs_dollar_neutral: bool = False,
) -> dict[str, object]:
    cfg = dataclasses.replace(
        ForecastConfig(),
        slippage_pct=slippage_bps / 10_000.0,
        xs_dollar_neutral=xs_dollar_neutral,
    )
```

(The rest of the function body is unchanged.)

- [ ] **Step 3: Add the two new table builders**

Add these functions above `_print_df`:

```python
def _beta_attribution_table(
    conn: duckdb.DuckDBPyConnection, symbols: list[str]
) -> pd.DataFrame:
    closes, fundings = load_daily_inputs(conn, symbols)
    mkt = equal_weight_market_return(closes)
    union = mkt.index
    mkt_arr = mkt.to_numpy(dtype=float)
    btc = (
        closes["BTCUSDT"].pct_change().reindex(union).to_numpy(dtype=float)
        if "BTCUSDT" in closes
        else None
    )
    rows: list[dict[str, object]] = []
    for label, neutral in (("original", False), ("dollar-neutral", True)):
        cfg = dataclasses.replace(
            ForecastConfig(), slippage_pct=0.0002, xs_dollar_neutral=neutral
        )
        r = run_xs_backtest(closes, fundings, cfg).portfolio_return
        proxies: list[tuple[str, object]] = [("alt-mkt", mkt_arr)]
        if btc is not None:
            proxies.append(("BTC", btc))
        for proxy_name, proxy in proxies:
            ba = beta_attribution(r, proxy)
            rows.append(
                {
                    "book": label,
                    "proxy": proxy_name,
                    "alpha_ann": ba.alpha_annual,
                    "beta": ba.beta,
                    "alpha_t": ba.alpha_tstat,
                    "hedged_sharpe": ba.beta_hedged_sharpe,
                    "r2": ba.r_squared,
                }
            )
    return pd.DataFrame(rows)


def _persistence_table(
    conn: duckdb.DuckDBPyConnection, symbols: list[str]
) -> pd.DataFrame:
    closes, fundings = load_daily_inputs(conn, symbols)
    cfg = dataclasses.replace(
        ForecastConfig(), slippage_pct=0.0002, xs_dollar_neutral=True
    )
    res = run_xs_backtest(closes, fundings, cfg)
    pr = subperiod_sharpe(res.portfolio_return, res.daily_index)
    rows: list[dict[str, object]] = [
        {"period": str(year), "sharpe": sr} for year, sr in sorted(pr.by_year.items())
    ]
    rows.append({"period": "trailing_2y", "sharpe": pr.trailing_2y})
    rows.append({"period": "trailing_1y", "sharpe": pr.trailing_1y})
    return pd.DataFrame(rows)
```

- [ ] **Step 4: Render the new sections in `main`**

In `main`, after the existing breadth-contrast block (the `_print_df("Gate G3 — XS breadth contrast", ...)` call), add the dollar-neutral comparison and the two new tables:

```python
    neutral_rows = [
        build_xs_report_row(conn, "universe original @2bps", universe, 2.0, False),
        build_xs_report_row(conn, "universe neutral @2bps", universe, 2.0, True),
    ]
    _print_df("Dollar-neutral gate (original vs neutral)", pd.DataFrame(neutral_rows))
    _print_df("Beta attribution (universe)", _beta_attribution_table(conn, universe))
    _print_df("Forward persistence (neutral, universe)", _persistence_table(conn, universe))
```

- [ ] **Step 5: Run the audit (read-only smoke)**

Run: `PYTHONPATH=. poetry run python tools/xsmom_audit.py`
Expected: prints all tables including "Dollar-neutral gate", "Beta attribution", and "Forward persistence"; no exception. (Read-only against `analytics.db`.)

- [ ] **Step 6: Lint + typecheck the touched Python, then commit**

Run: `make lint-py && make typecheck`
Expected: both pass.

```bash
git add tools/xsmom_audit.py
git commit -m "feat(xsmom): audit — dollar-neutral gate + beta-attribution + persistence tables"
```

---

### Task 6: Run audit, write verdict, sync docs, full DoD

**Files:**

- Create: `docs/audits/2026-06-17-p3-xsmom-beta-neutral-persistence.md`
- Modify: `CLAUDE.md` (the `xsmom/` bullet)

- [ ] **Step 1: Capture the audit numbers**

Run: `PYTHONPATH=. poetry run python tools/xsmom_audit.py | tee /tmp/xsmom-beta-audit.txt`
Read the three new tables: does the gate (DSR ≥ 0.95 ∧ PBO ≤ 0.5 ∧ boot_lo > 0) survive on the dollar-neutral book; the alt-mkt/BTC beta and `hedged_sharpe`; the per-year + trailing Sharpe.

- [ ] **Step 2: Write the verdict doc**

Create `docs/audits/2026-06-17-p3-xsmom-beta-neutral-persistence.md` with: the numbers from each table, an explicit answer to each of the four spec questions (gate survives neutralization? how much was beta? persistent? graduate yes/no), and the standing survivorship caveat (demoted to pre-capital rigor audit). Use ` ```text ` fences for any ASCII tables and spaced `| --- |` delimiters for markdown tables (markdownlint MD040/MD060).

- [ ] **Step 3: Sync CLAUDE.md**

Update the `xsmom/` bullet in `CLAUDE.md` to mention `diagnostics.py` (`beta_attribution` / `equal_weight_market_return` / `subperiod_sharpe`) and the `ForecastConfig.xs_dollar_neutral` flag, and reference the new verdict doc.

- [ ] **Step 4: Full Definition of Done**

Run each and confirm:

```bash
make lint-py        # ruff format + lint — PASS
make typecheck      # mypy strict — PASS
make test           # full suite — PASS (XS book + diagnostics tests green)
make test-regression  # goldens UNMOVED (additive + default-off)
make lint-md        # new spec + verdict doc — PASS
```

If `test-regression` reports drift, STOP — the change was supposed to be additive/default-off; investigate before regenerating any golden.

- [ ] **Step 5: Commit**

```bash
git add docs/audits/2026-06-17-p3-xsmom-beta-neutral-persistence.md CLAUDE.md
git commit -m "docs(xsmom): beta-neutral + persistence verdict; sync CLAUDE.md"
```

---

## Self-review notes

- **Spec coverage:** Component 1 → Task 1; Component 2 → Task 2; Component 3 → Task 3; exports → Task 4; Component 4 wiring → Task 5; Component 5 verdict + DoD → Task 6. All spec sections mapped.
- **Type consistency:** field `xs_dollar_neutral` (config) used identically in book + audit; `BetaAttribution.beta_hedged_sharpe` / `alpha_annual` / `alpha_tstat` / `r_squared` consistent between Task 2 def and Task 5 audit; `PersistenceReport.by_year` / `trailing_2y` / `trailing_1y` / `n_obs` consistent between Task 3 def and Task 5 audit; `equal_weight_market_return` / `beta_attribution` / `subperiod_sharpe` signatures match across tasks.
- **Causality:** the re-center is a same-day op on already-`shift(1)`-ed leverage; Task 1 Step 1 includes the perturbation guard.
- **Goldens:** every code path is gated behind `xs_dollar_neutral` (default False) or lives in a new module / the audit driver → regression goldens must stay unmoved (Task 6 Step 4 enforces).
