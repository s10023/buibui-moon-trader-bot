"""Tests for the ST39 rr_ratio restatement migration.

The forward fix (analytics/signal/_common.py::realised_rr) makes new rows carry
the R their own `tp_price` implies. Rows written before it carry the *requested*
`tp_r`, so without this migration the column holds two bases permanently — the
same trap `outcome_r` already carries from its gross/net break at e5d92bb.

Only wins need an `outcome_r` adjustment: a loss pays -1.0 and an expired row
pays mark-to-market, neither of which reads `rr_ratio`.
"""

import duckdb
import pytest

from analytics.store import init_schema, upsert_signal_outcome
from tools.restate_rr_ratio import restate_rr_ratio


def _insert(
    conn: duckdb.DuckDBPyConnection,
    *,
    signal_id: str,
    entry: float,
    sl: float,
    tp: float,
    rr: float,
    outcome: str | None,
    outcome_r: float | None,
) -> None:
    upsert_signal_outcome(
        conn,
        {
            "signal_id": signal_id,
            "symbol": "BTCUSDT",
            "tf": "4h",
            "strategy": "fib_golden_zone",
            "direction": "long",
            "fired_at_ms": 0,
            "candle_ts_ms": 0,
            "entry_price": entry,
            "sl_price": sl,
            "tp_price": tp,
            "rr_ratio": rr,
            "confidence_at_fire": 3,
            "tags": "",
        },
    )
    if outcome is not None:
        conn.execute(
            "UPDATE signal_alert_outcomes SET outcome = ?, outcome_r = ? "
            "WHERE signal_id = ?",
            [outcome, outcome_r, signal_id],
        )


def _row(conn: duckdb.DuckDBPyConnection, signal_id: str) -> tuple:
    r = conn.execute(
        "SELECT rr_ratio, outcome_r FROM signal_alert_outcomes WHERE signal_id = ?",
        [signal_id],
    ).fetchone()
    assert r is not None
    return r


def _conn() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    return conn


class TestRestateRrRatio:
    def test_win_gets_both_rr_and_outcome_r_restated(self) -> None:
        # risk 4, tp 106 -> implied 1.5R, but the row stores the requested 3.0
        # and was paid 3.0. Restating removes the 1.5R it was over-credited.
        conn = _conn()
        _insert(
            conn,
            signal_id="win1",
            entry=100.0,
            sl=96.0,
            tp=106.0,
            rr=3.0,
            outcome="win",
            outcome_r=3.0,
        )

        counts = restate_rr_ratio(conn, apply=True)

        rr, outcome_r = _row(conn, "win1")
        assert rr == pytest.approx(1.5)
        assert outcome_r == pytest.approx(1.5)
        assert counts["rr_restated"] == 1
        assert counts["outcome_r_restated"] == 1
        assert counts["r_removed"] == pytest.approx(-1.5)

    def test_loss_restates_rr_only_and_leaves_outcome_r(self) -> None:
        """A loss pays -1.0 whatever the ratio claims, so its R must not move."""
        conn = _conn()
        _insert(
            conn,
            signal_id="loss1",
            entry=100.0,
            sl=96.0,
            tp=106.0,
            rr=3.0,
            outcome="loss",
            outcome_r=-1.0,
        )

        counts = restate_rr_ratio(conn, apply=True)

        rr, outcome_r = _row(conn, "loss1")
        assert rr == pytest.approx(1.5)
        assert outcome_r == pytest.approx(-1.0)
        assert counts["rr_restated"] == 1
        assert counts["outcome_r_restated"] == 0

    def test_dry_run_writes_nothing(self) -> None:
        conn = _conn()
        _insert(
            conn,
            signal_id="win1",
            entry=100.0,
            sl=96.0,
            tp=106.0,
            rr=3.0,
            outcome="win",
            outcome_r=3.0,
        )

        counts = restate_rr_ratio(conn)  # apply defaults to False

        rr, outcome_r = _row(conn, "win1")
        assert rr == pytest.approx(3.0), "dry-run must not write"
        assert outcome_r == pytest.approx(3.0), "dry-run must not write"
        assert counts["rr_restated"] == 1, "but it still REPORTS what it would do"

    def test_consistent_row_is_untouched(self) -> None:
        """entry 100, sl 96 -> risk 4, tp 116 -> implied 4.0 == stored. Not a candidate."""
        conn = _conn()
        _insert(
            conn,
            signal_id="ok1",
            entry=100.0,
            sl=96.0,
            tp=116.0,
            rr=4.0,
            outcome="win",
            outcome_r=4.0,
        )

        counts = restate_rr_ratio(conn, apply=True)

        rr, outcome_r = _row(conn, "ok1")
        assert rr == pytest.approx(4.0)
        assert outcome_r == pytest.approx(4.0)
        assert counts["rr_restated"] == 0

    def test_is_idempotent(self) -> None:
        conn = _conn()
        _insert(
            conn,
            signal_id="win1",
            entry=100.0,
            sl=96.0,
            tp=106.0,
            rr=3.0,
            outcome="win",
            outcome_r=3.0,
        )

        restate_rr_ratio(conn, apply=True)
        second = restate_rr_ratio(conn, apply=True)

        assert second["rr_restated"] == 0
        rr, outcome_r = _row(conn, "win1")
        assert rr == pytest.approx(1.5)
        assert outcome_r == pytest.approx(1.5)

    def test_zero_risk_row_is_skipped(self) -> None:
        """entry == sl leaves the implied ratio undefined — nothing to restate to."""
        conn = _conn()
        _insert(
            conn,
            signal_id="zero1",
            entry=100.0,
            sl=100.0,
            tp=110.0,
            rr=2.0,
            outcome="win",
            outcome_r=2.0,
        )

        counts = restate_rr_ratio(conn, apply=True)

        rr, outcome_r = _row(conn, "zero1")
        assert rr == pytest.approx(2.0)
        assert outcome_r == pytest.approx(2.0)
        assert counts["rr_restated"] == 0
        assert counts["skipped_zero_risk"] == 1
