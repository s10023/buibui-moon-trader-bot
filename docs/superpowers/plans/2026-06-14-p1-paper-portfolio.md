# P1 Paper Portfolio Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replay the de-biased `signal_alert_outcomes` ledger through a Carver two-layer sizing model into an overlapping-position paper portfolio, and produce the system's first risk-adjusted numbers (Sharpe / Sortino / max-DD / attribution) under policy #0 (today's exits).

**Architecture:** A new pure-lib `portfolio/` package (DI, typed, tested — mirrors `analytics/exits/`). `sizing.py` holds the frozen `SizingConfig` + pure sizing math; `book.py` runs a single causal forward pass over the entry-ordered ledger, marking open positions to a daily MTM equity curve (1d closes) on **two bases in parallel** — fixed-notional (the headline) and compounding (what the vol-governor's feedback reads); `metrics.py` computes risk-adjusted stats on a daily curve; `replay.py` glues the DuckDB ledger + 1d OHLCV + regime labels into the book. A thin `cli/portfolio.py` wrapper + `make buibui-portfolio-replay` run it read-only over `analytics.db`. No live daemon, no detectors, no schema changes, no golden movement.

**Tech Stack:** Python 3.11+, DuckDB (read-only ledger + OHLCV), pandas, numpy. Poetry. ruff + mypy strict + pytest. Tests use `duckdb.connect(":memory:")` with `init_schema` + `upsert_signal_outcome`, mirroring `tests/test_mfe_mae.py`.

**Resolved design decisions** (from `docs/redesign/2026-06-05-p1-sizing-spec.md` §10, resolved 2026-06-14):

- Headline Sharpe = **fixed-notional / constant-R** curve; compounding curve reported alongside; both keep governor + caps active.
- Sequenced build: this branch ships **P1 portfolio only** under policy #0; exit-policy replay is a sibling follow-up branch.
- Static majors cluster `{BTCUSDT, ETHUSDT, SOLUSDT}`; `g_location` = `g_conviction` = 1.0; `g_regime` = high_vol→0.5 else 1.0.
- **Vol governor is causal** — reads trailing `vol_window_days` realized vol of the **compounding** MTM curve ending strictly before each entry.
- **Portfolio vol from a daily MTM curve** — open positions marked at the symbol's **1d close**; same-day-resolving trades bank realized R on their exit day (no prior mark). Annualize on **√365**.
- **Cap breach → scale-down-to-fit**, with a skip floor (capped `r_eff` below `skip_floor_frac × r_base` → skip).
- **No OOS/DSR gate** here (P1 fits zero parameters — pure replay at fixed defaults). Known caveat to surface in the results doc: `outcome_r` mixes pre/post-PR-3 cost rows.
- **Regime coarsening (documented):** the P1 sizing regime uses the symbol's **1d** regime at entry (a macro vol state for sizing), not the per-tf live-gate regime. Same 1d OHLCV already loaded for marking.

**Data source:** `signal_alert_outcomes`, resolved rows only (`outcome IN ('win','loss','expired')` AND `outcome_r IS NOT NULL`). Entry anchor `candle_ts_ms`; exit `outcome_filled_at_ms`; realized R `outcome_r`; `entry_price`, `sl_price`, `direction`, `symbol`, `tf`, `strategy`, `signal_id`.

**Branch:** create `feat/p1-paper-portfolio` first (ask the user before branching, per their branch-first preference). `gh`/git on `s10023`.

---

## File Structure

| File | Responsibility |
| --- | --- |
| `portfolio/__init__.py` | Package docstring + eager re-exports (`SizingConfig`, `PaperBook`, `replay_ledger`, metrics fns). |
| `portfolio/sizing.py` | `SizingConfig` (frozen) + `from_toml` + pure sizing math (governor, regime mult, caps, cluster). No I/O. |
| `portfolio/book.py` | `LedgerTrade`, `SizedTrade`, `BookResult`, `PaperBook` — the causal forward pass + dual-basis daily MTM curves. No I/O. |
| `portfolio/metrics.py` | Pure metrics on a daily curve / sized trades (sharpe, sortino, max_drawdown, calmar, annual_return/vol, exposure, turnover, attribution). |
| `portfolio/replay.py` | `replay_ledger(conn, cfg) -> BookResult` — reads ledger + 1d OHLCV + regime labels, drives `PaperBook`. The only module touching DuckDB. |
| `cli/portfolio.py` | Thin wrapper: `run_portfolio_replay(args)` (open read-only conn, load cfg, replay, print report) + `add_portfolio_subparser`. |
| `cli/main.py` | Register the new subparser (1-line edit). |
| `Makefile` | `buibui-portfolio-replay` target. |
| `tests/test_portfolio_sizing.py` | SizingConfig defaults/from_toml + sizing math. |
| `tests/test_portfolio_book.py` | Caps, cluster cap, skip floor, same-day banking, multi-day MTM marking, causal governor, high_vol halving. |
| `tests/test_portfolio_metrics.py` | Metrics on known curves. |
| `tests/test_portfolio_replay.py` | End-to-end over a seeded in-memory DB. |

---

## Task 1: Package skeleton + `SizingConfig`

**Files:**

- Create: `portfolio/__init__.py`
- Create: `portfolio/sizing.py`
- Test: `tests/test_portfolio_sizing.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_portfolio_sizing.py
"""Tests for portfolio.sizing — SizingConfig defaults/from_toml + pure sizing math."""

import math

import pytest

from portfolio.sizing import SizingConfig


def test_sizing_config_defaults() -> None:
    cfg = SizingConfig()
    assert cfg.capital == 10_000.0
    assert cfg.r_base == pytest.approx(0.0025)
    assert cfg.vol_target_annual == pytest.approx(0.20)
    assert cfg.vol_window_days == 30
    assert cfg.g_vol_min == 0.5 and cfg.g_vol_max == 1.5
    assert cfg.r_open_max == pytest.approx(0.02)
    assert cfg.r_cluster_max == pytest.approx(0.01)
    assert cfg.high_vol_risk_mult == 0.5
    assert cfg.apply_high_vol_halving is True
    assert cfg.annualization_days == pytest.approx(365.0)
    assert ("BTCUSDT", "ETHUSDT", "SOLUSDT") in cfg.clusters


def test_sizing_config_from_toml_overrides(tmp_path) -> None:
    p = tmp_path / "p.toml"
    p.write_text(
        "[portfolio]\n"
        "capital = 25000\n"
        "r_base = 0.005\n"
        "vol_target_annual = 0.15\n"
        "clusters = [[\"BTCUSDT\", \"ETHUSDT\"]]\n"
    )
    cfg = SizingConfig.from_toml(p)
    assert cfg.capital == 25_000.0
    assert cfg.r_base == pytest.approx(0.005)
    assert cfg.vol_target_annual == pytest.approx(0.15)
    assert cfg.clusters == (("BTCUSDT", "ETHUSDT"),)
    # unspecified keys keep defaults
    assert cfg.r_open_max == pytest.approx(0.02)


def test_sizing_config_from_toml_missing_block_is_defaults(tmp_path) -> None:
    p = tmp_path / "empty.toml"
    p.write_text("[other]\nx = 1\n")
    cfg = SizingConfig.from_toml(p)
    assert cfg == SizingConfig()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_portfolio_sizing.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'portfolio'`

- [ ] **Step 3: Write minimal implementation**

```python
# portfolio/__init__.py
"""Paper-portfolio sizing + replay (P1 spec 2026-06-05).

Replays the de-biased `signal_alert_outcomes` ledger through a Carver
two-layer sizing model into an overlapping-position paper book, producing the
system's first risk-adjusted numbers (Sharpe / Sortino / max-DD / attribution)
under policy #0 (today's exits). Pure libs over a DuckDB conn; no live risk.
"""

from portfolio.sizing import SizingConfig

__all__ = ["SizingConfig"]
```

```python
# portfolio/sizing.py
"""Two-layer position sizing (P1 spec §2) — pure math, no I/O.

Layer A: per-trade risk unit = (r_eff × equity) / |entry − stop|.
Layer B: r_eff = r_base × g_vol × g_regime × g_location × g_conviction,
then clipped by concurrent-risk and majors-cluster caps.
"""

from __future__ import annotations

import math
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

_MAJORS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")


@dataclass(frozen=True)
class SizingConfig:
    capital: float = 10_000.0
    r_base: float = 0.0025
    vol_target_annual: float = 0.20
    vol_window_days: int = 30
    g_vol_min: float = 0.5
    g_vol_max: float = 1.5
    r_open_max: float = 0.02
    r_cluster_max: float = 0.01
    high_vol_risk_mult: float = 0.5
    apply_high_vol_halving: bool = True
    skip_floor_frac: float = 0.1
    annualization_days: float = 365.0
    clusters: tuple[tuple[str, ...], ...] = (_MAJORS,)

    @classmethod
    def from_toml(cls, path: str | Path) -> SizingConfig:
        """Build a config from a TOML file's optional `[portfolio]` table.

        Missing keys keep dataclass defaults; `clusters` accepts a TOML array
        of arrays. An absent `[portfolio]` block yields plain defaults.
        """
        with open(Path(path), "rb") as f:
            data: dict[str, Any] = tomllib.load(f)
        block = data.get("portfolio", {})
        if not isinstance(block, dict):
            raise ValueError("[portfolio] must be a TOML table")
        cfg = cls()
        kwargs: dict[str, Any] = {}
        for field_name in (
            "capital", "r_base", "vol_target_annual", "vol_window_days",
            "g_vol_min", "g_vol_max", "r_open_max", "r_cluster_max",
            "high_vol_risk_mult", "apply_high_vol_halving", "skip_floor_frac",
            "annualization_days",
        ):
            if field_name in block:
                kwargs[field_name] = block[field_name]
        if "clusters" in block:
            kwargs["clusters"] = tuple(tuple(str(s) for s in c) for c in block["clusters"])
        return replace(cfg, **kwargs)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_portfolio_sizing.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add portfolio/__init__.py portfolio/sizing.py tests/test_portfolio_sizing.py
git commit -m "feat(portfolio): SizingConfig frozen dataclass + from_toml loader"
```

---

## Task 2: Sizing pure functions

**Files:**

- Modify: `portfolio/sizing.py` (append functions)
- Test: `tests/test_portfolio_sizing.py` (append)

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_portfolio_sizing.py  (append)
from portfolio.sizing import (
    apply_caps,
    cluster_of,
    effective_risk_fraction,
    position_size,
    regime_multiplier,
    risk_per_unit,
    vol_governor,
)


def test_risk_per_unit_and_position_size() -> None:
    assert risk_per_unit(100.0, 95.0) == pytest.approx(5.0)
    assert risk_per_unit(95.0, 100.0) == pytest.approx(5.0)
    # risk_capital 25 at 5/unit => 5 units
    assert position_size(25.0, 100.0, 95.0) == pytest.approx(5.0)
    assert position_size(25.0, 100.0, 100.0) == 0.0  # zero risk => no position


def test_vol_governor_clamps_and_cold_start() -> None:
    cfg = SizingConfig()
    # realized vol == target => g_vol 1.0
    assert vol_governor(0.20, cfg) == pytest.approx(1.0)
    # hot book (realized 0.40 vs target 0.20) => shrink, clamped at floor 0.5
    assert vol_governor(0.40, cfg) == pytest.approx(0.5)
    # cold book (realized 0.05) => expand, clamped at ceiling 1.5
    assert vol_governor(0.05, cfg) == pytest.approx(1.5)
    # undefined / non-positive vol => neutral 1.0 (cold start)
    assert vol_governor(0.0, cfg) == pytest.approx(1.0)
    assert vol_governor(float("nan"), cfg) == pytest.approx(1.0)


def test_regime_multiplier() -> None:
    cfg = SizingConfig()
    assert regime_multiplier("high_vol", cfg) == pytest.approx(0.5)
    assert regime_multiplier("trend", cfg) == pytest.approx(1.0)
    assert regime_multiplier(None, cfg) == pytest.approx(1.0)
    off = SizingConfig(apply_high_vol_halving=False)
    assert regime_multiplier("high_vol", off) == pytest.approx(1.0)


def test_effective_risk_fraction() -> None:
    cfg = SizingConfig()  # r_base 0.0025
    assert effective_risk_fraction(cfg, g_vol=1.0, g_regime=1.0) == pytest.approx(0.0025)
    assert effective_risk_fraction(cfg, g_vol=1.5, g_regime=0.5) == pytest.approx(
        0.0025 * 1.5 * 0.5
    )


def test_cluster_of() -> None:
    cfg = SizingConfig()
    # majors share one cluster id; non-majors get their own singleton
    assert cluster_of("BTCUSDT", cfg) == cluster_of("ETHUSDT", cfg)
    assert cluster_of("DOGEUSDT", cfg) == "DOGEUSDT"
    assert cluster_of("BTCUSDT", cfg) != "BTCUSDT"


def test_apply_caps_scales_down_to_fit() -> None:
    cfg = SizingConfig()  # r_open_max 0.02, r_cluster_max 0.01
    # plenty of headroom => unchanged
    assert apply_caps(
        0.0025, symbol="BTCUSDT", open_risk_total=0.0, open_risk_cluster=0.0, cfg=cfg
    ) == pytest.approx(0.0025)
    # cluster nearly full => scaled to remaining headroom
    assert apply_caps(
        0.0025, symbol="BTCUSDT", open_risk_total=0.005, open_risk_cluster=0.009, cfg=cfg
    ) == pytest.approx(0.001)
    # total cap binds before cluster
    assert apply_caps(
        0.0025, symbol="DOGEUSDT", open_risk_total=0.0195, open_risk_cluster=0.0, cfg=cfg
    ) == pytest.approx(0.0005)


def test_apply_caps_skip_floor() -> None:
    cfg = SizingConfig()  # skip_floor_frac 0.1 => floor 0.00025
    # headroom below floor => skip (0.0)
    assert apply_caps(
        0.0025, symbol="BTCUSDT", open_risk_total=0.0199, open_risk_cluster=0.0, cfg=cfg
    ) == 0.0
```

- [ ] **Step 2: Run to verify it fails**

Run: `poetry run pytest tests/test_portfolio_sizing.py -v`
Expected: FAIL — `ImportError: cannot import name 'risk_per_unit'`

- [ ] **Step 3: Write minimal implementation**

```python
# portfolio/sizing.py  (append below SizingConfig)


def risk_per_unit(entry: float, stop: float) -> float:
    """Per-unit risk in price terms = |entry − stop|."""
    return abs(entry - stop)


def position_size(risk_capital: float, entry: float, stop: float) -> float:
    """Units = risk_capital / |entry − stop| (0.0 when risk is undefined)."""
    rpu = risk_per_unit(entry, stop)
    return risk_capital / rpu if rpu > 0.0 else 0.0


def vol_governor(realized_vol_annual: float, cfg: SizingConfig) -> float:
    """g_vol = clamp(target / realized, [g_vol_min, g_vol_max]).

    Non-finite or non-positive realized vol (cold start) → neutral 1.0.
    """
    if not math.isfinite(realized_vol_annual) or realized_vol_annual <= 0.0:
        return 1.0
    g = cfg.vol_target_annual / realized_vol_annual
    return float(min(max(g, cfg.g_vol_min), cfg.g_vol_max))


def regime_multiplier(regime_label: str | None, cfg: SizingConfig) -> float:
    """high_vol → high_vol_risk_mult (when enabled); everything else → 1.0."""
    if cfg.apply_high_vol_halving and regime_label == "high_vol":
        return cfg.high_vol_risk_mult
    return 1.0


def effective_risk_fraction(
    cfg: SizingConfig,
    *,
    g_vol: float,
    g_regime: float,
    g_location: float = 1.0,
    g_conviction: float = 1.0,
) -> float:
    """r_eff = r_base × g_vol × g_regime × g_location × g_conviction (pre-cap)."""
    return cfg.r_base * g_vol * g_regime * g_location * g_conviction


def cluster_of(symbol: str, cfg: SizingConfig) -> str:
    """Cluster id for a symbol: the joined members for a configured cluster it
    belongs to, else the symbol itself (its own singleton cluster)."""
    for members in cfg.clusters:
        if symbol in members:
            return "|".join(members)
    return symbol


def apply_caps(
    r_eff: float,
    *,
    symbol: str,
    open_risk_total: float,
    open_risk_cluster: float,
    cfg: SizingConfig,
) -> float:
    """Clip r_eff by concurrent-risk + cluster headroom; scale-down-to-fit.

    Returns the admissible r_eff, or 0.0 when the remaining headroom is below
    `skip_floor_frac × r_base` (skip rather than open a dust position).
    """
    headroom_total = max(cfg.r_open_max - open_risk_total, 0.0)
    headroom_cluster = max(cfg.r_cluster_max - open_risk_cluster, 0.0)
    allowed = min(r_eff, headroom_total, headroom_cluster)
    if allowed < cfg.skip_floor_frac * cfg.r_base:
        return 0.0
    return allowed
```

- [ ] **Step 4: Run to verify it passes**

Run: `poetry run pytest tests/test_portfolio_sizing.py -v`
Expected: PASS (all)

- [ ] **Step 5: Lint, typecheck, commit**

```bash
make lint-py typecheck
git add portfolio/sizing.py tests/test_portfolio_sizing.py
git commit -m "feat(portfolio): pure sizing math — governor, regime, caps, cluster"
```

---

## Task 3: Metrics

**Files:**

- Create: `portfolio/metrics.py`
- Test: `tests/test_portfolio_metrics.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_portfolio_metrics.py
"""Tests for portfolio.metrics — risk-adjusted stats on a daily curve."""

import math

import numpy as np
import pandas as pd
import pytest

from portfolio.metrics import (
    annual_return,
    annual_vol,
    calmar,
    max_drawdown,
    sharpe,
    sortino,
)


def _curve(values: list[float]) -> pd.Series:
    idx = pd.date_range("2026-01-01", periods=len(values), freq="D", tz="UTC")
    return pd.Series(values, index=idx)


def test_sharpe_positive_drift() -> None:
    # steady +0.1%/day, zero variance theoretically -> guard returns 0.0
    flat = _curve([100.0 * (1.001 ** i) for i in range(50)])
    # constant geometric return has ~0 stdev of pct change -> sharpe guard 0.0
    assert sharpe(flat) == pytest.approx(0.0, abs=1e-6)


def test_sharpe_known_value() -> None:
    rng = np.random.default_rng(0)
    rets = rng.normal(0.001, 0.01, 365)
    curve = _curve(list(100.0 * np.cumprod(1.0 + rets)))
    s = sharpe(curve)
    # mean/std * sqrt(365); positive, finite, in a sane band
    assert math.isfinite(s) and s > 0.0


def test_max_drawdown() -> None:
    curve = _curve([100, 120, 90, 110, 80])
    # worst peak->trough: 120 -> 80 = -33.3%
    assert max_drawdown(curve) == pytest.approx(-1.0 / 3.0, rel=1e-3)


def test_sortino_only_penalizes_downside() -> None:
    curve = _curve([100, 101, 100, 102, 101, 103])
    assert math.isfinite(sortino(curve))


def test_annual_return_and_vol_and_calmar() -> None:
    curve = _curve([100, 110, 121])  # +10%/period compounding
    ar = annual_return(curve)
    assert ar > 0.0
    assert annual_vol(curve) >= 0.0
    assert math.isfinite(calmar(curve))


def test_flat_curve_is_zero_not_nan() -> None:
    curve = _curve([100.0] * 30)
    assert sharpe(curve) == 0.0
    assert sortino(curve) == 0.0
    assert max_drawdown(curve) == 0.0
```

- [ ] **Step 2: Run to verify it fails**

Run: `poetry run pytest tests/test_portfolio_metrics.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'portfolio.metrics'`

- [ ] **Step 3: Write minimal implementation**

```python
# portfolio/metrics.py
"""Risk-adjusted metrics on a daily equity curve + sized-trade attribution.

Pure functions over a pandas daily curve (Series indexed by UTC day) and the
`SizedTrade` list from `portfolio.book`. Annualization defaults to 365 days
(crypto trades every day). Degenerate inputs (flat / single-point curves)
return 0.0 rather than NaN.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from portfolio.book import BookResult, SizedTrade

_PPY = 365.0


def daily_returns(curve: pd.Series) -> pd.Series:
    return curve.pct_change().dropna()


def sharpe(curve: pd.Series, periods_per_year: float = _PPY) -> float:
    r = daily_returns(curve)
    sd = float(r.std(ddof=1)) if len(r) > 1 else 0.0
    if sd <= 0.0:
        return 0.0
    return float(r.mean() / sd * math.sqrt(periods_per_year))


def sortino(curve: pd.Series, periods_per_year: float = _PPY) -> float:
    r = daily_returns(curve)
    if len(r) < 2:
        return 0.0
    downside = r[r < 0.0]
    dd = float(math.sqrt((downside**2).mean())) if len(downside) > 0 else 0.0
    if dd <= 0.0:
        return 0.0
    return float(r.mean() / dd * math.sqrt(periods_per_year))


def max_drawdown(curve: pd.Series) -> float:
    """Worst peak-to-trough return (≤ 0.0)."""
    if len(curve) < 2:
        return 0.0
    roll_max = curve.cummax()
    dd = (curve - roll_max) / roll_max
    return float(dd.min())


def annual_return(curve: pd.Series, periods_per_year: float = _PPY) -> float:
    if len(curve) < 2 or curve.iloc[0] <= 0.0:
        return 0.0
    total = curve.iloc[-1] / curve.iloc[0]
    if total <= 0.0:
        return -1.0
    return float(total ** (periods_per_year / len(curve)) - 1.0)


def annual_vol(curve: pd.Series, periods_per_year: float = _PPY) -> float:
    r = daily_returns(curve)
    sd = float(r.std(ddof=1)) if len(r) > 1 else 0.0
    return sd * math.sqrt(periods_per_year)


def calmar(curve: pd.Series, periods_per_year: float = _PPY) -> float:
    mdd = abs(max_drawdown(curve))
    if mdd <= 0.0:
        return 0.0
    return annual_return(curve, periods_per_year) / mdd


def avg_exposure(result: BookResult) -> float:
    """Mean daily gross open-risk fraction across the curve."""
    n = len(result.daily_index)
    if n == 0:
        return 0.0
    open_risk = np.zeros(n)
    for t in result.sized:
        open_risk[t.entry_idx : max(t.exit_idx, t.entry_idx + 1)] += t.r_eff
    return float(open_risk.mean())


def risk_turnover(result: BookResult) -> float:
    """Σ risk-capital deployed ÷ mean fixed-basis equity (dimensionless)."""
    equity = result.capital + result.pnl_fixed
    mean_eq = float(equity.mean()) if len(equity) else result.capital
    deployed = sum(t.rc_fixed for t in result.sized)
    return deployed / mean_eq if mean_eq > 0.0 else 0.0


def attribution(
    sized: list[SizedTrade],
    by: tuple[str, ...] = ("strategy", "tf", "direction"),
) -> pd.DataFrame:
    """Per-bucket realized P&L (fixed basis) + trade count + total/avg R."""
    if not sized:
        return pd.DataFrame()
    rows = [
        {
            "strategy": t.strategy,
            "tf": t.tf,
            "direction": t.direction,
            "pnl_fixed": t.pnl_fixed,
            "realized_r": t.realized_r,
        }
        for t in sized
    ]
    df = pd.DataFrame(rows)
    agg = (
        df.groupby(list(by))
        .agg(
            n=("pnl_fixed", "size"),
            total_pnl=("pnl_fixed", "sum"),
            total_r=("realized_r", "sum"),
            avg_r=("realized_r", "mean"),
        )
        .reset_index()
        .sort_values("total_pnl", ascending=False)
        .reset_index(drop=True)
    )
    return agg
```

> Note: `metrics.py` imports `BookResult`/`SizedTrade` from `portfolio.book`, built in Task 4. Write Task 4 before running the full metrics suite; the pure-curve tests above pass independently, but the module import requires `portfolio.book` to exist. If executing strictly in order, temporarily stub the import — or reorder: do Task 4's dataclasses first. **Recommended:** implement Task 4 `book.py` dataclasses (`SizedTrade`, `BookResult`) before this module so the import resolves. The plan keeps metrics here for narrative grouping; the executor may interleave.

- [ ] **Step 4: Run to verify it passes**

Run: `poetry run pytest tests/test_portfolio_metrics.py -v`
Expected: PASS (after `portfolio.book` dataclasses exist)

- [ ] **Step 5: Lint, typecheck, commit**

```bash
make lint-py typecheck
git add portfolio/metrics.py tests/test_portfolio_metrics.py
git commit -m "feat(portfolio): risk-adjusted metrics + attribution"
```

---

## Task 4: `PaperBook` — caps, concurrency, dual-basis MTM curves (governor OFF)

**Files:**

- Create: `portfolio/book.py`
- Test: `tests/test_portfolio_book.py`

This task builds the forward pass with `g_vol`/`g_regime` hardcoded to neutral (1.0) so the caps/concurrency/marking mechanics are tested in isolation. Task 5 wires the real modulators in.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_portfolio_book.py
"""Tests for portfolio.book — caps, concurrency, dual-basis daily MTM curves."""

import numpy as np
import pytest

from portfolio.book import LedgerTrade, PaperBook
from portfolio.sizing import SizingConfig

_DAY = 86_400_000


def _grid(n_days: int) -> np.ndarray:
    return np.arange(0, n_days * _DAY, _DAY, dtype=np.int64)


def _flat_close(n_days: int, price: float) -> np.ndarray:
    return np.full(n_days, price, dtype=np.float64)


def test_same_day_trade_banks_realized_on_exit_day() -> None:
    cfg = SizingConfig()  # r_base 0.0025, capital 10_000
    grid = _grid(5)
    close = {"BTCUSDT": _flat_close(5, 100.0)}
    # entry and exit both on day 2; win +2R
    trades = [
        LedgerTrade(
            signal_id="s1", symbol="BTCUSDT", tf="15m", strategy="fvg",
            direction="long", entry_ts_ms=2 * _DAY + 1, exit_ts_ms=2 * _DAY + 5,
            entry_price=100.0, sl_price=95.0, outcome="win", realized_r=2.0,
        )
    ]
    book = PaperBook(cfg, grid, close, regime_by_signal=None)
    res = book.run(trades)
    # risk_capital_fixed = 0.0025 * 10_000 = 25; pnl = 25 * 2 = 50, banked day 2+
    assert res.pnl_fixed[1] == pytest.approx(0.0)
    assert res.pnl_fixed[2] == pytest.approx(50.0)
    assert res.pnl_fixed[4] == pytest.approx(50.0)
    assert len(res.sized) == 1 and not res.skipped


def test_multi_day_long_marks_to_market() -> None:
    cfg = SizingConfig()
    grid = _grid(6)
    # price rises 100 -> 110 over the hold; risk_per_unit = 5
    close = {"BTCUSDT": np.array([100, 100, 105, 110, 110, 110], dtype=np.float64)}
    trades = [
        LedgerTrade(
            signal_id="s1", symbol="BTCUSDT", tf="1h", strategy="bos",
            direction="long", entry_ts_ms=1 * _DAY + 1, exit_ts_ms=3 * _DAY + 1,
            entry_price=100.0, sl_price=95.0, outcome="win", realized_r=2.0,
        )
    ]
    res = PaperBook(cfg, grid, close, regime_by_signal=None).run(trades)
    rc = 25.0  # 0.0025 * 10_000
    # day1 mark: (100-100)/5 = 0R -> 0; day2 mark: (105-100)/5 = +1R -> +25
    assert res.pnl_fixed[1] == pytest.approx(0.0)
    assert res.pnl_fixed[2] == pytest.approx(rc * 1.0)
    # exit day 3 snaps to realized +2R -> +50, held thereafter
    assert res.pnl_fixed[3] == pytest.approx(rc * 2.0)
    assert res.pnl_fixed[5] == pytest.approx(rc * 2.0)


def test_short_marks_invert_sign() -> None:
    cfg = SizingConfig()
    grid = _grid(4)
    close = {"BTCUSDT": np.array([100, 100, 95, 90], dtype=np.float64)}
    trades = [
        LedgerTrade(
            signal_id="s1", symbol="BTCUSDT", tf="1h", strategy="bos",
            direction="short", entry_ts_ms=1 * _DAY + 1, exit_ts_ms=3 * _DAY + 1,
            entry_price=100.0, sl_price=105.0, outcome="win", realized_r=2.0,
        )
    ]
    res = PaperBook(cfg, grid, close, regime_by_signal=None).run(trades)
    # short: day2 (100-95)/5 = +1R favorable -> +25
    assert res.pnl_fixed[2] == pytest.approx(25.0)


def test_cluster_cap_scales_down_third_major() -> None:
    cfg = SizingConfig()  # r_cluster_max 0.01; three majors at 0.0025 each = 0.0075 < 0.01
    grid = _grid(3)
    close = {s: _flat_close(3, 100.0) for s in ("BTCUSDT", "ETHUSDT", "SOLUSDT")}
    # four concurrent majors: 4th must be capped (0.0075 used, headroom 0.0025)
    trades = []
    for i, sym in enumerate(("BTCUSDT", "ETHUSDT", "SOLUSDT", "BTCUSDT")):
        trades.append(
            LedgerTrade(
                signal_id=f"s{i}", symbol=sym, tf="1h", strategy="bos",
                direction="long", entry_ts_ms=1 * _DAY + i, exit_ts_ms=2 * _DAY,
                entry_price=100.0, sl_price=95.0, outcome="loss", realized_r=-1.0,
            )
        )
    res = PaperBook(cfg, grid, close, regime_by_signal=None).run(trades)
    # first three at full r_eff, fourth scaled to remaining cluster headroom 0.0025 -> 0.0025
    # (cluster headroom exactly equals r_base here, so it stays full but caps still applied)
    assert len(res.sized) == 4
    assert res.sized[3].r_eff == pytest.approx(0.0025)


def test_open_risk_cap_skips_when_headroom_below_floor() -> None:
    cfg = SizingConfig(clusters=())  # no clusters -> only the 2% total cap binds
    grid = _grid(3)
    close = {f"C{i}USDT": _flat_close(3, 100.0) for i in range(10)}
    # 8 concurrent at 0.0025 = 0.02 -> total cap full; 9th has 0 headroom -> skip
    trades = []
    for i in range(9):
        trades.append(
            LedgerTrade(
                signal_id=f"s{i}", symbol=f"C{i}USDT", tf="1h", strategy="bos",
                direction="long", entry_ts_ms=1 * _DAY + i, exit_ts_ms=2 * _DAY,
                entry_price=100.0, sl_price=95.0, outcome="loss", realized_r=-1.0,
            )
        )
    res = PaperBook(cfg, grid, close, regime_by_signal=None).run(trades)
    assert len(res.sized) == 8
    assert res.skipped and res.skipped[-1][0] == "s8"


def test_zero_risk_trade_skipped() -> None:
    cfg = SizingConfig()
    grid = _grid(3)
    close = {"BTCUSDT": _flat_close(3, 100.0)}
    trades = [
        LedgerTrade(
            signal_id="s1", symbol="BTCUSDT", tf="1h", strategy="bos",
            direction="long", entry_ts_ms=1 * _DAY, exit_ts_ms=2 * _DAY,
            entry_price=100.0, sl_price=100.0, outcome="loss", realized_r=-1.0,
        )
    ]
    res = PaperBook(cfg, grid, close, regime_by_signal=None).run(trades)
    assert not res.sized and res.skipped[0] == ("s1", "zero_risk")


def test_compounding_curve_diverges_from_fixed_after_pnl() -> None:
    cfg = SizingConfig()
    grid = _grid(4)
    close = {"BTCUSDT": _flat_close(4, 100.0)}
    # two sequential wins; second sizes off grown equity on the comp basis
    trades = [
        LedgerTrade("s1", "BTCUSDT", "15m", "fvg", "long", 0 * _DAY + 1, 0 * _DAY + 2,
                    100.0, 95.0, "win", 4.0),
        LedgerTrade("s2", "BTCUSDT", "15m", "fvg", "long", 2 * _DAY + 1, 2 * _DAY + 2,
                    100.0, 95.0, "win", 4.0),
    ]
    res = PaperBook(cfg, grid, close, regime_by_signal=None).run(trades)
    # comp 2nd trade risk_capital > fixed because equity grew after trade 1
    assert res.pnl_comp[-1] > res.pnl_fixed[-1]
```

> Note: `LedgerTrade` is positional-friendly (last test uses positional args) — keep its field order: signal_id, symbol, tf, strategy, direction, entry_ts_ms, exit_ts_ms, entry_price, sl_price, outcome, realized_r.

- [ ] **Step 2: Run to verify it fails**

Run: `poetry run pytest tests/test_portfolio_book.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'portfolio.book'`

- [ ] **Step 3: Write minimal implementation**

```python
# portfolio/book.py
"""Overlapping-position paper book — the causal forward pass (P1 spec §4).

Processes resolved ledger trades in entry-time order, applying concurrent +
cluster risk caps in real time and marking open positions to a daily MTM
equity curve on TWO bases in parallel:

  - fixed-notional (risk-% of the initial `capital`) — the headline curve.
  - compounding (risk-% of current equity) — what the vol-governor reads.

Same-day-resolving trades bank realized R on their exit day (no prior mark).
Pure: no DB / network / clock. Marking uses caller-supplied 1d close series
aligned to `daily_index`. The vol governor + regime modulator are wired in by
`PaperBook._g_vol` / `regime_by_signal`; this file's defaults are neutral so
the caps/marking mechanics test in isolation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from portfolio.sizing import (
    SizingConfig,
    apply_caps,
    cluster_of,
    effective_risk_fraction,
    regime_multiplier,
    risk_per_unit,
    vol_governor,
)


@dataclass(frozen=True)
class LedgerTrade:
    signal_id: str
    symbol: str
    tf: str
    strategy: str
    direction: str
    entry_ts_ms: int
    exit_ts_ms: int
    entry_price: float
    sl_price: float
    outcome: str
    realized_r: float


@dataclass(frozen=True)
class SizedTrade:
    signal_id: str
    symbol: str
    tf: str
    strategy: str
    direction: str
    entry_idx: int
    exit_idx: int
    r_eff: float
    g_vol: float
    g_regime: float
    rc_fixed: float
    rc_comp: float
    pnl_fixed: float
    pnl_comp: float
    realized_r: float
    regime: str | None


@dataclass(frozen=True)
class BookResult:
    daily_index: np.ndarray
    capital: float
    pnl_fixed: np.ndarray
    pnl_comp: np.ndarray
    sized: list[SizedTrade]
    skipped: list[tuple[str, str]]


class PaperBook:
    """Replays sized trades onto dual-basis daily MTM curves with live caps."""

    def __init__(
        self,
        cfg: SizingConfig,
        daily_index: np.ndarray,
        close_by_symbol: dict[str, np.ndarray],
        regime_by_signal: dict[str, str] | None = None,
    ) -> None:
        self.cfg = cfg
        self.daily_index = daily_index
        self.close_by_symbol = close_by_symbol
        self.regime_by_signal = regime_by_signal

    def _g_vol(self, pnl_comp: np.ndarray, entry_idx: int) -> float:
        """Trailing realized vol of the compounding curve, days < entry_idx."""
        lo = max(0, entry_idx - self.cfg.vol_window_days)
        if entry_idx - lo < 2:
            return 1.0
        equity = self.cfg.capital + pnl_comp[lo:entry_idx]
        if np.any(equity[:-1] <= 0.0):
            return 1.0
        rets = np.diff(equity) / equity[:-1]
        if rets.size < 2:
            return 1.0
        sd = float(np.std(rets, ddof=1))
        realized_vol = sd * math.sqrt(self.cfg.annualization_days)
        return vol_governor(realized_vol, self.cfg)

    def run(self, trades: list[LedgerTrade]) -> BookResult:
        n = len(self.daily_index)
        pnl_fixed = np.zeros(n)
        pnl_comp = np.zeros(n)
        open_positions: list[tuple[int, float, str]] = []  # (exit_ts_ms, r_eff, cluster)
        sized: list[SizedTrade] = []
        skipped: list[tuple[str, str]] = []

        for t in sorted(trades, key=lambda x: x.entry_ts_ms):
            entry_idx = int(np.searchsorted(self.daily_index, t.entry_ts_ms, side="right")) - 1
            if entry_idx < 0:
                skipped.append((t.signal_id, "before_grid"))
                continue
            exit_idx = int(np.searchsorted(self.daily_index, t.exit_ts_ms, side="right")) - 1
            exit_idx = max(exit_idx, entry_idx)

            rpu = risk_per_unit(t.entry_price, t.sl_price)
            if rpu <= 0.0:
                skipped.append((t.signal_id, "zero_risk"))
                continue

            open_positions = [p for p in open_positions if p[0] > t.entry_ts_ms]

            g_vol = self._g_vol(pnl_comp, entry_idx)
            regime = None if self.regime_by_signal is None else self.regime_by_signal.get(
                t.signal_id
            )
            g_regime = regime_multiplier(regime, self.cfg)
            r_eff_candidate = effective_risk_fraction(self.cfg, g_vol=g_vol, g_regime=g_regime)

            cluster = cluster_of(t.symbol, self.cfg)
            open_total = sum(p[1] for p in open_positions)
            open_cluster = sum(p[1] for p in open_positions if p[2] == cluster)
            r_eff = apply_caps(
                r_eff_candidate,
                symbol=t.symbol,
                open_risk_total=open_total,
                open_risk_cluster=open_cluster,
                cfg=self.cfg,
            )
            if r_eff <= 0.0:
                skipped.append((t.signal_id, "cap_breach"))
                continue

            comp_equity_at_entry = self.cfg.capital + pnl_comp[entry_idx]
            rc_fixed = r_eff * self.cfg.capital
            rc_comp = r_eff * comp_equity_at_entry
            side = 1.0 if t.direction == "long" else -1.0
            closes = self.close_by_symbol.get(t.symbol)

            for arr, rc in ((pnl_fixed, rc_fixed), (pnl_comp, rc_comp)):
                if exit_idx > entry_idx and closes is not None:
                    seg = closes[entry_idx:exit_idx]
                    unreal = rc * side * (seg - t.entry_price) / rpu
                    arr[entry_idx:exit_idx] += np.nan_to_num(unreal)
                arr[exit_idx:] += rc * t.realized_r

            open_positions.append((t.exit_ts_ms, r_eff, cluster))
            sized.append(
                SizedTrade(
                    signal_id=t.signal_id, symbol=t.symbol, tf=t.tf,
                    strategy=t.strategy, direction=t.direction,
                    entry_idx=entry_idx, exit_idx=exit_idx, r_eff=r_eff,
                    g_vol=g_vol, g_regime=g_regime, rc_fixed=rc_fixed, rc_comp=rc_comp,
                    pnl_fixed=rc_fixed * t.realized_r, pnl_comp=rc_comp * t.realized_r,
                    realized_r=t.realized_r, regime=regime,
                )
            )

        return BookResult(
            daily_index=self.daily_index, capital=self.cfg.capital,
            pnl_fixed=pnl_fixed, pnl_comp=pnl_comp, sized=sized, skipped=skipped,
        )
```

- [ ] **Step 4: Run to verify it passes**

Run: `poetry run pytest tests/test_portfolio_book.py tests/test_portfolio_metrics.py -v`
Expected: PASS (both — metrics import now resolves)

- [ ] **Step 5: Lint, typecheck, commit**

```bash
make lint-py typecheck
git add portfolio/book.py tests/test_portfolio_book.py
git commit -m "feat(portfolio): PaperBook — caps, concurrency, dual-basis MTM curves"
```

---

## Task 5: Wire the causal vol governor + regime into `PaperBook`

The governor logic already lives in `_g_vol` (Task 4) and is called in `run()`. This task adds **tests that exercise it end-to-end** (it was dormant under flat curves) and the regime path, plus a guard fix if any test exposes one. No new production code is expected beyond what Task 4 wrote — this task is the behavioral proof that the governor is causal and the regime halving fires.

**Files:**

- Test: `tests/test_portfolio_book.py` (append)
- Modify (only if a test fails): `portfolio/book.py`

- [ ] **Step 1: Write the failing/な tests**

```python
# tests/test_portfolio_book.py  (append)


def test_governor_shrinks_size_after_volatile_run() -> None:
    # Build a volatile compounding history, then check a late trade is downsized
    cfg = SizingConfig(vol_window_days=20)
    grid = _grid(60)
    close = {"BTCUSDT": _flat_close(60, 100.0)}
    trades = []
    # alternating big win/loss every 2 days for 40 days -> high realized vol
    r = 4.0
    for i in range(20):
        day = i * 2
        r = -r
        trades.append(
            LedgerTrade(f"v{i}", "BTCUSDT", "15m", "fvg", "long",
                        day * _DAY + 1, day * _DAY + 2, 100.0, 95.0,
                        "win" if r > 0 else "loss", r)
        )
    # a calm reference trade very early (low prior vol) and one late (high prior vol)
    res = PaperBook(cfg, grid, close, regime_by_signal=None).run(trades)
    early = next(t for t in res.sized if t.signal_id == "v1")
    late = next(t for t in res.sized if t.signal_id == "v19")
    assert late.g_vol < early.g_vol  # governor reacted to the volatile run
    assert late.r_eff <= early.r_eff


def test_governor_is_causal_only_reads_past() -> None:
    # A single trade has no prior history -> g_vol must be the cold-start 1.0
    cfg = SizingConfig()
    grid = _grid(10)
    close = {"BTCUSDT": _flat_close(10, 100.0)}
    trades = [
        LedgerTrade("s1", "BTCUSDT", "15m", "fvg", "long", 5 * _DAY + 1, 5 * _DAY + 2,
                    100.0, 95.0, "win", 2.0)
    ]
    res = PaperBook(cfg, grid, close, regime_by_signal=None).run(trades)
    assert res.sized[0].g_vol == pytest.approx(1.0)


def test_regime_high_vol_halves_size() -> None:
    cfg = SizingConfig()
    grid = _grid(4)
    close = {"BTCUSDT": _flat_close(4, 100.0)}
    trades = [
        LedgerTrade("s1", "BTCUSDT", "1h", "bos", "long", 1 * _DAY + 1, 2 * _DAY,
                    100.0, 95.0, "loss", -1.0)
    ]
    res = PaperBook(cfg, grid, close, regime_by_signal={"s1": "high_vol"}).run(trades)
    # g_regime 0.5 -> r_eff = 0.0025 * 0.5 = 0.00125, rc_fixed = 12.5
    assert res.sized[0].g_regime == pytest.approx(0.5)
    assert res.sized[0].rc_fixed == pytest.approx(12.5)
```

- [ ] **Step 2: Run to verify behavior**

Run: `poetry run pytest tests/test_portfolio_book.py -v -k "governor or regime"`
Expected: PASS. If `test_governor_shrinks_size_after_volatile_run` fails, inspect whether `_g_vol`'s window indexing is reading future days — fix only the indexing, keep the test.

- [ ] **Step 3: Implementation (only if a test failed)**

No change expected. If the volatile-run test is flaky on the clamp boundary, widen the scenario (more alternations / bigger R) rather than loosening the governor.

- [ ] **Step 4: Run the full book suite**

Run: `poetry run pytest tests/test_portfolio_book.py -v`
Expected: PASS (all)

- [ ] **Step 5: Lint, typecheck, commit**

```bash
make lint-py typecheck
git add tests/test_portfolio_book.py portfolio/book.py
git commit -m "test(portfolio): causal vol-governor + high_vol regime halving"
```

---

## Task 6: `replay_ledger` — DuckDB ledger → 1d OHLCV + regime → PaperBook

**Files:**

- Create: `portfolio/replay.py`
- Modify: `portfolio/__init__.py` (export `replay_ledger`, `PaperBook`, `BookResult`)
- Test: `tests/test_portfolio_replay.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_portfolio_replay.py
"""End-to-end: replay a seeded in-memory ledger through the paper book."""

import duckdb
import numpy as np
import pandas as pd
import pytest

from analytics.store import init_schema, upsert_signal_outcome
from portfolio.replay import replay_ledger
from portfolio.sizing import SizingConfig

_DAY = 86_400_000


def _seed_ohlcv_1d(conn: duckdb.DuckDBPyConnection, symbol: str, n_days: int) -> None:
    df = pd.DataFrame(
        [
            {
                "symbol": symbol, "timeframe": "1d", "open_time": d * _DAY,
                "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0,
                "volume": 1.0, "taker_buy_volume": 0.5,
            }
            for d in range(n_days)
        ]
    )
    conn.register("_o", df)
    conn.execute("INSERT INTO ohlcv SELECT * FROM _o")
    conn.unregister("_o")


def _seed_resolved(
    conn: duckdb.DuckDBPyConnection, *, signal_id: str, symbol: str,
    entry_day: int, exit_day: int, outcome: str, outcome_r: float,
    direction: str = "long", entry: float = 100.0, sl: float = 95.0,
) -> None:
    upsert_signal_outcome(
        conn,
        {
            "signal_id": signal_id, "symbol": symbol, "tf": "15m",
            "strategy": "fvg", "direction": direction,
            "fired_at_ms": entry_day * _DAY, "candle_ts_ms": entry_day * _DAY,
            "entry_price": entry, "sl_price": sl, "tp_price": entry + 10.0,
            "rr_ratio": 2.0, "confidence_at_fire": 3, "tags": "",
            "outcome": outcome, "outcome_r": outcome_r,
            "outcome_filled_at_ms": exit_day * _DAY,
        },
    )


def test_replay_ledger_produces_curves_and_trades() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    _seed_ohlcv_1d(conn, "BTCUSDT", 12)
    _seed_resolved(conn, signal_id="a", symbol="BTCUSDT", entry_day=1, exit_day=1,
                   outcome="win", outcome_r=2.0)
    _seed_resolved(conn, signal_id="b", symbol="BTCUSDT", entry_day=3, exit_day=3,
                   outcome="loss", outcome_r=-1.0)
    cfg = SizingConfig(apply_high_vol_halving=False)  # isolate sizing from regime
    res = replay_ledger(conn, cfg)
    assert len(res.sized) == 2
    # net realized = 25*2 - 25*1 = +25 on the fixed basis by the end
    assert res.pnl_fixed[-1] == pytest.approx(25.0)


def test_replay_skips_unscoreable_and_null_r() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    _seed_ohlcv_1d(conn, "BTCUSDT", 6)
    _seed_resolved(conn, signal_id="ok", symbol="BTCUSDT", entry_day=1, exit_day=1,
                   outcome="win", outcome_r=2.0)
    # NULL outcome_r -> excluded by the query
    upsert_signal_outcome(
        conn,
        {
            "signal_id": "null_r", "symbol": "BTCUSDT", "tf": "15m",
            "strategy": "fvg", "direction": "long", "fired_at_ms": 2 * _DAY,
            "candle_ts_ms": 2 * _DAY, "entry_price": 100.0, "sl_price": 95.0,
            "tp_price": 110.0, "rr_ratio": 2.0, "confidence_at_fire": 3, "tags": "",
            "outcome": "open", "outcome_r": None, "outcome_filled_at_ms": None,
        },
    )
    res = replay_ledger(conn, SizingConfig(apply_high_vol_halving=False))
    assert [t.signal_id for t in res.sized] == ["ok"]


def test_replay_empty_ledger_returns_empty_result() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    res = replay_ledger(conn, SizingConfig())
    assert res.sized == [] and len(res.daily_index) == 0
```

- [ ] **Step 2: Run to verify it fails**

Run: `poetry run pytest tests/test_portfolio_replay.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'portfolio.replay'`

- [ ] **Step 3: Write minimal implementation**

```python
# portfolio/replay.py
"""Glue the DuckDB outcome ledger + 1d OHLCV + regime into the paper book.

The only module in `portfolio/` that touches the database. Reads resolved
`signal_alert_outcomes` rows, builds a daily grid spanning the ledger, aligns
each symbol's 1d close to that grid (forward-filled), optionally labels each
entry's 1d regime via `analytics.regime.classify_series`, and runs `PaperBook`.
"""

from __future__ import annotations

import duckdb
import numpy as np

from analytics.data_store import get_ohlcv
from analytics.regime import classify_series
from portfolio.book import BookResult, LedgerTrade, PaperBook
from portfolio.sizing import SizingConfig

_DAY = 86_400_000

_RESOLVED_SQL = (
    "SELECT signal_id, symbol, tf, strategy, direction, candle_ts_ms, "
    "       outcome_filled_at_ms, entry_price, sl_price, outcome, outcome_r "
    "FROM signal_alert_outcomes "
    "WHERE outcome IN ('win', 'loss', 'expired') AND outcome_r IS NOT NULL "
    "  AND candle_ts_ms IS NOT NULL AND outcome_filled_at_ms IS NOT NULL "
    "ORDER BY candle_ts_ms"
)


def _empty_result(cfg: SizingConfig) -> BookResult:
    return BookResult(
        daily_index=np.array([], dtype=np.int64), capital=cfg.capital,
        pnl_fixed=np.array([]), pnl_comp=np.array([]), sized=[], skipped=[],
    )


def replay_ledger(conn: duckdb.DuckDBPyConnection, cfg: SizingConfig) -> BookResult:
    rows = conn.execute(_RESOLVED_SQL).fetchall()
    if not rows:
        return _empty_result(cfg)

    trades = [
        LedgerTrade(
            signal_id=str(r[0]), symbol=str(r[1]), tf=str(r[2]), strategy=str(r[3]),
            direction=str(r[4]), entry_ts_ms=int(r[5]), exit_ts_ms=int(r[6]),
            entry_price=float(r[7]), sl_price=float(r[8]), outcome=str(r[9]),
            realized_r=float(r[10]),
        )
        for r in rows
    ]

    min_entry = min(t.entry_ts_ms for t in trades)
    max_exit = max(t.exit_ts_ms for t in trades)
    start_day = (min_entry // _DAY) * _DAY
    end_day = (max_exit // _DAY) * _DAY
    daily_index = np.arange(start_day, end_day + _DAY, _DAY, dtype=np.int64)

    symbols = sorted({t.symbol for t in trades})
    close_by_symbol: dict[str, np.ndarray] = {}
    regime_by_signal: dict[str, str] = {}
    regime_by_symbol_grid: dict[str, np.ndarray] = {}

    for sym in symbols:
        bars = get_ohlcv(conn, sym, "1d", int(start_day), int(end_day + _DAY))
        if bars.empty:
            close_by_symbol[sym] = np.full(len(daily_index), np.nan)
            continue
        ot = bars["open_time"].to_numpy(dtype=np.int64)
        cl = bars["close"].to_numpy(dtype=np.float64)
        idx = np.searchsorted(ot, daily_index, side="right") - 1
        valid = idx >= 0
        aligned = np.full(len(daily_index), np.nan)
        aligned[valid] = cl[idx[valid]]
        close_by_symbol[sym] = aligned
        if cfg.apply_high_vol_halving:
            labels = classify_series(bars).to_numpy()
            grid_labels = np.full(len(daily_index), "unknown", dtype=object)
            grid_labels[valid] = labels[idx[valid]]
            regime_by_symbol_grid[sym] = grid_labels

    if cfg.apply_high_vol_halving:
        for t in trades:
            grid = regime_by_symbol_grid.get(t.symbol)
            if grid is None:
                continue
            entry_idx = int(np.searchsorted(daily_index, t.entry_ts_ms, side="right")) - 1
            if 0 <= entry_idx < len(grid):
                regime_by_signal[t.signal_id] = str(grid[entry_idx])

    book = PaperBook(
        cfg, daily_index, close_by_symbol,
        regime_by_signal=regime_by_signal if cfg.apply_high_vol_halving else None,
    )
    return book.run(trades)
```

```python
# portfolio/__init__.py  (replace exports)
from portfolio.book import BookResult, LedgerTrade, PaperBook, SizedTrade
from portfolio.replay import replay_ledger
from portfolio.sizing import SizingConfig

__all__ = [
    "BookResult", "LedgerTrade", "PaperBook", "SizedTrade",
    "SizingConfig", "replay_ledger",
]
```

> **Verify before running:** confirm `analytics.regime.classify_series` accepts a 1d OHLCV DataFrame with `open/high/low/close` columns and returns a per-row label Series aligned to the input rows (`grep -n -A30 'def classify_series' analytics/regime.py`). If it requires renamed columns or a specific index, adapt the `classify_series(bars)` call only.

- [ ] **Step 4: Run to verify it passes**

Run: `poetry run pytest tests/test_portfolio_replay.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Lint, typecheck, full suite, commit**

```bash
make lint-py typecheck
poetry run pytest tests/test_portfolio_sizing.py tests/test_portfolio_book.py tests/test_portfolio_metrics.py tests/test_portfolio_replay.py -v
git add portfolio/replay.py portfolio/__init__.py tests/test_portfolio_replay.py
git commit -m "feat(portfolio): replay_ledger — DuckDB ledger + 1d OHLCV + regime into PaperBook"
```

---

## Task 7: CLI wrapper + Makefile target

**Files:**

- Create: `cli/portfolio.py`
- Modify: `cli/main.py` (import + register, 2 lines)
- Modify: `Makefile` (add `buibui-portfolio-replay` target + `.PHONY`)
- Test: `tests/test_portfolio_replay.py` (append a CLI-report smoke test)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_portfolio_replay.py  (append)
from portfolio.report import format_report


def test_format_report_renders_headline(capsys=None) -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    _seed_ohlcv_1d(conn, "BTCUSDT", 12)
    for i, (ed, oc, r) in enumerate([(1, "win", 2.0), (3, "loss", -1.0), (5, "win", 2.0)]):
        _seed_resolved(conn, signal_id=f"s{i}", symbol="BTCUSDT",
                       entry_day=ed, exit_day=ed, outcome=oc, outcome_r=r)
    cfg = SizingConfig(apply_high_vol_halving=False)
    res = replay_ledger(conn, cfg)
    text = format_report(res, cfg)
    assert "Sharpe" in text
    assert "fixed-notional" in text.lower()
    assert "Attribution" in text
```

- [ ] **Step 2: Run to verify it fails**

Run: `poetry run pytest tests/test_portfolio_replay.py::test_format_report_renders_headline -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'portfolio.report'`

- [ ] **Step 3: Write the report helper + CLI wrapper**

```python
# portfolio/report.py
"""Render a replay result into a terminal report (pure string builder)."""

from __future__ import annotations

import pandas as pd

from portfolio import metrics
from portfolio.book import BookResult
from portfolio.sizing import SizingConfig


def _curve(values, index) -> pd.Series:  # type: ignore[no-untyped-def]
    return pd.Series(values, index=pd.to_datetime(index, unit="ms", utc=True))


def format_report(res: BookResult, cfg: SizingConfig) -> str:
    if not res.sized:
        return "P1 paper portfolio: no resolved ledger rows to replay."
    fixed = cfg.capital + res.pnl_fixed
    comp = cfg.capital + res.pnl_comp
    fixed_curve = _curve(fixed, res.daily_index)
    comp_curve = _curve(comp, res.daily_index)
    ppy = cfg.annualization_days

    lines: list[str] = []
    lines.append("=== P1 Paper Portfolio — policy #0 (today's exits) ===")
    lines.append(
        f"trades sized={len(res.sized)}  skipped={len(res.skipped)}  "
        f"days={len(res.daily_index)}  capital={cfg.capital:,.0f}"
    )
    lines.append("")
    lines.append("-- HEADLINE: fixed-notional / constant-R --")
    lines.append(f"  Sharpe        {metrics.sharpe(fixed_curve, ppy):+.2f}")
    lines.append(f"  Sortino       {metrics.sortino(fixed_curve, ppy):+.2f}")
    lines.append(f"  Calmar        {metrics.calmar(fixed_curve, ppy):+.2f}")
    lines.append(f"  Max drawdown  {metrics.max_drawdown(fixed_curve):+.1%}")
    lines.append(f"  Ann. return   {metrics.annual_return(fixed_curve, ppy):+.1%}")
    lines.append(f"  Ann. vol      {metrics.annual_vol(fixed_curve, ppy):.1%} "
                 f"(target {cfg.vol_target_annual:.0%})")
    lines.append(f"  Avg exposure  {metrics.avg_exposure(res):.2%} gross open risk")
    lines.append(f"  Risk turnover {metrics.risk_turnover(res):.1f}x")
    lines.append(f"  Final equity  {fixed[-1]:,.0f}")
    lines.append("")
    lines.append("-- compounding curve (governor basis) --")
    lines.append(f"  Sharpe        {metrics.sharpe(comp_curve, ppy):+.2f}")
    lines.append(f"  Max drawdown  {metrics.max_drawdown(comp_curve):+.1%}")
    lines.append(f"  Final equity  {comp[-1]:,.0f}")
    lines.append("")
    lines.append("-- Attribution (fixed basis, by strategy×tf×direction) --")
    attr = metrics.attribution(res.sized)
    lines.append(attr.to_string(index=False, float_format=lambda x: f"{x:+.2f}"))
    return "\n".join(lines)
```

```python
# cli/portfolio.py
"""Buibui CLI — `portfolio replay` subcommand (read-only paper replay)."""

from __future__ import annotations

import argparse
from pathlib import Path

import duckdb

from analytics.store import DEFAULT_DB_PATH
from portfolio.report import format_report
from portfolio.replay import replay_ledger
from portfolio.sizing import SizingConfig


def run_portfolio_replay(args: argparse.Namespace) -> None:
    cfg = SizingConfig.from_toml(args.config) if args.config else SizingConfig()
    if args.capital is not None:
        cfg = SizingConfig.from_toml(args.config) if args.config else SizingConfig()
        from dataclasses import replace

        overrides = {}
        if args.capital is not None:
            overrides["capital"] = float(args.capital)
        if args.vol_target is not None:
            overrides["vol_target_annual"] = float(args.vol_target)
        cfg = replace(cfg, **overrides)
    conn = duckdb.connect(str(args.db), read_only=True)
    res = replay_ledger(conn, cfg)
    print(format_report(res, cfg))


def add_portfolio_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    p = subparsers.add_parser(
        "portfolio", help="Paper-portfolio replay of the live outcome ledger"
    )
    sub = p.add_subparsers(dest="portfolio_command", required=True)
    replay_p = sub.add_parser("replay", help="Replay signal_alert_outcomes into a sized book")
    replay_p.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="DuckDB path")
    replay_p.add_argument("--config", type=str, default=None,
                          help="TOML with a [portfolio] block (optional)")
    replay_p.add_argument("--capital", type=float, default=None, help="paper capital override")
    replay_p.add_argument("--vol-target", type=float, default=None, dest="vol_target",
                          help="annual vol target override (e.g. 0.20)")
    replay_p.set_defaults(func=run_portfolio_replay)
```

> Simplify `run_portfolio_replay` — the duplicated `from_toml` call above is redundant; collapse to: load cfg from `--config` (or defaults), then `replace()` in `--capital`/`--vol-target` when provided. Final form:

```python
def run_portfolio_replay(args: argparse.Namespace) -> None:
    from dataclasses import replace

    cfg = SizingConfig.from_toml(args.config) if args.config else SizingConfig()
    overrides: dict[str, float] = {}
    if args.capital is not None:
        overrides["capital"] = float(args.capital)
    if args.vol_target is not None:
        overrides["vol_target_annual"] = float(args.vol_target)
    if overrides:
        cfg = replace(cfg, **overrides)
    conn = duckdb.connect(str(args.db), read_only=True)
    res = replay_ledger(conn, cfg)
    print(format_report(res, cfg))
```

- [ ] **Step 4: Register in `cli/main.py`**

Add `portfolio` to the import line and register the subparser:

```python
from cli import (
    analytics, backtest, digest, monitor, param, portfolio, recalibrate, signal, web,
)
# ... inside main(), alongside the other add_*_subparser calls:
portfolio.add_portfolio_subparser(subparsers)
```

- [ ] **Step 5: Add the Makefile target**

Append near `buibui-recalibrate` (and add `buibui-portfolio-replay` to the `.PHONY` line):

<!-- markdownlint-disable MD010 -->

```make
buibui-portfolio-replay:
	@poetry run python buibui.py portfolio replay \
		$(if $(CONFIG),--config $(CONFIG),) \
		$(if $(CAPITAL),--capital $(CAPITAL),) \
		$(if $(VOL_TARGET),--vol-target $(VOL_TARGET),)
```

<!-- markdownlint-enable MD010 -->

- [ ] **Step 6: Run the smoke test + CLI parse check**

```bash
poetry run pytest tests/test_portfolio_replay.py -v
poetry run python buibui.py portfolio replay --help
```

Expected: tests PASS; `--help` prints the replay usage.

- [ ] **Step 7: Lint, typecheck, commit**

```bash
make lint-py typecheck
git add cli/portfolio.py cli/main.py Makefile portfolio/report.py tests/test_portfolio_replay.py
git commit -m "feat(portfolio): buibui portfolio replay CLI + report + Makefile target"
```

---

## Task 8: Run the baseline, write the results doc

**Files:**

- Create: `docs/audits/2026-06-14-p1-portfolio-baseline.md`

- [ ] **Step 1: Run the replay against the real ledger** (read-only; safe — no Telegram, no writes)

```bash
PYTHONPATH=. poetry run python buibui.py portfolio replay
# also capture the regime-on vs regime-off delta and a higher vol target sensitivity:
PYTHONPATH=. poetry run python buibui.py portfolio replay --vol-target 0.30
```

- [ ] **Step 2: Write the results doc**

Capture, in `docs/audits/2026-06-14-p1-portfolio-baseline.md` (markdownlint-clean — every fence languaged, spaced `| --- |` tables):

- The headline fixed-notional Sharpe / Sortino / Calmar / max-DD / ann. return / ann. vol, plus the compounding curve's Sharpe + max-DD.
- Sized vs skipped counts; skip-reason breakdown (cap_breach / zero_risk / before_grid).
- Attribution table (top + bottom strategy×tf×direction by P&L).
- The realized-vs-target vol read (did the governor hold ~20%?).
- **G1 framing:** this is the system's first *historical* risk-adjusted baseline, NOT the G1 gate itself. G1 = ≥3-month **forward paper** Sharpe ≥ 1.0. State plainly whether the historical baseline clears 1.0 and what that implies for sequencing the exit branch (sub-project B).
- **Caveats:** `outcome_r` cost-mix (pre/post-PR-3); 1d-regime coarsening; single-symbol-cluster non-majors; concurrency vol approximated at daily granularity.

- [ ] **Step 3: Commit**

```bash
make lint-md
git add docs/audits/2026-06-14-p1-portfolio-baseline.md
git commit -m "docs(portfolio): P1 paper-portfolio baseline results + G1 framing"
```

---

## Task 9: Docs sync + Definition of Done

**Files:**

- Modify: `CLAUDE.md` (Project Structure — add `portfolio/` package entry + CLI line)
- Modify: `README.md` (if it enumerates packages/CLI subcommands)
- Modify: `.claude/context/analytics.md` (only if it indexes the ledger consumers)
- Modify: `~/.claude-personal/projects/-home-kng-repo-buibui-moon-trader-bot/memory/MEMORY.md` (Current State) + `project_todo_master.md` (mark P1 portfolio shipped, exits next)
- Modify: `docs/plans/next-conversation-prompt.md` (point at sub-project B: exit replay)

- [ ] **Step 1: Full Definition-of-Done gate**

```bash
make lint-py
make typecheck
make test
make test-regression
```

Expected: lint ✓, mypy ✓, full suite green (new `tests/test_portfolio_*` included), **regression goldens UNMOVED** (this branch adds a read-only package; the backtest pipeline is untouched — if a golden moves, stop and investigate, do not regenerate).

- [ ] **Step 2: Update `CLAUDE.md`**

Add under Project Structure a `portfolio/` bullet describing the package (sizing/book/metrics/replay/report + `cli/portfolio.py` + `make buibui-portfolio-replay`), and add `buibui portfolio replay` to the CLI subcommand list.

- [ ] **Step 3: Update `README.md` / `.claude/context/analytics.md`** where they enumerate packages or the ledger's consumers (run `/post-branch` discipline — behaviour-gated; this is a new feature so docs apply).

- [ ] **Step 4: Update memory**

In `MEMORY.md` Current State: one-line session summary (P1 paper portfolio shipped, first Sharpe numbers, exits next). In `project_todo_master.md`: mark P1 sizing+portfolio shipped under policy #0; next = exit replay (sub-project B). Refresh `docs/plans/next-conversation-prompt.md` to hand off sub-project B (exit-policy replay: `analytics/exits/{policies,replay}.py`, #1/#2/#6, MFE-timing caveat first, reuse this `PaperBook` for the joint A/B).

- [ ] **Step 5: Commit + PR**

```bash
git add -A
git commit -m "docs(portfolio): sync CLAUDE.md/README + memory; close P1 portfolio"
```

Then `/pr-summary` → `gh pr create` (on `s10023`) → `/post-branch`.

---

## Self-Review

**Spec coverage (P1 spec §0–§9):**

- §2 two-layer model → Task 2 (`effective_risk_fraction`, governor, regime, caps) + Task 4/5 (pass).
- §4 data source + concurrency → Task 6 (`replay_ledger`, `candle_ts_ms`→`outcome_filled_at_ms`, non-NULL filter) + Task 4 (overlap/caps).
- §5 module structure (`sizing/book/replay/metrics` + cli + tests + Makefile) → Tasks 1–7. (Spec lists these exact modules; `report.py` is an added thin renderer to keep `cli/portfolio.py` minimal — consistent with the repo's lib/wrapper split.)
- §6 regime as size modulator (high_vol→0.5, not a router) → Task 5 + Task 6 regime labeling.
- §9 defaults → Task 1 `SizingConfig`.
- §10 headline = fixed-notional (resolved) → Task 7 report; both curves shown.
- L11 metrics (Sharpe/Sortino/Calmar/max-DD/turnover/exposure/attribution) → Task 3.
- First risk-adjusted number deliverable → Task 8.

**Placeholder scan:** none — every step carries runnable code or an exact command. (Task 5 expects no new production code; that is explicit, not a placeholder.)

**Type consistency:** `LedgerTrade` field order fixed (used positionally in tests) and matches `replay.py`'s constructor. `SizedTrade` fields referenced by `metrics.attribution` / `avg_exposure` / `risk_turnover` (`entry_idx`, `exit_idx`, `r_eff`, `rc_fixed`, `pnl_fixed`, `realized_r`, `strategy`, `tf`, `direction`) all defined in Task 4. `BookResult` fields (`daily_index`, `capital`, `pnl_fixed`, `pnl_comp`, `sized`, `skipped`) consistent across `book.py` / `metrics.py` / `report.py` / `replay.py`. `SizingConfig.from_toml` returns `SizingConfig`; `replace` import local. Governor reads `pnl_comp` (compounding) per the resolved decision.

**Ordering caveat (flagged in Task 3):** `metrics.py` imports from `portfolio.book`; implement `book.py` dataclasses (Task 4) before running the metrics module import. The executor may interleave Tasks 3↔4 dataclass-first.
