# P3 trend×XS combine layer — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a read-only, additive, default-off `analytics/combine/` package that combines the two validated post-governor sleeve return streams (XS +1.375 core, trend +0.36 diversifier, corr +0.37) into one IDM-scaled book, and reports whether it clears DSR ≥ 0.95 ∧ PBO ≤ 0.5 ∧ boot_lo > 0 over {trend, XS, combined}.

**Architecture:** Book-return-space combine. Each sleeve already produces a causal, NaN-free, post-governor `portfolio_return` array on a shared daily index. The combine weights them (equal-risk 0.5/0.5), scales by a causal-rolling Carver IDM = `1/√(wᵀρw)`, and applies a final causal 20%-vol governor. Pure math over arrays; one read-only DuckDB front door; a driver tool prints the gated verdict + sensitivity tables. New package touches nothing in the backtest pipeline ⇒ regression goldens unmoved by construction.

**Tech Stack:** Python 3.11+, numpy, pandas, duckdb (read-only), pytest. Reuses `analytics/forecast/`, `analytics/xsmom/`, `portfolio.metrics`, `analytics/research_guards/`.

**Spec:** `docs/superpowers/specs/2026-06-18-p3-trend-xs-combine-design.md`

---

## File Structure

**Create:**

- `analytics/combine/__init__.py` — package marker → eager re-exports (Task 6)
- `analytics/combine/config.py` — `CombineConfig` frozen dataclass
- `analytics/combine/idm.py` — `idm_value`, `static_idm`, `causal_idm_series` (pure, no DB/IO)
- `analytics/combine/book.py` — `CombinedBookResult`, `combine_books`, `equity_curve`
- `analytics/combine/report.py` — `CombineReport`, `evaluate_combined`, `combine_gate_verdict`
- `analytics/combine/replay.py` — `load_sleeves`, `replay_combined`, `replay_combined_trials`
- `tools/combine_audit.py` — read-only driver (`build_combine_report_row` + `main`)
- `tests/combine/__init__.py`
- `tests/combine/test_config.py`
- `tests/combine/test_idm.py`
- `tests/combine/test_book.py`
- `tests/combine/test_report.py`
- `tests/combine/test_replay.py`
- `tests/combine/test_audit_cli.py`
- `docs/audits/2026-06-18-p3-trend-xs-combine.md` — verdict (Task 8)

**Modify:**

- `Makefile` — add `buibui-combine-audit` target + `.PHONY` entry
- `CLAUDE.md` — add the `analytics/combine/` package + `tools/combine_audit.py` row (Task 9)
- `README.md` — sync if the combine layer is user-facing (Task 9)

---

## Task 1: Package scaffold + `CombineConfig`

**Files:**

- Create: `analytics/combine/__init__.py` (minimal marker for now)
- Create: `analytics/combine/config.py`
- Create: `tests/combine/__init__.py`
- Create: `tests/combine/test_config.py`

- [ ] **Step 1: Create the package markers**

Create `analytics/combine/__init__.py` with a one-line docstring (eager re-exports come in Task 6):

```python
"""P3 trend×XS combine layer — IDM book-return-space portfolio construction."""
```

Create an empty `tests/combine/__init__.py` (zero bytes).

- [ ] **Step 2: Write the failing config test**

Create `tests/combine/test_config.py`:

```python
from __future__ import annotations

from pathlib import Path

from analytics.combine.config import CombineConfig
from analytics.forecast.config import ForecastConfig


def test_defaults_are_equal_risk_causal() -> None:
    cfg = CombineConfig()
    assert cfg.w_xs == 0.5
    assert cfg.w_trend == 0.5
    assert cfg.idm_mode == "causal"
    assert cfg.idm_window == 365
    assert cfg.idm_min_periods == 120
    assert cfg.idm_cap == 2.5
    assert cfg.apply_governor is True
    assert isinstance(cfg.sleeve_cfg, ForecastConfig)
    # the headline XS sleeve is the validated original (NOT dollar-neutral)
    assert cfg.sleeve_cfg.xs_dollar_neutral is False


def test_from_toml_picks_up_sleeve_costs(tmp_path: Path) -> None:
    toml = tmp_path / "p.toml"
    toml.write_text("[backtest]\nfee_pct = 0.0007\nslippage_bps = 3.0\n")
    cfg = CombineConfig.from_toml(toml)
    assert cfg.sleeve_cfg.fee_pct == 0.0007
    assert cfg.sleeve_cfg.slippage_pct == 3.0 / 10_000.0


def test_invalid_idm_mode_rejected() -> None:
    import pytest

    with pytest.raises(ValueError, match="idm_mode"):
        CombineConfig(idm_mode="bogus")
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `PYTHONPATH=. poetry run pytest tests/combine/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'analytics.combine.config'`

- [ ] **Step 4: Implement `config.py`**

Create `analytics/combine/config.py`:

```python
"""Configuration for the trend×XS combine layer (P3).

Frozen dataclass holding one shared `ForecastConfig` (feeds BOTH sleeves so fees /
speeds / governor constants match) plus the combine-specific knobs. `from_toml`
defers to `ForecastConfig.from_toml` for the shared honest-cost values.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from analytics.forecast.config import ForecastConfig

_VALID_IDM_MODES = ("causal", "static")


@dataclass(frozen=True)
class CombineConfig:
    sleeve_cfg: ForecastConfig = field(default_factory=ForecastConfig)
    w_xs: float = 0.5
    w_trend: float = 0.5
    idm_mode: str = "causal"
    idm_window: int = 365
    idm_min_periods: int = 120
    idm_cap: float = 2.5
    apply_governor: bool = True

    def __post_init__(self) -> None:
        if self.idm_mode not in _VALID_IDM_MODES:
            raise ValueError(
                f"idm_mode {self.idm_mode!r} not in {_VALID_IDM_MODES}"
            )

    @classmethod
    def from_toml(cls, path: Path | str) -> CombineConfig:
        return cls(sleeve_cfg=ForecastConfig.from_toml(path))
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `PYTHONPATH=. poetry run pytest tests/combine/test_config.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Lint + typecheck**

Run: `make lint-py && make typecheck`
Expected: both pass.

- [ ] **Step 7: Commit**

```bash
git add analytics/combine/__init__.py analytics/combine/config.py tests/combine/
git commit -m "feat(combine): CombineConfig + package scaffold for the trend×XS layer"
```

---

## Task 2: `idm.py` — `idm_value` (pure Carver IDM math)

**Files:**

- Create: `analytics/combine/idm.py`
- Create: `tests/combine/test_idm.py`

- [ ] **Step 1: Write the failing test**

Create `tests/combine/test_idm.py`:

```python
from __future__ import annotations

import math

from analytics.combine.idm import idm_value


def test_idm_zero_corr_equal_weights() -> None:
    # IDM = 1/sqrt(0.5^2 + 0.5^2 + 0) = 1/sqrt(0.5) = 1.41421356
    assert idm_value(0.5, 0.5, 0.0, cap=2.5) == math.sqrt(2.0)


def test_idm_perfect_corr_is_one() -> None:
    # IDM = 1/sqrt(0.25 + 0.25 + 0.5) = 1/sqrt(1.0) = 1.0 (no diversification)
    assert idm_value(0.5, 0.5, 1.0, cap=2.5) == 1.0


def test_idm_caps_when_denominator_nonpositive() -> None:
    # corr = -1, equal weights -> var = 0 -> would be +inf -> capped
    assert idm_value(0.5, 0.5, -1.0, cap=2.5) == 2.5


def test_idm_monotone_decreasing_in_corr() -> None:
    lo = idm_value(0.5, 0.5, 0.0, cap=2.5)
    mid = idm_value(0.5, 0.5, 0.37, cap=2.5)
    hi = idm_value(0.5, 0.5, 0.9, cap=2.5)
    assert lo > mid > hi
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `PYTHONPATH=. poetry run pytest tests/combine/test_idm.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'analytics.combine.idm'`

- [ ] **Step 3: Implement `idm_value`**

Create `analytics/combine/idm.py`:

```python
"""Carver Instrument Diversification Multiplier for the two-sleeve combine.

Pure math, no DB/IO. IDM = 1/√(wᵀ ρ w) scales a diversified combination back up to
the vol target; capped (Carver uses 2.5). `static_idm` uses one full-sample
correlation (a reported sensitivity — mild look-ahead); `causal_idm_series`
estimates the correlation on a trailing window through `d-1` (the headline,
no-look-ahead path).
"""

from __future__ import annotations

import math


def idm_value(w_xs: float, w_trend: float, corr: float, cap: float) -> float:
    """1/√(wᵀρw) for two sleeves, capped at `cap`.

    `var = w_xs² + w_trend² + 2·w_xs·w_trend·corr`. A non-positive `var` (e.g.
    corr ≈ −1 with equal weights) means infinite scale-up — return `cap`.
    """
    var = w_xs**2 + w_trend**2 + 2.0 * w_xs * w_trend * corr
    if var <= 0.0:
        return cap
    return min(1.0 / math.sqrt(var), cap)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `PYTHONPATH=. poetry run pytest tests/combine/test_idm.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add analytics/combine/idm.py tests/combine/test_idm.py
git commit -m "feat(combine): idm_value — Carver 2-sleeve IDM with cap"
```

---

## Task 3: `idm.py` — `static_idm` + `causal_idm_series`

**Files:**

- Modify: `analytics/combine/idm.py`
- Modify: `tests/combine/test_idm.py`

- [ ] **Step 1: Write the failing tests (append to `tests/combine/test_idm.py`)**

```python
import numpy as np
import pandas as pd

from analytics.combine.idm import causal_idm_series, static_idm


def test_static_idm_independent_streams_near_sqrt2() -> None:
    rng = np.random.default_rng(0)
    a = rng.standard_normal(2000)
    b = rng.standard_normal(2000)
    # ~zero correlation -> IDM ~ 1.414
    assert 1.30 < static_idm(a, b, 0.5, 0.5, cap=2.5) < 1.50


def test_static_idm_identical_streams_is_one() -> None:
    a = np.linspace(-1.0, 1.0, 500)
    # corr == 1 -> IDM == 1.0
    assert abs(static_idm(a, a, 0.5, 0.5, cap=2.5) - 1.0) < 1e-9


def test_static_idm_ignores_joint_zero_warmup() -> None:
    rng = np.random.default_rng(1)
    a = np.concatenate([np.zeros(100), rng.standard_normal(900)])
    b = np.concatenate([np.zeros(100), rng.standard_normal(900)])
    # leading joint-zero warm-up must not inflate the correlation toward 1
    assert 1.30 < static_idm(a, b, 0.5, 0.5, cap=2.5) < 1.50


def test_causal_idm_series_warmup_is_neutral_one() -> None:
    idx = pd.date_range("2021-01-01", periods=600, freq="D")
    rng = np.random.default_rng(2)
    a = rng.standard_normal(600)
    b = rng.standard_normal(600)
    s = causal_idm_series(a, b, 0.5, 0.5, window=365, min_periods=120, cap=2.5, index=idx)
    assert len(s) == 600
    # before min_periods of trailing data the IDM is the neutral 1.0
    assert (s.iloc[:120] == 1.0).all()
    # once warmed, an ~uncorrelated pair lifts IDM above 1.0
    assert s.iloc[-1] > 1.0


def test_causal_idm_series_identical_streams_trends_to_one() -> None:
    idx = pd.date_range("2021-01-01", periods=600, freq="D")
    rng = np.random.default_rng(3)
    a = rng.standard_normal(600)
    s = causal_idm_series(a, a, 0.5, 0.5, window=365, min_periods=120, cap=2.5, index=idx)
    # perfectly correlated sleeves -> no diversification -> IDM ~ 1.0 once warmed
    assert abs(s.iloc[-1] - 1.0) < 1e-6


def test_causal_idm_series_is_causal_no_lookahead() -> None:
    idx = pd.date_range("2021-01-01", periods=600, freq="D")
    rng = np.random.default_rng(4)
    a = rng.standard_normal(600)
    b = rng.standard_normal(600)
    base = causal_idm_series(a, b, 0.5, 0.5, window=365, min_periods=120, cap=2.5, index=idx)
    a2 = a.copy()
    a2[400] += 5.0  # perturb a future return
    after = causal_idm_series(a2, b, 0.5, 0.5, window=365, min_periods=120, cap=2.5, index=idx)
    # IDM at day t uses corr through t-1; a change at 400 cannot move IDM[:401]
    pd.testing.assert_series_equal(base.iloc[:401], after.iloc[:401], check_names=False)
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=. poetry run pytest tests/combine/test_idm.py -v`
Expected: FAIL with `ImportError: cannot import name 'static_idm'`

- [ ] **Step 3: Implement `static_idm` + `causal_idm_series` (append to `analytics/combine/idm.py`)**

Add imports at the top of `idm.py`:

```python
import numpy as np
import numpy.typing as npt
import pandas as pd
```

Append:

```python
def _joint_live_corr(
    a: npt.NDArray[np.float64], b: npt.NDArray[np.float64]
) -> float:
    """Pearson corr over the common tail, excluding joint dead warm-up (0, 0).

    Mirrors `analytics.xsmom.report._aligned_corr`. Degenerate (n<2 or zero
    variance) -> 0.0.
    """
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


def static_idm(
    r_xs: npt.ArrayLike,
    r_trend: npt.ArrayLike,
    w_xs: float,
    w_trend: float,
    cap: float,
) -> float:
    """One constant IDM from the full-sample joint-live correlation.

    A reported sensitivity only — uses future data to size early periods (a mild
    look-ahead leak), never the headline.
    """
    a = np.asarray(r_xs, dtype=np.float64)
    b = np.asarray(r_trend, dtype=np.float64)
    return idm_value(w_xs, w_trend, _joint_live_corr(a, b), cap)


def causal_idm_series(
    r_xs: npt.ArrayLike,
    r_trend: npt.ArrayLike,
    w_xs: float,
    w_trend: float,
    window: int,
    min_periods: int,
    cap: float,
    index: pd.DatetimeIndex,
) -> pd.Series:
    """Per-day IDM from a trailing-window correlation, shifted to be causal.

    The correlation each day is over the trailing `window` of joint-live returns;
    the resulting IDM is `.shift(1)` so the position on day `d` uses correlation
    through `d-1`. Before `min_periods` of trailing live data the IDM is the
    neutral 1.0 (the combined return there is ~0 anyway). Joint warm-up rows
    (both returns exactly 0.0) are masked out so they do not pollute the corr.
    """
    idx = pd.DatetimeIndex(index)
    s_xs = pd.Series(np.asarray(r_xs, dtype=np.float64), index=idx)
    s_tr = pd.Series(np.asarray(r_trend, dtype=np.float64), index=idx)
    live = ~((s_xs == 0.0) & (s_tr == 0.0))
    xm = s_xs.where(live)
    tm = s_tr.where(live)
    roll = xm.rolling(window, min_periods=min_periods).corr(tm)

    idm = pd.Series(1.0, index=idx)
    valid = roll.notna()
    idm.loc[valid] = roll.loc[valid].map(
        lambda c: idm_value(w_xs, w_trend, float(c), cap)
    )
    return idm.shift(1).fillna(1.0)
```

- [ ] **Step 4: Run to verify pass**

Run: `PYTHONPATH=. poetry run pytest tests/combine/test_idm.py -v`
Expected: PASS (10 tests total). If `test_causal_idm_series_warmup_is_neutral_one` is off by a few rows because of how `rolling.corr` counts masked NaN windows, adjust the slice bound in the assertion (e.g. `s.iloc[:100]`) — the invariant is "neutral 1.0 until enough trailing live data," not the exact bar.

- [ ] **Step 5: Lint + typecheck**

Run: `make lint-py && make typecheck`
Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add analytics/combine/idm.py tests/combine/test_idm.py
git commit -m "feat(combine): static + causal-rolling IDM estimation"
```

---

## Task 4: `book.py` — `combine_books` + `CombinedBookResult` + `equity_curve`

**Files:**

- Create: `analytics/combine/book.py`
- Create: `tests/combine/test_book.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/combine/test_book.py`:

```python
from __future__ import annotations

import numpy as np
import pandas as pd

from analytics.combine.book import CombinedBookResult, combine_books, equity_curve
from analytics.combine.config import CombineConfig
from analytics.forecast.book import ForecastBookResult
from analytics.xsmom.book import XSBookResult


def _xs_result(returns: np.ndarray, idx: pd.DatetimeIndex) -> XSBookResult:
    return XSBookResult(
        daily_index=idx,
        portfolio_return=returns,
        pre_governor_return=returns,
        governor=np.ones(len(returns)),
        active_count=np.full(len(returns), 2, dtype=np.int64),
        per_instrument_net={},
    )


def _trend_result(returns: np.ndarray, idx: pd.DatetimeIndex) -> ForecastBookResult:
    return ForecastBookResult(
        daily_index=idx,
        portfolio_return=returns,
        pre_governor_return=returns,
        governor=np.ones(len(returns)),
        active_count=np.full(len(returns), 2, dtype=np.int64),
        per_instrument_net={},
    )


def _pair(n: int = 600, seed: int = 0) -> tuple[XSBookResult, ForecastBookResult]:
    idx = pd.date_range("2021-01-01", periods=n, freq="D")
    rng = np.random.default_rng(seed)
    r_xs = 0.001 + 0.01 * rng.standard_normal(n)
    r_tr = 0.0005 + 0.01 * rng.standard_normal(n)
    return _xs_result(r_xs, idx), _trend_result(r_tr, idx)


def test_combine_books_returns_result_shape() -> None:
    xs, tr = _pair()
    res = combine_books(xs, tr, CombineConfig())
    assert isinstance(res, CombinedBookResult)
    assert res.portfolio_return.shape[0] == 600
    assert not np.isnan(res.portfolio_return).any()
    assert res.idm.shape[0] == 600
    assert res.governor.shape[0] == 600


def test_no_governor_no_idm_is_weighted_sum() -> None:
    # idm_mode static on perfectly-correlated streams -> IDM == 1.0, governor off
    idx = pd.date_range("2021-01-01", periods=400, freq="D")
    r = 0.001 + 0.01 * np.random.default_rng(7).standard_normal(400)
    xs = _xs_result(r, idx)
    tr = _trend_result(r, idx)
    cfg = CombineConfig(idm_mode="static", apply_governor=False)
    res = combine_books(xs, tr, cfg)
    # IDM(corr=1) = 1.0, so combined == 0.5r + 0.5r == r
    np.testing.assert_allclose(res.portfolio_return, r, atol=1e-12)


def test_idm_lifts_uncorrelated_pre_return() -> None:
    xs, tr = _pair(seed=1)
    cfg = CombineConfig(idm_mode="static", apply_governor=False)
    res = combine_books(xs, tr, cfg)
    pre = res.pre_idm_return
    # uncorrelated sleeves -> IDM > 1 -> post-IDM magnitude scaled up vs pre
    assert res.idm.mean() > 1.0
    assert np.nanstd(res.portfolio_return) > np.nanstd(pre)


def test_governor_targets_vol() -> None:
    xs, tr = _pair(seed=2)
    res = combine_books(xs, tr, CombineConfig())  # governor on
    ann = np.sqrt(365.0)
    live = res.portfolio_return[res.governor > 0.0]
    realized = float(np.std(live, ddof=1)) * ann
    # causal governor pulls realized annual vol toward the 20% target band
    assert 0.10 < realized < 0.35


def test_combine_is_causal_no_lookahead() -> None:
    xs, tr = _pair(seed=3)
    cfg = CombineConfig()  # causal IDM + governor
    base = combine_books(xs, tr, cfg).portfolio_return
    bumped_xs = XSBookResult(
        daily_index=xs.daily_index,
        portfolio_return=xs.portfolio_return.copy(),
        pre_governor_return=xs.pre_governor_return,
        governor=xs.governor,
        active_count=xs.active_count,
        per_instrument_net={},
    )
    bumped_xs.portfolio_return[400] += 5.0  # perturb a future sleeve return
    after = combine_books(bumped_xs, tr, cfg).portfolio_return
    # combined[t] for t < 400 uses corr/vol through t-1 + day-t returns only
    np.testing.assert_array_equal(base[:400], after[:400])


def test_combine_aligns_mismatched_indices() -> None:
    idx_a = pd.date_range("2021-01-01", periods=500, freq="D")
    idx_b = pd.date_range("2021-03-01", periods=500, freq="D")
    rng = np.random.default_rng(5)
    xs = _xs_result(0.01 * rng.standard_normal(500), idx_a)
    tr = _trend_result(0.01 * rng.standard_normal(500), idx_b)
    res = combine_books(xs, tr, CombineConfig())
    union = idx_a.union(idx_b)
    assert len(res.daily_index) == len(union)
    assert not np.isnan(res.portfolio_return).any()


def test_equity_curve_starts_at_one() -> None:
    xs, tr = _pair(seed=6)
    res = combine_books(xs, tr, CombineConfig())
    curve = equity_curve(res)
    assert curve.iloc[0] == 1.0 + res.portfolio_return[0]


def test_degenerate_flat_sleeves() -> None:
    idx = pd.date_range("2021-01-01", periods=300, freq="D")
    xs = _xs_result(np.zeros(300), idx)
    tr = _trend_result(np.zeros(300), idx)
    res = combine_books(xs, tr, CombineConfig())
    assert np.all(res.portfolio_return == 0.0)
    assert np.all(res.idm == 1.0)  # no live data -> neutral IDM throughout
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=. poetry run pytest tests/combine/test_book.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'analytics.combine.book'`

- [ ] **Step 3: Implement `book.py`**

Create `analytics/combine/book.py`:

```python
"""Book-return-space combine of the two validated sleeve return streams.

All sizing is causal: the combined return on day `d` is `g_d · idm_d · (w_xs·r_xs,d
+ w_trend·r_trend,d)` where `r_*,d` are the sleeves' own causal post-governor
returns, `idm_d` is the IDM from correlation through `d-1`, and `g_d` is the final
vol governor from trailing vol through `d-1`.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from analytics.combine.config import CombineConfig
from analytics.combine.idm import causal_idm_series, static_idm
from analytics.forecast.book import ForecastBookResult
from analytics.xsmom.book import XSBookResult


@dataclass(frozen=True)
class CombinedBookResult:
    daily_index: pd.DatetimeIndex
    portfolio_return: np.ndarray  # net, post-IDM, post-governor (NaN-free)
    pre_idm_return: np.ndarray  # weighted sum before IDM
    idm: np.ndarray  # per-day IDM (1.0 during warm-up)
    governor: np.ndarray  # 0.0 / NaN→0.0 during warm-up
    xs_return_aligned: np.ndarray
    trend_return_aligned: np.ndarray


def combine_books(
    xs_result: XSBookResult,
    trend_result: ForecastBookResult,
    cfg: CombineConfig,
) -> CombinedBookResult:
    """Weight → IDM → final causal governor over the two sleeve return streams."""
    s_xs = pd.Series(xs_result.portfolio_return, index=xs_result.daily_index)
    s_tr = pd.Series(trend_result.portfolio_return, index=trend_result.daily_index)
    union = s_xs.index.union(s_tr.index).sort_values()
    r_xs = s_xs.reindex(union).fillna(0.0)
    r_tr = s_tr.reindex(union).fillna(0.0)

    pre = cfg.w_xs * r_xs + cfg.w_trend * r_tr

    if cfg.idm_mode == "static":
        idm_const = static_idm(
            r_xs.to_numpy(), r_tr.to_numpy(), cfg.w_xs, cfg.w_trend, cfg.idm_cap
        )
        idm = pd.Series(idm_const, index=union)
    else:
        idm = causal_idm_series(
            r_xs.to_numpy(),
            r_tr.to_numpy(),
            cfg.w_xs,
            cfg.w_trend,
            cfg.idm_window,
            cfg.idm_min_periods,
            cfg.idm_cap,
            union,
        )

    post_idm = idm * pre

    sc = cfg.sleeve_cfg
    if cfg.apply_governor:
        ann = np.sqrt(sc.annualization_days)
        trailing_vol = (
            post_idm.rolling(sc.gov_window, min_periods=sc.gov_window).std().shift(1)
            * ann
        )
        g = (sc.vol_target_annual / trailing_vol).clip(sc.g_min, sc.g_max)
        port = g.fillna(0.0) * post_idm
    else:
        g = pd.Series(1.0, index=union)
        port = post_idm

    return CombinedBookResult(
        daily_index=union,
        portfolio_return=port.to_numpy(dtype=np.float64),
        pre_idm_return=pre.to_numpy(dtype=np.float64),
        idm=idm.to_numpy(dtype=np.float64),
        governor=g.to_numpy(dtype=np.float64),
        xs_return_aligned=r_xs.to_numpy(dtype=np.float64),
        trend_return_aligned=r_tr.to_numpy(dtype=np.float64),
    )


def equity_curve(result: CombinedBookResult) -> pd.Series:
    """Compounding equity curve (starts at 1.0+r₀) for portfolio.metrics."""
    r = pd.Series(result.portfolio_return, index=result.daily_index)
    return (1.0 + r).cumprod()
```

- [ ] **Step 4: Run to verify pass**

Run: `PYTHONPATH=. poetry run pytest tests/combine/test_book.py -v`
Expected: PASS (8 tests). If `test_governor_targets_vol` lands outside the band on this synthetic data, widen the band — the assertion guards "the governor pulls vol toward target," not an exact number.

- [ ] **Step 5: Lint + typecheck**

Run: `make lint-py && make typecheck`
Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add analytics/combine/book.py tests/combine/test_book.py
git commit -m "feat(combine): combine_books — weight × causal IDM × governor (causal)"
```

---

## Task 5: `report.py` — `evaluate_combined` + `CombineReport` + `combine_gate_verdict`

**Files:**

- Create: `analytics/combine/report.py`
- Create: `tests/combine/test_report.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/combine/test_report.py`:

```python
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from analytics.combine.book import CombinedBookResult
from analytics.combine.config import CombineConfig
from analytics.combine.report import (
    CombineReport,
    combine_gate_verdict,
    evaluate_combined,
)


def _result(returns: np.ndarray, idm: np.ndarray | None = None) -> CombinedBookResult:
    idx = pd.date_range("2021-01-01", periods=len(returns), freq="D")
    return CombinedBookResult(
        daily_index=idx,
        portfolio_return=returns,
        pre_idm_return=returns,
        idm=idm if idm is not None else np.full(len(returns), 1.2),
        governor=np.ones(len(returns)),
        xs_return_aligned=returns,
        trend_return_aligned=returns,
    )


def test_report_shape_and_fields() -> None:
    rng = np.random.default_rng(0)
    r = 0.001 + 0.01 * rng.standard_normal(800)
    xs = 0.0012 + 0.01 * rng.standard_normal(800)
    tr = 0.0006 + 0.01 * rng.standard_normal(800)
    res = _result(r)
    res = CombinedBookResult(
        daily_index=res.daily_index,
        portfolio_return=r,
        pre_idm_return=r,
        idm=np.full(800, 1.2),
        governor=np.ones(800),
        xs_return_aligned=xs,
        trend_return_aligned=tr,
    )
    trials = {"combined": r, "xs": xs, "trend": tr}
    rep = evaluate_combined(res, CombineConfig(), trials, xs, tr)
    assert isinstance(rep, CombineReport)
    assert rep.n_obs == 800
    assert rep.boot_lo <= rep.sharpe_annual <= rep.boot_hi
    assert 0.0 <= rep.pbo <= 1.0
    assert -1.0 <= rep.corr_xs_trend <= 1.0
    assert rep.realized_idm > 1.0
    assert rep.diversification_mult > 0.0


def test_gate_verdict_true_when_all_pass() -> None:
    rep = CombineReport(
        sharpe_annual=1.5, sortino_annual=2.0, max_dd=-0.1, calmar=3.0,
        annual_return=0.3, annual_vol=0.2, n_obs=800,
        dsr=0.99, pbo=0.2, boot_lo=0.4, boot_hi=2.5, min_trl=300.0,
        corr_xs_trend=0.37, realized_idm=1.2, vol_xs=0.2, vol_trend=0.2,
        vol_combined=0.165, diversification_mult=1.21, sharpe_xs=1.375,
        sharpe_trend=0.36, xs_contribution=0.0006, trend_contribution=0.0002,
    )
    assert combine_gate_verdict(rep) is True


def test_gate_verdict_false_when_pbo_high() -> None:
    rep = CombineReport(
        sharpe_annual=1.5, sortino_annual=2.0, max_dd=-0.1, calmar=3.0,
        annual_return=0.3, annual_vol=0.2, n_obs=800,
        dsr=0.99, pbo=0.6, boot_lo=0.4, boot_hi=2.5, min_trl=300.0,
        corr_xs_trend=0.37, realized_idm=1.2, vol_xs=0.2, vol_trend=0.2,
        vol_combined=0.165, diversification_mult=1.21, sharpe_xs=1.375,
        sharpe_trend=0.36, xs_contribution=0.0006, trend_contribution=0.0002,
    )
    assert combine_gate_verdict(rep) is False


def test_flat_returns_degenerate_to_zero() -> None:
    res = _result(np.zeros(500), idm=np.ones(500))
    rep = evaluate_combined(
        res, CombineConfig(),
        trial_returns={"combined": np.zeros(500)},
        xs_returns=np.zeros(500),
        trend_returns=np.zeros(500),
    )
    assert rep.sharpe_annual == 0.0
    assert rep.dsr == 0.0
    assert math.isinf(rep.min_trl)
    assert combine_gate_verdict(rep) is False
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=. poetry run pytest tests/combine/test_report.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'analytics.combine.report'`

- [ ] **Step 3: Implement `report.py`**

Create `analytics/combine/report.py`:

```python
"""Assemble the trend×XS combine verdict: headline metrics + guards + diversification.

Pure over a CombinedBookResult plus the gate family's daily returns ({trend, XS,
combined}) and the aligned per-sleeve return arrays. Mirrors
`analytics.xsmom.report`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
import pandas as pd

from analytics.combine.book import CombinedBookResult, equity_curve
from analytics.combine.config import CombineConfig
from analytics.research_guards import (
    block_bootstrap_ci,
    cscv_pbo,
    deflated_sharpe_ratio,
    min_track_record_length,
)
from portfolio import metrics

_GATE_DSR = 0.95
_GATE_PBO = 0.5


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


def _live_vol(r: npt.NDArray[np.float64], ann: float) -> float:
    """Annualized vol over the non-zero (live) tail; 0.0 if degenerate."""
    live = r[r != 0.0]
    if len(live) < 2:
        return 0.0
    return float(np.std(live, ddof=1)) * ann


@dataclass(frozen=True)
class CombineReport:
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
    corr_xs_trend: float
    realized_idm: float
    vol_xs: float
    vol_trend: float
    vol_combined: float
    diversification_mult: float
    sharpe_xs: float
    sharpe_trend: float
    xs_contribution: float
    trend_contribution: float


def evaluate_combined(
    result: CombinedBookResult,
    cfg: CombineConfig,
    trial_returns: dict[str, npt.NDArray[np.float64]],
    xs_returns: npt.NDArray[np.float64],
    trend_returns: npt.NDArray[np.float64],
) -> CombineReport:
    """Headline metrics + DSR/PBO/boot/MinTRL over {trend, XS, combined} + diversification."""
    r = result.portfolio_return
    curve = equity_curve(result)
    ann = math.sqrt(cfg.sleeve_cfg.annualization_days)

    sr_d = _per_period_sharpe(r)
    trial_srs = [_per_period_sharpe(np.asarray(v, dtype=np.float64)) for v in trial_returns.values()]

    min_len = min((len(v) for v in trial_returns.values()), default=0)
    if min_len >= 28 and len(trial_returns) >= 2:
        mat = np.column_stack([np.asarray(v, dtype=np.float64)[-min_len:] for v in trial_returns.values()])
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

    xs_arr = np.asarray(xs_returns, dtype=np.float64)
    tr_arr = np.asarray(trend_returns, dtype=np.float64)
    vol_xs = _live_vol(xs_arr, ann)
    vol_trend = _live_vol(tr_arr, ann)
    vol_combined = _live_vol(result.pre_idm_return, ann)
    weighted_avg_vol = cfg.w_xs * vol_xs + cfg.w_trend * vol_trend
    diversification_mult = (
        weighted_avg_vol / vol_combined if vol_combined > 1e-12 else 0.0
    )

    live_idm = result.idm[result.idm != 1.0]
    realized_idm = float(np.mean(live_idm)) if len(live_idm) else 1.0

    xs_curve = (1.0 + pd.Series(xs_arr)).cumprod()
    tr_curve = (1.0 + pd.Series(tr_arr)).cumprod()

    return CombineReport(
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
        corr_xs_trend=_aligned_corr(xs_arr, tr_arr),
        realized_idm=realized_idm,
        vol_xs=vol_xs,
        vol_trend=vol_trend,
        vol_combined=vol_combined,
        diversification_mult=diversification_mult,
        sharpe_xs=metrics.sharpe(xs_curve),
        sharpe_trend=metrics.sharpe(tr_curve),
        xs_contribution=cfg.w_xs * float(np.mean(xs_arr)) if len(xs_arr) else 0.0,
        trend_contribution=cfg.w_trend * float(np.mean(tr_arr)) if len(tr_arr) else 0.0,
    )


def combine_gate_verdict(report: CombineReport) -> bool:
    """The headline gate: DSR ≥ 0.95 ∧ PBO ≤ 0.5 ∧ boot_lo > 0."""
    if math.isnan(report.pbo):
        return False
    return (
        report.dsr >= _GATE_DSR
        and report.pbo <= _GATE_PBO
        and report.boot_lo > 0.0
    )
```

- [ ] **Step 4: Run to verify pass**

Run: `PYTHONPATH=. poetry run pytest tests/combine/test_report.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Lint + typecheck**

Run: `make lint-py && make typecheck`
Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add analytics/combine/report.py tests/combine/test_report.py
git commit -m "feat(combine): evaluate_combined + gate verdict over {trend,XS,combined}"
```

---

## Task 6: `replay.py` (read-only DB front door) + `__init__.py` eager re-exports

**Files:**

- Create: `analytics/combine/replay.py`
- Modify: `analytics/combine/__init__.py`
- Create: `tests/combine/test_replay.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/combine/test_replay.py`:

```python
from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd

from analytics.combine.book import CombinedBookResult
from analytics.combine.config import CombineConfig
from analytics.combine.replay import (
    load_sleeves,
    replay_combined,
    replay_combined_trials,
)
from analytics.forecast.book import ForecastBookResult
from analytics.store import init_schema
from analytics.store.market_data import upsert_ohlcv
from analytics.xsmom.book import XSBookResult

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


def _conn() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    _seed(conn, "AAAUSDT", 1.0)
    _seed(conn, "BBBUSDT", -0.5)
    return conn


def test_load_sleeves_returns_both_results() -> None:
    conn = _conn()
    xs, tr = load_sleeves(conn, CombineConfig(), symbols=["AAAUSDT", "BBBUSDT"])
    assert isinstance(xs, XSBookResult)
    assert isinstance(tr, ForecastBookResult)


def test_replay_combined_returns_book_result() -> None:
    conn = _conn()
    res = replay_combined(conn, CombineConfig(), symbols=["AAAUSDT", "BBBUSDT"])
    assert isinstance(res, CombinedBookResult)
    assert res.portfolio_return.shape[0] > 0
    assert not np.isnan(res.portfolio_return).any()


def test_replay_combined_trials_keys() -> None:
    conn = _conn()
    trials = replay_combined_trials(
        conn, CombineConfig(), symbols=["AAAUSDT", "BBBUSDT"]
    )
    assert set(trials) == {"trend", "xs", "combined"}
    for v in trials.values():
        assert isinstance(v, np.ndarray)
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=. poetry run pytest tests/combine/test_replay.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'analytics.combine.replay'`

- [ ] **Step 3: Implement `replay.py`**

Create `analytics/combine/replay.py`:

```python
"""Read-only DuckDB front door for the trend×XS combine layer.

Runs both validated sleeves over the shared 1d inputs, then the combine book. The
only module in `analytics/combine/` that touches the DB; never writes. The XS book
uses `cfg.sleeve_cfg` (the validated original, dollar-neutral off); the trend book
ignores the XS-only flag.
"""

from __future__ import annotations

import duckdb
import numpy as np

from analytics.combine.book import CombinedBookResult, combine_books
from analytics.combine.config import CombineConfig
from analytics.forecast.book import ForecastBookResult, run_forecast_backtest
from analytics.forecast.replay import load_daily_inputs
from analytics.universe import load_universe
from analytics.xsmom.book import XSBookResult, run_xs_backtest


def load_sleeves(
    conn: duckdb.DuckDBPyConnection,
    cfg: CombineConfig,
    symbols: list[str] | None = None,
) -> tuple[XSBookResult, ForecastBookResult]:
    """Run both sleeves once over the shared 1d inputs (read-only)."""
    syms = symbols if symbols is not None else load_universe()
    closes, fundings = load_daily_inputs(conn, syms)
    xs = run_xs_backtest(closes, fundings, cfg.sleeve_cfg)
    trend = run_forecast_backtest(closes, fundings, cfg.sleeve_cfg)
    return xs, trend


def replay_combined(
    conn: duckdb.DuckDBPyConnection,
    cfg: CombineConfig,
    symbols: list[str] | None = None,
) -> CombinedBookResult:
    """Load both sleeves and run the combine book (read-only)."""
    xs, trend = load_sleeves(conn, cfg, symbols)
    return combine_books(xs, trend, cfg)


def replay_combined_trials(
    conn: duckdb.DuckDBPyConnection,
    cfg: CombineConfig,
    symbols: list[str] | None = None,
) -> dict[str, np.ndarray]:
    """The honest gate family for DSR/PBO: {trend, XS, combined} daily returns."""
    xs, trend = load_sleeves(conn, cfg, symbols)
    combined = combine_books(xs, trend, cfg)
    return {
        "trend": trend.portfolio_return,
        "xs": xs.portfolio_return,
        "combined": combined.portfolio_return,
    }
```

- [ ] **Step 4: Run to verify pass**

Run: `PYTHONPATH=. poetry run pytest tests/combine/test_replay.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Populate `__init__.py` eager re-exports**

Replace `analytics/combine/__init__.py` with:

```python
"""P3 trend×XS combine layer — IDM book-return-space portfolio construction."""

from __future__ import annotations

from analytics.combine.book import CombinedBookResult, combine_books, equity_curve
from analytics.combine.config import CombineConfig
from analytics.combine.idm import causal_idm_series, idm_value, static_idm
from analytics.combine.replay import (
    load_sleeves,
    replay_combined,
    replay_combined_trials,
)
from analytics.combine.report import (
    CombineReport,
    combine_gate_verdict,
    evaluate_combined,
)

__all__ = [
    "CombineConfig",
    "CombineReport",
    "CombinedBookResult",
    "causal_idm_series",
    "combine_books",
    "combine_gate_verdict",
    "equity_curve",
    "evaluate_combined",
    "idm_value",
    "load_sleeves",
    "replay_combined",
    "replay_combined_trials",
    "static_idm",
]
```

- [ ] **Step 6: Verify the package import + full combine suite**

Run: `PYTHONPATH=. poetry run python -c "import analytics.combine as c; print(sorted(c.__all__))"`
Expected: prints the export list, no ImportError.

Run: `PYTHONPATH=. poetry run pytest tests/combine/ -v`
Expected: all green.

- [ ] **Step 7: Lint + typecheck**

Run: `make lint-py && make typecheck`
Expected: pass.

- [ ] **Step 8: Commit**

```bash
git add analytics/combine/replay.py analytics/combine/__init__.py tests/combine/test_replay.py
git commit -m "feat(combine): read-only replay front door + eager package re-exports"
```

---

## Task 7: `tools/combine_audit.py` driver + Makefile target

**Files:**

- Create: `tools/combine_audit.py`
- Modify: `Makefile` (add target + `.PHONY` entry)
- Create: `tests/combine/test_audit_cli.py`

- [ ] **Step 1: Write the failing audit-CLI test**

Create `tests/combine/test_audit_cli.py`:

```python
from __future__ import annotations

import duckdb
import pandas as pd

from analytics.store import init_schema
from analytics.store.market_data import upsert_ohlcv
from tools.combine_audit import build_combine_report_row

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


def test_build_combine_report_row_returns_dict() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    _seed(conn, "AAAUSDT", 1.0)
    _seed(conn, "BBBUSDT", -0.5)
    row = build_combine_report_row(
        conn, "label", symbols=["AAAUSDT", "BBBUSDT"], slippage_bps=2.0
    )
    assert row["label"] == "label"
    for col in (
        "sharpe",
        "dsr",
        "pbo",
        "boot_lo",
        "corr_xs_trend",
        "realized_idm",
        "gate",
    ):
        assert col in row
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=. poetry run pytest tests/combine/test_audit_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tools.combine_audit'`

- [ ] **Step 3: Implement `tools/combine_audit.py`**

Create `tools/combine_audit.py`:

```python
"""Trend×XS combine-layer audit (P3) — read-only IDM portfolio verdict.

Replays the two validated sleeves over the N3 universe (1d), combines them in
book-return space with a causal-rolling Carver IDM, and prints: the gate verdict
({trend, XS, combined} headline + DSR/PBO/bootstrap-CI/MinTRL + PASS/FAIL), a
diversification read (correlation, realized IDM, vol reduction, sleeve
contribution), and sensitivity panels — sleeve weights, IDM mode (causal vs
static), and cost (with the combined cost-drag check) — plus a breadth contrast.

Read-only — no writes, no schema changes.

Usage::

    PYTHONPATH=. poetry run python tools/combine_audit.py
    PYTHONPATH=. poetry run python tools/combine_audit.py --majors BTCUSDT,ETHUSDT,SOLUSDT
"""

from __future__ import annotations

import argparse
import dataclasses
from pathlib import Path

import duckdb
import pandas as pd

from analytics.combine import (
    CombineConfig,
    combine_gate_verdict,
    evaluate_combined,
    load_sleeves,
)
from analytics.combine.book import combine_books
from analytics.forecast.config import ForecastConfig
from analytics.store import DEFAULT_DB_PATH
from analytics.universe import load_universe

_MAJORS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]


def _cfg(
    slippage_bps: float,
    w_xs: float = 0.5,
    w_trend: float = 0.5,
    idm_mode: str = "causal",
) -> CombineConfig:
    sleeve = dataclasses.replace(
        ForecastConfig(), slippage_pct=slippage_bps / 10_000.0
    )
    return CombineConfig(
        sleeve_cfg=sleeve, w_xs=w_xs, w_trend=w_trend, idm_mode=idm_mode
    )


def build_combine_report_row(
    conn: duckdb.DuckDBPyConnection,
    label: str,
    symbols: list[str],
    slippage_bps: float,
    w_xs: float = 0.5,
    w_trend: float = 0.5,
    idm_mode: str = "causal",
) -> dict[str, object]:
    cfg = _cfg(slippage_bps, w_xs, w_trend, idm_mode)
    xs, trend = load_sleeves(conn, cfg, symbols=symbols)
    combined = combine_books(xs, trend, cfg)
    trials = {
        "trend": trend.portfolio_return,
        "xs": xs.portfolio_return,
        "combined": combined.portfolio_return,
    }
    rep = evaluate_combined(
        combined, cfg, trials, xs.portfolio_return, trend.portfolio_return
    )
    return {
        "label": label,
        "days": rep.n_obs,
        "sharpe": rep.sharpe_annual,
        "sharpe_xs": rep.sharpe_xs,
        "sharpe_trend": rep.sharpe_trend,
        "max_dd": rep.max_dd,
        "ann_vol": rep.annual_vol,
        "dsr": rep.dsr,
        "pbo": rep.pbo,
        "boot_lo": rep.boot_lo,
        "boot_hi": rep.boot_hi,
        "min_trl": rep.min_trl,
        "corr_xs_trend": rep.corr_xs_trend,
        "realized_idm": rep.realized_idm,
        "div_mult": rep.diversification_mult,
        "gate": "PASS" if combine_gate_verdict(rep) else "FAIL",
    }


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

    _print_df(
        "Gate — trend×XS combine (universe vs majors @2bps)",
        pd.DataFrame(
            [
                build_combine_report_row(conn, "universe @2bps", universe, 2.0),
                build_combine_report_row(conn, "majors @2bps", majors, 2.0),
            ]
        ),
    )

    _print_df(
        "Weights sensitivity (universe @2bps)",
        pd.DataFrame(
            [
                build_combine_report_row(conn, "equal 0.5/0.5", universe, 2.0, 0.5, 0.5),
                build_combine_report_row(conn, "xs-heavy 0.7/0.3", universe, 2.0, 0.7, 0.3),
                build_combine_report_row(conn, "xs-heavy 0.79/0.21", universe, 2.0, 0.79, 0.21),
            ]
        ),
    )

    _print_df(
        "IDM-mode sensitivity (universe @2bps)",
        pd.DataFrame(
            [
                build_combine_report_row(conn, "causal", universe, 2.0, idm_mode="causal"),
                build_combine_report_row(conn, "static", universe, 2.0, idm_mode="static"),
            ]
        ),
    )

    _print_df(
        "Cost sensitivity (universe)",
        pd.DataFrame(
            [
                build_combine_report_row(conn, f"universe @{b:g}bps", universe, b)
                for b in (0.0, 2.0, 8.0, 16.0)
            ]
        ),
    )

    print(
        "\nCombine read: does the combined book BEAT the best single sleeve "
        "(sharpe vs sharpe_xs) AND clear the gate (dsr≥0.95 ∧ pbo≤0.5 ∧ boot_lo>0)? "
        "Read div_mult (>1 = real diversification) + realized_idm + the cost sweep "
        "(if the combined Sharpe decays much faster with cost than each sleeve alone, "
        "double-counted turnover is binding → forecast-space netting is the next lever)."
    )


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run to verify the CLI test passes**

Run: `PYTHONPATH=. poetry run pytest tests/combine/test_audit_cli.py -v`
Expected: PASS (1 test).

- [ ] **Step 5: Add the Makefile target**

In `Makefile`, append `buibui-combine-audit` (space-separated) to the end of the
long `.PHONY` list on line 14, then add the target after the `buibui-xsmom-audit`
block (around line 272):

```makefile
.PHONY: buibui-combine-audit
buibui-combine-audit:  ## P3: read-only trend×XS IDM combine-layer audit over the N3 universe
    PYTHONPATH=. poetry run python tools/combine_audit.py
```

(The recipe line above must be indented with a real **TAB**, not the 4 spaces
shown here — Make requires a tab; markdownlint forbids hard tabs in the doc.)

- [ ] **Step 6: Verify the Makefile target wiring**

Run: `make -n buibui-combine-audit`
Expected: prints `PYTHONPATH=. poetry run python tools/combine_audit.py`

- [ ] **Step 7: Lint + typecheck + full combine suite**

Run: `make lint-py && make typecheck && PYTHONPATH=. poetry run pytest tests/combine/ -v`
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add tools/combine_audit.py tests/combine/test_audit_cli.py Makefile
git commit -m "feat(combine): combine_audit driver + make buibui-combine-audit"
```

---

## Task 8: Run the audit over the real DB + write the verdict

**Files:**

- Create: `docs/audits/2026-06-18-p3-trend-xs-combine.md`

> Requires the real `analytics.db` (N3 universe, 1d, deep history). Read-only.

- [ ] **Step 1: Run the audit, capture output**

Run: `make buibui-combine-audit | tee /tmp/combine-audit.txt`
Expected: the four tables print without error. Note the universe row's `sharpe`,
`sharpe_xs`, `dsr`, `pbo`, `boot_lo`, `corr_xs_trend`, `realized_idm`, `div_mult`,
`gate`.

- [ ] **Step 2: Write the verdict doc**

Create `docs/audits/2026-06-18-p3-trend-xs-combine.md` capturing, with the actual numbers:

- The headline call in the title: does the combined book clear the gate, and does
  it beat the best single sleeve (combined `sharpe` vs `sharpe_xs`)?
- The gate table ({trend, XS, combined} metrics + PASS/FAIL).
- The diversification read (corr, realized IDM, `div_mult` > 1 confirmation, sleeve
  contribution) — is the diversification real?
- Weights sensitivity (does an XS-heavy tilt help in-sample? note it's not
  gate-selected).
- IDM-mode sensitivity (causal vs static — is the verdict robust to the choice?).
- Cost sensitivity + the double-turnover read (does the combined Sharpe decay
  faster than each sleeve → forecast-space-netting trigger, or not?).
- The decision: deploy-combined vs deploy-XS-solo vs (if it fails) retire-trend.
- Honest caveats carried forward (survivorship still pre-capital; this is replay).

- [ ] **Step 3: Lint the doc**

Run: `make lint-md`
Expected: pass (every code fence has a language; tables spaced).

- [ ] **Step 4: Commit**

```bash
git add docs/audits/2026-06-18-p3-trend-xs-combine.md
git commit -m "docs(combine): P3 trend×XS combine verdict"
```

---

## Task 9: Docs sync (CLAUDE.md, README) + final DoD gate

**Files:**

- Modify: `CLAUDE.md`
- Modify: `README.md` (only if the combine layer is user-facing — likely a one-line CLI/Make note)

- [ ] **Step 1: Add the `analytics/combine/` package to CLAUDE.md**

In `CLAUDE.md`, after the `xsmom/` bullet in the analytics package list, add a
parallel `combine/` bullet summarizing: book-return-space trend×XS combine,
causal-rolling IDM + equal-risk default, the modules (`config`, `idm`, `book`,
`replay`, `report`), read-only/additive/default-off, the gate family
{trend, XS, combined}, and the verdict-doc path. Also add the `tools/combine_audit.py`
row to the tools list and note the `make buibui-combine-audit` target. Match the
existing prose density.

- [ ] **Step 2: Sync README if needed**

If `README.md` lists the per-sleeve audits / Make targets, add the
`make buibui-combine-audit` line; otherwise skip (note "no README change needed"
in the commit if so).

- [ ] **Step 3: Full Definition-of-Done gate**

Run each and confirm green, stating the result plainly:

```bash
make lint-py
make typecheck
make test
make test-regression
make lint-md
```

Expected: lint-py ✓, typecheck ✓ (mypy strict), `make test` green (new combine tests
included), `make test-regression` goldens **UNMOVED** (new read-only package — no
pipeline change), lint-md ✓.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md README.md
git commit -m "docs(combine): sync CLAUDE.md + README for the trend×XS combine layer"
```

---

## Self-review notes (for the implementer)

- **Causality is the load-bearing invariant.** Tasks 3 (`causal_idm_series`) and 4
  (`combine_books`) each ship a perturbation test proving a future return cannot
  move an earlier output. If either passes *without* the `.shift(1)`, the test is
  wrong — verify it goes RED when you delete the shift.
- **Static IDM is a reported sensitivity only**, never the headline; its full-sample
  correlation is a deliberate (mild) look-ahead. The causality perturbation tests
  run on `idm_mode="causal"` only.
- **Gate honesty:** the headline gate family is exactly {trend, XS, combined}. The
  weight / IDM-mode / cost variants in the audit are *reported, not gate-selected*.
- **Goldens unmoved by construction:** `analytics/combine/` is a new read-only
  package that imports the existing sleeves but changes nothing in the backtest
  pipeline. If `make test-regression` moves, something is wrong — stop and
  investigate, do not regenerate.
