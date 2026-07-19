# M5 Live Alert Outcomes UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the Live Alert Outcomes card a symbol dimension, sortable tables, and a live-marked view of open alerts, without adding a network dependency to the ground-truth tables.

**Architecture:** Additive changes to `analytics/stats/live_outcomes.py` (pure, DB-only) add a symbol filter, a chip list, and two new functions for open positions — one that reads the ledger, one that does all price arithmetic as a pure function over a plain dict. The API splits by cadence: `/api/live-outcomes` stays a pure DuckDB read while a new `/api/live-outcomes/open` carries the sole mark-price dependency. The card is extracted from `Stats.svelte` into `LiveOutcomes.svelte` in a behaviour-free move, then gains its features.

**Tech Stack:** Python 3.11+, DuckDB, FastAPI, Pydantic, pytest; Svelte 5 (runes) + Vite + TypeScript.

**Spec:** `docs/superpowers/specs/2026-07-19-m5-live-outcomes-ux-design.md`

## Global Constraints

- Every Python function needs full type annotations including the return type (`-> None` for test methods). `mypy` runs in strict mode (`disallow_untyped_defs = true`).
- Gate after every task: `make lint-py` ✓, `make typecheck` ✓, `make test` green. Tasks 4 and 5 additionally need `make web-build` ✓.
- `make test-regression` goldens must stay **unmoved**. Nothing in this plan touches a backtest path; any golden movement is a bug in your change, not a reason to regenerate.
- Tests must never make real network calls. Pass a `MagicMock` directly as the `client`.
- Analytics tests use `duckdb.connect(":memory:")` — never touch the real `analytics.db`.
- `symbol=None` must be **byte-identical** to today's output. It is the default and the `ALL` chip; a regression there silently changes the card everyone reads.
- Unrealized R is **gross of costs** and must be labelled as such wherever it surfaces. Resolved `outcome_r` in the tables is net. Never present them as the same basis.
- Run tests in the **foreground**. Do not background them.
- Conventional commits: `feat:`, `fix:`, `test:`, `docs:`, `refactor:`.

---

### Task 1: Symbol filter and chip list

**Files:**

- Modify: `analytics/stats/live_outcomes.py`
- Modify: `analytics/stats/__init__.py:11-16,54-57,74`
- Test: `tests/test_live_outcomes_stats.py`

**Interfaces:**

- Consumes: nothing (first task).
- Produces:
  - `LiveOutcomeSymbolRow` dataclass with fields `symbol: str`, `n: int`
  - `compute_live_outcomes(conn, days=30, min_n=1, *, symbol: str | None = None) -> LiveOutcomesResult`
  - `LiveOutcomesResult.symbols: list[LiveOutcomeSymbolRow]` (appended as the **last** field)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_live_outcomes_stats.py`. Note `_insert` already accepts a `symbol` keyword.

```python
def test_symbol_filter_slices_rollup_and_tables() -> None:
    conn = _conn()
    _insert(conn, "b1", symbol="BTCUSDT", outcome="win", outcome_r=1.0)
    _insert(conn, "b2", symbol="BTCUSDT", outcome="loss", outcome_r=-1.0)
    _insert(conn, "e1", symbol="ETHUSDT", outcome="win", outcome_r=2.0)

    res = compute_live_outcomes(conn, days=0, min_n=1, symbol="BTCUSDT")

    # Roll-up follows the filter too (spec D2) — not just the tables.
    assert res.rollup.total_rows == 2
    assert res.rollup.resolved == 2
    assert res.rollup.wins == 1
    assert res.rollup.losses == 1
    # Tables: avg_r over BTC rows only = (1.0 - 1.0) / 2 = 0.0
    assert len(res.by_strategy) == 1
    assert res.by_strategy[0].avg_r == 0.0


def test_symbol_none_matches_unfiltered_baseline() -> None:
    conn = _conn()
    _insert(conn, "b1", symbol="BTCUSDT", outcome="win", outcome_r=1.0)
    _insert(conn, "e1", symbol="ETHUSDT", outcome="loss", outcome_r=-1.0)

    explicit_none = compute_live_outcomes(conn, days=0, min_n=1, symbol=None)
    default = compute_live_outcomes(conn, days=0, min_n=1)

    assert explicit_none.rollup == default.rollup
    assert explicit_none.cells == default.cells
    assert explicit_none.by_strategy == default.by_strategy
    assert default.rollup.total_rows == 2


def test_symbols_chip_list_is_global_and_stable() -> None:
    conn = _conn()
    _insert(conn, "b1", symbol="BTCUSDT", outcome="win", outcome_r=1.0)
    _insert(conn, "b2", symbol="BTCUSDT", outcome="win", outcome_r=1.0)
    _insert(conn, "e1", symbol="ETHUSDT", outcome="win", outcome_r=1.0)
    # Old row: must still count toward the chip list despite the days window.
    _insert(
        conn,
        "s1",
        symbol="SOLUSDT",
        outcome="win",
        outcome_r=1.0,
        fired_at_ms=_NOW_MS - 400 * _DAY_MS,
    )

    filtered = compute_live_outcomes(conn, days=30, min_n=1, symbol="ETHUSDT")

    # Chips are global: unaffected by BOTH the symbol filter and the days window.
    assert [(r.symbol, r.n) for r in filtered.symbols] == [
        ("BTCUSDT", 2),
        ("ETHUSDT", 1),
        ("SOLUSDT", 1),
    ]


def test_unknown_symbol_returns_zero_rollup_not_error() -> None:
    conn = _conn()
    _insert(conn, "b1", symbol="BTCUSDT", outcome="win", outcome_r=1.0)

    res = compute_live_outcomes(conn, days=0, min_n=1, symbol="DOGEUSDT")

    assert res.rollup.total_rows == 0
    assert res.cells == []
    assert res.by_strategy == []
    # Chips still list the real symbols so the operator can navigate back.
    assert [r.symbol for r in res.symbols] == ["BTCUSDT"]
```

Add `LiveOutcomeSymbolRow` to the import block at the top of the test file:

```python
from analytics.stats import (
    LiveOutcomeCell,
    LiveOutcomesResult,
    LiveOutcomeStrategyRow,
    LiveOutcomeSymbolRow,
    compute_live_outcomes,
)
```

Then use it once so the import is not unused — add this assertion at the end of `test_symbols_chip_list_is_global_and_stable`:

```python
    assert isinstance(filtered.symbols[0], LiveOutcomeSymbolRow)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `poetry run pytest tests/test_live_outcomes_stats.py -v`

Expected: FAIL — `ImportError: cannot import name 'LiveOutcomeSymbolRow'`.

- [ ] **Step 3: Add the dataclass and the result field**

In `analytics/stats/live_outcomes.py`, add after `LiveOutcomeStrategyRow`:

```python
@dataclass
class LiveOutcomeSymbolRow:
    """One symbol chip: the symbol and its all-time alert count."""

    symbol: str
    n: int
```

Append `symbols` as the **last** field of `LiveOutcomesResult` (appending keeps every existing keyword construction valid):

```python
@dataclass
class LiveOutcomesResult:
    """Full live-outcomes payload for the Stats card."""

    days: int  # window applied to cells/by_strategy (0 = all time)
    min_n: int  # minimum resolved rows per cell/strategy
    rollup: LiveOutcomesRollup
    cells: list[LiveOutcomeCell]
    by_strategy: list[LiveOutcomeStrategyRow]
    symbols: list[LiveOutcomeSymbolRow]  # chip list — always global, all-time
```

- [ ] **Step 4: Thread the symbol filter through all three queries**

Change the signature to add a keyword-only `symbol`, and update the docstring — the old one promises an always-global roll-up, which is no longer true:

```python
def compute_live_outcomes(
    conn: duckdb.DuckDBPyConnection,
    days: int = 30,
    min_n: int = 1,
    *,
    symbol: str | None = None,
) -> LiveOutcomesResult:
    """Compute the live-outcomes roll-up + breakdowns.

    ``days`` windows only the per-cell / per-strategy tables (0 = all time).

    ``symbol`` scopes the roll-up AND both tables to one symbol; ``None`` (the
    default, and the UI's ALL chip) is the global view and is byte-identical to
    the pre-symbol-filter behaviour. Under a symbol filter ``open_no_tp``
    reports that symbol's integrity rather than the ledger's.

    ``symbols`` (the chip list) is always global and all-time, unaffected by
    both arguments, so chips never disappear or churn as filters change.

    Never raises on empty data — returns a zero roll-up.
    """
```

Replace the roll-up query with a symbol-aware version:

```python
    sym_where = ""
    sym_params: tuple[object, ...] = ()
    if symbol:
        sym_where = "WHERE symbol = ?"
        sym_params = (symbol,)

    totals = conn.execute(
        f"""
        SELECT
          COUNT(*)                                     AS total_rows,
          COUNT(*) FILTER (WHERE outcome IS NOT NULL)  AS resolved,
          COUNT(*) FILTER (WHERE outcome IS NULL)      AS open_rows,
          COUNT(*) FILTER (WHERE outcome IS NULL
                           AND tp_price IS NULL)       AS open_no_tp,
          COUNT(*) FILTER (WHERE outcome = 'win')      AS wins,
          COUNT(*) FILTER (WHERE outcome = 'loss')     AS losses,
          COUNT(*) FILTER (WHERE outcome = 'expired')  AS expired
        FROM signal_alert_outcomes
        {sym_where}
        """,
        sym_params,
    ).fetchone()
```

Extend the shared table WHERE clause. The symbol clause goes **before** the days clause so parameter order matches the SQL:

```python
    where = "WHERE outcome IS NOT NULL"
    params: list[object] = []
    if symbol:
        where += " AND symbol = ?"
        params.append(symbol)
    if days > 0:
        cutoff_ms = int((time.time() - days * 86_400) * 1000)
        where += " AND fired_at_ms >= ?"
        params.append(cutoff_ms)
```

The `cell_rows` and `strat_rows` queries already interpolate `{where}` and pass `(*params, min_n)`, so they need no further change.

- [ ] **Step 5: Add the chip query and return it**

Add before the `return`:

```python
    symbol_rows = conn.execute(
        """
        SELECT symbol, COUNT(*) AS n
        FROM signal_alert_outcomes
        GROUP BY symbol
        ORDER BY n DESC, symbol ASC
        """
    ).fetchall()

    symbols = [
        LiveOutcomeSymbolRow(symbol=str(sym), n=int(n)) for (sym, n) in symbol_rows
    ]
```

Add `symbols=symbols` to the `LiveOutcomesResult(...)` construction.

- [ ] **Step 6: Export the new dataclass**

In `analytics/stats/__init__.py`, add `LiveOutcomeSymbolRow` to the
`from analytics.stats.live_outcomes import (...)` block and to `__all__`,
keeping both lists alphabetically sorted as they already are.

- [ ] **Step 7: Run the tests to verify they pass**

Run: `poetry run pytest tests/test_live_outcomes_stats.py -v`

Expected: PASS — all tests, including the six pre-existing ones (the
no-regression guard).

- [ ] **Step 8: Run the gate**

Run: `make lint-py && make typecheck && make test`

Expected: ruff clean, mypy clean, full suite green.

- [ ] **Step 9: Commit**

```bash
git add analytics/stats/live_outcomes.py analytics/stats/__init__.py tests/test_live_outcomes_stats.py
git commit -m "feat(stats): symbol filter + chip list for live outcomes"
```

---

### Task 2: Open positions and pure marking

**Files:**

- Modify: `analytics/stats/live_outcomes.py`
- Modify: `analytics/stats/__init__.py`
- Test: `tests/test_live_outcomes_stats.py`

**Interfaces:**

- Consumes: nothing from Task 1 (independent; both edit the same module).
- Produces:
  - `OpenPosition` dataclass: `signal_id: str`, `symbol: str`, `strategy: str`, `tf: str`, `direction: str`, `fired_at_ms: int`, `entry_price: float | None`, `sl_price: float | None`, `tp_price: float | None`
  - `MarkedOpenPosition` dataclass: `position: OpenPosition`, `mark: float | None`, `unrealized_r: float | None`, `dist_sl_pct: float | None`, `dist_tp_pct: float | None`
  - `open_positions(conn, *, symbol: str | None = None) -> list[OpenPosition]`
  - `mark_open_positions(rows: Sequence[OpenPosition], marks: Mapping[str, float]) -> list[MarkedOpenPosition]`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_live_outcomes_stats.py`:

```python
def _open_pos(
    *,
    symbol: str = "BTCUSDT",
    direction: str = "long",
    entry: float | None = 100.0,
    sl: float | None = 95.0,
    tp: float | None = 110.0,
) -> OpenPosition:
    return OpenPosition(
        signal_id="x",
        symbol=symbol,
        strategy="bos",
        tf="1h",
        direction=direction,
        fired_at_ms=_NOW_MS,
        entry_price=entry,
        sl_price=sl,
        tp_price=tp,
    )


def test_open_positions_returns_only_unresolved_newest_first() -> None:
    conn = _conn()
    _insert(conn, "resolved", outcome="win", outcome_r=1.0)
    _insert(conn, "older", outcome=None, outcome_r=None, fired_at_ms=_NOW_MS - 1000)
    _insert(conn, "newer", outcome=None, outcome_r=None, fired_at_ms=_NOW_MS)

    rows = open_positions(conn)

    assert [r.signal_id for r in rows] == ["newer", "older"]
    assert rows[0].entry_price == 100.0
    assert rows[0].sl_price == 95.0


def test_open_positions_honours_symbol_filter() -> None:
    conn = _conn()
    _insert(conn, "b", symbol="BTCUSDT", outcome=None, outcome_r=None)
    _insert(conn, "e", symbol="ETHUSDT", outcome=None, outcome_r=None)

    assert [r.signal_id for r in open_positions(conn, symbol="ETHUSDT")] == ["e"]


def test_mark_long_and_short_unrealized_r_sign() -> None:
    # risk = |100 - 95| = 5. Long at mark 105 → +1R. Short at mark 105 → -1R.
    long_pos = _open_pos(direction="long", entry=100.0, sl=95.0)
    short_pos = _open_pos(direction="short", entry=100.0, sl=105.0)

    marked = mark_open_positions([long_pos, short_pos], {"BTCUSDT": 105.0})

    assert marked[0].unrealized_r is not None
    assert abs(marked[0].unrealized_r - 1.0) < 1e-9
    assert marked[1].unrealized_r is not None
    assert abs(marked[1].unrealized_r - (-1.0)) < 1e-9


def test_mark_computes_distances_to_sl_and_tp() -> None:
    pos = _open_pos(entry=100.0, sl=95.0, tp=110.0)

    marked = mark_open_positions([pos], {"BTCUSDT": 100.0})

    assert marked[0].mark == 100.0
    assert marked[0].dist_sl_pct is not None
    assert abs(marked[0].dist_sl_pct - 0.05) < 1e-9
    assert marked[0].dist_tp_pct is not None
    assert abs(marked[0].dist_tp_pct - 0.10) < 1e-9


def test_mark_missing_symbol_yields_all_none() -> None:
    marked = mark_open_positions([_open_pos()], {"ETHUSDT": 3000.0})

    assert marked[0].mark is None
    assert marked[0].unrealized_r is None
    assert marked[0].dist_sl_pct is None
    assert marked[0].dist_tp_pct is None
    # The ledger row itself still comes back.
    assert marked[0].position.symbol == "BTCUSDT"


def test_mark_zero_risk_does_not_divide_by_zero() -> None:
    # entry == sl → risk 0. Must yield None, not ZeroDivisionError or inf.
    pos = _open_pos(entry=100.0, sl=100.0)

    marked = mark_open_positions([pos], {"BTCUSDT": 105.0})

    assert marked[0].unrealized_r is None
    # Distance to the stop is still meaningful.
    assert marked[0].dist_sl_pct is not None


def test_mark_null_entry_or_tp_degrades_field_by_field() -> None:
    no_entry = mark_open_positions([_open_pos(entry=None)], {"BTCUSDT": 105.0})
    assert no_entry[0].unrealized_r is None

    no_tp = mark_open_positions([_open_pos(tp=None)], {"BTCUSDT": 105.0})
    assert no_tp[0].dist_tp_pct is None
    # Everything else still computed.
    assert no_tp[0].unrealized_r is not None
    assert no_tp[0].dist_sl_pct is not None


def test_mark_zero_mark_price_yields_none() -> None:
    # A zero mark is a bad tick, not a price — every derived field degrades.
    marked = mark_open_positions([_open_pos()], {"BTCUSDT": 0.0})

    assert marked[0].unrealized_r is None
    assert marked[0].dist_sl_pct is None
    assert marked[0].dist_tp_pct is None


def test_mark_empty_input_returns_empty() -> None:
    assert mark_open_positions([], {"BTCUSDT": 100.0}) == []
```

Extend the test file's import block:

```python
from analytics.stats import (
    LiveOutcomeCell,
    LiveOutcomesResult,
    LiveOutcomeStrategyRow,
    LiveOutcomeSymbolRow,
    OpenPosition,
    compute_live_outcomes,
    mark_open_positions,
    open_positions,
)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `poetry run pytest tests/test_live_outcomes_stats.py -v`

Expected: FAIL — `ImportError: cannot import name 'OpenPosition'`.

- [ ] **Step 3: Add the dataclasses**

In `analytics/stats/live_outcomes.py`, extend the imports at the top:

```python
from collections.abc import Mapping, Sequence
```

Add the dataclasses after `LiveOutcomeSymbolRow`:

```python
@dataclass
class OpenPosition:
    """One unresolved ledger row — the alert is still live."""

    signal_id: str
    symbol: str
    strategy: str
    tf: str
    direction: str
    fired_at_ms: int
    entry_price: float | None
    sl_price: float | None
    tp_price: float | None


@dataclass
class MarkedOpenPosition:
    """An open position with current-price arithmetic attached.

    ``unrealized_r`` is GROSS of costs, unlike the resolved ``outcome_r`` in
    the tables, which is net of fee + slippage + funding. Netting an open
    position would mean accruing funding to the current moment; the UI labels
    this column gross instead.
    """

    position: OpenPosition
    mark: float | None
    unrealized_r: float | None
    dist_sl_pct: float | None
    dist_tp_pct: float | None
```

- [ ] **Step 4: Implement the ledger read**

```python
def open_positions(
    conn: duckdb.DuckDBPyConnection,
    *,
    symbol: str | None = None,
) -> list[OpenPosition]:
    """Return unresolved ledger rows, newest first.

    Pure DB read — no price, no network, no clock.
    """
    where = "WHERE outcome IS NULL"
    params: list[object] = []
    if symbol:
        where += " AND symbol = ?"
        params.append(symbol)

    rows = conn.execute(
        f"""
        SELECT signal_id, symbol, strategy, tf, direction, fired_at_ms,
               entry_price, sl_price, tp_price
        FROM signal_alert_outcomes
        {where}
        ORDER BY fired_at_ms DESC
        """,
        tuple(params),
    ).fetchall()

    return [
        OpenPosition(
            signal_id=str(sid),
            symbol=str(sym),
            strategy=str(strategy),
            tf=str(tf),
            direction=str(direction),
            fired_at_ms=int(fired_at_ms),
            entry_price=None if entry is None else float(entry),
            sl_price=None if sl is None else float(sl),
            tp_price=None if tp is None else float(tp),
        )
        for (
            sid,
            sym,
            strategy,
            tf,
            direction,
            fired_at_ms,
            entry,
            sl,
            tp,
        ) in rows
    ]
```

- [ ] **Step 5: Implement the pure marking**

```python
def mark_open_positions(
    rows: Sequence[OpenPosition],
    marks: Mapping[str, float],
) -> list[MarkedOpenPosition]:
    """Attach mark price, gross unrealized R, and SL/TP distances.

    Pure: no clock, no network, no DB. Every derived field degrades to ``None``
    rather than raising, so a missing price or a degenerate stop never breaks
    the panel.
    """
    marked: list[MarkedOpenPosition] = []

    for pos in rows:
        mark = marks.get(pos.symbol)
        unrealized_r: float | None = None
        dist_sl_pct: float | None = None
        dist_tp_pct: float | None = None

        # A missing or zero mark is a bad tick — nothing derived is meaningful.
        if mark is not None and mark > 0:
            if pos.entry_price is not None and pos.sl_price is not None:
                risk = abs(pos.entry_price - pos.sl_price)
                if risk > 0:
                    gain = (
                        mark - pos.entry_price
                        if pos.direction == "long"
                        else pos.entry_price - mark
                    )
                    unrealized_r = gain / risk
                dist_sl_pct = abs(mark - pos.sl_price) / mark
            if pos.tp_price is not None:
                dist_tp_pct = abs(mark - pos.tp_price) / mark

        marked.append(
            MarkedOpenPosition(
                position=pos,
                mark=mark if mark is not None and mark > 0 else None,
                unrealized_r=unrealized_r,
                dist_sl_pct=dist_sl_pct,
                dist_tp_pct=dist_tp_pct,
            )
        )

    return marked
```

- [ ] **Step 6: Export the new names**

In `analytics/stats/__init__.py`, add `MarkedOpenPosition`, `OpenPosition`,
`mark_open_positions`, and `open_positions` to the import block and to
`__all__`, preserving the existing alphabetical ordering.

- [ ] **Step 7: Run the tests to verify they pass**

Run: `poetry run pytest tests/test_live_outcomes_stats.py -v`

Expected: PASS.

- [ ] **Step 8: Run the gate**

Run: `make lint-py && make typecheck && make test`

Expected: all clean and green.

- [ ] **Step 9: Commit**

```bash
git add analytics/stats/live_outcomes.py analytics/stats/__init__.py tests/test_live_outcomes_stats.py
git commit -m "feat(stats): open-position read + pure mark-price arithmetic"
```

---

### Task 3: API routes and the TypeScript client contract

**Files:**

- Modify: `web/api/models/live_outcomes.py`
- Modify: `web/api/routers/live_outcomes.py`
- Modify: `web/ui/src/api.ts:576-614`
- Test: `tests/test_web_live_outcomes.py`

**Interfaces:**

- Consumes: `compute_live_outcomes(..., symbol=...)` and `LiveOutcomesResult.symbols` from Task 1; `open_positions`, `mark_open_positions`, `OpenPosition`, `MarkedOpenPosition` from Task 2.
- Produces:
  - `GET /api/live-outcomes?days&min_n&symbol` — response gains `symbols: [{symbol, n}]`
  - `GET /api/live-outcomes/open?symbol` — `{symbol, marks_ok, marked_at_ms, positions[]}`
  - TypeScript: `LiveOutcomeSymbolRow`, `LiveOpenPosition`, `LiveOpenPositionsResponse`, `getLiveOutcomes(days, minN, symbol?)`, `getLiveOutcomesOpen(symbol?)`

- [ ] **Step 1: Write the failing tests**

The existing `_client_for` helper in `tests/test_web_live_outcomes.py` overrides
only `get_db`. The `/open` route also depends on `get_client`, so the helper
needs an optional client override. Replace the helper with:

```python
def _client_for(
    conn: duckdb.DuckDBPyConnection,
    binance: MagicMock | None = None,
) -> Generator[TestClient]:
    """Yield a TestClient whose get_db returns ``conn`` and get_client ``binance``."""
    from web.api.deps import get_client, get_db, require_token
    from web.api.main import app

    stub = binance if binance is not None else MagicMock()
    app.dependency_overrides[get_db] = lambda: conn
    app.dependency_overrides[get_client] = lambda: stub
    app.dependency_overrides[require_token] = lambda: None
    with (
        patch("web.api.main.duckdb.connect", return_value=MagicMock()),
        patch("web.api.main.create_client", return_value=MagicMock()),
        patch("web.api.main.init_schema"),
        TestClient(app) as client,
    ):
        yield client
    app.dependency_overrides.clear()
```

Then append these tests:

```python
def test_live_outcomes_includes_symbol_chip_list() -> None:
    conn = _seed_conn()
    client_gen = _client_for(conn)
    client = next(client_gen)
    try:
        data = client.get("/api/live-outcomes?days=0&min_n=1").json()
        # Seed: 2 BTCUSDT rows, 1 ETHUSDT row — ordered by count desc.
        assert [(s["symbol"], s["n"]) for s in data["symbols"]] == [
            ("BTCUSDT", 2),
            ("ETHUSDT", 1),
        ]
    finally:
        with pytest.raises(StopIteration):
            next(client_gen)


def test_live_outcomes_symbol_param_slices() -> None:
    conn = _seed_conn()
    client_gen = _client_for(conn)
    client = next(client_gen)
    try:
        data = client.get("/api/live-outcomes?days=0&min_n=1&symbol=ETHUSDT").json()
        assert data["rollup"]["total_rows"] == 1
        assert data["rollup"]["open"] == 1
        # Chips stay global even under the filter.
        assert len(data["symbols"]) == 2
    finally:
        with pytest.raises(StopIteration):
            next(client_gen)


def test_open_endpoint_marks_positions() -> None:
    conn = _seed_conn()
    binance = MagicMock()
    binance.futures_mark_price.return_value = [
        {"symbol": "ETHUSDT", "markPrice": "105.0"},
        {"symbol": "BTCUSDT", "markPrice": "64000.0"},
    ]
    client_gen = _client_for(conn, binance)
    client = next(client_gen)
    try:
        resp = client.get("/api/live-outcomes/open")
        assert resp.status_code == 200
        data = resp.json()

        assert data["marks_ok"] is True
        assert data["marked_at_ms"] > 0
        # Seed row 'c' is the only open one: ETHUSDT long, entry 100, sl 95.
        assert len(data["positions"]) == 1
        pos = data["positions"][0]
        assert pos["symbol"] == "ETHUSDT"
        assert pos["mark"] == 105.0
        # risk = 5, gain = 5 → +1R
        assert abs(pos["unrealized_r"] - 1.0) < 1e-9
        # tp_price is NULL on the seed row.
        assert pos["dist_tp_pct"] is None
    finally:
        with pytest.raises(StopIteration):
            next(client_gen)


def test_open_endpoint_survives_price_failure() -> None:
    conn = _seed_conn()
    binance = MagicMock()
    binance.futures_mark_price.side_effect = RuntimeError("binance unreachable")
    client_gen = _client_for(conn, binance)
    client = next(client_gen)
    try:
        resp = client.get("/api/live-outcomes/open")
        # Never 5xx because a price feed is down.
        assert resp.status_code == 200
        data = resp.json()

        assert data["marks_ok"] is False
        # Ledger rows still come back, price columns null.
        assert len(data["positions"]) == 1
        assert data["positions"][0]["mark"] is None
        assert data["positions"][0]["unrealized_r"] is None
        assert data["positions"][0]["entry_price"] == 100.0
    finally:
        with pytest.raises(StopIteration):
            next(client_gen)


def test_open_endpoint_symbol_filter() -> None:
    conn = _seed_conn()
    client_gen = _client_for(conn)
    client = next(client_gen)
    try:
        data = client.get("/api/live-outcomes/open?symbol=BTCUSDT").json()
        # The only open seed row is ETHUSDT.
        assert data["positions"] == []
        assert data["symbol"] == "BTCUSDT"
    finally:
        with pytest.raises(StopIteration):
            next(client_gen)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `poetry run pytest tests/test_web_live_outcomes.py -v`

Expected: FAIL — the chip-list test fails on a missing `symbols` key, and the
`/open` tests return 404.

- [ ] **Step 3: Add the Pydantic models**

Append to `web/api/models/live_outcomes.py`:

```python
class LiveOutcomeSymbolModel(BaseModel):
    symbol: str
    n: int


class LiveOpenPositionModel(BaseModel):
    signal_id: str
    symbol: str
    strategy: str
    tf: str
    direction: str
    fired_at_ms: int
    entry_price: float | None
    sl_price: float | None
    tp_price: float | None
    mark: float | None
    unrealized_r: float | None  # GROSS of costs — see the card help text
    dist_sl_pct: float | None
    dist_tp_pct: float | None


class LiveOpenPositionsResponse(BaseModel):
    symbol: str | None
    marks_ok: bool
    marked_at_ms: int
    positions: list[LiveOpenPositionModel]
```

Add `symbols` as the last field of `LiveOutcomesResponse`:

```python
class LiveOutcomesResponse(BaseModel):
    days: int
    min_n: int
    rollup: LiveOutcomesRollupModel
    cells: list[LiveOutcomeCellModel]
    by_strategy: list[LiveOutcomeStrategyModel]
    symbols: list[LiveOutcomeSymbolModel]
```

- [ ] **Step 4: Extend the existing route**

In `web/api/routers/live_outcomes.py`, update the imports:

```python
import time

from binance.client import Client

from analytics.stats import compute_live_outcomes, mark_open_positions, open_positions
from web.api.deps import get_client, get_db, require_token
from web.api.models.live_outcomes import (
    LiveOpenPositionModel,
    LiveOpenPositionsResponse,
    LiveOutcomeCellModel,
    LiveOutcomesResponse,
    LiveOutcomesRollupModel,
    LiveOutcomeStrategyModel,
    LiveOutcomeSymbolModel,
)
```

Add the `symbol` parameter and pass it through:

```python
@router.get("/live-outcomes", response_model=LiveOutcomesResponse)
def get_live_outcomes(
    days: int = Query(default=30, ge=0, le=365),
    min_n: int = Query(default=1, ge=1, le=100),
    symbol: str | None = Query(default=None),
    db: duckdb.DuckDBPyConnection = Depends(get_db),
) -> LiveOutcomesResponse:
    """Return the live signal-alert outcome roll-up + per-cell breakdowns.

    ``days`` windows the per-cell / per-strategy tables (0 = all time).
    ``symbol`` scopes the roll-up and both tables to one symbol; omitting it is
    the global view. An unknown symbol returns a zero roll-up, not 404. Empty
    ledger returns a zero roll-up, not 404.
    """
    result = compute_live_outcomes(db, days=days, min_n=min_n, symbol=symbol)
```

Add `symbols` to the returned `LiveOutcomesResponse(...)`:

```python
        symbols=[
            LiveOutcomeSymbolModel(symbol=s.symbol, n=s.n) for s in result.symbols
        ],
```

- [ ] **Step 5: Add the open route**

Append to `web/api/routers/live_outcomes.py`:

```python
@router.get("/live-outcomes/open", response_model=LiveOpenPositionsResponse)
def get_live_outcomes_open(
    symbol: str | None = Query(default=None),
    db: duckdb.DuckDBPyConnection = Depends(get_db),
    client: Client = Depends(get_client),
) -> LiveOpenPositionsResponse:
    """Return unresolved alerts marked to the current price.

    The mark-price call is best-effort: any failure sets ``marks_ok=False`` and
    returns every ledger row with null price columns. This route never 5xxs
    because the exchange is unreachable, rate-limited, or slow.
    """
    positions = open_positions(db, symbol=symbol)

    marks: dict[str, float] = {}
    marks_ok = True
    try:
        payload = client.futures_mark_price()
        rows = payload if isinstance(payload, list) else [payload]
        for row in rows:
            if not isinstance(row, dict):
                continue
            sym = row.get("symbol")
            price = row.get("markPrice")
            if sym is None or price is None:
                continue
            try:
                marks[str(sym)] = float(price)
            except (TypeError, ValueError):
                continue
    except Exception:
        marks_ok = False
        marks = {}

    marked = mark_open_positions(positions, marks)

    return LiveOpenPositionsResponse(
        symbol=symbol,
        marks_ok=marks_ok,
        marked_at_ms=int(time.time() * 1000),
        positions=[
            LiveOpenPositionModel(
                signal_id=m.position.signal_id,
                symbol=m.position.symbol,
                strategy=m.position.strategy,
                tf=m.position.tf,
                direction=m.position.direction,
                fired_at_ms=m.position.fired_at_ms,
                entry_price=m.position.entry_price,
                sl_price=m.position.sl_price,
                tp_price=m.position.tp_price,
                mark=m.mark,
                unrealized_r=m.unrealized_r,
                dist_sl_pct=m.dist_sl_pct,
                dist_tp_pct=m.dist_tp_pct,
            )
            for m in marked
        ],
    )
```

The bare `except Exception` is deliberate — `python-binance` raises several
unrelated exception types plus `requests` transport errors, and the whole point
is that none of them can break the panel. Add a `# noqa: BLE001` if ruff flags it.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `poetry run pytest tests/test_web_live_outcomes.py -v`

Expected: PASS — all tests including the two pre-existing ones.

- [ ] **Step 7: Extend the TypeScript client**

In `web/ui/src/api.ts`, add after `LiveOutcomeStrategyRow`:

```typescript
export interface LiveOutcomeSymbolRow {
  symbol: string;
  n: number;
}

export interface LiveOpenPosition {
  signal_id: string;
  symbol: string;
  strategy: string;
  tf: string;
  direction: string;
  fired_at_ms: number;
  entry_price: number | null;
  sl_price: number | null;
  tp_price: number | null;
  mark: number | null;
  /** GROSS of costs, unlike the net outcome_r in the tables. */
  unrealized_r: number | null;
  dist_sl_pct: number | null;
  dist_tp_pct: number | null;
}

export interface LiveOpenPositionsResponse {
  symbol: string | null;
  marks_ok: boolean;
  marked_at_ms: number;
  positions: LiveOpenPosition[];
}
```

Add `symbols` to `LiveOutcomesResponse`:

```typescript
export interface LiveOutcomesResponse {
  days: number;
  min_n: number;
  rollup: LiveOutcomesRollup;
  cells: LiveOutcomeCell[];
  by_strategy: LiveOutcomeStrategyRow[];
  symbols: LiveOutcomeSymbolRow[];
}
```

Replace the fetcher and add the new one:

```typescript
export const getLiveOutcomes = (
  days: number = 30,
  minN: number = 1,
  symbol: string | null = null,
) =>
  apiFetch<LiveOutcomesResponse>(
    `/api/live-outcomes?days=${days}&min_n=${minN}` +
      (symbol ? `&symbol=${encodeURIComponent(symbol)}` : ""),
  );

export const getLiveOutcomesOpen = (symbol: string | null = null) =>
  apiFetch<LiveOpenPositionsResponse>(
    `/api/live-outcomes/open` +
      (symbol ? `?symbol=${encodeURIComponent(symbol)}` : ""),
  );
```

- [ ] **Step 8: Run the gate**

Run: `make lint-py && make typecheck && make test && make web-build`

Expected: all clean; the Vite build succeeds (the existing `Stats.svelte` call
site still compiles because the new `symbol` argument is optional).

- [ ] **Step 9: Commit**

```bash
git add web/api/models/live_outcomes.py web/api/routers/live_outcomes.py web/ui/src/api.ts tests/test_web_live_outcomes.py
git commit -m "feat(api): symbol-scoped live outcomes + marked open-positions route"
```

---

### Task 4: Extract the card into a component (no behaviour change)

**Files:**

- Create: `web/ui/src/components/LiveOutcomes.svelte`
- Modify: `web/ui/src/pages/Stats.svelte`

**Interfaces:**

- Consumes: `getLiveOutcomes` from Task 3 (signature is backward compatible).
- Produces: `<LiveOutcomes />` — a self-contained, prop-less component.

**Follow the PathCone precedent exactly.** `.card`, `.card-header`,
`.card-title`, `.header-actions`, `.pill-toggle`, `.help-btn`, `.help-panel`,
`.help-section`, `.grid` and `.val-green` / `.val-red` are all defined **inside
`Stats.svelte`** and are Svelte-scoped, so they do not reach a child component.
`PathCone.svelte` handles this by leaving the card shell, header and help panel
in `Stats.svelte` and owning only the body plus its own controls — which it
styles with its own `.chip-group` / `.chip` rules rather than reusing
`.pill-toggle`. Do the same here. Do not copy the chrome CSS into the component.

**One deliberate visual change:** the period and min-n toggles move out of the
card header and down into a control row inside the component, joining the symbol
chips added in Task 5. Everything else must look identical.

- [ ] **Step 1: Create the component**

Create `web/ui/src/components/LiveOutcomes.svelte`. Move these out of
`Stats.svelte`:

From the `<script>` block:

- the `liveOutcomes`, `loLoading`, `loError`, `loDays`, `loMinN` state declarations (`Stats.svelte:36-40`)
- `loadLiveOutcomes`, `setLoDays`, `setLoMinN` (`Stats.svelte:136-158`)
- `fmtR` and `loMaxAbsR` (`Stats.svelte:160-170`)
- a local copy of `formatPct` (`Stats.svelte:121`) — `Stats.svelte` still uses it elsewhere, so copy rather than move

From the markup: the `rbar` snippet (`Stats.svelte:752-765`) and the card
**body** — `Stats.svelte:793-873`, i.e. the `{#if loError}` block through its
matching `{/if}`. The `.grid` / `.card` / `.card-header` / help-panel wrapper
(`Stats.svelte:768-791`) and its closing tags stay in `Stats.svelte`.

From the styles: the `/* ── Live Alert Outcomes ── */` block
(`Stats.svelte:1633` to the end of the file), plus copies of just two rules the
body needs — `.val-green` and `.val-red`. Read their exact declarations from
`Stats.svelte` rather than guessing at the colour values.

Component skeleton:

```svelte
<script lang="ts">
  import { onMount } from "svelte";
  import { getLiveOutcomes, type LiveOutcomesResponse } from "../api";

  let liveOutcomes = $state<LiveOutcomesResponse | null>(null);
  let loLoading = $state(false);
  let loError = $state<string | null>(null);
  let loDays = $state(30); // 0 = all time
  let loMinN = $state(1);

  onMount(() => {
    void loadLiveOutcomes();
  });
</script>
```

- [ ] **Step 2: Add the control row**

The toggles that lived in the card header become a control row at the top of the
component's body, directly above the `lo-rollup` block. Style them with the
component's own classes, modelled on `PathCone.svelte`'s `.chip-group` / `.chip`
— read those rules and mirror them so the two cards' controls look alike:

```svelte
      <div class="lo-controls">
        <div class="lo-chip-group">
          <button class="lo-chip" class:active={loDays === 30} onclick={() => setLoDays(30)}>30D</button>
          <button class="lo-chip" class:active={loDays === 90} onclick={() => setLoDays(90)}>90D</button>
          <button class="lo-chip" class:active={loDays === 0} onclick={() => setLoDays(0)}>All</button>
        </div>
        <div class="lo-chip-group">
          <button class="lo-chip" class:active={loMinN === 1} onclick={() => setLoMinN(1)}>n≥1</button>
          <button class="lo-chip" class:active={loMinN === 10} onclick={() => setLoMinN(10)}>n≥10</button>
        </div>
      </div>
```

```css
  .lo-controls {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    margin: 10px 0 4px;
  }
  .lo-chip-group {
    display: flex;
    gap: 4px;
  }
```

For `.lo-chip` and `.lo-chip.active`, copy the declarations from
`PathCone.svelte`'s `.chip` and `.chip.active` so the two cards match.

- [ ] **Step 3: Mount it and delete the moved code**

In `Stats.svelte`, import the component alongside the existing `PathCone` import:

```typescript
import LiveOutcomes from "../components/LiveOutcomes.svelte";
```

Remove the two `pill-toggle` divs from the card header (the toggles now live in
the component), leaving the title and the help button. Then replace the card
body — `Stats.svelte:793-873` — with the mount, keeping the surrounding card
wrapper intact:

```svelte
      <LiveOutcomes />
```

Then delete from `Stats.svelte`: the moved state declarations, the three `lo*`
functions, `fmtR`, `loMaxAbsR`, the `rbar` snippet, the
`void loadLiveOutcomes();` line inside `onMount`, the `getLiveOutcomes` and
`type LiveOutcomesResponse` imports, and the
`/* ── Live Alert Outcomes ── */` style block.

Keep the `liveOutcomes` entry of `CARD_HELP` in `Stats.svelte` — the help panel
stays in the shell. Task 5 updates its wording.

- [ ] **Step 4: Verify nothing else used the moved helpers**

Run:

```bash
grep -n "fmtR\|loMaxAbsR\|rbar\|liveOutcomes\|loDays\|loMinN" web/ui/src/pages/Stats.svelte
```

Expected: only the `<LiveOutcomes />` mount line matches, or no output at all.
If `fmtR` still has call sites in `Stats.svelte`, keep a copy there — do not
delete a helper another card is using.

- [ ] **Step 5: Build**

Run: `make web-build`

Expected: build succeeds with no unused-variable or unresolved-reference errors.
Svelte will warn about unused CSS selectors if you left orphaned rules behind —
clean those up.

- [ ] **Step 6: Verify the page**

Start the backend and screenshot the Stats page:

```bash
make buibui-web &
sleep 5
google-chrome --headless=new --disable-gpu --no-sandbox \
  --window-size=1600,2600 --hide-scrollbars --virtual-time-budget=20000 \
  --screenshot=/tmp/stats-after-extract.png "http://localhost:8000/#/stats"
```

Expected: the Live Alert Outcomes card renders as before with one intended
difference — the period and min-n toggles now sit in a control row below the
roll-up tiles instead of in the card header. Everything else is unchanged:
roll-up tiles, the help `?` button and its panel, and both tables with their
diverging R bars. Stop the server when done: `pkill -f "buibui.py web --host"`.

If the DuckDB read fails with a lock error, the signal daemon is running and
holding the write lock — that is expected and not caused by your change. Retry,
or check with `pgrep -af "buibui.py signal watch"`.

- [ ] **Step 7: Commit**

```bash
git add web/ui/src/components/LiveOutcomes.svelte web/ui/src/pages/Stats.svelte
git commit -m "refactor(ui): extract Live Alert Outcomes body into its own component"
```

---

### Task 5: Chips, sorting, and the open-positions panel

**Files:**

- Modify: `web/ui/src/components/LiveOutcomes.svelte`
- Modify: `web/ui/src/pages/Stats.svelte` (Step 6 only — the `CARD_HELP.liveOutcomes` text, which stayed in the shell)

**Interfaces:**

- Consumes: `getLiveOutcomes(days, minN, symbol)`, `getLiveOutcomesOpen(symbol)`, and the `LiveOutcomeSymbolRow` / `LiveOpenPosition` / `LiveOpenPositionsResponse` types from Task 3; the component and its `.lo-controls` / `.lo-chip` classes from Task 4.
- Produces: the finished card. Nothing downstream consumes it.

Load the `/frontend-design` skill before writing CSS. All new styling reuses the
existing dark-minimal terminal vocabulary — no new visual language, no new
colours beyond the `val-green` / `val-red` / `muted` set already in the file.

- [ ] **Step 1: Add symbol chip state and filtering**

Extend the script block:

```typescript
import {
  getLiveOutcomes,
  getLiveOutcomesOpen,
  type LiveOutcomesResponse,
  type LiveOpenPositionsResponse,
} from "../api";

let loSymbol = $state<string | null>(null); // null = ALL

function setLoSymbol(s: string | null): void {
  if (loSymbol === s) return;
  loSymbol = s;
  void loadLiveOutcomes();
}

// The ALL chip's count must stay global even while a symbol is selected, so it
// sums the chip list (always global) rather than reading the filtered roll-up.
const loTotalAllSymbols = $derived(
  liveOutcomes ? liveOutcomes.symbols.reduce((acc, s) => acc + s.n, 0) : 0,
);
```

Step 5 extends `setLoSymbol` to also refresh the open panel. Leave it as written
here — referencing the panel's state before it exists will not compile.

Update `loadLiveOutcomes` to pass the symbol:

```typescript
liveOutcomes = await getLiveOutcomes(loDays, loMinN, loSymbol);
```

Add a third chip group to the `.lo-controls` row Task 4 created, after the
period and min-n groups:

```svelte
        <div class="lo-chip-group">
          <button class="lo-chip" class:active={loSymbol === null} onclick={() => setLoSymbol(null)}>
            ALL <span class="lo-chip-n">{loTotalAllSymbols.toLocaleString()}</span>
          </button>
          {#each liveOutcomes.symbols as s}
            <button class="lo-chip" class:active={loSymbol === s.symbol} onclick={() => setLoSymbol(s.symbol)}>
              {s.symbol} <span class="lo-chip-n">{s.n.toLocaleString()}</span>
            </button>
          {/each}
        </div>
```

The symbol chips reuse `.lo-chip` / `.lo-chip.active` from Task 4, so they match
the period and min-n toggles automatically. Only the count needs new styling.

Update the scope line so it names the active symbol:

```svelte
        <div class="lo-scope muted">
          {loSymbol ?? "all symbols"} · all-time roll-up · tables show
          {loDays === 0 ? "all time" : `last ${loDays}d`}, min n {loMinN}
        </div>
```

One new rule for the count:

```css
  .lo-chip-n {
    opacity: 0.55;
    margin-left: 4px;
  }
```

- [ ] **Step 2: Verify the chips work**

Run `make web-build`, start the backend, and load the Stats page. Expected: four
chips (ALL, BTCUSDT, ETHUSDT, SOLUSDT). Clicking one re-slices the roll-up tiles
and both tables; the scope line names it; ALL restores the global view.

- [ ] **Step 3: Add client-side sorting**

Add sort state and a comparator. Nulls sort last in **both** directions, so an
unresolved cell never appears as the best or worst row:

```typescript
type SortDir = "asc" | "desc";
let stratSort = $state<{ key: string; dir: SortDir } | null>(null);
let cellSort = $state<{ key: string; dir: SortDir } | null>(null);

function toggleSort(
  current: { key: string; dir: SortDir } | null,
  key: string,
): { key: string; dir: SortDir } {
  if (current && current.key === key) {
    return { key, dir: current.dir === "desc" ? "asc" : "desc" };
  }
  return { key, dir: "desc" };
}

function sortRows<T extends Record<string, unknown>>(
  rows: T[],
  sort: { key: string; dir: SortDir } | null,
): T[] {
  if (!sort) return rows;
  const sign = sort.dir === "desc" ? -1 : 1;
  return [...rows].sort((a, b) => {
    const av = a[sort.key];
    const bv = b[sort.key];
    // Nulls always last, whichever way the column is sorted.
    if (av === null && bv === null) return 0;
    if (av === null) return 1;
    if (bv === null) return -1;
    if (typeof av === "number" && typeof bv === "number") return sign * (av - bv);
    return sign * String(av).localeCompare(String(bv));
  });
}

const sortedStrategies = $derived(
  liveOutcomes ? sortRows(liveOutcomes.by_strategy, stratSort) : [],
);
const sortedCells = $derived(
  liveOutcomes ? sortRows(liveOutcomes.cells, cellSort) : [],
);
```

Replace `{#each liveOutcomes.by_strategy as s}` with `{#each sortedStrategies as s}`
and `{#each liveOutcomes.cells as c}` with `{#each sortedCells as c}`.

Make the header cells clickable. For the by-strategy table:

```svelte
                <div class="lo-row lo-head">
                  <button class="lo-th" onclick={() => (stratSort = toggleSort(stratSort, "strategy"))}>
                    strategy{stratSort?.key === "strategy" ? (stratSort.dir === "desc" ? " ▾" : " ▴") : ""}
                  </button>
                  <button class="lo-th num" onclick={() => (stratSort = toggleSort(stratSort, "n"))}>
                    n{stratSort?.key === "n" ? (stratSort.dir === "desc" ? " ▾" : " ▴") : ""}
                  </button>
                  <button class="lo-th num" onclick={() => (stratSort = toggleSort(stratSort, "win_rate"))}>
                    win{stratSort?.key === "win_rate" ? (stratSort.dir === "desc" ? " ▾" : " ▴") : ""}
                  </button>
                  <button class="lo-th num" onclick={() => (stratSort = toggleSort(stratSort, "avg_r"))}>
                    avg R{stratSort?.key === "avg_r" ? (stratSort.dir === "desc" ? " ▾" : " ▴") : ""}
                  </button>
                  <span></span>
                </div>
```

And the cell table's header, which drives `cellSort`:

```svelte
                <div class="lo-row lo-cell-row lo-head">
                  <button class="lo-th" onclick={() => (cellSort = toggleSort(cellSort, "strategy"))}>
                    strat{cellSort?.key === "strategy" ? (cellSort.dir === "desc" ? " ▾" : " ▴") : ""}
                  </button>
                  <button class="lo-th" onclick={() => (cellSort = toggleSort(cellSort, "tf"))}>
                    tf{cellSort?.key === "tf" ? (cellSort.dir === "desc" ? " ▾" : " ▴") : ""}
                  </button>
                  <button class="lo-th" onclick={() => (cellSort = toggleSort(cellSort, "direction"))}>
                    dir{cellSort?.key === "direction" ? (cellSort.dir === "desc" ? " ▾" : " ▴") : ""}
                  </button>
                  <button class="lo-th num" onclick={() => (cellSort = toggleSort(cellSort, "n"))}>
                    n{cellSort?.key === "n" ? (cellSort.dir === "desc" ? " ▾" : " ▴") : ""}
                  </button>
                  <button class="lo-th num" onclick={() => (cellSort = toggleSort(cellSort, "win_rate"))}>
                    win{cellSort?.key === "win_rate" ? (cellSort.dir === "desc" ? " ▾" : " ▴") : ""}
                  </button>
                  <button class="lo-th num" onclick={() => (cellSort = toggleSort(cellSort, "avg_r"))}>
                    avg R{cellSort?.key === "avg_r" ? (cellSort.dir === "desc" ? " ▾" : " ▴") : ""}
                  </button>
                  <span></span>
                </div>
```

Style the header buttons so they read as headers, not controls:

```css
  .lo-th {
    background: none;
    border: none;
    padding: 0;
    font: inherit;
    color: inherit;
    text-align: left;
    cursor: pointer;
  }
  .lo-th.num {
    text-align: right;
  }
  .lo-th:hover {
    color: var(--fg, #d5dde6);
  }
```

Resetting sort on refetch is **not** wanted: if the operator sorted by win rate
and then switches period, the sort should persist. `stratSort` and `cellSort`
live outside `loadLiveOutcomes`, so this happens naturally — do not clear them.

- [ ] **Step 4: Verify sorting**

Run `make web-build` and reload. Expected: clicking `avg R` sorts descending,
clicking again ascending, and the ▾/▴ indicator follows. Rows whose win rate is
`—` sink to the bottom in both directions. Switching the period keeps the sort.

- [ ] **Step 5: Add the open-positions panel**

Add state and a loader:

```typescript
let openExpanded = $state(false);
let openData = $state<LiveOpenPositionsResponse | null>(null);
let openError = $state<string | null>(null);
let openTimer: ReturnType<typeof setInterval> | null = null;
let nowMs = $state(Date.now());
let clockTimer: ReturnType<typeof setInterval> | null = null;

async function loadOpenPositions(): Promise<void> {
  try {
    openData = await getLiveOutcomesOpen(loSymbol);
    openError = null;
  } catch (e) {
    // Keep the last good data and mark it stale rather than blanking the panel
    // — a transient DuckDB lock (the signal daemon writing) must not flash an
    // error banner on every poll.
    openError = e instanceof Error ? e.message : String(e);
  }
}

// Extend the Step 1 version so a chip click also re-slices the open list.
function setLoSymbol(s: string | null): void {
  if (loSymbol === s) return;
  loSymbol = s;
  void loadLiveOutcomes();
  if (openExpanded) void loadOpenPositions();
}

function toggleOpenPanel(): void {
  openExpanded = !openExpanded;
  if (openExpanded) {
    void loadOpenPositions();
    openTimer = setInterval(() => void loadOpenPositions(), 30_000);
  } else if (openTimer !== null) {
    clearInterval(openTimer);
    openTimer = null;
  }
}
```

Extend `onMount` to run the age clock and to clean up both timers:

```typescript
  onMount(() => {
    void loadLiveOutcomes();
    clockTimer = setInterval(() => (nowMs = Date.now()), 60_000);
    return () => {
      if (clockTimer !== null) clearInterval(clockTimer);
      if (openTimer !== null) clearInterval(openTimer);
    };
  });
```

Add the age formatter:

```typescript
function fmtAge(firedAtMs: number): string {
  const mins = Math.max(0, Math.floor((nowMs - firedAtMs) / 60_000));
  if (mins < 60) return `${mins}m`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h${String(mins % 60).padStart(2, "0")}m`;
  return `${Math.floor(hours / 24)}d${String(hours % 24).padStart(2, "0")}h`;
}
```

Turn the open tile into a disclosure button. Replace the existing open `lo-stat`
div with:

```svelte
          <button class="lo-stat lo-stat-btn" onclick={toggleOpenPanel}>
            <span class="lo-stat-val">{rollup.open.toLocaleString()} {openExpanded ? "▾" : "▸"}</span>
            <span class="lo-stat-label">open</span>
          </button>
```

Add the panel after the `lo-scope` line:

```svelte
        {#if openExpanded}
          <div class="lo-open">
            {#if openError && !openData}
              <div class="lo-msg val-red">Failed to load open positions: {openError}</div>
            {:else if !openData}
              <div class="lo-msg muted">Loading open positions…</div>
            {:else if openData.positions.length === 0}
              <div class="lo-msg muted">No open alerts{loSymbol ? ` on ${loSymbol}` : ""}.</div>
            {:else}
              {#if !openData.marks_ok}
                <div class="lo-note muted">Live prices unavailable — showing ledger values only.</div>
              {:else if openError}
                <div class="lo-note muted">Prices may be stale — last refresh failed.</div>
              {/if}
              <div class="lo-table lo-open-table">
                <div class="lo-row lo-open-row lo-head">
                  <span>sym</span><span>strat</span><span>tf</span><span>dir</span>
                  <span class="num">age</span><span class="num">entry</span>
                  <span class="num">mark</span><span class="num">uR*</span>
                  <span class="num">→SL</span><span class="num">→TP</span>
                </div>
                {#each openData.positions as p}
                  <div class="lo-row lo-open-row">
                    <span>{p.symbol}</span>
                    <span class="lo-strat">{p.strategy}</span>
                    <span class="muted">{p.tf}</span>
                    <span class:val-green={p.direction === "long"} class:val-red={p.direction === "short"}>
                      {p.direction === "long" ? "▲ L" : "▼ S"}
                    </span>
                    <span class="num muted">{fmtAge(p.fired_at_ms)}</span>
                    <span class="num muted">{p.entry_price === null ? "—" : p.entry_price}</span>
                    <span class="num">{p.mark === null ? "—" : p.mark}</span>
                    <span class="num" class:val-green={(p.unrealized_r ?? 0) > 0} class:val-red={(p.unrealized_r ?? 0) < 0}>
                      {fmtR(p.unrealized_r)}
                    </span>
                    <span class="num muted">{p.dist_sl_pct === null ? "—" : formatPct(p.dist_sl_pct)}</span>
                    <span class="num muted">{p.dist_tp_pct === null ? "—" : formatPct(p.dist_tp_pct)}</span>
                  </div>
                {/each}
              </div>
              <div class="lo-note muted">
                *uR is gross of costs (fees, slippage, funding); the avg R in the tables below is net.
              </div>
            {/if}
          </div>
        {/if}
```

Style the panel, matching the existing `.lo-table` / `.lo-row` grid conventions
already in the file — read them first and mirror the column-template approach:

```css
  .lo-stat-btn {
    background: none;
    border: none;
    font: inherit;
    color: inherit;
    cursor: pointer;
    text-align: left;
    padding: 0;
  }
  .lo-open {
    margin: 10px 0 4px;
  }
  .lo-open-row {
    grid-template-columns: 78px 92px 34px 34px 52px 1fr 1fr 58px 52px 52px;
  }
  .lo-note {
    font-size: 11px;
    margin: 6px 0 0;
  }
```

- [ ] **Step 6: Update the card help text**

`CARD_HELP.liveOutcomes` lives in `web/ui/src/pages/Stats.svelte` (the help panel
stayed in the card shell). Its text currently promises a cross-symbol,
always-all-time roll-up; both claims are now conditional. Replace the `what`
string and extend `value` there:

```typescript
      what: "REAL outcomes of every Telegram alert the live daemon fired, scored from the signal_alert_outcomes ledger. The symbol chips scope the whole card — roll-up, open list, and both tables — to one coin; ALL is the cross-symbol view. Resolved = TP/SL touched or held to expiry; No-TP should read 0 (every fired alert now persists a stop/target). The tables window by the selected period; the roll-up is all-time. Win rate excludes expired trades; avg R averages outcome_r over all resolved rows. Click any column header to sort, and the open count to see what is live right now.",
      value: "This is ground truth — what actually happened live, not a backtest. Use it to confirm or kill an edge: if a (strategy, tf, direction) cell is positive live with enough N, it earns its place; if it bleeds, it's a hard-flip candidate. Note retro-backfilled rows use a pct-fallback stop (best-effort R), while forward rows are exact. Unrealized R in the open panel is GROSS of costs, unlike the net avg R in the tables — don't compare them directly.",
```

- [ ] **Step 7: Verify the panel end to end**

Run `make web-build`, start the backend, and load the Stats page. Expected:

- clicking the open tile expands a table of the currently open alerts with live
  mark, unrealized R, and distances
- ages tick over on the minute
- selecting a symbol chip while expanded re-slices the open list too
- collapsing the panel stops the network requests (watch the backend log — no
  `/api/live-outcomes/open` hits after collapse)

To verify the price-failure path without breaking Binance, temporarily stop the
backend, edit nothing, and instead confirm the API contract directly:

```bash
poetry run pytest tests/test_web_live_outcomes.py::test_open_endpoint_survives_price_failure -v
```

- [ ] **Step 8: Run the full gate**

Run: `make lint-py && make typecheck && make test && make web-build && make test-regression`

Expected: everything green, and the regression goldens **unmoved**. If a golden
moved, stop — nothing in this task touches a backtest path, so it is a bug.

- [ ] **Step 9: Commit**

```bash
git add web/ui/src/components/LiveOutcomes.svelte web/ui/src/pages/Stats.svelte
git commit -m "feat(ui): symbol chips, sortable tables, live-marked open panel"
```

---

## Final verification

- [ ] `make lint-py` ✓
- [ ] `make typecheck` ✓
- [ ] `make test` green
- [ ] `make test-regression` goldens unmoved
- [ ] `make web-build` ✓
- [ ] Visual check: chips slice everything, sorting works with nulls last, open panel marks live and degrades cleanly
- [ ] `/post-branch` docs sweep before reporting the PR
