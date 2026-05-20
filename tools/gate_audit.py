"""tools/gate_audit.py — Phase A engine-side gate audit.

Reads `backtest_trades` from DuckDB, applies a candidate gate change to each
existing trade (re-tag kept vs would-be-suppressed), and emits a four-grain
decision table for systematic Phase A reviews.

Replay-only — does NOT regenerate trades. For gates that PREVENT trades from
being generated (e.g. `strategy_timeframes` TF allowlist), the audit must be
run against a permissive baseline (e.g. signal_watch_all.toml) and the mask
applied here. Documented inline per handler.

Usage:
  PYTHONPATH=. poetry run python tools/gate_audit.py volume-suppress
  PYTHONPATH=. poetry run python tools/gate_audit.py volume-suppress --strategy bos
  PYTHONPATH=. poetry run python tools/gate_audit.py adr-exempt --config config/signal_watch.toml
  PYTHONPATH=. poetry run python tools/gate_audit.py day-filter --day-filter tue_thu

Decision rule per cell (n_supp >= --min-n):
  supp_avg_r <= -0.05R  → ENABLE this gate at this scope
  supp_avg_r >= +0.05R  → DISABLE (gate kills winners)
  else                  → insufficient evidence

ASSUMPTIONS (verify before relying on output):
  * `backtest_trades` schema includes: symbol, tf, strategy, direction,
    signal_time (ms), entry_price, sl_price, exit_price, outcome, pnl_r,
    low_volume, volume_spike, run_id.
  * Most recent run per (config, since) is canonical — use --run-id to pin.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from analytics.backtest_config import load_backtest_config  # noqa: E402
from analytics.signal.gates import _filter_signals_by_adr  # noqa: E402
from analytics.signal_config import _day_filter_to_weekdays  # noqa: E402
from analytics.store import DEFAULT_DB_PATH  # noqa: E402

# Day-filter modes recognised by `_day_filter_to_weekdays`. Kept here as an
# explicit allowlist so argparse can validate `--day-filter` and we can raise
# loudly on unknown candidates (the canonical helper silently returns None).
DAY_FILTER_MODES: tuple[str, ...] = (
    "off",
    "weekdays",
    "mon_fri",
    "no_monfi",
    "tue_thu",
    "weekend",
)

OhlcvLoader = Callable[[str, str], pd.DataFrame]

# ---------------------------------------------------------------------------
# Gate registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GateAudit:
    """One auditable toggle. `flag_suppressed` returns a Series of bools
    indicating which trades WOULD be suppressed if the candidate change were
    applied. The semantic of "candidate change" is gate-specific:

      volume-suppress   → flip volume_suppress=true everywhere → mask trades
                          with low_volume=True for strategies that currently
                          have volume_suppress=false.
      adr-exempt        → remove all adr_exempt flags → mask trades where
                          ADR-consumed at signal time crosses bias.adr_suppress_threshold
                          in the chasing direction, for strategies currently exempt.
      day-filter        → apply the candidate day_filter to trades that fired
                          on filtered days.
    """

    name: str
    description: str
    flag_suppressed: Callable[[pd.DataFrame, dict[str, Any]], pd.Series]


def _gate_volume_suppress(df: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    """Mask: low-volume trades on strategies that currently have
    volume_suppress=false in the active config. If we flip them all to True,
    these are the trades that would have been dropped.

    NULL `low_volume` (pre-PR#371 rows) is treated as False so legacy trades
    never get flagged as suppressed under an unknown-volume regime.
    """
    current_off = params["volume_suppress_off"]  # set[str] of strategy names
    flag = df["low_volume"].fillna(False).astype(bool)
    mask = flag & df["strategy"].isin(current_off)
    return mask


def _gate_adr_exempt(df: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    """Mask: trades on currently-exempt strategies where ADR-consumed at signal
    time exceeded threshold IN THE CHASING DIRECTION.

    Reuses live `_filter_signals_by_adr` verbatim per (symbol, tf) group; the
    suppressed set is the complement of its return value. OHLCV is fetched via
    `params["ohlcv_loader"]` so tests can inject a stub.
    """
    exempt: set[str] = params["exempt"]
    threshold: float = params["threshold"]
    loader: OhlcvLoader = params["ohlcv_loader"]

    suppressed = pd.Series(False, index=df.index)
    if not exempt:
        return suppressed
    cand = df[df["strategy"].isin(exempt)]
    if cand.empty:
        return suppressed

    for (symbol, tf), sub in cand.groupby(["symbol", "tf"], dropna=False):
        ohlcv = loader(str(symbol), str(tf))
        if ohlcv.empty:
            continue
        # _filter_signals_by_adr keys on (open_time, direction); we tag _idx to
        # map survivors back to the original trade-frame index.
        signals_df = pd.DataFrame(
            {
                "open_time": sub["signal_time"].astype(int).to_numpy(),
                "direction": sub["direction"].to_numpy(),
                "_idx": sub.index.to_numpy(),
            }
        )
        kept = _filter_signals_by_adr(ohlcv, signals_df, threshold)
        kept_idx = set(kept["_idx"].tolist())
        dropped = [i for i in sub.index if i not in kept_idx]
        suppressed.loc[dropped] = True
    return suppressed


def _gate_day_filter(df: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    """Mask: trades whose signal day-of-week would be suppressed by the
    candidate day_filter value. Defers to `_day_filter_to_weekdays` for the
    canonical mode → allowed-weekdays mapping so this stays in sync with the
    live signal-watch and backtest scope.

    Recognised modes: see `DAY_FILTER_MODES`. `off` keeps everything; every
    other mode keeps only the weekdays returned by the canonical helper.
    """
    candidate = params["candidate"]
    if candidate not in DAY_FILTER_MODES:
        raise ValueError(f"Unknown candidate day_filter: {candidate}")
    if candidate == "off":
        return pd.Series(False, index=df.index)
    allowed = _day_filter_to_weekdays(candidate)
    assert allowed is not None  # guaranteed by DAY_FILTER_MODES check above
    dow = pd.to_datetime(df["signal_time"], unit="ms", utc=True).dt.dayofweek
    return ~dow.isin(allowed)


GATE_REGISTRY: dict[str, GateAudit] = {
    "volume-suppress": GateAudit(
        "volume-suppress",
        "Re-tag low-volume trades on strategies currently NOT volume_suppressed.",
        _gate_volume_suppress,
    ),
    "adr-exempt": GateAudit(
        "adr-exempt",
        "Re-tag chasing-direction trades on currently-exempt strategies.",
        _gate_adr_exempt,
    ),
    "day-filter": GateAudit(
        "day-filter",
        "Re-tag trades whose day-of-week the candidate day_filter would drop.",
        _gate_day_filter,
    ),
    # strategy-timeframes: requires permissive-baseline run (e.g. signal_watch_all.toml).
    # Mask = trades whose (strategy, tf) is NOT in the candidate config's
    # strategy_timeframes allowlist. Deferred until baseline run protocol decided.
}


# ---------------------------------------------------------------------------
# Data loader + verdict
# ---------------------------------------------------------------------------


def load_trades(
    db_path: Path,
    run_ids: list[str] | None,
    since_ms: int | None,
) -> pd.DataFrame:
    """Return the trade frame the audit replays against.

    Scoped to `run_ids` when supplied (list of UUID strings from
    `backtest_runs.run_id`); otherwise reads every row. See ASSUMPTIONS at top
    of file for schema.
    """
    where: list[str] = []
    params: list[object] = []
    if run_ids is not None:
        if not run_ids:
            # Empty list = "scope matches nothing" — return an empty frame with
            # the right columns rather than an unscoped read of every row.
            placeholder = "?"
            where.append(f"run_id = {placeholder}")
            params.append("__no_match__")
        else:
            placeholders = ", ".join(["?"] * len(run_ids))
            where.append(f"run_id IN ({placeholders})")
            params.extend(run_ids)
    if since_ms is not None:
        where.append("signal_time >= ?")
        params.append(since_ms)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    sql = f"""
        SELECT symbol, timeframe AS tf, strategy, direction, signal_time,
               entry_price, sl_price, exit_price, outcome, pnl_r,
               low_volume, volume_spike, run_id
        FROM backtest_trades
        {where_sql}
    """
    with duckdb.connect(str(db_path), read_only=True) as conn:
        return conn.execute(sql, params).fetchdf()


def _resolve_config_run_ids(db_path: Path, config_path: Path) -> list[str]:
    """Return `run_id`s belonging to the most recent sweep matching the
    config's `day_filter`.

    Path C (PR #372) made `day_filter` values disjoint across the three
    production configs (`tue_thu` / `mon_fri` / `weekend`), so `day_filter`
    alone is a sufficient scoping key. If multiple sweeps share the same
    day_filter, the most recent one (by `run_at_ms`) wins — matches "audit the
    latest run of this config" intent.

    Single-run backtests (no `--sweep` flag) write rows with `sweep_id IS NULL`.
    These are excluded so a stray ad-hoc backtest can never shadow a real sweep.
    """
    cfg = load_backtest_config(config_path)
    day_filter = cfg.day_filter or "off"
    with duckdb.connect(str(db_path), read_only=True) as conn:
        sweep_row = conn.execute(
            "SELECT sweep_id FROM backtest_runs "
            "WHERE day_filter = ? AND sweep_id IS NOT NULL "
            "ORDER BY run_at_ms DESC LIMIT 1",
            [day_filter],
        ).fetchone()
        if sweep_row is None:
            return []
        sweep_id = sweep_row[0]
        rows = conn.execute(
            "SELECT run_id FROM backtest_runs WHERE sweep_id = ?",
            [sweep_id],
        ).fetchall()
    return [r[0] for r in rows]


def build_audit_table(
    df: pd.DataFrame,
    gate: GateAudit,
    params: dict[str, Any],
    grain: list[str],
    min_n: int,
    threshold: float,
) -> pd.DataFrame:
    """Return one row per `grain` group with n / avg_r / verdict columns.

    grain examples:
      ["strategy"]                          → coarsest
      ["strategy", "tf"]                    → standard
      ["strategy", "tf", "direction"]       → directional
      ["strategy", "tf", "direction", "symbol"]  → finest
    """
    df = df.copy()
    df["_suppressed"] = gate.flag_suppressed(df, params)
    df["_pnl"] = pd.to_numeric(df["pnl_r"], errors="coerce")
    df = df.dropna(subset=["_pnl"])

    rows: list[dict[str, Any]] = []
    for key, sub in df.groupby(grain, dropna=False):
        key_tuple = key if isinstance(key, tuple) else (key,)
        kept = sub[~sub["_suppressed"]]
        supp = sub[sub["_suppressed"]]
        n_kept = len(kept)
        n_supp = len(supp)
        kept_avg = kept["_pnl"].mean() if n_kept else float("nan")
        supp_avg = supp["_pnl"].mean() if n_supp else float("nan")
        dR = (n_kept * kept_avg if n_kept else 0.0) - (
            (n_kept + n_supp) * sub["_pnl"].mean() if n_kept + n_supp else 0.0
        )
        if n_supp >= min_n and supp_avg <= -threshold:
            verdict = "ENABLE"
        elif n_supp >= min_n and supp_avg >= threshold:
            verdict = "DISABLE"
        else:
            verdict = "INSUFFICIENT"
        rows.append(
            {
                **dict(zip(grain, key_tuple, strict=True)),
                "n_kept": n_kept,
                "kept_avg_r": round(kept_avg, 4) if n_kept else None,
                "n_supp": n_supp,
                "supp_avg_r": round(supp_avg, 4) if n_supp else None,
                "delta_R": round(dR, 2),
                "verdict": verdict,
            }
        )
    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.sort_values(grain).reset_index(drop=True)
    return out


# ---------------------------------------------------------------------------
# Parameter resolvers — translate current TOML into the flag sets each gate
# handler needs. Keeps gate handlers pure functions of (df, params).
# ---------------------------------------------------------------------------


def _make_db_ohlcv_loader(db_path: Path) -> OhlcvLoader:
    """Default loader: read-only DuckDB pull of `ohlcv` per (symbol, timeframe),
    cached per-key inside the closure so repeated lookups during one audit run
    hit memory after the first call.
    """
    cache: dict[tuple[str, str], pd.DataFrame] = {}

    def load(symbol: str, tf: str) -> pd.DataFrame:
        key = (symbol, tf)
        if key not in cache:
            with duckdb.connect(str(db_path), read_only=True) as conn:
                cache[key] = conn.execute(
                    "SELECT open_time, open, high, low, close FROM ohlcv "
                    "WHERE symbol = ? AND timeframe = ? "
                    "ORDER BY open_time",
                    [symbol, tf],
                ).fetchdf()
        return cache[key]

    return load


def _resolve_params(
    gate_name: str,
    config_path: Path | None,
    args: argparse.Namespace,
    ohlcv_loader: OhlcvLoader | None = None,
) -> dict[str, Any]:
    if gate_name == "volume-suppress":
        if config_path is None:
            return {"volume_suppress_off": set()}
        cfg = load_backtest_config(config_path)
        off = {
            name
            for name, override in cfg.strategy_params.items()
            if override.volume_suppress is False
            or (override.volume_suppress is None and not cfg.volume_suppress)
        }
        return {"volume_suppress_off": off}
    if gate_name == "adr-exempt":
        if ohlcv_loader is None:
            raise ValueError("adr-exempt gate requires ohlcv_loader")
        if config_path is None:
            return {
                "exempt": set(),
                "threshold": 0.80,
                "ohlcv_loader": ohlcv_loader,
            }
        cfg = load_backtest_config(config_path)
        exempt = {
            name
            for name, override in cfg.strategy_params.items()
            if override.adr_exempt
        }
        return {
            "exempt": exempt,
            "threshold": cfg.adr_suppress_threshold or 0.80,
            "ohlcv_loader": ohlcv_loader,
        }
    if gate_name == "day-filter":
        return {"candidate": args.day_filter or "tue_thu"}
    raise ValueError(f"Unknown gate: {gate_name}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Phase A gate audit (replay-only).")
    p.add_argument("gate", choices=list(GATE_REGISTRY.keys()))
    p.add_argument(
        "--config",
        type=Path,
        default=None,
        help="TOML config to resolve current toggle state from.",
    )
    p.add_argument("--db", type=Path, default=Path(DEFAULT_DB_PATH))
    p.add_argument(
        "--run-id",
        type=str,
        default=None,
        help="Pin to a single backtest_runs.run_id (UUID string). Overrides --config scoping.",
    )
    p.add_argument(
        "--since",
        type=str,
        default=None,
        help="ISO date YYYY-MM-DD; filters by signal_time.",
    )
    p.add_argument("--strategy", type=str, default=None)
    p.add_argument("--tf", type=str, default=None)
    p.add_argument("--direction", choices=["long", "short"], default=None)
    p.add_argument("--symbol", type=str, default=None)
    p.add_argument(
        "--grain",
        default="all",
        choices=[
            "strategy",
            "strategy_tf",
            "strategy_tf_dir",
            "strategy_tf_dir_sym",
            "all",
        ],
    )
    p.add_argument("--min-n", type=int, default=30)
    p.add_argument("--threshold", type=float, default=0.05)
    p.add_argument(
        "--day-filter",
        choices=list(DAY_FILTER_MODES),
        default=None,
        help="For gate=day-filter: which candidate value to test.",
    )
    return p


GRAIN_COLUMNS = {
    "strategy": ["strategy"],
    "strategy_tf": ["strategy", "tf"],
    "strategy_tf_dir": ["strategy", "tf", "direction"],
    "strategy_tf_dir_sym": ["strategy", "tf", "direction", "symbol"],
}


def main() -> int:
    args = build_parser().parse_args()
    gate = GATE_REGISTRY[args.gate]
    since_ms = (
        int(pd.Timestamp(args.since, tz="UTC").timestamp() * 1000)
        if args.since
        else None
    )

    if args.run_id is not None:
        run_ids: list[str] | None = [args.run_id]
        scope_label = f"run_id={args.run_id}"
    elif args.config is not None:
        run_ids = _resolve_config_run_ids(args.db, args.config)
        scope_label = f"config={args.config} → {len(run_ids)} run_ids"
    else:
        run_ids = None
        scope_label = "(unscoped — all backtest_trades rows)"

    df = load_trades(args.db, run_ids, since_ms)
    for col, val in [
        ("strategy", args.strategy),
        ("tf", args.tf),
        ("direction", args.direction),
        ("symbol", args.symbol),
    ]:
        if val is not None:
            df = df[df[col] == val]

    if df.empty:
        print(f"No trades match filter ({scope_label}) — nothing to audit.")
        return 1

    ohlcv_loader = _make_db_ohlcv_loader(args.db) if args.gate == "adr-exempt" else None
    params = _resolve_params(args.gate, args.config, args, ohlcv_loader)
    print(f"Gate: {gate.name}  ({gate.description})")
    print(
        f"Scope: {scope_label}  Rows: {len(df)}  min_n={args.min_n}  threshold={args.threshold}"
    )
    print(f"Params: {params}")
    print()

    grains_to_print = (
        list(GRAIN_COLUMNS.values())
        if args.grain == "all"
        else [GRAIN_COLUMNS[args.grain]]
    )
    for grain in grains_to_print:
        table = build_audit_table(df, gate, params, grain, args.min_n, args.threshold)
        print(f"--- grain: {' × '.join(grain)} ---")
        if table.empty:
            print("(empty)")
        else:
            print(table.to_string(index=False))
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
