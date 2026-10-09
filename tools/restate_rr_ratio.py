"""Restate legacy `rr_ratio` rows onto the realised-ratio basis (SoT ST39).

The forward fix (`analytics.signal._common.realised_rr`) makes the ledger writer
store the R a row's own `tp_price` implies. Rows written before it stored the
*requested* `tp_r` instead, which a structural TP never fed — so without this
migration `rr_ratio` carries two bases permanently, exactly the trap `outcome_r`
already carries from its gross/net break at e5d92bb.

Measured on the live ledger before this ran: 29 of 5,304 rows disagreed by more
than 0.01R, unanimously with the stored ratio the larger one, largest gap
4.2251R. Only the 8 wins among them carry an `outcome_r` consequence — a loss
pays -1.0 and an expired row pays mark-to-market, neither of which reads
`rr_ratio` — and those 8 were over-credited by 9.1591R in total.

`_scan_forward` only revisits rows where `outcome IS NULL`, so resolved rows are
never re-scored by the resolver. This tool is the only thing that restates them,
which is why it is deliberate, dry-run by default, and committed rather than run
as a scratch statement.

Usage::

    PYTHONPATH=. poetry run python tools/restate_rr_ratio.py            # dry-run
    PYTHONPATH=. poetry run python tools/restate_rr_ratio.py --apply
"""

from __future__ import annotations

import argparse

import duckdb

from analytics.signal._common import realised_rr
from analytics.store import DEFAULT_DB_PATH

# Matches the tolerance the ST17 audit used to define the defect, so this tool's
# count reconciles against the filed 29 rather than sweeping in float noise from
# price arithmetic.
_TOL = 0.01


def restate_rr_ratio(
    conn: duckdb.DuckDBPyConnection, *, apply: bool = False
) -> dict[str, float]:
    """Restate `rr_ratio` (and a win's `outcome_r`) onto the realised basis.

    Read-only unless ``apply=True``; the returned counts are identical either
    way, so a dry-run reports exactly what the write would do. Idempotent —
    a restated row no longer disagrees and is not a candidate again.
    """
    rows = conn.execute(
        "SELECT signal_id, entry_price, sl_price, tp_price, rr_ratio, "
        "       outcome, outcome_r "
        "FROM signal_alert_outcomes "
        "WHERE entry_price IS NOT NULL AND sl_price IS NOT NULL "
        "  AND tp_price IS NOT NULL AND rr_ratio IS NOT NULL"
    ).fetchall()

    counts: dict[str, float] = {
        "scanned": len(rows),
        "rr_restated": 0,
        "outcome_r_restated": 0,
        "skipped_zero_risk": 0,
        "r_removed": 0.0,
    }
    updates: list[tuple[float, float | None, str]] = []

    for signal_id, entry, sl, tp, rr, outcome, outcome_r in rows:
        if abs(float(entry) - float(sl)) <= 0.0:
            # Implied ratio is undefined, so there is no basis to restate onto.
            counts["skipped_zero_risk"] += 1
            continue
        implied = realised_rr(
            entry=float(entry),
            sl_price=float(sl),
            tp_price=float(tp),
            fallback=float(rr),
        )
        if abs(implied - float(rr)) <= _TOL:
            continue

        counts["rr_restated"] += 1
        new_outcome_r = None if outcome_r is None else float(outcome_r)
        if outcome == "win" and outcome_r is not None:
            delta = implied - float(rr)
            new_outcome_r = float(outcome_r) + delta
            counts["outcome_r_restated"] += 1
            counts["r_removed"] += delta
        updates.append((implied, new_outcome_r, str(signal_id)))

    if apply and updates:
        conn.executemany(
            "UPDATE signal_alert_outcomes SET rr_ratio = ?, outcome_r = ? "
            "WHERE signal_id = ?",
            updates,
        )

    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the restatement. Omit for a dry-run (the default).",
    )
    args = parser.parse_args()

    conn = duckdb.connect(args.db_path, read_only=not args.apply)
    counts = restate_rr_ratio(conn, apply=args.apply)
    mode = "APPLIED" if args.apply else "DRY-RUN (nothing written)"
    print(f"ST39 rr_ratio restatement — {mode}")
    print(f"  scanned rows           : {int(counts['scanned'])}")
    print(f"  rr_ratio to restate    : {int(counts['rr_restated'])}")
    print(f"  of which wins (outcome_r): {int(counts['outcome_r_restated'])}")
    print(f"  skipped (zero risk)    : {int(counts['skipped_zero_risk'])}")
    print(f"  net R removed          : {counts['r_removed']:+.4f}")


if __name__ == "__main__":
    from utils.stdio import utf8_stdio

    utf8_stdio()
    main()
