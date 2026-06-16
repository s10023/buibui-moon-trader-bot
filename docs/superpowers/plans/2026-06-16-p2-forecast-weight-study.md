# P2 Forecast-Weight Study Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Determine whether re-weighting the four EWMAC speeds lifts the universe-combined Sharpe toward the fast-speed level (s8/32 ≈ +0.83) **and survives DSR/PBO** over an enlarged, honestly-labelled trial family.

**Architecture:** Add an optional `weights` vector to the existing pure forecast engine (equal-weight stays byte-identical so goldens don't move), a small pure module of labelled candidate schemes, a read-only replay front door producing per-scheme books, a two-family DSR/PBO split in `report.evaluate`, and a `--weight-study` driver that prints the table + a gate verdict. The study itself is read-only against `analytics.db`.

**Tech Stack:** Python 3.11+, pandas, numpy, duckdb, pytest. Reuses `analytics/research_guards` (DSR/PBO/bootstrap/MinTRL) and `portfolio.metrics`.

**Spec:** `docs/superpowers/specs/2026-06-16-p2-forecast-weight-study-design.md`

---

## File structure

- `analytics/forecast/config.py` — add `weights` field + length validation (modify).
- `analytics/forecast/ewmac.py` — weighted `combine_forecasts` (modify).
- `analytics/forecast/book.py` — thread `cfg.weights` into `combine_forecasts` (modify).
- `analytics/forecast/weights.py` — labelled candidate scheme family (**create**).
- `analytics/forecast/replay.py` — `replay_weight_schemes` (modify).
- `analytics/forecast/__init__.py` — export `replay_weight_schemes` (modify).
- `analytics/forecast/report.py` — optional `pbo_returns` two-family split (modify).
- `tools/forecast_audit.py` — `--weight-study` flag + `build_weight_study` (modify).
- `Makefile` — `buibui-forecast-weight-study` target (modify).
- `tests/forecast/test_config.py`, `test_ewmac.py`, `test_report.py`, `test_audit_cli.py` — extend.
- `tests/forecast/test_weights.py`, `test_weight_schemes_replay.py` — **create**.
- `docs/audits/2026-06-16-p2-forecast-weight-study.md` — verdict (**create**, Task 8).

**Spec deviation (noted):** the spec sketched `replay_weight_schemes -> dict[str, np.ndarray]`. The plan returns `dict[str, ForecastBookResult]` instead — the driver needs full results for per-scheme metrics *and* the returns-family is then a one-line comprehension, so one pass serves both (DRY, no double book compute). Also: the spec's `carver_handcraft` a-priori scheme is **dropped** per the spec's own Risks clause — no clean a-priori non-equal handcraft exists for 4 speeds without correlation estimation (which would reintroduce look-ahead). The a-priori set is therefore `{equal, inverse_cost}`.

---

### Task 1: `ForecastConfig.weights` field + validation

**Files:**

- Modify: `analytics/forecast/config.py`
- Test: `tests/forecast/test_config.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/forecast/test_config.py`:

```python
import pytest

from analytics.forecast.config import ForecastConfig


def test_weights_defaults_to_none() -> None:
    assert ForecastConfig().weights is None


def test_weights_length_mismatch_raises() -> None:
    # default speeds has 4 entries; 2 weights must raise
    with pytest.raises(ValueError):
        ForecastConfig(weights=(1.0, 1.0))


def test_weights_matching_length_ok() -> None:
    cfg = ForecastConfig(weights=(1.0, 1.0, 1.0, 1.0))
    assert cfg.weights == (1.0, 1.0, 1.0, 1.0)
```

(If `test_config.py` already imports `ForecastConfig`/`pytest`, do not duplicate the imports — reuse the existing ones.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/forecast/test_config.py -k weights -v`
Expected: FAIL — `test_weights_length_mismatch_raises` does not raise (field/validation absent) and/or `weights` attribute missing.

- [ ] **Step 3: Implement the field + validation**

In `analytics/forecast/config.py`, add the field to the dataclass (after `annualization_days`) and a `__post_init__`:

```python
    annualization_days: float = 365.0
    weights: tuple[float, ...] | None = None

    def __post_init__(self) -> None:
        if self.weights is not None and len(self.weights) != len(self.speeds):
            raise ValueError(
                f"weights length {len(self.weights)} != "
                f"speeds length {len(self.speeds)}"
            )
```

(`__post_init__` only raises — it sets nothing — so it is compatible with `frozen=True`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/forecast/test_config.py -v`
Expected: PASS (all, including pre-existing config tests).

- [ ] **Step 5: Commit**

```bash
git add analytics/forecast/config.py tests/forecast/test_config.py
git commit -m "feat(forecast): add optional weights vector to ForecastConfig"
```

---

### Task 2: Weighted `combine_forecasts`

**Files:**

- Modify: `analytics/forecast/ewmac.py`
- Modify: `analytics/forecast/book.py`
- Test: `tests/forecast/test_ewmac.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/forecast/test_ewmac.py`:

```python
def test_equal_weights_matches_none_path() -> None:
    close = pd.Series(np.linspace(100.0, 130.0, 400))
    speeds = ((8, 32, 5.3), (16, 64, 3.75), (32, 128, 2.65))
    none_path = combine_forecasts(close, speeds=speeds, fdm=1.25, vol_span=32, cap=20.0)
    eq_path = combine_forecasts(
        close, speeds=speeds, fdm=1.25, vol_span=32, cap=20.0, weights=(1.0, 1.0, 1.0)
    )
    pd.testing.assert_series_equal(none_path, eq_path, check_names=False)


def test_weighted_mean_matches_manual_after_warmup() -> None:
    close = pd.Series(np.linspace(100.0, 130.0, 400))
    speeds = ((8, 32, 5.3), (16, 64, 3.75))
    combined = combine_forecasts(
        close, speeds=speeds, fdm=1.25, vol_span=32, cap=20.0, weights=(3.0, 1.0)
    )
    f1 = scaled_forecast(close, 8, 32, 5.3, 32, 20.0)
    f2 = scaled_forecast(close, 16, 64, 3.75, 32, 20.0)
    both = f1.notna() & f2.notna()
    expected = ((f1[both] * 0.75 + f2[both] * 0.25) * 1.25).clip(-20.0, 20.0)
    pd.testing.assert_series_equal(combined[both], expected, check_names=False)


def test_weighted_zero_weight_drops_a_speed() -> None:
    close = pd.Series(np.linspace(100.0, 130.0, 400))
    speeds = ((8, 32, 5.3), (16, 64, 3.75))
    # weight 0 on the slow speed -> equals the fast speed alone x FDM, capped
    combined = combine_forecasts(
        close, speeds=speeds, fdm=1.25, vol_span=32, cap=20.0, weights=(1.0, 0.0)
    )
    f1 = scaled_forecast(close, 8, 32, 5.3, 32, 20.0)
    expected = (f1 * 1.25).clip(-20.0, 20.0)
    pd.testing.assert_series_equal(
        combined[f1.notna()], expected[f1.notna()], check_names=False
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/forecast/test_ewmac.py -k weight -v`
Expected: FAIL — `combine_forecasts` has no `weights` kwarg (TypeError).

- [ ] **Step 3: Implement the weighted combine**

Replace `combine_forecasts` in `analytics/forecast/ewmac.py`. Add a numpy import at the top (`import numpy as np`) if not present:

```python
def combine_forecasts(
    close: pd.Series,
    speeds: tuple[tuple[int, int, float], ...],
    fdm: float,
    vol_span: int,
    cap: float,
    weights: tuple[float, ...] | None = None,
) -> pd.Series:
    """Weighted mean of per-speed forecasts x FDM, re-capped to +/-cap.

    ``weights`` (one per speed, normalised internally; NaN legs re-normalised
    per row) defaults to equal weight, which is byte-identical to the prior
    ``.mean(axis=1)`` path.
    """
    parts = [
        scaled_forecast(close, fast, slow, scalar, vol_span, cap)
        for fast, slow, scalar in speeds
    ]
    stacked = pd.concat(parts, axis=1)
    if weights is None:
        mean = stacked.mean(axis=1)
    else:
        if len(weights) != len(speeds):
            raise ValueError("weights length must match speeds length")
        w = np.asarray(weights, dtype=float)
        vals = stacked.to_numpy()
        present = ~np.isnan(vals)
        denom = (present * w).sum(axis=1)
        num = np.nansum(vals * w, axis=1)
        mean_vals = np.where(denom > 0.0, num / denom, np.nan)
        mean = pd.Series(mean_vals, index=stacked.index)
    return (mean * fdm).clip(lower=-cap, upper=cap)
```

- [ ] **Step 4: Thread `cfg.weights` through `book.py`**

In `analytics/forecast/book.py`, `instrument_returns`, change the `combine_forecasts` call to pass weights:

```python
    forecast = combine_forecasts(
        close, cfg.speeds, cfg.fdm, cfg.vol_span, cfg.cap, weights=cfg.weights
    ).shift(1)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `poetry run pytest tests/forecast/test_ewmac.py tests/forecast/test_book_instrument.py -v`
Expected: PASS (new weighted tests + unchanged instrument tests — default `weights=None` keeps `instrument_returns` byte-identical).

- [ ] **Step 6: Commit**

```bash
git add analytics/forecast/ewmac.py analytics/forecast/book.py tests/forecast/test_ewmac.py
git commit -m "feat(forecast): weighted combine_forecasts (equal-weight byte-identical)"
```

---

### Task 3: Candidate weight schemes (`analytics/forecast/weights.py`)

**Files:**

- Create: `analytics/forecast/weights.py`
- Test: `tests/forecast/test_weights.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/forecast/test_weights.py`:

```python
"""Tests for analytics.forecast.weights.candidate_schemes."""

from __future__ import annotations

from analytics.forecast.config import ForecastConfig
from analytics.forecast.weights import WeightScheme, candidate_schemes


def test_all_schemes_sum_to_one() -> None:
    for s in candidate_schemes(ForecastConfig()).values():
        assert abs(sum(s.weights) - 1.0) < 1e-12


def test_scheme_lengths_match_speeds() -> None:
    cfg = ForecastConfig()
    for s in candidate_schemes(cfg).values():
        assert len(s.weights) == len(cfg.speeds)


def test_a_priori_flags() -> None:
    schemes = candidate_schemes(ForecastConfig())
    assert schemes["equal"].a_priori is True
    assert schemes["inverse_cost"].a_priori is True
    assert schemes["fast_tilt_linear"].a_priori is False
    assert schemes["fast_tilt_geom"].a_priori is False
    assert schemes["drop_two_slowest"].a_priori is False
    assert schemes["fast_only"].a_priori is False


def test_fast_only_zeros_all_but_first() -> None:
    w = candidate_schemes(ForecastConfig())["fast_only"].weights
    assert w[0] == 1.0 and all(x == 0.0 for x in w[1:])


def test_drop_two_slowest_zeros_slow_half() -> None:
    w = candidate_schemes(ForecastConfig())["drop_two_slowest"].weights
    assert w[0] > 0.0 and w[1] > 0.0 and w[2] == 0.0 and w[3] == 0.0


def test_fast_tilt_geom_strictly_decreasing() -> None:
    w = candidate_schemes(ForecastConfig())["fast_tilt_geom"].weights
    assert w[0] > w[1] > w[2] > w[3]


def test_inverse_cost_favours_slow_leg() -> None:
    # slower legs trade less (cheaper) -> a-priori cost logic weights them up
    w = candidate_schemes(ForecastConfig())["inverse_cost"].weights
    assert w[-1] > w[0]


def test_weightscheme_is_namedtuple_shape() -> None:
    s = candidate_schemes(ForecastConfig())["equal"]
    assert isinstance(s, WeightScheme)
    assert len(s.weights) == 4 and isinstance(s.a_priori, bool)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/forecast/test_weights.py -v`
Expected: FAIL — module `analytics.forecast.weights` does not exist (ImportError).

- [ ] **Step 3: Create the module**

Create `analytics/forecast/weights.py`:

```python
"""Candidate forecast-weight schemes for the P2 weight study.

Pure: weight vectors are derived from the speed *structure* (count + slow
spans), never from realized performance. Each scheme is flagged ``a_priori``
(no look-ahead, defensible to ship) or not (data-snooped, motivated by the
observed per-speed Sharpes — must be haircut as such).

The a-priori set is ``{equal, inverse_cost}``: no clean a-priori non-equal
handcraft toward the *fast* speeds exists without correlation estimation on
this data (which would reintroduce look-ahead), so any fast tilt is, by
construction, data-snooped here.
"""

from __future__ import annotations

from typing import NamedTuple

from analytics.forecast.config import ForecastConfig


class WeightScheme(NamedTuple):
    weights: tuple[float, ...]
    a_priori: bool


def _norm(w: tuple[float, ...]) -> tuple[float, ...]:
    s = sum(w)
    if s <= 0.0:
        raise ValueError("weight scheme must have positive sum")
    return tuple(x / s for x in w)


def candidate_schemes(cfg: ForecastConfig) -> dict[str, WeightScheme]:
    """Labelled weight-scheme family for the study (speeds ordered fast->slow)."""
    n = len(cfg.speeds)
    slows = [float(slow) for _, slow, _ in cfg.speeds]

    equal = _norm(tuple(1.0 for _ in range(n)))
    # slower legs trade less -> cheaper: a-priori weight proportional to slow span
    inverse_cost = _norm(tuple(slows))

    fast_tilt_linear = _norm(tuple(float(n - i) for i in range(n)))
    rho = 0.5
    fast_tilt_geom = _norm(tuple(rho**i for i in range(n)))
    half = (n + 1) // 2
    drop_two_slowest = _norm(tuple(1.0 if i < half else 0.0 for i in range(n)))
    fast_only = _norm(tuple(1.0 if i == 0 else 0.0 for i in range(n)))

    return {
        "equal": WeightScheme(equal, True),
        "inverse_cost": WeightScheme(inverse_cost, True),
        "fast_tilt_linear": WeightScheme(fast_tilt_linear, False),
        "fast_tilt_geom": WeightScheme(fast_tilt_geom, False),
        "drop_two_slowest": WeightScheme(drop_two_slowest, False),
        "fast_only": WeightScheme(fast_only, False),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/forecast/test_weights.py -v`
Expected: PASS (all 8).

- [ ] **Step 5: Commit**

```bash
git add analytics/forecast/weights.py tests/forecast/test_weights.py
git commit -m "feat(forecast): labelled candidate weight-scheme family"
```

---

### Task 4: `replay_weight_schemes` front door

**Files:**

- Modify: `analytics/forecast/replay.py`
- Modify: `analytics/forecast/__init__.py`
- Test: `tests/forecast/test_weight_schemes_replay.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/forecast/test_weight_schemes_replay.py`:

```python
"""Tests for analytics.forecast.replay.replay_weight_schemes."""

from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd

from analytics.forecast.book import ForecastBookResult
from analytics.forecast.config import ForecastConfig
from analytics.forecast.replay import replay_universe, replay_weight_schemes
from analytics.forecast.weights import candidate_schemes
from analytics.store import init_schema
from analytics.store.market_data import upsert_ohlcv

_DAY = 86_400_000


def _seed(conn: duckdb.DuckDBPyConnection, symbol: str, n: int, slope: float) -> None:
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
        for i in range(n)
    ]
    upsert_ohlcv(conn, pd.DataFrame(rows))


def test_replay_weight_schemes_has_all_schemes() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    _seed(conn, "AAAUSDT", 320, 1.0)
    out = replay_weight_schemes(conn, ForecastConfig(), symbols=["AAAUSDT"])
    assert set(out) == set(candidate_schemes(ForecastConfig()))
    assert all(isinstance(v, ForecastBookResult) for v in out.values())


def test_equal_scheme_matches_default_combined_book() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    _seed(conn, "AAAUSDT", 320, 1.0)
    _seed(conn, "BBBUSDT", 320, 0.5)
    syms = ["AAAUSDT", "BBBUSDT"]
    out = replay_weight_schemes(conn, ForecastConfig(), symbols=syms)
    default = replay_universe(conn, ForecastConfig(), symbols=syms)
    np.testing.assert_allclose(
        out["equal"].portfolio_return,
        default.portfolio_return,
        rtol=1e-9,
        atol=1e-12,
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/forecast/test_weight_schemes_replay.py -v`
Expected: FAIL — `replay_weight_schemes` is not importable (ImportError).

- [ ] **Step 3: Implement `replay_weight_schemes`**

In `analytics/forecast/replay.py`, add the import and the function (place after `replay_trials`):

```python
from analytics.forecast.weights import candidate_schemes
```

```python
def replay_weight_schemes(
    conn: duckdb.DuckDBPyConnection,
    cfg: ForecastConfig,
    symbols: list[str] | None = None,
) -> dict[str, ForecastBookResult]:
    """Run the universe book once per candidate weight scheme (read-only).

    Keys are scheme names from ``candidate_schemes``; values are the full
    ``ForecastBookResult`` under that scheme's weights. Loads the daily inputs
    once and re-runs the book per scheme via ``dataclasses.replace``.
    """
    syms = symbols if symbols is not None else load_universe()
    closes, fundings = load_daily_inputs(conn, syms)
    out: dict[str, ForecastBookResult] = {}
    for name, scheme in candidate_schemes(cfg).items():
        scheme_cfg = dataclasses.replace(cfg, weights=scheme.weights)
        out[name] = run_forecast_backtest(closes, fundings, scheme_cfg)
    return out
```

- [ ] **Step 4: Export from the package**

In `analytics/forecast/__init__.py`, add `replay_weight_schemes` to the `from analytics.forecast.replay import (...)` block and to `__all__` (keep `__all__` alphabetised):

```python
from analytics.forecast.replay import (
    load_daily_inputs,
    replay_trials,
    replay_universe,
    replay_weight_schemes,
)
```

Add `"replay_weight_schemes",` to `__all__` (after `"replay_universe",`).

- [ ] **Step 5: Run tests to verify they pass**

Run: `poetry run pytest tests/forecast/test_weight_schemes_replay.py -v`
Expected: PASS (both).

- [ ] **Step 6: Commit**

```bash
git add analytics/forecast/replay.py analytics/forecast/__init__.py tests/forecast/test_weight_schemes_replay.py
git commit -m "feat(forecast): replay_weight_schemes read-only front door"
```

---

### Task 5: Two-family DSR/PBO split in `report.evaluate`

**Files:**

- Modify: `analytics/forecast/report.py`
- Test: `tests/forecast/test_report.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/forecast/test_report.py`:

```python
def test_pbo_returns_none_equals_explicit_trial_returns() -> None:
    rng = np.random.default_rng(2)
    r = 0.001 + 0.01 * rng.standard_normal(800)
    res = _result(r)
    fam = {"combined": r, "s8_32": r * 1.1, "s64_256": r * 0.2}
    a = evaluate(res, ForecastConfig(), trial_returns=fam)
    b = evaluate(res, ForecastConfig(), trial_returns=fam, pbo_returns=fam)
    assert (a.pbo == b.pbo) or (np.isnan(a.pbo) and np.isnan(b.pbo))


def test_pbo_returns_overrides_pbo_family_keeps_dsr() -> None:
    rng = np.random.default_rng(3)
    r = 0.001 + 0.01 * rng.standard_normal(800)
    res = _result(r)
    dsr_family = {"combined": r, "s8_32": r * 1.1, "s64_256": r * 0.2}
    pbo_family = {"a": r, "b": r * 0.95, "c": -r, "d": r * 0.3}
    base = evaluate(res, ForecastConfig(), trial_returns=dsr_family)
    split = evaluate(
        res, ForecastConfig(), trial_returns=dsr_family, pbo_returns=pbo_family
    )
    # DSR is deflated against trial_returns only -> unchanged by pbo_returns
    assert split.dsr == base.dsr
    assert 0.0 <= split.pbo <= 1.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/forecast/test_report.py -k pbo_returns -v`
Expected: FAIL — `evaluate` has no `pbo_returns` kwarg (TypeError).

- [ ] **Step 3: Add the optional `pbo_returns` parameter**

In `analytics/forecast/report.py`, change the `evaluate` signature and the PBO block. Signature:

```python
def evaluate(
    result: ForecastBookResult,
    cfg: ForecastConfig,
    trial_returns: dict[str, npt.NDArray[np.float64]],
    pbo_returns: dict[str, npt.NDArray[np.float64]] | None = None,
) -> G2Report:
```

Update the docstring's `trial_returns` note to add:

```text
    ``pbo_returns`` (optional) is the family used for the PBO/CSCV matrix; it
    defaults to ``trial_returns``. The weight study passes the schemes-only
    family here (DSR still deflates against the wider ``trial_returns``).
```

Replace the PBO block (currently keyed off `trial_returns`) with:

```python
    # PBO over the selection family (defaults to the DSR family).
    # cscv_pbo's default n_splits=14 needs block_size = T // 14 >= 2, i.e. T >= 28.
    pbo_family = pbo_returns if pbo_returns is not None else trial_returns
    min_len = min((len(v) for v in pbo_family.values()), default=0)
    if min_len >= 28 and len(pbo_family) >= 2:
        mat = np.column_stack([v[-min_len:] for v in pbo_family.values()])
        pbo = cscv_pbo(mat).pbo
    else:
        pbo = float("nan")
```

(Leave the DSR/`trial_srs`, bootstrap, and MinTRL blocks unchanged — they keep using `trial_returns`/`r`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/forecast/test_report.py -v`
Expected: PASS (new + pre-existing report tests — default `pbo_returns=None` reproduces prior behaviour).

- [ ] **Step 5: Commit**

```bash
git add analytics/forecast/report.py tests/forecast/test_report.py
git commit -m "feat(forecast): two-family DSR/PBO split in evaluate (pbo_returns)"
```

---

### Task 6: `--weight-study` driver + Makefile target

**Files:**

- Modify: `tools/forecast_audit.py`
- Modify: `Makefile`
- Test: `tests/forecast/test_audit_cli.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/forecast/test_audit_cli.py` (and add the imports it needs at the top of the file):

```python
from analytics.forecast.weights import candidate_schemes
from tools.forecast_audit import build_weight_study


def test_build_weight_study_returns_table() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    _seed(conn, "AAAUSDT", 320)
    df = build_weight_study(conn, symbols=["AAAUSDT"])
    assert set(df["scheme"]) == set(candidate_schemes(ForecastConfig()))
    for col in ("a_priori", "sharpe", "dsr", "pbo", "boot_lo", "min_trl", "rank"):
        assert col in df.columns
    assert (df["scheme"] == "equal").any()
```

The existing `_seed` builds only `AAAUSDT`-style symbols already; reuse it. Add `from analytics.forecast.config import ForecastConfig` to the imports if not present.

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/forecast/test_audit_cli.py -k weight_study -v`
Expected: FAIL — `build_weight_study` does not exist (ImportError).

- [ ] **Step 3: Implement `build_weight_study` + wire `--weight-study`**

In `tools/forecast_audit.py`, add imports:

```python
from analytics.forecast import replay_weight_schemes
from analytics.forecast.weights import candidate_schemes
```

Add the builder (near `build_report_row`):

```python
def _ann_sharpe_of(r: np.ndarray, ann: float) -> float:
    sd = float(np.std(r, ddof=1)) if len(r) > 1 else 0.0
    return (float(np.mean(r)) / sd * ann) if sd > 1e-12 else 0.0


def build_weight_study(
    conn: duckdb.DuckDBPyConnection,
    symbols: list[str],
) -> pd.DataFrame:
    """Per-scheme universe Sharpe + two-family DSR/PBO stamps + gate verdict.

    DSR deflates over {all schemes} + {per-speed singles}; PBO/CSCV over the
    schemes only (the configs we select among). Clears the bar iff
    DSR >= 0.95 and PBO <= 0.5 and boot_lo > 0.
    """
    cfg = ForecastConfig()
    ann = math.sqrt(cfg.annualization_days)
    schemes = candidate_schemes(cfg)

    results = replay_weight_schemes(conn, cfg, symbols=symbols)
    scheme_returns = {name: res.portfolio_return for name, res in results.items()}
    singles = {
        k: v
        for k, v in replay_trials(conn, cfg, symbols=symbols).items()
        if k != "combined"
    }
    dsr_family = {**scheme_returns, **singles}
    pbo_family = scheme_returns

    rows: list[dict[str, object]] = []
    for name, res in results.items():
        rep = evaluate(res, cfg, trial_returns=dsr_family, pbo_returns=pbo_family)
        clears = bool(rep.dsr >= 0.95 and rep.pbo <= 0.5 and rep.boot_lo > 0.0)
        rows.append(
            {
                "scheme": name,
                "a_priori": schemes[name].a_priori,
                "sharpe": _ann_sharpe_of(res.portfolio_return, ann),
                "dsr": rep.dsr,
                "pbo": rep.pbo,
                "boot_lo": rep.boot_lo,
                "min_trl": rep.min_trl,
                "clears": clears,
            }
        )
    df = pd.DataFrame(rows).sort_values("sharpe", ascending=False).reset_index(drop=True)
    df["rank"] = range(1, len(df) + 1)
    return df
```

In `main()`, add the flag (after the `--majors` argument):

```python
    parser.add_argument(
        "--weight-study",
        action="store_true",
        help="run the forecast-weight study (DSR/PBO-gated) and exit",
    )
```

And branch early in `main()` (right after `universe = load_universe()` and `majors = ...`, before the breadth-contrast rows):

```python
    if args.weight_study:
        df = build_weight_study(conn, universe)
        _print_df("Forecast-weight study (universe)", df)
        winners = df[df["clears"]]
        if winners.empty:
            print(
                "\nVERDICT: no scheme clears DSR>=0.95 & PBO<=0.5 & boot_lo>0 -> "
                "equal-weight stays; the lift is in-sample. Breadth (P3) remains "
                "the binding constraint."
            )
        else:
            ap = winners[winners["a_priori"]]
            kind = "a-priori" if not ap.empty else "data-snooped only"
            best = (ap if not ap.empty else winners).iloc[0]
            print(
                f"\nVERDICT: {len(winners)} scheme(s) clear the bar ({kind}); "
                f"best = {best['scheme']} (Sharpe {best['sharpe']:+.3f}, "
                f"DSR {best['dsr']:.3f}, PBO {best['pbo']:.3f}). "
                "If a-priori -> re-test G2 with these weights; if snooped-only -> "
                "suggestive, needs OOS confirmation before shipping."
            )
        return
```

- [ ] **Step 4: Add the Makefile target**

In `Makefile`, append `buibui-forecast-weight-study` to the master `.PHONY:` list (line 14, after `buibui-forecast-audit`), then add the target after the `buibui-forecast-audit` target:

```makefile
.PHONY: buibui-forecast-weight-study
buibui-forecast-weight-study:  ## P2: read-only forecast-weight study (DSR/PBO-gated)
    PYTHONPATH=. poetry run python tools/forecast_audit.py --weight-study
```

> The recipe line above MUST be indented with a single literal **TAB**, not
> spaces (Make requires it) — it is shown space-indented here only to keep this
> plan markdownlint-clean.

- [ ] **Step 5: Run test + a no-op default smoke to verify it passes**

Run: `poetry run pytest tests/forecast/test_audit_cli.py -v`
Expected: PASS (new weight-study test + pre-existing `build_report_row` test — default `main()` path is unchanged).

- [ ] **Step 6: Commit**

```bash
git add tools/forecast_audit.py Makefile tests/forecast/test_audit_cli.py
git commit -m "feat(forecast): --weight-study driver + make target"
```

---

### Task 7: Causality guard for the weighted path

**Files:**

- Test: `tests/forecast/test_book_instrument.py`

- [ ] **Step 1: Write the test**

Append to `tests/forecast/test_book_instrument.py` (add `import dataclasses` and `from analytics.forecast.config import ForecastConfig` if not already imported):

```python
def test_weighted_combine_has_no_lookahead() -> None:
    close = pd.Series(np.linspace(100.0, 160.0, 400))
    funding = pd.Series(0.0, index=close.index)
    cfg = dataclasses.replace(ForecastConfig(), weights=(0.6, 0.3, 0.1, 0.0))

    base = instrument_returns(close, funding, cfg)
    bumped = close.copy()
    bumped.iloc[300] *= 1.5
    pert = instrument_returns(bumped, pd.Series(0.0, index=close.index), cfg)

    # leverage on day d uses forecast through d-1; perturbing close[300] can only
    # move leverage from day 301 onward. Days 0..299 must be byte-identical.
    pd.testing.assert_series_equal(
        base["leverage"].iloc[:300],
        pert["leverage"].iloc[:300],
        check_names=False,
    )
```

- [ ] **Step 2: Run the test to verify it passes**

Run: `poetry run pytest tests/forecast/test_book_instrument.py -k lookahead -v`
Expected: PASS (the `.shift(1)` in `book.py` is the causality barrier; the weighted combine is still EW-causal). If it FAILS, a look-ahead leak was introduced — stop and debug before proceeding.

- [ ] **Step 3: Commit**

```bash
git add tests/forecast/test_book_instrument.py
git commit -m "test(forecast): no-look-ahead guard for weighted combine"
```

---

### Task 8: Run the study + write the verdict

**Files:**

- Create: `docs/audits/2026-06-16-p2-forecast-weight-study.md`

- [ ] **Step 1: Run the study against the real DB**

Run: `make buibui-forecast-weight-study`
Expected: a "Forecast-weight study (universe)" table (one row per scheme, sorted by Sharpe, with `a_priori / sharpe / dsr / pbo / boot_lo / min_trl / clears / rank`) plus a `VERDICT:` line. Capture the full output.

- [ ] **Step 2: Interpret against the decision rule**

Apply: a scheme clears iff `DSR >= 0.95 AND PBO <= 0.5 AND boot_lo > 0`.

- If an **a-priori** scheme clears AND lifts Sharpe materially toward the s8/32 level -> recommendation: re-test G2 with those weights (defensible, no look-ahead).
- If only **data-snooped** schemes clear -> report as suggestive-but-snooped; do not ship without OOS confirmation.
- If nothing clears -> equal-weight stays; the lift was in-sample; breadth (P3) is the binding constraint.

Cross-check the headline: equal-weight Sharpe should reproduce the G2 +0.36 (sanity that the engine path is unchanged); `fast_only` should reproduce ≈ +0.83.

- [ ] **Step 3: Write the verdict doc**

Create `docs/audits/2026-06-16-p2-forecast-weight-study.md` with: the goal line, the study table (markdown, markdownlint-conformant — spaced `| --- |` delimiters), the per-rule verdict, the recommendation (one of the three branches above), and a one-line "next lever" pointer (P3 cross-sectional / breadth). Keep it factual; a negative result (equal-weight stays) is a valid outcome and must be stated plainly, not hidden.

- [ ] **Step 4: Lint the doc**

Run: `npx markdownlint-cli2 "docs/audits/2026-06-16-p2-forecast-weight-study.md"`
Expected: `0 error(s)`.

- [ ] **Step 5: Commit**

```bash
git add docs/audits/2026-06-16-p2-forecast-weight-study.md
git commit -m "docs(forecast): P2 forecast-weight study verdict"
```

---

### Task 9: Definition-of-Done gate

**Files:** none (verification only)

- [ ] **Step 1: Lint + format**

Run: `make lint-py`
Expected: ruff format + lint clean.

- [ ] **Step 2: Type-check**

Run: `make typecheck`
Expected: mypy strict, 0 errors.

- [ ] **Step 3: Full test suite**

Run: `make test`
Expected: green; new tests included; count up by the tests added in Tasks 1–7.

- [ ] **Step 4: Regression goldens UNMOVED**

Run: `make test-regression`
Expected: PASS with goldens **unchanged**. This is the load-bearing invariant — the default `weights=None` path is byte-identical, so no golden may move. If a golden moves, the byte-identical invariant is broken: stop and fix (do NOT regenerate goldens — this change is not intentionally behavioural).

- [ ] **Step 5: Markdown lint**

Run: `make lint-md`
Expected: clean (spec + verdict docs).

- [ ] **Step 6: Final state check**

Run: `git status` and `git log --oneline origin/main..HEAD`
Expected: clean tree; one commit per task. Branch ready for PR (open the PR only when the user asks).

---

## Self-review

- **Spec coverage:** weighted combine (Task 2), `ForecastConfig.weights` (Task 1), labelled scheme family a-priori + snooped (Task 3), read-only front door (Task 4), two-family DSR/PBO with `pbo_returns` (Task 5), `--weight-study` driver + decision rule (Task 6), causality guard (Task 7), verdict doc (Task 8), DoD incl. goldens-unmoved (Task 9). All spec sections mapped. Two documented deviations: `replay_weight_schemes` returns full results (DRY); `carver_handcraft` dropped per the spec's Risks clause.
- **Placeholder scan:** none — every code/command step is concrete.
- **Type consistency:** `WeightScheme(weights, a_priori)`, `candidate_schemes(cfg) -> dict[str, WeightScheme]`, `combine_forecasts(..., weights=None)`, `replay_weight_schemes(...) -> dict[str, ForecastBookResult]`, `evaluate(..., pbo_returns=None)`, `build_weight_study(conn, symbols) -> pd.DataFrame` — names/signatures consistent across tasks and call sites.
