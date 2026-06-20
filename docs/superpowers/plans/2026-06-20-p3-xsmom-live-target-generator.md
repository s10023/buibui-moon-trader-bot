# XS-solo Daily Target-Position Generator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a read-only daily generator that emits today's XS target positions (side · governor-scaled leverage · $notional at ~$10k), equal to the validated +1.375 book's next-bar positions.

**Architecture:** Pure math in a new `analytics/xsmom/live.py` (next-period leverage/governor, target book assembly, snapshot serialization), a DB front-door `replay_targets` in `analytics/xsmom/replay.py`, and a read-only `tools/xsmom_targets.py` driver + `make buibui-xsmom-targets`. The engine (`run_xs_backtest`) is untouched — purely additive, goldens stay frozen.

**Tech Stack:** Python 3.11, pandas, numpy, duckdb, pytest, Poetry. Reuses `analytics/forecast/` (`ForecastConfig`, `load_daily_inputs`) and `analytics/xsmom/book.py`.

---

## Background for the implementer

The XS book's `xs_leverage(closes, cfg)` (in `analytics/xsmom/book.py`) is a per-day, per-instrument leverage matrix. Row `d` is the position **held during bar `d`**, sized from info through `d-1` (a `.shift(1)` is baked in). The build is:

```text
xs_leverage[d] = (demeaned[d-1] / 10) * (vol_target / vol_ann[d])
demeaned[d]    = xs_demeaned_forecasts(closes, cfg)[d]      # causal, uses closes <= d
vol_ann[d]     = ew_return_vol(close, vol_span)[d] * sqrt(ann)
ew_return_vol  = close.pct_change().ewm(span, min_periods=span).std().shift(1)   # value at d uses returns <= d-1
```

Standing at the close of the last completed bar `T`, the operator needs the position to **hold during the next bar `T+1`** — that is the same formula one step ahead, using the *latest* (unshifted) forecast and vol:

```text
next_lev = (demeaned[T] / 10) * (vol_target / vol_ann_asof_T)
vol_ann_asof_T = close.pct_change().ewm(span, min_periods=span).std().iloc[-1] * sqrt(ann)   # UNSHIFTED -> returns <= T
```

Because `ew_return_vol` bakes in `.shift(1)`, the unshifted `.std().iloc[-1]` is exactly what `ew_return_vol` would report at a hypothetical `T+1`. So **by construction** `next_period_leverage(closes through T) == xs_leverage(closes through T+1).loc[T+1]`. That equality is the backtest↔live consistency guarantee *and* the no-look-ahead proof, and it is the load-bearing test in Task 1.

`ForecastConfig()` defaults are the deploy config: `vol_span=32`, `vol_target_annual=0.20`, `gov_window=64`, `g_min=0.5`, `g_max=1.5`, `annualization_days=365.0`, `xs_dollar_neutral=False`. `min_history = 288` bars.

---

## File structure

- Create `analytics/xsmom/live.py` — pure: next-period leverage/governor, `TargetPosition`/`TargetBook`, `build_target_book`, snapshot (de)serialization, `position_deltas`, `reconcile`.
- Modify `analytics/xsmom/replay.py` — add `replay_targets` (DB front door).
- Modify `analytics/xsmom/__init__.py` — export the new public names.
- Create `tools/xsmom_targets.py` — read-only driver (render + snapshot file I/O + `main`).
- Modify `Makefile` — add `buibui-xsmom-targets` (+ `.PHONY`).
- Modify `README.md` and `CLAUDE.md` — document the tool/make target.
- Create `tests/xsmom/test_live.py` — pure tests.
- Create `tests/xsmom/test_targets_replay.py` — DB-replay test.
- Create `tests/xsmom/test_targets_cli.py` — render + snapshot + `main` tests.

---

### Task 1: Next-period leverage + reconciliation primitive

**Files:**

- Create: `analytics/xsmom/live.py`
- Test: `tests/xsmom/test_live.py`

- [ ] **Step 1: Write the failing test**

Create `tests/xsmom/test_live.py`:

```python
from __future__ import annotations

import numpy as np
import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.xsmom.book import xs_demeaned_forecasts, xs_leverage
from analytics.xsmom.live import next_period_leverage, reconcile

_SYMS = ["AAAUSDT", "BBBUSDT", "CCCUSDT", "DDDUSDT"]


def _make_closes(n: int = 400) -> dict[str, pd.Series]:
    idx = pd.date_range("2021-01-01", periods=n, freq="D", tz="UTC")
    rng = np.random.default_rng(7)
    out: dict[str, pd.Series] = {}
    for i, sym in enumerate(_SYMS):
        steps = rng.normal(0.0005 * (i - 1.5), 0.02, n)
        out[sym] = pd.Series(100.0 * np.exp(np.cumsum(steps)), index=idx)
    return out


def test_reconcile_matches_book_next_bar() -> None:
    closes = _make_closes()
    cfg = ForecastConfig()
    union = xs_leverage(closes, cfg).index
    for cutoff in (union[-30], union[-15], union[-2]):
        assert reconcile(closes, cfg, cutoff) < 1e-9


def test_future_bar_does_not_leak_into_target() -> None:
    closes = _make_closes()
    cfg = ForecastConfig()
    cutoff = xs_leverage(closes, cfg).index[-10]
    base = reconcile(closes, cfg, cutoff)
    perturbed = {s: c.copy() for s, c in closes.items()}
    perturbed["AAAUSDT"].iloc[-1] *= 1.5  # a bar strictly after the cutoff
    after = reconcile(perturbed, cfg, cutoff)
    assert base < 1e-9
    assert after < 1e-9


def test_demeaned_latest_sums_to_zero() -> None:
    closes = _make_closes()
    latest = xs_demeaned_forecasts(closes, ForecastConfig()).iloc[-1]
    assert abs(float(latest.dropna().sum())) < 1e-9
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/xsmom/test_live.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'analytics.xsmom.live'`

- [ ] **Step 3: Write minimal implementation**

Create `analytics/xsmom/live.py`:

```python
"""Live daily target-position generation for the XS-solo deploy core.

Pure (no DB I/O). Computes the *next-period* target leverage — the position to
hold during the bar after the last completed close — from the latest causal
forecast and vol, with no backtest position-alignment shift. The reconciliation
helper proves `next_period_leverage(through T) == xs_leverage(through T+1)[T+1]`,
which is both the backtest<->live consistency guarantee and the no-look-ahead
proof.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.xsmom.book import xs_demeaned_forecasts, xs_leverage


def next_period_leverage(
    closes: dict[str, pd.Series], cfg: ForecastConfig
) -> pd.Series:
    """Per-instrument vol-parity leverage to hold during the next bar.

    `(demeaned[T] / 10) * (vol_target / vol_ann_asof_T)`, where `demeaned[T]` is
    the latest cross-sectionally demeaned forecast and `vol_ann_asof_T` is the
    UNSHIFTED EW return vol at `T` (uses returns through `T`). NaN for
    not-yet-warmed-up / absent instruments. Mirrors `xs_leverage` one step ahead,
    including the optional `xs_dollar_neutral` re-center.
    """
    demeaned = xs_demeaned_forecasts(closes, cfg)
    union = demeaned.index
    latest = demeaned.iloc[-1]
    ann = np.sqrt(cfg.annualization_days)

    out: dict[str, float] = {}
    for sym, close in closes.items():
        raw_std = (
            close.pct_change().ewm(span=cfg.vol_span, min_periods=cfg.vol_span).std()
        )
        vol_ann_t = float(raw_std.reindex(union).iloc[-1]) * ann
        f = float(latest.get(sym, np.nan))
        out[sym] = (f / 10.0) * (cfg.vol_target_annual / vol_ann_t)

    series = pd.Series(out).replace([np.inf, -np.inf], np.nan)
    if cfg.xs_dollar_neutral:
        series = series - series.mean()
    return series


def reconcile(
    closes: dict[str, pd.Series], cfg: ForecastConfig, cutoff: pd.Timestamp
) -> float:
    """Max abs diff between the live target as-of `cutoff` and the research book's
    leverage for the first bar after `cutoff`. ~0 when correct (NaN treated as 0
    on both sides so any active-set mismatch surfaces)."""
    truncated = {
        sym: s[s.index <= cutoff]
        for sym, s in closes.items()
        if len(s[s.index <= cutoff]) > 0
    }
    live = next_period_leverage(truncated, cfg)
    full = xs_leverage(closes, cfg)
    after = full.index[full.index > cutoff]
    if len(after) == 0:
        raise ValueError("cutoff leaves no bar after it")
    book_row = full.loc[after[0]]
    diff = (
        live.reindex(book_row.index).fillna(0.0) - book_row.fillna(0.0)
    ).abs()
    return float(diff.max())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/xsmom/test_live.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add analytics/xsmom/live.py tests/xsmom/test_live.py
git commit -m "feat(xsmom): next_period_leverage + frozen-clock reconcile primitive"
```

---

### Task 2: Next-period governor

**Files:**

- Modify: `analytics/xsmom/live.py`
- Test: `tests/xsmom/test_live.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/xsmom/test_live.py` (and extend the imports line):

```python
from analytics.xsmom.book import run_xs_backtest, xs_demeaned_forecasts, xs_leverage
from analytics.xsmom.live import (
    next_period_governor,
    next_period_leverage,
    reconcile,
)


def _make_fundings(closes: dict[str, pd.Series]) -> dict[str, pd.Series]:
    return {sym: pd.Series(0.0, index=s.index) for sym, s in closes.items()}


def test_governor_matches_book_next_bar() -> None:
    closes = _make_closes()
    cfg = ForecastConfig()
    res = run_xs_backtest(closes, _make_fundings(closes), cfg)
    pre = pd.Series(res.pre_governor_return, index=res.daily_index)
    g_full = pd.Series(res.governor, index=res.daily_index)
    k = len(pre) - 2  # cutoff index; next bar = k+1 = last
    g_live = next_period_governor(pre.iloc[: k + 1], cfg)
    assert abs(g_live - float(g_full.iloc[k + 1])) < 1e-9


def test_governor_cold_start_is_neutral() -> None:
    pre = pd.Series([0.01, -0.02, 0.0])  # fewer than gov_window points
    assert next_period_governor(pre, ForecastConfig()) == 1.0
```

(Replace the existing `from analytics.xsmom.book import ...` and `from analytics.xsmom.live import ...` lines at the top of the file with the two import blocks above.)

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/xsmom/test_live.py -k governor -v`
Expected: FAIL with `ImportError: cannot import name 'next_period_governor'`

- [ ] **Step 3: Write minimal implementation**

Add to `analytics/xsmom/live.py`:

```python
def next_period_governor(pre_returns: pd.Series, cfg: ForecastConfig) -> float:
    """Causal 20%-vol governor to apply during the next bar.

    `clip(vol_target / (trailing_std_asof_T * sqrt(ann)), g_min, g_max)`, where
    `trailing_std_asof_T` is the UNSHIFTED rolling std of the pre-governor
    portfolio returns at `T`. Cold start (< gov_window history, or degenerate
    vol) returns the neutral 1.0 — matching `portfolio.sizing.vol_governor`.
    """
    ann = np.sqrt(cfg.annualization_days)
    trailing_std = float(
        pre_returns.rolling(cfg.gov_window, min_periods=cfg.gov_window).std().iloc[-1]
    )
    if not np.isfinite(trailing_std) or trailing_std <= 0.0:
        return 1.0
    g = cfg.vol_target_annual / (trailing_std * ann)
    return float(np.clip(g, cfg.g_min, cfg.g_max))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/xsmom/test_live.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add analytics/xsmom/live.py tests/xsmom/test_live.py
git commit -m "feat(xsmom): next_period_governor (causal vol governor for the live target)"
```

---

### Task 3: Target book assembly

**Files:**

- Modify: `analytics/xsmom/live.py`
- Test: `tests/xsmom/test_live.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/xsmom/test_live.py` (extend the `analytics.xsmom.live` import to add `TargetBook`, `TargetPosition`, `build_target_book`):

```python
def test_build_target_book_positions() -> None:
    closes = _make_closes()
    fundings = _make_fundings(closes)
    cfg = ForecastConfig()
    book = build_target_book(closes, fundings, cfg, 10_000.0)

    assert book.active_count == len(book.positions)
    assert book.active_count > 0

    res = run_xs_backtest(closes, fundings, cfg)
    pre = pd.Series(res.pre_governor_return, index=res.daily_index)
    g = next_period_governor(pre, cfg)
    lev = next_period_leverage(closes, cfg)
    assert book.governor == g

    for p in book.positions:
        assert abs(p.leverage - g * float(lev[p.symbol])) < 1e-12
        assert abs(p.notional_usd - p.leverage * 10_000.0) < 1e-9
        expected_side = (
            "long" if p.leverage > 0 else "short" if p.leverage < 0 else "flat"
        )
        assert p.side == expected_side

    assert abs(book.gross_leverage - sum(abs(p.leverage) for p in book.positions)) < 1e-12
    assert abs(book.net_leverage - sum(p.leverage for p in book.positions)) < 1e-12

    last = pd.Timestamp(res.daily_index[-1])
    assert book.as_of_date == last.date().isoformat()
    assert book.next_period_date == (last + pd.Timedelta(days=1)).date().isoformat()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/xsmom/test_live.py -k build_target -v`
Expected: FAIL with `ImportError: cannot import name 'build_target_book'`

- [ ] **Step 3: Write minimal implementation**

Add to `analytics/xsmom/live.py` (add `from dataclasses import dataclass` and `from typing import Any` to the imports):

```python
@dataclass(frozen=True)
class TargetPosition:
    symbol: str
    side: str  # "long" | "short" | "flat"
    leverage: float  # governor-scaled, signed
    notional_usd: float  # leverage * capital
    forecast: float  # demeaned (relative-strength) signal, for context


@dataclass(frozen=True)
class TargetBook:
    as_of_date: str  # ISO date of the last completed 1d bar (T)
    next_period_date: str  # ISO date these targets are held during (T+1)
    capital: float
    governor: float
    active_count: int
    gross_leverage: float
    net_leverage: float
    positions: list[TargetPosition]


def build_target_book(
    closes: dict[str, pd.Series],
    fundings: dict[str, pd.Series],
    cfg: ForecastConfig,
    capital: float,
) -> TargetBook:
    """Assemble today's governor-scaled XS target positions.

    Runs `run_xs_backtest` once to recover the pre-governor return series (so the
    governor is identical to the validated book), then scales the next-period
    per-leg leverage by the next-period governor. Active (non-NaN) legs only.
    """
    res = run_xs_backtest(closes, fundings, cfg)
    pre = pd.Series(res.pre_governor_return, index=res.daily_index)
    g_next = next_period_governor(pre, cfg)
    lev = next_period_leverage(closes, cfg)
    forecast_latest = xs_demeaned_forecasts(closes, cfg).iloc[-1]

    positions: list[TargetPosition] = []
    gross = 0.0
    net = 0.0
    for sym in sorted(lev.index):
        raw = float(lev[sym])
        if not np.isfinite(raw):
            continue
        scaled = g_next * raw
        side = "long" if scaled > 0 else "short" if scaled < 0 else "flat"
        positions.append(
            TargetPosition(
                symbol=sym,
                side=side,
                leverage=scaled,
                notional_usd=scaled * capital,
                forecast=float(forecast_latest.get(sym, np.nan)),
            )
        )
        gross += abs(scaled)
        net += scaled

    last = pd.Timestamp(res.daily_index[-1])
    return TargetBook(
        as_of_date=last.date().isoformat(),
        next_period_date=(last + pd.Timedelta(days=1)).date().isoformat(),
        capital=capital,
        governor=g_next,
        active_count=len(positions),
        gross_leverage=gross,
        net_leverage=net,
        positions=positions,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/xsmom/test_live.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add analytics/xsmom/live.py tests/xsmom/test_live.py
git commit -m "feat(xsmom): TargetBook + build_target_book (governor-scaled next-period positions)"
```

---

### Task 4: Snapshot serialization + position deltas

**Files:**

- Modify: `analytics/xsmom/live.py`
- Test: `tests/xsmom/test_live.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/xsmom/test_live.py` (extend the `analytics.xsmom.live` import to add `position_deltas`, `target_book_from_dict`, `target_book_to_dict`):

```python
import json


def test_snapshot_round_trip() -> None:
    closes = _make_closes()
    book = build_target_book(closes, _make_fundings(closes), ForecastConfig(), 10_000.0)
    d = target_book_to_dict(book)
    assert json.loads(json.dumps(d)) == d  # JSON-serializable, stable
    book2 = target_book_from_dict(d)
    assert target_book_to_dict(book2) == d


def test_position_deltas() -> None:
    closes = _make_closes()
    book = build_target_book(closes, _make_fundings(closes), ForecastConfig(), 10_000.0)
    same = target_book_to_dict(book)
    deltas = position_deltas(book, same)
    assert all(abs(v) < 1e-9 for v in deltas.values())
    assert position_deltas(book, None) == {
        p.symbol: p.notional_usd for p in book.positions
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/xsmom/test_live.py -k "snapshot or deltas" -v`
Expected: FAIL with `ImportError: cannot import name 'target_book_to_dict'`

- [ ] **Step 3: Write minimal implementation**

Add to `analytics/xsmom/live.py`:

```python
def target_book_to_dict(book: TargetBook) -> dict[str, Any]:
    """Plain JSON-serializable dict for the gitignored daily snapshot."""
    return {
        "as_of_date": book.as_of_date,
        "next_period_date": book.next_period_date,
        "capital": book.capital,
        "governor": book.governor,
        "active_count": book.active_count,
        "gross_leverage": book.gross_leverage,
        "net_leverage": book.net_leverage,
        "positions": [
            {
                "symbol": p.symbol,
                "side": p.side,
                "leverage": p.leverage,
                "notional_usd": p.notional_usd,
                "forecast": p.forecast,
            }
            for p in book.positions
        ],
    }


def target_book_from_dict(d: dict[str, Any]) -> TargetBook:
    """Inverse of `target_book_to_dict`."""
    return TargetBook(
        as_of_date=d["as_of_date"],
        next_period_date=d["next_period_date"],
        capital=d["capital"],
        governor=d["governor"],
        active_count=d["active_count"],
        gross_leverage=d["gross_leverage"],
        net_leverage=d["net_leverage"],
        positions=[
            TargetPosition(
                symbol=p["symbol"],
                side=p["side"],
                leverage=p["leverage"],
                notional_usd=p["notional_usd"],
                forecast=p["forecast"],
            )
            for p in d["positions"]
        ],
    )


def position_deltas(
    book: TargetBook, prev: dict[str, Any] | None
) -> dict[str, float]:
    """Δ notional_usd per symbol vs a prior snapshot dict (None -> all current)."""
    prev_notional: dict[str, float] = {}
    if prev is not None:
        prev_notional = {p["symbol"]: p["notional_usd"] for p in prev["positions"]}
    cur_notional = {p.symbol: p.notional_usd for p in book.positions}
    out: dict[str, float] = {}
    for sym in set(cur_notional) | set(prev_notional):
        out[sym] = cur_notional.get(sym, 0.0) - prev_notional.get(sym, 0.0)
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/xsmom/test_live.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add analytics/xsmom/live.py tests/xsmom/test_live.py
git commit -m "feat(xsmom): target-book snapshot serialization + position deltas"
```

---

### Task 5: DB front door — `replay_targets`

**Files:**

- Modify: `analytics/xsmom/replay.py`
- Test: `tests/xsmom/test_targets_replay.py`

- [ ] **Step 1: Write the failing test**

Create `tests/xsmom/test_targets_replay.py`:

```python
from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.store.market_data import upsert_ohlcv
from analytics.store.schema import init_schema
from analytics.xsmom.replay import replay_targets

_DAY = 86_400_000


def _seed(conn: duckdb.DuckDBPyConnection, n: int = 400) -> list[str]:
    rng = np.random.default_rng(3)
    start = 1_609_459_200_000  # 2021-01-01 UTC
    syms = ["AAAUSDT", "BBBUSDT", "CCCUSDT"]
    for i, sym in enumerate(syms):
        steps = rng.normal(0.0005 * (i - 1), 0.02, n)
        close = 100.0 * np.exp(np.cumsum(steps))
        rows = pd.DataFrame(
            {
                "symbol": sym,
                "timeframe": "1d",
                "open_time": [start + k * _DAY for k in range(n)],
                "open": close,
                "high": close * 1.01,
                "low": close * 0.99,
                "close": close,
                "volume": 1000.0,
                "taker_buy_volume": 500.0,
            }
        )
        upsert_ohlcv(conn, rows)
    return syms


def test_replay_targets_builds_book() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    syms = _seed(conn)
    book = replay_targets(conn, ForecastConfig(), 10_000.0, symbols=syms)
    assert book.capital == 10_000.0
    assert book.active_count >= 1
    assert len(book.positions) == book.active_count
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/xsmom/test_targets_replay.py -v`
Expected: FAIL with `ImportError: cannot import name 'replay_targets'`

- [ ] **Step 3: Write minimal implementation**

Add to `analytics/xsmom/replay.py` (add the import `from analytics.xsmom.live import TargetBook, build_target_book` near the other `analytics.xsmom` imports):

```python
def replay_targets(
    conn: duckdb.DuckDBPyConnection,
    cfg: ForecastConfig,
    capital: float,
    symbols: list[str] | None = None,
) -> TargetBook:
    """Load the universe's 1d inputs and build today's XS target book (read-only)."""
    syms = symbols if symbols is not None else load_universe()
    closes, fundings = load_daily_inputs(conn, syms)
    return build_target_book(closes, fundings, cfg, capital)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/xsmom/test_targets_replay.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add analytics/xsmom/replay.py tests/xsmom/test_targets_replay.py
git commit -m "feat(xsmom): replay_targets DB front door for the live target book"
```

---

### Task 6: Exports + read-only tool driver

**Files:**

- Modify: `analytics/xsmom/__init__.py`
- Create: `tools/xsmom_targets.py`
- Test: `tests/xsmom/test_targets_cli.py`

- [ ] **Step 1: Write the failing test**

Create `tests/xsmom/test_targets_cli.py`:

```python
from __future__ import annotations

import sys

import duckdb
import numpy as np
import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.store.market_data import upsert_ohlcv
from analytics.store.schema import init_schema
from analytics.xsmom.live import build_target_book, position_deltas
from tools.xsmom_targets import (
    format_target_table,
    load_latest_snapshot,
    main,
    write_snapshot,
)

_DAY = 86_400_000
_SYMS = ["AAAUSDT", "BBBUSDT", "CCCUSDT"]


def _make_closes(n: int = 400) -> dict[str, pd.Series]:
    idx = pd.date_range("2021-01-01", periods=n, freq="D", tz="UTC")
    rng = np.random.default_rng(5)
    return {
        sym: pd.Series(100.0 * np.exp(np.cumsum(rng.normal(0.0005 * (i - 1), 0.02, n))), index=idx)
        for i, sym in enumerate(_SYMS)
    }


def _seed_db(path: str, n: int = 400) -> None:
    conn = duckdb.connect(path)
    init_schema(conn)
    rng = np.random.default_rng(5)
    start = 1_609_459_200_000
    for i, sym in enumerate(_SYMS):
        close = 100.0 * np.exp(np.cumsum(rng.normal(0.0005 * (i - 1), 0.02, n)))
        upsert_ohlcv(
            conn,
            pd.DataFrame(
                {
                    "symbol": sym,
                    "timeframe": "1d",
                    "open_time": [start + k * _DAY for k in range(n)],
                    "open": close,
                    "high": close * 1.01,
                    "low": close * 0.99,
                    "close": close,
                    "volume": 1000.0,
                    "taker_buy_volume": 500.0,
                }
            ),
        )
    conn.close()


def test_format_and_snapshot_round_trip(tmp_path) -> None:
    closes = _make_closes()
    fundings = {s: pd.Series(0.0, index=c.index) for s, c in closes.items()}
    book = build_target_book(closes, fundings, ForecastConfig(), 10_000.0)
    txt = format_target_table(book, position_deltas(book, None))
    assert "XS target positions" in txt
    path = write_snapshot(book, tmp_path)
    assert path.exists()
    loaded = load_latest_snapshot(tmp_path)
    assert loaded is not None
    assert loaded["next_period_date"] == book.next_period_date


def test_load_latest_snapshot_empty_dir(tmp_path) -> None:
    assert load_latest_snapshot(tmp_path) is None


def test_main_prints_and_writes_snapshot(tmp_path, capsys, monkeypatch) -> None:
    db = tmp_path / "a.db"
    _seed_db(str(db))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "xsmom_targets",
            "--db",
            str(db),
            "--symbols",
            ",".join(_SYMS),
            "--snapshot-dir",
            str(tmp_path),
            "--capital",
            "10000",
        ],
    )
    main()
    out = capsys.readouterr().out
    assert "XS target positions" in out
    assert list(tmp_path.glob("*.json"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/xsmom/test_targets_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tools.xsmom_targets'`

- [ ] **Step 3: Write minimal implementation**

First, add the new exports to `analytics/xsmom/__init__.py` — insert this import block after the existing `from analytics.xsmom.execution import (...)` block:

```python
from analytics.xsmom.live import (
    TargetBook,
    TargetPosition,
    build_target_book,
    next_period_governor,
    next_period_leverage,
    position_deltas,
    reconcile,
    target_book_from_dict,
    target_book_to_dict,
)
```

and add to `analytics/xsmom/replay.py`'s consumers by adding `replay_targets` to the `__init__.py` `from analytics.xsmom.replay import (...)` block. Then add these names to `__all__` (keep it alphabetized): `"TargetBook"`, `"TargetPosition"`, `"build_target_book"`, `"next_period_governor"`, `"next_period_leverage"`, `"position_deltas"`, `"reconcile"`, `"replay_targets"`, `"target_book_from_dict"`, `"target_book_to_dict"`.

Then create `tools/xsmom_targets.py`:

```python
"""XS-solo daily target-position generator (P3 sub-project #3, slice 1) — read-only.

Computes today's governor-scaled XS target positions (side, leverage, $notional)
from the local `analytics.db`, prints a table, and appends a gitignored JSON
snapshot. The emitted targets equal the validated +1.375 book's next-bar
positions. No order routing.

Run `buibui analytics sync --universe` first to refresh the 1d bars.

Usage::

    PYTHONPATH=. poetry run python tools/xsmom_targets.py
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import duckdb

from analytics.forecast.config import ForecastConfig
from analytics.store import DEFAULT_DB_PATH
from analytics.universe import load_universe
from analytics.xsmom.live import (
    TargetBook,
    position_deltas,
    reconcile,
    target_book_to_dict,
)
from analytics.xsmom.replay import replay_targets
from analytics.forecast.replay import load_daily_inputs

_DEFAULT_SNAPSHOT_DIR = Path("docs/plans/xsmom_targets")


def format_target_table(book: TargetBook, deltas: dict[str, float]) -> str:
    """Render the target book as a fixed-width terminal table (pure)."""
    lines = [
        f"XS target positions — as_of {book.as_of_date} → hold "
        f"{book.next_period_date}   capital ${book.capital:,.0f}",
        f"{'SYM':<12}{'SIDE':<7}{'LEV':>8}{'$NOTIONAL':>14}{'Δ$ vs prev':>14}",
    ]
    for p in sorted(book.positions, key=lambda x: -abs(x.leverage)):
        lines.append(
            f"{p.symbol:<12}{p.side:<7}{p.leverage:>+8.3f}"
            f"{p.notional_usd:>+14,.0f}{deltas.get(p.symbol, 0.0):>+14,.0f}"
        )
    lines.append(
        f"governor g={book.governor:.2f}   active={book.active_count}   "
        f"gross={book.gross_leverage:.2f}   net={book.net_leverage:+.2f}"
    )
    return "\n".join(lines)


def write_snapshot(book: TargetBook, snapshot_dir: Path) -> Path:
    """Write the book to `<snapshot_dir>/<next_period_date>.json`."""
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    path = snapshot_dir / f"{book.next_period_date}.json"
    path.write_text(json.dumps(target_book_to_dict(book), indent=2))
    return path


def load_latest_snapshot(snapshot_dir: Path) -> dict[str, Any] | None:
    """Most recent snapshot dict by filename, or None if the dir is empty."""
    if not snapshot_dir.exists():
        return None
    files = sorted(snapshot_dir.glob("*.json"))
    if not files:
        return None
    return json.loads(files[-1].read_text())  # type: ignore[no-any-return]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="DuckDB path")
    parser.add_argument("--capital", type=float, default=10_000.0, help="Account capital USD")
    parser.add_argument("--config", type=Path, default=None, help="Optional TOML for ForecastConfig")
    parser.add_argument("--symbols", type=str, default=None, help="Comma-separated override of the universe")
    parser.add_argument("--snapshot-dir", type=Path, default=_DEFAULT_SNAPSHOT_DIR)
    parser.add_argument("--no-snapshot", action="store_true", help="Skip writing the snapshot")
    parser.add_argument("--reconcile", action="store_true", help="Print frozen-clock reconcile diff and exit")
    args = parser.parse_args()

    cfg = ForecastConfig.from_toml(args.config) if args.config else ForecastConfig()
    symbols = args.symbols.split(",") if args.symbols else load_universe()
    conn = duckdb.connect(str(args.db), read_only=True)

    if args.reconcile:
        closes, _ = load_daily_inputs(conn, symbols)
        union = next(iter(closes.values())).index
        cutoff = union[-5]
        diff = reconcile(closes, cfg, cutoff)
        print(f"reconcile @ {cutoff.date().isoformat()}: max abs diff = {diff:.3e}")
        return

    book = replay_targets(conn, cfg, args.capital, symbols=symbols)
    prev = load_latest_snapshot(args.snapshot_dir)
    print(format_target_table(book, position_deltas(book, prev)))
    if not args.no_snapshot:
        path = write_snapshot(book, args.snapshot_dir)
        print(f"\nsnapshot: {path}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/xsmom/test_targets_cli.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add analytics/xsmom/__init__.py tools/xsmom_targets.py tests/xsmom/test_targets_cli.py
git commit -m "feat(xsmom): read-only daily target-position tool + package exports"
```

---

### Task 7: Makefile target, docs, and full DoD gate

**Files:**

- Modify: `Makefile`
- Modify: `README.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add the Makefile target**

Append after the `buibui-xsmom-capacity-audit` target (around `Makefile:276`):

```makefile
.PHONY: buibui-xsmom-targets
buibui-xsmom-targets:  ## P3: read-only daily XS target positions (run buibui-analytics-sync first)
    PYTHONPATH=. poetry run python tools/xsmom_targets.py
```

The recipe line above MUST be **tab-indented** in the real Makefile (shown with
spaces here only to satisfy markdownlint). Add `buibui-xsmom-targets` to the
`.PHONY` list on line 14 (append to the end of the existing space-separated list).

- [ ] **Step 2: Verify the target is wired**

Run: `make help 2>/dev/null | grep xsmom-targets || grep -n "buibui-xsmom-targets" Makefile`
Expected: the new target appears.

- [ ] **Step 3: Document in README.md and CLAUDE.md**

In `README.md`, find the section listing the `tools/` scripts / `make buibui-xsmom-*` targets and add a line:

```markdown
- `make buibui-xsmom-targets` — read-only daily XS target-position generator
  (`tools/xsmom_targets.py`): today's governor-scaled target positions
  (side · leverage · $notional at ~$10k) + a gitignored snapshot. Run
  `buibui analytics sync --universe` first. No order routing.
```

In `CLAUDE.md`, under the `xsmom/` package bullet, append a sentence after the capacity-test description:

```markdown
**Live wiring (deploy-hardening sub-project #3, slice 1):** new pure/causal/read-only
`live.py` (`next_period_leverage` — the position to hold during the NEXT bar from the
latest causal forecast, NOT the `.shift(1)` last `xs_leverage` row; `next_period_governor`;
`build_target_book`→`TargetBook`/`TargetPosition`; snapshot `target_book_to_dict`/`_from_dict`;
`position_deltas`; `reconcile` — frozen-clock `next_period_leverage(through T) ==
xs_leverage(through T+1)[T+1]`, the backtest↔live + no-look-ahead proof) + `replay.replay_targets`
DB front door + `tools/xsmom_targets.py` (`make buibui-xsmom-targets`; governor-scaled target
table + gitignored `docs/plans/xsmom_targets/<date>.json` snapshot; read-only, no order routing).
```

Also add `tools/xsmom_targets.py` to the `tools/` list in CLAUDE.md mirroring the other driver entries.

- [ ] **Step 4: Run the full DoD gate**

Run each and confirm green:

```bash
make lint-py
make typecheck
make test
make test-regression
make lint-md
```

Expected: lint-py ✓, typecheck ✓ (mypy strict), `make test` green (all new tests pass, ~+14), `make test-regression` goldens **UNMOVED** (no engine change), lint-md ✓.

- [ ] **Step 5: Commit**

```bash
git add Makefile README.md CLAUDE.md
git commit -m "build(xsmom): make buibui-xsmom-targets + docs for the live target generator"
```

---

## Self-review notes

- **Spec coverage:** next-period leverage (Task 1) · reconciliation/no-look-ahead (Task 1) · governor (Task 2) · build_target_book governor-scaled + dates (Task 3) · snapshot + deltas (Task 4) · sync-then-read DB path (Task 5 `replay_targets`; sync is the operator/Make step) · terminal table + gitignored snapshot (Task 6) · make target + docs (Task 7) · DoD incl. goldens unmoved (Task 7). All spec sections map to a task.
- **Engine untouched:** `run_xs_backtest` and `xs_leverage` are read, never modified → `make test-regression` goldens must stay UNMOVED. If they move, something leaked into the engine — stop and investigate.
- **Type consistency:** `TargetBook`/`TargetPosition` field names are identical across `build_target_book`, `target_book_to_dict`/`_from_dict`, `position_deltas`, and `format_target_table`. `replay_targets(conn, cfg, capital, symbols=None)` signature is used identically in the tool and its test.
- **Commit-failure gotcha:** do not pipe `git commit` through `tail`/`head` — a pre-commit auto-fix can abort the commit while the pipe still prints success. After each commit, verify with `git log --oneline -1`.
