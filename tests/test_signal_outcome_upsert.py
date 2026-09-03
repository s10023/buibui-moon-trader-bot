"""ST82 — a re-detected candle must not blank its own resolved outcome.

Ported from wifey #157. The write was `INSERT OR REPLACE` over all 16 columns
while its only caller (`analytics/signal/scanner.py`) passes no outcome fields,
so `row.get()` supplied NULLs and a re-scan silently restated a resolved row.

The two tests are a pair on purpose: without the second, "never overwrite
anything on conflict" (an `INSERT ... ON CONFLICT DO NOTHING`) passes the first
and quietly freezes every fire-time correction the scanner makes.
"""

from __future__ import annotations

from typing import Any

import duckdb
import pytest

from analytics.store.schema import init_schema
from analytics.store.signals import upsert_signal_outcome

_SIGNAL_ID = "BTCUSDT-15m-bos-1756800000000-long"


def _fire_row(**over: object) -> dict[str, object]:
    row: dict[str, object] = {
        "signal_id": _SIGNAL_ID,
        "symbol": "BTCUSDT",
        "tf": "15m",
        "strategy": "bos",
        "direction": "long",
        "fired_at_ms": 1_756_800_900_000,
        "candle_ts_ms": 1_756_800_000_000,
        "entry_price": 76_450.0,
        "sl_price": 75_700.0,
        "tp_price": 77_350.0,
        "rr_ratio": 1.2,
        "confidence_at_fire": 3,
        "tags": "bos long",
    }
    row.update(over)
    return row


@pytest.fixture
def conn() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect(":memory:")
    init_schema(c)
    return c


def _fetch(conn: duckdb.DuckDBPyConnection, cols: str) -> tuple[Any, ...]:
    row = conn.execute(
        f"SELECT {cols} FROM signal_alert_outcomes WHERE signal_id = ?",
        [_SIGNAL_ID],
    ).fetchone()
    assert row is not None, "the row is missing entirely"
    return tuple(row)


def _outcome(conn: duckdb.DuckDBPyConnection) -> tuple[Any, ...]:
    return _fetch(conn, "outcome, outcome_r, outcome_filled_at_ms")


def test_re_detection_preserves_a_resolved_outcome(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    upsert_signal_outcome(conn, _fire_row())
    # The resolver's own write path -- a direct UPDATE, never this function.
    conn.execute(
        "UPDATE signal_alert_outcomes "
        "SET outcome = ?, outcome_r = ?, outcome_filled_at_ms = ? "
        "WHERE signal_id = ?",
        ["WIN", 1.2, 1_756_890_000_000, _SIGNAL_ID],
    )

    # `--catch-up` replaying the same closed candle, or any re-scan of it.
    upsert_signal_outcome(conn, _fire_row())

    assert _outcome(conn) == ("WIN", 1.2, 1_756_890_000_000), (
        "a re-detected candle blanked its resolved outcome -- "
        "outcome_filled_at_ms is the column the cost-basis era split is taken on"
    )
    count = conn.execute("SELECT count(*) FROM signal_alert_outcomes").fetchone()
    assert count is not None and count[0] == 1


def test_re_detection_still_refreshes_the_fire_time_fields(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    """It must stay an UPSERT: DO NOTHING would pass the test above and freeze these."""
    upsert_signal_outcome(conn, _fire_row())
    upsert_signal_outcome(conn, _fire_row(sl_price=75_100.0, confidence_at_fire=5))

    assert _fetch(conn, "sl_price, confidence_at_fire") == (75_100.0, 5)


def test_a_caller_that_supplies_an_outcome_on_conflict_still_writes_it(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    """The contract narrows on PRESENCE, not on the column's name.

    Keying the guard on "never update an outcome column" would have silently
    no-opped this caller, trading one silent wrong write for a silent lost one.
    `tests/test_data_store.py::test_upsert_replaces_on_conflict` is the standing
    contract this preserves.
    """
    upsert_signal_outcome(conn, _fire_row())
    upsert_signal_outcome(
        conn,
        _fire_row(outcome="WIN", outcome_r=2.0, outcome_filled_at_ms=1_756_899_000_000),
    )
    assert _outcome(conn) == ("WIN", 2.0, 1_756_899_000_000)


def test_a_fresh_row_still_writes_every_column(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    upsert_signal_outcome(
        conn,
        _fire_row(
            outcome="LOSS", outcome_r=-1.0, outcome_filled_at_ms=1_756_888_000_000
        ),
    )
    assert _outcome(conn) == ("LOSS", -1.0, 1_756_888_000_000)
