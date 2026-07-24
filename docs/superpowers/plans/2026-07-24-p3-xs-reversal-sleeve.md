# P3 Cross-Sectional Short-Horizon Reversal Sleeve — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a short-horizon (2–7 day) cross-sectional reversal sleeve as a candidate second strong edge, reusing the validated XS-momentum book by injecting a sign-flipped reversal forecast, and emit a de-biased BUILD/SHELF/REDUNDANT/INSUFFICIENT verdict.

**Architecture:** The XS pipeline (`analytics/xsmom/book.py`) is `xs_forecasts` (EWMAC) → `xs_demeaned_forecasts` → `xs_leverage` (shift + vol-parity) → `run_xs_backtest` (honest-cost book + 20%-vol governor). Only the first step is signal-specific. We make the forecast **injectable** (additive keyword-only `forecasts=None`, byte-identical when None), then add a new `analytics/xsrev/` sibling package that builds a reversal forecast matrix and feeds it through the shared book. Read-only over `analytics.db`; no schema/golden change.

**Tech Stack:** Python 3.11+, Poetry, pandas, numpy, duckdb, pytest. Spec: `docs/superpowers/specs/2026-07-24-p3-xs-reversal-sleeve-design.md`.

## Global Constraints

- Every module is **pure, causal, read-only**; the sleeve is **additive / default-off** — no schema change, no golden change.
- **Definition of Done gate** after every Python change: `make lint-py` ✓ (ruff), `make typecheck` ✓ (mypy strict), `make test` green, `make test-regression` goldens unmoved.
- **All functions fully type-annotated** (mypy strict — `disallow_untyped_defs`); `-> None` on test functions.
- **A-priori constants, DO NOT TUNE:** `formation_windows = (2, 3, 5, 7)` days, `reversal_scalar = 10.0`, `fdm = 1.25`. k=1 is diagnostic-only, never in the headline family.
- **De-biased gate:** `DSR ≥ 0.95 ∧ PBO ≤ 0.5 ∧ block-bootstrap-CI lower bound > 0`, and still positive at 8 bps/leg cost.
- Tests must use `duckdb.connect(":memory:")` — never touch the real `analytics.db`.
- Every commit message ends with the trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- **Never `git add` `.claude/skills/humanizer`** (it is in `.git/info/exclude`).

## File Structure

| File | Responsibility |
| --- | --- |
| `analytics/xsmom/book.py` (modify) | Add `forecasts: pd.DataFrame \| None = None` kwarg to `xs_demeaned_forecasts` / `xs_leverage` / `run_xs_backtest`; None = current EWMAC path, byte-identical |
| `analytics/xsrev/config.py` (create) | `ReversalConfig` frozen dataclass (composes `sleeve_cfg: ForecastConfig`) |
| `analytics/xsrev/forecast.py` (create) | Reversal forecast math + `reversal_forecast_matrix`; later the descriptive `crowding_forecast_matrix` |
| `analytics/xsrev/book.py` (create) | `run_xsrev_backtest` — build reversal forecast, call the shared XS book |
| `analytics/xsrev/replay.py` (create) | `replay_xsrev` / `replay_xsrev_trials`; later `load_daily_open_interest` |
| `analytics/xsrev/__init__.py` (create) | Package export surface |
| `tools/xsrev_audit.py` (create) | Read-only audit CLI (breadth / cost / per-k / corr-to-XS / OI panel) |
| `Makefile` (modify) | `buibui-xsrev-audit` target + `.PHONY` |
| `tests/xsrev/` (create) | Package unit tests |
| `tests/xsmom/test_book.py` (modify) | Add the injection + equivalence tests |
| `docs/audits/2026-07-24-p3-xs-reversal-sleeve.md` (create) | Verdict doc (Task 9) |

---

### Task 1: Make the XS forecast injectable (additive, default-off, byte-identical)

**Files:**

- Modify: `analytics/xsmom/book.py:47-126`
- Test: `tests/xsmom/test_book.py` (append)

**Interfaces:**

- Consumes: existing `xs_forecasts(closes, cfg) -> pd.DataFrame`.
- Produces:
  - `xs_demeaned_forecasts(closes, cfg, *, forecasts: pd.DataFrame | None = None) -> pd.DataFrame`
  - `xs_leverage(closes, cfg, *, forecasts: pd.DataFrame | None = None) -> pd.DataFrame`
  - `run_xs_backtest(closes, fundings, cfg, *, turnover_cost_rate=None, forecasts: pd.DataFrame | None = None) -> XSBookResult`
  - When `forecasts is None`, all three are byte-identical to today. When supplied, the raw per-instrument forecast matrix (union-indexed, NaN warmup preserved) is demeaned → shifted → vol-parity-levered in place of the EWMAC path.

- [ ] **Step 1: Write the failing tests** (append to `tests/xsmom/test_book.py`)

```python
def test_injected_forecast_equals_internal_path() -> None:
    # Injecting the SAME forecast the internal EWMAC path computes must be
    # byte-identical to the default path — proves the wiring + default are intact.
    from analytics.xsmom.book import run_xs_backtest, xs_forecasts

    closes = _closes()
    f = xs_forecasts(closes, ForecastConfig())
    base = run_xs_backtest(closes, _fundings(closes), ForecastConfig())
    inj = run_xs_backtest(closes, _fundings(closes), ForecastConfig(), forecasts=f)
    np.testing.assert_array_equal(base.portfolio_return, inj.portfolio_return)


def test_injected_forecast_overrides_signal() -> None:
    # A negated forecast must flip the leverage sign vs the default path.
    from analytics.xsmom.book import xs_forecasts, xs_leverage

    closes = _closes()
    f = xs_forecasts(closes, ForecastConfig())
    base = xs_leverage(closes, ForecastConfig())
    neg = xs_leverage(closes, ForecastConfig(), forecasts=-f)
    assert base.iloc[-1]["STRONG"] > 0.0
    assert neg.iloc[-1]["STRONG"] < 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/xsmom/test_book.py::test_injected_forecast_equals_internal_path tests/xsmom/test_book.py::test_injected_forecast_overrides_signal -v`
Expected: FAIL — `run_xs_backtest() got an unexpected keyword argument 'forecasts'`.

- [ ] **Step 3: Add the `forecasts=` kwarg to the three functions**

In `analytics/xsmom/book.py`, change `xs_demeaned_forecasts`:

```python
def xs_demeaned_forecasts(
    closes: dict[str, pd.Series],
    cfg: ForecastConfig,
    *,
    forecasts: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Cross-sectionally demeaned forecasts (relative strength).

    `g_i(d) = f_i(d) - mean_{j in active(d)} f_j(d)`; the row mean skips NaN so it
    is taken over the active instruments only. Each active row sums to ~0
    (dollar-neutral). Not yet shifted — see `xs_leverage`. When ``forecasts`` is
    given (a raw per-instrument forecast matrix, union-indexed, NaN warmup
    preserved) it is demeaned in place of the internal EWMAC path — this is how a
    sibling sleeve (e.g. reversal) injects its own signal. ``None`` is
    byte-identical to the EWMAC path.
    """
    f = xs_forecasts(closes, cfg) if forecasts is None else forecasts
    return f.sub(f.mean(axis=1), axis=0)
```

Change `xs_leverage` signature + its first line:

```python
def xs_leverage(
    closes: dict[str, pd.Series],
    cfg: ForecastConfig,
    *,
    forecasts: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Causal cross-sectional (demeaned) vol-parity leverage matrix.

    Demean the forecast across active instruments (dollar-neutral), shift one day
    (position on day `d` uses info through `d-1`), then vol-target each leg:
    `leverage_i = (g_i_shifted / 10) * (vol_target / vol_ann_i)`. The `/10` mirrors
    the trend sleeve so magnitudes are comparable; the absolute level is governed
    downstream. Columns = symbols, index = union daily index. When
    ``cfg.xs_dollar_neutral`` is set, the matrix is re-centered so each day's
    active leverage sums to zero (dollar-neutral). ``forecasts`` (default ``None``,
    the EWMAC path) injects a sibling sleeve's raw forecast matrix.
    """
    demeaned = xs_demeaned_forecasts(closes, cfg, forecasts=forecasts)
    demeaned_shifted = demeaned.shift(1)
```

(Leave the rest of `xs_leverage` unchanged.) Change `run_xs_backtest` signature + its first line:

```python
def run_xs_backtest(
    closes: dict[str, pd.Series],
    fundings: dict[str, pd.Series],
    cfg: ForecastConfig,
    *,
    turnover_cost_rate: pd.DataFrame | None = None,
    forecasts: pd.DataFrame | None = None,
) -> XSBookResult:
```

and its first body line:

```python
    leverage = xs_leverage(closes, cfg, forecasts=forecasts)
```

Append one sentence to the `run_xs_backtest` docstring: `` ``forecasts`` (default ``None``) injects a sibling sleeve's raw forecast matrix in place of the EWMAC path;``None``is byte-identical.``

- [ ] **Step 4: Run the new tests + the full xsmom book suite**

Run: `poetry run pytest tests/xsmom/test_book.py -v`
Expected: PASS (all, including the two new tests).

- [ ] **Step 5: Verify nothing else moved**

Run: `make lint-py && make typecheck && poetry run pytest tests/xsmom -q && make test-regression`
Expected: lint/typecheck clean; xsmom tests pass; goldens unmoved.

- [ ] **Step 6: Commit**

```bash
git add analytics/xsmom/book.py tests/xsmom/test_book.py
git commit -m "refactor(xsmom): injectable forecast matrix (additive, default-off, byte-identical)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: `ReversalConfig`

**Files:**

- Create: `analytics/xsrev/config.py`
- Create: `analytics/xsrev/__init__.py` (minimal — expanded in Task 6)
- Test: `tests/xsrev/__init__.py`, `tests/xsrev/test_config.py`

**Interfaces:**

- Produces: `ReversalConfig(sleeve_cfg: ForecastConfig = ForecastConfig(), formation_windows: tuple[int, ...] = (2, 3, 5, 7), reversal_scalar: float = 10.0, fdm: float = 1.25)`, with properties `vol_span: int`, `cap: float`, and `from_toml(path) -> ReversalConfig`.

- [ ] **Step 1: Write the failing test** (`tests/xsrev/test_config.py`)

```python
from __future__ import annotations

import pytest

from analytics.forecast.config import ForecastConfig
from analytics.xsrev.config import ReversalConfig


def test_defaults_are_a_priori() -> None:
    cfg = ReversalConfig()
    assert cfg.formation_windows == (2, 3, 5, 7)
    assert cfg.reversal_scalar == 10.0
    assert cfg.fdm == 1.25
    # k=1 excluded from the headline family
    assert 1 not in cfg.formation_windows


def test_properties_delegate_to_sleeve_cfg() -> None:
    cfg = ReversalConfig(sleeve_cfg=ForecastConfig(vol_span=16, cap=15.0))
    assert cfg.vol_span == 16
    assert cfg.cap == 15.0


def test_empty_windows_rejected() -> None:
    with pytest.raises(ValueError):
        ReversalConfig(formation_windows=())


def test_zero_window_rejected() -> None:
    with pytest.raises(ValueError):
        ReversalConfig(formation_windows=(0, 2))
```

Also create an empty `tests/xsrev/__init__.py` (one blank line).

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/xsrev/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'analytics.xsrev'`.

- [ ] **Step 3: Create the package + config**

`analytics/xsrev/__init__.py`:

```python
"""P3 cross-sectional short-horizon reversal sleeve (read-only, default-off).

A sign-flipped short-horizon (2-7 day) cross-sectional return signal, fed through
the validated XS-momentum demean/leverage/cost/governor book via its injectable
`forecasts=` hook. Candidate second strong edge, decorrelated from XS-solo. Pure,
read-only over ``analytics.db``, additive — no schema/golden change.
"""

from analytics.xsrev.config import ReversalConfig

__all__ = ["ReversalConfig"]
```

`analytics/xsrev/config.py`:

```python
"""Configuration for the P3 cross-sectional reversal sleeve.

Frozen dataclass composing a ``ForecastConfig`` for the shared honest-cost / vol /
governor constants (mirrors ``carry.CarryConfig`` holding ``sleeve_cfg``). The
reversal-specific knobs (formation-window family, a-priori forecast scalar, FDM)
are used only to BUILD the forecast matrix; the shared book supplies everything
downstream. All constants are a-priori, NOT crypto-fit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from analytics.forecast.config import ForecastConfig


@dataclass(frozen=True)
class ReversalConfig:
    sleeve_cfg: ForecastConfig = field(default_factory=ForecastConfig)
    formation_windows: tuple[int, ...] = (2, 3, 5, 7)
    reversal_scalar: float = 10.0
    fdm: float = 1.25

    def __post_init__(self) -> None:
        if not self.formation_windows:
            raise ValueError("formation_windows must be non-empty")
        if any(w < 1 for w in self.formation_windows):
            raise ValueError("formation_windows must all be >= 1")

    @property
    def vol_span(self) -> int:
        return self.sleeve_cfg.vol_span

    @property
    def cap(self) -> float:
        return self.sleeve_cfg.cap

    @classmethod
    def from_toml(cls, path: Path | str) -> ReversalConfig:
        return cls(sleeve_cfg=ForecastConfig.from_toml(path))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/xsrev/test_config.py -v`
Expected: PASS.

- [ ] **Step 5: Lint + typecheck, then commit**

Run: `make lint-py && make typecheck`

```bash
git add analytics/xsrev/__init__.py analytics/xsrev/config.py tests/xsrev/__init__.py tests/xsrev/test_config.py
git commit -m "feat(xsrev): ReversalConfig — a-priori reversal sleeve config

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Reversal forecast math

**Files:**

- Create: `analytics/xsrev/forecast.py`
- Test: `tests/xsrev/test_forecast.py`

**Interfaces:**

- Consumes: `ew_return_vol(close, span) -> pd.Series` (from `analytics.forecast.vol`); `_union_index(closes) -> pd.DatetimeIndex` (from `analytics.xsmom.book`); `ReversalConfig`.
- Produces:
  - `scaled_reversal_forecast(close, window, scalar, vol_span, cap) -> pd.Series`
  - `combine_reversal_forecasts(close, windows, scalar, fdm, vol_span, cap) -> pd.Series`
  - `reversal_forecast_matrix(closes: dict[str, pd.Series], cfg: ReversalConfig) -> pd.DataFrame` — per-instrument raw forecast, aligned to the union daily index, NaN warmup preserved (same shape `xs_forecasts` produces, so the shared demean/leverage consume it unchanged).

- [ ] **Step 1: Write the failing tests** (`tests/xsrev/test_forecast.py`)

```python
from __future__ import annotations

import numpy as np
import pandas as pd

from analytics.xsrev.config import ReversalConfig
from analytics.xsrev.forecast import (
    combine_reversal_forecasts,
    reversal_forecast_matrix,
    scaled_reversal_forecast,
)


def _idx(n: int) -> pd.DatetimeIndex:
    return pd.date_range("2021-01-01", periods=n, freq="D", tz="UTC")


def test_reversal_sign_short_recent_winner() -> None:
    # A steadily rising price is a recent WINNER -> reversal forecast is negative.
    close = pd.Series(np.linspace(100.0, 200.0, 80), index=_idx(80))
    f = scaled_reversal_forecast(close, window=3, scalar=10.0, vol_span=32, cap=20.0)
    assert f.dropna().iloc[-1] < 0.0
    # A falling price is a recent LOSER -> reversal forecast is positive.
    down = pd.Series(np.linspace(200.0, 100.0, 80), index=_idx(80))
    g = scaled_reversal_forecast(down, window=3, scalar=10.0, vol_span=32, cap=20.0)
    assert g.dropna().iloc[-1] > 0.0


def test_reversal_forecast_is_causal() -> None:
    # Bumping a LATER close must not change forecast values strictly before it.
    close = pd.Series(np.linspace(100.0, 150.0, 80), index=_idx(80))
    bumped = close.copy()
    k = 60
    bumped.iloc[k] *= 1.3
    a = scaled_reversal_forecast(close, 3, 10.0, 32, 20.0)
    b = scaled_reversal_forecast(bumped, 3, 10.0, 32, 20.0)
    np.testing.assert_allclose(a.iloc[:k].to_numpy(), b.iloc[:k].to_numpy())


def test_reversal_forecast_capped() -> None:
    # Tiny vol + a big move saturates the cap.
    close = pd.Series(np.linspace(100.0, 100.5, 60), index=_idx(60))
    close.iloc[-1] = 130.0  # violent late move, tiny prior vol
    f = scaled_reversal_forecast(close, 2, 10.0, 32, 20.0)
    assert f.dropna().abs().max() <= 20.0 + 1e-9


def test_combine_is_mean_times_fdm_recapped() -> None:
    close = pd.Series(np.linspace(100.0, 130.0, 90), index=_idx(90))
    windows = (2, 3)
    parts = [scaled_reversal_forecast(close, w, 10.0, 32, 20.0) for w in windows]
    expected = (pd.concat(parts, axis=1).mean(axis=1) * 1.25).clip(-20.0, 20.0)
    got = combine_reversal_forecasts(close, windows, 10.0, 1.25, 32, 20.0)
    np.testing.assert_allclose(
        got.dropna().to_numpy(), expected.reindex(got.index).dropna().to_numpy()
    )


def test_matrix_aligned_to_union_with_nan_warmup() -> None:
    closes = {
        "UP": pd.Series(np.linspace(100.0, 200.0, 100), index=_idx(100)),
        "DOWN": pd.Series(np.linspace(200.0, 100.0, 100), index=_idx(100)),
    }
    m = reversal_forecast_matrix(closes, ReversalConfig())
    assert list(m.columns) == ["UP", "DOWN"]
    assert len(m) == 100
    assert m.iloc[0].isna().all()  # warmup NaN preserved (not filled 0)
    # last bar: winner UP short (negative), loser DOWN long (positive)
    assert m.iloc[-1]["UP"] < 0.0
    assert m.iloc[-1]["DOWN"] > 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/xsrev/test_forecast.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'analytics.xsrev.forecast'`.

- [ ] **Step 3: Implement `analytics/xsrev/forecast.py`**

```python
"""Cross-sectional reversal forecast math — sign-flipped short-horizon return.

Pure functions over per-instrument close Series. No DB, no IO. Mirrors
``analytics.carry.forecast``: a vol-adjusted, scalar-adjusted, capped single-window
forecast, then an equal-weight combine over the window family x FDM, re-capped.
The forecast is NEGATIVE for a recent winner (reversal: short winners, long losers).
Causal within the series (``ew_return_vol`` is shifted); the position-level
``.shift(1)`` that makes sizing causal lives downstream in ``xs_leverage``.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from analytics.forecast.vol import ew_return_vol
from analytics.xsmom.book import _union_index
from analytics.xsrev.config import ReversalConfig


def _return_zscore(close: pd.Series, window: int, vol_span: int) -> pd.Series:
    """Horizon-normalized recent return: ``r_k / (sigma_daily * sqrt(k))``.

    ``sigma_daily`` is the causal EW daily return vol; ``* sqrt(window)`` scales it
    to the window's horizon so windows are comparable in the equal-weight combine.
    """
    r = close / close.shift(window) - 1.0
    sigma = ew_return_vol(close, vol_span)
    z = r / (sigma * math.sqrt(window))
    return z.replace([np.inf, -np.inf], np.nan)


def scaled_reversal_forecast(
    close: pd.Series, window: int, scalar: float, vol_span: int, cap: float
) -> pd.Series:
    """Vol-adjusted, scalar-adjusted, capped single-window reversal forecast.

    ``forecast = clip(-scalar * zscore, +/-cap)`` — the minus is the reversal
    (short recent winners, long recent losers).
    """
    z = _return_zscore(close, window, vol_span)
    return (-scalar * z).clip(lower=-cap, upper=cap)


def combine_reversal_forecasts(
    close: pd.Series,
    windows: tuple[int, ...],
    scalar: float,
    fdm: float,
    vol_span: int,
    cap: float,
) -> pd.Series:
    """Equal-weight mean of per-window reversal forecasts x FDM, re-capped."""
    parts = [
        scaled_reversal_forecast(close, w, scalar, vol_span, cap) for w in windows
    ]
    mean = pd.concat(parts, axis=1).mean(axis=1)
    return (mean * fdm).clip(lower=-cap, upper=cap)


def reversal_forecast_matrix(
    closes: dict[str, pd.Series], cfg: ReversalConfig
) -> pd.DataFrame:
    """Per-instrument raw reversal forecasts, aligned to the union daily index.

    Same shape as ``xsmom.book.xs_forecasts`` (columns = symbols, NaN warmup
    preserved), so the shared cross-sectional demean/leverage consume it unchanged.
    """
    union = _union_index(closes)
    cols: dict[str, pd.Series] = {}
    for sym, close in closes.items():
        f = combine_reversal_forecasts(
            close,
            cfg.formation_windows,
            cfg.reversal_scalar,
            cfg.fdm,
            cfg.vol_span,
            cfg.cap,
        )
        cols[sym] = f.reindex(union)
    return pd.DataFrame(cols, index=union)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/xsrev/test_forecast.py -v`
Expected: PASS.

- [ ] **Step 5: Lint + typecheck, then commit**

Run: `make lint-py && make typecheck`

```bash
git add analytics/xsrev/forecast.py tests/xsrev/test_forecast.py
git commit -m "feat(xsrev): reversal forecast math (sign-flipped vol-scaled return)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Reversal book (feed the shared XS book)

**Files:**

- Create: `analytics/xsrev/book.py`
- Test: `tests/xsrev/test_book.py`

**Interfaces:**

- Consumes: `run_xs_backtest(..., forecasts=)` (Task 1), `reversal_forecast_matrix` (Task 3), `xs_leverage` (for the sign test).
- Produces: `run_xsrev_backtest(closes, fundings, cfg: ReversalConfig, *, turnover_cost_rate: pd.DataFrame | None = None) -> XSBookResult`; re-exports `XSBookResult` and `equity_curve`.

- [ ] **Step 1: Write the failing tests** (`tests/xsrev/test_book.py`)

```python
from __future__ import annotations

import numpy as np
import pandas as pd

from analytics.xsmom.book import XSBookResult, xs_leverage
from analytics.xsrev.book import run_xsrev_backtest
from analytics.xsrev.config import ReversalConfig
from analytics.xsrev.forecast import reversal_forecast_matrix


def _closes() -> dict[str, pd.Series]:
    idx = pd.date_range("2021-01-01", periods=400, freq="D", tz="UTC")
    return {
        "STRONG": pd.Series(np.linspace(100.0, 400.0, 400), index=idx),
        "WEAK": pd.Series(np.linspace(400.0, 100.0, 400), index=idx),
    }


def _fundings(closes: dict[str, pd.Series]) -> dict[str, pd.Series]:
    return {s: pd.Series(0.0, index=c.index) for s, c in closes.items()}


def test_reversal_leverage_is_inverse_of_momentum() -> None:
    # Reversal SHORTS the recent winner and LONGS the recent loser — the exact
    # inverse of XS momentum's sign on the same monotone cross-section.
    closes = _closes()
    cfg = ReversalConfig()
    lev = xs_leverage(
        closes, cfg.sleeve_cfg, forecasts=reversal_forecast_matrix(closes, cfg)
    )
    last = lev.iloc[-1]
    assert last["STRONG"] < 0.0  # winner held short
    assert last["WEAK"] > 0.0    # loser held long


def test_run_xsrev_backtest_shape_and_finite() -> None:
    closes = _closes()
    res = run_xsrev_backtest(closes, _fundings(closes), ReversalConfig())
    assert isinstance(res, XSBookResult)
    assert res.portfolio_return.shape[0] == 400
    assert not np.isnan(res.portfolio_return).any()
    assert res.active_count.max() == 2


def test_run_xsrev_backtest_is_causal_no_lookahead() -> None:
    closes = _closes()
    cfg = ReversalConfig()
    base = xs_leverage(
        closes, cfg.sleeve_cfg, forecasts=reversal_forecast_matrix(closes, cfg)
    )
    k = 250
    bumped = {s: c.copy() for s, c in closes.items()}
    bumped["STRONG"].iloc[k] *= 1.5
    after = xs_leverage(
        bumped, cfg.sleeve_cfg, forecasts=reversal_forecast_matrix(bumped, cfg)
    )
    pd.testing.assert_frame_equal(
        base.iloc[: k + 1], after.iloc[: k + 1], check_names=False
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/xsrev/test_book.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'analytics.xsrev.book'`.

- [ ] **Step 3: Implement `analytics/xsrev/book.py`**

```python
"""Reversal book: build the reversal forecast, run the shared XS book.

The demean, vol-parity leverage, honest-cost accrual, and 20%-vol governor are all
reused verbatim from ``analytics.xsmom.book.run_xs_backtest`` via its injectable
``forecasts=`` hook — this module only supplies the reversal forecast matrix and
the sleeve's shared ``ForecastConfig`` (``cfg.sleeve_cfg``) for the sizing/cost
constants. Returns the reused ``XSBookResult``.
"""

from __future__ import annotations

import pandas as pd

from analytics.xsmom.book import XSBookResult, equity_curve, run_xs_backtest
from analytics.xsrev.config import ReversalConfig
from analytics.xsrev.forecast import reversal_forecast_matrix

__all__ = ["XSBookResult", "equity_curve", "run_xsrev_backtest"]


def run_xsrev_backtest(
    closes: dict[str, pd.Series],
    fundings: dict[str, pd.Series],
    cfg: ReversalConfig,
    *,
    turnover_cost_rate: pd.DataFrame | None = None,
) -> XSBookResult:
    """Causal dollar-neutral long-short reversal book (reuses the XS book)."""
    forecasts = reversal_forecast_matrix(closes, cfg)
    return run_xs_backtest(
        closes,
        fundings,
        cfg.sleeve_cfg,
        turnover_cost_rate=turnover_cost_rate,
        forecasts=forecasts,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/xsrev/test_book.py -v`
Expected: PASS.

- [ ] **Step 5: Lint + typecheck, then commit**

Run: `make lint-py && make typecheck`

```bash
git add analytics/xsrev/book.py tests/xsrev/test_book.py
git commit -m "feat(xsrev): reversal book over the shared XS demean/cost/governor

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Reversal replay (read-only DB front door)

**Files:**

- Create: `analytics/xsrev/replay.py`
- Test: `tests/xsrev/test_replay.py`

**Interfaces:**

- Consumes: `load_daily_inputs(conn, syms)` (from `analytics.forecast.replay`), `load_universe()`, `run_xsrev_backtest` (Task 4).
- Produces:
  - `replay_xsrev(conn, cfg: ReversalConfig, symbols: list[str] | None = None) -> XSBookResult`
  - `replay_xsrev_trials(conn, cfg, symbols=None) -> dict[str, np.ndarray]` — keys `k{w}` per window + `combined`.

- [ ] **Step 1: Write the failing tests** (`tests/xsrev/test_replay.py`)

```python
from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd

from analytics.store import init_schema
from analytics.store.market_data import upsert_ohlcv
from analytics.xsmom.book import XSBookResult
from analytics.xsrev.config import ReversalConfig
from analytics.xsrev.replay import replay_xsrev, replay_xsrev_trials

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


def test_replay_xsrev_returns_book_result() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    _seed(conn, "AAAUSDT", 1.0)
    _seed(conn, "BBBUSDT", -0.5)
    res = replay_xsrev(conn, ReversalConfig(), symbols=["AAAUSDT", "BBBUSDT"])
    assert isinstance(res, XSBookResult)
    assert res.portfolio_return.shape[0] > 0
    assert not np.isnan(res.portfolio_return).any()


def test_replay_xsrev_trials_has_per_window_plus_combined() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    _seed(conn, "AAAUSDT", 1.0)
    _seed(conn, "BBBUSDT", -0.5)
    trials = replay_xsrev_trials(
        conn, ReversalConfig(), symbols=["AAAUSDT", "BBBUSDT"]
    )
    assert set(trials) == {"k2", "k3", "k5", "k7", "combined"}
    for v in trials.values():
        assert isinstance(v, np.ndarray)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/xsrev/test_replay.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'analytics.xsrev.replay'`.

- [ ] **Step 3: Implement `analytics/xsrev/replay.py`**

```python
"""Read-only DuckDB front door for the cross-sectional reversal sleeve.

Reuses the trend sleeve's ``load_daily_inputs`` (1d closes + summed daily funding)
and runs the reversal book. Read-only; never writes.
"""

from __future__ import annotations

import dataclasses

import duckdb
import numpy as np

from analytics.forecast.replay import load_daily_inputs
from analytics.universe import load_universe
from analytics.xsmom.book import XSBookResult
from analytics.xsrev.book import run_xsrev_backtest
from analytics.xsrev.config import ReversalConfig


def replay_xsrev(
    conn: duckdb.DuckDBPyConnection,
    cfg: ReversalConfig,
    symbols: list[str] | None = None,
) -> XSBookResult:
    """Load the universe's 1d inputs and run the reversal book (read-only)."""
    syms = symbols if symbols is not None else load_universe()
    closes, fundings = load_daily_inputs(conn, syms)
    return run_xsrev_backtest(closes, fundings, cfg)


def replay_xsrev_trials(
    conn: duckdb.DuckDBPyConnection,
    cfg: ReversalConfig,
    symbols: list[str] | None = None,
) -> dict[str, np.ndarray]:
    """Daily reversal portfolio returns per single-window sleeve + the combined book.

    The honest multiple-testing family for DSR/PBO. Keys: ``k{w}`` per window in
    ``cfg.formation_windows``, plus ``combined``.
    """
    syms = symbols if symbols is not None else load_universe()
    closes, fundings = load_daily_inputs(conn, syms)

    trials: dict[str, np.ndarray] = {}
    for w in cfg.formation_windows:
        single = dataclasses.replace(cfg, formation_windows=(w,))
        trials[f"k{w}"] = run_xsrev_backtest(closes, fundings, single).portfolio_return

    combined = run_xsrev_backtest(closes, fundings, cfg)
    trials["combined"] = combined.portfolio_return
    return trials
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/xsrev/test_replay.py -v`
Expected: PASS.

- [ ] **Step 5: Lint + typecheck, then commit**

Run: `make lint-py && make typecheck`

```bash
git add analytics/xsrev/replay.py tests/xsrev/test_replay.py
git commit -m "feat(xsrev): read-only replay + per-window DSR/PBO trial family

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Package export surface

**Files:**

- Modify: `analytics/xsrev/__init__.py`
- Test: `tests/xsrev/test_init.py`

**Interfaces:**

- Produces: `analytics.xsrev` re-exports `ReversalConfig`, `run_xsrev_backtest`, `XSBookResult`, `equity_curve`, `reversal_forecast_matrix`, `combine_reversal_forecasts`, `scaled_reversal_forecast`, `replay_xsrev`, `replay_xsrev_trials`.

- [ ] **Step 1: Write the failing test** (`tests/xsrev/test_init.py`)

```python
from __future__ import annotations

import analytics.xsrev as xsrev


def test_public_surface() -> None:
    expected = {
        "ReversalConfig",
        "XSBookResult",
        "combine_reversal_forecasts",
        "equity_curve",
        "reversal_forecast_matrix",
        "replay_xsrev",
        "replay_xsrev_trials",
        "run_xsrev_backtest",
        "scaled_reversal_forecast",
    }
    assert expected.issubset(set(xsrev.__all__))
    for name in expected:
        assert hasattr(xsrev, name)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/xsrev/test_init.py -v`
Expected: FAIL — `AttributeError: module 'analytics.xsrev' has no attribute 'run_xsrev_backtest'`.

- [ ] **Step 3: Expand `analytics/xsrev/__init__.py`**

```python
"""P3 cross-sectional short-horizon reversal sleeve (read-only, default-off).

A sign-flipped short-horizon (2-7 day) cross-sectional return signal, fed through
the validated XS-momentum demean/leverage/cost/governor book via its injectable
`forecasts=` hook. Candidate second strong edge, decorrelated from XS-solo. Pure,
read-only over ``analytics.db``, additive — no schema/golden change.
"""

from analytics.xsmom.book import XSBookResult, equity_curve
from analytics.xsrev.book import run_xsrev_backtest
from analytics.xsrev.config import ReversalConfig
from analytics.xsrev.forecast import (
    combine_reversal_forecasts,
    reversal_forecast_matrix,
    scaled_reversal_forecast,
)
from analytics.xsrev.replay import replay_xsrev, replay_xsrev_trials

__all__ = [
    "ReversalConfig",
    "XSBookResult",
    "combine_reversal_forecasts",
    "equity_curve",
    "reversal_forecast_matrix",
    "replay_xsrev",
    "replay_xsrev_trials",
    "run_xsrev_backtest",
    "scaled_reversal_forecast",
]
```

- [ ] **Step 4: Run test + the whole xsrev suite**

Run: `poetry run pytest tests/xsrev -v`
Expected: PASS (all).

- [ ] **Step 5: Lint + typecheck, then commit**

Run: `make lint-py && make typecheck`

```bash
git add analytics/xsrev/__init__.py tests/xsrev/test_init.py
git commit -m "feat(xsrev): package export surface

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: Audit CLI + Makefile target

**Files:**

- Create: `tools/xsrev_audit.py`
- Modify: `Makefile` (add `buibui-xsrev-audit` target + `.PHONY` entry on line 14)
- Test: `tests/xsrev/test_audit_cli.py`

**Interfaces:**

- Consumes: `replay_xsrev`, `replay_xsrev_trials` (Task 5), `replay_xs` (XS momentum, for the corr-to-XS read), `evaluate_xs`, `load_universe`, `ForecastConfig`, `DEFAULT_DB_PATH`.
- Produces: `build_xsrev_report_row(conn, label, symbols, slippage_bps) -> dict[str, object]` (keys include `sharpe`, `dsr`, `pbo`, `boot_lo`, `min_trl`, `corr_to_xs`, `xs_sharpe`) and `main() -> None`.

**Note on `corr_to_xs`:** `evaluate_xs` returns the diversification correlation in its `corr_to_trend` field. We pass the **XS-momentum** book's returns as `trend_returns`, so `corr_to_trend` is literally the reversal-vs-XS correlation. The audit relabels that column `corr_to_xs` (and `trend_sharpe` → `xs_sharpe`). No new report type is needed.

- [ ] **Step 1: Write the failing test** (`tests/xsrev/test_audit_cli.py`)

```python
from __future__ import annotations

import duckdb
import pandas as pd

from analytics.store import init_schema
from analytics.store.market_data import upsert_ohlcv
from tools.xsrev_audit import build_xsrev_report_row

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


def test_build_xsrev_report_row_returns_dict() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    _seed(conn, "AAAUSDT", 1.0)
    _seed(conn, "BBBUSDT", -0.5)
    row = build_xsrev_report_row(
        conn, "label", symbols=["AAAUSDT", "BBBUSDT"], slippage_bps=2.0
    )
    assert row["label"] == "label"
    for col in ("sharpe", "dsr", "pbo", "boot_lo", "min_trl", "corr_to_xs", "xs_sharpe"):
        assert col in row
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/xsrev/test_audit_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.xsrev_audit'`.

- [ ] **Step 3: Implement `tools/xsrev_audit.py`**

```python
"""Cross-sectional reversal sleeve audit (P3) — read-only verdict.

Replays the sign-flipped short-horizon reversal book across the N3 universe (1d)
and prints: a breadth contrast (universe vs majors), a cost-sensitivity sweep, the
per-window (k) Sharpes, the k=1 bid-ask-bounce diagnostic, a scalar-sensitivity
table, and the correlation to the XS-momentum deploy core — each with
DSR/PBO/bootstrap-CI/MinTRL stamps. Read-only; no writes, no schema changes.

Usage::

    PYTHONPATH=. poetry run python tools/xsrev_audit.py
    PYTHONPATH=. poetry run python tools/xsrev_audit.py --majors BTCUSDT,ETHUSDT,SOLUSDT
"""

from __future__ import annotations

import argparse
import dataclasses
import math
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.store import DEFAULT_DB_PATH
from analytics.universe import load_universe
from analytics.xsmom import evaluate_xs, replay_xs
from analytics.xsrev import ReversalConfig, replay_xsrev, replay_xsrev_trials

_MAJORS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]


def _rcfg(slippage_bps: float, scalar: float = 10.0) -> ReversalConfig:
    sleeve = dataclasses.replace(ForecastConfig(), slippage_pct=slippage_bps / 10_000.0)
    return ReversalConfig(sleeve_cfg=sleeve, reversal_scalar=scalar)


def build_xsrev_report_row(
    conn: duckdb.DuckDBPyConnection,
    label: str,
    symbols: list[str],
    slippage_bps: float,
) -> dict[str, object]:
    rcfg = _rcfg(slippage_bps)
    result = replay_xsrev(conn, rcfg, symbols=symbols)
    trials = replay_xsrev_trials(conn, rcfg, symbols=symbols)
    xs_cfg = dataclasses.replace(ForecastConfig(), slippage_pct=slippage_bps / 10_000.0)
    xs_ret = replay_xs(conn, xs_cfg, symbols=symbols).portfolio_return
    rep = evaluate_xs(
        result, rcfg.sleeve_cfg, trial_returns=trials, trend_returns=xs_ret
    )
    return {
        "label": label,
        "n_inst": len(result.per_instrument_net),
        "days": rep.n_obs,
        "sharpe": rep.sharpe_annual,
        "max_dd": rep.max_dd,
        "ann_ret": rep.annual_return,
        "dsr": rep.dsr,
        "pbo": rep.pbo,
        "boot_lo": rep.boot_lo,
        "boot_hi": rep.boot_hi,
        "min_trl": rep.min_trl,
        "corr_to_xs": rep.corr_to_trend,
        "xs_sharpe": rep.trend_sharpe,
        "gate": bool(rep.dsr >= 0.95 and rep.pbo <= 0.5 and rep.boot_lo > 0.0),
    }


def _per_window_sharpes(
    conn: duckdb.DuckDBPyConnection, symbols: list[str]
) -> pd.DataFrame:
    cfg = ReversalConfig()
    ann = math.sqrt(cfg.sleeve_cfg.annualization_days)
    trials = replay_xsrev_trials(conn, cfg, symbols=symbols)
    rows = []
    for name, r in trials.items():
        sd = float(np.std(r, ddof=1)) if len(r) > 1 else 0.0
        sr = (float(np.mean(r)) / sd * ann) if sd > 1e-12 else 0.0
        rows.append({"trial": name, "sharpe": sr})
    return pd.DataFrame(rows)


def _k1_diagnostic_row(
    conn: duckdb.DuckDBPyConnection, symbols: list[str]
) -> dict[str, object]:
    # k=1 is excluded from the headline family (bid-ask bounce). Report it alone
    # so a "k=1-only, dies with cost" pattern is visible = microstructure, not alpha.
    cfg = dataclasses.replace(ReversalConfig(), formation_windows=(1,))
    result = replay_xsrev(conn, cfg, symbols=symbols)
    trials = replay_xsrev_trials(conn, cfg, symbols=symbols)
    xs_ret = replay_xs(conn, ForecastConfig(), symbols=symbols).portfolio_return
    rep = evaluate_xs(result, cfg.sleeve_cfg, trial_returns=trials, trend_returns=xs_ret)
    return {"label": "k=1 (diagnostic)", "sharpe": rep.sharpe_annual, "dsr": rep.dsr}


def _scalar_sensitivity(
    conn: duckdb.DuckDBPyConnection, symbols: list[str]
) -> pd.DataFrame:
    rows = []
    for s in (5.0, 10.0, 20.0):
        rcfg = _rcfg(2.0, scalar=s)
        sr = replay_xsrev(conn, rcfg, symbols=symbols)
        curve = (1.0 + pd.Series(sr.portfolio_return)).cumprod()
        from portfolio import metrics

        rows.append({"scalar": s, "sharpe": metrics.sharpe(curve)})
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
        "--majors", type=str, default=",".join(_MAJORS), help="majors-only set"
    )
    args = parser.parse_args()

    conn = duckdb.connect(str(args.db), read_only=True)
    print(f"DB: {args.db}")
    universe = load_universe()
    majors = [s.strip().upper() for s in args.majors.split(",") if s.strip()]

    _print_df(
        "Reversal breadth contrast",
        pd.DataFrame(
            [
                build_xsrev_report_row(conn, "universe @2bps", universe, 2.0),
                build_xsrev_report_row(conn, "majors @2bps", majors, 2.0),
            ]
        ),
    )
    _print_df(
        "Cost sensitivity (universe)",
        pd.DataFrame(
            [
                build_xsrev_report_row(conn, f"universe @{b:g}bps", universe, b)
                for b in (0.0, 2.0, 8.0, 16.0)
            ]
        ),
    )
    _print_df("Per-window (k) Sharpe", _per_window_sharpes(conn, universe))
    _print_df("k=1 bounce diagnostic", pd.DataFrame([_k1_diagnostic_row(conn, universe)]))
    _print_df("Scalar sensitivity (universe @2bps)", _scalar_sensitivity(conn, universe))

    print(
        "\nRead: reversal BUILDs only if the universe book clears the gate "
        "(dsr>=0.95, pbo<=0.5, boot_lo>0) AND stays positive at 8bps AND is "
        "additive to XS (corr_to_xs low/negative, not a redundant -XS). A k=1-only "
        "edge that dies with cost is bid-ask bounce, not alpha."
    )


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/xsrev/test_audit_cli.py -v`
Expected: PASS.

- [ ] **Step 5: Add the Makefile target**

In `Makefile`, append `buibui-xsrev-audit` to the big `.PHONY:` list on line 14 (after `buibui-xsmom-audit`). Then add this block immediately after the `buibui-xsmom-audit` target (around line 285):

```makefile
.PHONY: buibui-xsrev-audit
buibui-xsrev-audit:  ## P3: read-only cross-sectional reversal sleeve audit over the N3 universe
 PYTHONPATH=. poetry run python tools/xsrev_audit.py
```

- [ ] **Step 6: Verify the target resolves**

Run: `make -n buibui-xsrev-audit`
Expected: prints `PYTHONPATH=. poetry run python tools/xsrev_audit.py` (dry-run, no execution).

- [ ] **Step 7: Lint + typecheck, then commit**

Run: `make lint-py && make typecheck`

```bash
git add tools/xsrev_audit.py tests/xsrev/test_audit_cli.py Makefile
git commit -m "feat(xsrev): read-only audit CLI + buibui-xsrev-audit target

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 8: OI-positioning descriptive panel (data-limited, NOT gated)

**Files:**

- Modify: `analytics/xsrev/forecast.py` (add `crowding_forecast_matrix`)
- Modify: `analytics/xsrev/replay.py` (add `load_daily_open_interest`)
- Modify: `tools/xsrev_audit.py` (add a clearly-caveated descriptive block)
- Test: `tests/xsrev/test_forecast.py` (append the crowding sign test)

**Interfaces:**

- Produces:
  - `crowding_forecast_matrix(closes, fundings, ois, cfg, *, oi_window=20, funding_span=5) -> pd.DataFrame` — `-sign(EWMA funding) * zscore(OI growth)`, capped, same shape as `reversal_forecast_matrix`.
  - `load_daily_open_interest(conn, symbols) -> dict[str, pd.Series]` — daily (last-per-day) `oi_usd`.

**Caveat (must appear in code + output):** `open_interest` spans ~2026-02-28 → now (~144d majors, ~30–60d rest). This panel is **descriptive only — underpowered, no BUILD/SHELF verdict.**

- [ ] **Step 1: Write the failing test** (append to `tests/xsrev/test_forecast.py`)

```python
def test_crowding_sign_fades_building_crowded_long() -> None:
    from analytics.xsrev.config import ReversalConfig
    from analytics.xsrev.forecast import crowding_forecast_matrix

    idx = _idx(60)
    closes = {"AAA": pd.Series(np.linspace(100.0, 120.0, 60), index=idx)}
    # crowded long (funding > 0) AND open interest steadily BUILDING (rising)
    fundings = {"AAA": pd.Series(0.001, index=idx)}
    ois = {"AAA": pd.Series(np.linspace(1e6, 3e6, 60), index=idx)}
    m = crowding_forecast_matrix(closes, fundings, ois, ReversalConfig())
    # fade it: forecast is negative (lean short the crowded, building long)
    assert m["AAA"].dropna().iloc[-1] < 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/xsrev/test_forecast.py::test_crowding_sign_fades_building_crowded_long -v`
Expected: FAIL — `ImportError: cannot import name 'crowding_forecast_matrix'`.

- [ ] **Step 3: Add `crowding_forecast_matrix` to `analytics/xsrev/forecast.py`**

Add imports if missing (`import numpy as np` already present) and append:

```python
def crowding_forecast_matrix(
    closes: dict[str, pd.Series],
    fundings: dict[str, pd.Series],
    ois: dict[str, pd.Series],
    cfg: ReversalConfig,
    *,
    oi_window: int = 20,
    funding_span: int = 5,
) -> pd.DataFrame:
    """DESCRIPTIVE-ONLY positioning forecast: ``-sign(EWMA funding) * z(OI growth)``.

    Fade the side the crowd is BUILDING into (funding sign = which side is crowded;
    OI-growth z-score = how fast it is building). Same shape as
    ``reversal_forecast_matrix``. NOT a gated signal — ``open_interest`` history is
    shallow (~144d majors); this exists for an underpowered exploratory read only.
    Causal: the OI z-score uses a trailing rolling mean/std.
    """
    union = _union_index(closes)
    cols: dict[str, pd.Series] = {}
    for sym, close in closes.items():
        oi = ois.get(sym)
        fund = fundings.get(sym)
        if oi is None or fund is None:
            continue
        growth = oi.pct_change()
        z = (growth - growth.rolling(oi_window).mean()) / growth.rolling(
            oi_window
        ).std()
        sign_f = np.sign(fund.ewm(span=funding_span, adjust=False).mean())
        raw = (-sign_f * z).clip(lower=-cfg.cap, upper=cfg.cap)
        cols[sym] = raw.reindex(union)
    return pd.DataFrame(cols, index=union)
```

- [ ] **Step 4: Add `load_daily_open_interest` to `analytics/xsrev/replay.py`**

Add `import pandas as pd` if missing, then append:

```python
def load_daily_open_interest(
    conn: duckdb.DuckDBPyConnection,
    symbols: list[str],
) -> dict[str, pd.Series]:
    """Per-symbol daily open interest (last ``oi_usd`` per UTC day), day-indexed.

    DESCRIPTIVE-ONLY: the ``open_interest`` table is shallow (Binance recent-only,
    ~2026-02-28 onward). Read-only. Symbols with no OI rows are skipped.
    """
    out: dict[str, pd.Series] = {}
    for sym in symbols:
        df = conn.execute(
            "SELECT timestamp, oi_usd FROM open_interest "
            "WHERE symbol = ? ORDER BY timestamp",
            [sym],
        ).df()
        if df.empty:
            continue
        idx = pd.to_datetime(df["timestamp"], unit="ms", utc=True).dt.normalize()
        s = pd.Series(df["oi_usd"].to_numpy(dtype=float), index=idx)
        out[sym] = s[~s.index.duplicated(keep="last")].sort_index()
    return out
```

- [ ] **Step 5: Add the descriptive block to `tools/xsrev_audit.py`**

Add to the imports: `from analytics.xsrev import crowding_forecast_matrix` and `from analytics.xsrev.replay import load_daily_open_interest`, `from analytics.forecast.replay import load_daily_inputs`, `from analytics.xsmom.book import run_xs_backtest`, `from portfolio import metrics`. Add this function:

```python
def _oi_crowding_panel(
    conn: duckdb.DuckDBPyConnection, symbols: list[str]
) -> pd.DataFrame:
    """DESCRIPTIVE-ONLY OI-positioning read over the shallow OI overlap window."""
    closes, fundings = load_daily_inputs(conn, symbols)
    ois = load_daily_open_interest(conn, symbols)
    have = [s for s in closes if s in ois]
    if len(have) < 2:
        return pd.DataFrame()
    closes = {s: closes[s] for s in have}
    fundings = {s: fundings[s] for s in have}
    cfg = ReversalConfig()
    forecasts = crowding_forecast_matrix(closes, fundings, ois, cfg)
    res = run_xs_backtest(closes, fundings, cfg.sleeve_cfg, forecasts=forecasts)
    curve = (1.0 + pd.Series(res.portfolio_return)).cumprod()
    return pd.DataFrame(
        [{"panel": "OI crowding (DESCRIPTIVE)", "n_inst": len(have),
          "days": len(res.portfolio_return), "sharpe": metrics.sharpe(curve)}]
    )
```

In `main()`, after the scalar-sensitivity print, add:

```python
    oi = _oi_crowding_panel(conn, universe)
    _print_df("OI-positioning crowding — DESCRIPTIVE ONLY (underpowered)", oi)
    print(
        "\n[OI panel is DESCRIPTIVE ONLY] open_interest is ~144d (majors) / 30-60d "
        "(rest) — far short of MinTRL. No BUILD/SHELF verdict; a rigorous OI arm "
        "needs a deeper OI backfill (CoinGlass, not justified pre-gate)."
    )
```

- [ ] **Step 6: Run tests + typecheck**

Run: `poetry run pytest tests/xsrev/test_forecast.py -v && make lint-py && make typecheck`
Expected: PASS; lint/typecheck clean.

- [ ] **Step 7: Commit**

```bash
git add analytics/xsrev/forecast.py analytics/xsrev/replay.py tools/xsrev_audit.py tests/xsrev/test_forecast.py
git commit -m "feat(xsrev): descriptive OI-positioning crowding panel (data-limited, not gated)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 9: Run the audit, write the verdict, reconcile the SoT

**Files:**

- Create: `docs/audits/2026-07-24-p3-xs-reversal-sleeve.md`
- Modify: `~/.claude-personal/projects/-home-kng-repo-buibui-moon-trader-bot/memory/project_todo_master.md` (add the verdict row) + `MEMORY.md` (Current State line)

**This task has no unit test — its gate is: full DoD green, the audit runs clean on the real DB, and the verdict doc records the actual numbers.**

- [ ] **Step 1: Full DoD gate**

Run: `make lint-py && make typecheck && make test && make test-regression`
Expected: all green; goldens unmoved.

- [ ] **Step 2: Run the audit and capture the numbers**

Run: `make buibui-xsrev-audit 2>&1 | tee /tmp/xsrev-audit.txt`
Expected: prints the breadth contrast, cost sweep, per-window Sharpe, k=1 diagnostic, scalar sensitivity, and the OI descriptive panel — no traceback.

- [ ] **Step 3: Write the verdict doc**

Create `docs/audits/2026-07-24-p3-xs-reversal-sleeve.md` with the real numbers from Step 2. Required structure (mirror `docs/audits/2026-06-19-p3-carry-sleeve.md`), and lead with a **plain-English glossary** (headline → metric → money → backtest-vs-live), per the operator preference:

```markdown
# P3 Cross-Sectional Reversal Sleeve — verdict (2026-07-24)

**Verdict: <BUILD | SHELF | REDUNDANT | INSUFFICIENT>.** <one plain-English sentence>.

## The gate
> DSR >= 0.95 ∧ PBO <= 0.5 ∧ block-bootstrap CI lower bound > 0, still positive at 8 bps.

<the universe/majors breadth table with sharpe/dsr/pbo/boot_lo/corr_to_xs/gate>

## Findings
1. Cost-robustness (0/2/8/16 bps): <...>  — the make-or-break axis.
2. corr_to_xs = <...> vs xs_sharpe <...> — is it additive or just -XS?
3. Per-window (k) Sharpe + the k=1 bounce diagnostic: <is the edge microstructure?>
4. Scalar sensitivity: <governor-normalized, so ~flat = healthy>.
5. OI crowding panel: DESCRIPTIVE ONLY, underpowered (~144d).

## Decision
<BUILD -> gate a live-OOS follow-up like XS; SHELF -> demote, keep as template;
REDUNDANT -> it is -XS; INSUFFICIENT -> underpowered.> XS-solo stays the deploy core
unless BUILD clears cleanly AND is additive.

## Reproduce
`make buibui-xsrev-audit`
```

- [ ] **Step 4: Lint the doc**

Run: `npx markdownlint-cli2 "docs/audits/2026-07-24-p3-xs-reversal-sleeve.md"`
Expected: `0 issues`.

- [ ] **Step 5: Update the SoT + MEMORY**

In `project_todo_master.md`, add a Closed-section row (or a hypothesis-table verdict) recording the reversal verdict with its one-line result. In `MEMORY.md`, update the Current-State "Last session" line. (These are memory files, edited directly — not part of the git commit below.)

- [ ] **Step 6: Commit the verdict doc**

```bash
git add docs/audits/2026-07-24-p3-xs-reversal-sleeve.md
git commit -m "docs(xsrev): reversal sleeve verdict — <BUILD|SHELF|REDUNDANT>

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

- [ ] **Step 7: Final DoD confirmation**

Run: `make lint-py && make typecheck && make test && make test-regression`
Expected: all green; goldens unmoved. State each result plainly in the PR summary.

---

## Self-Review (completed at write time)

- **Spec coverage:** §2 goal/gate → Tasks 7+9; §4 signal → Task 3; §5 architecture (injectable forecast + `xsrev/`) → Tasks 1–6; §7 costs/cost-sweep → Task 7; §8 OI descriptive arm → Task 8; §9 causality invariant → Tasks 3+4 perturbation tests; §10 DoD/byte-identical → Task 1 equivalence test + every task's gate; §11 deliverables (package, tool, target, verdict) → Tasks 2–9. All covered.
- **Placeholder scan:** the only `<...>` placeholders are in the Task-9 verdict-doc template, which is filled from live audit output at execution time by design — not code placeholders.
- **Type consistency:** `run_xsrev_backtest`, `reversal_forecast_matrix`, `ReversalConfig`, `replay_xsrev(_trials)`, `build_xsrev_report_row`, `crowding_forecast_matrix`, `load_daily_open_interest` names + signatures are consistent across the tasks that define and consume them; `forecasts=` kwarg name is identical in Tasks 1, 4, 8.
