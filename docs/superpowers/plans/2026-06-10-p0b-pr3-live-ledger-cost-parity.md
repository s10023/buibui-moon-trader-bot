# P0b PR-3 — Live-Ledger Cost Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the live outcome ledger (`signal_alert_outcomes.outcome_r`) net of costs using the exact `net_R = raw_R − fee_R − slippage_R − funding_R` formula the backtest engine applies in `Trade.pnl_r`, closing the last P0b gap (spec `docs/redesign/2026-06-09-p0b-honest-costs-design.md` §4).

**Architecture:** `analytics/signal/outcome_backfill.py::_scan_forward` gains keyword-only cost parameters (per-leg `fee_pct` / `slippage_pct` + pre-extracted funding numpy arrays) and deducts costs on every resolved outcome (win / loss / expired). `backfill_outcomes` reads the funding series from the same DuckDB conn per (symbol, tf) group (`get_funding_rates`; empty table → graceful 0.0, which covers the OKX GH-Actions path where funding is never ingested) and accepts `fee_pct` / `slippage_pct` kwargs. `analytics/signal_runner.py` threads both from the already-parsed `BacktestFilterConfig` (PR-2 added `slippage_pct`; `fee_pct` predates it). All defaults are 0.0 / None, so callers that pass nothing (existing tests, `tools/backfill_null_tp_outcomes.py`) keep today's raw behaviour byte-for-byte.

**Tech Stack:** Python 3.11+, numpy `searchsorted` window math (mirrors `analytics/backtest/engine.py:1084-1103`), in-memory DuckDB tests (pytest), mypy strict.

**Parity invariants (from the engine — do not deviate):**

- Drag: `2.0 × fee_pct × entry / risk` + `2.0 × slippage_pct × entry / risk`, `risk = abs(entry − sl_price)`.
- Funding window: stamps in `(entry_ts, exit_ts]` via `np.searchsorted(..., side="right")` on **both** ends.
- Funding sign: `side_sign = +1.0` long (pays positive rates) / `−1.0` short (receives); `funding_r = side_sign × Σrates × entry / risk`; `pnl` **subtracts** `funding_r`.
- Entry anchor: the live ledger's entry fills at the open of the first post-signal bar (`open_time > candle_ts_ms`) — same next-bar-open convention as the engine's `Trade.entry_time`.
- Zero-risk rows (`entry == sl_price`): return `raw_r` untouched (engine returns `pnl_r=None` there; the ledger keeps the row scoreable instead).

**Out of scope (note in PR body):** retro re-scoring of already-resolved rows (mixed raw/net history until a gated migration, mirroring `tools/backfill_null_tp_outcomes.py`); cost columns on the `signal_alert_outcomes` schema; `tools/backfill_null_tp_outcomes.py` wiring (defaults keep it raw — it has already run).

---

## Task 0: Branch setup

**Files:** none (git only)

- [ ] **Step 1: Sync main and create the feature branch**

```bash
cd /home/kng/repo/buibui-moon-trader-bot
git checkout main && git pull origin main
git checkout -b feat/p0b-pr3-live-ledger-cost-parity
```

Expected: branch created from `origin/main` at or after `5b9dc3c`.

- [ ] **Step 2: Confirm clean baseline**

Run: `poetry run pytest tests/test_outcome_backfill.py -q`
Expected: all pass (13 tests as of `5b9dc3c`).

---

## Task 1: Fee + slippage drag in resolved outcome_r

**Files:**

- Modify: `analytics/signal/outcome_backfill.py`
- Test: `tests/test_outcome_backfill.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_outcome_backfill.py` (uses existing `_insert_signal` / `_insert_ohlcv` / `_fetch_one` helpers and `_HOUR`):

```python
class TestCostParity:
    """P0b PR-3 — outcome_r mirrors the engine's net_R = raw − fee − slippage − funding."""

    def test_win_deducts_fee_and_slippage_drag(self) -> None:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        _insert_signal(conn, candle_ts_ms=0, entry=100.0, sl=95.0, tp=110.0, rr=2.0)
        _insert_ohlcv(
            conn,
            "BTCUSDT",
            "1h",
            [
                {"open_time": _HOUR, "high": 102.0, "low": 99.0, "close": 101.0},
                {"open_time": 2 * _HOUR, "high": 111.0, "low": 100.0, "close": 110.5},
            ],
        )

        counts = backfill_outcomes(
            conn, now_ms=3 * _HOUR, fee_pct=0.0005, slippage_pct=0.0002
        )

        assert counts["win"] == 1
        _, outcome_r, _ = _fetch_one(conn, "sig1")
        # risk = 5 → drag = 2 × (0.0005 + 0.0002) × 100 / 5 = 0.028
        assert outcome_r == pytest.approx(2.0 - 0.028)

    def test_loss_deducts_drag_below_minus_one(self) -> None:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        _insert_signal(conn, candle_ts_ms=0, entry=100.0, sl=95.0, tp=110.0)
        _insert_ohlcv(
            conn,
            "BTCUSDT",
            "1h",
            [{"open_time": _HOUR, "high": 101.0, "low": 94.0, "close": 96.0}],
        )

        counts = backfill_outcomes(
            conn, now_ms=2 * _HOUR, fee_pct=0.0005, slippage_pct=0.0002
        )

        assert counts["loss"] == 1
        _, outcome_r, _ = _fetch_one(conn, "sig1")
        assert outcome_r == pytest.approx(-1.0 - 0.028)

    def test_expired_mtm_deducts_drag(self) -> None:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        _insert_signal(conn, candle_ts_ms=0, entry=100.0, sl=95.0, tp=110.0)
        # Two chop bars, neither touches SL/TP → expired at max_hold=2.
        _insert_ohlcv(
            conn,
            "BTCUSDT",
            "1h",
            [
                {"open_time": _HOUR, "high": 101.0, "low": 99.0, "close": 100.5},
                {"open_time": 2 * _HOUR, "high": 103.0, "low": 100.0, "close": 102.0},
            ],
        )

        counts = backfill_outcomes(
            conn,
            now_ms=4 * _HOUR,
            max_hold_bars_by_tf={"1h": 2},
            fee_pct=0.0005,
            slippage_pct=0.0002,
        )

        assert counts["expired"] == 1
        _, outcome_r, _ = _fetch_one(conn, "sig1")
        # mtm = (102 − 100) / 5 = 0.4, minus drag 0.028
        assert outcome_r == pytest.approx(0.4 - 0.028)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_outcome_backfill.py::TestCostParity -v`
Expected: 3 FAIL — `backfill_outcomes() got an unexpected keyword argument 'fee_pct'`.

- [ ] **Step 3: Implement drag deduction**

In `analytics/signal/outcome_backfill.py`:

(a) Add `from typing import Any` to the imports (needed for the numpy generics in Task 2's helper signature — add it now so the helper below typechecks).

(b) Insert a module-level helper between `DEFAULT_MAX_HOLD_BARS` and `_scan_forward` (funding params are wired in Task 2 but defined now so the signature doesn't churn):

```python
def _net_outcome_r(
    raw_r: float,
    *,
    direction: str,
    entry: float,
    sl_price: float,
    entry_ts: int,
    exit_ts: int,
    fee_pct: float,
    slippage_pct: float,
    funding_times: "np.ndarray[Any, np.dtype[np.int64]] | None",
    funding_rates: "np.ndarray[Any, np.dtype[np.float64]] | None",
) -> float:
    """net_R = raw_R − fee_R − slippage_R − funding_R, mirroring Trade.pnl_r.

    Fee/slippage: 2 legs × pct × entry / risk (engine.py::Trade.pnl_r).
    Funding: stamps in (entry_ts, exit_ts] via searchsorted side="right" on
    both ends; long pays positive rates (+side_sign), short receives (−)
    — engine.py run_backtest's at-close funding block. Zero-risk rows return
    raw_r untouched: costs in R are undefined when nothing is risked (the
    engine returns pnl_r=None there; the ledger keeps the row scoreable).
    """
    risk = abs(entry - sl_price)
    if risk <= 0.0:
        return raw_r
    drag_r = 2.0 * (fee_pct + slippage_pct) * entry / risk
    funding_r = 0.0
    if funding_times is not None and funding_rates is not None:
        lo_i = int(np.searchsorted(funding_times, entry_ts, side="right"))
        hi_i = int(np.searchsorted(funding_times, exit_ts, side="right"))
        if hi_i > lo_i:
            funding_sum = float(funding_rates[lo_i:hi_i].sum())
            side_sign = 1.0 if direction == "long" else -1.0
            funding_r = side_sign * funding_sum * entry / risk
    return raw_r - drag_r - funding_r
```

(c) Extend `_scan_forward` with keyword-only cost params and route every resolved return through the helper. Full replacement body:

```python
def _scan_forward(
    bars: pd.DataFrame,
    candle_ts_ms: int,
    direction: str,
    entry: float,
    sl_price: float,
    tp_price: float,
    rr_ratio: float,
    max_hold_bars: int,
    *,
    fee_pct: float = 0.0,
    slippage_pct: float = 0.0,
    funding_times: "np.ndarray[Any, np.dtype[np.int64]] | None" = None,
    funding_rates: "np.ndarray[Any, np.dtype[np.float64]] | None" = None,
) -> tuple[str | None, float | None, int | None]:
    """Decide outcome for one signal given pre-fetched OHLCV bars for its TF.

    Resolved outcome_r is net of costs (P0b PR-3): the same
    net_R = raw_R − fee_R − slippage_R − funding_R the engine applies in
    Trade.pnl_r. Defaults (zero costs, no funding) reproduce the historical
    raw behaviour byte-for-byte.
    """
    post = bars[bars["open_time"] > candle_ts_ms].reset_index(drop=True)
    if post.empty:
        return None, None, None

    window = post.iloc[:max_hold_bars]
    h = window["high"].to_numpy()
    lo = window["low"].to_numpy()
    t = window["open_time"].to_numpy()

    if direction == "long":
        sl_idxs = np.nonzero(lo <= sl_price)[0]
        tp_idxs = np.nonzero(h >= tp_price)[0]
        sign = 1.0
    else:
        sl_idxs = np.nonzero(h >= sl_price)[0]
        tp_idxs = np.nonzero(lo <= tp_price)[0]
        sign = -1.0

    sl_first = int(sl_idxs[0]) if len(sl_idxs) else len(t)
    tp_first = int(tp_idxs[0]) if len(tp_idxs) else len(t)

    # Entry fills at the open of the first post-signal bar — the same
    # next-bar-open convention as the engine's Trade.entry_time. Anchors
    # the funding window (entry_ts, exit_ts].
    entry_ts = int(t[0])

    def _net(raw_r: float, exit_ts: int) -> float:
        return _net_outcome_r(
            raw_r,
            direction=direction,
            entry=entry,
            sl_price=sl_price,
            entry_ts=entry_ts,
            exit_ts=exit_ts,
            fee_pct=fee_pct,
            slippage_pct=slippage_pct,
            funding_times=funding_times,
            funding_rates=funding_rates,
        )

    if sl_first <= tp_first and sl_first < len(t):
        exit_ts = int(t[sl_first])
        return "loss", _net(-1.0, exit_ts), exit_ts
    if tp_first < len(t):
        exit_ts = int(t[tp_first])
        return "win", _net(float(rr_ratio), exit_ts), exit_ts

    # Neither hit within the window so far.
    if len(window) < max_hold_bars:
        return None, None, None

    sl_dist = abs(entry - sl_price)
    last_close = float(window["close"].iloc[-1])
    mtm_r = (last_close - entry) / sl_dist * sign if sl_dist > 0 else 0.0
    exit_ts_exp = int(t[-1])
    return "expired", _net(float(mtm_r), exit_ts_exp), exit_ts_exp
```

(d) Extend `backfill_outcomes` with keyword-only params (funding stays unwired until Task 2):

```python
def backfill_outcomes(
    conn: duckdb.DuckDBPyConnection,
    now_ms: int,
    max_hold_bars_by_tf: dict[str, int] | None = None,
    *,
    fee_pct: float = 0.0,
    slippage_pct: float = 0.0,
) -> dict[str, int]:
```

and pass them through in the `_scan_forward` call:

```python
            outcome, outcome_r, filled_at = _scan_forward(
                bars,
                int(candle_ts_ms),
                str(direction),
                float(entry_price),
                float(sl_price),
                float(tp_price),
                float(rr_ratio),
                max_hold,
                fee_pct=fee_pct,
                slippage_pct=slippage_pct,
            )
```

Update the `backfill_outcomes` docstring: add a line "Resolved `outcome_r` is net of costs — `fee_pct` / `slippage_pct` are per-leg fractions (same semantics as `BacktestFilterConfig`); defaults 0.0 keep raw behaviour."

- [ ] **Step 4: Run tests to verify they pass (and nothing else moved)**

Run: `poetry run pytest tests/test_outcome_backfill.py -v`
Expected: all pass — 3 new + 13 existing (existing pass unchanged because defaults are 0.0).

- [ ] **Step 5: Commit**

```bash
git add analytics/signal/outcome_backfill.py tests/test_outcome_backfill.py
git commit -m "feat(outcome-backfill): deduct fee + slippage drag in resolved outcome_r (P0b PR-3)"
```

---

## Task 2: Funding cost over (entry, exit] from the funding_rates table

**Files:**

- Modify: `analytics/signal/outcome_backfill.py`
- Test: `tests/test_outcome_backfill.py`

- [ ] **Step 1: Write the failing tests**

Add a funding insert helper after `_fetch_one` in `tests/test_outcome_backfill.py`:

```python
def _insert_funding(
    conn: duckdb.DuckDBPyConnection,
    rows: list[tuple[int, float]],
    symbol: str = "BTCUSDT",
) -> None:
    df = pd.DataFrame(
        [{"symbol": symbol, "funding_time": t, "funding_rate": r} for t, r in rows]
    )
    upsert_funding_rates(conn, df)
```

and extend the store import line to:

```python
from analytics.store import init_schema, upsert_funding_rates, upsert_signal_outcome
```

(If `upsert_funding_rates` is not re-exported from `analytics.store`, import it as `from analytics.store.market_data import upsert_funding_rates` — verify with `poetry run python -c "from analytics.store import upsert_funding_rates"`.)

Append to `TestCostParity`:

```python
    def test_long_pays_positive_funding(self) -> None:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        _insert_signal(conn, candle_ts_ms=0, entry=100.0, sl=95.0, tp=110.0, rr=2.0)
        _insert_ohlcv(
            conn,
            "BTCUSDT",
            "1h",
            [
                {"open_time": _HOUR, "high": 102.0, "low": 99.0, "close": 101.0},
                {"open_time": 2 * _HOUR, "high": 111.0, "low": 100.0, "close": 110.5},
            ],
        )
        # Entry fills at bar-1 open (_HOUR). Window is (entry, exit]: the stamp
        # AT entry is excluded; the mid-hold stamp and the stamp AT exit count.
        _insert_funding(
            conn,
            [
                (_HOUR, 0.0001),
                (_HOUR + _HOUR // 2, 0.0001),
                (2 * _HOUR, 0.0001),
            ],
        )

        counts = backfill_outcomes(conn, now_ms=3 * _HOUR)

        assert counts["win"] == 1
        _, outcome_r, _ = _fetch_one(conn, "sig1")
        # funding_sum = 0.0002 → funding_r = +0.0002 × 100 / 5 = 0.004 (long pays)
        assert outcome_r == pytest.approx(2.0 - 0.004)

    def test_short_receives_positive_funding(self) -> None:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        _insert_signal(
            conn,
            candle_ts_ms=0,
            direction="short",
            entry=100.0,
            sl=105.0,
            tp=90.0,
            rr=2.0,
        )
        _insert_ohlcv(
            conn,
            "BTCUSDT",
            "1h",
            [
                {"open_time": _HOUR, "high": 102.0, "low": 99.0, "close": 100.0},
                {"open_time": 2 * _HOUR, "high": 101.0, "low": 89.5, "close": 90.5},
            ],
        )
        _insert_funding(
            conn,
            [(_HOUR + _HOUR // 2, 0.0001), (2 * _HOUR, 0.0001)],
        )

        counts = backfill_outcomes(conn, now_ms=3 * _HOUR)

        assert counts["win"] == 1
        _, outcome_r, _ = _fetch_one(conn, "sig1")
        # side_sign = −1 → funding_r = −0.004; subtracting it ADDS R (short receives)
        assert outcome_r == pytest.approx(2.0 + 0.004)

    def test_all_costs_combined(self) -> None:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        _insert_signal(conn, candle_ts_ms=0, entry=100.0, sl=95.0, tp=110.0, rr=2.0)
        _insert_ohlcv(
            conn,
            "BTCUSDT",
            "1h",
            [
                {"open_time": _HOUR, "high": 102.0, "low": 99.0, "close": 101.0},
                {"open_time": 2 * _HOUR, "high": 111.0, "low": 100.0, "close": 110.5},
            ],
        )
        _insert_funding(conn, [(2 * _HOUR, 0.0002)])

        counts = backfill_outcomes(
            conn, now_ms=3 * _HOUR, fee_pct=0.0005, slippage_pct=0.0002
        )

        assert counts["win"] == 1
        _, outcome_r, _ = _fetch_one(conn, "sig1")
        # drag 0.028 + funding 0.0002 × 100 / 5 = 0.004 → 2.0 − 0.032
        assert outcome_r == pytest.approx(2.0 - 0.032)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_outcome_backfill.py::TestCostParity -v`
Expected: the 3 new funding tests FAIL (outcome_r equals the raw/drag-only value — funding not yet read); the 3 Task-1 tests still PASS.

- [ ] **Step 3: Wire funding reads in backfill_outcomes**

In `analytics/signal/outcome_backfill.py`:

(a) Extend the data_store import:

```python
from analytics.data_store import get_funding_rates, get_ohlcv
```

(b) In the per-`(symbol, tf)` loop, after the `bars.empty` guard, fetch the funding stamps once per group (mirrors `backtest_runner._build_funding_series_by_symbol`'s dtype handling):

```python
        # Funding stamps for the cost window (P0b PR-3). One fetch per group
        # spanning earliest signal → now; per-row windows are narrowed in
        # _net_outcome_r via searchsorted. Empty table (e.g. the OKX
        # GH-Actions path never ingests funding) → None → funding_r = 0.0.
        fdf = get_funding_rates(conn, symbol, int(earliest_candle), now_ms)
        if fdf.empty:
            funding_times = None
            funding_rates = None
        else:
            funding_times = fdf["funding_time"].astype("int64").to_numpy()
            funding_rates = fdf["funding_rate"].astype(float).to_numpy()
```

(c) Pass both into the `_scan_forward` call alongside the Task-1 kwargs:

```python
                fee_pct=fee_pct,
                slippage_pct=slippage_pct,
                funding_times=funding_times,
                funding_rates=funding_rates,
```

(d) Update the module docstring's "Outcomes" block to state the net semantics:

```text
Outcomes:
  - "win"     — TP hit first. outcome_r = +rr_ratio − costs
  - "loss"    — SL hit first or same-bar tie. outcome_r = -1.0 − costs
  - "expired" — exceeded `max_hold_bars` without hitting either.
                outcome_r = mark-to-market at the last in-window bar − costs.
  - (NULL)    — still within hold window; retry on the next cycle.

Costs (P0b PR-3, live-ledger parity with Trade.pnl_r):
  net_R = raw_R − fee_R − slippage_R − funding_R. Fee/slippage are per-leg
  fractions passed by the caller (defaults 0.0); funding is read from the
  `funding_rates` table on the same conn — stamps in (entry, exit], long
  pays / short receives. Missing funding data degrades gracefully to 0.0.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_outcome_backfill.py -v`
Expected: all pass — 6 cost-parity + 13 existing (existing unchanged: empty funding table → None → 0.0).

- [ ] **Step 5: Commit**

```bash
git add analytics/signal/outcome_backfill.py tests/test_outcome_backfill.py
git commit -m "feat(outcome-backfill): funding cost over (entry, exit] in resolved outcome_r (P0b PR-3)"
```

---

## Task 3: Thread live config into the daemon call site + docs

**Files:**

- Modify: `analytics/signal_runner.py:384`
- Modify: `CLAUDE.md` (signal/ package bullet)

No new test: the wiring is a pure pass-through of two floats already parsed by `signal_config.py` (PR-2); it is covered by mypy strict + the existing `tests/test_signal_runner.py` suite, and the kwarg semantics are covered lib-level in Tasks 1–2.

- [ ] **Step 1: Update the call site**

In `analytics/signal_runner.py`, replace:

```python
                try:
                    backfill_outcomes(conn, now_ms=now_ms)
                except Exception:
                    logger.exception("Outcome backfill failed this cycle")
```

with:

```python
                try:
                    backfill_outcomes(
                        conn,
                        now_ms=now_ms,
                        fee_pct=backtest_cfg.fee_pct if backtest_cfg else 0.0,
                        slippage_pct=backtest_cfg.slippage_pct
                        if backtest_cfg
                        else 0.0,
                    )
                except Exception:
                    logger.exception("Outcome backfill failed this cycle")
```

- [ ] **Step 2: Update CLAUDE.md**

In the `analytics/` → `signal/` bullet, extend the `outcome_backfill.py` description:

`outcome_backfill.py` (`backfill_outcomes` — forward-walks OHLCV to resolve outstanding `signal_alert_outcomes` rows; called once per cycle from `signal_runner`. **P0b PR-3:** resolved `outcome_r` is net of costs — same `net_R = raw − fee − slippage − funding` as `Trade.pnl_r`; fee/slippage threaded from `BacktestFilterConfig`, funding read from `funding_rates` over `(entry, exit]` with graceful 0.0 when absent, e.g. the OKX GH-Actions path)

- [ ] **Step 3: Run the runner tests**

Run: `poetry run pytest tests/test_signal_runner.py tests/test_outcome_backfill.py -q`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add analytics/signal_runner.py CLAUDE.md
git commit -m "feat(signal-runner): thread fee/slippage into outcome backfill (P0b PR-3)"
```

---

## Task 4: Definition-of-Done gate

**Files:** none (verification; fix anything that surfaces)

- [ ] **Step 1: Lint**

Run: `make lint-py`
Expected: ruff format + lint clean.

- [ ] **Step 2: Typecheck**

Run: `make typecheck`
Expected: mypy strict, 0 errors.

- [ ] **Step 3: Full test suite**

Run: `make test`
Expected: green (1693 + 6 new = 1699).

- [ ] **Step 4: Regression goldens UNMOVED**

Run: `make test-regression`
Expected: 3/3 pass against unchanged goldens — this PR does not touch the backtest pipeline; any golden movement is a bug in the change, not a reason to regenerate.

- [ ] **Step 5: Markdown lint (CLAUDE.md + this plan changed)**

Run: `make lint-md`
Expected: clean.

- [ ] **Step 6: Commit any formatter fallout**

```bash
git status --short   # if dirty from ruff format only:
git add -A && git commit -m "chore: ruff format fallout"
```

---

## Task 5: PR

- [ ] **Step 1: Invoke `/pr-summary`** (writes `/tmp/pr-feat-p0b-pr3-live-ledger-cost-parity.md`). PR body must note: (1) forward-only — already-resolved rows keep raw outcome_r (retro re-score is a gated follow-up tool, same pattern as `tools/backfill_null_tp_outcomes.py`); (2) OKX GH-Actions path gets fee+slippage but funding 0.0 (no funding ingest there); (3) goldens untouched by design.

- [ ] **Step 2: Push + create PR**

```bash
git push -u origin feat/p0b-pr3-live-ledger-cost-parity
gh pr create --title "feat(outcome-backfill): P0b PR-3 — live-ledger cost parity" --body-file /tmp/pr-feat-p0b-pr3-live-ledger-cost-parity.md
```

(If `gh` errors on repo resolution: `gh auth switch --user s10023`.)

- [ ] **Step 3: Invoke `/post-branch`** (docs sweep + readiness check) before reporting the PR URL.
