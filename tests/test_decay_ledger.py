"""Tests for tools/decay_ledger.py — the pinned, UTC-bucketed Leg 3 (#837)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import duckdb
import pytest

from tools.decay_ledger import NO_CUT_MS, buckets, cutoff_ms_from_manifest, pooled

# 2026-03-15 20:00 UTC == 2026-03-16 04:00 MYT: UTC says 03-a, MYT would say 03-b.
EDGE_MS = 1_773_604_800_000
LATER_MS = EDGE_MS + 86_400_000 * 5


def _conn(tz: str) -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    conn.execute(f"SET TimeZone = '{tz}'")
    conn.execute(
        "CREATE TABLE signal_alert_outcomes (outcome TEXT, outcome_r DOUBLE, "
        "outcome_filled_at_ms BIGINT)"
    )
    conn.execute(
        "INSERT INTO signal_alert_outcomes VALUES "
        "('sl', -1.0, ?), ('tp', 2.0, ?), (NULL, NULL, ?)",
        [EDGE_MS, LATER_MS, LATER_MS],
    )
    return conn


@pytest.mark.parametrize("tz", ["UTC", "Asia/Kuala_Lumpur", "America/New_York"])
def test_bucket_is_utc_whatever_the_session_timezone(tz: str) -> None:
    got = [(b.bucket, b.n) for b in buckets(_conn(tz))]
    assert got == [("2026-03-a", 1), ("2026-03-b", 1)]


def test_unresolved_rows_are_excluded_from_pooled() -> None:
    assert pooled(_conn("UTC")) == (2, 0.5)


def test_time_cut_drops_later_fills() -> None:
    conn = _conn("UTC")
    assert pooled(conn, EDGE_MS) == (1, -1.0)
    assert [b.bucket for b in buckets(conn, EDGE_MS)] == ["2026-03-a"]
    assert pooled(conn, NO_CUT_MS)[0] == 2


def test_manifest_cutoff_reads_captured_at_utc(tmp_path: Path) -> None:
    m = tmp_path / "MANIFEST.json"
    m.write_text(
        json.dumps({"captured_at_utc": "2026-03-15T20:00:00Z"}), encoding="utf-8"
    )
    assert cutoff_ms_from_manifest(m) == EDGE_MS


def test_bare_invocation_works(tmp_path: Path) -> None:
    db = tmp_path / "a.db"
    _conn("UTC").execute(
        f"ATTACH '{db}' AS f; CREATE TABLE f.signal_alert_outcomes AS "
        "SELECT * FROM signal_alert_outcomes; DETACH f"
    )
    root = Path(__file__).resolve().parents[1]
    out = subprocess.run(
        [sys.executable, str(root / "tools/decay_ledger.py"), "--db", str(db)],
        capture_output=True,
        text=True,
        check=True,
        cwd=tmp_path,
    ).stdout
    assert "2026-03-a" in out and "pooled" in out
