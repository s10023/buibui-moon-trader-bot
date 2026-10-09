"""Tests for the data.binance.vision open-interest archive backfill (#936).

No test touches the network: `fetch` is injected, `urlopen` is patched.
"""

from __future__ import annotations

import argparse
import io
import urllib.error
import urllib.request
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
import pytest

from analytics import oi_archive as oa
from analytics.store import (
    get_open_interest_merged,
    init_schema,
    upsert_open_interest,
)
from analytics.store.oi_archive import ARCHIVE_SOURCE

DAY = date(2021, 1, 5)
DAY_MS = int(datetime(2021, 1, 5, tzinfo=UTC).timestamp() * 1000)
FIVE = 300_000
HOUR = 3_600_000
TODAY = date(2021, 6, 1)


def _csv_row(ts: datetime, i: int, symbol: str = "BTCUSDT") -> str:
    # oi_contracts = 1000+i, oi_usd = 5000+i, ratios encode i so rows are identifiable.
    return (
        f"{ts:%Y-%m-%d %H:%M:%S},{symbol},{1000 + i}.5,{5000 + i}.25,"
        f"{1 + i / 1000},{2 + i / 1000},{3 + i / 1000},{4 + i / 1000}"
    )


def make_zip(
    day: date = DAY,
    *,
    skip: set[int] | None = None,
    doubled: bool = False,
    shuffled: bool = False,
    header: str | None = None,
) -> bytes:
    """A metrics zip for `day`; slot i is the i-th 5-minute snapshot (0..287)."""
    skip = skip or set()
    rows: list[str] = []
    for i in range(288):
        if i in skip:
            continue
        ts = datetime(day.year, day.month, day.day, tzinfo=UTC) + pd.Timedelta(
            minutes=5 * i
        )
        rows.append(_csv_row(ts.replace(tzinfo=None), i))
    if doubled:
        rows = [r for r in rows for _ in (0, 1)]
    if shuffled:
        rows = rows[::-1]
    head = header or ",".join(oa.EXPECTED_HEADER)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            f"BTCUSDT-metrics-{day.isoformat()}.csv",
            head + "\n" + "\n".join(rows) + "\n",
        )
    return buf.getvalue()


def _conn() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    return conn


class FakeArchive:
    """Injectable fetch: serves `days` (date -> bytes), 404 elsewhere, counts calls."""

    def __init__(self, days: dict[date, bytes], fail: set[date] | None = None) -> None:
        self.days = days
        self.fail = fail or set()
        self.urls: list[str] = []

    def __call__(self, url: str) -> bytes | None:
        self.urls.append(url)
        d = date.fromisoformat(url.rsplit("-metrics-", 1)[1].removesuffix(".zip"))
        if d in self.fail:
            raise RuntimeError("boom")
        return self.days.get(d)


# ---------------------------------------------------------------- url + parse


def test_archive_url_shape() -> None:
    assert oa.archive_url("ETHUSDT", date(2022, 3, 4)) == (
        "https://data.binance.vision/data/futures/um/daily/metrics/ETHUSDT/"
        "ETHUSDT-metrics-2022-03-04.zip"
    )


def test_parse_maps_columns_and_utc_ms() -> None:
    df = oa.parse_metrics_zip(make_zip(), "BTCUSDT")
    assert list(df.columns) == oa.FIVE_MIN_COLUMNS
    assert len(df) == 288
    first = df.iloc[0]
    assert first["timestamp"] == DAY_MS
    assert first["oi_contracts"] == 1000.5
    assert first["oi_usd"] == 5000.25
    assert first["toptrader_count_ls_ratio"] == pytest.approx(1.0)
    assert first["toptrader_sum_ls_ratio"] == pytest.approx(2.0)
    assert first["count_ls_ratio"] == pytest.approx(3.0)
    assert first["taker_ls_vol_ratio"] == pytest.approx(4.0)
    assert df["timestamp"].diff().dropna().eq(FIVE).all()


def test_parse_sorts_a_shuffled_file() -> None:
    # Recent archive files arrive in non-time order (measured 2026-10).
    df = oa.parse_metrics_zip(make_zip(shuffled=True), "BTCUSDT")
    assert df["timestamp"].is_monotonic_increasing
    assert df.iloc[0]["timestamp"] == DAY_MS


def test_parse_collapses_doubled_rows() -> None:
    # Early files carry every row twice (measured: 576 rows for 288 timestamps).
    df = oa.parse_metrics_zip(make_zip(doubled=True), "BTCUSDT")
    assert len(df) == 288
    assert df["timestamp"].is_unique


def test_parse_rejects_an_unknown_header() -> None:
    bad = make_zip(header="create_time,symbol,something_new")
    with pytest.raises(ValueError, match="unexpected metrics header"):
        oa.parse_metrics_zip(bad, "BTCUSDT")


# ------------------------------------------------------------------ resample


def test_resample_stamps_hour_t_with_the_snapshot_labelled_five_minutes_earlier() -> (
    None
):
    # REST's row stamped T equals the archive row labelled T-5min (measured on the
    # real overlap), so the hour stamped 01:00 carries the 00:55 snapshot (slot 11).
    hourly = oa.resample_hourly(oa.parse_metrics_zip(make_zip(), "BTCUSDT"))
    assert len(hourly) == 24
    assert list(hourly["timestamp"]) == [DAY_MS + h * HOUR for h in range(1, 25)]
    assert hourly.iloc[0]["oi_usd"] == 5000 + 11 + 0.25
    assert hourly.iloc[0]["oi_contracts"] == 1000 + 11 + 0.5
    assert hourly.iloc[3]["oi_usd"] == 5000 + 47 + 0.25  # T=04:00 <- 03:55 (slot 47)
    assert hourly.iloc[-1]["oi_usd"] == 5000 + 287 + 0.25  # T=next-day 00:00 <- 23:55


def test_resample_falls_back_to_the_last_snapshot_before_the_target() -> None:
    # T=01:00 targets slot 11 (00:55). Drop it: slot 10 (00:50) stands in.
    hourly = oa.resample_hourly(oa.parse_metrics_zip(make_zip(skip={11}), "BTCUSDT"))
    row = hourly[hourly["timestamp"] == DAY_MS + HOUR].iloc[0]
    assert row["oi_usd"] == 5000 + 10 + 0.25


def test_resample_staleness_bound_is_ten_minutes_inclusive() -> None:
    # target 00:55 and 00:50 missing -> 00:45 is exactly 10 min older: accepted.
    ok = oa.resample_hourly(oa.parse_metrics_zip(make_zip(skip={11, 10}), "BTCUSDT"))
    row = ok[ok["timestamp"] == DAY_MS + HOUR].iloc[0]
    assert row["oi_usd"] == 5000 + 9 + 0.25
    # one more missing -> 15 min older: no row, a visible hole rather than a stale carry.
    hole = oa.resample_hourly(
        oa.parse_metrics_zip(make_zip(skip={11, 10, 9}), "BTCUSDT")
    )
    assert (DAY_MS + HOUR) not in set(hole["timestamp"])
    assert len(hole) == 23


def test_consecutive_day_files_tile_the_hour_grid_without_overlap() -> None:
    d2 = date(2021, 1, 6)
    h1 = oa.resample_hourly(oa.parse_metrics_zip(make_zip(), "BTCUSDT"))
    h2 = oa.resample_hourly(oa.parse_metrics_zip(make_zip(d2), "BTCUSDT"))
    stamps = list(h1["timestamp"]) + list(h2["timestamp"])
    assert stamps == [DAY_MS + h * HOUR for h in range(1, 49)]  # contiguous, unique


def test_a_missing_last_snapshot_leaves_the_day_end_hour_as_a_hole() -> None:
    # 23:55 is the file's evidence for the hour stamped next-day 00:00. Without it the
    # file cannot show that boundary, so it is a hole, never a value borrowed from the
    # next file (which would break the no-reach-across-files property).
    hole = oa.resample_hourly(oa.parse_metrics_zip(make_zip(skip={287}), "BTCUSDT"))
    assert len(hole) == 23
    assert hole["timestamp"].iloc[-1] == DAY_MS + 23 * HOUR


def test_hourly_frame_has_no_taker_ratio() -> None:
    hourly = oa.resample_hourly(oa.parse_metrics_zip(make_zip(), "BTCUSDT"))
    assert list(hourly.columns) == oa.HOURLY_COLUMNS
    assert "taker_ls_vol_ratio" not in hourly.columns


def test_resample_empty_input() -> None:
    assert oa.resample_hourly(pd.DataFrame(columns=oa.FIVE_MIN_COLUMNS)).empty


# ------------------------------------------------------------------ backfill


def _run(
    conn: duckdb.DuckDBPyConnection,
    fake: FakeArchive,
    *,
    start: date = DAY,
    end: date = date(2021, 1, 7),
    cache_dir: Path | None = None,
    **kw: Any,
) -> oa.SymbolResult:
    kw.setdefault("keep_5m", True)  # most tests count both tables
    return oa.backfill_symbol(
        conn,
        "BTCUSDT",
        start,
        end,
        fetch=fake,
        cache_dir=cache_dir,
        workers=2,
        today=TODAY,
        now_ms=1,
        **kw,
    )


def _count(conn: duckdb.DuckDBPyConnection, table: str) -> int:
    row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    assert row is not None
    return int(row[0])


def test_backfill_loads_hourly_and_5m_with_source() -> None:
    conn = _conn()
    fake = FakeArchive({DAY: make_zip(), date(2021, 1, 6): make_zip(date(2021, 1, 6))})
    res = _run(conn, fake, end=date(2021, 1, 6))
    assert (res.days_ok, res.days_missing, res.days_failed) == (2, 0, 0)
    assert _count(conn, "open_interest_archive") == 48
    assert _count(conn, "open_interest_archive_5m") == 576
    sources = conn.execute(
        "SELECT DISTINCT source FROM open_interest_archive"
    ).fetchall()
    assert sources == [(ARCHIVE_SOURCE,)]
    assert _count(conn, "open_interest") == 0  # REST table untouched


def test_missing_day_is_recorded_not_raised() -> None:
    conn = _conn()
    fake = FakeArchive({DAY: make_zip()})  # 01-06 and 01-07 are 404
    res = _run(conn, fake)
    assert (res.days_ok, res.days_missing) == (1, 2)
    ledger = dict(
        conn.execute("SELECT day, status FROM open_interest_archive_days").fetchall()
    )
    assert ledger == {
        "2021-01-05": "ok",
        "2021-01-06": "missing",
        "2021-01-07": "missing",
    }


def test_rerun_is_idempotent_and_skips_loaded_days() -> None:
    conn = _conn()
    fake = FakeArchive({DAY: make_zip(), date(2021, 1, 6): make_zip(date(2021, 1, 6))})
    _run(conn, fake, end=date(2021, 1, 6))
    n_calls = len(fake.urls)
    snap = conn.execute(
        "SELECT * FROM open_interest_archive ORDER BY timestamp"
    ).fetchall()
    again = _run(conn, fake, end=date(2021, 1, 6))
    assert len(fake.urls) == n_calls  # no request for a ledgered day
    assert again.days_skipped == 2 and again.days_ok == 0
    assert (
        conn.execute(
            "SELECT * FROM open_interest_archive ORDER BY timestamp"
        ).fetchall()
        == snap
    )


def test_force_reloads_without_duplicating() -> None:
    conn = _conn()
    fake = FakeArchive({DAY: make_zip()})
    _run(conn, fake, end=DAY)
    _run(conn, fake, end=DAY, force=True)
    assert len(fake.urls) == 2
    assert _count(conn, "open_interest_archive") == 24
    assert _count(conn, "open_interest_archive_5m") == 288


def test_force_replaces_changed_values() -> None:
    conn = _conn()
    _run(conn, FakeArchive({DAY: make_zip()}), end=DAY)
    buf = io.BytesIO()
    csv_text = zipfile.ZipFile(io.BytesIO(make_zip())).read(
        "BTCUSDT-metrics-2021-01-05.csv"
    )
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("m.csv", csv_text.replace(b"5000.25", b"9999.25"))
    _run(conn, FakeArchive({DAY: buf.getvalue()}), end=DAY, force=True)
    row = conn.execute(
        "SELECT oi_usd FROM open_interest_archive_5m WHERE timestamp = ?", [DAY_MS]
    ).fetchone()
    assert row == (9999.25,)
    assert _count(conn, "open_interest_archive") == 24
    assert _count(conn, "open_interest_archive_5m") == 288


def test_recent_missing_day_is_retried_old_missing_is_not() -> None:
    conn = _conn()
    old, recent = date(2021, 1, 5), date(2021, 5, 25)  # TODAY 2021-06-01 -> 7 days back
    fake = FakeArchive({})
    _run(conn, fake, start=old, end=old)
    _run(conn, fake, start=recent, end=recent)
    assert len(fake.urls) == 2
    _run(conn, fake, start=old, end=old)
    assert len(fake.urls) == 2  # old 404 is final
    _run(conn, fake, start=recent, end=recent)
    assert len(fake.urls) == 3  # recent 404 may just be publication lag
    _run(conn, fake, start=old, end=old, retry_missing=True)
    assert len(fake.urls) == 4


def test_a_missing_day_that_appears_later_is_loaded() -> None:
    conn = _conn()
    recent = date(2021, 5, 30)
    _run(conn, FakeArchive({}), start=recent, end=recent)
    res = _run(conn, FakeArchive({recent: make_zip(recent)}), start=recent, end=recent)
    assert res.days_ok == 1
    status = conn.execute(
        "SELECT status FROM open_interest_archive_days WHERE day = '2021-05-30'"
    ).fetchone()
    assert status == ("ok",)


def test_failed_day_is_counted_not_ledgered_and_does_not_stop_the_rest() -> None:
    conn = _conn()
    d2 = date(2021, 1, 6)
    fake = FakeArchive({DAY: make_zip(), d2: make_zip(d2)}, fail={DAY})
    res = _run(conn, fake, end=d2)
    assert res.days_failed == 1 and res.failed_days == ["2021-01-05"]
    assert res.days_ok == 1
    days = [
        r[0]
        for r in conn.execute("SELECT day FROM open_interest_archive_days").fetchall()
    ]
    assert days == ["2021-01-06"]  # the failed day will be retried next run


def test_default_writes_hourly_only() -> None:
    # 5m rows cost ~340 MB for the universe against ~28 MB hourly (measured): opt-in.
    conn = _conn()
    oa.backfill_symbol(
        conn,
        "BTCUSDT",
        DAY,
        DAY,
        fetch=FakeArchive({DAY: make_zip()}),
        cache_dir=None,
        today=TODAY,
    )
    assert _count(conn, "open_interest_archive") == 24
    assert _count(conn, "open_interest_archive_5m") == 0


def test_cache_serves_a_second_database_without_the_network(tmp_path: Path) -> None:
    fake = FakeArchive({DAY: make_zip()})
    _run(_conn(), fake, end=DAY, cache_dir=tmp_path)
    assert len(fake.urls) == 1
    assert (tmp_path / "BTCUSDT" / "BTCUSDT-metrics-2021-01-05.zip").is_file()
    conn2 = _conn()
    _run(conn2, fake, end=DAY, cache_dir=tmp_path)
    assert len(fake.urls) == 1  # served from cache
    assert _count(conn2, "open_interest_archive") == 24


def test_a_truncated_cache_entry_is_refetched(tmp_path: Path) -> None:
    path = tmp_path / "BTCUSDT" / "BTCUSDT-metrics-2021-01-05.zip"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"not a zip")
    fake = FakeArchive({DAY: make_zip()})
    _run(_conn(), fake, end=DAY, cache_dir=tmp_path)
    assert len(fake.urls) == 1
    assert zipfile.is_zipfile(path)


def test_junk_download_is_not_cached(tmp_path: Path) -> None:
    fake = FakeArchive({DAY: b"<html>rate limited</html>"})
    res = _run(_conn(), fake, end=DAY, cache_dir=tmp_path)
    assert res.days_failed == 1
    assert not list(tmp_path.rglob("*.zip"))


def test_symbol_start_is_clamped_to_the_listing_date() -> None:
    conn = _conn()
    onboard = int(datetime(2024, 3, 10, 12, tzinfo=UTC).timestamp() * 1000)
    conn.execute(
        "INSERT INTO symbol_lifecycle VALUES ('NEWUSDT', 'TRADING', ?, 1, 1, NULL)",
        [onboard],
    )
    assert oa.symbol_start(conn, "NEWUSDT", date(2020, 9, 1)) == date(2024, 3, 9)
    assert oa.symbol_start(conn, "NEWUSDT", date(2025, 1, 1)) == date(2025, 1, 1)
    assert oa.symbol_start(conn, "UNKNOWNUSDT", date(2020, 9, 1)) == date(2020, 9, 1)


def test_backfill_aggregates_download_counters() -> None:
    conn = _conn()
    zbytes = make_zip()
    fake = FakeArchive({DAY: zbytes})
    summary = oa.backfill(
        conn,
        ["BTCUSDT"],
        since=DAY,
        until=DAY,
        fetch=fake,
        cache_dir=None,
        workers=1,
        today=TODAY,
    )
    assert summary.bytes_downloaded == len(zbytes)
    assert summary.network_fetches == 1
    assert summary.failed_days == 0


# ------------------------------------------------------ source separation + read


def test_archive_write_never_touches_rest_rows_and_read_rule_prefers_rest() -> None:
    conn = _conn()
    # REST has the 01:00 and 02:00 boundaries of DAY with a deliberately different value.
    upsert_open_interest(
        conn,
        pd.DataFrame(
            {
                "symbol": ["BTCUSDT", "BTCUSDT"],
                "timestamp": [DAY_MS + HOUR, DAY_MS + 2 * HOUR],
                "oi_usd": [111.0, 222.0],
            }
        ),
    )
    _run(conn, FakeArchive({DAY: make_zip()}), end=DAY)
    rest = conn.execute(
        "SELECT timestamp, oi_usd FROM open_interest ORDER BY 1"
    ).fetchall()
    assert rest == [(DAY_MS + HOUR, 111.0), (DAY_MS + 2 * HOUR, 222.0)]

    merged = get_open_interest_merged(conn, "BTCUSDT", DAY_MS, DAY_MS + 24 * HOUR)
    assert len(merged) == 24  # one row per hour, never two
    assert merged["timestamp"].is_unique
    by_ts = merged.set_index("timestamp")
    assert by_ts.loc[DAY_MS + HOUR, "oi_usd"] == 111.0
    assert by_ts.loc[DAY_MS + HOUR, "source"] == "rest"
    assert by_ts.loc[DAY_MS + 3 * HOUR, "source"] == ARCHIVE_SOURCE
    assert by_ts.loc[DAY_MS + 3 * HOUR, "oi_usd"] == 5000 + 35 + 0.25
    assert (merged["source"] == "rest").sum() == 2


def test_merged_read_respects_symbol_and_window() -> None:
    conn = _conn()
    _run(conn, FakeArchive({DAY: make_zip()}), end=DAY)
    assert get_open_interest_merged(conn, "ETHUSDT", 0, DAY_MS * 2).empty
    w = get_open_interest_merged(conn, "BTCUSDT", DAY_MS + HOUR, DAY_MS + 2 * HOUR)
    assert list(w["timestamp"]) == [DAY_MS + HOUR, DAY_MS + 2 * HOUR]


def test_schema_adds_tables_without_changing_open_interest() -> None:
    conn = _conn()
    cols = [r[1] for r in conn.execute("PRAGMA table_info('open_interest')").fetchall()]
    assert cols == ["symbol", "timestamp", "oi_usd"]  # live key untouched: no migration
    init_schema(conn)  # idempotent
    tables = {r[0] for r in conn.execute("SHOW TABLES").fetchall()}
    assert {
        "open_interest_archive",
        "open_interest_archive_5m",
        "open_interest_archive_days",
    } <= tables


# ------------------------------------------------------------------ coverage


def test_coverage_reports_first_last_and_gap_days() -> None:
    conn = _conn()
    d = {date(2021, 1, n): make_zip(date(2021, 1, n)) for n in (3, 4, 7, 8)}
    # Jan 1-2 are leading 404s (history that does not exist); 5-6 are an interior gap.
    _run(conn, FakeArchive(d), start=date(2021, 1, 1), end=date(2021, 1, 9))
    (row,) = oa.coverage_report(conn, ["BTCUSDT"])
    assert (row.first_day, row.last_day) == ("2021-01-03", "2021-01-08")
    assert row.ok_days == 4
    assert row.gap_days == 2
    assert row.gap_ranges == (("2021-01-05", "2021-01-06"),)
    assert row.rows_hourly == 4 * 24 and row.rows_5m == 4 * 288
    text = oa.format_coverage([row])
    assert "2021-01-05..2021-01-06" in text and "BTCUSDT" in text


def test_coverage_for_a_symbol_with_no_data() -> None:
    (row,) = oa.coverage_report(_conn(), ["NOPEUSDT"])
    assert row.first_day is None and row.gap_days == 0 and row.ok_days == 0


# ------------------------------------------------------------------ http_fetch


class _Resp:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *a: object) -> None:
        return None


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("u", code, "x", None, None)  # type: ignore[arg-type]


def test_http_fetch_retries_429_and_5xx_with_doubling_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seq: list[Any] = [_http_error(429), _http_error(503), _Resp(b"ok")]

    def fake_urlopen(*a: object, **k: object) -> Any:
        item = seq.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    sleeps: list[float] = []
    assert oa.http_fetch("https://x.invalid/f.zip", sleep=sleeps.append) == b"ok"
    assert sleeps == [1.0, 2.0]


def test_http_fetch_404_is_none_and_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []

    def fake_urlopen(*a: object, **k: object) -> Any:
        calls.append(1)
        raise _http_error(404)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert oa.http_fetch("https://x.invalid/f.zip", sleep=lambda s: None) is None
    assert len(calls) == 1


def test_http_fetch_other_4xx_raises_immediately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_urlopen(*a: object, **k: object) -> Any:
        raise _http_error(403)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(urllib.error.HTTPError):
        oa.http_fetch("https://x.invalid/f.zip", sleep=lambda s: None)


def test_http_fetch_gives_up_after_the_retry_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_urlopen(*a: object, **k: object) -> Any:
        raise _http_error(500)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    sleeps: list[float] = []
    with pytest.raises(urllib.error.HTTPError):
        oa.http_fetch("https://x.invalid/f.zip", retries=3, sleep=sleeps.append)
    assert sleeps == [1.0, 2.0, 4.0]


# ----------------------------------------------------------------------- CLI


def test_cli_subcommand_is_registered_and_requires_a_symbol_source() -> None:
    from cli.analytics import add_analytics_subparser

    parser = argparse.ArgumentParser()
    add_analytics_subparser(parser.add_subparsers(dest="command"))
    args = parser.parse_args(
        ["analytics", "oi-archive", "--symbols", "BTCUSDT", "--with-5m"]
    )
    assert args.symbols == ["BTCUSDT"] and args.with_5m and args.workers == 4
    assert args.since == "2020-09-01" and args.db == "analytics.db"
    with pytest.raises(SystemExit):
        parser.parse_args(["analytics", "oi-archive"])
