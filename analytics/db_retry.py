"""Retrying DuckDB connect for the scheduled write jobs.

DuckDB allows one writer. On this box several unattended jobs want the write
lock: the 15-minute signal-watch (which opens and closes ~10 connections per
run), the thrice-daily xsmom sync, the backup, and the web backend -- which
serves most requests read-only but still takes a write lock at startup and when
the stats router refreshes its cache.

Those jobs are `Persistent=true` systemd timers, so after the laptop resumes
from suspend several of them catch up *in the same second* rather than on their
staggered schedules. That is how `buibui-xsmom-daily` died on 2026-08-11: its
timer and signal-watch's both report a last-trigger of 09:27:34, and xsmom hit
`Conflicting lock` nine seconds later.

Waiting is the right response: every holder releases in seconds, and a sync that
starts a minute late is worth strictly more than one that does not run. Retrying
covers whichever job happens to hold the lock, which a fix aimed at any single
holder does not.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import duckdb

# Sized against the longest observed holder: a signal-watch run is 24-34s of
# repeated acquire/release. The schedule has hours of headroom, so the budget is
# set to clear a full run plus margin rather than to fail fast.
DEFAULT_ATTEMPTS = 6
_BACKOFF_S = (2.0, 5.0, 10.0, 15.0, 20.0)


def is_lock_conflict(exc: BaseException) -> bool:
    """True when `exc` is DuckDB refusing a second writer.

    Matched on the message because DuckDB raises a bare `IOException` for every
    I/O failure -- a missing directory and a busy lock are the same class. Only
    the lock case is worth retrying; a genuinely bad path would otherwise retry
    six times before failing with the same error a minute later.
    """
    return isinstance(exc, duckdb.IOException) and "Conflicting lock" in str(exc)


def connect_with_retry(
    db_path: Path | str,
    *,
    read_only: bool = False,
    attempts: int = DEFAULT_ATTEMPTS,
    sleep: object = time.sleep,
) -> duckdb.DuckDBPyConnection:
    """Open `db_path`, waiting out a conflicting writer.

    Raises the underlying `duckdb.IOException` once the budget is spent, so a
    lock that never clears still fails loudly rather than hanging the job.
    `sleep` is injected so tests do not pay the backoff.
    """
    if attempts < 1:
        raise ValueError(f"attempts must be >= 1, got {attempts}")

    sleep_fn = sleep if callable(sleep) else time.sleep
    last: duckdb.IOException | None = None

    for attempt in range(attempts):
        try:
            return duckdb.connect(str(db_path), read_only=read_only)
        except duckdb.IOException as e:
            if not is_lock_conflict(e):
                raise
            last = e
            if attempt == attempts - 1:
                break
            delay = _BACKOFF_S[min(attempt, len(_BACKOFF_S) - 1)]
            logging.warning(
                "analytics.db is locked by another job; retrying in %.0fs (%d/%d)",
                delay,
                attempt + 1,
                attempts - 1,
            )
            sleep_fn(delay)

    assert last is not None  # only reachable via the lock-conflict branch
    logging.error(
        "analytics.db still locked after %d attempts (~%.0fs) -- giving up",
        attempts,
        sum(_BACKOFF_S[: attempts - 1]),
    )
    raise last
