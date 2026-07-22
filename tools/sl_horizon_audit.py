#!/usr/bin/env python
"""ST9 / H11 SL-horizon audit (read-only).

Six candle detectors hard-code ``sl_pct = 0.02`` at every timeframe. This tool
re-resolves the same signals under an a-priori ATR-multiplier grid and emits a
pre-committed SUSPECT / CONFIRMED-BAD / NO-DIFFERENCE / INSUFFICIENT verdict per
(strategy x timeframe), via :mod:`analytics.sl_horizon`.

Substrate roles (pre-committed): LIVE ``signal_alert_outcomes`` is the GATE;
``backtest_trades`` corroborates. One Holm family per substrate, never pooled.

Read-only: no DB writes, no config edits, no engine change.

Run: ``PYTHONPATH=. poetry run python tools/sl_horizon_audit.py``
(wrapped by ``make buibui-sl-horizon-audit``).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from analytics.sl_horizon import (  # noqa: E402
    BASELINE_ARM,
    FAMILY,
    SLGridConfig,
    arm_label,
    atr_by_open_time,
    baseline_levels,
    counterfactual_levels,
    resolve_arm,
    window_for_signal,
)
from analytics.store import DEFAULT_DB_PATH  # noqa: E402
from analytics.store.market_data import get_ohlcv  # noqa: E402
from analytics.strategies._registry import DETECTOR_REGISTRY  # noqa: E402

TF_MS: dict[str, int] = {
    "15m": 15 * 60_000,
    "1h": 60 * 60_000,
    "4h": 4 * 60 * 60_000,
    "1d": 24 * 60 * 60_000,
}

#: (strategy, symbol, tf, direction) -> pinned tp_r
TpRLookup = Callable[[str, str, str, str], float]


@dataclass(frozen=True)
class FidelityReport:
    """Result of comparing a replayed baseline arm against a stored substrate."""

    passed: bool
    n_matched: int
    agreement: float
    worst_avg_r_delta: float
    reasons: list[str] = field(default_factory=list)


def load_live_signals(conn: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Resolved live alerts for the six audited detectors.

    Only rows with a non-NULL ``outcome_r`` are returned — unresolved alerts have
    no stored result to compare against and cannot anchor the live fidelity check.
    """
    placeholders = ", ".join("?" for _ in FAMILY)
    return conn.execute(
        f"""
        SELECT signal_id, symbol, tf, strategy, direction, candle_ts_ms,
               entry_price, sl_price, tp_price, rr_ratio, outcome, outcome_r
        FROM signal_alert_outcomes
        WHERE strategy IN ({placeholders})
          AND outcome_r IS NOT NULL
        ORDER BY candle_ts_ms
        """,
        list(FAMILY),
    ).df()


def load_backtest_signals(
    conn: duckdb.DuckDBPyConnection,
    *,
    symbols: list[str],
    timeframes: list[str],
) -> pd.DataFrame:
    """Re-detect the six detectors over full OHLCV history.

    Returns one row per detected signal with columns
    ``SIGNAL_KEY + ["sig_idx", "detector_sl_price"]``. Signals are re-detected
    rather than read from ``backtest_trades`` because the stored runs cover only
    3 symbols over ~10 months, which leaves the 1d cell unpowered.
    """
    records: list[dict[str, object]] = []
    for tf in timeframes:
        for symbol in symbols:
            ohlcv = get_ohlcv(conn, symbol, tf, 0, 2**62)
            if ohlcv.empty:
                continue
            position = {int(t): i for i, t in enumerate(ohlcv["open_time"].to_numpy())}
            for strategy in FAMILY:
                detector = DETECTOR_REGISTRY[strategy]
                signals = detector(ohlcv)
                if signals.empty:
                    continue
                for _, sig in signals.iterrows():
                    open_time = int(sig["open_time"])
                    sig_idx = position.get(open_time)
                    if sig_idx is None:
                        continue
                    records.append(
                        {
                            "symbol": symbol,
                            "tf": tf,
                            "strategy": strategy,
                            "direction": str(sig["direction"]),
                            "open_time": open_time,
                            "sig_idx": sig_idx,
                            "detector_sl_price": float(sig["sl_price"]),
                        }
                    )
    return pd.DataFrame(records)


def resolve_all_arms(
    signals: pd.DataFrame,
    ohlcv_by_key: dict[tuple[str, str], pd.DataFrame],
    *,
    cfg: SLGridConfig,
    convention: str,
    tp_r_for: TpRLookup,
) -> pd.DataFrame:
    """Resolve every signal under the baseline arm and each ``k`` arm.

    Returns long-format rows (one per signal x arm) carrying ``net_r``,
    ``outcome``, ``exit_bar``, ``sl_dist_pct`` and ``atr_pct``, ready for
    ``build_paired_table`` and ``describe_horizon``.
    """
    rows: list[dict[str, object]] = []
    for (symbol, tf), grp in signals.groupby(["symbol", "tf"], sort=True):
        ohlcv = ohlcv_by_key.get((str(symbol), str(tf)))
        if ohlcv is None or ohlcv.empty:
            continue
        max_hold = cfg.max_hold_bars_by_tf.get(str(tf), 48)
        atr_map = atr_by_open_time(ohlcv, grp["open_time"].tolist())

        for _, sig in grp.iterrows():
            open_time = int(sig["open_time"])
            atr = atr_map.get(open_time)
            if atr is None or atr <= 0.0:
                continue
            direction = str(sig["direction"])
            strategy = str(sig["strategy"])
            tp_r = tp_r_for(strategy, str(symbol), str(tf), direction)

            entry, highs, lows, closes = window_for_signal(
                ohlcv,
                sig_idx=int(sig["sig_idx"]),
                convention=convention,
                max_hold_bars=max_hold,
            )
            if len(highs) == 0 or not np.isfinite(entry):
                continue

            arms: list[tuple[str, tuple[float, float]]] = [
                (
                    BASELINE_ARM,
                    baseline_levels(
                        entry, direction, baseline_pct=cfg.baseline_pct, tp_r=tp_r
                    ),
                )
            ]
            for k in cfg.multipliers:
                arms.append(
                    (
                        arm_label(k),
                        counterfactual_levels(
                            entry, direction, atr=atr, k=k, tp_r=tp_r
                        ),
                    )
                )

            for arm, (sl_price, _tp_price) in arms:
                res = resolve_arm(
                    highs,
                    lows,
                    closes,
                    direction=direction,
                    entry=entry,
                    sl_price=sl_price,
                    tp_r=tp_r,
                    max_hold_bars=max_hold,
                    round_trip_cost_pct=cfg.round_trip_cost_pct,
                    funding_r=0.0,
                )
                if res is None:
                    continue
                rows.append(
                    {
                        "symbol": symbol,
                        "tf": tf,
                        "strategy": strategy,
                        "direction": direction,
                        "open_time": open_time,
                        "arm": arm,
                        "net_r": res.net_r,
                        "realized_r": res.realized_r,
                        "outcome": res.outcome,
                        "exit_bar": res.exit_bar,
                        "sl_dist_pct": res.sl_dist_pct,
                        "cost_r": res.cost_r,
                        "atr_pct": atr / entry,
                    }
                )
    return pd.DataFrame(rows)


def check_fidelity(
    replayed: pd.DataFrame,
    stored: pd.DataFrame,
    *,
    tolerance_r: float,
    min_agreement: float,
) -> FidelityReport:
    """Compare a replayed baseline arm against the stored substrate.

    Both conditions must hold: the avg_r delta is checked **per (strategy, tf)**
    (``|avg_r_replayed - avg_r_stored| <= tolerance_r`` for every cell) while the
    outcome agreement is checked **globally** over all matched trades
    (``>= min_agreement``). The mean alone would hide offsetting per-trade errors;
    the agreement rate alone would hide a uniform shift.
    """
    merged = replayed.merge(stored, on=["strategy", "tf", "key"], how="inner")
    if merged.empty:
        return FidelityReport(
            passed=False,
            n_matched=0,
            agreement=0.0,
            worst_avg_r_delta=float("nan"),
            reasons=["no rows matched between replayed and stored substrates"],
        )

    reasons: list[str] = []
    worst = 0.0
    for (strategy, tf), grp in merged.groupby(["strategy", "tf"], sort=True):
        delta = abs(float(grp["net_r"].mean()) - float(grp["stored_r"].mean()))
        worst = max(worst, delta)
        if delta > tolerance_r:
            reasons.append(
                f"{strategy} {tf}: avg_r delta {delta:.4f} > tolerance {tolerance_r}"
            )

    agreement = float((merged["outcome"] == merged["stored_outcome"]).mean())
    if agreement < min_agreement:
        reasons.append(f"outcome agreement {agreement:.3f} < required {min_agreement}")

    return FidelityReport(
        passed=not reasons,
        n_matched=int(len(merged)),
        agreement=agreement,
        worst_avg_r_delta=worst,
        reasons=reasons,
    )


#: The backtest engine has no expiry, so the fidelity replay must not impose one.
#: Larger than any realistic OHLCV history, so the window is never truncated.
NO_TIME_STOP_BARS = 10_000_000


def load_stored_backtest_trades(
    conn: duckdb.DuckDBPyConnection,
    *,
    symbols: list[str],
    timeframes: list[str],
) -> pd.DataFrame:
    """Stored engine trades for the family, deduped across saved runs.

    Keeps the lexicographically-latest ``run_id`` per
    ``(symbol, tf, strategy, direction, signal_time)`` — the same dedup
    ``tools/warning_value_audit.py`` uses. Returns the columns
    ``check_fidelity`` expects: ``strategy``, ``tf``, ``key``, ``stored_r``,
    ``stored_outcome``.
    """
    fam = ", ".join("?" for _ in FAMILY)
    sym = ", ".join("?" for _ in symbols)
    tfs = ", ".join("?" for _ in timeframes)
    raw = conn.execute(
        f"""
        SELECT run_id, symbol, timeframe AS tf, strategy, direction,
               signal_time, pnl_r, outcome
        FROM backtest_trades
        WHERE strategy IN ({fam})
          AND symbol IN ({sym})
          AND timeframe IN ({tfs})
          AND pnl_r IS NOT NULL
        """,
        [*FAMILY, *symbols, *timeframes],
    ).df()
    if raw.empty:
        return pd.DataFrame(
            columns=["strategy", "tf", "key", "stored_r", "stored_outcome"]
        )

    # Sort then drop_duplicates in pandas — DuckDB window functions have
    # segfaulted on this table before (see feedback_duckdb_window_functions).
    raw = raw.sort_values("run_id")
    deduped = raw.drop_duplicates(
        subset=["symbol", "tf", "strategy", "direction", "signal_time"], keep="last"
    )
    deduped = deduped.assign(
        key=(
            deduped["symbol"].astype(str)
            + "|"
            + deduped["direction"].astype(str)
            + "|"
            + deduped["signal_time"].astype("int64").astype(str)
        )
    )
    return deduped.rename(columns={"pnl_r": "stored_r", "outcome": "stored_outcome"})[
        ["strategy", "tf", "key", "stored_r", "stored_outcome"]
    ]


def resolve_live_arms(
    alerts: pd.DataFrame,
    ohlcv_by_key: dict[tuple[str, str], pd.DataFrame],
    *,
    cfg: SLGridConfig,
    tp_r_for: TpRLookup,
) -> pd.DataFrame:
    """Resolve live alerts under the baseline and each ``k`` arm.

    The baseline arm uses the alert's **stored** ``sl_price``, not a recomputed
    2%, so it reproduces what actually fired — that is what makes the stored
    ``outcome_r`` a usable second fidelity anchor. The ``k`` arms replace the
    stop with ``k × ATR14`` at the signal candle.
    """
    rows: list[dict[str, object]] = []
    for (symbol, tf), grp in alerts.groupby(["symbol", "tf"], sort=True):
        ohlcv = ohlcv_by_key.get((str(symbol), str(tf)))
        if ohlcv is None or ohlcv.empty:
            continue
        max_hold = cfg.max_hold_bars_by_tf.get(str(tf), 48)
        position = {int(t): i for i, t in enumerate(ohlcv["open_time"].to_numpy())}
        atr_map = atr_by_open_time(ohlcv, grp["candle_ts_ms"].tolist())

        for _, alert in grp.iterrows():
            candle_ts = int(alert["candle_ts_ms"])
            sig_idx = position.get(candle_ts)
            atr = atr_map.get(candle_ts)
            if sig_idx is None or atr is None or atr <= 0.0:
                continue

            direction = str(alert["direction"])
            strategy = str(alert["strategy"])
            entry = float(alert["entry_price"])
            tp_r = tp_r_for(strategy, str(symbol), str(tf), direction)

            _entry_unused, highs, lows, closes = window_for_signal(
                ohlcv, sig_idx=sig_idx, convention="live", max_hold_bars=max_hold
            )
            if len(highs) == 0:
                continue

            arms: list[tuple[str, float]] = [(BASELINE_ARM, float(alert["sl_price"]))]
            for k in cfg.multipliers:
                sl_price, _tp = counterfactual_levels(
                    entry, direction, atr=atr, k=k, tp_r=tp_r
                )
                arms.append((arm_label(k), sl_price))

            for arm, sl_price in arms:
                res = resolve_arm(
                    highs,
                    lows,
                    closes,
                    direction=direction,
                    entry=entry,
                    sl_price=sl_price,
                    tp_r=tp_r,
                    max_hold_bars=max_hold,
                    round_trip_cost_pct=cfg.round_trip_cost_pct,
                    funding_r=0.0,
                )
                if res is None:
                    continue
                rows.append(
                    {
                        "symbol": symbol,
                        "tf": tf,
                        "strategy": strategy,
                        "direction": direction,
                        "open_time": candle_ts,
                        "arm": arm,
                        "net_r": res.net_r,
                        "realized_r": res.realized_r,
                        "outcome": res.outcome,
                        "exit_bar": res.exit_bar,
                        "sl_dist_pct": res.sl_dist_pct,
                        "cost_r": res.cost_r,
                        "atr_pct": atr / entry,
                        "key": f"{symbol}|{direction}|{candle_ts}",
                    }
                )
    return pd.DataFrame(rows)


def backtest_fidelity(
    conn: duckdb.DuckDBPyConnection,
    signals: pd.DataFrame,
    ohlcv_by_key: dict[tuple[str, str], pd.DataFrame],
    *,
    cfg: SLGridConfig,
    tp_r_for: TpRLookup,
) -> FidelityReport:
    """Spec §7a — replay the baseline arm with NO time stop and compare to stored.

    A systematic offset here almost always means the entry convention is wrong,
    not that the model is wrong. Check `window_for_signal(convention="engine")`
    against `analytics/backtest/engine.py:954-1061` before anything else.
    """
    no_expiry = SLGridConfig(
        multipliers=cfg.multipliers,
        baseline_pct=cfg.baseline_pct,
        max_hold_bars_by_tf=dict.fromkeys(TF_MS, NO_TIME_STOP_BARS),
        fee_pct=cfg.fee_pct,
        slippage_bps=cfg.slippage_bps,
    )
    replayed = resolve_all_arms(
        signals, ohlcv_by_key, cfg=no_expiry, convention="engine", tp_r_for=tp_r_for
    )
    if replayed.empty:
        return FidelityReport(
            passed=False,
            n_matched=0,
            agreement=0.0,
            worst_avg_r_delta=float("nan"),
            reasons=["baseline replay produced no rows"],
        )
    baseline = replayed[replayed["arm"] == BASELINE_ARM].copy()
    baseline["key"] = (
        baseline["symbol"].astype(str)
        + "|"
        + baseline["direction"].astype(str)
        + "|"
        + baseline["open_time"].astype("int64").astype(str)
    )
    stored = load_stored_backtest_trades(
        conn,
        symbols=sorted({str(s) for s in signals["symbol"].unique()}),
        timeframes=sorted({str(t) for t in signals["tf"].unique()}),
    )
    return check_fidelity(baseline, stored, tolerance_r=0.02, min_agreement=0.95)


def live_fidelity(live_rows: pd.DataFrame, alerts: pd.DataFrame) -> FidelityReport:
    """Spec §7b — the re-resolved live baseline vs the stored ``outcome_r``."""
    if live_rows.empty:
        return FidelityReport(
            passed=False,
            n_matched=0,
            agreement=0.0,
            worst_avg_r_delta=float("nan"),
            reasons=["live replay produced no rows"],
        )
    baseline = live_rows[live_rows["arm"] == BASELINE_ARM].copy()
    stored = alerts.assign(
        key=(
            alerts["symbol"].astype(str)
            + "|"
            + alerts["direction"].astype(str)
            + "|"
            + alerts["candle_ts_ms"].astype("int64").astype(str)
        ),
        tf=alerts["tf"],
    ).rename(columns={"outcome_r": "stored_r", "outcome": "stored_outcome"})[
        ["strategy", "tf", "key", "stored_r", "stored_outcome"]
    ]
    return check_fidelity(baseline, stored, tolerance_r=0.02, min_agreement=0.95)


from analytics.signal.resolvers import _resolve_tp_r  # noqa: E402
from analytics.signal_config import load_signal_config  # noqa: E402
from analytics.sl_horizon import (  # noqa: E402
    SLVerdict,
    build_paired_table,
    describe_horizon,
    evaluate_sl_grid,
)
from analytics.universe import load_universe  # noqa: E402

DEFAULT_CONFIG = "config/signal_watch.toml"
DEFAULT_OUT = REPO_ROOT / "docs" / "audits" / "2026-07-21-st9-sl-horizon.md"
MAJORS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")


def build_tp_r_lookup(config_path: str) -> TpRLookup:
    """Pin tp_r per (strategy, symbol, tf, direction) from a live config.

    tp_r is PINNED, never swept — the audit has exactly one free axis (k).
    """
    cfg = load_signal_config(config_path)

    def lookup(strategy: str, symbol: str, tf: str, direction: str) -> float:
        return _resolve_tp_r(
            cfg.strategy_params, strategy, symbol, tf, cfg.tp_r, direction
        )

    return lookup


def _fmt(value: float | None, digits: int = 3) -> str:
    return "—" if value is None or not np.isfinite(value) else f"{value:.{digits}f}"


def render_report(
    *,
    horizon: pd.DataFrame,
    verdicts_live: list[SLVerdict],
    verdicts_backtest: list[SLVerdict],
    fidelity_backtest: FidelityReport,
    fidelity_live: FidelityReport,
    cfg: SLGridConfig,
) -> str:
    """Render the markdown audit report (markdownlint-conformant)."""
    lines: list[str] = [
        "# ST9 / H11 — SL-horizon audit",
        "",
        "Read-only. Live `signal_alert_outcomes` GATES the verdict;",
        "`backtest_trades` corroborates. One Holm family per substrate.",
        "",
        f"Grid (a-priori, never tuned): `k in {list(cfg.multipliers)}` x ATR14, "
        f"baseline `{cfg.baseline_pct:.0%}` flat. `tp_r` pinned, never swept.",
        "",
        "## Fidelity gate",
        "",
        "| Substrate | Passed | Matched | Agreement | Worst avg_r delta |",
        "| --- | --- | --- | --- | --- |",
        f"| backtest | {fidelity_backtest.passed} | {fidelity_backtest.n_matched} | "
        f"{_fmt(fidelity_backtest.agreement)} | {_fmt(fidelity_backtest.worst_avg_r_delta)} |",
        f"| live | {fidelity_live.passed} | {fidelity_live.n_matched} | "
        f"{_fmt(fidelity_live.agreement)} | {_fmt(fidelity_live.worst_avg_r_delta)} |",
        "",
    ]
    for report, name in ((fidelity_backtest, "backtest"), (fidelity_live, "live")):
        for reason in report.reasons:
            lines.append(f"- **{name} fidelity:** {reason}")
    if fidelity_backtest.reasons or fidelity_live.reasons:
        lines.append("")

    lines += [
        "## Descriptive horizon (baseline arm)",
        "",
        "| Strategy | TF | n | avg_r | median bars | expiry rate | SL % | SL in ATR |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for _, row in horizon.iterrows():
        lines.append(
            f"| {row['strategy']} | {row['tf']} | {int(row['n'])} | "
            f"{_fmt(row['avg_r'])} | {_fmt(row['median_bars'], 1)} | "
            f"{_fmt(row['expiry_rate'])} | {_fmt(row['median_sl_pct'], 4)} | "
            f"{_fmt(row['median_sl_atr'], 2)} |"
        )
    lines.append("")

    for verdicts, name in (
        (verdicts_live, "LIVE (gate)"),
        (verdicts_backtest, "Backtest"),
    ):
        lines += [
            f"## Verdicts — {name}",
            "",
            "| Strategy | TF | Decision | n | baseline avg_r | best k | lift | "
            "CI lo | CI hi | adj p | DSR | PBO |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for v in verdicts:
            lines.append(
                f"| {v.strategy} | {v.tf} | **{v.decision}** | {v.n} | "
                f"{_fmt(v.baseline_avg_r)} | {_fmt(v.best_k, 1)} | {_fmt(v.best_lift)} | "
                f"{_fmt(v.ci_lo)} | {_fmt(v.ci_hi)} | {_fmt(v.adj_pvalue)} | "
                f"{_fmt(v.dsr)} | {_fmt(v.pbo)} |"
            )
        lines.append("")

    cost = cfg.round_trip_cost_pct
    lines += [
        "## Cost context",
        "",
        f"Round-trip cost is `{cost:.4%}` of notional "
        f"(`2 x fee {cfg.fee_pct:.4f}` + `2 x slippage {cfg.slippage_bps} bps`).",
        "In R terms this scales inversely with stop width, so a tight stop is",
        "charged more R for the same trade. Read every lift against the",
        "`SL in ATR` column above before calling it an edge.",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Read-only; writes only the markdown report."""
    parser = argparse.ArgumentParser(description="ST9/H11 SL-horizon audit (read-only)")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument(
        "--timeframes",
        nargs="+",
        default=["1h", "4h", "1d"],
        help="15m is majors-only (no universe OHLCV at 15m).",
    )
    parser.add_argument("--min-n", type=int, default=30)
    parser.add_argument(
        "--majors-only",
        action="store_true",
        help="Restrict to BTC/ETH/SOL for a like-for-like cross-TF read.",
    )
    args = parser.parse_args(argv)

    cfg = SLGridConfig(min_n=args.min_n)
    arms = [arm_label(k) for k in cfg.multipliers]
    conn = duckdb.connect(args.db, read_only=True)
    try:
        symbols = list(MAJORS) if args.majors_only else load_universe()
        tp_r_for = build_tp_r_lookup(args.config)

        ohlcv_by_key = {
            (str(sym), str(tf)): get_ohlcv(conn, str(sym), str(tf), 0, 2**62)
            for tf in args.timeframes
            for sym in symbols
        }

        # --- backtest leg (corroboration) ---
        signals = load_backtest_signals(
            conn, symbols=symbols, timeframes=list(args.timeframes)
        )
        bt_rows = resolve_all_arms(
            signals, ohlcv_by_key, cfg=cfg, convention="engine", tp_r_for=tp_r_for
        )
        fid_bt = backtest_fidelity(
            conn, signals, ohlcv_by_key, cfg=cfg, tp_r_for=tp_r_for
        )

        # --- live leg (the GATE) ---
        alerts = load_live_signals(conn)
        live_ohlcv = (
            {
                (str(sym), str(tf)): get_ohlcv(conn, str(sym), str(tf), 0, 2**62)
                for tf in sorted({str(t) for t in alerts["tf"].unique()})
                for sym in sorted({str(s) for s in alerts["symbol"].unique()})
            }
            if not alerts.empty
            else {}
        )
        live_rows = resolve_live_arms(alerts, live_ohlcv, cfg=cfg, tp_r_for=tp_r_for)
        fid_live = live_fidelity(live_rows, alerts)
    finally:
        conn.close()

    bt_verdicts = evaluate_sl_grid(
        build_paired_table(bt_rows, arms=[BASELINE_ARM, *arms]), arms=arms, cfg=cfg
    )
    live_verdicts = evaluate_sl_grid(
        build_paired_table(live_rows, arms=[BASELINE_ARM, *arms]), arms=arms, cfg=cfg
    )

    report = render_report(
        horizon=describe_horizon(bt_rows, arm=BASELINE_ARM),
        verdicts_live=live_verdicts,
        verdicts_backtest=bt_verdicts,
        fidelity_backtest=fid_bt,
        fidelity_live=fid_live,
        cfg=cfg,
    )
    Path(args.out).write_text(report, encoding="utf-8")
    print(report)

    # The fidelity gate is an ACCEPTANCE condition, not a warning. A non-zero
    # exit makes a drifting harness impossible to ignore in CI or a make run.
    if not (fid_bt.passed and fid_live.passed):
        print(
            "\nFIDELITY GATE FAILED — verdicts above are NOT trustworthy.\n"
            "Check the entry convention first (engine enters at opens[sig_idx+1] "
            "and scans inclusive; live uses the stored entry with a window "
            "strictly after the signal candle).",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
