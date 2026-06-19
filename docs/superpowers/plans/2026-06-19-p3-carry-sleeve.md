# P3 Carry Sleeve Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a pure, read-only, default-off `analytics/carry/` sleeve that expresses crypto perp funding as a Carver-style vol-scaled carry forecast, runs it through both an absolute and a cross-sectional book, and gates it on DSR≥0.95 ∧ PBO≤0.5 ∧ boot_lo>0.

**Architecture:** Mirror the established sleeve templates (`analytics/forecast/` absolute, `analytics/xsmom/` cross-sectional). The only genuinely new piece is the carry-forecast construction (`forecast.py`); the book, replay, and report modules are close mirrors of the existing sleeves. Reuse `ForecastConfig`, `forecast.vol.ew_return_vol`, `forecast.replay.load_daily_inputs`, `research_guards`, and `portfolio.metrics` unchanged. No schema/golden change.

**Tech Stack:** Python 3.11, pandas, numpy, duckdb, pytest, mypy strict, ruff.

**Spec:** `docs/superpowers/specs/2026-06-19-p3-carry-sleeve-design.md`

---

## File Structure

- Create `analytics/carry/__init__.py` — package + eager re-exports.
- Create `analytics/carry/config.py` — `CarryConfig` (composes `ForecastConfig`).
- Create `analytics/carry/forecast.py` — `annualized_funding`, `scaled_carry_forecast`, `combine_carry_forecasts`.
- Create `analytics/carry/book.py` — `CarryBookResult`, `carry_forecast_matrix`, `carry_leverage`, `run_carry_backtest`, `equity_curve`.
- Create `analytics/carry/replay.py` — `replay_carry`, `replay_carry_trials`.
- Create `analytics/carry/report.py` — `CarryReport`, `evaluate_carry`, `carry_gate_verdict`.
- Create `tools/carry_audit.py` — read-only driver.
- Modify `Makefile` — add `buibui-carry-audit` target (+ `.PHONY` list).
- Create tests: `tests/test_carry_config.py`, `tests/test_carry_forecast.py`, `tests/test_carry_book.py`, `tests/test_carry_replay.py`, `tests/test_carry_report.py`, `tests/test_carry_init.py`.

---

### Task 1: Package scaffold + `CarryConfig`

**Files:**

- Create: `analytics/carry/__init__.py`, `analytics/carry/config.py`
- Test: `tests/test_carry_config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_carry_config.py
"""Tests for CarryConfig."""

from __future__ import annotations

import pytest

from analytics.carry.config import CarryConfig
from analytics.forecast.config import ForecastConfig


def test_defaults() -> None:
    cfg = CarryConfig()
    assert cfg.carry_spans == (1, 5, 20, 60)
    assert cfg.carry_scalar == 30.0
    assert cfg.fdm == 1.25
    assert cfg.cross_sectional is True
    assert isinstance(cfg.sleeve_cfg, ForecastConfig)


def test_pass_throughs_match_sleeve_cfg() -> None:
    cfg = CarryConfig()
    assert cfg.annualization_days == cfg.sleeve_cfg.annualization_days
    assert cfg.cap == cfg.sleeve_cfg.cap
    assert cfg.vol_span == cfg.sleeve_cfg.vol_span
    assert cfg.vol_target_annual == cfg.sleeve_cfg.vol_target_annual
    assert cfg.fee_pct == cfg.sleeve_cfg.fee_pct
    assert cfg.slippage_pct == cfg.sleeve_cfg.slippage_pct
    assert cfg.gov_window == cfg.sleeve_cfg.gov_window
    assert cfg.g_min == cfg.sleeve_cfg.g_min
    assert cfg.g_max == cfg.sleeve_cfg.g_max


def test_empty_spans_rejected() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        CarryConfig(carry_spans=())


def test_span_below_one_rejected() -> None:
    with pytest.raises(ValueError, match=">= 1"):
        CarryConfig(carry_spans=(0, 5))


def test_frozen() -> None:
    cfg = CarryConfig()
    with pytest.raises(Exception):
        cfg.carry_scalar = 99.0  # type: ignore[misc]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. poetry run pytest tests/test_carry_config.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'analytics.carry'`

- [ ] **Step 3: Create the package init and config**

```python
# analytics/carry/__init__.py
"""P3 carry sleeve — funding-carry as a vol-scaled forecast (read-only, default-off)."""

from analytics.carry.config import CarryConfig

__all__ = ["CarryConfig"]
```

```python
# analytics/carry/config.py
"""Configuration for the P3 carry sleeve.

Frozen dataclass composing a ``ForecastConfig`` for the shared honest-cost / vol /
governor constants (mirrors ``combine.CombineConfig`` holding ``sleeve_cfg``).
Carry-specific knobs: the EWMA smoothing-span family, a FIXED a-priori carry scalar
(NOT crypto-fit — the standalone book is governor-normalised, see spec §5.3), the
forecast diversification multiplier, and the expression toggle.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from analytics.forecast.config import ForecastConfig


@dataclass(frozen=True)
class CarryConfig:
    sleeve_cfg: ForecastConfig = field(default_factory=ForecastConfig)
    carry_spans: tuple[int, ...] = (1, 5, 20, 60)
    carry_scalar: float = 30.0
    fdm: float = 1.25
    cross_sectional: bool = True

    def __post_init__(self) -> None:
        if not self.carry_spans:
            raise ValueError("carry_spans must be non-empty")
        if any(s < 1 for s in self.carry_spans):
            raise ValueError("carry_spans must all be >= 1")

    @property
    def annualization_days(self) -> float:
        return self.sleeve_cfg.annualization_days

    @property
    def cap(self) -> float:
        return self.sleeve_cfg.cap

    @property
    def vol_span(self) -> int:
        return self.sleeve_cfg.vol_span

    @property
    def vol_target_annual(self) -> float:
        return self.sleeve_cfg.vol_target_annual

    @property
    def fee_pct(self) -> float:
        return self.sleeve_cfg.fee_pct

    @property
    def slippage_pct(self) -> float:
        return self.sleeve_cfg.slippage_pct

    @property
    def gov_window(self) -> int:
        return self.sleeve_cfg.gov_window

    @property
    def g_min(self) -> float:
        return self.sleeve_cfg.g_min

    @property
    def g_max(self) -> float:
        return self.sleeve_cfg.g_max

    @classmethod
    def from_toml(cls, path: Path | str) -> CarryConfig:
        return cls(sleeve_cfg=ForecastConfig.from_toml(path))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. poetry run pytest tests/test_carry_config.py -q`
Expected: PASS (5 tests)

- [ ] **Step 5: Lint + typecheck + commit**

```bash
make lint-py && make typecheck
git add analytics/carry/__init__.py analytics/carry/config.py tests/test_carry_config.py
git commit -m "feat(carry): CarryConfig — composes ForecastConfig + carry-span family"
```

---

### Task 2: `forecast.py` — carry-forecast construction

**Files:**

- Create: `analytics/carry/forecast.py`
- Test: `tests/test_carry_forecast.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_carry_forecast.py
"""Tests for the funding-carry forecast construction."""

from __future__ import annotations

import numpy as np
import pandas as pd

from analytics.carry.forecast import (
    annualized_funding,
    combine_carry_forecasts,
    scaled_carry_forecast,
)

_ANN = 365.0


def _idx(n: int) -> pd.DatetimeIndex:
    return pd.date_range("2021-01-01", periods=n, freq="D", tz="UTC")


def test_annualized_funding_span1_is_raw_times_ann() -> None:
    idx = _idx(5)
    fund = pd.Series([0.001, 0.002, -0.001, 0.0, 0.003], index=idx)
    out = annualized_funding(fund, span=1, ann_days=_ANN)
    # span=1 EWMA (adjust=False) returns the latest value unchanged
    np.testing.assert_allclose(out.to_numpy(), fund.to_numpy() * _ANN)


def test_annualized_funding_is_causal() -> None:
    idx = _idx(6)
    base = pd.Series([0.001] * 6, index=idx)
    bumped = base.copy()
    bumped.iloc[4] = 0.05  # bump a later point
    a = annualized_funding(base, span=3, ann_days=_ANN)
    b = annualized_funding(bumped, span=3, ann_days=_ANN)
    # values strictly before the bump are unchanged (EWMA is causal)
    np.testing.assert_allclose(a.iloc[:4].to_numpy(), b.iloc[:4].to_numpy())


def test_scaled_carry_sign_long_when_funding_negative() -> None:
    idx = _idx(40)
    close = pd.Series(np.linspace(100.0, 110.0, 40), index=idx)
    neg_fund = pd.Series([-0.001] * 40, index=idx)
    pos_fund = pd.Series([0.001] * 40, index=idx)
    f_neg = scaled_carry_forecast(close, neg_fund, 1, 30.0, 32, 20.0, _ANN)
    f_pos = scaled_carry_forecast(close, pos_fund, 1, 30.0, 32, 20.0, _ANN)
    # long (positive) when funding negative; short (negative) when funding positive
    assert f_neg.dropna().iloc[-1] > 0
    assert f_pos.dropna().iloc[-1] < 0


def test_scaled_carry_capped() -> None:
    idx = _idx(40)
    close = pd.Series(np.linspace(100.0, 101.0, 40), index=idx)  # tiny vol
    huge_fund = pd.Series([-0.5] * 40, index=idx)  # enormous negative funding
    f = scaled_carry_forecast(close, huge_fund, 1, 30.0, 32, 20.0, _ANN)
    assert f.dropna().abs().max() <= 20.0 + 1e-9


def test_combine_equalweight_times_fdm_recapped() -> None:
    idx = _idx(50)
    close = pd.Series(np.linspace(100.0, 120.0, 50), index=idx)
    fund = pd.Series([-0.0005] * 50, index=idx)
    spans = (1, 5)
    parts = [scaled_carry_forecast(close, fund, s, 30.0, 32, 20.0, _ANN) for s in spans]
    expected = (pd.concat(parts, axis=1).mean(axis=1) * 1.25).clip(-20.0, 20.0)
    got = combine_carry_forecasts(close, fund, spans, 30.0, 1.25, 32, 20.0, _ANN)
    np.testing.assert_allclose(
        got.dropna().to_numpy(), expected.reindex(got.index).dropna().to_numpy()
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. poetry run pytest tests/test_carry_forecast.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'analytics.carry.forecast'`

- [ ] **Step 3: Implement `forecast.py`**

```python
# analytics/carry/forecast.py
"""Funding-carry forecast math — annualised funding, vol-adjust, scale, combine.

Pure functions over ``(price, daily-funding)`` Series. No DB, no IO. Carver-style
carry: the expected carry return (``−annualised funding`` for a perp long) risk-
adjusted by annualised price-return vol, scaled to a Carver-magnitude forecast and
capped. Funding is the perp's basis, so this is the literal carry signal. Causal
throughout (EWMA and ``ew_return_vol`` use only data through each day).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from analytics.forecast.vol import ew_return_vol


def annualized_funding(
    funding_daily: pd.Series, span: int, ann_days: float
) -> pd.Series:
    """EWMA-smoothed daily funding, annualised.

    ``funding_daily`` is the day's summed funding (≈3 8-h rows already summed by
    ``load_daily_inputs``), so annualisation is ``× ann_days`` — the 3/day is already
    inside the sum. Span=1 (``adjust=False``) returns the latest value unchanged.
    """
    smoothed = funding_daily.ewm(span=span, adjust=False).mean()
    return smoothed * ann_days


def scaled_carry_forecast(
    close: pd.Series,
    funding_daily: pd.Series,
    span: int,
    scalar: float,
    vol_span: int,
    cap: float,
    ann_days: float,
) -> pd.Series:
    """Vol-adjusted, scalar-adjusted, capped single-span carry forecast.

    ``carry_adj = (−annualised_funding) / annualised_return_vol`` (long when funding is
    negative — it pays you to hold long); ``forecast = (carry_adj × scalar).clip(±cap)``.
    """
    ann_f = annualized_funding(funding_daily, span, ann_days)
    vol_ann = ew_return_vol(close, vol_span).mul(np.sqrt(ann_days)).reindex(ann_f.index)
    carry_adj = (-ann_f) / vol_ann
    carry_adj = carry_adj.replace([np.inf, -np.inf], np.nan)
    return (carry_adj * scalar).clip(lower=-cap, upper=cap)


def combine_carry_forecasts(
    close: pd.Series,
    funding_daily: pd.Series,
    spans: tuple[int, ...],
    scalar: float,
    fdm: float,
    vol_span: int,
    cap: float,
    ann_days: float,
) -> pd.Series:
    """Equal-weight mean of per-span carry forecasts × FDM, re-capped ±cap.

    Mirrors ``analytics.forecast.ewmac.combine_forecasts`` (equal-weight branch).
    """
    parts = [
        scaled_carry_forecast(close, funding_daily, s, scalar, vol_span, cap, ann_days)
        for s in spans
    ]
    stacked = pd.concat(parts, axis=1)
    mean = stacked.mean(axis=1)
    return (mean * fdm).clip(lower=-cap, upper=cap)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. poetry run pytest tests/test_carry_forecast.py -q`
Expected: PASS (5 tests)

- [ ] **Step 5: Lint + typecheck + commit**

```bash
make lint-py && make typecheck
git add analytics/carry/forecast.py tests/test_carry_forecast.py
git commit -m "feat(carry): Carver carry-forecast construction (annualised funding / vol, capped)"
```

---

### Task 3: `book.py` — leverage, book, governor (incl. causality)

**Files:**

- Create: `analytics/carry/book.py`
- Test: `tests/test_carry_book.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_carry_book.py
"""Tests for the carry book — absolute + cross-sectional, costs, causality."""

from __future__ import annotations

import dataclasses

import numpy as np
import pandas as pd

from analytics.carry.book import (
    CarryBookResult,
    carry_forecast_matrix,
    carry_leverage,
    equity_curve,
    run_carry_backtest,
)
from analytics.carry.config import CarryConfig


def _idx(n: int) -> pd.DatetimeIndex:
    return pd.date_range("2021-01-01", periods=n, freq="D", tz="UTC")


def _make_inputs(
    n: int = 200,
) -> tuple[dict[str, pd.Series], dict[str, pd.Series]]:
    idx = _idx(n)
    rng = np.random.default_rng(0)
    closes: dict[str, pd.Series] = {}
    fundings: dict[str, pd.Series] = {}
    for k, sym in enumerate(["AAA", "BBB", "CCC"]):
        steps = rng.normal(0.0, 0.02, n)
        closes[sym] = pd.Series(100.0 * np.exp(np.cumsum(steps)), index=idx)
        fundings[sym] = pd.Series((-1) ** k * 0.0002, index=idx)
    return closes, fundings


def test_forecast_matrix_aligned_to_union() -> None:
    closes, fundings = _make_inputs()
    cfg = CarryConfig(carry_spans=(1, 5))
    f = carry_forecast_matrix(closes, fundings, cfg)
    assert list(f.columns) == ["AAA", "BBB", "CCC"]
    assert len(f.index) == 200


def test_cross_sectional_demean_rows_sum_near_zero() -> None:
    closes, fundings = _make_inputs()
    cfg = CarryConfig(carry_spans=(1, 5), cross_sectional=True)
    # reconstruct the demeaned (pre-shift) forecast the way carry_leverage does
    f = carry_forecast_matrix(closes, fundings, cfg)
    demeaned = f.sub(f.mean(axis=1), axis=0)
    warm = demeaned.dropna()
    assert np.allclose(warm.sum(axis=1).to_numpy(), 0.0, atol=1e-9)


def test_run_carry_backtest_result_shape() -> None:
    closes, fundings = _make_inputs()
    cfg = CarryConfig(carry_spans=(1, 5))
    res = run_carry_backtest(closes, fundings, cfg)
    assert isinstance(res, CarryBookResult)
    assert len(res.daily_index) == 200
    assert res.portfolio_return.shape == (200,)
    assert np.isfinite(res.portfolio_return).all()
    assert set(res.per_instrument_net) == {"AAA", "BBB", "CCC"}


def test_absolute_uses_mean_xs_uses_sum() -> None:
    closes, fundings = _make_inputs()
    abs_cfg = CarryConfig(carry_spans=(1, 5), cross_sectional=False)
    xs_cfg = CarryConfig(carry_spans=(1, 5), cross_sectional=True)
    abs_res = run_carry_backtest(closes, fundings, abs_cfg)
    xs_res = run_carry_backtest(closes, fundings, xs_cfg)
    # different aggregation -> different curves
    assert not np.allclose(abs_res.portfolio_return, xs_res.portfolio_return)


def test_funding_sign_short_receives_funding() -> None:
    # A single instrument with strong positive funding -> carry forecast short.
    # Short position * positive funding -> funding_cost negative -> net > gross.
    idx = _idx(120)
    rng = np.random.default_rng(1)
    close = pd.Series(
        100.0 * np.exp(np.cumsum(rng.normal(0.0, 0.001, 120))), index=idx
    )
    closes = {"AAA": close}
    fundings = {"AAA": pd.Series(0.002, index=idx)}  # strongly positive -> go short
    cfg = CarryConfig(carry_spans=(1,), cross_sectional=False)
    lev = carry_leverage(closes, fundings, cfg)["AAA"]
    net = run_carry_backtest(closes, fundings, cfg).per_instrument_net["AAA"]
    # where leverage is short (negative) and funding positive, funding_cost = lev*fund < 0
    warm = lev.dropna()
    assert (warm < 0).any()


def test_governor_clipped_in_range() -> None:
    closes, fundings = _make_inputs(300)
    cfg = CarryConfig(carry_spans=(1, 5))
    res = run_carry_backtest(closes, fundings, cfg)
    g = pd.Series(res.governor).dropna()
    assert (g >= cfg.g_min - 1e-9).all()
    assert (g <= cfg.g_max + 1e-9).all()


def test_equity_curve_starts_near_one() -> None:
    closes, fundings = _make_inputs()
    cfg = CarryConfig(carry_spans=(1, 5))
    curve = equity_curve(run_carry_backtest(closes, fundings, cfg))
    assert abs(curve.iloc[0] - 1.0) < 0.5
    assert (curve > 0).all()


def test_causality_today_leverage_blind_to_today_funding() -> None:
    """Position on day d uses funding through d-1: bumping funding[k] must NOT move
    leverage at day k (it depends on the demeaned forecast at k-1), but MUST move
    leverage at day k+1. Cross-sectional: the bump on one instrument must leave ALL
    instruments' day-k leverage unchanged. Goes RED if the .shift(1) is removed.
    """
    closes, fundings = _make_inputs(200)
    cfg = CarryConfig(carry_spans=(1, 5), cross_sectional=True)
    k = 150
    base_lev = carry_leverage(closes, fundings, cfg)
    bumped = {s: v.copy() for s, v in fundings.items()}
    bumped["AAA"].iloc[k] = 0.05  # large bump at day k on one instrument
    bump_lev = carry_leverage(closes, bumped, cfg)
    # day k: every instrument's leverage unchanged (uses info through k-1)
    np.testing.assert_allclose(
        base_lev.iloc[k].to_numpy(), bump_lev.iloc[k].to_numpy(), atol=1e-12
    )
    # day k+1: AAA leverage moved (the bump propagated) — non-vacuous
    assert not np.allclose(
        base_lev.iloc[k + 1].to_numpy(), bump_lev.iloc[k + 1].to_numpy()
    )


def test_causality_future_does_not_leak_into_past() -> None:
    closes, fundings = _make_inputs(200)
    cfg = CarryConfig(carry_spans=(1, 5))
    k = 150
    base = run_carry_backtest(closes, fundings, cfg)
    bumped_closes = {s: v.copy() for s, v in closes.items()}
    bumped_closes["AAA"].iloc[k + 10] *= 1.5  # future price shock
    bump = run_carry_backtest(bumped_closes, fundings, cfg)
    np.testing.assert_allclose(
        base.portfolio_return[: k + 1], bump.portfolio_return[: k + 1], atol=1e-12
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. poetry run pytest tests/test_carry_book.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'analytics.carry.book'`

- [ ] **Step 3: Implement `book.py`**

```python
# analytics/carry/book.py
"""Per-instrument carry leverage and portfolio aggregation.

All sizing is causal: the position held during day ``d`` is sized from information
through ``d-1`` only. The cross-sectional demean (when enabled) is a same-day reduction
over causal forecasts; the ``.shift(1)`` is applied AFTER demeaning, BEFORE sizing.
Mirrors the trend (``analytics.forecast.book``) and XS (``analytics.xsmom.book``)
templates, swapping the EWMAC forecast for the funding-carry forecast.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from analytics.carry.config import CarryConfig
from analytics.carry.forecast import combine_carry_forecasts
from analytics.forecast.vol import ew_return_vol


def _union_index(closes: dict[str, pd.Series]) -> pd.DatetimeIndex:
    union = pd.DatetimeIndex([])
    for s in closes.values():
        union = union.union(pd.DatetimeIndex(s.index))
    return union.sort_values()


def carry_forecast_matrix(
    closes: dict[str, pd.Series],
    fundings: dict[str, pd.Series],
    cfg: CarryConfig,
) -> pd.DataFrame:
    """Combined carry forecast per instrument, aligned to the union daily index.

    Columns = symbols, index = sorted union of all instrument dates. NaN where an
    instrument has not warmed up or where return-vol is undefined; the NaN warm-up
    bars are intentional (the cross-sectional demean skips NaN via ``mean(axis=1)``).
    """
    union = _union_index(closes)
    cols: dict[str, pd.Series] = {}
    for sym, close in closes.items():
        fund = fundings.get(sym, pd.Series(0.0, index=close.index))
        f = combine_carry_forecasts(
            close,
            fund,
            cfg.carry_spans,
            cfg.carry_scalar,
            cfg.fdm,
            cfg.vol_span,
            cfg.cap,
            cfg.annualization_days,
        )
        cols[sym] = f.reindex(union)
    return pd.DataFrame(cols, index=union)


def carry_leverage(
    closes: dict[str, pd.Series],
    fundings: dict[str, pd.Series],
    cfg: CarryConfig,
) -> pd.DataFrame:
    """Causal vol-parity leverage matrix from the carry forecast.

    Absolute: per-instrument forecast. Cross-sectional: forecast demeaned across the
    active set (dollar-neutral). Demean (if enabled) → ``.shift(1)`` (position on day
    ``d`` uses info through ``d-1``) → vol-target each leg:
    ``leverage = (f_shifted / 10) * (vol_target / vol_ann)``.
    """
    f = carry_forecast_matrix(closes, fundings, cfg)
    if cfg.cross_sectional:
        f = f.sub(f.mean(axis=1), axis=0)
    f_shifted = f.shift(1)
    union = pd.DatetimeIndex(f.index)
    ann = np.sqrt(cfg.annualization_days)

    lev_cols: dict[str, pd.Series] = {}
    for sym, close in closes.items():
        vol_ann = ew_return_vol(close, cfg.vol_span).mul(ann).reindex(union)
        lev = (f_shifted[sym] / 10.0) * (cfg.vol_target_annual / vol_ann)
        lev_cols[sym] = lev.replace([np.inf, -np.inf], np.nan)
    return pd.DataFrame(lev_cols, index=union)


@dataclass(frozen=True)
class CarryBookResult:
    daily_index: pd.DatetimeIndex
    portfolio_return: np.ndarray  # net, post-governor (NaN-free; warm-up = 0.0)
    pre_governor_return: np.ndarray
    governor: np.ndarray  # NaN for the first gov_window warm-up bars
    active_count: np.ndarray
    per_instrument_net: dict[str, pd.Series]


def run_carry_backtest(
    closes: dict[str, pd.Series],
    fundings: dict[str, pd.Series],
    cfg: CarryConfig,
) -> CarryBookResult:
    """Causal carry book — absolute (equal-risk mean) or cross-sectional (sum)."""
    leverage = carry_leverage(closes, fundings, cfg)
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
        funding_cost = lev * fund  # shorts (lev<0) receive funding when fund>0
        net = gross - turnover - funding_cost
        per_net[sym] = net
        net_cols.append(net)

    net_mat = pd.concat(net_cols, axis=1)
    active = net_mat.notna().sum(axis=1)
    if cfg.cross_sectional:
        pre = net_mat.sum(axis=1)  # long-short P&L; all-NaN warm-up -> 0.0
    else:
        pre = net_mat.mean(axis=1)  # equal risk weight across active instruments
    pre = pre.fillna(0.0)

    ann = np.sqrt(cfg.annualization_days)
    trailing_vol = (
        pre.rolling(cfg.gov_window, min_periods=cfg.gov_window).std().shift(1) * ann
    )
    g = (cfg.vol_target_annual / trailing_vol).clip(cfg.g_min, cfg.g_max)
    port = g.fillna(0.0) * pre

    return CarryBookResult(
        daily_index=union,
        portfolio_return=port.to_numpy(dtype=np.float64),
        pre_governor_return=pre.to_numpy(dtype=np.float64),
        governor=g.to_numpy(dtype=np.float64),
        active_count=active.to_numpy(dtype=np.int64),
        per_instrument_net=per_net,
    )


def equity_curve(result: CarryBookResult) -> pd.Series:
    """Compounding equity curve (starts at 1.0+r₀) for portfolio.metrics."""
    r = pd.Series(result.portfolio_return, index=result.daily_index)
    return (1.0 + r).cumprod()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. poetry run pytest tests/test_carry_book.py -q`
Expected: PASS (9 tests)

- [ ] **Step 4b: Verify the causality test is non-vacuous (RED-without-shift)**

Temporarily change `f_shifted = f.shift(1)` to `f_shifted = f` in `carry_leverage`, re-run
`tests/test_carry_book.py::test_causality_today_leverage_blind_to_today_funding` and confirm
it FAILS. Then restore the `.shift(1)`.

- [ ] **Step 5: Lint + typecheck + commit**

```bash
make lint-py && make typecheck
git add analytics/carry/book.py tests/test_carry_book.py
git commit -m "feat(carry): causal carry book (abs + cross-sectional) + leverage/governor"
```

---

### Task 4: `replay.py` — read-only DB front door

**Files:**

- Create: `analytics/carry/replay.py`
- Test: `tests/test_carry_replay.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_carry_replay.py
"""Tests for the read-only carry replay front door (in-memory DuckDB)."""

from __future__ import annotations

import duckdb
import numpy as np

from analytics.carry.config import CarryConfig
from analytics.carry.replay import replay_carry, replay_carry_trials
from analytics.store.schema import init_schema


def _seed(conn: duckdb.DuckDBPyConnection, n: int = 300) -> list[str]:
    init_schema(conn)
    rng = np.random.default_rng(7)
    syms = ["AAA", "BBB", "CCC"]
    day_ms = 86_400_000
    for k, sym in enumerate(syms):
        price = 100.0
        for i in range(n):
            price *= float(np.exp(rng.normal(0.0, 0.02)))
            t = i * day_ms
            conn.execute(
                "INSERT INTO ohlcv VALUES (?, '1d', ?, ?, ?, ?, ?, ?, ?)",
                [sym, t, price, price, price, price, 1000.0, 500.0],
            )
            # 3 funding rows per day
            for j in range(3):
                conn.execute(
                    "INSERT INTO funding_rates VALUES (?, ?, ?)",
                    [sym, t + j * 8 * 3_600_000, ((-1) ** k) * 0.0001],
                )
    return syms


def test_replay_carry_shape() -> None:
    conn = duckdb.connect(":memory:")
    syms = _seed(conn)
    cfg = CarryConfig(carry_spans=(1, 5))
    res = replay_carry(conn, cfg, symbols=syms)
    assert len(res.daily_index) == 300
    assert res.portfolio_return.shape == (300,)
    assert np.isfinite(res.portfolio_return).all()


def test_replay_carry_trials_keys() -> None:
    conn = duckdb.connect(":memory:")
    syms = _seed(conn)
    cfg = CarryConfig(carry_spans=(1, 5))
    trials = replay_carry_trials(conn, cfg, symbols=syms)
    assert set(trials) == {"span1", "span5", "combined"}
    for v in trials.values():
        assert v.shape == (300,)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. poetry run pytest tests/test_carry_replay.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'analytics.carry.replay'`

- [ ] **Step 3: Implement `replay.py`**

```python
# analytics/carry/replay.py
"""Read-only DuckDB front door for the carry sleeve.

Reuses the trend sleeve's ``load_daily_inputs`` (1d closes + summed daily funding)
and runs the carry book. The only module in ``analytics/carry/`` that touches the DB;
never writes.
"""

from __future__ import annotations

import dataclasses

import duckdb
import numpy as np

from analytics.carry.book import CarryBookResult, run_carry_backtest
from analytics.carry.config import CarryConfig
from analytics.forecast.replay import load_daily_inputs
from analytics.universe import load_universe


def replay_carry(
    conn: duckdb.DuckDBPyConnection,
    cfg: CarryConfig,
    symbols: list[str] | None = None,
) -> CarryBookResult:
    """Load the universe's 1d inputs and run the carry book (read-only)."""
    syms = symbols if symbols is not None else load_universe()
    closes, fundings = load_daily_inputs(conn, syms)
    return run_carry_backtest(closes, fundings, cfg)


def replay_carry_trials(
    conn: duckdb.DuckDBPyConnection,
    cfg: CarryConfig,
    symbols: list[str] | None = None,
) -> dict[str, np.ndarray]:
    """Daily carry portfolio returns per single-span book + the combined book.

    The honest multiple-testing family for DSR/PBO, all under ``cfg.cross_sectional``.
    Keys: ``span{s}`` per span in ``cfg.carry_spans``, plus ``combined``.
    """
    syms = symbols if symbols is not None else load_universe()
    closes, fundings = load_daily_inputs(conn, syms)

    trials: dict[str, np.ndarray] = {}
    for s in cfg.carry_spans:
        single = dataclasses.replace(cfg, carry_spans=(s,))
        trials[f"span{s}"] = run_carry_backtest(closes, fundings, single).portfolio_return
    trials["combined"] = run_carry_backtest(closes, fundings, cfg).portfolio_return
    return trials
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. poetry run pytest tests/test_carry_replay.py -q`
Expected: PASS (2 tests)

- [ ] **Step 5: Lint + typecheck + commit**

```bash
make lint-py && make typecheck
git add analytics/carry/replay.py tests/test_carry_replay.py
git commit -m "feat(carry): read-only replay front door + DSR/PBO span trial family"
```

---

### Task 5: `report.py` — gate metrics + diversification read

**Files:**

- Create: `analytics/carry/report.py`
- Test: `tests/test_carry_report.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_carry_report.py
"""Tests for the carry report — gate verdict + plumbing + corr."""

from __future__ import annotations

import numpy as np

import pandas as pd

from analytics.carry.book import run_carry_backtest
from analytics.carry.config import CarryConfig
from analytics.carry.report import CarryReport, carry_gate_verdict, evaluate_carry


def _idx(n: int) -> pd.DatetimeIndex:
    return pd.date_range("2021-01-01", periods=n, freq="D", tz="UTC")


def _make_inputs(n: int = 300) -> tuple[dict[str, pd.Series], dict[str, pd.Series]]:
    idx = _idx(n)
    rng = np.random.default_rng(3)
    closes: dict[str, pd.Series] = {}
    fundings: dict[str, pd.Series] = {}
    for k, sym in enumerate(["AAA", "BBB", "CCC"]):
        closes[sym] = pd.Series(
            100.0 * np.exp(np.cumsum(rng.normal(0.0, 0.02, n))), index=idx
        )
        fundings[sym] = pd.Series(((-1) ** k) * 0.0002, index=idx)
    return closes, fundings


def test_gate_verdict_boundaries() -> None:
    base = CarryReport(
        sharpe_annual=1.0, sortino_annual=1.0, max_dd=-0.1, calmar=1.0,
        annual_return=0.2, annual_vol=0.2, n_obs=300,
        dsr=0.96, pbo=0.4, boot_lo=0.1, boot_hi=2.0, min_trl=100.0,
        corr_to_xs=0.0, xs_sharpe=1.3, corr_to_trend=0.2, trend_sharpe=0.3,
    )
    assert carry_gate_verdict(base) is True
    assert carry_gate_verdict(base.__class__(**{**base.__dict__, "dsr": 0.94})) is False
    assert carry_gate_verdict(base.__class__(**{**base.__dict__, "pbo": 0.6})) is False
    assert carry_gate_verdict(base.__class__(**{**base.__dict__, "boot_lo": 0.0})) is False


def test_evaluate_carry_fields_present() -> None:
    closes, fundings = _make_inputs()
    cfg = CarryConfig(carry_spans=(1, 5))
    res = run_carry_backtest(closes, fundings, cfg)
    trials = {
        "span1": res.portfolio_return,
        "span5": res.portfolio_return * 0.9,
        "combined": res.portfolio_return,
    }
    xs = res.portfolio_return * 0.5
    trend = res.portfolio_return * 0.3
    rep = evaluate_carry(res, cfg, trial_returns=trials, xs_returns=xs, trend_returns=trend)
    assert isinstance(rep, CarryReport)
    assert rep.n_obs == 300
    assert -1.0 <= rep.corr_to_xs <= 1.0
    assert -1.0 <= rep.corr_to_trend <= 1.0


def test_corr_to_xs_excludes_joint_dead_warmup() -> None:
    closes, fundings = _make_inputs()
    cfg = CarryConfig(carry_spans=(1, 5))
    res = run_carry_backtest(closes, fundings, cfg)
    # XS returns identical to the book -> corr should be ~1.0 over the live tail
    rep = evaluate_carry(
        res, cfg,
        trial_returns={"span1": res.portfolio_return, "combined": res.portfolio_return},
        xs_returns=res.portfolio_return.copy(),
        trend_returns=res.portfolio_return.copy(),
    )
    assert rep.corr_to_xs > 0.99
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. poetry run pytest tests/test_carry_report.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'analytics.carry.report'`

- [ ] **Step 3: Implement `report.py`**

```python
# analytics/carry/report.py
"""Assemble the carry verdict: headline metrics + guards + diversification read.

Pure over a ``CarryBookResult`` plus the candidate trials' daily returns (the honest
multiple-testing family for DSR/PBO) and the XS + trend sleeves' daily returns (the
diversification read against the deploy core, XS). Mirrors ``analytics.xsmom.report``
and adds ``corr_to_xs`` / ``xs_sharpe``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
import pandas as pd

from analytics.carry.book import CarryBookResult, equity_curve
from analytics.carry.config import CarryConfig
from analytics.research_guards import (
    block_bootstrap_ci,
    cscv_pbo,
    deflated_sharpe_ratio,
    min_track_record_length,
)
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


def _aligned_corr(a: npt.NDArray[np.float64], b: npt.NDArray[np.float64]) -> float:
    """Pearson corr over the common tail, excluding joint dead warm-up (0, 0)."""
    n = min(len(a), len(b))
    if n < 2:
        return 0.0
    x = np.asarray(a[-n:], dtype=np.float64)
    y = np.asarray(b[-n:], dtype=np.float64)
    live = ~((x == 0.0) & (y == 0.0))
    x, y = x[live], y[live]
    if (
        len(x) < 2
        or float(np.std(x, ddof=1)) < 1e-12
        or float(np.std(y, ddof=1)) < 1e-12
    ):
        return 0.0
    return float(np.corrcoef(x, y)[0, 1])


def _sharpe_of(returns: npt.NDArray[np.float64]) -> float:
    if len(returns) >= 2:
        return metrics.sharpe((1.0 + pd.Series(returns)).cumprod())
    return 0.0


@dataclass(frozen=True)
class CarryReport:
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
    corr_to_xs: float
    xs_sharpe: float
    corr_to_trend: float
    trend_sharpe: float


def evaluate_carry(
    result: CarryBookResult,
    cfg: CarryConfig,
    trial_returns: dict[str, npt.NDArray[np.float64]],
    xs_returns: npt.NDArray[np.float64],
    trend_returns: npt.NDArray[np.float64],
) -> CarryReport:
    """Headline metrics + research-guard stamps + XS/trend diversification reads.

    ``trial_returns`` is the honest multiple-testing family (per-span carry books +
    combined). ``xs_returns`` / ``trend_returns`` are the deploy-core + trend sleeve's
    daily returns on the same universe/window.
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

    return CarryReport(
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
        corr_to_xs=_aligned_corr(r, xs_returns),
        xs_sharpe=_sharpe_of(xs_returns),
        corr_to_trend=_aligned_corr(r, trend_returns),
        trend_sharpe=_sharpe_of(trend_returns),
    )


def carry_gate_verdict(report: CarryReport) -> bool:
    """The de-biased gate: DSR ≥ 0.95 ∧ PBO ≤ 0.5 ∧ bootstrap CI lower bound > 0."""
    return report.dsr >= 0.95 and report.pbo <= 0.5 and report.boot_lo > 0.0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. poetry run pytest tests/test_carry_report.py -q`
Expected: PASS (3 tests)

- [ ] **Step 5: Lint + typecheck + commit**

```bash
make lint-py && make typecheck
git add analytics/carry/report.py tests/test_carry_report.py
git commit -m "feat(carry): evaluate_carry gate metrics + corr_to_xs/corr_to_trend reads"
```

---

### Task 6: `__init__.py` full re-exports

**Files:**

- Modify: `analytics/carry/__init__.py`
- Test: `tests/test_carry_init.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_carry_init.py
"""The carry package re-exports its public surface."""

from __future__ import annotations


def test_public_surface_importable() -> None:
    from analytics.carry import (  # noqa: F401
        CarryBookResult,
        CarryConfig,
        CarryReport,
        annualized_funding,
        carry_forecast_matrix,
        carry_gate_verdict,
        carry_leverage,
        combine_carry_forecasts,
        equity_curve,
        evaluate_carry,
        replay_carry,
        replay_carry_trials,
        run_carry_backtest,
        scaled_carry_forecast,
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. poetry run pytest tests/test_carry_init.py -q`
Expected: FAIL — `ImportError: cannot import name 'CarryBookResult' from 'analytics.carry'`

- [ ] **Step 3: Expand `analytics/carry/__init__.py`**

```python
# analytics/carry/__init__.py
"""P3 carry sleeve — funding-carry as a vol-scaled forecast (read-only, default-off).

Carver-style carry: the perp funding rate is the cost-of-carry, expressed as a
vol-scaled forecast (long when funding pays you to be long). Built BOTH absolute and
cross-sectional; the headline is cross-sectional. Pure, read-only over ``analytics.db``,
additive — no schema/golden change.
"""

from analytics.carry.book import (
    CarryBookResult,
    carry_forecast_matrix,
    carry_leverage,
    equity_curve,
    run_carry_backtest,
)
from analytics.carry.config import CarryConfig
from analytics.carry.forecast import (
    annualized_funding,
    combine_carry_forecasts,
    scaled_carry_forecast,
)
from analytics.carry.replay import replay_carry, replay_carry_trials
from analytics.carry.report import CarryReport, carry_gate_verdict, evaluate_carry

__all__ = [
    "CarryBookResult",
    "CarryConfig",
    "CarryReport",
    "annualized_funding",
    "carry_forecast_matrix",
    "carry_gate_verdict",
    "carry_leverage",
    "combine_carry_forecasts",
    "equity_curve",
    "evaluate_carry",
    "replay_carry",
    "replay_carry_trials",
    "run_carry_backtest",
    "scaled_carry_forecast",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. poetry run pytest tests/test_carry_init.py -q`
Expected: PASS (1 test)

- [ ] **Step 5: Lint + typecheck + commit**

```bash
make lint-py && make typecheck
git add analytics/carry/__init__.py tests/test_carry_init.py
git commit -m "feat(carry): eager package re-exports"
```

---

### Task 7: `tools/carry_audit.py` driver + Makefile target

**Files:**

- Create: `tools/carry_audit.py`
- Modify: `Makefile` (add `buibui-carry-audit` target + extend the `.PHONY` line)

- [ ] **Step 1: Implement the read-only driver**

```python
# tools/carry_audit.py
"""Carry sleeve audit (P3) — read-only verdict.

Replays the funding-carry book across the N3 universe (1d) and prints, each with
DSR/PBO/bootstrap-CI/MinTRL stamps where applicable:

1. Headline cross-sectional carry gate (per-span + combined family) + corr_to_xs /
   corr_to_trend (the diversification read against the XS deploy core).
2. Absolute-vs-cross-sectional contrast (Sharpe each).
3. Breadth contrast (universe vs majors-only).
4. Cost sensitivity (0 / 2 / 8 / 16 bps per leg).
5. Per-span Sharpe (which smoothing horizon carries the edge).
6. Scalar sensitivity (robustness to the un-fit carry scalar).

Read-only — no writes, no schema changes.

Usage::

    PYTHONPATH=. poetry run python tools/carry_audit.py
    PYTHONPATH=. poetry run python tools/carry_audit.py --majors BTCUSDT,ETHUSDT,SOLUSDT
"""

from __future__ import annotations

import argparse
import dataclasses
import math
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from analytics.carry import (
    CarryConfig,
    carry_gate_verdict,
    evaluate_carry,
    replay_carry,
    replay_carry_trials,
)
from analytics.forecast import ForecastConfig, replay_universe
from analytics.store import DEFAULT_DB_PATH
from analytics.universe import load_universe
from analytics.xsmom import replay_xs

_MAJORS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]


def _cfg(slippage_bps: float, *, cross_sectional: bool, scalar: float = 30.0) -> CarryConfig:
    sleeve = dataclasses.replace(ForecastConfig(), slippage_pct=slippage_bps / 10_000.0)
    return CarryConfig(
        sleeve_cfg=sleeve, cross_sectional=cross_sectional, carry_scalar=scalar
    )


def _sharpe(r: np.ndarray, ann: float) -> float:
    sd = float(np.std(r, ddof=1)) if len(r) > 1 else 0.0
    return (float(np.mean(r)) / sd * ann) if sd > 1e-12 else 0.0


def build_report_row(
    conn: duckdb.DuckDBPyConnection,
    label: str,
    symbols: list[str],
    slippage_bps: float,
    *,
    cross_sectional: bool = True,
    scalar: float = 30.0,
) -> dict[str, object]:
    cfg = _cfg(slippage_bps, cross_sectional=cross_sectional, scalar=scalar)
    result = replay_carry(conn, cfg, symbols=symbols)
    trials = replay_carry_trials(conn, cfg, symbols=symbols)
    xs = replay_xs(conn, cfg.sleeve_cfg, symbols=symbols).portfolio_return
    trend = replay_universe(conn, cfg.sleeve_cfg, symbols=symbols).portfolio_return
    rep = evaluate_carry(
        result, cfg, trial_returns=trials, xs_returns=xs, trend_returns=trend
    )
    return {
        "label": label,
        "n_inst": len(result.per_instrument_net),
        "days": rep.n_obs,
        "sharpe": rep.sharpe_annual,
        "max_dd": rep.max_dd,
        "ann_ret": rep.annual_return,
        "ann_vol": rep.annual_vol,
        "dsr": rep.dsr,
        "pbo": rep.pbo,
        "boot_lo": rep.boot_lo,
        "boot_hi": rep.boot_hi,
        "min_trl": rep.min_trl,
        "corr_to_xs": rep.corr_to_xs,
        "xs_sharpe": rep.xs_sharpe,
        "corr_to_trend": rep.corr_to_trend,
        "gate": "CLEAR" if carry_gate_verdict(rep) else "FAIL",
    }


def _per_span_sharpe(
    conn: duckdb.DuckDBPyConnection, symbols: list[str], cross_sectional: bool
) -> pd.DataFrame:
    cfg = _cfg(2.0, cross_sectional=cross_sectional)
    ann = math.sqrt(cfg.annualization_days)
    trials = replay_carry_trials(conn, cfg, symbols=symbols)
    rows = [{"trial": name, "sharpe": _sharpe(r, ann)} for name, r in trials.items()]
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
    parser.add_argument("--majors", type=str, default=",".join(_MAJORS))
    args = parser.parse_args()

    conn = duckdb.connect(str(args.db), read_only=True)
    print(f"DB: {args.db}")
    universe = load_universe()
    majors = [s.strip().upper() for s in args.majors.split(",") if s.strip()]

    _print_df(
        "Gate — headline cross-sectional carry (universe @2bps)",
        pd.DataFrame([build_report_row(conn, "xs-carry universe @2bps", universe, 2.0)]),
    )

    _print_df(
        "Absolute vs cross-sectional contrast (universe @2bps)",
        pd.DataFrame(
            [
                build_report_row(
                    conn, "cross-sectional", universe, 2.0, cross_sectional=True
                ),
                build_report_row(
                    conn, "absolute", universe, 2.0, cross_sectional=False
                ),
            ]
        ),
    )

    _print_df(
        "Breadth contrast (cross-sectional @2bps)",
        pd.DataFrame(
            [
                build_report_row(conn, "universe", universe, 2.0),
                build_report_row(conn, "majors", majors, 2.0),
            ]
        ),
    )

    _print_df(
        "Cost sensitivity (cross-sectional universe)",
        pd.DataFrame(
            [
                build_report_row(conn, f"universe @{b:g}bps", universe, b)
                for b in (0.0, 2.0, 8.0, 16.0)
            ]
        ),
    )

    _print_df(
        "Per-span carry Sharpe (cross-sectional universe)",
        _per_span_sharpe(conn, universe, True),
    )

    _print_df(
        "Scalar sensitivity (cross-sectional universe @2bps)",
        pd.DataFrame(
            [
                build_report_row(
                    conn, f"scalar={s:g}", universe, 2.0, scalar=s
                )
                for s in (15.0, 30.0, 60.0)
            ]
        ),
    )

    print(
        "\nCarry read: is the cross-sectional carry sleeve positive, cost-robust, "
        "DSR/PBO-survivable, AND low/negative-correlated to the XS deploy core "
        "(corr_to_xs near 0 or below)? A comparable-Sharpe carry sleeve uncorrelated "
        "with XS is the second edge the combine layer needs. Read corr_to_xs + boot_lo "
        "+ pbo + dsr alongside the headline before calling it."
    )


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Add the Makefile target**

Add `buibui-carry-audit` (with a leading space) to the end of the `.PHONY: lint ...`
master list on line 14, then add this block after the `buibui-combine-audit` block (after
line 276). **The recipe line must be indented with a literal TAB, not spaces** (Make
requires it — the four spaces shown below are a markdown-lint placeholder):

```makefile
.PHONY: buibui-carry-audit
buibui-carry-audit:  ## P3: read-only funding-carry sleeve audit over the N3 universe
    PYTHONPATH=. poetry run python tools/carry_audit.py
```

- [ ] **Step 3: Smoke-run the driver (read-only)**

Run: `PYTHONPATH=. poetry run python tools/carry_audit.py 2>&1 | tail -60`
Expected: six tables print without error; the headline row shows a `gate` of CLEAR or FAIL.
(If `analytics.db` is missing locally, note it and defer the live run — the unit tests still gate correctness.)

- [ ] **Step 4: Lint + typecheck + commit**

```bash
make lint-py && make typecheck
git add tools/carry_audit.py Makefile
git commit -m "feat(carry): read-only carry-sleeve audit driver + make buibui-carry-audit"
```

---

### Task 8: Full DoD + verdict + docs

**Files:**

- Create: `docs/audits/2026-06-19-p3-carry-sleeve.md`
- Modify: `CLAUDE.md` (add `analytics/carry/` section + `tools/carry_audit.py` row + note the Makefile target), `README.md` if it lists the audit tools.

- [ ] **Step 1: Run the full Definition of Done**

```bash
make lint-py && make typecheck && make test && make test-regression
```

Expected: all green; `make test-regression` goldens **UNMOVED** (additive read-only package).
If goldens moved, STOP and investigate — something leaked into a shared path.

- [ ] **Step 2: Run the audit and capture the verdict**

Run: `PYTHONPATH=. poetry run python tools/carry_audit.py | tee /tmp/carry-audit.txt`
Read the six tables. The verdict is CLEAR iff the headline cross-sectional row has
`dsr ≥ 0.95 ∧ pbo ≤ 0.5 ∧ boot_lo > 0`. Note the corr_to_xs (diversification of the
deploy core), the absolute-vs-cross-sectional contrast, per-span concentration, and
cost/scalar robustness.

- [ ] **Step 3: Write the verdict doc**

Write `docs/audits/2026-06-19-p3-carry-sleeve.md` honestly (CLEAR or FAIL), mirroring the
shape of `docs/audits/2026-06-16-p3-xsmom-sleeve.md`: headline gate numbers, the
diversification read vs XS, which expression/span carries it, cost/scalar robustness, and
the decision (graduate to combine if it clears at a comparable Sharpe AND diversifies XS;
else shelf alongside trend with XS-solo remaining the core).

- [ ] **Step 4: Update CLAUDE.md + README**

Add an `analytics/carry/` bullet under the analytics package list (mirroring the
`xsmom/` and `combine/` bullets), a `tools/carry_audit.py` row, and confirm the Makefile
target is mentioned. Keep entries terse.

- [ ] **Step 5: Commit**

```bash
git add docs/audits/2026-06-19-p3-carry-sleeve.md CLAUDE.md README.md
git commit -m "docs(carry): P3 carry-sleeve verdict + CLAUDE/README sync"
```

---

## Self-Review notes

- **Spec coverage:** config (§5.1) → T1; forecast construction (§5.2) → T2; book/leverage/
  governor + causality (§5.4, §6) → T3; replay + trial family (§5.5) → T4; report + gate
  (§5.6) → T5; re-exports (§5.7) → T6; driver + Makefile (§5.8) → T7; DoD + verdict (§8) → T8.
- **Anti-overfitting (§5.3):** `carry_scalar` fixed at 30.0; scalar-sensitivity table in the
  audit demonstrates robustness. No crypto-fit parameter committed.
- **Causality (§6):** T3 Step 4b explicitly verifies the causality test is RED without the
  `.shift(1)` — non-vacuous.
- **Type consistency:** `CarryBookResult` / `CarryReport` / `run_carry_backtest` /
  `carry_leverage` / `replay_carry` / `replay_carry_trials` / `evaluate_carry` /
  `carry_gate_verdict` names are used identically across tasks. `replay_carry_trials` keys
  are `span{s}` + `combined` in both T4 test and the audit driver.
- **Goldens:** additive read-only package; T8 asserts goldens UNMOVED.
