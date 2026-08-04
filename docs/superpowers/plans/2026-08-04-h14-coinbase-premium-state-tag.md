# H14 Coinbase-Premium State Tag — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Test whether a BTC-level Coinbase-premium market state conditions the avg_r of this system's existing trades, and emit a pre-committed BUILD / AVOID / NO-EDGE / INSUFFICIENT verdict.

**Architecture:** Three free daily series (Coinbase `BTC-USD`, Binance spot `BTCUSDT`, Coinbase `USDT-USD`) land in one additive DuckDB table. A pure library builds the peg-adjusted / raw / peg-deviation series, labels each day with a **causal** 90-day z-score state, collapses tagged trades to **one observation per UTC day**, and feeds cells to the existing `analytics/audit_guard.py`. A read-only tool prints the verdict table.

**Tech Stack:** Python 3.11, pandas, numpy, DuckDB, pytest. No new dependencies.

**Spec (read it first — it holds the pre-committed gate):** `docs/superpowers/specs/2026-08-04-h14-coinbase-premium-state-tag-design.md`

## Global Constraints

- **Definition of Done, every task:** `make lint-py` ✓ · `make typecheck` ✓ (mypy strict) · `make test` green. `make test-regression` is required only for Task 1 (schema touch) and must show goldens **unmoved** — this change is additive and must not move them.
- **All functions annotated**, including `-> None` on tests. mypy strict.
- **Tests make no network calls.** Fetchers take an injected `get` callable (the `tools/x_fetch.py` / `tools/video_fetch.py` pattern).
- **Analytics tests use `duckdb.connect(":memory:")`** — never the real `analytics.db`.
- **`analytics/venue_premium.py` is PURE**: no DB, no network, no file IO. This is what makes it testable and is non-negotiable.
- **Never modify `_upsert` in `analytics/store/_common.py`.** Its explicit `register`/`unregister` in try/finally prevents DuckDB heap corruption. Call it; do not reshape it.
- **No new detector, no new sleeve, no entry trigger.** The frozen-detector list stands (spec §8).
- Conventional commits (`feat:`, `test:`, `docs:`). Branch `feat/h14-coinbase-premium-state`.

## File Structure

| File | Responsibility |
| --- | --- |
| `analytics/store/schema.py` | +1 additive `CREATE TABLE IF NOT EXISTS venue_spot_daily` |
| `analytics/store/venue_prices.py` | `upsert_venue_spot_daily` / `get_venue_spot_daily` |
| `analytics/venue_fetch.py` | Coinbase + Binance-spot daily fetchers, injected `get`, paging |
| `analytics/venue_premium.py` | **pure**: series → causal z → states → per-day collapse → cells → verdict |
| `tools/premium_state_audit.py` | read-only DB front door + report |
| `tests/test_venue_premium.py`, `tests/test_venue_fetch.py`, `tests/test_store_venue_prices.py` | unit tests |

---

### Task 1: Additive store for venue spot dailies

**Files:**

- Modify: `analytics/store/schema.py` (append a `CREATE TABLE` after the `open_interest` block, ~line 46)
- Create: `analytics/store/venue_prices.py`
- Test: `tests/test_store_venue_prices.py`

**Interfaces:**

- Consumes: `analytics.store._common._upsert`, `DEFAULT_DB_PATH`
- Produces: `upsert_venue_spot_daily(conn, df) -> None` where `df` has columns `venue, symbol, open_time, close`; `get_venue_spot_daily(conn, venue, symbol) -> pd.DataFrame` returning those columns sorted by `open_time` ascending.

- [ ] **Step 1: Write the failing test**

```python
import duckdb
import pandas as pd

from analytics.store.schema import init_schema
from analytics.store.venue_prices import get_venue_spot_daily, upsert_venue_spot_daily


def test_upsert_and_read_roundtrip_is_idempotent() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    df = pd.DataFrame(
        {
            "venue": ["coinbase", "coinbase", "binance"],
            "symbol": ["BTC-USD", "BTC-USD", "BTCUSDT"],
            "open_time": [1_600_000_000_000, 1_600_086_400_000, 1_600_000_000_000],
            "close": [10_000.0, 10_500.0, 9_990.0],
        }
    )
    upsert_venue_spot_daily(conn, df)
    upsert_venue_spot_daily(conn, df)  # idempotent: PK replace, not duplicate

    out = get_venue_spot_daily(conn, "coinbase", "BTC-USD")
    assert list(out["open_time"]) == [1_600_000_000_000, 1_600_086_400_000]
    assert list(out["close"]) == [10_000.0, 10_500.0]
    assert len(get_venue_spot_daily(conn, "binance", "BTCUSDT")) == 1
    assert get_venue_spot_daily(conn, "coinbase", "NOPE-USD").empty
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_store_venue_prices.py -v`
Expected: FAIL — `ModuleNotFoundError: analytics.store.venue_prices`

- [ ] **Step 3: Add the table to `init_schema`**

Insert immediately after the `open_interest` `conn.execute("""…""")` block:

```python
    conn.execute("""
        CREATE TABLE IF NOT EXISTS venue_spot_daily (
            venue     TEXT   NOT NULL,
            symbol    TEXT   NOT NULL,
            open_time BIGINT NOT NULL,
            close     DOUBLE NOT NULL,
            PRIMARY KEY (venue, symbol, open_time)
        )
    """)
```

- [ ] **Step 4: Write `analytics/store/venue_prices.py`**

```python
"""Venue spot daily closes — additive store for the H14 premium state tag."""

from __future__ import annotations

import duckdb
import pandas as pd

from analytics.store._common import _upsert

_COLUMNS = "venue, symbol, open_time, close"


def upsert_venue_spot_daily(conn: duckdb.DuckDBPyConnection, df: pd.DataFrame) -> None:
    """Insert-or-replace daily closes keyed on (venue, symbol, open_time)."""
    _upsert(conn, df, "venue_spot_daily", _COLUMNS)


def get_venue_spot_daily(
    conn: duckdb.DuckDBPyConnection, venue: str, symbol: str
) -> pd.DataFrame:
    """All stored closes for one (venue, symbol), ascending by open_time."""
    return conn.execute(
        f"SELECT {_COLUMNS} FROM venue_spot_daily "
        "WHERE venue = ? AND symbol = ? ORDER BY open_time",
        [venue, symbol],
    ).df()
```

- [ ] **Step 5: Run tests**

Run: `poetry run pytest tests/test_store_venue_prices.py -v`
Expected: PASS

- [ ] **Step 6: Prove the schema change moved no goldens**

Run: `make lint-py && make typecheck && make test && make test-regression`
Expected: all green, regression goldens **unmoved**. If a golden moves, STOP — an additive table cannot legitimately move a backtest golden, so something else is wrong.

- [ ] **Step 7: Commit**

```bash
git add analytics/store/schema.py analytics/store/venue_prices.py tests/test_store_venue_prices.py
git commit -m "feat(store): additive venue_spot_daily table for the H14 premium tag"
```

---

### Task 2: Network fetchers (injected `get`, paged)

**Files:**

- Create: `analytics/venue_fetch.py`
- Test: `tests/test_venue_fetch.py`

**Interfaces:**

- Produces: `fetch_coinbase_daily(product, start_ms, end_ms, *, get) -> pd.DataFrame` and `fetch_binance_spot_daily(symbol, start_ms, *, get) -> pd.DataFrame`, both returning columns `open_time, close` ascending, de-duplicated. `get: Callable[[str], Any]` returns already-parsed JSON.
- Coinbase caps at **300 candles/request** → page backwards in ≤300-day windows. Response rows are `[time_s, low, high, open, close, volume]`, newest first.
- Binance klines rows are `[open_time_ms, o, h, l, c, …]`, oldest first, `limit=1000`.

- [ ] **Step 1: Write the failing test**

```python
from typing import Any

import pandas as pd

from analytics.venue_fetch import fetch_binance_spot_daily, fetch_coinbase_daily

DAY = 86_400_000


def test_coinbase_pages_and_normalizes() -> None:
    calls: list[str] = []

    def fake_get(url: str) -> Any:
        calls.append(url)
        if "start=2020-01-01" in url:
            return [[1_577_923_200, 1.0, 2.0, 1.5, 7_100.0, 10.0]]  # 2020-01-02
        return [[1_577_836_800, 1.0, 2.0, 1.5, 7_000.0, 10.0]]  # 2020-01-01

    df = fetch_coinbase_daily("BTC-USD", 1_577_836_800_000, 1_578_009_600_000, get=fake_get)
    assert list(df.columns) == ["open_time", "close"]
    assert list(df["open_time"]) == [1_577_836_800_000, 1_577_923_200_000]
    assert list(df["close"]) == [7_000.0, 7_100.0]
    assert calls, "must actually call get"


def test_binance_stops_when_page_not_full() -> None:
    def fake_get(url: str) -> Any:
        return [[1_577_836_800_000, "1", "2", "0.5", "7000.0", "1"]]

    df = fetch_binance_spot_daily("BTCUSDT", 1_577_836_800_000, get=fake_get)
    assert list(df["close"]) == [7000.0]
    assert df["open_time"].dtype.kind == "i"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_venue_fetch.py -v`
Expected: FAIL — `ModuleNotFoundError: analytics.venue_fetch`

- [ ] **Step 3: Implement `analytics/venue_fetch.py`**

```python
"""Free, keyless daily-close fetchers for the H14 premium state tag.

``get`` is injected so the suite is network-free. Both venues are public REST;
no API key is used or required.
"""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pandas as pd

DAY_MS = 86_400_000
_COINBASE_MAX = 300
_BINANCE_LIMIT = 1000
Getter = Callable[[str], Any]


def http_get_json(url: str) -> Any:
    """Default real-network getter. Never used in tests."""
    req = urllib.request.Request(url, headers={"User-Agent": "buibui-research/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d")


def _frame(rows: list[tuple[int, float]]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["open_time", "close"])
    if df.empty:
        return df.astype({"open_time": "int64", "close": "float64"})
    df = df.drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)
    return df.astype({"open_time": "int64", "close": "float64"})


def fetch_coinbase_daily(
    product: str, start_ms: int, end_ms: int, *, get: Getter = http_get_json
) -> pd.DataFrame:
    """Daily closes for a Coinbase Exchange product, paged under the 300-bar cap."""
    rows: list[tuple[int, float]] = []
    cursor = start_ms
    while cursor < end_ms:
        window_end = min(cursor + _COINBASE_MAX * DAY_MS, end_ms)
        url = (
            f"https://api.exchange.coinbase.com/products/{product}/candles"
            f"?granularity=86400&start={_iso(cursor)}&end={_iso(window_end)}"
        )
        for row in get(url) or []:
            rows.append((int(row[0]) * 1000, float(row[4])))
        cursor = window_end
    return _frame(rows)


def fetch_binance_spot_daily(
    symbol: str, start_ms: int, *, get: Getter = http_get_json
) -> pd.DataFrame:
    """Daily closes for a Binance SPOT symbol from ``start_ms`` to now."""
    rows: list[tuple[int, float]] = []
    cursor = start_ms
    while True:
        url = (
            "https://api.binance.com/api/v3/klines"
            f"?symbol={symbol}&interval=1d&startTime={cursor}&limit={_BINANCE_LIMIT}"
        )
        page = get(url) or []
        for row in page:
            rows.append((int(row[0]), float(row[4])))
        if len(page) < _BINANCE_LIMIT:
            break
        cursor = int(page[-1][0]) + DAY_MS
    return _frame(rows)
```

- [ ] **Step 4: Run tests**

Run: `poetry run pytest tests/test_venue_fetch.py -v`
Expected: PASS

- [ ] **Step 5: DoD + commit**

```bash
make lint-py && make typecheck && make test
git add analytics/venue_fetch.py tests/test_venue_fetch.py
git commit -m "feat(analytics): keyless Coinbase + Binance-spot daily fetchers"
```

---

### Task 3: Pure series + causal state labelling

**Files:**

- Create: `analytics/venue_premium.py`
- Test: `tests/test_venue_premium.py`

**Interfaces:**

- Produces: `build_premium_series(cb_btc, bn_btc, cb_usdt) -> pd.DataFrame` with index `open_time` and columns `prem_raw, prem_adj, peg_dev`; `causal_zscore(s, window=90) -> pd.Series`; `label_levels(z) -> pd.Series` of `elevated|neutral|depressed`; `label_changes(s, span=5) -> pd.Series` of `rising|falling`.
- Consumed by Task 4.

- [ ] **Step 1: Write the failing tests (causality is the load-bearing one)**

```python
import numpy as np
import pandas as pd

from analytics.venue_premium import (
    build_premium_series,
    causal_zscore,
    label_levels,
)


def _series(vals: list[float]) -> pd.Series:
    return pd.Series(vals, index=range(len(vals)), dtype=float)


def test_premium_removes_the_peg_deviation() -> None:
    cb = _series([100.0])
    bn = _series([100.0])
    usdt = _series([0.99])
    out = build_premium_series(cb, bn, usdt)
    assert out["prem_raw"].iloc[0] == 0.0
    # peg-adjusted: 100 / (100 * 0.99) - 1 == +1.0101%
    assert abs(out["prem_adj"].iloc[0] - 0.010101) < 1e-5
    assert abs(out["peg_dev"].iloc[0] + 0.01) < 1e-12


def test_causal_zscore_never_uses_the_future() -> None:
    rng = np.random.default_rng(0)
    base = _series(list(rng.normal(size=200)))
    z = causal_zscore(base, window=90)

    bumped = base.copy()
    k = 150
    bumped.iloc[k] += 10.0
    z2 = causal_zscore(bumped, window=90)

    # Everything strictly BEFORE the perturbed day is untouched.
    pd.testing.assert_series_equal(z.iloc[:k], z2.iloc[:k])
    # And the perturbation IS visible at k, proving the test can fail.
    assert z.iloc[k] != z2.iloc[k]


def test_label_levels_uses_the_pre_registered_thresholds() -> None:
    z = _series([-2.0, -1.0, 0.0, 1.0, 2.0, float("nan")])
    out = label_levels(z)
    assert list(out[:5]) == [
        "depressed", "depressed", "neutral", "elevated", "elevated",
    ]
    assert pd.isna(out.iloc[5])
```

- [ ] **Step 2: Run to verify failure**

Run: `poetry run pytest tests/test_venue_premium.py -v`
Expected: FAIL — `ModuleNotFoundError: analytics.venue_premium`

- [ ] **Step 3: Implement the series half**

```python
"""H14 Coinbase-premium market-state tag — pure library.

Spec: docs/superpowers/specs/2026-08-04-h14-coinbase-premium-state-tag-design.md

PURE: no DB, no network, no file IO. Every threshold here is a-priori and was
written down in the spec before any avg_r was computed.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

Z_WINDOW = 90
Z_THRESHOLD = 1.0
CHANGE_SPAN = 5

LEVEL_ELEVATED = "elevated"
LEVEL_NEUTRAL = "neutral"
LEVEL_DEPRESSED = "depressed"
CHANGE_RISING = "rising"
CHANGE_FALLING = "falling"


def build_premium_series(
    cb_btc: pd.Series, bn_btc: pd.Series, cb_usdt: pd.Series
) -> pd.DataFrame:
    """The three pre-registered series, aligned on their shared index.

    ``prem_adj`` divides out the USDT peg, which the 2026-08-04 probe measured
    as accounting for essentially the whole raw premium at that instant.
    """
    idx = cb_btc.index.intersection(bn_btc.index)
    cb, bn = cb_btc.reindex(idx), bn_btc.reindex(idx)
    usdt = cb_usdt.reindex(idx)
    return pd.DataFrame(
        {
            "prem_raw": cb / bn - 1.0,
            "prem_adj": cb / (bn * usdt) - 1.0,
            "peg_dev": usdt - 1.0,
        },
        index=idx,
    )


def causal_zscore(series: pd.Series, window: int = Z_WINDOW) -> pd.Series:
    """z of today's value against the ``window`` days STRICTLY before it.

    The ``.shift(1)`` is the causality guarantee and is asserted by a
    perturbation test — do not remove it as a "warm-up" convenience.
    """
    prior = series.shift(1)
    mean = prior.rolling(window, min_periods=window).mean()
    std = prior.rolling(window, min_periods=window).std(ddof=1)
    return (series - mean) / std.replace(0.0, np.nan)


def label_levels(z: pd.Series) -> pd.Series:
    """Pre-registered ±1.0 thresholds. NaN (warm-up) stays NaN, never 'neutral'."""
    out = pd.Series(np.where(z >= Z_THRESHOLD, LEVEL_ELEVATED, LEVEL_NEUTRAL), index=z.index)
    out = out.where(z > -Z_THRESHOLD, LEVEL_DEPRESSED)
    return out.where(z.notna(), other=np.nan)


def label_changes(series: pd.Series, span: int = CHANGE_SPAN) -> pd.Series:
    """Sign of the ``span``-day change. NaN warm-up preserved."""
    delta = series.diff(span)
    out = pd.Series(np.where(delta >= 0.0, CHANGE_RISING, CHANGE_FALLING), index=series.index)
    return out.where(delta.notna(), other=np.nan)
```

- [ ] **Step 4: Run tests**

Run: `poetry run pytest tests/test_venue_premium.py -v`
Expected: PASS

- [ ] **Step 5: DoD + commit**

```bash
make lint-py && make typecheck && make test
git add analytics/venue_premium.py tests/test_venue_premium.py
git commit -m "feat(analytics): causal premium series + a-priori state labels (H14)"
```

---

### Task 4: Per-day collapse, cells, and the pre-committed verdict

**Files:**

- Modify: `analytics/venue_premium.py` (append)
- Test: `tests/test_venue_premium.py` (append)

**Interfaces:**

- Consumes: Task 3's labellers; `analytics.audit_guard.AuditCell`, `evaluate_audit_cells`, `CellVerdict`.
- Produces: `collapse_to_daily(trades, states) -> pd.DataFrame` with columns `day, direction, mean_r, state`; `build_state_cells(daily) -> list[AuditCell]`; `evaluate_premium_states(cells) -> list[tuple[str, str]]` mapping each label to a H14 verdict string.
- `trades` input columns: `entry_time` (ms), `direction` (`long`/`short`), `pnl_r` (float).

**The sign inversion is the single highest-risk item in this plan.** `audit_guard` answers a *suppression* question, so `DISABLE` means the slice is reliably **good**. It has already caused two defects in this repo (ST9, H8). The test below is the guard.

- [ ] **Step 1: Write the failing tests**

```python
from analytics.audit_guard import AuditCell
from analytics.venue_premium import (
    VERDICT_AVOID,
    VERDICT_BUILD,
    VERDICT_NO_EDGE,
    build_state_cells,
    collapse_to_daily,
    evaluate_premium_states,
)

DAY = 86_400_000


def test_collapse_gives_one_observation_per_day_not_per_trade() -> None:
    # 4 trades, 2 days, one direction -> 2 observations, NOT 4.
    trades = pd.DataFrame(
        {
            "entry_time": [DAY * 100 + 1, DAY * 100 + 2, DAY * 101 + 1, DAY * 101 + 2],
            "direction": ["long"] * 4,
            "pnl_r": [1.0, 3.0, -1.0, -3.0],
        }
    )
    states = pd.Series({100: "elevated", 101: "depressed"})
    out = collapse_to_daily(trades, states)
    assert len(out) == 2
    assert sorted(out["mean_r"]) == [-2.0, 2.0]
    assert set(out["state"]) == {"elevated", "depressed"}


def test_sign_inversion_is_mapped_the_right_way_round() -> None:
    # A reliably POSITIVE slice must come back as BUILD, not AVOID.
    good = AuditCell(label="elevated|long", supp_r=[0.5] * 40 + [0.4] * 40, kept_r=[0.0] * 80)
    bad = AuditCell(label="depressed|long", supp_r=[-0.5] * 40 + [-0.4] * 40, kept_r=[0.0] * 80)
    verdicts = dict(evaluate_premium_states([good, bad]))
    assert verdicts["elevated|long"] == VERDICT_BUILD
    assert verdicts["depressed|long"] == VERDICT_AVOID


def test_underpowered_cell_is_insufficient_not_no_edge() -> None:
    thin = AuditCell(label="elevated|short", supp_r=[0.5] * 5, kept_r=[0.0] * 5)
    assert dict(evaluate_premium_states([thin]))["elevated|short"] != VERDICT_NO_EDGE
```

- [ ] **Step 2: Run to verify failure**

Run: `poetry run pytest tests/test_venue_premium.py -v`
Expected: FAIL — `ImportError: cannot import name 'collapse_to_daily'`

- [ ] **Step 3: Append the implementation**

```python
from analytics.audit_guard import (
    DECISION_DISABLE,
    DECISION_ENABLE,
    DECISION_INSUFFICIENT,
    AuditCell,
    evaluate_audit_cells,
)

VERDICT_BUILD = "BUILD"
VERDICT_AVOID = "AVOID"
VERDICT_NO_EDGE = "NO-EDGE"
VERDICT_INSUFFICIENT = "INSUFFICIENT"

# audit_guard answers a SUPPRESSION question, so its decisions are inverted
# relative to "is this state good". Verified in analytics/audit_guard.py:1-20.
# This inversion caused defects in ST9 and H8 — the mapping is asserted by a test.
_VERDICT_MAP = {
    DECISION_DISABLE: VERDICT_BUILD,      # slice reliably POSITIVE
    DECISION_ENABLE: VERDICT_AVOID,       # slice reliably NEGATIVE
    DECISION_INSUFFICIENT: VERDICT_INSUFFICIENT,
}


def collapse_to_daily(trades: pd.DataFrame, states: pd.Series) -> pd.DataFrame:
    """One observation per (UTC day, direction) — the H10 independence lesson.

    25 symbols x 4 timeframes sharing one market-wide daily state are nowhere
    near 100 independent draws, so the trade-level view is not the unit of
    inference. ``states`` is indexed by day number (``entry_time // 86_400_000``).
    """
    if trades.empty:
        return pd.DataFrame(columns=["day", "direction", "mean_r", "state"])
    df = trades.copy()
    df["day"] = (df["entry_time"] // 86_400_000).astype("int64")
    daily = df.groupby(["day", "direction"], as_index=False)["pnl_r"].mean()
    daily = daily.rename(columns={"pnl_r": "mean_r"})
    daily["state"] = daily["day"].map(states)
    return daily.dropna(subset=["state"]).reset_index(drop=True)


def build_state_cells(daily: pd.DataFrame) -> list[AuditCell]:
    """One cell per (state, direction); ``kept_r`` is the same-direction complement."""
    cells: list[AuditCell] = []
    for direction in sorted(daily["direction"].unique()):
        side = daily[daily["direction"] == direction]
        for state in sorted(side["state"].unique()):
            inside = side[side["state"] == state]["mean_r"]
            outside = side[side["state"] != state]["mean_r"]
            cells.append(
                AuditCell(
                    label=f"{state}|{direction}",
                    supp_r=list(inside),
                    kept_r=list(outside),
                )
            )
    return cells


def evaluate_premium_states(cells: list[AuditCell]) -> list[tuple[str, str]]:
    """Pre-committed verdict per cell, one shared Holm family (spec section 7)."""
    verdicts = evaluate_audit_cells(cells, bar=0.05, alpha=0.05, min_n=30)
    return [
        (cell.label, _VERDICT_MAP.get(v.decision, VERDICT_NO_EDGE))
        for cell, v in zip(cells, verdicts, strict=True)
    ]
```

- [ ] **Step 4: Run tests**

Run: `poetry run pytest tests/test_venue_premium.py -v`
Expected: PASS. If `test_sign_inversion_is_mapped_the_right_way_round` fails, **do not flip the assertion** — re-read `analytics/audit_guard.py`'s module docstring and fix `_VERDICT_MAP`.

- [ ] **Step 5: DoD + commit**

```bash
make lint-py && make typecheck && make test
git add analytics/venue_premium.py tests/test_venue_premium.py
git commit -m "feat(analytics): per-day collapse + pre-committed H14 verdict mapping"
```

---

### Task 5: Audit driver + Makefile target + docs

**Files:**

- Create: `tools/premium_state_audit.py`
- Modify: `Makefile` (new `buibui-premium-state-audit` target, beside the other `buibui-*-audit` targets)
- Modify: `CLAUDE.md` (one `tools/` bullet + one `analytics/` bullet)

**Interfaces:**

- Consumes: everything above, plus `analytics.store.schema.DEFAULT_DB_PATH`.
- CLI: `--db` (default `analytics.db`), `--series {prem_adj,prem_raw,peg_dev}` (default `prem_adj`), `--source {backtest,live,both}` (default `both`), `--refresh` (fetch + upsert before auditing), `--min-n` (default 30).

- [ ] **Step 1: Write the driver**

Open the DB **read-only** unless `--refresh` is passed — mirrors every other audit tool (`duckdb.connect(str(db), read_only=True)`). Then:

1. If `--refresh`: fetch the three series via Task 2, `upsert_venue_spot_daily`, reopen read-only.
2. Load closes with `get_venue_spot_daily` for `("coinbase","BTC-USD")`, `("binance","BTCUSDT")`, `("coinbase","USDT-USD")`; index each by `open_time // 86_400_000`.
3. `build_premium_series` → pick `--series` → `causal_zscore` → `label_levels`; separately `label_changes`.
4. Load trades. Backtest leg:
   `SELECT entry_time, direction, pnl_r FROM backtest_trades WHERE pnl_r IS NOT NULL`.
   Live leg: `SELECT candle_ts_ms AS entry_time, direction, outcome_r AS pnl_r FROM signal_alert_outcomes WHERE outcome_r IS NOT NULL`.
5. `collapse_to_daily` → `build_state_cells` → `evaluate_premium_states`.
6. Print, per source and per axis: label · n_days · mean_r · CI · adjusted p · verdict. Print the **per-trade** mean alongside, labelled `(optimistic, decides nothing)`.
7. Print the early/late sign-agreement column (split the day index at its median).
8. Print a one-line header naming the series in use and its coverage window — `prem_adj` starts 2021-05-04 and a reader must not mistake it for full history.

- [ ] **Step 2: Verify it runs read-only end-to-end**

Run: `PYTHONPATH=. poetry run python tools/premium_state_audit.py --refresh --source both`
Expected: a verdict table. Sanity checks before believing any number:

- `prem_adj` coverage starts **2021-05-04**, not 2019.
- `elevated` and `depressed` each hold roughly 10–20% of days (±1σ on a causal z).
- n per cell is in the **hundreds of days**, not tens of thousands — if it is tens of thousands the per-day collapse is not being applied and every CI is wrong.

- [ ] **Step 3: Add the Makefile target**

<!-- markdownlint-disable MD010 -->

```makefile
# add near the other buibui-*-audit targets (the recipe line is a real TAB)
.PHONY: buibui-premium-state-audit
buibui-premium-state-audit:  ## H14: read-only Coinbase-premium market-state audit
	PYTHONPATH=. poetry run python tools/premium_state_audit.py $(ARGS)
```

<!-- markdownlint-enable MD010 -->

- [ ] **Step 4: Full DoD**

Run: `make lint-py && make typecheck && make test && make test-regression`
Expected: all green; goldens unmoved.

- [ ] **Step 5: Commit**

```bash
git add tools/premium_state_audit.py Makefile CLAUDE.md
git commit -m "feat(tools): H14 Coinbase-premium state audit driver"
```

---

### Task 6: Write the verdict document

**Files:**

- Create: `docs/audits/2026-08-04-h14-coinbase-premium-state-tag.md`

- [ ] **Step 1: Run the audit for all three series**

```bash
make buibui-premium-state-audit ARGS="--refresh --series prem_adj --source both"
make buibui-premium-state-audit ARGS="--series prem_raw --source both"
make buibui-premium-state-audit ARGS="--series peg_dev --source both"
```

- [ ] **Step 2: Apply spec section 8's decision rule, verbatim, without reinterpreting it**

Report the headline verdict, then which of the three pre-committed branches fired:
BUILD-and-replicates → live-gate/size-governor hypothesis; all NO-EDGE → the sixth
conditioning NO **and** what that licenses about the signal book; BUILD on
`prem_raw` only → re-file as stablecoin stress, not US demand.

**Report a negative honestly and prominently — a strategy that loses is a result.**
Include the per-day n, the coverage window, and the sign-inversion note so the next
reader can audit the audit.

- [ ] **Step 3: Commit**

```bash
git add docs/audits/2026-08-04-h14-coinbase-premium-state-tag.md
git commit -m "docs: H14 Coinbase-premium state tag verdict"
```

---

## Self-Review

**Spec coverage.** §3 data → Tasks 1–2. §4 three series → Task 3
(`build_premium_series`). §5 states → Task 3 (`label_levels`, `label_changes`,
causality test). §6 per-day unit → Task 4 (`collapse_to_daily` + its test). §7 gate
and sign inversion → Task 4 (`_VERDICT_MAP` + its test). §8 decision rule → Task 6.
§9 file map → File Structure. No gaps.

**Placeholders.** None — every code step carries real code; every verification step
carries the command and its expected output.

**Type consistency.** `AuditCell(label, supp_r, kept_r)` and
`CellVerdict.decision` match `analytics/audit_guard.py` as read from source.
`collapse_to_daily` emits `day, direction, mean_r, state`, which is exactly what
`build_state_cells` consumes. `fetch_*` both emit `open_time, close`, which is what
`upsert_venue_spot_daily` and `build_premium_series` consume.

**Known residual risk.** Task 5 step 2's sanity checks exist because the per-day
collapse is silently skippable — a wired-wrong driver would produce a table that
looks fine and is statistically meaningless. That check is not optional.
