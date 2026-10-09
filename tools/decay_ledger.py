"""Decay review Leg 3 — live-ledger half-month buckets, pinned (#837, SoT ST137).

Leg 3 was hand-rolled SQL every review, so its population definition was
unpinned by construction. Two drifts came out of that, and this tool closes both:

* **Timezone.** DuckDB's ``TimeZone`` defaults to the HOST's zone
  (``Asia/Kuala_Lumpur`` on the laptop), so an unqualified
  ``strftime(to_timestamp(ms / 1000))`` buckets in MYT. Reports run in different
  sessions disagreed by 19-25 rows per bucket for that reason alone. Every bucket
  here is computed ``AT TIME ZONE 'UTC'``.
* **Snapshot reuse.** The twice-daily backup reuses ``daily/<date>/``, so the
  snapshot a report read is overwritten hours later. ``--manifest`` reads the
  snapshot's ``captured_at_utc`` and applies it as a TIME CUT on
  ``outcome_filled_at_ms``, so a later run reproduces the earlier one from
  whatever that directory now holds (expect a row or two of gap).

Population: ``outcome IS NOT NULL``, keyed on ``outcome_filled_at_ms`` — never
``candle_ts_ms``. ``avg_r`` is the stored ``outcome_r`` as written, with no
cost-basis restatement (that is Leg 2's job), so compare it only against other
Leg 3 readings.

Read-only. Usage::

    python tools/decay_ledger.py --db <copy>/analytics.db [--manifest <dir>/MANIFEST.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import duckdb

BUCKET_SQL = """
SELECT
    strftime(ts, '%Y-%m') || CASE WHEN day(ts) <= 15 THEN '-a' ELSE '-b' END AS bucket,
    count(*) AS n,
    avg(outcome_r) AS avg_r
FROM (
    SELECT to_timestamp(outcome_filled_at_ms / 1000) AT TIME ZONE 'UTC' AS ts, outcome_r
    FROM signal_alert_outcomes
    WHERE outcome IS NOT NULL
      AND outcome_filled_at_ms IS NOT NULL
      AND outcome_filled_at_ms <= ?
)
GROUP BY bucket
ORDER BY bucket
"""

POOLED_SQL = """
SELECT count(*), avg(outcome_r)
FROM signal_alert_outcomes
WHERE outcome IS NOT NULL
  AND outcome_filled_at_ms IS NOT NULL
  AND outcome_filled_at_ms <= ?
"""

NO_CUT_MS = 2**62


@dataclass(frozen=True)
class Bucket:
    bucket: str
    n: int
    avg_r: float | None


def cutoff_ms_from_manifest(path: Path) -> int:
    """Return the snapshot's ``captured_at_utc`` as epoch milliseconds."""
    raw = json.loads(path.read_text(encoding="utf-8"))["captured_at_utc"]
    return int(datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp() * 1000)


def buckets(
    conn: duckdb.DuckDBPyConnection, cutoff_ms: int = NO_CUT_MS
) -> list[Bucket]:
    rows = conn.execute(BUCKET_SQL, [cutoff_ms]).fetchall()
    return [Bucket(str(b), int(n), None if r is None else float(r)) for b, n, r in rows]


def pooled(
    conn: duckdb.DuckDBPyConnection, cutoff_ms: int = NO_CUT_MS
) -> tuple[int, float | None]:
    n, r = conn.execute(POOLED_SQL, [cutoff_ms]).fetchone() or (0, None)
    return int(n), None if r is None else float(r)


def render(rows: list[Bucket], total: tuple[int, float | None], cutoff_ms: int) -> str:
    cut = "none" if cutoff_ms == NO_CUT_MS else str(cutoff_ms)
    lines = [f"Leg 3 — UTC half-month buckets (cut outcome_filled_at_ms <= {cut})"]
    lines.append(f"{'bucket':<10} {'n':>6} {'avg_r':>8}")
    for b in rows:
        avg = "-" if b.avg_r is None else f"{b.avg_r:+.4f}"
        lines.append(f"{b.bucket:<10} {b.n:>6} {avg:>8}")
    n, r = total
    lines.append(f"{'pooled':<10} {n:>6} {'-' if r is None else f'{r:+.4f}':>8}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--db", required=True, help="analytics.db COPY (never the live file)"
    )
    cut = ap.add_mutually_exclusive_group()
    cut.add_argument(
        "--manifest", type=Path, help="snapshot MANIFEST.json; cuts at captured_at_utc"
    )
    cut.add_argument("--cutoff-ms", type=int, help="explicit outcome_filled_at_ms cut")
    args = ap.parse_args(argv)

    if args.manifest is not None:
        cutoff = cutoff_ms_from_manifest(args.manifest)
    elif args.cutoff_ms is not None:
        cutoff = args.cutoff_ms
    else:
        cutoff = NO_CUT_MS
    conn = duckdb.connect(args.db, read_only=True)
    try:
        print(render(buckets(conn, cutoff), pooled(conn, cutoff), cutoff))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    # Repo root, for utils.stdio: a bare `python tools/<name>.py` puts only
    # tools/ on the path. Scoped to the entry so an import mutates nothing.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from utils.stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main())
