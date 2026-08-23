# ST60(b) — `ohlcv` venue key Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make an OKX bar and a Binance bar for the same `(symbol, timeframe, open_time)` both
exist, so a `DATA_SOURCE=okx` run can never overwrite Binance history.

**Architecture:** A physical table `ohlcv_all` carries `venue` in its primary key. A **view named
`ohlcv`** is the read path, generated from a preference order stored in a new `db_meta` table, so
~50 existing read sites are untouched and the regression goldens cannot move. `upsert_ohlcv` is
the single production writer and gains a required `venue` argument.

**Tech Stack:** Python 3.11, DuckDB 1.5.5, pandas, pytest, ruff, mypy strict, Poetry.

**Spec:** `docs/superpowers/specs/2026-08-21-st60b-ohlcv-venue-key-design.md`

> ## ⚠ THIS PLAN CONTAINS TWO CONDITIONS THAT SHIPPED WRONG — corrected 2026-08-21, read before reusing
>
> Both were written against the assumption that a database awaiting migration holds the old `ohlcv`
> TABLE **and nothing else**. That assumption is false, and the live database disproved it: a failed
> `init_schema` runs `CREATE TABLE IF NOT EXISTS ohlcv_all` and `... db_meta` **successfully** before
> reaching the `CREATE OR REPLACE VIEW` that fails, so every failed run leaves two EMPTY tables
> behind. The operator's 15-minute daemon did that repeatedly and took itself down for ~1h.
>
> - **Task 3's guard (line ~661)** — `if "ohlcv" in tables and "ohlcv_all" not in tables:` would NOT
>   FIRE, because both names are present after the first failure. **Shipped condition:
>   `if "ohlcv" in tables:` alone.** `duckdb_tables()` lists tables only, never views, so after a
>   real migration `ohlcv` never appears there; `ohlcv_all`'s presence is irrelevant to the question.
> - **Task 6's refusal (line ~1101)** — `if "ohlcv_all" in tables: raise AlreadyMigratedError` would
>   REFUSE the real database, the one the tool exists for. **Shipped logic: "already migrated" keys
>   on `ohlcv` NOT being a TABLE.** An EMPTY leftover `ohlcv_all` is expected and is dropped and
>   rebuilt; a POPULATED one is an undesigned state and refuses without mutating.
>
> The code blocks below are left as written on purpose — this plan is the record of what was
> intended, and the corrections are the more useful artifact. **Read the shipped code, not these
> two blocks.** Detail: `analytics/store/schema.py`, `tools/migrate_ohlcv_venue.py`, and the SDD
> ledger's LIVE INCIDENT section.

## Global Constraints

- **Definition of Done per task:** `make lint-py` ✓, `make typecheck` ✓, `make test` green.
- **This diff touches the backtest surface** (`analytics/store/`, `analytics/backtest/` reads), so
  `make test-regression` is REQUIRED at the final gate. **Pre-registered claim: the goldens MUST
  NOT move.** A moved golden is a defect in this change. Do **not** run `make regression-update`.
- **Background anything measured in minutes** (`make test` ~4m55s, `make test-regression` ~93s)
  with `run_in_background: true`. Foreground `make lint-py` / `make typecheck` / `make lint-md`.
- ⚠ **Never edit anything under the Python tree while `make test` is in flight.** Both run against
  the working tree and pytest imports at collection.
- ⚠ **Do NOT run any tool against the real `analytics.db` until Task 6 has landed and the operator
  has run the migration.** Between Task 2 and Task 6 the code expects `ohlcv_all` and the live DB
  still has `ohlcv`. Task 3's refusal guard makes that failure loud rather than destructive, but do
  not go looking for it. The 15-minute timer owns that file.
- ⚠ **The `guard-destructive` PreToolUse hook blocks any Bash command containing `DROP TABLE`.**
  Task 6 writes a file containing that SQL — write it with the **Write tool**, never a Bash
  heredoc. If the hook fires, surface it; do not work around it silently.
- **Venue strings are interpolated into generated SQL.** Every venue must be validated against
  `^[a-z0-9_]+$` before it reaches a query. This is not optional.
- **Commit style:** conventional commits (`feat:`, `fix:`, `test:`, `refactor:`). Branch is
  `feat/st60b-ohlcv-venue-key`, already created off `736666b`.

## File Structure

| File | Responsibility | Task |
| --- | --- | --- |
| `analytics/store/venue.py` (new) | Venue-order parsing, validation, storage, and view-SQL generation. Pure + conn-level helpers, no schema knowledge beyond the two names. | 1 |
| `tests/test_ohlcv_venue.py` (new) | Every venue behaviour: view forms, collision, unknown venue, refusal guard, migration. | 1, 2, 3, 6 |
| `analytics/store/schema.py` | Creates `ohlcv_all` + `db_meta` + the view; refuses on an unmigrated DB. | 2, 3 |
| `analytics/store/market_data.py` | `upsert_ohlcv` gains `venue`; `FABRICATED_CVD_SQL` scans all venues. | 2, 4 |
| `analytics/data_sync.py` | Threads a resolved venue into `backfill` / `sync`. | 2, 5 |
| `utils/binance_client.py` | `resolve_venue()` beside the existing `create_data_client()`. | 5 |
| `tools/migrate_ohlcv_venue.py` (new) | One-shot operator migration with a backup-freshness guard. | 6 |
| `tools/export_live_db.py` | Copies `ohlcv_all`; stamps the slim DB's read order. | 7 |

---

### Task 1: Venue helpers — parsing, storage, and view SQL

Pure and near-pure functions with no callers yet. The tree stays green and behaviour is unchanged;
this task exists so the SQL generator is tested in isolation before anything depends on it.

**Files:**

- Create: `analytics/store/venue.py`
- Create: `tests/test_ohlcv_venue.py`

**Interfaces:**

- Consumes: nothing.
- Produces:
  - `DEFAULT_VENUE: str = "binance"`
  - `READ_VENUE_ORDER_KEY: str = "read_venue_order"`
  - `OHLCV_COLUMNS: str` — the nine `ohlcv` columns, comma-joined, in table order
  - `parse_venue_order(raw: str) -> list[str]`
  - `ohlcv_view_sql(order: list[str]) -> str`
  - `read_venue_order(conn: duckdb.DuckDBPyConnection) -> list[str]`
  - `set_read_venue_order(conn: duckdb.DuckDBPyConnection, order: list[str]) -> None`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ohlcv_venue.py`:

```python
"""Venue-keyed ohlcv: order parsing, view generation, and read behaviour."""

import duckdb
import pytest

from analytics.store.venue import (
    DEFAULT_VENUE,
    ohlcv_view_sql,
    parse_venue_order,
    read_venue_order,
    set_read_venue_order,
)


class TestParseVenueOrder:
    def test_single_venue(self) -> None:
        assert parse_venue_order("binance") == ["binance"]

    def test_comma_separated_is_ordered_and_normalised(self) -> None:
        assert parse_venue_order(" OKX , binance ") == ["okx", "binance"]

    def test_empty_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            parse_venue_order("  ,  ")

    def test_duplicates_are_rejected(self) -> None:
        with pytest.raises(ValueError, match="duplicate"):
            parse_venue_order("okx,okx")

    def test_injection_is_rejected(self) -> None:
        # Venues are interpolated into generated SQL, so this is a security boundary,
        # not a tidiness rule.
        with pytest.raises(ValueError, match="invalid venue"):
            parse_venue_order("binance'; DELETE FROM ohlcv_all; --")


def _conn_with_rows() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    conn.execute(
        "CREATE TABLE ohlcv_all (venue TEXT NOT NULL, symbol TEXT NOT NULL, "
        "timeframe TEXT NOT NULL, open_time BIGINT NOT NULL, open DOUBLE, high DOUBLE, "
        "low DOUBLE, close DOUBLE, volume DOUBLE, taker_buy_volume DOUBLE, "
        "PRIMARY KEY (venue, symbol, timeframe, open_time))"
    )
    rows = [
        ("binance", "BTCUSDT", "1h", 1, 10.0, 11.0, 9.0, 10.5, 100.0, 50.0),
        ("binance", "BTCUSDT", "1h", 2, 10.0, 11.0, 9.0, 20.5, 100.0, 50.0),
        ("okx", "BTCUSDT", "1h", 2, 10.0, 11.0, 9.0, 99.0, 100.0, None),
        ("kraken", "BTCUSDT", "1h", 3, 10.0, 11.0, 9.0, 77.0, 100.0, None),
    ]
    for row in rows:
        conn.execute("INSERT INTO ohlcv_all VALUES (?,?,?,?,?,?,?,?,?,?)", list(row))
    return conn


class TestOhlcvViewSql:
    def test_single_venue_order_returns_only_that_venue(self) -> None:
        conn = _conn_with_rows()
        conn.execute(ohlcv_view_sql(["binance"]))
        got = conn.execute(
            "SELECT open_time, close FROM ohlcv ORDER BY open_time"
        ).fetchall()
        assert got == [(1, 10.5), (2, 20.5)]

    def test_preference_order_prefers_first_and_falls_back(self) -> None:
        # The CI shape: OKX wins where it has a bar, Binance fills every gap.
        conn = _conn_with_rows()
        conn.execute(ohlcv_view_sql(["okx", "binance"]))
        got = conn.execute(
            "SELECT open_time, close FROM ohlcv ORDER BY open_time"
        ).fetchall()
        assert got == [(1, 10.5), (2, 99.0)]

    def test_venue_outside_the_order_is_invisible(self) -> None:
        conn = _conn_with_rows()
        conn.execute(ohlcv_view_sql(["okx", "binance"]))
        opens = [r[0] for r in conn.execute("SELECT open_time FROM ohlcv").fetchall()]
        assert 3 not in opens, (
            "a venue absent from the order must never shadow a known one"
        )

    def test_view_exposes_exactly_the_nine_ohlcv_columns(self) -> None:
        conn = _conn_with_rows()
        conn.execute(ohlcv_view_sql(["binance"]))
        cols = [r[1] for r in conn.execute("PRAGMA table_info('ohlcv')").fetchall()]
        assert cols == [
            "symbol",
            "timeframe",
            "open_time",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "taker_buy_volume",
        ]

    def test_empty_order_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            ohlcv_view_sql([])


class TestReadVenueOrder:
    def test_missing_db_meta_defaults_to_binance(self) -> None:
        conn = duckdb.connect(":memory:")
        conn.execute(
            "CREATE TABLE db_meta (key TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY (key))"
        )
        assert read_venue_order(conn) == [DEFAULT_VENUE]

    def test_round_trips_through_db_meta(self) -> None:
        conn = duckdb.connect(":memory:")
        conn.execute(
            "CREATE TABLE db_meta (key TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY (key))"
        )
        set_read_venue_order(conn, ["okx", "binance"])
        assert read_venue_order(conn) == ["okx", "binance"]

    def test_set_is_idempotent(self) -> None:
        conn = duckdb.connect(":memory:")
        conn.execute(
            "CREATE TABLE db_meta (key TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY (key))"
        )
        set_read_venue_order(conn, ["okx", "binance"])
        set_read_venue_order(conn, ["okx", "binance"])
        rows = conn.execute("SELECT COUNT(*) FROM db_meta").fetchone()
        assert rows is not None and rows[0] == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `poetry run pytest tests/test_ohlcv_venue.py -q`

Expected: collection error — `ModuleNotFoundError: No module named 'analytics.store.venue'`.

- [ ] **Step 3: Write the implementation**

Create `analytics/store/venue.py`:

```python
"""Venue preference for the `ohlcv` read path.

`ohlcv_all` holds every venue's bars; the view named `ohlcv` exposes ONE bar per
(symbol, timeframe, open_time), chosen by a preference order stored in `db_meta`.
The order lives in the DATABASE rather than the process environment: a view
definition is shared schema state, so deriving it from `DATA_SOURCE` would let a
stray process change what every later reader sees.
"""

import re

import duckdb

DEFAULT_VENUE: str = "binance"
READ_VENUE_ORDER_KEY: str = "read_venue_order"

OHLCV_COLUMNS: str = (
    "symbol, timeframe, open_time, open, high, low, close, volume, taker_buy_volume"
)

# Venue names are interpolated into generated SQL (DuckDB cannot parameterise a view
# body), so this pattern is a security boundary rather than a naming preference.
_VENUE_RE = re.compile(r"^[a-z0-9_]+$")


def parse_venue_order(raw: str) -> list[str]:
    """Parse a comma-separated preference list into normalised venue names."""
    order = [part.strip().lower() for part in raw.split(",")]
    order = [part for part in order if part]
    if not order:
        raise ValueError("venue order is empty")
    for venue in order:
        if not _VENUE_RE.match(venue):
            raise ValueError(f"invalid venue name: {venue!r}")
    if len(set(order)) != len(order):
        raise ValueError(f"duplicate venue in order: {raw!r}")
    return order


def ohlcv_view_sql(order: list[str]) -> str:
    """Return the CREATE OR REPLACE VIEW statement for the `ohlcv` read path."""
    if not order:
        raise ValueError("venue order is empty")
    for venue in order:
        if not _VENUE_RE.match(venue):
            raise ValueError(f"invalid venue name: {venue!r}")
    if len(order) == 1:
        # A plain filter, deliberately: the single-venue production case must not pay
        # for a window function it can never need.
        body = f"SELECT {OHLCV_COLUMNS} FROM ohlcv_all WHERE venue = '{order[0]}'"
    else:
        venues = ", ".join(f"'{v}'" for v in order)
        cases = " ".join(f"WHEN '{v}' THEN {i}" for i, v in enumerate(order))
        body = (
            f"SELECT {OHLCV_COLUMNS} FROM ohlcv_all "
            f"WHERE venue IN ({venues}) "
            "QUALIFY row_number() OVER ("
            "PARTITION BY symbol, timeframe, open_time "
            f"ORDER BY CASE venue {cases} END) = 1"
        )
    return f"CREATE OR REPLACE VIEW ohlcv AS {body}"


def read_venue_order(conn: duckdb.DuckDBPyConnection) -> list[str]:
    """Return the stored read preference order, defaulting to Binance alone."""
    row = conn.execute(
        "SELECT value FROM db_meta WHERE key = ?", [READ_VENUE_ORDER_KEY]
    ).fetchone()
    if row is None:
        return [DEFAULT_VENUE]
    return parse_venue_order(str(row[0]))


def set_read_venue_order(conn: duckdb.DuckDBPyConnection, order: list[str]) -> None:
    """Store the read preference order (validated first)."""
    validated = parse_venue_order(",".join(order))
    conn.execute(
        "INSERT OR REPLACE INTO db_meta (key, value) VALUES (?, ?)",
        [READ_VENUE_ORDER_KEY, ",".join(validated)],
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `poetry run pytest tests/test_ohlcv_venue.py -q`

Expected: PASS, 13 tests.

- [ ] **Step 5: Lint and typecheck**

Run: `make lint-py && make typecheck`

Expected: both clean.

- [ ] **Step 6: Commit**

```bash
git add analytics/store/venue.py tests/test_ohlcv_venue.py
git commit -m "feat: add venue-order parsing and ohlcv view SQL generation"
```

---

### Task 2: Schema switch — `ohlcv_all` + `db_meta` + the view, and the single writer

The atomic task. `ohlcv` stops being a table and becomes a view in the same commit that moves
every writer, because the two cannot disagree even briefly.

⚠ **This task touches ~61 test call sites.** Two distinct populations, both mechanical:

- **39 `upsert_ohlcv(conn, ...)` calls** across ~30 test files — each needs `, venue="binance"`.
- **22 raw `INSERT INTO ohlcv` statements** across ~17 test files — each needs the table renamed
  to `ohlcv_all` and a `'binance'` value added as the first column.

**Files:**

- Modify: `analytics/store/schema.py:8-30` (the `ohlcv` CREATE TABLE and the `taker_buy_volume`
  migration guard)
- Modify: `analytics/store/market_data.py:13-25` (`upsert_ohlcv`)
- Modify: `analytics/data_sync.py:69` (the single production caller)
- Modify: `tests/test_ohlcv_venue.py` (add the collision test)
- Modify: ~30 test files (the `upsert_ohlcv` sweep) and ~17 test files (the raw-INSERT sweep)

**Interfaces:**

- Consumes: `analytics.store.venue.{DEFAULT_VENUE, ohlcv_view_sql, read_venue_order, set_read_venue_order}`
- Produces:
  - `upsert_ohlcv(conn: duckdb.DuckDBPyConnection, df: pd.DataFrame, *, venue: str) -> None`
    — `venue` is **required and keyword-only**
  - `init_schema(conn)` now yields tables `ohlcv_all` + `db_meta` and a view `ohlcv`

- [ ] **Step 1: Write the failing collision test**

Append to `tests/test_ohlcv_venue.py`:

```python
class TestVenueCollision:
    """The whole point of ST60(b). Read this test first."""

    def test_okx_write_cannot_overwrite_a_binance_bar(self) -> None:
        import pandas as pd

        from analytics.store import init_schema, upsert_ohlcv

        conn = duckdb.connect(":memory:")
        init_schema(conn)
        bar = {
            "symbol": "BTCUSDT",
            "timeframe": "1h",
            "open_time": 1,
            "open": 10.0,
            "high": 11.0,
            "low": 9.0,
            "close": 10.5,
            "volume": 100.0,
            "taker_buy_volume": 50.0,
        }
        upsert_ohlcv(conn, pd.DataFrame([bar]), venue="binance")
        upsert_ohlcv(
            conn,
            pd.DataFrame([{**bar, "close": 99.0, "taker_buy_volume": None}]),
            venue="okx",
        )

        both = conn.execute(
            "SELECT venue, close FROM ohlcv_all ORDER BY venue"
        ).fetchall()
        assert both == [("binance", 10.5), ("okx", 99.0)], "both venues must survive"

        through_view = conn.execute(
            "SELECT close, taker_buy_volume FROM ohlcv"
        ).fetchall()
        assert through_view == [(10.5, 50.0)], (
            "reads stay on Binance, taker split intact"
        )

    def test_fresh_schema_defaults_to_binance_only(self) -> None:
        from analytics.store import init_schema
        from analytics.store.venue import read_venue_order

        conn = duckdb.connect(":memory:")
        init_schema(conn)
        assert read_venue_order(conn) == ["binance"]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `poetry run pytest tests/test_ohlcv_venue.py::TestVenueCollision -q`

Expected: FAIL — `upsert_ohlcv() got an unexpected keyword argument 'venue'`.

- [ ] **Step 3: Switch the schema**

In `analytics/store/schema.py`, replace the `ohlcv` CREATE TABLE block and its migration guard
with:

```python
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ohlcv_all (
            venue            TEXT   NOT NULL,
            symbol           TEXT   NOT NULL,
            timeframe        TEXT   NOT NULL,
            open_time        BIGINT NOT NULL,
            open             DOUBLE NOT NULL,
            high             DOUBLE NOT NULL,
            low              DOUBLE NOT NULL,
            close            DOUBLE NOT NULL,
            volume           DOUBLE NOT NULL,
            taker_buy_volume DOUBLE,
            PRIMARY KEY (venue, symbol, timeframe, open_time)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS db_meta (
            key   TEXT NOT NULL,
            value TEXT NOT NULL,
            PRIMARY KEY (key)
        )
    """)
    # Migration guard: add the column to DBs created before this field existed.
    # Keyed on `duckdb_tables()`, NOT information_schema -- a VIEW appears in
    # information_schema.columns, so the old query would inspect the `ohlcv` view
    # (which always projects the column) and the ALTER would never fire.
    existing = {
        row[0]
        for row in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'ohlcv_all'"
        ).fetchall()
    }
    if existing and "taker_buy_volume" not in existing:
        conn.execute("ALTER TABLE ohlcv_all ADD COLUMN taker_buy_volume DOUBLE")
    conn.execute(ohlcv_view_sql(read_venue_order(conn)))
```

Add the import at the top of `schema.py`:

```python
from analytics.store.venue import ohlcv_view_sql, read_venue_order
```

- [ ] **Step 4: Move the writer**

In `analytics/store/market_data.py`, replace `upsert_ohlcv`:

```python
def upsert_ohlcv(
    conn: duckdb.DuckDBPyConnection, df: pd.DataFrame, *, venue: str
) -> None:
    """Insert or replace OHLCV rows for one venue.

    df must have columns: symbol, timeframe, open_time, open, high, low, close, volume,
    taker_buy_volume. Conflicts on (venue, symbol, timeframe, open_time) are replaced,
    so a write for one venue can never touch another's bars.

    `venue` is required rather than defaulted on purpose: this whole table shape exists
    because a silent default destroyed data once already.
    """
    if df.empty:
        return
    _upsert(
        conn,
        df.assign(venue=venue),
        "ohlcv_all",
        "venue, symbol, timeframe, open_time, open, high, low, close, volume, "
        "taker_buy_volume",
    )
```

In `analytics/data_sync.py:69`, change the single production call:

```python
        upsert_ohlcv(conn, df, venue="binance")
```

(Task 5 replaces that literal with a resolved venue. Leaving it hardcoded for one task keeps
this commit green without dragging env resolution into a schema change.)

- [ ] **Step 5: Sweep the 39 `upsert_ohlcv` test call sites**

Most are single-line and take a scripted edit:

```bash
grep -rln 'upsert_ohlcv(' tests/ | xargs sed -i -E \
  's/upsert_ohlcv\((conn, [^()]*)\)/upsert_ohlcv(\1, venue="binance")/g'
```

Then find the multi-line calls the regex cannot reach and edit them by hand:

```bash
grep -rn 'upsert_ohlcv($' tests/
grep -rn 'upsert_ohlcv(' tests/ | grep -v 'venue='
```

Expected leftovers needing hand edits: `tests/test_stats_adr.py:111`,
`tests/xsmom/test_targets_cli.py:44`, `tests/cvd/test_replay.py:32`,
`tests/xsmom/test_targets_replay.py:77`. Verify the second grep returns nothing before moving on.

- [ ] **Step 6: Sweep the 22 raw `INSERT INTO ohlcv` statements**

These are positional inserts, so each needs the table renamed **and** a leading venue value.
List them first:

```bash
grep -rniE "insert (or replace )?into[[:space:]]+ohlcv\b" tests/
```

For each, rename the table to `ohlcv_all` and add `'binance',` as the first value. Two shapes
appear:

```python
# before
conn.execute("INSERT INTO ohlcv VALUES ('BTCUSDT', '1h', 1, 10, 11, 9, 10.5, 100, 50)")
# after
conn.execute(
    "INSERT INTO ohlcv_all VALUES ('binance', 'BTCUSDT', '1h', 1, 10, 11, 9, 10.5, 100, 50)"
)

# before
conn.execute("INSERT INTO ohlcv SELECT * FROM _o")
# after
conn.execute("INSERT INTO ohlcv_all SELECT 'binance', * FROM _o")
```

Verify none remain:

```bash
grep -rniE "insert (or replace )?into[[:space:]]+ohlcv\b" tests/ | grep -v ohlcv_all
```

Expected: only `tests/test_export_live_db.py:59`, which is a docstring line, not a statement.

- [ ] **Step 7: Run the full suite in the background**

Run: `make test` with `run_in_background: true`

Expected: green. Do not edit the Python tree while it runs.

- [ ] **Step 8: Lint and typecheck**

Run: `make lint-py && make typecheck`

Expected: both clean.

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "feat: key ohlcv by venue behind a view named ohlcv"
```

---

### Task 3: `init_schema` refuses on an unmigrated database

Without this, `init_schema` against the real `analytics.db` tries to `CREATE OR REPLACE VIEW ohlcv`
while `ohlcv` is still a table, and DuckDB raises something that reads like a bug rather than an
instruction.

**Files:**

- Modify: `analytics/store/schema.py` (guard at the top of `init_schema`)
- Modify: `tests/test_ohlcv_venue.py`

**Interfaces:**

- Consumes: `init_schema` from Task 2.
- Produces: `UnmigratedDatabaseError(RuntimeError)` exported from `analytics.store.schema`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_ohlcv_venue.py`:

```python
class TestUnmigratedDatabaseGuard:
    def _legacy_conn(self) -> duckdb.DuckDBPyConnection:
        conn = duckdb.connect(":memory:")
        conn.execute(
            "CREATE TABLE ohlcv (symbol TEXT NOT NULL, timeframe TEXT NOT NULL, "
            "open_time BIGINT NOT NULL, open DOUBLE, high DOUBLE, low DOUBLE, "
            "close DOUBLE, volume DOUBLE, taker_buy_volume DOUBLE, "
            "PRIMARY KEY (symbol, timeframe, open_time))"
        )
        return conn

    def test_legacy_table_raises_and_names_the_tool(self) -> None:
        from analytics.store.schema import UnmigratedDatabaseError, init_schema

        conn = self._legacy_conn()
        with pytest.raises(UnmigratedDatabaseError) as excinfo:
            init_schema(conn)
        assert "tools/migrate_ohlcv_venue.py" in str(excinfo.value)

    def test_guard_reads_duckdb_tables_not_information_schema(self) -> None:
        # A VIEW appears in information_schema.columns but NOT in duckdb_tables().
        # Keying the guard on information_schema would make a MIGRATED database look
        # unmigrated forever, so this pins the discriminator itself.
        from analytics.store import init_schema

        conn = duckdb.connect(":memory:")
        init_schema(conn)
        init_schema(conn)  # second call on a migrated DB must be a no-op, not a raise

        tables = {
            r[0]
            for r in conn.execute("SELECT table_name FROM duckdb_tables()").fetchall()
        }
        assert "ohlcv" not in tables, "ohlcv must be a view, not a table"
        infoschema = {
            r[0]
            for r in conn.execute(
                "SELECT table_name FROM information_schema.columns WHERE table_name='ohlcv'"
            ).fetchall()
        }
        assert infoschema == {"ohlcv"}, "the view IS visible in information_schema"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `poetry run pytest tests/test_ohlcv_venue.py::TestUnmigratedDatabaseGuard -q`

Expected: FAIL — `ImportError: cannot import name 'UnmigratedDatabaseError'`.

- [ ] **Step 3: Add the guard**

At the top of `analytics/store/schema.py`:

```python
class UnmigratedDatabaseError(RuntimeError):
    """Raised when a database still has the pre-ST60(b) `ohlcv` TABLE."""
```

And as the first statement inside `init_schema`:

```python
    tables = {
        row[0] for row in conn.execute("SELECT table_name FROM duckdb_tables()").fetchall()
    }
    # ⚠ WRONG AS WRITTEN — shipped as `if "ohlcv" in tables:` alone. See the banner at the top.
    if "ohlcv" in tables and "ohlcv_all" not in tables:
        raise UnmigratedDatabaseError(
            "This database predates the ohlcv venue key: `ohlcv` is still a TABLE. "
            "Take a backup, then run `poetry run python tools/migrate_ohlcv_venue.py`. "
            "Nothing has been modified."
        )
```

- [ ] **Step 4: Run the tests**

Run: `poetry run pytest tests/test_ohlcv_venue.py -q`

Expected: PASS.

- [ ] **Step 5: Mutation check — prove the guard has teeth**

Temporarily re-key the guard onto `information_schema` (swap the `duckdb_tables()` query for
`SELECT table_name FROM information_schema.tables WHERE table_schema='main'`) and re-run:

Run: `poetry run pytest tests/test_ohlcv_venue.py::TestUnmigratedDatabaseGuard -q`

Expected: `test_guard_reads_duckdb_tables_not_information_schema` FAILS (a migrated DB now looks
unmigrated). **Revert the mutation** and confirm green again. A guard whose discriminator is not
mutation-tested is the exact shape that shipped the vacuous xsmom causality test.

- [ ] **Step 6: Commit**

```bash
git add analytics/store/schema.py tests/test_ohlcv_venue.py
git commit -m "feat: refuse init_schema on a pre-venue-key ohlcv table"
```

---

### Task 4: The fabricated-CVD check scans every venue

`FABRICATED_CVD_SQL` backs the tier-1 `fabricated CVD bars` line in `daily_check.py`. Per
`AGENTS.md` it now guards **history and regressions**, so reading it through the Binance-only view
would blind it to exactly the rows it exists to find.

**Files:**

- Modify: `analytics/store/market_data.py` (the `FABRICATED_CVD_SQL` constant and its comment block)
- Modify: `tests/test_neutral_cvd_detection.py`

**Interfaces:**

- Consumes: `ohlcv_all` from Task 2.
- Produces: no signature change — `FABRICATED_CVD_SQL` keeps its three-column shape
  `(symbol, timeframe, open_time)`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_neutral_cvd_detection.py`:

```python
def test_fabricated_cvd_scan_sees_non_binance_rows() -> None:
    """A fabricated bar parked under a non-default venue must still be flagged."""
    import duckdb

    from analytics.store import init_schema
    from analytics.store.market_data import FABRICATED_CVD_SQL

    conn = duckdb.connect(":memory:")
    init_schema(conn)
    conn.execute(
        "INSERT INTO ohlcv_all VALUES "
        "('okx', 'BTCUSDT', '1h', 1, 10, 11, 9, 10.5, 100, 50)"
    )
    flagged = conn.execute(FABRICATED_CVD_SQL).fetchall()
    assert flagged == [("BTCUSDT", "1h", 1)], (
        "the check guards history across venues; the binance-only view would miss this"
    )
```

- [ ] **Step 2: Run it to verify it fails**

Run: `poetry run pytest tests/test_neutral_cvd_detection.py::test_fabricated_cvd_scan_sees_non_binance_rows -q`

Expected: FAIL — `assert [] == [('BTCUSDT', '1h', 1)]`.

- [ ] **Step 3: Point the scan at `ohlcv_all`**

In `analytics/store/market_data.py`, change the constant:

```python
FABRICATED_CVD_SQL = """
    SELECT symbol, timeframe, open_time
    FROM ohlcv_all
    WHERE volume > 0 AND taker_buy_volume = volume / 2.0
    ORDER BY symbol, timeframe, open_time
"""
```

Replace the stale comment block above it (the paragraph beginning "`upsert_ohlcv` above REPLACES
on conflict and the `ohlcv` primary key carries NO venue component") with:

```python
# `ohlcv_all` carries `venue` in its PRIMARY KEY, so an OKX write lands BESIDE the
# Binance bar rather than replacing it (ST60(b)). This scan therefore reads the
# physical table, not the `ohlcv` view: the view exposes one venue's bars, while this
# check guards HISTORY and regressions across all of them. #678 stopped the OKX
# adapter fabricating `taker_buy_volume = volume / 2`; this finds any that predate it.
```

- [ ] **Step 4: Run the tests**

Run: `poetry run pytest tests/test_neutral_cvd_detection.py -q`

Expected: PASS, all cases.

- [ ] **Step 5: Commit**

```bash
git add analytics/store/market_data.py tests/test_neutral_cvd_detection.py
git commit -m "fix: scan every venue for fabricated CVD bars, not just the read view"
```

---

### Task 5: Resolve the venue from `DATA_SOURCE`

**Files:**

- Modify: `utils/binance_client.py` (beside `create_data_client`, ~line 42)
- Modify: `analytics/data_sync.py` (`backfill` signature + the `upsert_ohlcv` call; `sync`
  signature + its `backfill` call)
- Modify: `tests/test_data_sync.py`

**Interfaces:**

- Consumes: `upsert_ohlcv(..., venue=...)` from Task 2.
- Produces:
  - `utils.binance_client.resolve_venue() -> str`
  - `backfill(conn, client, symbol, timeframe, start_ms, sleep_fn=None, venue=None) -> int`
  - `sync(conn, client, symbol, timeframe, sleep_fn=None, venue=None) -> int`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_data_sync.py`:

```python
class TestVenueResolution:
    def test_default_is_binance(self, monkeypatch: Any) -> None:
        from utils.binance_client import resolve_venue

        monkeypatch.delenv("DATA_SOURCE", raising=False)
        assert resolve_venue() == "binance"

    def test_okx_env_resolves_to_okx(self, monkeypatch: Any) -> None:
        from utils.binance_client import resolve_venue

        monkeypatch.setenv("DATA_SOURCE", "OKX")
        assert resolve_venue() == "okx"

    def test_unknown_source_falls_back_to_binance(self, monkeypatch: Any) -> None:
        # Mirrors create_data_client(), which treats anything unrecognised as Binance.
        from utils.binance_client import resolve_venue

        monkeypatch.setenv("DATA_SOURCE", "kraken")
        assert resolve_venue() == "binance"


def test_backfill_tags_rows_with_the_resolved_venue(monkeypatch: Any) -> None:
    """The env var must reach the stored row, not just the client choice."""
    import duckdb

    from analytics.data_sync import backfill
    from analytics.store import init_schema

    monkeypatch.setenv("DATA_SOURCE", "okx")
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    client = MagicMock()
    client.futures_klines.return_value = _RAW_KLINES_ONE_BATCH

    backfill(conn, client, "BTCUSDT", "1h", 0, sleep_fn=lambda _: None)

    venues = {
        r[0] for r in conn.execute("SELECT DISTINCT venue FROM ohlcv_all").fetchall()
    }
    assert venues == {"okx"}
    assert conn.execute("SELECT COUNT(*) FROM ohlcv").fetchone()[0] == 0, (
        "a fresh DB reads binance-only, so OKX rows are stored but inert"
    )
```

Reuse whatever raw-kline fixture `tests/test_data_sync.py` already defines for `backfill`; name it
`_RAW_KLINES_ONE_BATCH` if no equivalent exists, built from the file's existing `_make_df` helper.

- [ ] **Step 2: Run them to verify they fail**

Run: `poetry run pytest tests/test_data_sync.py::TestVenueResolution -q`

Expected: FAIL — `ImportError: cannot import name 'resolve_venue'`.

- [ ] **Step 3: Add `resolve_venue`**

In `utils/binance_client.py`, directly after `create_data_client`:

```python
def resolve_venue() -> str:
    """Return the venue name for the active DATA_SOURCE.

    Mirrors create_data_client()'s dispatch exactly -- anything unrecognised is
    Binance -- so the stored venue tag can never disagree with the client that
    fetched the bars.
    """
    source = os.environ.get("DATA_SOURCE", "binance").lower()
    return "okx" if source == "okx" else "binance"
```

- [ ] **Step 4: Thread it through `data_sync`**

In `analytics/data_sync.py`, add `venue: str | None = None` as the last parameter of both
`backfill` and `sync`, import `resolve_venue`, and resolve once at the top of `backfill`:

```python
    resolved_venue = venue if venue is not None else resolve_venue()
```

Change the upsert call to `upsert_ohlcv(conn, df, venue=resolved_venue)` and have `sync` forward
its own argument: `return backfill(conn, client, symbol, timeframe, latest, sleep_fn=sleep_fn, venue=venue)`.

- [ ] **Step 5: Run the tests**

Run: `poetry run pytest tests/test_data_sync.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add utils/binance_client.py analytics/data_sync.py tests/test_data_sync.py
git commit -m "feat: tag stored bars with the venue DATA_SOURCE resolved to"
```

---

### Task 6: The one-shot migration tool

**Files:**

- Create: `tools/migrate_ohlcv_venue.py` — ⚠ **write this with the Write tool.** It contains
  `DROP TABLE`, which the `guard-destructive` hook blocks in any Bash command.
- Modify: `tests/test_ohlcv_venue.py`

**Interfaces:**

- Consumes: `analytics.store.venue.{ohlcv_view_sql, set_read_venue_order}`,
  `analytics.store.schema.UnmigratedDatabaseError`.
- Produces: `migrate(db_path: Path, *, force: bool = False) -> tuple[int, int]` returning
  `(rows_before, rows_after)`; `newest_backup_age_hours(root: Path) -> float | None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ohlcv_venue.py`:

```python
class TestMigration:
    def _legacy_db(self, tmp_path: Any) -> Any:
        path = tmp_path / "legacy.duckdb"
        conn = duckdb.connect(str(path))
        conn.execute(
            "CREATE TABLE ohlcv (symbol TEXT NOT NULL, timeframe TEXT NOT NULL, "
            "open_time BIGINT NOT NULL, open DOUBLE, high DOUBLE, low DOUBLE, "
            "close DOUBLE, volume DOUBLE, taker_buy_volume DOUBLE, "
            "PRIMARY KEY (symbol, timeframe, open_time))"
        )
        for i in range(1, 6):
            conn.execute(
                "INSERT INTO ohlcv VALUES ('BTCUSDT','1h',?,10,11,9,?,100,50)",
                [i, 10.0 + i],
            )
        conn.close()
        return path

    def test_migrates_every_row_under_binance(self, tmp_path: Any) -> None:
        from tools.migrate_ohlcv_venue import migrate

        path = self._legacy_db(tmp_path)
        before, after = migrate(path, force=True)
        assert (before, after) == (5, 5)

        conn = duckdb.connect(str(path))
        assert conn.execute("SELECT DISTINCT venue FROM ohlcv_all").fetchall() == [
            ("binance",)
        ]
        tables = {
            r[0]
            for r in conn.execute("SELECT table_name FROM duckdb_tables()").fetchall()
        }
        assert "ohlcv" not in tables and "ohlcv_all" in tables

    def test_reads_through_the_view_are_unchanged_by_migrating(
        self, tmp_path: Any
    ) -> None:
        from tools.migrate_ohlcv_venue import migrate

        path = self._legacy_db(tmp_path)
        conn = duckdb.connect(str(path))
        before_rows = conn.execute(
            "SELECT symbol, timeframe, open_time, close FROM ohlcv ORDER BY open_time"
        ).fetchall()
        conn.close()

        migrate(path, force=True)

        conn = duckdb.connect(str(path))
        after_rows = conn.execute(
            "SELECT symbol, timeframe, open_time, close FROM ohlcv ORDER BY open_time"
        ).fetchall()
        assert after_rows == before_rows

    def test_second_run_is_a_no_op(self, tmp_path: Any) -> None:
        from tools.migrate_ohlcv_venue import AlreadyMigratedError, migrate

        path = self._legacy_db(tmp_path)
        migrate(path, force=True)
        with pytest.raises(AlreadyMigratedError):
            migrate(path, force=True)

    def test_refuses_without_a_fresh_backup(
        self, tmp_path: Any, monkeypatch: Any
    ) -> None:
        from tools.migrate_ohlcv_venue import StaleBackupError, migrate

        path = self._legacy_db(tmp_path)
        monkeypatch.setenv("BUIBUI_BACKUP_ROOT", str(tmp_path / "no-backups"))
        with pytest.raises(StaleBackupError, match="backup"):
            migrate(path, force=False)
        # and the database is untouched
        conn = duckdb.connect(str(path))
        tables = {
            r[0]
            for r in conn.execute("SELECT table_name FROM duckdb_tables()").fetchall()
        }
        assert "ohlcv" in tables and "ohlcv_all" not in tables
```

- [ ] **Step 2: Run them to verify they fail**

Run: `poetry run pytest tests/test_ohlcv_venue.py::TestMigration -q`

Expected: FAIL — `ModuleNotFoundError: No module named 'tools.migrate_ohlcv_venue'`.

- [ ] **Step 3: Write the tool (Write tool, not Bash)**

Create `tools/migrate_ohlcv_venue.py`:

```python
"""One-shot ST60(b) migration: rebuild `ohlcv` as venue-keyed `ohlcv_all` + a view.

Run ONCE, by the operator, against a database that is not being written to:

    poetry run python tools/migrate_ohlcv_venue.py

`analytics.db` is single-copy and gitignored and the 15-minute signal-watch timer owns
it, so this refuses to run unless a local backup snapshot is less than 24h old. Stop
the timer, run `make buibui-backup`, then run this.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analytics.store.venue import (  # noqa: E402
    DEFAULT_VENUE,
    ohlcv_view_sql,
    set_read_venue_order,
)

DEFAULT_DB = Path("analytics.db")
_MAX_BACKUP_AGE_HOURS = 24.0

_CREATE_OHLCV_ALL = """
    CREATE TABLE ohlcv_all (
        venue            TEXT   NOT NULL,
        symbol           TEXT   NOT NULL,
        timeframe        TEXT   NOT NULL,
        open_time        BIGINT NOT NULL,
        open             DOUBLE NOT NULL,
        high             DOUBLE NOT NULL,
        low              DOUBLE NOT NULL,
        close            DOUBLE NOT NULL,
        volume           DOUBLE NOT NULL,
        taker_buy_volume DOUBLE,
        PRIMARY KEY (venue, symbol, timeframe, open_time)
    )
"""


class AlreadyMigratedError(RuntimeError):
    """`ohlcv_all` already exists -- a second run would be a second rebuild."""


class StaleBackupError(RuntimeError):
    """No local backup snapshot is fresh enough to migrate against."""


def newest_backup_age_hours(root: Path) -> float | None:
    """Return the age in hours of the newest daily snapshot MANIFEST, or None."""
    manifests = sorted(root.glob("daily/*/MANIFEST.json"))
    if not manifests:
        return None
    newest = max(m.stat().st_mtime for m in manifests)
    return (time.time() - newest) / 3600.0


def _backup_root() -> Path:
    return Path(
        os.environ.get("BUIBUI_BACKUP_ROOT", str(Path.home() / "backups" / "buibui"))
    )


def migrate(db_path: Path, *, force: bool = False) -> tuple[int, int]:
    """Rebuild `ohlcv` as `ohlcv_all` + a Binance-pinned view. Returns (before, after)."""
    if not force:
        age = newest_backup_age_hours(_backup_root())
        if age is None or age > _MAX_BACKUP_AGE_HOURS:
            found = "none found" if age is None else f"newest is {age:.1f}h old"
            raise StaleBackupError(
                f"refusing to migrate: no backup under {_MAX_BACKUP_AGE_HOURS:.0f}h "
                f"({found}). Run `make buibui-backup`, or pass --force."
            )

    conn = duckdb.connect(str(db_path))
    try:
        tables = {
            r[0]
            for r in conn.execute("SELECT table_name FROM duckdb_tables()").fetchall()
        }
        # ⚠ WRONG AS WRITTEN — this refuses the real database. "Already migrated" keys on
        # `ohlcv` NOT being a table; an empty leftover `ohlcv_all` is expected. See the banner.
        if "ohlcv_all" in tables:
            raise AlreadyMigratedError(
                f"{db_path} already has ohlcv_all -- nothing to do."
            )
        if "ohlcv" not in tables:
            raise RuntimeError(f"{db_path} has no ohlcv table to migrate.")

        row = conn.execute("SELECT COUNT(*) FROM ohlcv").fetchone()
        before = int(row[0]) if row else 0

        conn.execute("BEGIN TRANSACTION")
        conn.execute(_CREATE_OHLCV_ALL)
        conn.execute(f"INSERT INTO ohlcv_all SELECT '{DEFAULT_VENUE}', * FROM ohlcv")
        row = conn.execute("SELECT COUNT(*) FROM ohlcv_all").fetchone()
        after = int(row[0]) if row else 0
        if after != before:
            conn.execute("ROLLBACK")
            raise RuntimeError(
                f"row count changed during copy ({before} -> {after}); rolled back."
            )
        conn.execute("DROP TABLE ohlcv")
        conn.execute(ohlcv_view_sql([DEFAULT_VENUE]))
        conn.execute(
            "CREATE TABLE IF NOT EXISTS db_meta "
            "(key TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY (key))"
        )
        set_read_venue_order(conn, [DEFAULT_VENUE])
        conn.execute("COMMIT")
        return before, after
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("db", nargs="?", type=Path, default=DEFAULT_DB)
    parser.add_argument(
        "--force",
        action="store_true",
        help="skip the backup-freshness check (you are on your own)",
    )
    args = parser.parse_args()

    if args.force:
        print("WARNING: --force skips the backup check.", file=sys.stderr)
    before, after = migrate(args.db, force=args.force)
    print(f"migrated {before:,} rows -> ohlcv_all ({after:,} under '{DEFAULT_VENUE}')")

    conn = duckdb.connect(str(args.db), read_only=True)
    try:
        sample = conn.execute(
            "SELECT symbol, timeframe, open_time, close FROM ohlcv "
            "ORDER BY open_time DESC LIMIT 3"
        ).fetchall()
    finally:
        conn.close()
    print("read-back through the view:")
    for line in sample:
        print(f"  {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the tests**

Run: `poetry run pytest tests/test_ohlcv_venue.py::TestMigration -q`

Expected: PASS, 4 tests.

- [ ] **Step 5: Verify the bare invocation works**

Every tool in `tools/` must run as `python3 tools/<name>.py` without `PYTHONPATH` — CI invokes
that form and the Make targets do not, which has shipped a red CI before.

Run: `python3 tools/migrate_ohlcv_venue.py --help`

Expected: the argparse help text, no `ModuleNotFoundError`.

- [ ] **Step 6: Commit**

```bash
git add tools/migrate_ohlcv_venue.py tests/test_ohlcv_venue.py
git commit -m "feat: add the one-shot ohlcv venue-key migration tool"
```

---

### Task 7: `export_live_db.py` copies `ohlcv_all` and stamps the CI read order

The CI signal-watch job seeds an ephemeral DB from this output, then syncs an OKX tail into it and
reads a mixed series back. Stamping `okx,binance` is what preserves that behaviour.

**Files:**

- Modify: `tools/export_live_db.py:28` (`LIVE_TABLES`), `:88-97` (the copy loop), and the module
  docstring
- Modify: `tests/test_export_live_db.py`

**Interfaces:**

- Consumes: `analytics.store.venue.{parse_venue_order, set_read_venue_order}`.
- Produces: `export_live_db(..., read_venue_order: str = "okx,binance")`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_export_live_db.py`:

```python
def test_exported_db_prefers_okx_then_binance(tmp_path: Any) -> None:
    """The slim DB exists to be EXTENDED by an OKX run, so its read order says so."""
    import duckdb

    from analytics.store import init_schema
    from analytics.store.venue import read_venue_order
    from tools.export_live_db import export_live_db

    src = tmp_path / "src.duckdb"
    conn = duckdb.connect(str(src))
    init_schema(conn)
    conn.execute(
        "INSERT INTO ohlcv_all VALUES "
        "('binance', 'BTCUSDT', '1h', 1, 10, 11, 9, 10.5, 100, 50)"
    )
    conn.close()

    out = tmp_path / "out.duckdb"
    export_live_db(src=src, out=out, ohlcv_symbols=["BTCUSDT"], now_ms=86_400_000)

    conn = duckdb.connect(str(out))
    assert read_venue_order(conn) == ["okx", "binance"]
    assert conn.execute("SELECT venue FROM ohlcv_all").fetchall() == [("binance",)]
    # An OKX bar synced later must shadow the seeded Binance bar for reads, without
    # destroying it -- this is exactly what the CI cycle does.
    conn.execute(
        "INSERT INTO ohlcv_all VALUES ('okx', 'BTCUSDT', '1h', 1, 10, 11, 9, 77.0, 100, NULL)"
    )
    assert conn.execute("SELECT close FROM ohlcv").fetchall() == [(77.0,)]
    assert conn.execute("SELECT COUNT(*) FROM ohlcv_all").fetchone()[0] == 2
```

- [ ] **Step 2: Run it to verify it fails**

Run: `poetry run pytest tests/test_export_live_db.py::test_exported_db_prefers_okx_then_binance -q`

Expected: FAIL — the exporter still copies a table called `ohlcv`.

- [ ] **Step 3: Update the exporter**

In `tools/export_live_db.py`, change `LIVE_TABLES[0]` from `"ohlcv"` to `"ohlcv_all"`, add the
import `from analytics.store.venue import parse_venue_order, set_read_venue_order`, add the
parameter `read_venue_order: str = "okx,binance"` to `export_live_db`, and change the scoped copy
branch:

```python
if table == "ohlcv_all":
    placeholders = ", ".join("?" for _ in ohlcv_symbols)
    df = src_con.execute(
        f"SELECT * FROM ohlcv_all WHERE symbol IN ({placeholders}) AND open_time >= ?",
        [*ohlcv_symbols, floor_ms],
    ).fetchdf()  # noqa: F841
```

After the copy loop, before the row-count rollup:

```python
        # The slim DB is seeded into an ephemeral CI database that a DATA_SOURCE=okx
        # run then extends. Preferring OKX and falling back to the Binance history
        # underneath reproduces the pre-ST60(b) mixed read exactly -- without letting
        # either venue overwrite the other.
        set_read_venue_order(out_con, parse_venue_order(read_venue_order))
```

Add a `--read-venue-order` CLI argument wired to that parameter, and update the module docstring's
`INSERT OR REPLACE INTO ohlcv` sentence to name `ohlcv_all`.

- [ ] **Step 4: Run the tests**

Run: `poetry run pytest tests/test_export_live_db.py -q`

Expected: PASS, all cases.

- [ ] **Step 5: Commit**

```bash
git add tools/export_live_db.py tests/test_export_live_db.py
git commit -m "feat: export ohlcv_all and stamp the slim DB's venue read order"
```

---

### Task 7b: Regenerate and commit `live_signal.duckdb`

**Added 2026-08-21 after the Task 6 review, which found this gap. It is a MERGE BLOCKER and was
missing from the original plan.**

The committed `live_signal.duckdb` is still legacy-shaped: `ohlcv` is a TABLE inside it. So
`.github/workflows/signal-watch.yaml:54` copies it to `analytics.db`, `init_schema` raises
`UnmigratedDatabaseError`, and **the hourly OKX workflow is red on this branch.** Task 7 fixes the
exporter *code*; nothing regenerates the artifact.

**Files:**

- Modify: `live_signal.duckdb` (committed binary, ~16.5 MB)

**Ordering is forced and cannot be rearranged:** the exporter reads the real `analytics.db`, so the
operator's migration must already have happened, and Task 7's code fix must already be in the tree.

- [ ] **Step 1: Confirm the preconditions**

Run: `git log --oneline -1` (Task 7 landed) and check `ohlcv` is a view in the live DB:
`poetry run python -c "import duckdb; c=duckdb.connect('analytics.db', read_only=True); print('ohlcv' in {r[0] for r in c.execute('SELECT table_name FROM duckdb_tables()').fetchall()})"`

Expected: `False` — `ohlcv` is a view, i.e. the DB is migrated.

- [ ] **Step 2: Take the timer window — OPERATOR ACTION**

`make export-live-db` opens the live DB read-only, and DuckDB refuses that while the 15-minute
daemon holds the write lock. Ask the operator to run:

```bash
systemctl --user stop buibui-signal-watch.timer
```

Do not stop it on their behalf without asking — it is their live system, and a stopped timer is a
**silent** outage.

- [ ] **Step 3: Regenerate**

Run: `make export-live-db`

Expected: `Exported live_signal.duckdb (N MB): {'ohlcv_all': …, 'confidence_ratings': …, …}` with a
non-zero `ohlcv_all` count.

- [ ] **Step 4: Verify the artifact is venue-shaped and stamped**

```bash
poetry run python -c "
import duckdb
c = duckdb.connect('live_signal.duckdb', read_only=True)
print('tables:', sorted(r[0] for r in c.execute('SELECT table_name FROM duckdb_tables()').fetchall()))
print('order :', c.execute(\"SELECT value FROM db_meta WHERE key='read_venue_order'\").fetchall())
print('rows  :', c.execute('SELECT count(*) FROM ohlcv_all').fetchone()[0])
"
```

Expected: `ohlcv` absent from the table list (it is a view), `ohlcv_all` present with rows, and the
stored order `okx,binance`.

- [ ] **Step 5: Restart the timer — OPERATOR ACTION**

```bash
systemctl --user start buibui-signal-watch.timer
```

Then confirm one cycle succeeds before moving on — "started" is not "working".

- [ ] **Step 6: Commit**

```bash
git add live_signal.duckdb
git commit -m "chore: regenerate live_signal.duckdb with the venue-keyed schema"
```

---

### Task 8: Final gates and the pre-registered goldens claim

**Files:** none modified unless a gate fails.

- [ ] **Step 1: Lint and typecheck**

Run: `make lint-py && make typecheck`

Expected: both clean. ⚠ `make lint-py` runs `ruff format .`, which reformats python fences inside
`.md` files — if it rewrites this plan or the spec, commit that reformat on its own.

- [ ] **Step 2: Full suite, backgrounded, with a wall-clock reading**

Run: `time make test` with `run_in_background: true`

Expected: green. **Record the wall clock.** Spec §9 leaves suite-level perf unmeasured and
§10's D2 names a material regression as the trigger to reconsider the view. `main` measured
~4m55s on 4205 tests on 2026-08-20.

- [ ] **Step 3: The goldens leg — pre-registered**

Run: `make test-regression` with `run_in_background: true`

Expected: **green with the goldens unmoved.** The view returns the same rows in the same order as
the old table, so this is a falsifiable claim, not a formality. **If a golden moves, STOP.** Do
not run `make regression-update`; find out which read changed and why, and report it.

- [ ] **Step 4: Report both readings**

State the `make test` wall clock against the ~4m55s baseline and the regression result explicitly,
including "goldens unmoved" as a positive statement rather than silence.

- [ ] **Step 5: Clean-clone gate**

Run: `make preflight`

Expected: green on a clean clone of the branch's committed HEAD. This replaces the final
`make test` only; it refuses on a dirty tree, so commit everything first.

- [ ] **Step 6: Hand the migration to the operator**

The tool is **not** run by the session. Report to the operator, verbatim:

```text
Before anything touches analytics.db:
  1. systemctl --user stop buibui-signal-watch.timer   (the 15-min timer owns the file)
  2. make buibui-backup
  3. poetry run python tools/migrate_ohlcv_venue.py
  4. systemctl --user start buibui-signal-watch.timer
Expected output: "migrated 2,003,340 rows -> ohlcv_all (2,003,340 under 'binance')"
plus a three-row read-back through the view.
```

⚠ Until this runs, every writer entry point raises `UnmigratedDatabaseError` against the real DB.
That is the guard working, not a failure — but it means the daemon is down until step 3 completes.

- [ ] **Step 7: `/post-branch`**

Invoke `/post-branch` and run its phase table. It owns the doc sweep (`AGENTS.md`'s ST60 paragraph
and the `ohlcv has NO venue column` rule both go stale on this branch), the SoT ST60(b) row, the
MEMORY.md update, and the PR. Phases 0–4 run **before** `gh pr create` so the doc fixes ship in the
initial push.

---

## Self-Review

**Spec coverage:** §3 schema → Tasks 1–2. §4 reads, both exceptions → Tasks 2 and 4. §5 writes →
Tasks 2 and 5. §6 migration + refusal guard → Tasks 3 and 6. §7 exporter + CI → Task 7. §8 tests
and the pre-registered goldens claim → distributed across Tasks 1–7 with the claim itself in
Task 8. §9's unmeasured-perf caveat → Task 8 Step 2. No spec section is unimplemented.

**Two counts in the spec are wrong and this plan supersedes them.** Spec §5 and the header say
"22 test insert sites". That covers only the raw `INSERT INTO ohlcv` statements; there are also
**39 `upsert_ohlcv(...)` call sites in tests** that a required `venue` argument breaks, for ~61
edits rather than 22. The spec's scope line should be corrected during the Task 8 doc sweep.

**The required-vs-defaulted `venue` decision** is not in the spec's decision log and belongs
there: a defaulted `venue="binance"` would have left all 39 call sites untouched, but it would
reintroduce a silent default on the exact writer whose silent behaviour caused ST60 — an OKX run
that forgot to plumb venue would tag its bars `binance` and overwrite Binance history again. The
churn is the price of not rebuilding the defect. Add this as D7 during Task 8.
