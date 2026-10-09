"""Backfill open interest from the Binance `data.binance.vision` metrics archive (#936).

Source: `https://data.binance.vision/data/futures/um/daily/metrics/<SYM>/<SYM>-metrics-<YYYY-MM-DD>.zip`
(public, keyless, Binance-official). One CSV per symbol-day at 5-minute grain with
`create_time` (UTC), `sum_open_interest` (contracts), `sum_open_interest_value` (USD),
three long/short ratios and the taker buy/sell volume ratio. There is no monthly
`metrics` archive (404 measured), so history is one request per symbol-day.

Things measured on real files (2026-10-09) that the parser relies on:

- **Row order is not time order** on recent files (2026-10 files arrive shuffled), so the
  parser sorts by `create_time` and never assumes the file is ordered.
- **Early files carry every row twice** (2020-09 .. 2021-01 BTC: 576 rows for 288
  timestamps), so the parser keeps ONE row per timestamp.
- A file that is absent (404) is a gap, not an error: archive history starts at a
  per-symbol date, and the newest day is published with a lag.

HOURLY RESAMPLE RULE (the existing `open_interest` series is hourly): the row stamped
hour boundary `T` is the LAST 5-minute snapshot at or before `T - 5 minutes`
(`SNAP_LAG_MS`), accepted only if it is no more than `MAX_STALENESS_MS` (10 minutes,
two missed 5m snapshots) older than that target. A boundary with no acceptable snapshot
yields NO row, so a hole stays visible instead of being papered over with a stale
carry-forward. The 5-minute lag is MEASURED, not chosen: on the REST/archive overlap
(2026-03 to 2026-10, 7 symbols, 4,469 hours for the majors) the REST `open_interest`
value stamped `T` equals the archive row labelled `T - 5min` to float precision
(max relative difference 2.2e-16) and differs from the row labelled `T` by 0.07-0.24%
at the median, so this lag is what makes archive hourly rows the same series as the REST
ones rather than a series shifted by one snapshot. A consequence: day file D yields the
hours `D 01:00 .. D+1 00:00`, so consecutive files tile the hour grid with no overlap
and the rule never reads across a file boundary. OI and the three long/short ratios are
point-in-time state, so they take the same snapshot. The taker buy/sell ratio is a
per-5-minute FLOW; an hourly version needs volume weights the file does not carry, so
it is kept at 5m only and is NOT in the hourly table.

Idempotent: every write is an upsert on `(source, symbol, timestamp)`, and a per-day
ledger (`open_interest_archive_days`) lets a re-run skip days already loaded. A
`missing` day is final once it is older than `RETRY_MISSING_DAYS` (the archive lags by
about a day), unless `retry_missing` is set.
"""

from __future__ import annotations

import io
import logging
import os
import time
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from threading import Lock

import duckdb
import pandas as pd

from analytics.db_retry import connect_with_retry
from analytics.store.oi_archive import (
    ARCHIVE_SOURCE,
    DAY_STATUS_MISSING,
    DAY_STATUS_OK,
    FIVE_MIN_COLUMNS,
    HOURLY_COLUMNS,
    get_oi_archive_day_status,
    upsert_oi_archive_5m,
    upsert_oi_archive_days,
    upsert_oi_archive_hourly,
)
from analytics.store.schema import init_schema

VISION_BASE = "https://data.binance.vision/data/futures/um/daily/metrics"
DEFAULT_CACHE_DIR = Path(".cache/oi-archive")  # `.cache/` is gitignored

HOUR_MS = 3_600_000
FIVE_MIN_MS = 300_000
MAX_STALENESS_MS = 2 * FIVE_MIN_MS
SNAP_LAG_MS = FIVE_MIN_MS  # REST stamps T == archive label T-5min (measured, see above)
RETRY_MISSING_DAYS = 14
EARLIEST_DAY = date(2020, 9, 1)  # BTCUSDT's first archive file (measured)

EXPECTED_HEADER = [
    "create_time",
    "symbol",
    "sum_open_interest",
    "sum_open_interest_value",
    "count_toptrader_long_short_ratio",
    "sum_toptrader_long_short_ratio",
    "count_long_short_ratio",
    "sum_taker_long_short_vol_ratio",
]
_RENAME = {
    "sum_open_interest": "oi_contracts",
    "sum_open_interest_value": "oi_usd",
    "count_toptrader_long_short_ratio": "toptrader_count_ls_ratio",
    "sum_toptrader_long_short_ratio": "toptrader_sum_ls_ratio",
    "count_long_short_ratio": "count_ls_ratio",
    "sum_taker_long_short_vol_ratio": "taker_ls_vol_ratio",
}

# fetch(url) -> zip bytes, or None when the file does not exist (404). Anything else
# that goes wrong raises. Injected so tests never touch the network.
FetchFn = Callable[[str], bytes | None]


def archive_url(symbol: str, day: date) -> str:
    """URL of one symbol-day metrics zip."""
    return f"{VISION_BASE}/{symbol}/{symbol}-metrics-{day.isoformat()}.zip"


def http_fetch(
    url: str,
    *,
    retries: int = 5,
    backoff_s: float = 1.0,
    timeout_s: float = 30.0,
    sleep: Callable[[float], None] = time.sleep,
) -> bytes | None:
    """GET `url`; 404 -> None; 429/5xx/network errors retry with doubling backoff."""
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "buibui-oi-archive"}
            )
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                body: bytes = resp.read()
                return body
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            if exc.code != 429 and exc.code < 500:
                raise
            last = exc
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last = exc
        if attempt < retries:
            sleep(backoff_s * (2**attempt))
    assert last is not None
    raise last


def parse_metrics_zip(content: bytes, symbol: str) -> pd.DataFrame:
    """Parse one metrics zip into 5-minute rows (`FIVE_MIN_COLUMNS`), time-sorted.

    One row per timestamp (duplicates collapse, last wins), `timestamp` in Unix ms UTC.
    Raises ValueError when the header is not the known one: a changed archive schema
    must be loud, because every column below is read by position-free name.
    """
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        if len(names) != 1:
            raise ValueError(
                f"expected one CSV in the archive zip, got {zf.namelist()}"
            )
        raw = pd.read_csv(io.BytesIO(zf.read(names[0])), dtype=str)
    if list(raw.columns) != EXPECTED_HEADER:
        raise ValueError(f"unexpected metrics header: {list(raw.columns)}")
    ts = pd.to_datetime(raw["create_time"], format="%Y-%m-%d %H:%M:%S", errors="coerce")
    out = raw.drop(columns=["create_time", "symbol"]).rename(columns=_RENAME)
    for col in out.columns:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out["timestamp"] = (ts - pd.Timestamp("1970-01-01")) // pd.Timedelta(milliseconds=1)
    out = out.dropna(subset=["timestamp", "oi_usd", "oi_contracts"])
    out["timestamp"] = out["timestamp"].astype("int64")
    out["symbol"] = symbol
    out = (
        out.sort_values("timestamp", kind="stable")
        .drop_duplicates(subset="timestamp", keep="last")
        .reset_index(drop=True)
    )
    return out[FIVE_MIN_COLUMNS]


def resample_hourly(df5m: pd.DataFrame) -> pd.DataFrame:
    """Resample 5-minute rows to the hourly grain (rule in the module docstring).

    Call it once PER DAY FILE. Boundaries are limited to those whose target snapshot
    (`T - 5min`) lies inside the frame's own span, so nothing is borrowed from a
    neighbouring file and a hole stays a hole.
    """
    empty = pd.DataFrame({c: pd.Series(dtype="float64") for c in HOURLY_COLUMNS})
    if df5m.empty:
        return empty
    snaps = df5m.sort_values("timestamp").reset_index(drop=True)
    first = int(snaps["timestamp"].iloc[0])
    last = int(snaps["timestamp"].iloc[-1])
    lo = -(-(first + SNAP_LAG_MS) // HOUR_MS) * HOUR_MS
    hi = ((last + SNAP_LAG_MS) // HOUR_MS) * HOUR_MS
    if hi < lo:
        return empty
    bounds = pd.DataFrame(
        {"hour_ts": pd.Series(range(lo, hi + 1, HOUR_MS), dtype="int64")}
    )
    bounds["snap_target"] = bounds["hour_ts"] - SNAP_LAG_MS
    right = snaps[HOURLY_COLUMNS].rename(columns={"timestamp": "snap_ts"})
    merged = pd.merge_asof(
        bounds,
        right,
        left_on="snap_target",
        right_on="snap_ts",
        direction="backward",
        tolerance=MAX_STALENESS_MS,
    ).dropna(subset=["snap_ts"])
    merged = merged.drop(columns=["snap_ts", "snap_target"]).rename(
        columns={"hour_ts": "timestamp"}
    )
    merged["timestamp"] = merged["timestamp"].astype("int64")
    return merged[HOURLY_COLUMNS].reset_index(drop=True)


@dataclass
class _Counters:
    """Thread-safe download accounting."""

    bytes_downloaded: int = 0
    network_fetches: int = 0
    cache_hits: int = 0
    _lock: Lock = field(default_factory=Lock, repr=False)

    def add_download(self, n: int) -> None:
        with self._lock:
            self.bytes_downloaded += n
            self.network_fetches += 1

    def add_cache_hit(self) -> None:
        with self._lock:
            self.cache_hits += 1


@dataclass
class SymbolResult:
    """What one symbol's backfill did."""

    symbol: str
    days_planned: int = 0
    days_skipped: int = 0
    days_ok: int = 0
    days_missing: int = 0
    days_failed: int = 0
    rows_hourly: int = 0
    rows_5m: int = 0
    failed_days: list[str] = field(default_factory=list)


def _load_day(
    symbol: str,
    day: date,
    fetch: FetchFn,
    cache_dir: Path | None,
    counters: _Counters,
) -> pd.DataFrame | None:
    """Return the day's 5m rows, None when the archive has no file. Raises on failure."""
    cache_path = (
        cache_dir / symbol / f"{symbol}-metrics-{day.isoformat()}.zip"
        if cache_dir
        else None
    )
    content: bytes | None = None
    if cache_path is not None and cache_path.is_file():
        content = cache_path.read_bytes()
        counters.add_cache_hit()
        try:
            return parse_metrics_zip(content, symbol)
        except zipfile.BadZipFile:
            cache_path.unlink(missing_ok=True)  # truncated cache entry: refetch
            content = None
    content = fetch(archive_url(symbol, day))
    if content is None:
        return None
    counters.add_download(len(content))
    df = parse_metrics_zip(content, symbol)  # parse BEFORE caching: never cache junk
    if cache_path is not None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache_path.with_suffix(f".tmp{os.getpid()}")
        tmp.write_bytes(content)
        tmp.replace(cache_path)
    return df


def _days_between(start: date, end: date) -> list[date]:
    n = (end - start).days
    return [start + timedelta(days=i) for i in range(n + 1)] if n >= 0 else []


def _plan_days(
    ledger: dict[str, str],
    days: Sequence[date],
    *,
    today: date,
    force: bool,
    retry_missing: bool,
) -> list[date]:
    """Days that still need a request: unseen, forced, or a RECENT missing day."""
    todo: list[date] = []
    for d in days:
        status = ledger.get(d.isoformat())
        stale_missing = status == DAY_STATUS_MISSING and (
            retry_missing or (today - d).days <= RETRY_MISSING_DAYS
        )
        if force or status is None or stale_missing:
            todo.append(d)
    return todo


def backfill_symbol(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    start: date,
    end: date,
    *,
    fetch: FetchFn,
    cache_dir: Path | None,
    counters: _Counters | None = None,
    workers: int = 4,
    chunk_days: int = 60,
    keep_5m: bool = False,
    force: bool = False,
    retry_missing: bool = False,
    today: date | None = None,
    now_ms: int | None = None,
) -> SymbolResult:
    """Load `symbol`'s archive days in [start, end] into the archive tables."""
    counters = counters if counters is not None else _Counters()
    today = today if today is not None else datetime.now(UTC).date()
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    result = SymbolResult(symbol=symbol)
    all_days = _days_between(start, end)
    ledger = get_oi_archive_day_status(conn, symbol)
    todo = _plan_days(
        ledger, all_days, today=today, force=force, retry_missing=retry_missing
    )
    result.days_planned = len(all_days)
    result.days_skipped = len(all_days) - len(todo)

    def load(d: date) -> tuple[date, pd.DataFrame | None, Exception | None]:
        try:
            return d, _load_day(symbol, d, fetch, cache_dir, counters), None
        except Exception as exc:  # one bad day must not kill the symbol
            return d, None, exc

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for i in range(0, len(todo), chunk_days):
            chunk = todo[i : i + chunk_days]
            frames5: list[pd.DataFrame] = []
            frames1h: list[pd.DataFrame] = []
            ledger_rows: list[dict[str, object]] = []
            for d, df, err in pool.map(load, chunk):
                if err is not None:
                    result.days_failed += 1
                    result.failed_days.append(d.isoformat())
                    logging.warning("oi-archive %s %s failed: %s", symbol, d, err)
                    continue  # no ledger row: the next run retries it
                if df is None or df.empty:
                    result.days_missing += 1
                    ledger_rows.append(
                        _ledger_row(symbol, d, DAY_STATUS_MISSING, 0, now_ms)
                    )
                    continue
                result.days_ok += 1
                frames5.append(df)
                frames1h.append(resample_hourly(df))
                ledger_rows.append(
                    _ledger_row(symbol, d, DAY_STATUS_OK, len(df), now_ms)
                )
            _write_chunk(conn, frames5, frames1h, ledger_rows, keep_5m=keep_5m)
            result.rows_5m += sum(len(f) for f in frames5) if keep_5m else 0
            result.rows_hourly += sum(len(f) for f in frames1h)
    return result


def _ledger_row(
    symbol: str, d: date, status: str, n_rows: int, now_ms: int
) -> dict[str, object]:
    return {
        "symbol": symbol,
        "day": d.isoformat(),
        "status": status,
        "n_rows": n_rows,
        "fetched_at_ms": now_ms,
    }


def _write_chunk(
    conn: duckdb.DuckDBPyConnection,
    frames5: list[pd.DataFrame],
    frames1h: list[pd.DataFrame],
    ledger_rows: list[dict[str, object]],
    *,
    keep_5m: bool,
) -> None:
    """Write one chunk's data and its ledger rows in a single transaction."""
    if not ledger_rows:
        return
    conn.execute("BEGIN TRANSACTION")
    try:
        if frames1h:
            upsert_oi_archive_hourly(conn, pd.concat(frames1h, ignore_index=True))
        if keep_5m and frames5:
            upsert_oi_archive_5m(conn, pd.concat(frames5, ignore_index=True))
        upsert_oi_archive_days(conn, pd.DataFrame(ledger_rows))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def symbol_start(conn: duckdb.DuckDBPyConnection, symbol: str, since: date) -> date:
    """`since`, pushed forward to the day before the symbol listed when that is known.

    Skips years of certain 404s for newer symbols. The one-day margin absorbs the
    onboard timestamp's UTC date versus the first archive file.
    """
    row = conn.execute(
        "SELECT onboard_ms FROM symbol_lifecycle WHERE symbol = ?", [symbol]
    ).fetchone()
    if row is None or row[0] is None:
        return since
    listed = datetime.fromtimestamp(int(row[0]) / 1000, UTC).date() - timedelta(days=1)
    return max(since, listed)


@dataclass
class RunSummary:
    """Whole-run totals."""

    results: list[SymbolResult]
    bytes_downloaded: int
    network_fetches: int
    cache_hits: int
    seconds: float

    @property
    def failed_days(self) -> int:
        return sum(r.days_failed for r in self.results)


def backfill(
    conn: duckdb.DuckDBPyConnection,
    symbols: Sequence[str],
    *,
    since: date,
    until: date,
    fetch: FetchFn,
    cache_dir: Path | None,
    workers: int = 4,
    keep_5m: bool = False,
    force: bool = False,
    retry_missing: bool = False,
    today: date | None = None,
    now_ms: int | None = None,
) -> RunSummary:
    """Backfill every symbol sequentially (days within a symbol fetch concurrently)."""
    counters = _Counters()
    t0 = time.monotonic()
    results: list[SymbolResult] = []
    for symbol in symbols:
        start = symbol_start(conn, symbol, since)
        res = backfill_symbol(
            conn,
            symbol,
            start,
            until,
            fetch=fetch,
            cache_dir=cache_dir,
            counters=counters,
            workers=workers,
            keep_5m=keep_5m,
            force=force,
            retry_missing=retry_missing,
            today=today,
            now_ms=now_ms,
        )
        logging.info(
            "oi-archive %s: ok=%d missing=%d failed=%d skipped=%d hourly=%d",
            symbol,
            res.days_ok,
            res.days_missing,
            res.days_failed,
            res.days_skipped,
            res.rows_hourly,
        )
        results.append(res)
    return RunSummary(
        results=results,
        bytes_downloaded=counters.bytes_downloaded,
        network_fetches=counters.network_fetches,
        cache_hits=counters.cache_hits,
        seconds=time.monotonic() - t0,
    )


@dataclass(frozen=True)
class CoverageRow:
    """Per-symbol archive coverage."""

    symbol: str
    first_day: str | None
    last_day: str | None
    ok_days: int
    gap_days: int
    gap_ranges: tuple[tuple[str, str], ...]
    rows_hourly: int
    rows_5m: int


def coverage_report(
    conn: duckdb.DuckDBPyConnection,
    symbols: Sequence[str],
    *,
    source: str = ARCHIVE_SOURCE,
) -> list[CoverageRow]:
    """First/last loaded day and gap days per symbol.

    `gap_days` counts days between the first and last loaded day that are not loaded
    (recorded 404s plus any never-attempted day). Leading days before the first file
    are archive history that does not exist, not gaps.
    """
    out: list[CoverageRow] = []
    for symbol in symbols:
        ledger = get_oi_archive_day_status(conn, symbol, source=source)
        ok = sorted(d for d, s in ledger.items() if s == DAY_STATUS_OK)
        h = conn.execute(
            "SELECT COUNT(*) FROM open_interest_archive WHERE source = ? AND symbol = ?",
            [source, symbol],
        ).fetchone()
        m = conn.execute(
            "SELECT COUNT(*) FROM open_interest_archive_5m WHERE source = ? AND symbol = ?",
            [source, symbol],
        ).fetchone()
        rows_h = int(h[0]) if h else 0
        rows_m = int(m[0]) if m else 0
        if not ok:
            out.append(CoverageRow(symbol, None, None, 0, 0, (), rows_h, rows_m))
            continue
        first, last = date.fromisoformat(ok[0]), date.fromisoformat(ok[-1])
        have = set(ok)
        gaps = [d for d in _days_between(first, last) if d.isoformat() not in have]
        out.append(
            CoverageRow(
                symbol,
                ok[0],
                ok[-1],
                len(ok),
                len(gaps),
                _collapse_ranges(gaps),
                rows_h,
                rows_m,
            )
        )
    return out


def _collapse_ranges(days: Sequence[date]) -> tuple[tuple[str, str], ...]:
    ranges: list[tuple[str, str]] = []
    run_start: date | None = None
    prev: date | None = None
    for d in days:
        if run_start is None or prev is None or (d - prev).days != 1:
            if run_start is not None and prev is not None:
                ranges.append((run_start.isoformat(), prev.isoformat()))
            run_start = d
        prev = d
    if run_start is not None and prev is not None:
        ranges.append((run_start.isoformat(), prev.isoformat()))
    return tuple(ranges)


def format_coverage(rows: Sequence[CoverageRow], *, max_ranges: int = 3) -> str:
    """Fixed-width coverage table."""
    head = (
        f"{'symbol':<14}{'first':<12}{'last':<12}{'ok_days':>8}{'gap_days':>9}"
        f"{'hourly_rows':>13}{'5m_rows':>11}  gap_ranges"
    )
    lines = [head, "-" * len(head)]
    for r in rows:
        shown = ", ".join(
            a if a == b else f"{a}..{b}" for a, b in r.gap_ranges[:max_ranges]
        )
        extra = len(r.gap_ranges) - max_ranges
        if extra > 0:
            shown += f" (+{extra} more)"
        lines.append(
            f"{r.symbol:<14}{r.first_day or '-':<12}{r.last_day or '-':<12}"
            f"{r.ok_days:>8}{r.gap_days:>9}{r.rows_hourly:>13}{r.rows_5m:>11}  {shown or '-'}"
        )
    return "\n".join(lines)


def run_oi_archive(
    symbols: Sequence[str],
    *,
    db_path: Path,
    since: date = EARLIEST_DAY,
    until: date | None = None,
    cache_dir: Path | None = DEFAULT_CACHE_DIR,
    workers: int = 4,
    keep_5m: bool = False,
    force: bool = False,
    retry_missing: bool = False,
    report_only: bool = False,
    fetch: FetchFn = http_fetch,
) -> int:
    """CLI body: backfill (unless `report_only`), print the coverage table. Exit code."""
    until = until if until is not None else datetime.now(UTC).date() - timedelta(days=1)
    conn = connect_with_retry(db_path)
    try:
        init_schema(conn)
        summary: RunSummary | None = None
        if not report_only:
            summary = backfill(
                conn,
                symbols,
                since=since,
                until=until,
                fetch=fetch,
                cache_dir=cache_dir,
                workers=workers,
                keep_5m=keep_5m,
                force=force,
                retry_missing=retry_missing,
            )
        print(format_coverage(coverage_report(conn, symbols)))
        if summary is not None:
            print(
                f"\nwall={summary.seconds:.0f}s downloaded={summary.bytes_downloaded / 1e6:.1f}MB "
                f"fetches={summary.network_fetches} cache_hits={summary.cache_hits} "
                f"failed_days={summary.failed_days}"
            )
            for r in summary.results:
                if r.failed_days:
                    print(
                        f"FAILED {r.symbol}: {len(r.failed_days)} day(s), first {r.failed_days[0]}"
                    )
            return 1 if summary.failed_days else 0
        return 0
    finally:
        conn.close()
