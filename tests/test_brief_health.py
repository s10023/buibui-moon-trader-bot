"""Health footer: staleness boundaries, missing data, notes passthrough."""

from analytics.brief.config import BriefConfig
from analytics.brief.health import build_health
from tests._brief_fixtures import DAY_MS, START_MS, make_conn, seed_symbol

SYM = "BTCUSDT"


def _cfg(as_of_ms: int) -> BriefConfig:
    return BriefConfig(symbols=(SYM,), as_of_ms=as_of_ms)


def test_health_fresh() -> None:
    conn = make_conn()
    seed_symbol(conn, SYM, START_MS, 60)
    report = build_health(conn, _cfg(START_MS + 60 * DAY_MS), [])
    assert report.data_ok is True
    assert {r.tf for r in report.rows} == {"4h", "1d", "1h"}
    assert all(r.status == "ok" for r in report.rows)


def test_health_stale_after_gap() -> None:
    conn = make_conn()
    seed_symbol(conn, SYM, START_MS, 60)
    # as_of 3 days past the last seeded close -> 1d is 3 bars behind
    report = build_health(conn, _cfg(START_MS + 63 * DAY_MS), [])
    assert report.data_ok is False
    d1 = next(r for r in report.rows if r.tf == "1d")
    assert d1.status == "stale" and d1.bars_behind == 3


def test_health_missing_symbol() -> None:
    conn = make_conn()
    report = build_health(conn, _cfg(START_MS), ["a note"])
    assert report.data_ok is False
    assert all(r.status == "missing" for r in report.rows)
    assert report.notes == ["a note"]
