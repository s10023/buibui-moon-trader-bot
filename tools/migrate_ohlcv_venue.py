"""One-shot ST60(b) migration: rebuild `ohlcv` as venue-keyed `ohlcv_all` + a view.

Run ONCE, by the operator, against a database that nothing else is writing to:

    poetry run python tools/migrate_ohlcv_venue.py

`analytics.db` is single-copy and gitignored, and the 15-minute signal-watch timer
owns it, so this refuses to run unless a local backup snapshot is less than 24h old.
Stop the timer, run `make buibui-backup`, then run this.

Every state check and every write happen inside ONE explicit transaction, so a
refusal and a mid-flight failure are the same thing to the database: nothing
changed. DuckDB's DDL is transactional (measured on 1.5.5), which is what makes the
window between "the legacy table is gone" and "the view exists" survivable.

What it does, in order: copy every `ohlcv` row into `ohlcv_all` stamped
`venue = 'binance'` (the legacy table only ever held Binance bars), prove the row
count is unchanged, drop the legacy table, create the `ohlcv` view over
`ohlcv_all`, and stamp the read preference order into `db_meta`.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from contextlib import suppress
from pathlib import Path

import duckdb

# Runnable as a bare script, not only through Poetry or a Make target: a bare
# `python3 tools/migrate_ohlcv_venue.py` puts `tools/` on sys.path rather than the
# repo root, and the `analytics.*` import below would then raise ModuleNotFoundError.
# CI invokes tools this way and the Make targets do not, which has shipped a red CI
# here before. `sanity_checks.py:52`, `post_branch_checks.py:45` and
# `distil_power.py:38` do the same.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analytics.store.venue import (  # noqa: E402
    DEFAULT_VENUE,
    OHLCV_COLUMNS,
    ohlcv_view_sql,
    set_read_venue_order,
)

DEFAULT_DB = Path("analytics.db")
_MAX_BACKUP_AGE_HOURS = 24.0

# Mirrors analytics/store/schema.py's `ohlcv_all` exactly. It is restated rather
# than imported because `init_schema` refuses to run against the very database this
# tool exists to fix -- calling it here would raise before creating anything.
_CREATE_OHLCV_ALL = """
    CREATE TABLE ohlcv_all (
        venue            TEXT   NOT NULL,
        symbol           TEXT   NOT NULL,
        timeframe        TEXT   NOT NULL,
        open_time        BIGINT NOT NULL,
        open             DOUBLE NOT NULL,
        high             DOUBLE NOT NULL,
        low              DOUBLE NOT NULL,
        close            DOUBLE NOT NULL,
        volume           DOUBLE NOT NULL,
        taker_buy_volume DOUBLE,
        PRIMARY KEY (venue, symbol, timeframe, open_time)
    )
"""

_CREATE_DB_META = """
    CREATE TABLE IF NOT EXISTS db_meta (
        key   TEXT NOT NULL,
        value TEXT NOT NULL,
        PRIMARY KEY (key)
    )
"""

# Columns are named on BOTH sides rather than `SELECT '<venue>', *`: a legacy
# database that gained `taker_buy_volume` through the old ALTER TABLE path can hold
# these columns in a different physical order, and a positional copy would then
# write each value into the wrong column with no error at all.
_COPY_SQL = (
    f"INSERT INTO ohlcv_all (venue, {OHLCV_COLUMNS}) "
    f"SELECT ?, {OHLCV_COLUMNS} FROM ohlcv"
)

_EXPECTED_LEGACY_COLUMNS = frozenset(c.strip() for c in OHLCV_COLUMNS.split(","))


class MigrationRefused(RuntimeError):
    """Base for every refusal. A refusal never modifies the database."""


class MissingDatabaseError(MigrationRefused):
    """No database file at the given path -- probably a typo."""


class AlreadyMigratedError(MigrationRefused):
    """`ohlcv` is not a TABLE, so there is nothing to migrate."""


class PopulatedVenueTableError(MigrationRefused):
    """A legacy `ohlcv` table AND a non-empty `ohlcv_all` -- nobody designed this."""


class LegacySchemaError(MigrationRefused):
    """The legacy `ohlcv` table does not carry exactly the nine expected columns."""


class StaleBackupError(MigrationRefused):
    """No local backup snapshot is fresh enough to migrate against."""


class RowCountMismatchError(RuntimeError):
    """The copy did not reproduce the legacy row count. Rolled back."""


def newest_backup_age_hours(root: Path) -> float | None:
    """Return the age in hours of the newest daily snapshot MANIFEST, or None.

    `deploy/backup-analytics.sh` writes MANIFEST.json last and only then renames the
    staging directory into place, so its presence is what makes a snapshot count as
    verified -- the same signal `deploy/backup-offsite.sh` and `daily_check.py` use.
    """
    manifests = list(root.glob("daily/*/MANIFEST.json"))
    if not manifests:
        return None
    newest = max(m.stat().st_mtime for m in manifests)
    return (time.time() - newest) / 3600.0


def _backup_root() -> Path:
    """Read the backup root at CALL time, so an env change is never missed."""
    return Path(
        os.environ.get("BUIBUI_BACKUP_ROOT", str(Path.home() / "backups" / "buibui"))
    )


def _require_fresh_backup() -> None:
    root = _backup_root()
    age = newest_backup_age_hours(root)
    if age is None or age > _MAX_BACKUP_AGE_HOURS:
        found = "none found" if age is None else f"newest is {age:.1f}h old"
        raise StaleBackupError(
            f"refusing to migrate: no verified backup under "
            f"{_MAX_BACKUP_AGE_HOURS:.0f}h in {root} ({found}). "
            "Stop the signal-watch timer, run `make buibui-backup`, then retry. "
            "`--force` skips this check."
        )


def _table_names(conn: duckdb.DuckDBPyConnection) -> set[str]:
    # duckdb_tables() lists TABLES only, never views -- which is the whole
    # discriminator here: after a real migration `ohlcv` is a view and never appears.
    return {
        row[0]
        for row in conn.execute("SELECT table_name FROM duckdb_tables()").fetchall()
    }


def _count(conn: duckdb.DuckDBPyConnection, relation: str) -> int:
    row = conn.execute(f"SELECT COUNT(*) FROM {relation}").fetchone()
    return int(row[0]) if row else 0


def _require_expected_legacy_columns(
    conn: duckdb.DuckDBPyConnection, db_path: Path
) -> None:
    """Refuse a legacy table whose columns are not exactly the expected nine.

    The copy NAMES its columns, so an unexpected extra one would be dropped on the
    floor with the row count still matching -- a silent data loss no check after the
    fact could see. A missing column would fail at the copy anyway; refusing here
    says which one, instead of a binder error.
    """
    columns = {
        row[0]
        for row in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'ohlcv'"
        ).fetchall()
    }
    if columns == set(_EXPECTED_LEGACY_COLUMNS):
        return
    extra = sorted(columns - _EXPECTED_LEGACY_COLUMNS)
    missing = sorted(_EXPECTED_LEGACY_COLUMNS - columns)
    raise LegacySchemaError(
        f"{db_path}: `ohlcv` does not carry the nine expected columns "
        f"(unexpected: {extra or 'none'}; missing: {missing or 'none'}). "
        "This tool copies named columns, so migrating would discard anything "
        "unexpected. Nothing has been modified."
    )


def migrate(db_path: Path, *, force: bool = False) -> tuple[int, int]:
    """Rebuild `ohlcv` as `ohlcv_all` + a Binance-pinned view.

    Returns `(rows_before, rows_after)`. Raises a `MigrationRefused` subclass, having
    changed nothing, on every state this tool is not designed for.
    """
    db_path = Path(db_path)
    if not db_path.exists():
        # Checked BEFORE the backup guard so a typo reads as a typo, and before the
        # connect because duckdb.connect() CREATES a database at any path it is
        # handed -- which would otherwise leave a stray empty file behind and then
        # report it as "already migrated".
        raise MissingDatabaseError(f"no database at {db_path} -- nothing to migrate.")

    if not force:
        _require_fresh_backup()

    conn = duckdb.connect(str(db_path))
    try:
        conn.execute("BEGIN TRANSACTION")
        try:
            before, after = _migrate_in_transaction(conn, db_path)
            conn.execute("COMMIT")
            return before, after
        except BaseException:
            # DuckDB rolls a transaction back on close anyway; doing it explicitly
            # means the refusal paths above are provably no-ops rather than
            # relying on that. A failed ROLLBACK must not mask the real error.
            with suppress(Exception):
                conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()


def _migrate_in_transaction(
    conn: duckdb.DuckDBPyConnection, db_path: Path
) -> tuple[int, int]:
    """The whole migration, including every state check, inside one transaction."""
    tables = _table_names(conn)

    if "ohlcv" not in tables:
        detail = (
            "`ohlcv` is already a view over `ohlcv_all`"
            if "ohlcv_all" in tables
            else "there is no `ohlcv` table and no `ohlcv_all` -- wrong database?"
        )
        raise AlreadyMigratedError(f"{db_path}: nothing to migrate ({detail}).")

    _require_expected_legacy_columns(conn, db_path)

    if "ohlcv_all" in tables:
        leftover = _count(conn, "ohlcv_all")
        if leftover:
            raise PopulatedVenueTableError(
                f"{db_path}: `ohlcv` is still a legacy TABLE but `ohlcv_all` already "
                f"holds {leftover:,} rows. Nobody designed that state and either "
                "side could be the real data, so nothing has been modified. "
                "Inspect both tables by hand."
            )
        # An EMPTY `ohlcv_all` is the EXPECTED production leftover: `init_schema`
        # creates it (IF NOT EXISTS) before the view statement that fails against a
        # legacy `ohlcv`, and the 15-minute timer has been doing that repeatedly.
        # Dropping an empty table destroys nothing, and the rollback restores it.
        conn.execute("DROP TABLE ohlcv_all")

    before = _count(conn, "ohlcv")
    conn.execute(_CREATE_OHLCV_ALL)
    conn.execute(_COPY_SQL, [DEFAULT_VENUE])
    after = _count(conn, "ohlcv_all")
    if after != before:
        raise RowCountMismatchError(
            f"row count changed during the copy ({before:,} -> {after:,}); "
            "rolled back, nothing was modified."
        )

    conn.execute("DROP TABLE ohlcv")
    conn.execute(ohlcv_view_sql([DEFAULT_VENUE]))
    conn.execute(_CREATE_DB_META)
    # Stamps the order AND rebuilds the view from it, so the two can never disagree.
    # Deliberately NOT wrapped in its own transaction: this one is the caller's.
    set_read_venue_order(conn, [DEFAULT_VENUE])
    return before, after


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="migrate_ohlcv_venue",
        description=(
            "Rebuild a pre-ST60(b) `ohlcv` table as venue-keyed `ohlcv_all` plus an "
            "`ohlcv` view. Refuses unless a backup snapshot is under 24h old."
        ),
    )
    parser.add_argument(
        "db",
        nargs="?",
        type=Path,
        default=DEFAULT_DB,
        help=f"database to migrate (default: {DEFAULT_DB})",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="skip the backup-freshness check (you are on your own)",
    )
    args = parser.parse_args()

    if args.force:
        print(
            "!! FORCE: the backup-freshness check is SKIPPED. There is no second "
            "copy of this database.",
            file=sys.stderr,
        )

    started = time.time()
    try:
        before, after = migrate(args.db, force=args.force)
    except MigrationRefused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1

    print(
        f"migrated {before:,} rows -> ohlcv_all "
        f"({after:,} under '{DEFAULT_VENUE}') in {time.time() - started:.1f}s"
    )

    conn = duckdb.connect(str(args.db), read_only=True)
    try:
        sample = conn.execute(
            "SELECT symbol, timeframe, open_time, close FROM ohlcv "
            "ORDER BY open_time DESC LIMIT 3"
        ).fetchall()
    finally:
        conn.close()
    print("read-back through the view:")
    for line in sample:
        print(f"  {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
