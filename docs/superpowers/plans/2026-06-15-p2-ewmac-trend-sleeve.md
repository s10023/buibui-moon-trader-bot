# P2 — EWMAC Trend Sleeve Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a continuous, vol-normalised, multi-speed EWMAC trend forecast sleeve, evaluate it across the N3 25-perp universe through a portfolio-vol-targeted daily paper book with honest costs, and produce the gate-G2 verdict (trend-sleeve OOS Sharpe ≥ ~1 on breadth, DSR/PBO-gated).

**Architecture:** A new pure-Python `analytics/forecast/` package (mirrors `analytics/exits/`): `ewmac.py`/`vol.py` (pure forecast math), `book.py` (daily-rebalanced causal engine, return-space), `config.py` (`ForecastConfig`), `replay.py` (the only DB-touching module — read-only), `report.py` (metrics + guard stamps). Reuses `portfolio/metrics.py` (curve→Sharpe/Sortino/maxDD), `analytics/research_guards/` (DSR/PBO/MinTRL/bootstrap), `analytics/universe.py`, `analytics/store` getters, `analytics/regime.py`. Driver: read-only `tools/forecast_audit.py`.

**Tech Stack:** Python 3.11+, pandas, numpy, DuckDB (read-only), pytest + unittest.mock, Poetry, ruff, mypy strict.

**Spec:** `docs/superpowers/specs/2026-06-15-p2-ewmac-trend-sleeve-design.md`

---

## Conventions used throughout this plan

- Run tests with: `PYTHONPATH=. poetry run pytest <path> -v`
- Lint/type after each task touching Python: `make lint-py && make typecheck`
- Every module starts with `from __future__ import annotations`.
- All functions fully type-annotated (mypy strict; `-> None` on tests).
- Causality rule (the load-bearing invariant): the position **held during day d**
  is sized only from information available at the **close of day d−1**. In code:
  forecast and vol series are `.shift(1)` before they size a position; the daily
  return `r_d = close_d / close_{d−1} − 1` is what that position earns.

### Reference: APIs this plan consumes (already in the repo — do not reimplement)

```text
analytics/universe.py
  load_universe(path=Path("config/universe.toml")) -> list[str]

analytics/store  (re-exported from analytics.data_store)
  get_ohlcv(conn, symbol, timeframe, start, end) -> DataFrame
      cols: symbol, timeframe, open_time, open, high, low, close, volume, taker_buy_volume
  get_funding_rates(conn, symbol, start, end) -> DataFrame
      cols: symbol, funding_time, funding_rate
  get_symbol_lifecycle(conn) -> DataFrame
  DEFAULT_DB_PATH

analytics/regime.py
  classify_series(df, timeframe, slope_threshold=None) -> pd.Series  # needs high/low/close sorted by open_time

portfolio/metrics.py  (all take an equity CURVE Series, annualize at 365)
  sharpe(curve, periods_per_year=365.0) -> float
  sortino(curve, ...) -> float
  max_drawdown(curve) -> float
  calmar(curve, ...) -> float
  annual_return(curve, ...) -> float
  annual_vol(curve, ...) -> float
  daily_returns(curve) -> pd.Series

analytics/research_guards  (pure math; Sharpe args are PER-PERIOD, not annualized)
  deflated_sharpe_ratio(sr, n_obs, *, trial_srs=None, n_trials=None, sr_variance=None, skew=0.0, kurtosis=3.0) -> float
  min_track_record_length(sr, skew=0.0, kurtosis=3.0, target_sr=0.0, confidence=0.95) -> float
  cscv_pbo(perf_matrix: ndarray[(T, N)], n_splits=14, metric=None) -> PBOResult(pbo, logits, degradation_slope, n_combinations)
  block_bootstrap_ci(returns, stat_fn, n_boot=10000, block=None, alpha=0.05, method="stationary", seed=None) -> BootstrapCI(point, lo, hi, alpha, n_valid)

config/strategy_params.toml  [backtest]
  fee_pct = 0.0005      # 0.05% per leg
  slippage_bps = 2.0    # 2 bps per leg -> slippage_pct = 0.0002
```

---

## File structure (locked before tasks)

| File | Responsibility |
| --- | --- |
| `analytics/forecast/__init__.py` | eager re-exports of the public surface |
| `analytics/forecast/config.py` | `ForecastConfig` frozen dataclass + `from_toml` |
| `analytics/forecast/vol.py` | causal EW volatility estimators |
| `analytics/forecast/ewmac.py` | raw EWMAC, vol-adjust, scale/cap, multi-speed combine + FDM |
| `analytics/forecast/book.py` | `instrument_returns`, `run_forecast_backtest`, `ForecastBookResult`, `equity_curve` |
| `analytics/forecast/replay.py` | DB front door (read-only): universe → engine |
| `analytics/forecast/report.py` | metrics + DSR/PBO/CI/MinTRL + attribution assembly |
| `tools/forecast_audit.py` | read-only CLI driver |
| `tests/forecast/test_*.py` | one test module per source module |

---

## Task 0: Package scaffold + `ForecastConfig`

**Files:**

- Create: `analytics/forecast/__init__.py`
- Create: `analytics/forecast/config.py`
- Create: `tests/forecast/__init__.py`
- Create: `tests/forecast/test_config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/forecast/test_config.py
from __future__ import annotations

from analytics.forecast.config import ForecastConfig


def test_defaults() -> None:
    cfg = ForecastConfig()
    assert cfg.speeds == (
        (8, 32, 5.3),
        (16, 64, 3.75),
        (32, 128, 2.65),
        (64, 256, 1.91),
    )
    assert cfg.vol_span == 32
    assert cfg.fdm == 1.25
    assert cfg.cap == 20.0
    assert cfg.vol_target_annual == 0.20
    assert cfg.fee_pct == 0.0005
    assert cfg.slippage_pct == 0.0002
    assert cfg.gov_window == 64
    assert cfg.g_min == 0.5
    assert cfg.g_max == 1.5
    assert cfg.annualization_days == 365.0


def test_min_history_is_longest_slow_plus_vol_span() -> None:
    cfg = ForecastConfig()
    # longest slow span (256) + vol span (32)
    assert cfg.min_history == 288


def test_from_toml_reads_backtest_costs(tmp_path) -> None:  # type: ignore[no-untyped-def]
    p = tmp_path / "cfg.toml"
    p.write_text("[backtest]\nfee_pct = 0.001\nslippage_bps = 4.0\n")
    cfg = ForecastConfig.from_toml(p)
    assert cfg.fee_pct == 0.001
    assert cfg.slippage_pct == 0.0004  # 4 bps


def test_from_toml_missing_backtest_uses_defaults(tmp_path) -> None:  # type: ignore[no-untyped-def]
    p = tmp_path / "cfg.toml"
    p.write_text("[other]\nx = 1\n")
    cfg = ForecastConfig.from_toml(p)
    assert cfg.fee_pct == 0.0005
    assert cfg.slippage_pct == 0.0002
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. poetry run pytest tests/forecast/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: analytics.forecast.config`.

- [ ] **Step 3: Write minimal implementation**

```python
# analytics/forecast/config.py
"""Configuration for the EWMAC trend sleeve (P2).

Frozen dataclass + a `from_toml` that picks up the shared honest-cost values
from the `[backtest]` block (`fee_pct`, `slippage_bps`). All other knobs are
Carver-standard constants and rarely change.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

# (fast_span, slow_span, forecast_scalar) — pysystemtrade EWMAC scalars,
# derived from broad futures data (NOT crypto-fit) so they carry no look-ahead.
_DEFAULT_SPEEDS: tuple[tuple[int, int, float], ...] = (
    (8, 32, 5.3),
    (16, 64, 3.75),
    (32, 128, 2.65),
    (64, 256, 1.91),
)


@dataclass(frozen=True)
class ForecastConfig:
    speeds: tuple[tuple[int, int, float], ...] = _DEFAULT_SPEEDS
    vol_span: int = 32
    fdm: float = 1.25
    cap: float = 20.0
    vol_target_annual: float = 0.20
    fee_pct: float = 0.0005
    slippage_pct: float = 0.0002
    gov_window: int = 64
    g_min: float = 0.5
    g_max: float = 1.5
    annualization_days: float = 365.0

    @property
    def min_history(self) -> int:
        """Bars of warm-up an instrument needs before it can be sized."""
        longest_slow = max(slow for _, slow, _ in self.speeds)
        return longest_slow + self.vol_span

    @classmethod
    def from_toml(cls, path: Path | str) -> ForecastConfig:
        with open(path, "rb") as f:
            data = tomllib.load(f)
        bt = data.get("backtest", {})
        fee = float(bt.get("fee_pct", 0.0005))
        slip_bps = float(bt.get("slippage_bps", 2.0))
        return cls(fee_pct=fee, slippage_pct=slip_bps / 10_000.0)
```

```python
# analytics/forecast/__init__.py
"""EWMAC trend sleeve (P2) — continuous vol-normalised trend forecasts."""

from __future__ import annotations

from analytics.forecast.config import ForecastConfig

__all__ = ["ForecastConfig"]
```

```python
# tests/forecast/__init__.py
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. poetry run pytest tests/forecast/test_config.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add analytics/forecast/__init__.py analytics/forecast/config.py tests/forecast/
git commit -m "feat(forecast): P2 scaffold + ForecastConfig"
```

---

## Task 1: `vol.py` — causal volatility estimators

**Files:**

- Create: `analytics/forecast/vol.py`
- Create: `tests/forecast/test_vol.py`

`ew_return_vol` returns the **causal** daily % vol used to size the next day's
position: the EW std of daily simple returns, `.shift(1)` so the value at index
`d` uses returns through `d−1`. `price_vol` is `ew_return_vol × close` (price
units) for the EWMAC denominator — also `.shift(1)`-aligned via its inputs.
`annualize` scales a daily vol by `√days`.

- [ ] **Step 1: Write the failing test**

```python
# tests/forecast/test_vol.py
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from analytics.forecast.vol import annualize, ew_return_vol, price_vol


def test_annualize() -> None:
    assert annualize(0.02, 365.0) == 0.02 * math.sqrt(365.0)


def test_ew_return_vol_is_causal_and_shifted() -> None:
    # rising then a shock: the vol at index t must NOT include return at t.
    close = pd.Series([100.0, 101.0, 102.0, 103.0, 80.0, 81.0])
    vol = ew_return_vol(close, span=3)
    # index 0 has no prior return -> NaN; index 1 uses only return at idx1 via shift -> still NaN
    assert math.isnan(vol.iloc[0])
    # the big -22% shock lands at index 4; the position-sizing vol AT index 4
    # must come from data through index 3 (pre-shock, small vol).
    assert vol.iloc[4] < 0.05
    # by index 5 the shock is in the estimate -> vol jumps.
    assert vol.iloc[5] > vol.iloc[4]


def test_price_vol_is_return_vol_times_price() -> None:
    close = pd.Series([100.0, 110.0, 121.0, 133.1, 146.41])
    rv = ew_return_vol(close, span=3)
    pv = price_vol(close, span=3)
    # where both are defined, pv == rv * close
    mask = ~rv.isna()
    np.testing.assert_allclose(
        pv[mask].to_numpy(), (rv[mask] * close[mask]).to_numpy(), rtol=1e-12
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. poetry run pytest tests/forecast/test_vol.py -v`
Expected: FAIL — `ModuleNotFoundError: analytics.forecast.vol`.

- [ ] **Step 3: Write minimal implementation**

```python
# analytics/forecast/vol.py
"""Causal exponentially-weighted volatility estimators for the trend sleeve.

All estimators are shifted so the value at day `d` uses only returns through
day `d-1` — the position held during day `d` is sized on yesterday's information.
"""

from __future__ import annotations

import math

import pandas as pd


def ew_return_vol(close: pd.Series, span: int) -> pd.Series:
    """Causal EW std of daily simple returns (decimal, e.g. 0.03 = 3%/day)."""
    returns = close.pct_change()
    return returns.ewm(span=span, min_periods=span).std().shift(1)


def price_vol(close: pd.Series, span: int) -> pd.Series:
    """Causal price volatility in price units = return-vol x price."""
    return ew_return_vol(close, span) * close


def annualize(daily_vol: float, days: float = 365.0) -> float:
    return daily_vol * math.sqrt(days)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. poetry run pytest tests/forecast/test_vol.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add analytics/forecast/vol.py tests/forecast/test_vol.py
git commit -m "feat(forecast): causal EW volatility estimators"
```

---

## Task 2: `ewmac.py` — single-speed raw → scaled → capped forecast

**Files:**

- Create: `analytics/forecast/ewmac.py`
- Create: `tests/forecast/test_ewmac.py`

`raw_ewmac(close, fast, slow) = EWMA(close, fast) − EWMA(close, slow)`.
`scaled_forecast(close, fast, slow, scalar, vol_span, cap)` divides the raw by
`price_vol`, multiplies by the Carver `scalar`, and clips to `[−cap, +cap]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/forecast/test_ewmac.py
from __future__ import annotations

import numpy as np
import pandas as pd

from analytics.forecast.ewmac import combine_forecasts, raw_ewmac, scaled_forecast


def test_raw_ewmac_positive_in_uptrend() -> None:
    close = pd.Series(np.linspace(100.0, 200.0, 300))
    raw = raw_ewmac(close, fast=8, slow=32)
    # steady uptrend -> fast EMA above slow EMA -> positive once warmed up
    assert raw.iloc[-1] > 0.0


def test_raw_ewmac_matches_pandas_ewm() -> None:
    close = pd.Series([1.0, 2.0, 3.0, 5.0, 8.0, 13.0, 21.0])
    expected = (
        close.ewm(span=2, adjust=False).mean()
        - close.ewm(span=4, adjust=False).mean()
    )
    pd.testing.assert_series_equal(raw_ewmac(close, 2, 4), expected, check_names=False)


def test_scaled_forecast_is_capped() -> None:
    # explosive trend -> raw/price_vol large -> must clip to +cap
    close = pd.Series(np.geomspace(1.0, 1e6, 400))
    f = scaled_forecast(close, fast=8, slow=32, scalar=5.3, vol_span=32, cap=20.0)
    assert f.dropna().max() <= 20.0 + 1e-9
    assert f.dropna().min() >= -20.0 - 1e-9


def test_scaled_forecast_sign_follows_trend() -> None:
    up = pd.Series(np.linspace(100.0, 300.0, 400))
    down = pd.Series(np.linspace(300.0, 100.0, 400))
    assert scaled_forecast(up, 8, 32, 5.3, 32, 20.0).iloc[-1] > 0.0
    assert scaled_forecast(down, 8, 32, 5.3, 32, 20.0).iloc[-1] < 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. poetry run pytest tests/forecast/test_ewmac.py -v`
Expected: FAIL — `ModuleNotFoundError` (and `combine_forecasts` undefined; that test is Task 3 but the import fails now, which is fine — it fails for the right reason).

- [ ] **Step 3: Write minimal implementation**

```python
# analytics/forecast/ewmac.py
"""EWMAC forecast math — raw crossover, vol normalisation, scaling, combination.

Pure functions over a price `pd.Series`. No DB, no IO. Forecasts are continuous
and vol-normalised so a long-run average absolute value of ~10 holds across
instruments (Carver convention); capped to +/-20 before sizing.
"""

from __future__ import annotations

import pandas as pd

from analytics.forecast.vol import price_vol


def raw_ewmac(close: pd.Series, fast: int, slow: int) -> pd.Series:
    """Fast EMA minus slow EMA (price units)."""
    fast_ema = close.ewm(span=fast, adjust=False).mean()
    slow_ema = close.ewm(span=slow, adjust=False).mean()
    return fast_ema - slow_ema


def scaled_forecast(
    close: pd.Series,
    fast: int,
    slow: int,
    scalar: float,
    vol_span: int,
    cap: float,
) -> pd.Series:
    """Vol-normalised, scalar-adjusted, capped single-speed forecast."""
    raw = raw_ewmac(close, fast, slow)
    pv = price_vol(close, vol_span)
    vol_adj = raw / pv
    return (vol_adj * scalar).clip(lower=-cap, upper=cap)
```

- [ ] **Step 4: Run test to verify the Task-2 tests pass**

Run: `PYTHONPATH=. poetry run pytest tests/forecast/test_ewmac.py -k "raw_ewmac or scaled_forecast" -v`
Expected: PASS (4 tests). (The `combine_forecasts` test still errors on import of an undefined name — fixed in Task 3.)

- [ ] **Step 5: Commit**

```bash
git add analytics/forecast/ewmac.py tests/forecast/test_ewmac.py
git commit -m "feat(forecast): single-speed EWMAC scaled+capped forecast"
```

---

## Task 3: `ewmac.py` — multi-speed combination + FDM

**Files:**

- Modify: `analytics/forecast/ewmac.py`
- Modify: `tests/forecast/test_ewmac.py` (the `combine_forecasts` test added in Task 2 is already present)

`combine_forecasts(close, speeds, fdm, vol_span, cap)` computes each speed's
`scaled_forecast`, takes the **equal-weight mean**, multiplies by the constant
`fdm`, and re-clips to `[−cap, +cap]`.

- [ ] **Step 1: Add the failing test**

```python
# append to tests/forecast/test_ewmac.py
def test_combine_is_fdm_times_equal_weight_mean_then_capped() -> None:
    close = pd.Series(np.linspace(100.0, 130.0, 400))
    speeds = ((8, 32, 5.3), (16, 64, 3.75))
    combined = combine_forecasts(close, speeds=speeds, fdm=1.25, vol_span=32, cap=20.0)

    f1 = scaled_forecast(close, 8, 32, 5.3, 32, 20.0)
    f2 = scaled_forecast(close, 16, 64, 3.75, 32, 20.0)
    expected = ((f1 + f2) / 2.0 * 1.25).clip(-20.0, 20.0)
    pd.testing.assert_series_equal(combined, expected, check_names=False)


def test_combine_respects_cap_after_fdm() -> None:
    close = pd.Series(np.geomspace(1.0, 1e6, 400))
    combined = combine_forecasts(
        close,
        speeds=((8, 32, 5.3), (16, 64, 3.75)),
        fdm=3.0,
        vol_span=32,
        cap=20.0,
    )
    assert combined.dropna().abs().max() <= 20.0 + 1e-9
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. poetry run pytest tests/forecast/test_ewmac.py -k combine -v`
Expected: FAIL — `combine_forecasts` not defined.

- [ ] **Step 3: Add the implementation**

```python
# append to analytics/forecast/ewmac.py
def combine_forecasts(
    close: pd.Series,
    speeds: tuple[tuple[int, int, float], ...],
    fdm: float,
    vol_span: int,
    cap: float,
) -> pd.Series:
    """Equal-weight mean of per-speed forecasts x FDM, re-capped to +/-cap."""
    parts = [
        scaled_forecast(close, fast, slow, scalar, vol_span, cap)
        for fast, slow, scalar in speeds
    ]
    stacked = pd.concat(parts, axis=1)
    mean = stacked.mean(axis=1)
    return (mean * fdm).clip(lower=-cap, upper=cap)
```

- [ ] **Step 4: Run the full ewmac test module**

Run: `PYTHONPATH=. poetry run pytest tests/forecast/test_ewmac.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add analytics/forecast/ewmac.py tests/forecast/test_ewmac.py
git commit -m "feat(forecast): multi-speed forecast combination + FDM"
```

---

## Task 4: `book.py` — per-instrument subsystem returns (the causal heart)

**Files:**

- Create: `analytics/forecast/book.py`
- Create: `tests/forecast/test_book_instrument.py`

`instrument_returns(close, funding_daily, cfg)` returns a DataFrame indexed like
`close` with columns `leverage`, `gross`, `turnover_cost`, `funding_cost`, `net`.

Math (causal):

- `forecast = combine_forecasts(close, ...)`; `vol_ann = annualize(ew_return_vol(close, vol_span))`
- both `.shift(1)` so day-`d` position uses info through `d−1`
- `leverage_d = (forecast_{d−1} / 10) · (vol_target / vol_ann_{d−1})`
- `r_d = close_d / close_{d−1} − 1`; `gross_d = leverage_d · r_d`
- `turnover_cost_d = |leverage_d − leverage_{d−1}| · (fee_pct + slippage_pct)`
- `funding_cost_d = leverage_d · funding_daily_d` (long `lev>0` pays positive
  funding → cost; short receives). `funding_daily` is the summed funding rate
  over day `d`, aligned to the close index; missing → 0.0.
- `net_d = gross_d − turnover_cost_d − funding_cost_d`

- [ ] **Step 1: Write the failing test**

```python
# tests/forecast/test_book_instrument.py
from __future__ import annotations

import numpy as np
import pandas as pd

from analytics.forecast.book import instrument_returns
from analytics.forecast.config import ForecastConfig


def _trend_close(n: int = 500) -> pd.Series:
    idx = pd.date_range("2021-01-01", periods=n, freq="D")
    return pd.Series(np.linspace(100.0, 400.0, n), index=idx)


def test_uptrend_yields_positive_net_no_funding() -> None:
    close = _trend_close()
    funding = pd.Series(0.0, index=close.index)
    out = instrument_returns(close, funding, ForecastConfig())
    # a clean uptrend held long should net positive over the path
    assert out["net"].sum() > 0.0
    # leverage should be long (positive) once warmed up
    assert out["leverage"].dropna().iloc[-1] > 0.0


def test_position_is_causal_no_lookahead() -> None:
    close = _trend_close()
    funding = pd.Series(0.0, index=close.index)
    base = instrument_returns(close, funding, ForecastConfig())

    # perturb ONLY the last close; nothing before the last row may change.
    bumped = close.copy()
    bumped.iloc[-1] *= 1.5
    after = instrument_returns(bumped, funding, ForecastConfig())

    pd.testing.assert_series_equal(
        base["leverage"].iloc[:-1], after["leverage"].iloc[:-1], check_names=False
    )


def test_funding_sign_long_pays_short_receives() -> None:
    close = _trend_close()
    up_fund = pd.Series(0.001, index=close.index)  # positive funding
    out_long = instrument_returns(close, up_fund, ForecastConfig())
    # long in an uptrend with positive funding -> positive funding COST
    assert out_long["funding_cost"].dropna().iloc[-1] > 0.0

    down = pd.Series(np.linspace(400.0, 100.0, len(close)), index=close.index)
    out_short = instrument_returns(down, up_fund, ForecastConfig())
    # short (downtrend) with positive funding -> negative cost (a credit)
    assert out_short["funding_cost"].dropna().iloc[-1] < 0.0


def test_turnover_cost_nonnegative_and_charged_on_change() -> None:
    close = _trend_close()
    funding = pd.Series(0.0, index=close.index)
    out = instrument_returns(close, funding, ForecastConfig())
    assert (out["turnover_cost"].dropna() >= 0.0).all()
    assert out["turnover_cost"].dropna().sum() > 0.0  # leverage ramps -> some cost
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. poetry run pytest tests/forecast/test_book_instrument.py -v`
Expected: FAIL — `analytics.forecast.book` missing.

- [ ] **Step 3: Write minimal implementation**

```python
# analytics/forecast/book.py
"""Daily-rebalanced, return-space EWMAC paper book.

`instrument_returns` turns one instrument's 1d close + daily funding into a
causal subsystem return stream (gross, turnover cost, funding accrual, net).
`run_forecast_backtest` aggregates instruments at equal risk weight and applies
a causal portfolio vol governor. Everything is in fraction-of-capital units, so
`portfolio.metrics` can read the resulting equity curve directly.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.forecast.ewmac import combine_forecasts
from analytics.forecast.vol import annualize, ew_return_vol


def instrument_returns(
    close: pd.Series,
    funding_daily: pd.Series,
    cfg: ForecastConfig,
) -> pd.DataFrame:
    """Causal subsystem returns for one instrument.

    Columns: leverage, gross, turnover_cost, funding_cost, net (indexed like
    `close`). `funding_daily` is the day's summed funding rate aligned to the
    close index (0.0 where missing).
    """
    forecast = combine_forecasts(
        close, cfg.speeds, cfg.fdm, cfg.vol_span, cfg.cap
    ).shift(1)
    vol_ann = (
        ew_return_vol(close, cfg.vol_span)
        .mul(np.sqrt(cfg.annualization_days))
        .shift(1)
    )

    leverage = (forecast / 10.0) * (cfg.vol_target_annual / vol_ann)
    leverage = leverage.replace([np.inf, -np.inf], np.nan)

    r = close.pct_change()
    gross = leverage * r

    lev_prev = leverage.shift(1)
    turnover_cost = (leverage - lev_prev).abs() * (cfg.fee_pct + cfg.slippage_pct)

    fund = funding_daily.reindex(close.index).fillna(0.0)
    funding_cost = leverage * fund

    net = gross - turnover_cost - funding_cost

    return pd.DataFrame(
        {
            "leverage": leverage,
            "gross": gross,
            "turnover_cost": turnover_cost,
            "funding_cost": funding_cost,
            "net": net,
        }
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. poetry run pytest tests/forecast/test_book_instrument.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add analytics/forecast/book.py tests/forecast/test_book_instrument.py
git commit -m "feat(forecast): causal per-instrument subsystem returns"
```

---

## Task 5: `book.py` — portfolio aggregation + causal vol governor

**Files:**

- Modify: `analytics/forecast/book.py`
- Create: `tests/forecast/test_book_portfolio.py`

Add `ForecastBookResult` (frozen), `run_forecast_backtest`, and `equity_curve`.

- `run_forecast_backtest(closes, fundings, cfg)` takes `dict[str, pd.Series]`
  closes + fundings, builds a union daily index, computes each instrument's
  `net` (Task 4), aggregates at **equal risk weight** = the mean of `net` across
  instruments that are *active* that day (non-NaN leverage), then applies the
  **causal governor**: `g_d = clip(vol_target / trailing_vol_{<d}, g_min, g_max)`
  where `trailing_vol` is the annualised std of the pre-governor portfolio return
  over the prior `gov_window` days (strictly before `d`; `.shift(1)`). Final
  `portfolio_return_d = g_d · pre_return_d`.
- `equity_curve(result)` = `(1 + portfolio_return).cumprod()` as a Series indexed
  by `pd.to_datetime(daily_index)` — the input to `portfolio.metrics`.

- [ ] **Step 1: Write the failing test**

```python
# tests/forecast/test_book_portfolio.py
from __future__ import annotations

import numpy as np
import pandas as pd

from analytics.forecast.book import (
    ForecastBookResult,
    equity_curve,
    run_forecast_backtest,
)
from analytics.forecast.config import ForecastConfig


def _series(values: np.ndarray, start: str = "2021-01-01") -> pd.Series:
    idx = pd.date_range(start, periods=len(values), freq="D")
    return pd.Series(values, index=idx)


def test_two_instrument_uptrend_positive_and_shapes() -> None:
    a = _series(np.linspace(100.0, 400.0, 600))
    b = _series(np.linspace(50.0, 220.0, 600))
    z = pd.Series(0.0, index=a.index)
    res = run_forecast_backtest(
        {"AAA": a, "BBB": b}, {"AAA": z, "BBB": z}, ForecastConfig()
    )
    assert isinstance(res, ForecastBookResult)
    assert len(res.daily_index) == len(res.portfolio_return)
    assert set(res.per_instrument_net) == {"AAA", "BBB"}
    curve = equity_curve(res)
    assert curve.iloc[-1] > curve.iloc[0]  # net-positive trend book


def test_governor_is_clamped() -> None:
    a = _series(np.linspace(100.0, 400.0, 600))
    z = pd.Series(0.0, index=a.index)
    cfg = ForecastConfig()
    res = run_forecast_backtest({"AAA": a}, {"AAA": z}, cfg)
    g = res.governor[~np.isnan(res.governor)]
    assert (g >= cfg.g_min - 1e-9).all()
    assert (g <= cfg.g_max + 1e-9).all()


def test_governor_is_causal() -> None:
    # perturbing the final day must not change any earlier governor value
    a = _series(np.linspace(100.0, 400.0, 600))
    z = pd.Series(0.0, index=a.index)
    base = run_forecast_backtest({"AAA": a}, {"AAA": z}, ForecastConfig())
    bumped = a.copy()
    bumped.iloc[-1] *= 2.0
    after = run_forecast_backtest({"AAA": bumped}, {"AAA": z}, ForecastConfig())
    np.testing.assert_allclose(
        base.governor[:-1], after.governor[:-1], equal_nan=True
    )


def test_inactive_instrument_excluded_from_mean() -> None:
    # BBB starts late (NaNs before its listing) -> early days driven by AAA only
    a = _series(np.linspace(100.0, 400.0, 600))
    b_vals = np.concatenate([np.full(300, np.nan), np.linspace(50.0, 90.0, 300)])
    b = _series(b_vals)
    z = pd.Series(0.0, index=a.index)
    res = run_forecast_backtest(
        {"AAA": a, "BBB": b}, {"AAA": z, "BBB": z}, ForecastConfig()
    )
    # active count rises once BBB warms up
    assert res.active_count[50] <= 1
    assert res.active_count[-1] == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. poetry run pytest tests/forecast/test_book_portfolio.py -v`
Expected: FAIL — `ForecastBookResult` / `run_forecast_backtest` / `equity_curve` undefined.

- [ ] **Step 3: Write the implementation**

```python
# append to analytics/forecast/book.py  (add imports at top: dataclasses.dataclass)
from dataclasses import dataclass


@dataclass(frozen=True)
class ForecastBookResult:
    daily_index: pd.DatetimeIndex
    portfolio_return: np.ndarray  # net, post-governor
    pre_governor_return: np.ndarray
    governor: np.ndarray
    active_count: np.ndarray
    per_instrument_net: dict[str, pd.Series]


def run_forecast_backtest(
    closes: dict[str, pd.Series],
    fundings: dict[str, pd.Series],
    cfg: ForecastConfig,
) -> ForecastBookResult:
    """Aggregate per-instrument subsystem returns + causal vol governor."""
    union = pd.DatetimeIndex([])
    for s in closes.values():
        union = union.union(s.index)
    union = union.sort_values()

    per_net: dict[str, pd.Series] = {}
    net_cols: list[pd.Series] = []
    for sym, close in closes.items():
        fund = fundings.get(sym, pd.Series(0.0, index=close.index))
        out = instrument_returns(close, fund, cfg)
        net = out["net"].reindex(union)
        per_net[sym] = net
        net_cols.append(net)

    net_mat = pd.concat(net_cols, axis=1)
    active = net_mat.notna().sum(axis=1)
    pre = net_mat.mean(axis=1)  # equal risk weight across active instruments
    pre = pre.fillna(0.0)

    ann = np.sqrt(cfg.annualization_days)
    trailing_vol = pre.rolling(cfg.gov_window, min_periods=cfg.gov_window).std().shift(
        1
    ) * ann
    g = (cfg.vol_target_annual / trailing_vol).clip(cfg.g_min, cfg.g_max)
    port = (g.fillna(0.0) * pre)

    return ForecastBookResult(
        daily_index=union,
        portfolio_return=port.to_numpy(dtype=np.float64),
        pre_governor_return=pre.to_numpy(dtype=np.float64),
        governor=g.to_numpy(dtype=np.float64),
        active_count=active.to_numpy(dtype=np.int64),
        per_instrument_net=per_net,
    )


def equity_curve(result: ForecastBookResult) -> pd.Series:
    """Compounding equity curve (starts at 1.0) for portfolio.metrics."""
    r = pd.Series(result.portfolio_return, index=result.daily_index)
    return (1.0 + r).cumprod()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. poetry run pytest tests/forecast/test_book_portfolio.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add analytics/forecast/book.py tests/forecast/test_book_portfolio.py
git commit -m "feat(forecast): portfolio aggregation + causal vol governor"
```

---

## Task 6: `replay.py` — read-only DB front door

**Files:**

- Create: `analytics/forecast/replay.py`
- Create: `tests/forecast/test_replay.py`

`load_daily_inputs(conn, symbols)` reads 1d OHLCV + funding per symbol (read-only)
and returns `(closes, fundings)` dicts of Series indexed by UTC day. Funding rows
(every 8h) are summed per UTC day, then forward-aligned to the close index.
`replay_universe(conn, cfg, symbols=None)` defaults `symbols` to `load_universe()`,
loads inputs, and runs `run_forecast_backtest`.

- [ ] **Step 1: Write the failing test**

```python
# tests/forecast/test_replay.py
from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd

from analytics.forecast.book import ForecastBookResult
from analytics.forecast.config import ForecastConfig
from analytics.forecast.replay import load_daily_inputs, replay_universe
from analytics.store import init_schema
from analytics.store.market_data import upsert_funding_rates, upsert_ohlcv

_DAY = 86_400_000


def _seed(conn: duckdb.DuckDBPyConnection, symbol: str, n: int) -> None:
    t0 = 1_600_000_000_000
    rows = []
    for i in range(n):
        price = 100.0 + i  # uptrend
        rows.append(
            {
                "symbol": symbol,
                "timeframe": "1d",
                "open_time": t0 + i * _DAY,
                "open": price,
                "high": price + 1,
                "low": price - 1,
                "close": price,
                "volume": 1000.0,
                "taker_buy_volume": 500.0,
            }
        )
    upsert_ohlcv(conn, pd.DataFrame(rows))
    # funding 3x/day
    f = []
    for i in range(n * 3):
        f.append(
            {
                "symbol": symbol,
                "funding_time": t0 + i * (_DAY // 3),
                "funding_rate": 0.0001,
            }
        )
    upsert_funding_rates(conn, pd.DataFrame(f))


def test_load_daily_inputs_sums_funding_per_day() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    _seed(conn, "AAAUSDT", 320)
    closes, fundings = load_daily_inputs(conn, ["AAAUSDT"])
    assert "AAAUSDT" in closes
    # 3 funding intervals/day x 0.0001 -> ~0.0003/day where covered
    assert abs(fundings["AAAUSDT"].dropna().iloc[10] - 0.0003) < 1e-9


def test_replay_universe_runs_and_returns_result() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    _seed(conn, "AAAUSDT", 320)
    _seed(conn, "BBBUSDT", 320)
    res = replay_universe(conn, ForecastConfig(), symbols=["AAAUSDT", "BBBUSDT"])
    assert isinstance(res, ForecastBookResult)
    assert set(res.per_instrument_net) == {"AAAUSDT", "BBBUSDT"}
    assert len(res.daily_index) > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. poetry run pytest tests/forecast/test_replay.py -v`
Expected: FAIL — `analytics.forecast.replay` missing.

- [ ] **Step 3: Write the implementation**

```python
# analytics/forecast/replay.py
"""Read-only DuckDB front door for the EWMAC trend sleeve.

Loads 1d OHLCV + funding for the universe and runs the forecast book. The only
module in `analytics/forecast/` that touches the database; never writes.
"""

from __future__ import annotations

import duckdb
import pandas as pd

from analytics.data_store import get_funding_rates, get_ohlcv
from analytics.forecast.book import ForecastBookResult, run_forecast_backtest
from analytics.forecast.config import ForecastConfig
from analytics.universe import load_universe

_FAR_PAST = 0
_FAR_FUTURE = 4_102_444_800_000  # 2100-01-01


def load_daily_inputs(
    conn: duckdb.DuckDBPyConnection,
    symbols: list[str],
) -> tuple[dict[str, pd.Series], dict[str, pd.Series]]:
    """Return (closes, fundings) dicts of day-indexed Series per symbol."""
    closes: dict[str, pd.Series] = {}
    fundings: dict[str, pd.Series] = {}
    for sym in symbols:
        bars = get_ohlcv(conn, sym, "1d", _FAR_PAST, _FAR_FUTURE)
        if bars.empty:
            continue
        idx = pd.to_datetime(bars["open_time"], unit="ms", utc=True).dt.normalize()
        close = pd.Series(bars["close"].to_numpy(dtype=float), index=idx)
        close = close[~close.index.duplicated(keep="last")].sort_index()
        closes[sym] = close

        fr = get_funding_rates(conn, sym, _FAR_PAST, _FAR_FUTURE)
        if fr.empty:
            fundings[sym] = pd.Series(0.0, index=close.index)
            continue
        fidx = pd.to_datetime(fr["funding_time"], unit="ms", utc=True).dt.normalize()
        daily = (
            pd.Series(fr["funding_rate"].to_numpy(dtype=float), index=fidx)
            .groupby(level=0)
            .sum()
        )
        fundings[sym] = daily.reindex(close.index).fillna(0.0)
    return closes, fundings


def replay_universe(
    conn: duckdb.DuckDBPyConnection,
    cfg: ForecastConfig,
    symbols: list[str] | None = None,
) -> ForecastBookResult:
    """Load the universe's 1d inputs and run the forecast book (read-only)."""
    syms = symbols if symbols is not None else load_universe()
    closes, fundings = load_daily_inputs(conn, syms)
    return run_forecast_backtest(closes, fundings, cfg)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. poetry run pytest tests/forecast/test_replay.py -v`
Expected: PASS (2 tests). (`init_schema` is exported from `analytics.store`; if the
import path differs, use `from analytics.store.schema import init_schema`.)

- [ ] **Step 5: Commit**

```bash
git add analytics/forecast/replay.py tests/forecast/test_replay.py
git commit -m "feat(forecast): read-only DB front door (universe -> book)"
```

---

## Task 7: `report.py` — metrics + research-guard stamps + attribution

**Files:**

- Create: `analytics/forecast/report.py`
- Create: `tests/forecast/test_report.py`

`G2Report` (frozen) holds the headline metrics + guard stamps. `evaluate(result,
cfg, trial_returns)` computes them. Key conversions (the spec's load-bearing
detail): research guards take **per-period** Sharpe; `portfolio.metrics.sharpe`
returns the **annualised** one.

- per-period daily Sharpe `sr_d = mean(R)/std(R)`; annualised = `sr_d·√365`.
- `deflated_sharpe_ratio(sr=sr_d, n_obs=len(R), trial_srs=<per-period sharpes of
  each trial>)` — `trial_returns: dict[str, np.ndarray]` are the candidate
  configs (per-speed sleeves + combined + vol-span variants).
- `min_track_record_length(sr=sr_d, target_sr=1.0/√365, confidence=0.95)`.
- `block_bootstrap_ci(R, stat_fn=lambda x: ann_sharpe(x), seed=7)` → CI on the
  **annualised** Sharpe.
- `cscv_pbo(perf_matrix)` where columns are each trial's daily returns (rows
  trimmed to the common length).

- [ ] **Step 1: Write the failing test**

```python
# tests/forecast/test_report.py
from __future__ import annotations

import numpy as np
import pandas as pd

from analytics.forecast.book import ForecastBookResult
from analytics.forecast.config import ForecastConfig
from analytics.forecast.report import G2Report, evaluate


def _result(returns: np.ndarray) -> ForecastBookResult:
    idx = pd.date_range("2021-01-01", periods=len(returns), freq="D")
    return ForecastBookResult(
        daily_index=idx,
        portfolio_return=returns,
        pre_governor_return=returns,
        governor=np.ones(len(returns)),
        active_count=np.full(len(returns), 1, dtype=np.int64),
        per_instrument_net={"AAA": pd.Series(returns, index=idx)},
    )


def test_positive_drift_gives_positive_sharpe_and_report_shape() -> None:
    rng = np.random.default_rng(0)
    r = 0.001 + 0.01 * rng.standard_normal(800)  # positive-drift noise
    res = _result(r)
    trials = {"combined": r, "s64_256": r * 0.9}
    rep = evaluate(res, ForecastConfig(), trial_returns=trials)
    assert isinstance(rep, G2Report)
    assert rep.sharpe_annual > 0.0
    assert rep.n_obs == 800
    assert rep.boot_lo <= rep.sharpe_annual <= rep.boot_hi
    assert 0.0 <= rep.pbo <= 1.0
    assert rep.min_trl > 0.0


def test_flat_returns_degenerate_to_zero() -> None:
    res = _result(np.zeros(500))
    rep = evaluate(res, ForecastConfig(), trial_returns={"combined": np.zeros(500)})
    assert rep.sharpe_annual == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. poetry run pytest tests/forecast/test_report.py -v`
Expected: FAIL — `analytics.forecast.report` missing.

- [ ] **Step 3: Write the implementation**

```python
# analytics/forecast/report.py
"""Assemble the G2 verdict: headline metrics + research-guard stamps.

Pure over a ForecastBookResult plus the candidate trials' daily returns (the
honest multiple-testing family for DSR/PBO). Research guards consume per-period
Sharpe; portfolio.metrics returns annualised.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from analytics.forecast.book import ForecastBookResult, equity_curve
from analytics.forecast.config import ForecastConfig
from analytics.research_guards import (
    block_bootstrap_ci,
    cscv_pbo,
    deflated_sharpe_ratio,
    min_track_record_length,
)
from portfolio import metrics


def _per_period_sharpe(r: np.ndarray) -> float:
    if len(r) < 2:
        return 0.0
    sd = float(np.std(r, ddof=1))
    if sd < 1e-12:
        return 0.0
    return float(np.mean(r) / sd)


@dataclass(frozen=True)
class G2Report:
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


def evaluate(
    result: ForecastBookResult,
    cfg: ForecastConfig,
    trial_returns: dict[str, np.ndarray],
) -> G2Report:
    r = result.portfolio_return
    curve = equity_curve(result)
    ann = math.sqrt(cfg.annualization_days)

    sr_d = _per_period_sharpe(r)
    trial_srs = [_per_period_sharpe(v) for v in trial_returns.values()]

    # PBO over the trial family (rows trimmed to common length)
    min_len = min((len(v) for v in trial_returns.values()), default=0)
    if min_len >= 28 and len(trial_returns) >= 2:
        mat = np.column_stack([v[-min_len:] for v in trial_returns.values()])
        pbo = cscv_pbo(mat).pbo
    else:
        pbo = float("nan")

    if sr_d != 0.0:
        boot = block_bootstrap_ci(
            r, stat_fn=lambda x: _per_period_sharpe(x) * ann, seed=7
        )
        boot_lo, boot_hi = boot.lo, boot.hi
        dsr = deflated_sharpe_ratio(sr_d, len(r), trial_srs=trial_srs)
        min_trl = min_track_record_length(sr_d, target_sr=1.0 / ann, confidence=0.95)
    else:
        boot_lo = boot_hi = dsr = 0.0
        min_trl = float("inf")

    return G2Report(
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
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. poetry run pytest tests/forecast/test_report.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add analytics/forecast/report.py tests/forecast/test_report.py
git commit -m "feat(forecast): G2 report — metrics + DSR/PBO/CI/MinTRL"
```

---

## Task 8: Single-speed trials + breadth helper for the audit

**Files:**

- Modify: `analytics/forecast/replay.py`
- Modify: `analytics/forecast/__init__.py`
- Create: `tests/forecast/test_trials.py`

`replay_trials(conn, cfg, symbols)` returns `dict[str, np.ndarray]` of daily
portfolio returns for: each single-speed sleeve (`s8_32` … `s64_256`) and the
`combined` book — the honest DSR/PBO family + the **H2** check (compare
`s64_256` to `combined`). It reuses `replay_universe` by swapping `cfg.speeds`
to a single pair per trial.

- [ ] **Step 1: Write the failing test**

```python
# tests/forecast/test_trials.py
from __future__ import annotations

import dataclasses

import duckdb
import numpy as np
import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.forecast.replay import replay_trials
from analytics.store import init_schema
from analytics.store.market_data import upsert_ohlcv

_DAY = 86_400_000


def _seed(conn: duckdb.DuckDBPyConnection, symbol: str, n: int) -> None:
    t0 = 1_600_000_000_000
    rows = [
        {
            "symbol": symbol,
            "timeframe": "1d",
            "open_time": t0 + i * _DAY,
            "open": 100.0 + i,
            "high": 101.0 + i,
            "low": 99.0 + i,
            "close": 100.0 + i,
            "volume": 1000.0,
            "taker_buy_volume": 500.0,
        }
        for i in range(n)
    ]
    upsert_ohlcv(conn, pd.DataFrame(rows))


def test_replay_trials_has_per_speed_and_combined() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    _seed(conn, "AAAUSDT", 320)
    trials = replay_trials(conn, ForecastConfig(), symbols=["AAAUSDT"])
    assert "combined" in trials
    assert "s8_32" in trials and "s64_256" in trials
    assert all(isinstance(v, np.ndarray) for v in trials.values())


def test_dataclasses_replace_speeds_is_single_pair() -> None:
    # guards the mechanism replay_trials uses
    cfg = ForecastConfig()
    one = dataclasses.replace(cfg, speeds=((8, 32, 5.3),))
    assert one.speeds == ((8, 32, 5.3),)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. poetry run pytest tests/forecast/test_trials.py -v`
Expected: FAIL — `replay_trials` undefined.

- [ ] **Step 3: Add the implementation**

```python
# append to analytics/forecast/replay.py  (add: import dataclasses; import numpy as np)
import dataclasses

import numpy as np


def replay_trials(
    conn: duckdb.DuckDBPyConnection,
    cfg: ForecastConfig,
    symbols: list[str] | None = None,
) -> dict[str, np.ndarray]:
    """Daily portfolio returns for each single-speed sleeve + the combined book.

    The honest multiple-testing family for DSR/PBO and the H2 check (s64_256 vs
    combined). Loads inputs once; re-runs the book per speed in memory.
    """
    syms = symbols if symbols is not None else load_universe()
    closes, fundings = load_daily_inputs(conn, syms)
    from analytics.forecast.book import run_forecast_backtest

    out: dict[str, np.ndarray] = {}
    for fast, slow, scalar in cfg.speeds:
        one = dataclasses.replace(cfg, speeds=((fast, slow, scalar),))
        res = run_forecast_backtest(closes, fundings, one)
        out[f"s{fast}_{slow}"] = res.portfolio_return
    combined = run_forecast_backtest(closes, fundings, cfg)
    out["combined"] = combined.portfolio_return
    return out
```

```python
# analytics/forecast/__init__.py  (replace body)
"""EWMAC trend sleeve (P2) — continuous vol-normalised trend forecasts."""

from __future__ import annotations

from analytics.forecast.book import (
    ForecastBookResult,
    equity_curve,
    instrument_returns,
    run_forecast_backtest,
)
from analytics.forecast.config import ForecastConfig
from analytics.forecast.replay import (
    load_daily_inputs,
    replay_trials,
    replay_universe,
)
from analytics.forecast.report import G2Report, evaluate

__all__ = [
    "ForecastBookResult",
    "ForecastConfig",
    "G2Report",
    "equity_curve",
    "evaluate",
    "instrument_returns",
    "load_daily_inputs",
    "replay_trials",
    "replay_universe",
    "run_forecast_backtest",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. poetry run pytest tests/forecast/test_trials.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add analytics/forecast/replay.py analytics/forecast/__init__.py tests/forecast/test_trials.py
git commit -m "feat(forecast): per-speed trials family + package exports"
```

---

## Task 9: `tools/forecast_audit.py` — read-only CLI driver + Makefile target

**Files:**

- Create: `tools/forecast_audit.py`
- Modify: `Makefile` (add `buibui-forecast-audit` target)
- Create: `tests/forecast/test_audit_cli.py`

The CLI mirrors `tools/exit_audit.py`: read-only DuckDB connection, prints the G2
report for the **full universe** and the **majors-only** contrast, a
cost-sensitivity sweep (0/2/8/16 bps), and the per-speed (H2) Sharpes.

- [ ] **Step 1: Write the failing test**

```python
# tests/forecast/test_audit_cli.py
from __future__ import annotations

import duckdb
import pandas as pd

from analytics.store import init_schema
from analytics.store.market_data import upsert_ohlcv
from tools.forecast_audit import build_report_row

_DAY = 86_400_000


def _seed(conn: duckdb.DuckDBPyConnection, symbol: str, n: int) -> None:
    t0 = 1_600_000_000_000
    rows = [
        {
            "symbol": symbol,
            "timeframe": "1d",
            "open_time": t0 + i * _DAY,
            "open": 100.0 + i,
            "high": 101.0 + i,
            "low": 99.0 + i,
            "close": 100.0 + i,
            "volume": 1000.0,
            "taker_buy_volume": 500.0,
        }
        for i in range(320)
    ]
    upsert_ohlcv(conn, pd.DataFrame(rows))


def test_build_report_row_returns_dict() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    _seed(conn, "AAAUSDT", 320)
    row = build_report_row(conn, "label", symbols=["AAAUSDT"], slippage_bps=2.0)
    assert row["label"] == "label"
    assert "sharpe" in row and "max_dd" in row and "pbo" in row
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. poetry run pytest tests/forecast/test_audit_cli.py -v`
Expected: FAIL — `tools.forecast_audit` missing.

- [ ] **Step 3: Write the implementation**

```python
# tools/forecast_audit.py
"""EWMAC trend sleeve audit (P2) — read-only G2 verdict.

Runs the continuous multi-speed EWMAC trend forecast across the N3 universe
through the portfolio-vol-targeted paper book and prints the gate-G2 read:
portfolio Sharpe/Sortino/max-DD with DSR/PBO/bootstrap-CI/MinTRL stamps, a
cost-sensitivity sweep, a breadth (universe vs majors-only) contrast, and the
per-speed Sharpes (the H2 cycle-bias check).

Read-only — no writes, no schema changes.

Usage::

    PYTHONPATH=. poetry run python tools/forecast_audit.py
    PYTHONPATH=. poetry run python tools/forecast_audit.py --majors BTCUSDT,ETHUSDT,SOLUSDT
"""

from __future__ import annotations

import argparse
import dataclasses
from pathlib import Path

import duckdb
import pandas as pd

from analytics.forecast import (
    ForecastConfig,
    evaluate,
    replay_trials,
    replay_universe,
)
from analytics.store import DEFAULT_DB_PATH
from analytics.universe import load_universe

_MAJORS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]


def build_report_row(
    conn: duckdb.DuckDBPyConnection,
    label: str,
    symbols: list[str],
    slippage_bps: float,
) -> dict[str, object]:
    cfg = dataclasses.replace(ForecastConfig(), slippage_pct=slippage_bps / 10_000.0)
    result = replay_universe(conn, cfg, symbols=symbols)
    trials = replay_trials(conn, cfg, symbols=symbols)
    rep = evaluate(result, cfg, trial_returns=trials)
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
    }


def _per_speed_sharpes(
    conn: duckdb.DuckDBPyConnection, symbols: list[str]
) -> pd.DataFrame:
    cfg = ForecastConfig()
    trials = replay_trials(conn, cfg, symbols=symbols)
    import math

    import numpy as np

    ann = math.sqrt(cfg.annualization_days)
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
        build_report_row(conn, "universe @2bps", universe, 2.0),
        build_report_row(conn, "majors @2bps", majors, 2.0),
    ]
    _print_df("Gate G2 — breadth contrast", pd.DataFrame(rows))

    sweep = [
        build_report_row(conn, f"universe @{b:g}bps", universe, b)
        for b in (0.0, 2.0, 8.0, 16.0)
    ]
    _print_df("Cost sensitivity (universe)", pd.DataFrame(sweep))

    _print_df("Per-speed Sharpe (H2: s64_256 vs combined)", _per_speed_sharpes(conn, universe))

    print(
        "\nG2 = trend-sleeve OOS Sharpe >= ~1 on the universe, costs in, "
        "DSR/PBO-gated. Read boot_lo (annualised Sharpe CI lower bound) and pbo "
        "alongside the headline before calling PASS/MARGINAL/FAIL."
    )


if __name__ == "__main__":
    main()
```

<!-- markdownlint-disable MD010 -->

```makefile
# add to Makefile near the other buibui-* targets (recipe line is a real TAB)
.PHONY: buibui-forecast-audit
buibui-forecast-audit:  ## P2: read-only EWMAC trend-sleeve G2 audit over the N3 universe
	PYTHONPATH=. poetry run python tools/forecast_audit.py
```

<!-- markdownlint-enable MD010 -->

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. poetry run pytest tests/forecast/test_audit_cli.py -v`
Expected: PASS (1 test).

- [ ] **Step 5: Commit**

```bash
git add tools/forecast_audit.py tests/forecast/test_audit_cli.py Makefile
git commit -m "feat(forecast): read-only forecast-audit CLI + make target"
```

---

## Task 10: Full DoD gate + regression check

**Files:** none (verification only)

- [ ] **Step 1: Lint + format**

Run: `make lint-py`
Expected: clean (ruff format + lint). Fix any findings, re-run.

- [ ] **Step 2: Type-check**

Run: `make typecheck`
Expected: mypy strict passes for the new `analytics/forecast/` modules and
`tools/forecast_audit.py`. Common fixes: annotate lambdas via named helpers; add
explicit `dtype=` on numpy arrays; type the `dict[str, object]` row builder.

- [ ] **Step 3: Full test suite**

Run: `make test`
Expected: green, with ~24 new tests under `tests/forecast/`. State the new count.

- [ ] **Step 4: Regression goldens unmoved**

Run: `make test-regression`
Expected: **3/3 goldens UNMOVED.** This package is read-only and additive — it
touches nothing in the existing backtest pipeline. If a golden moves, STOP and
find what leaked; do not regenerate.

- [ ] **Step 5: Commit (if any lint/type fixes were made)**

```bash
git add -A
git commit -m "chore(forecast): satisfy DoD gate (lint/type/test/regression)"
```

---

## Task 11: Produce the G2 verdict

**Files:**

- Create: `docs/audits/2026-06-15-p2-ewmac-trend-g2.md` (adjust date to ship date)

- [ ] **Step 1: Run the audit against the real (read-only) DB**

Run: `make buibui-forecast-audit`
Expected: the three tables print (breadth contrast, cost sensitivity, per-speed).
This is **read-only** — never run the live daemon.

- [ ] **Step 2: Write the verdict doc**

Capture, in markdownlint-clean markdown (every fence has a language; table
delimiters spaced `| --- |`):

- the universe vs majors-only Sharpe/Sortino/max-DD/ann-vol;
- the DSR, PBO, bootstrap-CI (annualised Sharpe lo/hi), MinTRL vs `days`;
- the cost-sensitivity table (does it stay positive to 8–16 bps?);
- the per-speed Sharpes and the **H2 finding** (does s64_256 carry the cycle bias
  vs the combined book?);
- an explicit **G2 PASS / MARGINAL / FAIL** call with reasoning, following the
  rule: marginal on majors but positive on breadth → proceed (breadth is the
  mechanism).

- [ ] **Step 3: Commit**

```bash
git add docs/audits/2026-06-15-p2-ewmac-trend-g2.md
git commit -m "docs(forecast): P2 EWMAC trend sleeve G2 verdict"
```

---

## Post-implementation (not tasks — handled by skills after the branch)

- Run `/post-branch` to sync CLAUDE.md (`analytics/forecast/` package + the
  `tools/forecast_audit.py` + Makefile target), README, and the SoT/MEMORY
  (P2 sub-project A status, G2 verdict link).
- Deferred to later sub-projects (do NOT build here): full IDM + correlation
  optimiser (P3), cross-sectional momentum (P3), standalone carry sleeve (Q2),
  TA-confirmation fold-in (post-G2), range hindsight H1/H4/H5 (range follow-up),
  `buibui forecast` CLI subcommand + live wiring (post-G2).

---

## Self-review checklist (run before handing off)

- **Spec coverage:** §2 forecast math → Tasks 1–3; §3 position/P&L/governor →
  Tasks 4–5; §4 module layout → all tasks; §5 G2 methodology → Tasks 7–8 +
  Task 11; §6 out-of-scope → enforced (no IDM/XS/carry tasks); §7 DoD → Task 10.
- **Placeholder scan:** none — every code/test step has concrete code.
- **Type consistency:** `ForecastConfig`, `ForecastBookResult`,
  `G2Report`, `instrument_returns`, `run_forecast_backtest`, `equity_curve`,
  `replay_universe`, `replay_trials`, `load_daily_inputs`, `evaluate`,
  `build_report_row` are used with identical signatures across tasks.
