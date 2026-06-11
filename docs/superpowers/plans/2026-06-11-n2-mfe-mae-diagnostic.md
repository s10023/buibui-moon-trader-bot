# N2 — MFE/MAE Diagnostic (exit spec §2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Per-alert MFE/MAE excursion study over the live `signal_alert_outcomes` ledger, aggregated per cohort (win/loss/expired) × cell (strategy, tf, direction), plus a written exit-fixable-vs-entry-broken verdict — the go/no-go gate for all exit-policy work (spec §2, shipped diagnostic-only per §10.6).

**Architecture:** New `analytics/exits/` package with one pure-lib module (`mfe_mae.py`: per-row excursion core + DB-walking collector + cohort aggregator), a read-only CLI tool (`tools/exit_audit.py`, diagnose mode only), TDD tests against in-memory DuckDB, and an audit doc with the verdict. No engine/daemon/config changes — regression goldens must NOT move.

**Tech Stack:** Python 3.13, DuckDB, pandas/numpy, pytest; mypy strict + ruff.

---

## Context (verified 2026-06-11 against the live DB and source)

- **Ledger** (`signal_alert_outcomes`, 2,508 rows 2026-03-25 → 2026-06-11): 1,350 loss / 982 expired / 157 win / 19 open. Expired = 39.5% of resolved — the leak is real. Zero NULL `entry_price`/`sl_price`/`tp_price`/`rr_ratio` among resolved rows. 20 cells with resolved n ≥ 30 (15m shorts dominate). Columns: `signal_id` PK, `symbol`, `tf`, `strategy`, `direction`, `fired_at_ms`, `candle_ts_ms`, `entry_price`, `sl_price`, `tp_price`, `rr_ratio`, `confidence_at_fire`, `tags`, `outcome`, `outcome_r`, `outcome_filled_at_ms`.
- **`ohlcv`** columns: `symbol`, `timeframe`, `open_time`, `open`, `high`, `low`, `close`, `volume`, `taker_buy_volume`. Fetch via `analytics.data_store.get_ohlcv(conn, symbol, timeframe, start, end)` — both bounds inclusive Unix ms.
- **Conventions inherited from `analytics/signal/outcome_backfill.py::_scan_forward`:** risk = `|entry − sl_price|`; window = bars with `open_time > candle_ts_ms`; same-bar ambiguity resolves adverse-first; `outcome_filled_at_ms` = `open_time` of the exit bar (SL bar, TP bar, or last in-window bar for expired).

### Design decisions (locked)

1. **Held-window from ground truth, not re-derivation.** Every resolved row stores its exit bar in `outcome_filled_at_ms`. The excursion window is `candle_ts_ms < open_time ≤ outcome_filled_at_ms`. No `max_hold_bars` re-resolution, no risk of drifting from what the ledger actually did. (Deviation from the spec's literal "walk the full max_hold window": post-exit excursion is irrelevant to exit design for win/loss rows — that's the cap-removal #7 question, deferred; for expired rows the held window IS the full max_hold window, so patterns 1–2 are unaffected.)
2. **Conservative intrabar clamps (spec §4 adverse-first, applied to measurement):**
   - *loss* exit bar: its favorable extreme does NOT count toward MFE (can't know the favorable wick printed before the stop touch). MAE includes all bars (≥ 1.0 by construction, > 1.0 on a gap through the stop).
   - *win* exit bar: MFE = `max(prior-bar MFE, rr_ratio)` — post-TP overshoot not credited; the exit bar's adverse extreme DOES count toward MAE (assume it printed before TP).
   - *expired*: both extremes of every in-window bar count.
   - Both MFE and MAE floor at 0.0 (you are at 0R at entry).
3. **Excursions are GROSS of costs** — price-path geometry for exit design; net realized PnL already lives in `outcome_r` (P0b PR-3).
4. **Aggregation columns map 1:1 onto the spec's 4-pattern verdict grid:** `n`, `mfe_mean/p50`, `mae_mean/p50`, `reach_05`/`reach_10` (share of cohort with MFE ≥ 0.5R / ≥ 1.0R), `tp_r_p50` (the target they were asked to reach), `bars_held_p50`, `outcome_r_mean`. The tool reports facts; the verdict is authored in the audit doc.
5. **Tool is named `tools/exit_audit.py`** (spec §7) with diagnose as the only mode for now — the policy sweep lands in a later PR, no rename churn.

## File Structure

- Create: `analytics/exits/__init__.py` — package docstring + re-exports.
- Create: `analytics/exits/mfe_mae.py` — `_excursion_for_row` (pure core), `compute_excursions` (DB walker), `aggregate_cohorts` (pure aggregation), `EXCURSION_COLUMNS`.
- Create: `tools/exit_audit.py` — read-only CLI: coverage line + overall cohort roll-up + per-cell table + optional `--csv` per-alert dump.
- Create: `tests/test_mfe_mae.py` — TDD suite (in-memory DuckDB, mirrors `tests/test_outcome_backfill.py` helpers).
- Create: `docs/audits/2026-06-11-mfe-mae-diagnostic.md` — tables + written verdict (Task 6).
- Modify: `CLAUDE.md` (analytics package list + tools list), `.claude/context/analytics.md` (module reference).

---

### Task 0: Branch

- [ ] **Step 0.1:** `git checkout -b feat/n2-mfe-mae-diagnostic` (from up-to-date `main`).

### Task 1: Excursion core — `_excursion_for_row` (TDD)

**Files:**

- Create: `tests/test_mfe_mae.py`
- Create: `analytics/exits/__init__.py`
- Create: `analytics/exits/mfe_mae.py`

- [ ] **Step 1.1: Write the failing tests** — create `tests/test_mfe_mae.py`:

```python
"""Tests for analytics.exits.mfe_mae (exit spec §2 MFE/MAE diagnostic).

Covers the conservative intrabar conventions per cohort (loss excludes the
exit bar's favorable extreme; win clamps post-TP overshoot; expired counts
every in-window bar), short-direction sign handling, the zero floor,
zero-risk / missing-OHLCV row skips, (symbol, tf) batching, and the cohort
aggregation (reach fractions + min_n gate).
"""

import duckdb
import pandas as pd
import pytest

from analytics.exits import aggregate_cohorts, compute_excursions
from analytics.store import init_schema, upsert_signal_outcome

_HOUR = 3_600_000


def _insert_ohlcv(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    tf: str,
    rows: list[dict[str, int | float]],
) -> None:
    df = pd.DataFrame(
        [
            {
                "symbol": symbol,
                "timeframe": tf,
                "open_time": r["open_time"],
                "open": r.get("open", r["close"]),
                "high": r["high"],
                "low": r["low"],
                "close": r["close"],
                "volume": 1.0,
                "taker_buy_volume": 0.5,
            }
            for r in rows
        ]
    )
    conn.register("_o", df)
    conn.execute("INSERT INTO ohlcv SELECT * FROM _o")
    conn.unregister("_o")


def _insert_resolved(
    conn: duckdb.DuckDBPyConnection,
    *,
    signal_id: str,
    outcome: str,
    filled_at_ms: int,
    symbol: str = "BTCUSDT",
    tf: str = "1h",
    strategy: str = "fvg",
    direction: str = "long",
    candle_ts_ms: int = 0,
    entry: float = 100.0,
    sl: float = 95.0,
    tp: float = 110.0,
    rr: float = 2.0,
    outcome_r: float = 0.0,
) -> None:
    upsert_signal_outcome(
        conn,
        {
            "signal_id": signal_id,
            "symbol": symbol,
            "tf": tf,
            "strategy": strategy,
            "direction": direction,
            "fired_at_ms": candle_ts_ms,
            "candle_ts_ms": candle_ts_ms,
            "entry_price": entry,
            "sl_price": sl,
            "tp_price": tp,
            "rr_ratio": rr,
            "confidence_at_fire": 3,
            "tags": "",
        },
    )
    conn.execute(
        "UPDATE signal_alert_outcomes "
        "SET outcome = ?, outcome_r = ?, outcome_filled_at_ms = ? "
        "WHERE signal_id = ?",
        [outcome, outcome_r, filled_at_ms, signal_id],
    )


class TestExcursionConventions:
    def test_long_loss_excludes_exit_bar_favorable_extreme(self) -> None:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        # risk = 5. Bar 1: fav 0.8R. Bar 2 (SL exit): high would be 2.4R but
        # must NOT count; low 94 gaps past the stop -> MAE 1.2R.
        _insert_ohlcv(
            conn,
            "BTCUSDT",
            "1h",
            [
                {"open_time": _HOUR, "high": 104.0, "low": 98.0, "close": 103.0},
                {"open_time": 2 * _HOUR, "high": 112.0, "low": 94.0, "close": 96.0},
            ],
        )
        _insert_resolved(
            conn, signal_id="s1", outcome="loss", filled_at_ms=2 * _HOUR
        )
        exc = compute_excursions(conn)
        assert len(exc) == 1
        row = exc.iloc[0]
        assert row["mfe_r"] == pytest.approx(0.8)
        assert row["mae_r"] == pytest.approx(1.2)
        assert row["bars_held"] == 2

    def test_long_win_clamps_exit_bar_overshoot(self) -> None:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        # Bar 1: fav 0.6R, adv 0.2R. Bar 2 (TP exit): high 118 = 3.6R
        # overshoot -> clamped to rr 2.0; its adv 0.8R counts.
        _insert_ohlcv(
            conn,
            "BTCUSDT",
            "1h",
            [
                {"open_time": _HOUR, "high": 103.0, "low": 99.0, "close": 102.0},
                {"open_time": 2 * _HOUR, "high": 118.0, "low": 96.0, "close": 115.0},
            ],
        )
        _insert_resolved(
            conn, signal_id="s1", outcome="win", filled_at_ms=2 * _HOUR
        )
        exc = compute_excursions(conn)
        row = exc.iloc[0]
        assert row["mfe_r"] == pytest.approx(2.0)
        assert row["mae_r"] == pytest.approx(0.8)

    def test_short_direction_signs(self) -> None:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        # Short, entry 100, sl 105 (risk 5). Bar 1: low 96 -> fav 0.8R,
        # high 103 -> adv 0.6R. Bar 2: low 94 -> fav 1.2R, high 101 -> 0.2R.
        _insert_ohlcv(
            conn,
            "BTCUSDT",
            "1h",
            [
                {"open_time": _HOUR, "high": 103.0, "low": 96.0, "close": 98.0},
                {"open_time": 2 * _HOUR, "high": 101.0, "low": 94.0, "close": 95.0},
            ],
        )
        _insert_resolved(
            conn,
            signal_id="s1",
            outcome="expired",
            filled_at_ms=2 * _HOUR,
            direction="short",
            sl=105.0,
            tp=90.0,
        )
        exc = compute_excursions(conn)
        row = exc.iloc[0]
        assert row["mfe_r"] == pytest.approx(1.2)
        assert row["mae_r"] == pytest.approx(0.6)

    def test_expired_counts_all_bars_both_extremes(self) -> None:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        # Peak fav on bar 2 (high 107 -> 1.4R), worst adv on bar 3
        # (low 96 -> 0.8R) — the LAST bar still counts for expired.
        _insert_ohlcv(
            conn,
            "BTCUSDT",
            "1h",
            [
                {"open_time": _HOUR, "high": 102.0, "low": 99.0, "close": 101.0},
                {"open_time": 2 * _HOUR, "high": 107.0, "low": 100.0, "close": 105.0},
                {"open_time": 3 * _HOUR, "high": 104.0, "low": 96.0, "close": 97.0},
            ],
        )
        _insert_resolved(
            conn, signal_id="s1", outcome="expired", filled_at_ms=3 * _HOUR
        )
        exc = compute_excursions(conn)
        row = exc.iloc[0]
        assert row["mfe_r"] == pytest.approx(1.4)
        assert row["mae_r"] == pytest.approx(0.8)
        assert row["bars_held"] == 3

    def test_mfe_floors_at_zero_on_first_bar_stopout(self) -> None:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        # Single-bar loss: no prior bars -> MFE 0.0 (never favorable).
        _insert_ohlcv(
            conn,
            "BTCUSDT",
            "1h",
            [{"open_time": _HOUR, "high": 99.0, "low": 94.0, "close": 95.0}],
        )
        _insert_resolved(
            conn, signal_id="s1", outcome="loss", filled_at_ms=_HOUR
        )
        exc = compute_excursions(conn)
        row = exc.iloc[0]
        assert row["mfe_r"] == 0.0
        assert row["mae_r"] == pytest.approx(1.2)


class TestComputeExcursionsRobustness:
    def test_zero_risk_row_skipped(self) -> None:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        _insert_ohlcv(
            conn,
            "BTCUSDT",
            "1h",
            [{"open_time": _HOUR, "high": 101.0, "low": 99.0, "close": 100.0}],
        )
        _insert_resolved(
            conn, signal_id="s1", outcome="loss", filled_at_ms=_HOUR, sl=100.0
        )
        assert compute_excursions(conn).empty

    def test_missing_ohlcv_row_skipped(self) -> None:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        _insert_resolved(
            conn, signal_id="s1", outcome="loss", filled_at_ms=_HOUR
        )
        assert compute_excursions(conn).empty

    def test_open_rows_excluded(self) -> None:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        _insert_ohlcv(
            conn,
            "BTCUSDT",
            "1h",
            [{"open_time": _HOUR, "high": 101.0, "low": 99.0, "close": 100.0}],
        )
        upsert_signal_outcome(
            conn,
            {
                "signal_id": "s-open",
                "symbol": "BTCUSDT",
                "tf": "1h",
                "strategy": "fvg",
                "direction": "long",
                "fired_at_ms": 0,
                "candle_ts_ms": 0,
                "entry_price": 100.0,
                "sl_price": 95.0,
                "tp_price": 110.0,
                "rr_ratio": 2.0,
                "confidence_at_fire": 3,
                "tags": "",
            },
        )
        assert compute_excursions(conn).empty

    def test_batches_multiple_symbol_tf_groups(self) -> None:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        _insert_ohlcv(
            conn,
            "BTCUSDT",
            "1h",
            [{"open_time": _HOUR, "high": 104.0, "low": 98.0, "close": 99.0}],
        )
        _insert_ohlcv(
            conn,
            "ETHUSDT",
            "4h",
            [{"open_time": 4 * _HOUR, "high": 12.0, "low": 9.7, "close": 11.0}],
        )
        _insert_resolved(
            conn, signal_id="b1", outcome="expired", filled_at_ms=_HOUR
        )
        _insert_resolved(
            conn,
            signal_id="e1",
            outcome="expired",
            filled_at_ms=4 * _HOUR,
            symbol="ETHUSDT",
            tf="4h",
            entry=10.0,
            sl=9.5,
            tp=11.0,
        )
        exc = compute_excursions(conn)
        assert set(exc["symbol"]) == {"BTCUSDT", "ETHUSDT"}
        eth = exc[exc["symbol"] == "ETHUSDT"].iloc[0]
        assert eth["mfe_r"] == pytest.approx(4.0)  # (12-10)/0.5
        assert eth["mae_r"] == pytest.approx(0.6)  # (10-9.7)/0.5 -> fav; high 12 adv? no: long -> adv=(10-9.7)/0.5


class TestAggregateCohorts:
    def _exc_df(self) -> pd.DataFrame:
        rows = [
            # 4 expired in one cell: MFE 0.2 / 0.6 / 1.5 / 0.1
            ("e1", "expired", 0.2, 0.3),
            ("e2", "expired", 0.6, 0.5),
            ("e3", "expired", 1.5, 0.4),
            ("e4", "expired", 0.1, 1.1),
            # 1 loss in same cell (filtered out at min_n=2)
            ("l1", "loss", 0.8, 1.2),
        ]
        return pd.DataFrame(
            [
                {
                    "signal_id": sid,
                    "symbol": "BTCUSDT",
                    "tf": "1h",
                    "strategy": "fvg",
                    "direction": "long",
                    "outcome": outcome,
                    "outcome_r": -0.1,
                    "rr_ratio": 2.0,
                    "mfe_r": mfe,
                    "mae_r": mae,
                    "bars_held": 10,
                }
                for sid, outcome, mfe, mae in rows
            ]
        )

    def test_reach_fractions_and_min_n(self) -> None:
        agg = aggregate_cohorts(self._exc_df(), min_n=2)
        assert len(agg) == 1
        row = agg.iloc[0]
        assert row["outcome"] == "expired"
        assert row["n"] == 4
        assert row["reach_05"] == pytest.approx(0.5)
        assert row["reach_10"] == pytest.approx(0.25)
        assert row["tp_r_p50"] == pytest.approx(2.0)

    def test_overall_rollup_groups_by_outcome_only(self) -> None:
        agg = aggregate_cohorts(self._exc_df(), by=(), min_n=1)
        assert set(agg["outcome"]) == {"expired", "loss"}
        assert "strategy" not in agg.columns

    def test_empty_input_returns_empty(self) -> None:
        assert aggregate_cohorts(pd.DataFrame(), min_n=1).empty
```

Note on `test_batches_multiple_symbol_tf_groups`: the ETH long has entry 10.0, sl 9.5 (risk 0.5); the single expired bar has high 12.0 → fav (12−10)/0.5 = 4.0 and low 9.7 → adv (10−9.7)/0.5 = 0.6. The trailing inline comment in the test body must be cleaned to just `# (10-9.7)/0.5` when writing the file.

- [ ] **Step 1.2: Run to verify failure**

Run: `poetry run pytest tests/test_mfe_mae.py -x -q`
Expected: ImportError — `analytics.exits` does not exist.

- [ ] **Step 1.3: Implement** — create `analytics/exits/__init__.py`:

```python
"""Exit-policy research package (exit spec 2026-06-05).

Diagnostic-only for now: the §2 MFE/MAE excursion study. The policy library
(`policies.py`) and pluggable replay engine (`replay.py`) land with the
exit-sweep PR, gated on this diagnostic's verdict.
"""

from analytics.exits.mfe_mae import (
    EXCURSION_COLUMNS,
    aggregate_cohorts,
    compute_excursions,
)

__all__ = ["EXCURSION_COLUMNS", "aggregate_cohorts", "compute_excursions"]
```

and `analytics/exits/mfe_mae.py`:

```python
"""Per-alert MFE/MAE excursion study over the live outcome ledger (exit spec §2).

For every resolved `signal_alert_outcomes` row (win / loss / expired), walk
the OHLCV bars the trade actually held — strictly after the signal candle
(`candle_ts_ms`) up to and including the exit bar (`outcome_filled_at_ms`,
as resolved by `analytics/signal/outcome_backfill.py`) — and record, in R
units (÷ |entry − sl|):

  - mfe_r: max favorable excursion (best unrealized R reached, floored at 0)
  - mae_r: max adverse excursion (worst unrealized R, positive magnitude,
    floored at 0)

Conservative intrabar conventions (anti-bias, exit spec §4):

  - loss exit bar: its favorable extreme does NOT count toward MFE — no way
    to know the favorable wick printed before the stop touch (adverse-first,
    mirrors `_scan_forward`'s same-bar tie rule).
  - win exit bar: MFE clamps to max(prior-bar MFE, rr_ratio) — post-TP
    overshoot is not credited; the exit bar's adverse extreme DOES count
    toward MAE (assume it printed before TP).
  - expired: both extremes of every in-window bar count.

Excursions are GROSS of costs — price-path geometry for exit design; net
realized PnL (fee/slippage/funding) already lives in `outcome_r` (P0b PR-3).

Pure functions over a DuckDB conn / DataFrames; no clock or network I/O.
"""

import duckdb
import numpy as np
import pandas as pd

from analytics.data_store import get_ohlcv

EXCURSION_COLUMNS = [
    "signal_id",
    "symbol",
    "tf",
    "strategy",
    "direction",
    "outcome",
    "outcome_r",
    "rr_ratio",
    "mfe_r",
    "mae_r",
    "bars_held",
]


def _excursion_for_row(
    window: pd.DataFrame,
    *,
    direction: str,
    entry: float,
    sl_price: float,
    rr_ratio: float,
    outcome: str,
) -> tuple[float, float] | None:
    """(mfe_r, mae_r) for one resolved alert over its held window.

    `window` holds the bars strictly after the signal candle up to and
    including the exit bar, in time order. Returns None when the window is
    empty or risk is zero (excursions in R are undefined).
    """
    if window.empty:
        return None
    risk = abs(entry - sl_price)
    if risk <= 0.0:
        return None
    high = window["high"].to_numpy(dtype=np.float64)
    low = window["low"].to_numpy(dtype=np.float64)
    if direction == "long":
        fav = (high - entry) / risk
        adv = (entry - low) / risk
    else:
        fav = (entry - low) / risk
        adv = (high - entry) / risk

    prior_fav = float(fav[:-1].max()) if len(fav) > 1 else 0.0
    if outcome == "loss":
        mfe = prior_fav
    elif outcome == "win":
        mfe = max(prior_fav, float(rr_ratio))
    else:  # expired — no intrabar exit event; every extreme was reachable
        mfe = float(fav.max())
    mae = float(adv.max())
    return max(mfe, 0.0), max(mae, 0.0)


def compute_excursions(conn: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Per-alert MFE/MAE rows for every resolved ledger row (EXCURSION_COLUMNS).

    Groups rows by (symbol, tf) so each group's OHLCV is fetched once — the
    same batching shape as `backfill_outcomes`. Rows with zero risk, an empty
    held window, or missing OHLCV are dropped; the caller can diff
    len(result) against the resolved count for coverage.
    """
    rows = conn.execute(
        "SELECT signal_id, symbol, tf, strategy, direction, candle_ts_ms, "
        "entry_price, sl_price, rr_ratio, outcome, outcome_r, "
        "outcome_filled_at_ms "
        "FROM signal_alert_outcomes "
        "WHERE outcome IN ('win', 'loss', 'expired') "
        "AND candle_ts_ms IS NOT NULL "
        "AND entry_price IS NOT NULL "
        "AND sl_price IS NOT NULL "
        "AND rr_ratio IS NOT NULL "
        "AND outcome_filled_at_ms IS NOT NULL"
    ).fetchall()
    if not rows:
        return pd.DataFrame(columns=EXCURSION_COLUMNS)

    by_group: dict[tuple[str, str], list[tuple]] = {}
    for r in rows:
        by_group.setdefault((str(r[1]), str(r[2])), []).append(r)

    out: list[dict[str, object]] = []
    for (symbol, tf), grp in by_group.items():
        start = min(int(r[5]) for r in grp)
        end = max(int(r[11]) for r in grp)
        bars = get_ohlcv(conn, symbol, tf, start, end)
        if bars.empty:
            continue
        open_time = bars["open_time"].to_numpy(dtype=np.int64)
        for (
            signal_id,
            _sym,
            _tf,
            strategy,
            direction,
            candle_ts_ms,
            entry_price,
            sl_price,
            rr_ratio,
            outcome,
            outcome_r,
            filled_at_ms,
        ) in grp:
            lo_i = int(np.searchsorted(open_time, int(candle_ts_ms), side="right"))
            hi_i = int(np.searchsorted(open_time, int(filled_at_ms), side="right"))
            exc = _excursion_for_row(
                bars.iloc[lo_i:hi_i],
                direction=str(direction),
                entry=float(entry_price),
                sl_price=float(sl_price),
                rr_ratio=float(rr_ratio),
                outcome=str(outcome),
            )
            if exc is None:
                continue
            mfe_r, mae_r = exc
            out.append(
                {
                    "signal_id": str(signal_id),
                    "symbol": symbol,
                    "tf": tf,
                    "strategy": str(strategy),
                    "direction": str(direction),
                    "outcome": str(outcome),
                    "outcome_r": float(outcome_r)
                    if outcome_r is not None
                    else float("nan"),
                    "rr_ratio": float(rr_ratio),
                    "mfe_r": mfe_r,
                    "mae_r": mae_r,
                    "bars_held": hi_i - lo_i,
                }
            )
    return pd.DataFrame(out, columns=EXCURSION_COLUMNS)


def aggregate_cohorts(
    excursions: pd.DataFrame,
    *,
    by: tuple[str, ...] = ("strategy", "tf", "direction"),
    min_n: int = 30,
) -> pd.DataFrame:
    """Cohort-level MFE/MAE aggregation — the exit spec §2 table.

    Groups by (outcome, *by); pass by=() for the overall per-cohort roll-up.
    Columns map onto the spec's 4-pattern verdict grid: reach_05 / reach_10
    are the share of the cohort whose MFE hit ≥0.5R / ≥1.0R, and tp_r_p50 is
    the target those trades were asked to reach. Cells below min_n are
    dropped (diagnostic n-floor).
    """
    if excursions.empty:
        return pd.DataFrame()
    keys = ["outcome", *by]
    enriched = excursions.assign(
        reach_05=(excursions["mfe_r"] >= 0.5).astype(float),
        reach_10=(excursions["mfe_r"] >= 1.0).astype(float),
    )
    agg = (
        enriched.groupby(keys)
        .agg(
            n=("mfe_r", "size"),
            mfe_mean=("mfe_r", "mean"),
            mfe_p50=("mfe_r", "median"),
            mae_mean=("mae_r", "mean"),
            mae_p50=("mae_r", "median"),
            reach_05=("reach_05", "mean"),
            reach_10=("reach_10", "mean"),
            tp_r_p50=("rr_ratio", "median"),
            bars_held_p50=("bars_held", "median"),
            outcome_r_mean=("outcome_r", "mean"),
        )
        .reset_index()
    )
    agg = agg[agg["n"] >= min_n]
    return agg.sort_values(keys).reset_index(drop=True)
```

- [ ] **Step 1.4: Run tests to verify pass**

Run: `poetry run pytest tests/test_mfe_mae.py -v`
Expected: all PASS.

- [ ] **Step 1.5: Lint + typecheck + full suite**

Run: `make lint-py && make typecheck && make test`
Expected: all green (1699 existing + new).

- [ ] **Step 1.6: Commit**

```bash
git add analytics/exits/ tests/test_mfe_mae.py
git commit -m "feat(exits): MFE/MAE excursion study over the live ledger (N2, exit spec §2)"
```

### Task 2: `tools/exit_audit.py` — diagnose CLI

**Files:**

- Create: `tools/exit_audit.py`

- [ ] **Step 2.1: Write the tool** (thin, read-only; print-only logic untested by convention — mirrors `tools/live_outcomes_report.py`):

```python
"""MFE/MAE diagnostic over the live alert ledger (exit spec §2) — diagnose mode.

Answers "is the 40%-expiry leak exit-fixable or entry-broken?" by reporting,
per cohort (win / loss / expired) and per (strategy, tf, direction) cell:
mean/median MFE_R and MAE_R, the share of trades whose MFE reached
>=0.5R / >=1.0R, the median tp_r they were asked to reach, and median bars
held. Read the tables against the 4-pattern verdict grid in
`docs/redesign/2026-06-05-exit-improvement-spec.md` §2.

Read-only — no writes, no schema changes. The exit-policy sweep modes land
in a later PR (spec §3–§5).

Usage::

    PYTHONPATH=. poetry run python tools/exit_audit.py
    PYTHONPATH=. poetry run python tools/exit_audit.py --min-n 20
    PYTHONPATH=. poetry run python tools/exit_audit.py --csv /tmp/excursions.csv
"""

from __future__ import annotations

import argparse
from pathlib import Path

import duckdb
import pandas as pd

from analytics.exits import aggregate_cohorts, compute_excursions
from analytics.store import DEFAULT_DB_PATH


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
        "--min-n",
        type=int,
        default=30,
        help="hide cohort×cell rows with fewer than this many trades",
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=None,
        help="optional path to dump the per-alert excursion rows",
    )
    args = parser.parse_args()

    con = duckdb.connect(str(args.db), read_only=True)
    print(f"DB: {args.db}")

    excursions = compute_excursions(con)
    resolved_row = con.execute(
        "SELECT count(*) FROM signal_alert_outcomes "
        "WHERE outcome IN ('win', 'loss', 'expired')"
    ).fetchone()
    resolved = int(resolved_row[0]) if resolved_row else 0
    print(
        f"Coverage: {len(excursions)} of {resolved} resolved alerts scored "
        f"({resolved - len(excursions)} skipped: zero-risk or missing OHLCV)"
    )
    if excursions.empty:
        print("(nothing to report)")
        return

    _print_df("Cohort roll-up (all cells)", aggregate_cohorts(excursions, by=(), min_n=1))
    _print_df(
        f"Cohort × (strategy, tf, direction) — min_n={args.min_n}",
        aggregate_cohorts(excursions, min_n=args.min_n),
    )
    print(
        "\nVerdict grid: docs/redesign/2026-06-05-exit-improvement-spec.md §2 — "
        "expired reach_10 high + tp_r_p50 higher => lower tp / partials; "
        "expired reach_05 low => entry problem, don't tune exits; "
        "loss mfe high => breakeven/trail candidate."
    )

    if args.csv is not None:
        excursions.to_csv(args.csv, index=False)
        print(f"\nPer-alert excursions written to {args.csv}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2.2: Smoke-run against the real DB (read-only)**

Run: `PYTHONPATH=. poetry run python tools/exit_audit.py --min-n 30`
Expected: coverage ≈ 2,489 scored; three tables print; no exceptions, no writes.

- [ ] **Step 2.3: Lint + typecheck**

Run: `make lint-py && make typecheck`
Expected: green.

- [ ] **Step 2.4: Commit**

```bash
git add tools/exit_audit.py
git commit -m "feat(tools): exit_audit diagnose mode — MFE/MAE tables per cohort × cell (N2)"
```

### Task 3: DoD gates + docs sync

**Files:**

- Modify: `CLAUDE.md` (analytics package list: add `exits/`; tools list: add `exit_audit.py`)
- Modify: `.claude/context/analytics.md` (module reference entry for `analytics/exits/`)

- [ ] **Step 3.1:** Add to `CLAUDE.md` analytics section (after the `research_guards/` bullet):

```markdown
  - `exits/` — exit-policy research package (exit spec `docs/redesign/2026-06-05-exit-improvement-spec.md`). Diagnostic-only so far: `mfe_mae.py` (`compute_excursions` — per-resolved-alert MFE_R/MAE_R over the actually-held OHLCV window from `candle_ts_ms` to `outcome_filled_at_ms`, conservative adverse-first exit-bar clamps, gross of costs; `aggregate_cohorts` — cohort (win/loss/expired) × (strategy, tf, direction) table with reach_05/reach_10 fractions). Policy library + replay engine land with the exit-sweep PR, gated on the N2 verdict.
```

and to the `tools/` section:

```markdown
  - `exit_audit.py` — N2 MFE/MAE diagnostic (exit spec §2, diagnose mode only): per-cohort × per-cell excursion tables answering "is the 40%-expiry leak exit-fixable or entry-broken?". Read-only. Run via `PYTHONPATH=. poetry run python tools/exit_audit.py [--min-n N] [--csv <path>]`. Verdict doc: `docs/audits/2026-06-11-mfe-mae-diagnostic.md`.
```

- [ ] **Step 3.2:** Mirror a condensed entry in `.claude/context/analytics.md` (follow that file's existing format).

- [ ] **Step 3.3: Full DoD**

Run: `make lint-py && make typecheck && make test && make test-regression && make lint-md`
Expected: all green; regression goldens UNMOVED (no engine/config change).

- [ ] **Step 3.4: Commit**

```bash
git add CLAUDE.md .claude/context/analytics.md
git commit -m "docs(context): document analytics/exits + tools/exit_audit (N2)"
```

### Task 4: Run the diagnostic + write the verdict

**Files:**

- Create: `docs/audits/2026-06-11-mfe-mae-diagnostic.md`

- [ ] **Step 4.1:** Run `PYTHONPATH=. poetry run python tools/exit_audit.py --min-n 30 --csv /tmp/excursions.csv` and `--min-n 15` (context pass for thin cells).
- [ ] **Step 4.2:** Write the audit doc: tables (overall + per-cell), then a per-cell verdict applying the spec's 4-pattern grid verbatim (pattern → interpretation → action), an explicit overall go/no-go for exit-policy work, and which v1 policies (§3: time-stop / breakeven / trail / partial) the numbers justify. Note caveats: ledger mixes pre/post-PR-3 cost basis in `outcome_r` (excursions themselves are gross and unaffected); 15m shorts dominate n.
- [ ] **Step 4.3:** `make lint-md` → green.
- [ ] **Step 4.4: Commit**

```bash
git add docs/audits/2026-06-11-mfe-mae-diagnostic.md
git commit -m "docs(audits): N2 MFE/MAE diagnostic verdict — exit-fixable vs entry-broken per cell"
```

### Task 5: PR + wrap-up

- [ ] **Step 5.1:** Push branch; `gh pr create` (gh on `s10023`).
- [ ] **Step 5.2:** Invoke `/pr-summary` then `/post-branch` (docs sweep + readiness check) before reporting the PR URL.
- [ ] **Step 5.3:** Update `MEMORY.md` Current State + SoT (`project_todo_master.md` N2 row → done with verdict one-liner).

## Self-Review

- **Spec coverage:** §2 MFE_R/MAE_R in R units ✓ (Task 1); cohort × cell aggregation ✓ (aggregate_cohorts); 4-pattern verdict ✓ (Task 4 doc; reach_05/reach_10/tp_r_p50 map to the patterns); §10.6 diagnostic-only scope ✓ (no policies.py/replay.py); §4 conservative conventions applied to measurement ✓ (design decision 2). Held-window deviation from the literal spec documented with rationale (design decision 1).
- **Placeholder scan:** none — all code complete.
- **Type consistency:** `compute_excursions(conn)` / `aggregate_cohorts(excursions, *, by, min_n)` used identically in tests, tool, and lib; `EXCURSION_COLUMNS` order matches the dict literal in `compute_excursions`.
