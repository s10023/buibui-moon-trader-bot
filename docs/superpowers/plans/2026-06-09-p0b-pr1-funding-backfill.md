# P0b PR-1 — Funding-History Backfill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the OHLCV-backfill path full historical funding coverage so a later P0b cost integration can compute `funding_R` over the whole backtest window instead of only the recent ~90 days.

**Architecture:** Add `startTime`/`endTime` pagination to `fetch_funding_rates`; add a paginated `backfill_funding_rates` loop in `data_sync.py` mirroring the existing OHLCV `backfill()`; wire it into `run_backfill` via an optional `funding_since_ms` on `_sync_ancillary` (the incremental `run_sync` path keeps recent-only funding; open-interest stays recent-only). Then run the backfill locally and verify funding now spans the OHLCV window.

**Tech Stack:** Python 3.11, python-binance (`futures_funding_rate`), DuckDB, pandas, pytest + unittest.mock. Spec: `docs/redesign/2026-06-09-p0b-honest-costs-design.md` §2.

**Scope note:** This is PR-1 of a 3-PR series. PR-2 (cost integration into `Trade.pnl_r`) and PR-3 (live-ledger parity) are separate specs/plans. This plan is self-contained, non-behavioral (data layer only — regression goldens must stay UNMOVED), and independently testable.

---

## File Structure

| File | Responsibility | Change |
| --- | --- | --- |
| `analytics/data_fetcher.py` | Binance API → DataFrame, pure | Modify `fetch_funding_rates` — add `start_time`/`end_time` |
| `analytics/data_sync.py` | Backfill/sync orchestration | Add `_FUNDING_BACKFILL_LIMIT` + `backfill_funding_rates` |
| `analytics/analytics_runner.py` | Thin runner (creates client, delegates) | Wire paginated funding into `run_backfill` via `_sync_ancillary` |
| `tests/test_data_fetcher.py` | `TestFetchFundingRates` | Add 2 tests (kwarg passthrough) |
| `tests/test_data_sync.py` | `TestBackfillFundingRates` (new class) | Add 3 tests (pagination) |

---

## Task 0: Branch setup + commit design docs

**Files:**

- Existing (uncommitted on `main`): `docs/redesign/2026-06-09-p0b-honest-costs-design.md`
- This plan: `docs/superpowers/plans/2026-06-09-p0b-pr1-funding-backfill.md`

- [ ] **Step 1: Create the feature branch** (we are on `main`; branch before committing)

```bash
git checkout -b feat/funding-history-backfill
```

- [ ] **Step 2: Commit the spec + plan**

```bash
git add docs/redesign/2026-06-09-p0b-honest-costs-design.md \
        docs/superpowers/plans/2026-06-09-p0b-pr1-funding-backfill.md
git commit -m "docs(redesign): P0b honest-costs design + PR-1 funding-backfill plan

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 1: `fetch_funding_rates` — startTime/endTime pagination params

**Files:**

- Modify: `analytics/data_fetcher.py:87-106`
- Test: `tests/test_data_fetcher.py` (class `TestFetchFundingRates`, starts line 97)

- [ ] **Step 1: Write the failing tests**

Add these two methods inside the existing `class TestFetchFundingRates:` in `tests/test_data_fetcher.py` (the `_FUNDING_RAW` fixture and `MagicMock` import already exist at the top of the file):

```python
    def test_start_time_passes_time_kwargs(self) -> None:
        client = MagicMock()
        client.futures_funding_rate.return_value = [_FUNDING_RAW]
        fetch_funding_rates(
            client, "BTCUSDT", limit=1000, start_time=1_000, end_time=2_000
        )
        kwargs = client.futures_funding_rate.call_args.kwargs
        assert kwargs["startTime"] == 1_000
        assert kwargs["endTime"] == 2_000
        assert kwargs["limit"] == 1000

    def test_omits_time_kwargs_when_not_given(self) -> None:
        client = MagicMock()
        client.futures_funding_rate.return_value = [_FUNDING_RAW]
        fetch_funding_rates(client, "BTCUSDT")
        kwargs = client.futures_funding_rate.call_args.kwargs
        assert "startTime" not in kwargs
        assert "endTime" not in kwargs
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_data_fetcher.py::TestFetchFundingRates -v`
Expected: FAIL on `test_start_time_passes_time_kwargs` with `TypeError: fetch_funding_rates() got an unexpected keyword argument 'start_time'`.

- [ ] **Step 3: Implement the params**

Replace the `fetch_funding_rates` function body in `analytics/data_fetcher.py` (lines 87-106) with:

```python
def fetch_funding_rates(
    client: Client,
    symbol: str,
    limit: int = 100,
    start_time: int | None = None,
    end_time: int | None = None,
) -> pd.DataFrame:
    """Fetch funding rate records for symbol.

    Without `start_time`, returns the most recent `limit` records (back-compat).
    With `start_time` (Unix ms), Binance returns records from that time forward in
    ascending fundingTime order — used by the paginated history backfill. `end_time`
    optionally bounds the upper edge. Binance caps `limit` at 1000.

    Returns a DataFrame with columns matching FUNDING_COLUMNS.
    Returns an empty DataFrame (with correct columns) if no data.
    """
    kwargs: dict[str, Any] = {"symbol": symbol, "limit": limit}
    if start_time is not None:
        kwargs["startTime"] = start_time
    if end_time is not None:
        kwargs["endTime"] = end_time
    raw = client.futures_funding_rate(**kwargs)
    return _fetch_to_df(
        raw,
        lambda r: {
            "symbol": r["symbol"],
            "funding_time": int(r["fundingTime"]),
            "funding_rate": float(r["fundingRate"]),
        },
        FUNDING_COLUMNS,
    )
```

(`Any` is already imported at `analytics/data_fetcher.py:8`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_data_fetcher.py::TestFetchFundingRates -v`
Expected: PASS (all funding tests, including the original 3).

- [ ] **Step 5: Lint + commit**

```bash
make lint-py && make typecheck
git add analytics/data_fetcher.py tests/test_data_fetcher.py
git commit -m "feat(analytics): fetch_funding_rates startTime/endTime pagination params

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: `backfill_funding_rates` — paginated history loop

**Files:**

- Modify: `analytics/data_sync.py` (add constant near line 36; add function after `sync_funding_rates`, ~line 95)
- Test: `tests/test_data_sync.py` (new class `TestBackfillFundingRates`; update import at line 17)

- [ ] **Step 1: Write the failing tests**

First update the import at `tests/test_data_sync.py:17` from:

```python
from analytics.data_sync import backfill, sync, sync_funding_rates
```

to:

```python
from analytics.data_sync import (
    backfill,
    backfill_funding_rates,
    sync,
    sync_funding_rates,
)
```

Then append this new test class to `tests/test_data_sync.py` (the `_make_conn`, `_make_funding_df`, `get_funding_rates`, `FUNDING_COLUMNS`, `patch`, `Any`, `pd` names already exist in the file):

```python
class TestBackfillFundingRates:
    def test_stores_single_short_page(self) -> None:
        conn = _make_conn()
        df = _make_funding_df([1_000, 2_000])
        with patch("analytics.data_sync.fetch_funding_rates", return_value=df):
            total = backfill_funding_rates(
                conn, object(), "BTCUSDT", 0, sleep_fn=lambda _: None
            )
        assert total == 2
        stored = get_funding_rates(conn, "BTCUSDT", 0, 9_999_999)
        assert list(stored["funding_time"]) == [1_000, 2_000]

    def test_paginates_and_advances_start(self) -> None:
        conn = _make_conn()
        page1 = _make_funding_df(list(range(1, 1001)))  # 1000 rows -> next page
        page2 = _make_funding_df([1_001, 1_002])  # short page -> stop
        starts: list[Any] = []

        def fake(*args: Any, **kwargs: Any) -> pd.DataFrame:
            starts.append(kwargs.get("start_time"))
            return page1 if len(starts) == 1 else page2

        with patch("analytics.data_sync.fetch_funding_rates", side_effect=fake):
            total = backfill_funding_rates(
                conn, object(), "BTCUSDT", 0, sleep_fn=lambda _: None
            )
        assert total == 1002
        assert starts == [0, 1_001]  # advanced past page1's last funding_time

    def test_stops_on_empty_page(self) -> None:
        conn = _make_conn()
        empty = pd.DataFrame(columns=FUNDING_COLUMNS)
        with patch("analytics.data_sync.fetch_funding_rates", return_value=empty):
            total = backfill_funding_rates(
                conn, object(), "BTCUSDT", 0, sleep_fn=lambda _: None
            )
        assert total == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_data_sync.py::TestBackfillFundingRates -v`
Expected: collection/import error — `ImportError: cannot import name 'backfill_funding_rates' from 'analytics.data_sync'`.

- [ ] **Step 3: Implement the constant + function**

Add the constant in `analytics/data_sync.py` immediately after `_DEFAULT_SLEEP_SECONDS` (line 36):

```python
# Binance funding-rate-history endpoint caps at 1000 records per request.
_FUNDING_BACKFILL_LIMIT: int = 1000
```

Add this function after `sync_funding_rates` (after line 94):

```python
def backfill_funding_rates(
    conn: duckdb.DuckDBPyConnection,
    client: Client,
    symbol: str,
    since_ms: int,
    until_ms: int | None = None,
    sleep_fn: Callable[[float], None] | None = None,
) -> int:
    """Fetch full funding-rate history from since_ms forward and store it.

    Paginates in 1000-record batches (the endpoint cap), advancing past the last
    fundingTime each page. Returns total rows upserted. Stops when a short page
    (fewer rows than the limit) or an empty page is returned.

    Binance-only: the OKX adapter does not serve funding, and this is reached only
    on the Binance backfill path.
    """
    _sleep = sleep_fn if sleep_fn is not None else time.sleep
    total = 0
    current_start = since_ms
    while True:
        df = fetch_funding_rates(
            client,
            symbol,
            limit=_FUNDING_BACKFILL_LIMIT,
            start_time=current_start,
            end_time=until_ms,
        )
        if df.empty:
            break
        upsert_funding_rates(conn, df)
        total += len(df)
        logging.info(
            "backfill_funding_rates %s: stored %d rows (total %d)",
            symbol,
            len(df),
            total,
        )
        if len(df) < _FUNDING_BACKFILL_LIMIT:
            break
        current_start = int(df["funding_time"].iloc[-1]) + 1
        _sleep(_DEFAULT_SLEEP_SECONDS)
    return total
```

(`time`, `Callable`, `Client`, `duckdb`, `fetch_funding_rates`, `upsert_funding_rates` are all already imported in `analytics/data_sync.py`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_data_sync.py::TestBackfillFundingRates -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Lint + commit**

```bash
make lint-py && make typecheck
git add analytics/data_sync.py tests/test_data_sync.py
git commit -m "feat(analytics): backfill_funding_rates paginated history loop

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Wire paginated funding into `run_backfill`

**Files:**

- Modify: `analytics/analytics_runner.py:13` (import), `:44-54` (`_sync_ancillary`), `:72` (call site)

No unit test: `run_backfill` / `_sync_ancillary` create a real Binance client and are not unit-tested (matches the existing pattern — there is no `test_analytics_runner.py`). Behavior is validated by the operational run in Task 5. The wiring is a thin, type-checked branch.

- [ ] **Step 1: Update the import**

Change `analytics/analytics_runner.py:13` from:

```python
from analytics.data_sync import backfill, sync, sync_funding_rates, sync_open_interest
```

to:

```python
from analytics.data_sync import (
    backfill,
    backfill_funding_rates,
    sync,
    sync_funding_rates,
    sync_open_interest,
)
```

- [ ] **Step 2: Add `funding_since_ms` to `_sync_ancillary`**

Replace `_sync_ancillary` (lines 44-54) with:

```python
def _sync_ancillary(
    conn: duckdb.DuckDBPyConnection,
    client: Any,
    symbol: str,
    funding_since_ms: int | None = None,
) -> None:
    if funding_since_ms is not None:
        logging.info("Backfilling funding rates for %s ...", symbol)
        total_fr = backfill_funding_rates(conn, client, symbol, funding_since_ms)
    else:
        logging.info("Syncing funding rates for %s ...", symbol)
        total_fr = sync_funding_rates(conn, client, symbol)
    logging.info("Funding rates complete: %s — %d rows", symbol, total_fr)
    logging.info("Syncing open interest for %s ...", symbol)
    total_oi = sync_open_interest(conn, client, symbol)
    logging.info("Open interest complete: %s — %d rows", symbol, total_oi)
```

- [ ] **Step 3: Pass `since_ms` from `run_backfill`**

Change `analytics/analytics_runner.py:72` from:

```python
            _sync_ancillary(conn, client, symbol)
```

to:

```python
            _sync_ancillary(conn, client, symbol, funding_since_ms=since_ms)
```

Leave `run_sync`'s `_sync_ancillary(conn, client, symbol)` call (line 92) unchanged — incremental sync keeps recent-only funding.

- [ ] **Step 4: Lint + typecheck + commit**

Run: `make lint-py && make typecheck`
Expected: both PASS.

```bash
git add analytics/analytics_runner.py
git commit -m "feat(analytics): run_backfill paginates full funding history

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: DoD gate (full suite + regression unmoved)

**Files:** none (verification only).

- [ ] **Step 1: Lint + typecheck**

Run: `make lint-py && make typecheck`
Expected: ruff clean; mypy strict PASS.

- [ ] **Step 2: Full test suite**

Run: `make test`
Expected: green, with +5 new tests (2 in `test_data_fetcher.py`, 3 in `test_data_sync.py`).

- [ ] **Step 3: Regression goldens UNMOVED**

Run: `make test-regression`
Expected: PASS with goldens unchanged. PR-1 is non-behavioral (data layer only) — if any golden moves, STOP and investigate; nothing in this PR should touch backtest output. State the result plainly.

---

## Task 5: Run the backfill + verify coverage (operational, in-session)

**Files:** none (mutates local `analytics.db` via idempotent upsert; no commit). Uses the live Binance API — run locally with the default `DATA_SOURCE=binance`. This is NOT a daemon smoke-run; `buibui analytics backfill` only ingests OHLCV/funding/OI, it never fires Telegram.

- [ ] **Step 1: Record the OHLCV floor + current funding coverage (before)**

Run:

```bash
PYTHONPATH=. poetry run python -c "
import duckdb
from analytics.data_store import DEFAULT_DB_PATH
c = duckdb.connect(str(DEFAULT_DB_PATH))
for s in ('BTCUSDT','ETHUSDT','SOLUSDT'):
    ohlcv = c.execute('SELECT min(open_time) FROM ohlcv WHERE symbol=?', [s]).fetchone()[0]
    fr = c.execute('SELECT min(funding_time), max(funding_time), count(*) FROM funding_rates WHERE symbol=?', [s]).fetchone()
    print(s, 'ohlcv_min', ohlcv, 'funding', fr)
"
```

Note the OHLCV `min(open_time)` per symbol — that is the coverage target.

- [ ] **Step 2: Run the funding backfill from the OHLCV floor**

Pick a `--since` at or before the OHLCV floor (e.g. `2025-09-01`). The backfill re-fetches OHLCV too (idempotent upsert) and now paginates full funding:

```bash
PYTHONPATH=. poetry run buibui analytics backfill \
  --symbols BTCUSDT,ETHUSDT,SOLUSDT \
  --timeframes 15m,1h,4h,1d \
  --since 2025-09-01
```

Expected log lines: `Backfilling funding rates for <sym> ...` then `backfill_funding_rates <sym>: stored N rows ...`.

- [ ] **Step 3: Verify funding now spans the OHLCV window (after)**

Re-run the Step 1 snippet. Expected: `min(funding_time)` per symbol now ≤ the OHLCV `min(open_time)` (or within one 8h funding interval of it), and `count(*)` jumped from ~420 toward ~800+ per symbol for a 9-month window. State the before→after coverage plainly.

- [ ] **Step 4: Report**

Summarize the coverage delta (rows before/after, min funding_time before/after per symbol) so PR-2's §5 coverage gate can be confirmed satisfied. No commit — this is a local DB mutation.

---

## Self-Review (completed)

- **Spec coverage:** §2.1 → Task 1; §2.2 → Task 2; §2.3 → Task 3; §2.4 ingest+verify → Task 5; §2.5 tests → Tasks 1-2; non-behavioral / goldens-unmoved (§1 PR-1 row) → Task 4. All §2 requirements mapped.
- **Placeholder scan:** none — every code/command step shows full content.
- **Type/name consistency:** `backfill_funding_rates(conn, client, symbol, since_ms, until_ms=None, sleep_fn=None)` and `fetch_funding_rates(..., start_time=None, end_time=None)` used identically across data_sync, the tests, and the runner wiring; `_FUNDING_BACKFILL_LIMIT = 1000` referenced consistently; `funding_since_ms` threaded run_backfill → `_sync_ancillary` → `backfill_funding_rates(since_ms)`.
- **Scope:** single subsystem (funding data layer); non-behavioral; independently testable. No decomposition needed.
