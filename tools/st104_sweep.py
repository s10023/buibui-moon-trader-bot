"""ST104 — run the pre-registered `detect_eqh_eql` retune arms and land them.

Brief: `docs/superpowers/specs/2026-08-29-st104-eqh-eql-retune-prereg.md`.
Three knobs, three trials, **each knob alone against the default baseline**; a
joint arm is a different family and is out of scope here.

Two subcommands, and the split between them is the point:

    run     --arm T1_tol --timeframe 15m --db <study.db>
    merge   --from <study.db> --db <production.db> [--runs-only]
    export  --from <study.db> --out <dir>

`run` executes one arm on one timeframe. `merge` copies the study rows into the
production DB afterwards. They are separate because `run_backtest_sweep` holds a
read-write DuckDB connection for its entire duration and DuckDB admits exactly
one writer: pointed at `analytics.db` the full 16-leg sweep would hold that lock
for roughly an hour, and the 15-minute signal-watch sync would fail for most of
it. That ledger is the OOS evidence base — gaps in it are a *biased* sample, not
merely a thinner one — so the compute runs against a throwaway copy and
production is locked only for the seconds `merge` takes.

Three further properties are load-bearing, each because the obvious spelling is
wrong:

- **The baseline arm carries EXPLICIT default `detector_params`, never `None`.**
  A `None` baseline produces a run_id with no `|dp:` suffix — byte-identical to
  the key production's own sweeps write — so saving it would *overwrite* the live
  rows for every `eqh_eql` cell it touched, and per ST86 that collision is
  unmeasurable from the DB afterwards because the axis was never stored. Passing
  the defaults explicitly is behaviourally identical (`detect_signals_for_strategy`
  forwards them as keyword args) while landing the row in its own namespace.
  `test_baseline_arm_matches_detector_defaults` pins the two together, so an edit
  to the detector's signature defaults fails here rather than silently turning
  the control into a fourth treatment.

- **Selection is by stored `detector_params`, not by `sweep_id`.** Each
  invocation mints its own `sweep_id`, but `sweep_id` is not part of the run_id
  key, so re-running an arm upserts in place rather than duplicating. The arm's
  JSON *is* the run_id namespace, which makes "score only the rows this sweep
  wrote" (spec P2) an exact predicate rather than a heuristic — and it sidesteps
  the ~5.29x `backtest_trades` duplication factor by construction.

- **`merge --runs-only` is the default landing shape, and it is a size decision.**
  The three-arm study writes ~810k trade rows against production's ~865k, so
  copying them all would nearly double `backtest_trades` and every ad-hoc
  aggregate that forgets to join through `run_id` would silently include the
  study. The 312 aggregate rows carry the headline numbers and are namespaced, so
  they cost nothing and confuse nothing; per-trade rescoring is served by
  `export`, which writes the scoring columns to a compact parquet instead.

- **`merge` names every column on both sides.** `INSERT ... SELECT *` maps by
  POSITION, and `backtest_runs` has no single column order: `long_total_r` /
  `short_total_r` / `volume_suppress` are created inline by `init_schema` while
  `adr_suppress_threshold` / `recovery_factor` arrive through the ALTER
  migration, so a database predating the CREATE orders them differently from a
  fresh one. A positional copy between two such files reads a threshold out of a
  total_r column five positions away, silently.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, Any

# A bare `python3 tools/st104_sweep.py` puts `tools/` on sys.path rather than the
# repo root, so the `analytics.*` imports below die with ModuleNotFoundError. Per
# ST101 the guarantee is `test_bare_invocation_works`, not this line.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analytics.backtest_config import BacktestSweepConfig  # noqa: E402
from analytics.backtest_runner import run_backtest_sweep  # noqa: E402
from analytics.store._common import DEFAULT_DB_PATH  # noqa: E402

if TYPE_CHECKING:
    import duckdb

STRATEGY = "eqh_eql"

# Earliest bar in the OHLCV store; the study runs on full available history
# rather than a fitted window. `backtest_trades` held only 2025-09 -> 2026-07
# when the multi-regime study asked it for legs, which is how star ratings, the
# decay review and `min_avg_r` all came to be fitted inside one 10-month window.
SINCE = "2019-09-01"

# Cost and stop geometry, read from the shared production base rather than
# restated here. `BacktestSweepConfig` defaults `fee_pct`, `slippage_pct` and
# `min_sl_pct` to ZERO, and a first pass of this study took those defaults: the
# result was a control that CLEARED the three-leg gate on the 15m short cell the
# repo files as a loser, because a gross reading hides a drag that is
# `2(fee+slip)*entry/risk` and therefore inversely proportional to stop width. It
# also let structural stops of under 0.1% through, each paying over 1.4R of drag,
# which dominated every mean. A control that does not reproduce production's
# geometry is not testing the pre-registered question.
_PARAMS_TOML = Path(__file__).resolve().parent.parent / "config/strategy_params.toml"


def production_costs() -> dict[str, float]:
    """`fee_pct`, `slippage_pct` and `min_sl_pct` as production sets them."""
    with _PARAMS_TOML.open("rb") as fh:
        data = tomllib.load(fh)
    return {
        "fee_pct": float(data["fee_pct"]),
        # `slippage_bps` lives under [backtest]; the other two are top-level.
        "slippage_pct": float(data["backtest"]["slippage_bps"]) / 10_000.0,
        "min_sl_pct": float(data["min_sl_pct"]),
    }


# The four arms, exactly as pre-registered. `baseline` repeats the detector's own
# signature defaults; the guard test asserts that, so this dict cannot drift away
# from the control it claims to be.
ARMS: dict[str, dict[str, float | int]] = {
    "baseline": {"lookback": 50, "tolerance_pct": 0.003, "swing_n": 5},
    # T1 — measured cluster spans are 0.064% (BTC EQL) / 0.04% (ETH poor lows);
    # 0.075% is the observed ceiling plus margin. 4x tighter.
    "T1_tol": {"lookback": 50, "tolerance_pct": 0.00075, "swing_n": 5},
    # T2 — the levels actually traded sat 65 / 125 / 190 / >=387 bars back.
    "T2_look": {"lookback": 400, "tolerance_pct": 0.003, "swing_n": 5},
    # T3 — a 4-touch shelf has no two spaced pivots at n=5. The closest scalar to
    # a shelf detector, which would be a new construction and is out of scope.
    "T3_swing": {"lookback": 50, "tolerance_pct": 0.003, "swing_n": 2},
}

TIMEFRAMES = ("15m", "1h", "4h", "1d")

_STUDY_TABLES = ("backtest_runs", "backtest_trades")


def arm_params_json(arm: str) -> str:
    """The stored `detector_params` text for `arm`.

    Mirrors `upsert_backtest_run`'s own serialisation (sorted keys) so this is
    the exact string to select rows on when scoring.
    """
    params = ARMS[arm]
    return json.dumps({k: params[k] for k in sorted(params)})


def all_arm_params_json() -> list[str]:
    return [arm_params_json(arm) for arm in sorted(ARMS)]


def symbols_for(conn: duckdb.DuckDBPyConnection, timeframe: str) -> list[str]:
    """Every symbol the store actually holds bars for at `timeframe`.

    Read from the data rather than from `universe.toml` because the two do not
    agree: 15m exists for the three majors only, while 1h/4h/1d carry all 25. A
    universe-shaped symbol list would silently backtest nothing on 15m — the
    study's primary timeframe.
    """
    rows = conn.execute(
        "SELECT DISTINCT symbol FROM ohlcv WHERE timeframe = ? ORDER BY symbol",
        [timeframe],
    ).fetchall()
    return [r[0] for r in rows]


def build_config(arm: str, timeframe: str, symbols: list[str]) -> BacktestSweepConfig:
    """A programmatic sweep config scoped to one arm on one timeframe.

    Deliberately built without a TOML: `load_backtest_config` would attach the
    live `[bias]` gates and per-strategy overrides, which vary by config file and
    would confound the one axis under test. Every arm therefore sees identical
    engine settings and differs only in `detector_params`.
    """
    costs = production_costs()
    return BacktestSweepConfig(
        symbols=symbols,
        timeframes=[timeframe],
        strategies=[STRATEGY],
        since=SINCE,
        save_results=True,
        fee_pct=costs["fee_pct"],
        slippage_pct=costs["slippage_pct"],
        min_sl_pct=costs["min_sl_pct"],
        # Table-formatting floor only (`format_*` at the end of the sweep); the
        # save path does not consult it, so no cell is dropped from the study.
        min_trades=1,
        detector_params=ARMS[arm],
    )


def saved_row_summary(
    conn: duckdb.DuckDBPyConnection, arm: str, timeframe: str
) -> list[tuple[str, int, int]]:
    """(run_id, closed_trades, total_signals) for the rows this arm owns."""
    rows = conn.execute(
        "SELECT run_id, closed_trades, total_signals FROM backtest_runs "
        "WHERE strategy = ? AND timeframe = ? AND detector_params = ? "
        "ORDER BY run_id",
        [STRATEGY, timeframe, arm_params_json(arm)],
    ).fetchall()
    return [(str(r[0]), int(r[1]), int(r[2])) for r in rows]


def table_columns(conn: duckdb.DuckDBPyConnection, table: str) -> list[str]:
    """Column names of `table` in the CONNECTED database, in declared order.

    Scoped to `current_database()` because `merge` runs with the study DB
    attached and both catalogs hold a `backtest_runs`: an unscoped
    `information_schema` query returns every column twice, which surfaces as a
    "Duplicate column name" binder error rather than as a wrong answer.
    """
    rows = conn.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_catalog = current_database() AND table_name = ? "
        "ORDER BY ordinal_position",
        [table],
    ).fetchall()
    return [str(r[0]) for r in rows]


def _count(conn: duckdb.DuckDBPyConnection, sql: str, params: list[Any]) -> int:
    row = conn.execute(sql, params).fetchone()
    if row is None:  # pragma: no cover - COUNT(*) always returns a row
        raise RuntimeError(f"no row from: {sql}")
    return int(row[0])


def merge_study_rows(
    conn: duckdb.DuckDBPyConnection,
    study_db: Path,
    arms_json: list[str],
    *,
    runs_only: bool = True,
) -> dict[str, int]:
    """Copy this study's rows out of `study_db` into the connected production DB.

    `conn` must be read-write. Columns are named from the DESTINATION schema on
    both sides of every INSERT (see the module docstring); a column present in
    production but missing from the study file raises rather than shifting the
    row.
    """
    # ATTACH takes no bind parameter, so the path is inlined; single quotes are
    # doubled rather than trusted, since the path arrives from argv.
    literal = str(study_db).replace("'", "''")
    conn.execute(f"ATTACH '{literal}' AS study (READ_ONLY)")
    try:
        tables = ("backtest_runs",) if runs_only else _STUDY_TABLES
        for table in tables:
            dest = table_columns(conn, table)
            src = {
                r[0]
                for r in conn.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_catalog = 'study' AND table_name = ?",
                    [table],
                ).fetchall()
            }
            missing = [c for c in dest if c not in src]
            if missing:
                raise RuntimeError(
                    f"study DB {study_db} is missing {table} columns {missing} — "
                    "refusing to copy, a positional fallback would shift the row"
                )

        placeholders = ", ".join("?" for _ in arms_json)
        counts: dict[str, int] = {}
        conn.execute("BEGIN TRANSACTION")
        try:
            run_cols = table_columns(conn, "backtest_runs")
            trade_cols = table_columns(conn, "backtest_trades")
            run_list = ", ".join(run_cols)
            trade_list = ", ".join(trade_cols)

            # Delete-then-insert so a re-merge is idempotent rather than a PK error.
            conn.execute(
                "DELETE FROM backtest_trades WHERE run_id IN "
                "(SELECT run_id FROM backtest_runs "
                f" WHERE strategy = ? AND detector_params IN ({placeholders}))",
                [STRATEGY, *arms_json],
            )
            conn.execute(
                "DELETE FROM backtest_runs WHERE strategy = ? "
                f"AND detector_params IN ({placeholders})",
                [STRATEGY, *arms_json],
            )
            conn.execute(
                f"INSERT INTO backtest_runs ({run_list}) "
                f"SELECT {run_list} FROM study.backtest_runs "
                f"WHERE strategy = ? AND detector_params IN ({placeholders})",
                [STRATEGY, *arms_json],
            )
            counts["backtest_runs"] = _count(
                conn,
                "SELECT COUNT(*) FROM backtest_runs WHERE strategy = ? "
                f"AND detector_params IN ({placeholders})",
                [STRATEGY, *arms_json],
            )
            if not runs_only:
                conn.execute(
                    f"INSERT INTO backtest_trades ({trade_list}) "
                    f"SELECT t.{', t.'.join(trade_cols)} "
                    "FROM study.backtest_trades t "
                    "JOIN study.backtest_runs r ON r.run_id = t.run_id "
                    f"WHERE r.strategy = ? AND r.detector_params IN ({placeholders})",
                    [STRATEGY, *arms_json],
                )
            counts["backtest_trades"] = _count(
                conn,
                "SELECT COUNT(*) FROM backtest_trades t JOIN backtest_runs r "
                "ON r.run_id = t.run_id WHERE r.strategy = ? "
                f"AND r.detector_params IN ({placeholders})",
                [STRATEGY, *arms_json],
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return counts
    finally:
        conn.execute("DETACH study")


def _cmd_run(args: argparse.Namespace) -> int:
    import duckdb  # noqa: PLC0415 - deferred so --help works without the venv

    with duckdb.connect(str(args.db), read_only=True) as conn:
        symbols = symbols_for(conn, args.timeframe)

    print(f"ST104 arm={args.arm} tf={args.timeframe} db={args.db}")
    print(f"  detector_params : {arm_params_json(args.arm)}")
    print(f"  symbols ({len(symbols):>2})   : {', '.join(symbols)}")
    print(f"  window          : since {SINCE}")
    print(f"  costs           : {production_costs()}")
    if args.dry_run:
        print("  DRY RUN — nothing written.")
        return 0

    t0 = time.time()
    run_backtest_sweep(build_config(args.arm, args.timeframe, symbols), args.db)
    elapsed = time.time() - t0

    with duckdb.connect(str(args.db), read_only=True) as conn:
        saved = saved_row_summary(conn, args.arm, args.timeframe)
    trades = sum(t for _, t, _ in saved)
    signals = sum(s for _, _, s in saved)
    print(
        f"\nST104 arm={args.arm} tf={args.timeframe} DONE in {elapsed:.1f}s — "
        f"{len(saved)} run rows, {signals} signals, {trades} closed trades"
    )
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    """Write the scoring columns for every study row to one compact parquet.

    The study DB is a 432MB copy of `analytics.db` living in a scratch dir, so it
    does not survive the session; this is the durable artifact behind the audit's
    per-trade numbers, small enough to sit in the backed-up ledger tree.
    """
    import duckdb  # noqa: PLC0415 - deferred so --help works without the venv

    from analytics.store.backtest_trades import (  # noqa: PLC0415
        load_backtest_trades,
    )

    arms = all_arm_params_json()
    out = args.out / "st104-study-trades.parquet"
    with duckdb.connect(str(args.source), read_only=True) as conn:
        run_ids = [
            str(r[0])
            for r in conn.execute(
                "SELECT run_id FROM backtest_runs WHERE strategy = ? AND "
                f"detector_params IN ({', '.join('?' for _ in arms)})",
                [STRATEGY, *arms],
            ).fetchall()
        ]
        trades = load_backtest_trades(
            conn,
            columns=(
                "symbol",
                "timeframe",
                "direction",
                "entry_time",
                "exit_time",
                "entry_price",
                "sl_price",
                "tp_price",
                "pnl_r",
            ),
            run_ids=run_ids,
            run_columns=("detector_params", "fee_pct"),
            not_null=(),
            dedup_on=None,
        )
    cols = ["detector_params", *(c for c in trades.columns if c != "detector_params")]
    trades[cols].to_parquet(out, compression="zstd", index=False)
    print(f"ST104 export -> {out} ({out.stat().st_size / 1e6:.1f} MB)")
    return 0


def _cmd_merge(args: argparse.Namespace) -> int:
    import duckdb  # noqa: PLC0415 - deferred so --help works without the venv

    arms_json = all_arm_params_json()
    shape = "run rows only" if args.runs_only else "runs + trades"
    print(f"ST104 merge ({shape}) {args.source} -> {args.db}")
    with duckdb.connect(str(args.db)) as conn:
        counts = merge_study_rows(
            conn, args.source, arms_json, runs_only=args.runs_only
        )
    for table, n in counts.items():
        print(f"  {table}: {n} study rows now in production")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="run one arm on one timeframe")
    run.add_argument("--arm", required=True, choices=sorted(ARMS))
    run.add_argument("--timeframe", required=True, choices=TIMEFRAMES)
    run.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    run.add_argument(
        "--dry-run",
        action="store_true",
        help="print the resolved plan and exit without touching the DB",
    )
    run.set_defaults(func=_cmd_run)

    merge = sub.add_parser("merge", help="copy study rows into the production DB")
    merge.add_argument(
        "--from", dest="source", type=Path, required=True, help="the study DB"
    )
    merge.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    merge.add_argument(
        "--all-trades",
        dest="runs_only",
        action="store_false",
        help="also copy the ~810k per-trade rows (see the module docstring)",
    )
    merge.set_defaults(func=_cmd_merge, runs_only=True)

    export = sub.add_parser("export", help="write scoring columns to parquet")
    export.add_argument("--from", dest="source", type=Path, required=True)
    export.add_argument("--out", type=Path, required=True)
    export.set_defaults(func=_cmd_export)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover - CLI entry
    from utils.stdio import utf8_stdio

    utf8_stdio()
    raise SystemExit(main())
