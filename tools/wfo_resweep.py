"""ST128 — run the corrected WFO sweep across the live configs and report the delta.

Read-only. Writes no TOML and no database row: it answers "what would the
pre-registered rule do", and applying is a separate, deliberate act because the
signal-watch timer runs the WORKING TREE, so writing `config/signal_watch*.toml`
IS the deployment. Pre-registration:
`docs/superpowers/specs/2026-09-10-st128-wfo-resweep-preregistration.md`.

Three defects made every live ``tp_r`` unfit for its own config, and all three
are corrected on the path this tool drives:

* ``min_sl_pct`` and ``slippage_pct`` were never passed, so the sweep book had no
  stop floor and priced 71% of real round-trip drag (ST128, PR #764).
* ``--day-filter`` refused ``mon_fri`` and ``weekend``, so two of the three live
  configs could not be swept on their own population at all.

⚠ **The decision rule lives HERE, in one pure function, on purpose.** ST28's sixth
powered-null site sat in gitignored scratch code whose detection threshold had
silently diverged from its spec (``|t| >= 1.96`` against a pre-registered
``2.802``), unreachable by every gate, grep and review surface this repo has. A
pre-registered criterion implemented in a throwaway script is that defect waiting
to recur, so :func:`decide_cell` is tracked, typed and pinned by
``tests/test_wfo_resweep.py`` against the prose it implements.

⚠ **A KEEP is not a clean bill.** The rule skips a cell whose every row fails,
leaving the old value standing — but that old value was fitted on the defective
book, so ``defect_carrying`` marks the cells whose CURRENT ``tp_r`` would not
survive today's filter. Those are the ones a "no change" line would otherwise
absorb.

⛔ **A SKIP is a FAILURE TO CLEAR, never a null.** "No row survives the OOS
filter" and "the commit gate refused" both say this cell did not earn a write —
they say nothing about whether an effect is there. Reading either as "no edge
here" is the conflation that reached six sites in this repo, each spelling the
arithmetic differently; a sample-size floor, an MDE, a p-value and a
failure-to-clear are none of them power. A negative claim about any cell needs CI
containment via :func:`analytics.audit_guard.powered_null`, which this tool does
not compute and must not be quoted as.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

# A bare `python3 tools/wfo_resweep.py` puts `tools/` on sys.path rather than the
# repo root, so the `analytics.*` imports below die with ModuleNotFoundError. Per
# ST101 the guarantee is `test_bare_invocation_works`, not this line.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:  # ST127: swapping the interpreter is a SEPARATE resolver from sys.path —
    # this tool imports duckdb, which a bare `python3` does not have.
    from tools.venv_bootstrap import reexec_into_venv  # noqa: E402

    reexec_into_venv(Path(__file__).resolve().parent.parent)
except ImportError:  # pragma: no cover - a clone without the helper still runs
    pass

import numpy as np  # noqa: E402

from analytics.live_exposure import alerts_per_week  # noqa: E402
from analytics.param_sweep import (  # noqa: E402
    MIN_TRADES_BY_TF,
    ParamRange,
    ParamSweepReport,
    SweepRow,
    _compute_sweep_gate,
    _cost_defaults,
    _default_param_ranges,
    _recommended_row,
    min_trades_for,
    run_param_sweep,
)
from analytics.sweep_guard import DECISION_INSUFFICIENT, CommitGateVerdict  # noqa: E402

DEFAULT_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
DEFAULT_SINCE = "2025-09-12"
CONFIG_GLOB = "config/signal_watch*.toml"

# Pre-registration §2. Restated nowhere else; the test asserts these ARE the
# numbers the spec prose carries.
MIN_OOS_TRADES = MIN_TRADES_BY_TF  # imported, never restated
MIN_TP_R_STEP = 0.5
MIN_IMPROVEMENT_R = 0.05

ACTION_UPDATE = "UPDATE"
ACTION_KEEP = "KEEP"
ACTION_SKIP = "SKIP"

BOOKS: tuple[tuple[str, bool, bool], ...] = (
    ("raw", False, False),
    ("trials_corrected", True, False),
    ("obs_corrected", False, True),
    ("both", True, True),
)
"""ST134 section 5: every cell is scored under all four books.

Reporting only ``both`` makes the two corrections unattributable, which is the
failure ST128 section 5 exists to prevent. ``raw`` is first because it is the
baseline every other column is read against.
"""


def effect_size(
    current_oos_avg_r: float | None,
    winner_oos_avg_r: float | None,
) -> float | None:
    """OOS ``avg_r`` the winner adds over the live value, or None if either is absent.

    ⚠ **An optimistic bound, and it must be labelled one where it is reported.** The
    winner is SELECTED on this number, and that selection is exactly what DSR
    deflates for. The live realisation is expected to be smaller.
    """
    if current_oos_avg_r is None or winner_oos_avg_r is None:
        return None
    return winner_oos_avg_r - current_oos_avg_r


@dataclass(frozen=True)
class CellVerdict:
    """What the pre-registered rule does with one config × strategy × tf × symbol."""

    action: str
    reason: str
    current_tp_r: float | None
    current_oos_avg_r: float | None
    winner_tp_r: float | None
    winner_oos_avg_r: float | None
    winner_oos_n: int | None
    gate_decision: str
    defect_carrying: bool
    # Carried so the reachability question ("is the bar clearable at this n and
    # trial count?") is answerable from the JSON. Parsing them back out of the
    # reason string works and is exactly the brittleness worth not shipping.
    dsr: float | None
    pbo: float | None
    min_trl: float | None
    n_obs: int
    n_trials: int
    # ST134 §4a. None unless the caller passed `corrected` (the trials-corrected
    # re-score) into `decide_cell` — the pre-registered decision rule itself never
    # reads these two; they exist for the kill-switch measurement only.
    rho: float | None
    n_trials_eff: float | None


def _sweep_ranges(strategy: str) -> list[ParamRange]:
    """The grid this decision is about: ``tp_r`` (× ``sl_pct``), never the
    strategy-specific params.

    ⚠ ``_strategy_param_ranges`` is the WRONG function here and reads like the
    right one: its docstring says it builds ranges "for strategy-specific params
    only (excludes tp_r/sl_pct)", so a grid built from it has NO ``tp_r`` axis and
    every winner comes back ``None`` — a sweep that searches ``swing_n`` and
    ``lookback`` while claiming to pick a take-profit. This mirrors what the CLI
    does when ``--params`` is absent (``cli/param.py:30-33``).

    It also keeps the TRIAL COUNT honest. The commit gate deflates by the full
    grid size, and trial count dominates n in this repo — sweeping the strategy
    params alongside would inflate the family ~9x and make the gate unreachable
    for reasons that have nothing to do with ``tp_r``.
    """
    return _default_param_ranges(strategy)


def _row_tp_r(row: SweepRow) -> float | None:
    value = row.params.get("tp_r")
    return float(value) if value is not None else None


def _eligible(row: SweepRow, floor: int) -> bool:
    """Pre-registration §2 steps 1-3: not overfit, enough OOS trades, positive."""
    if row.overfit:
        return False
    if row.oos_trades < floor:
        return False
    oos = row.oos_avg_r
    return oos is not None and oos > 0


def decide_cell(
    report: ParamSweepReport,
    *,
    timeframe: str,
    current_tp_r: float | None,
    corrected: CommitGateVerdict | None = None,
) -> CellVerdict:
    """Apply the pre-registered rule to one sweep report.

    Pure and total: every path returns a verdict, so a cell can never fall out of
    the report silently. The COMMIT-GATE is checked FIRST and is a hard refusal —
    an in-sample winner that fails it is an overfit mirage, and this is the
    project's multiple-testing correction.

    ``corrected`` is ST134 §4a's trials-corrected re-score of the SAME report
    (``_compute_sweep_gate(report.all_rows, ..., correct_trials=True)``), and it
    is optional so every existing caller is unaffected. It is a caller-supplied
    value rather than computed here on purpose: computing it inline would make
    this pure, total, cheap function impure and expensive on every invocation,
    not just the ``--measure-rho`` kill-switch run that actually needs it.
    """
    floor = MIN_OOS_TRADES.get(timeframe, 0)

    current_row = next(
        (
            r
            for r in report.rows
            if current_tp_r is not None and _row_tp_r(r) == current_tp_r
        ),
        None,
    )
    current_oos = current_row.oos_avg_r if current_row is not None else None
    # A current value that would not survive today's filter is carrying the
    # defect forward even when the action below is KEEP or SKIP.
    defect_carrying = current_row is not None and not _eligible(current_row, floor)

    def verdict(action: str, reason: str, winner: SweepRow | None) -> CellVerdict:
        return CellVerdict(
            action=action,
            reason=reason,
            current_tp_r=current_tp_r,
            current_oos_avg_r=current_oos,
            winner_tp_r=_row_tp_r(winner) if winner is not None else None,
            winner_oos_avg_r=winner.oos_avg_r if winner is not None else None,
            winner_oos_n=winner.oos_trades if winner is not None else None,
            gate_decision=report.gate.decision,
            defect_carrying=defect_carrying,
            dsr=report.gate.dsr,
            pbo=report.gate.pbo,
            min_trl=report.gate.min_trl,
            n_obs=report.gate.n_obs,
            n_trials=report.gate.n_trials,
            rho=corrected.rho if corrected is not None else None,
            n_trials_eff=corrected.n_trials_eff if corrected is not None else None,
        )

    if not report.gate.committable:
        detail = "; ".join(report.gate.reasons) or report.gate.decision
        return verdict(
            ACTION_SKIP, f"commit-gate {report.gate.decision}: {detail}", None
        )

    survivors = [r for r in report.rows if _eligible(r, floor)]
    if not survivors:
        return verdict(
            ACTION_SKIP, f"no row survives the OOS filter (n>={floor})", None
        )

    winner = max(survivors, key=lambda r: r.oos_avg_r or 0.0)
    if current_tp_r is None:
        return verdict(ACTION_UPDATE, "no current tp_r for this cell", winner)

    winner_tp = _row_tp_r(winner)
    moved_enough = (
        winner_tp is not None and abs(winner_tp - current_tp_r) >= MIN_TP_R_STEP
    )
    improved_enough = (
        current_oos is not None
        and winner.oos_avg_r is not None
        and (winner.oos_avg_r - current_oos) > MIN_IMPROVEMENT_R
    )
    if moved_enough or improved_enough:
        return verdict(ACTION_UPDATE, "clears the step or improvement bar", winner)
    return verdict(
        ACTION_KEEP, "marginal: under both the step and improvement bars", winner
    )


def current_tp_r_for(
    params: dict[str, Any], strategy: str, timeframe: str, symbol: str
) -> float | None:
    """Resolve the TOML's effective ``tp_r``: per-symbol > per-tf > strategy fallback."""
    block = params.get(strategy)
    if not isinstance(block, dict):
        return None
    per_symbol = block.get(symbol)
    if isinstance(per_symbol, dict):
        value = per_symbol.get(f"tp_r_{timeframe}", per_symbol.get("tp_r"))
        if value is not None:
            return float(value)
    value = block.get(f"tp_r_{timeframe}", block.get("tp_r"))
    return float(value) if value is not None else None


def cells_for_config(
    config: dict[str, Any], symbols: tuple[str, ...]
) -> list[tuple[str, str, str]]:
    """`(strategy, timeframe, symbol)` for every cell this config actually runs."""
    active_tfs = set(config.get("timeframes", []))
    out: list[tuple[str, str, str]] = []
    for strategy, tfs in sorted(config.get("strategy_timeframes", {}).items()):
        for timeframe in tfs:
            if timeframe not in active_tfs:
                continue
            out.extend((strategy, timeframe, symbol) for symbol in symbols)
    return out


def _since_ms(since: str) -> int:
    from datetime import UTC, datetime

    return int(
        datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() * 1000
    )


def _fmt(value: float | None, spec: str = "+.4f") -> str:
    return "—" if value is None else format(value, spec)


def median_rho_ci(
    rhos: list[float],
    *,
    n_boot: int = 10_000,
    seed: int = 20260910,
) -> tuple[float, float, float]:
    """Median arm correlation and its percentile bootstrap CI.

    ST134 section 4a. The decision statistic is the DISTRIBUTION across cells, never
    any one cell's point estimate — rho is measured over ``2 * n_splits`` bins, so a
    per-cell value is noisy by construction.
    """
    clean = [r for r in rhos if not math.isnan(r)]
    if not clean:
        nan = float("nan")
        return nan, nan, nan
    arr = np.asarray(clean, dtype=np.float64)
    med = float(np.median(arr))
    rng = np.random.default_rng(seed)
    draws = rng.choice(arr, size=(n_boot, arr.size), replace=True)
    meds = np.median(draws, axis=1)
    return med, float(np.percentile(meds, 2.5)), float(np.percentile(meds, 97.5))


def rho_verdict(ci_lo: float, ci_hi: float, *, bar: float = 0.5) -> str:
    """CI containment against the pre-registered bar — THREE readings, not two.

    ⛔ A threshold comparison is not a verdict. A sample-size floor, an MDE, a
    p-value or a failure to clear a bar are none of them power; ``AGENTS.md`` records
    that family at six sites, each spelling the arithmetic differently.

    * ``PROCEED`` — the CI lower bound clears the bar; the correction is licensed.
    * ``NOT_LICENSED`` — the upper bound sits below it; established the other way.
    * ``INSUFFICIENT`` — the CI straddles or touches the bar. **Untested, not
      cleared**, and it licenses no conclusion about whether the gate's refusals are
      correct. Those are different claims.
    """
    if math.isnan(ci_lo) or math.isnan(ci_hi):
        return "INSUFFICIENT"
    if ci_lo > bar:
        return "PROCEED"
    if ci_hi < bar:
        return "NOT_LICENSED"
    return "INSUFFICIENT"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--db", default="analytics.db", help="DuckDB path (read-only)")
    parser.add_argument("--since", default=DEFAULT_SINCE)
    parser.add_argument(
        "--config", action="append", help="Repeatable; default = all shipped"
    )
    parser.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument(
        "--out",
        default="docs/plans/scratch/st134-resweep-{label}.json",
        help=(
            "Write the full per-cell result as JSON here. Defaults under "
            "docs/plans/scratch/ so the run's evidence survives a reboot and lands "
            "in the backup glob — ST128's went to /tmp and is gone."
        ),
    )
    # Attribution control. The point of ST128 is that the OLD book priced no
    # slippage and no stop floor, so "did the correction cause this?" is only
    # answerable by re-running the defective settings against the same cells.
    # Defaults stay production's -- an override is always a deliberate experiment.
    parser.add_argument("--fee-pct", type=float, default=None)
    parser.add_argument("--slippage-bps", type=float, default=None)
    parser.add_argument("--min-sl-pct", type=float, default=None)
    parser.add_argument(
        "--label", default="corrected", help="Tag written into every JSON row"
    )
    parser.add_argument(
        "--measure-rho",
        action="store_true",
        help=(
            "ST134 section 4a kill-switch: run every cell with the trial correction on, "
            "report the arm-correlation distribution and its CI verdict, and STOP. "
            "Writes no TOML and decides nothing."
        ),
    )
    parser.add_argument(
        "--books",
        action="store_true",
        help=(
            "ST134 section 5: score every cell under all four books (raw / "
            "trials_corrected / obs_corrected / both) and report effect size per "
            "book, never a pass count. Its OWN flag rather than riding "
            "--measure-rho, which is the section 4a kill-switch that runs FIRST "
            "and STOPS — gating this behind it would make the 2x2 reachable only "
            "inside a run that is supposed to stop. Writes no TOML and decides "
            "nothing."
        ),
    )
    args = parser.parse_args(argv)

    import duckdb

    configs = (
        [Path(c) for c in args.config]
        if args.config
        else sorted(Path().glob(CONFIG_GLOB))
    )
    symbols = tuple(s.strip() for s in args.symbols.split(",") if s.strip())
    fee_pct, slippage_pct, min_sl_pct = _cost_defaults()
    if args.fee_pct is not None:
        fee_pct = args.fee_pct
    if args.slippage_bps is not None:
        slippage_pct = args.slippage_bps / 10000.0
    if args.min_sl_pct is not None:
        min_sl_pct = args.min_sl_pct
    since_ms = _since_ms(args.since)
    # `now_ms` for `alerts_per_week` below. `since_ms` is already this run's start
    # (the sweep's own `--since`) — reused rather than restated via a second date
    # library, which would be the same value computed twice by different means.
    now_ms = int(time.time() * 1000)

    print(
        f"ST128 corrected re-sweep — {len(configs)} config(s), symbols {', '.join(symbols)}"
    )
    print(
        f"costs from strategy_params.toml: fee {fee_pct} · slippage {slippage_pct} "
        f"· stop floor {min_sl_pct}  (drag = 2(fee+slip) = {2 * (fee_pct + slippage_pct):.4f})"
    )

    conn = duckdb.connect(args.db, read_only=True)
    results: list[dict[str, Any]] = []
    started = time.time()
    try:
        for config_path in configs:
            with config_path.open("rb") as fh:
                config = tomllib.load(fh)
            day_filter = config.get("day_filter", "off")
            params = config.get("strategy_params", {})
            cells = cells_for_config(config, symbols)
            print(
                f"\n=== {config_path}  day_filter={day_filter}  {len(cells)} cells ==="
            )

            for strategy, timeframe, symbol in cells:
                current = current_tp_r_for(params, strategy, timeframe, symbol)
                try:
                    report = run_param_sweep(
                        conn,
                        strategy=strategy,
                        symbol=symbol,
                        timeframe=timeframe,
                        days=0,
                        param_ranges=_sweep_ranges(strategy),
                        wfo_split=0.7,
                        min_trades=min_trades_for(timeframe),
                        fee_pct=fee_pct,
                        min_sl_pct=min_sl_pct,
                        slippage_pct=slippage_pct,
                        top_n=20,
                        since_ms=since_ms,
                        day_filter=day_filter,
                    )
                except Exception as exc:  # a dead cell must not kill the run
                    print(f"  !! {strategy:22} {timeframe:4} {symbol:8} ERROR {exc}")
                    continue

                # ST134 section 5: the 2x2 re-score. Gated on its OWN flag as well
                # as the section 4a kill-switch (R13/main docstring above) — either
                # one triggers it, so `--measure-rho` alone still gets its
                # trials_corrected numbers. Every book reads `all_rows` (R3: the
                # full grid the gate deflates against), never the top-N `rows`
                # truncation, and the chosen row still comes from `rows`, mirroring
                # production's own recommended-row pick at
                # `analytics/param_sweep.py:546` — only the trial family widens.
                # This runs no additional backtests, but each of the four re-scores
                # still re-runs CSCV/DSR — real cost the default path must not pay.
                # `_recommended_row` is pure and cheap, so hoisting it here is not a
                # performance fix — it is computed ONCE and reused across all four
                # books to keep "one chosen row, four scorings" visible in the code,
                # rather than recomputed identically inside each comprehension pass.
                chosen_row = _recommended_row(report.rows)
                book_verdicts = (
                    {
                        name: _compute_sweep_gate(
                            report.all_rows,
                            chosen_row,
                            report.n_grid,
                            correct_trials=ct,
                            correct_obs=co,
                        )
                        for name, ct, co in BOOKS
                    }
                    if args.measure_rho or args.books
                    else None
                )

                verdict = decide_cell(
                    report,
                    timeframe=timeframe,
                    current_tp_r=current,
                    # The SAME value Task 6 computed — BOOKS's "trials_corrected"
                    # entry is exactly correct_trials=True, correct_obs=False —
                    # reused from book_verdicts rather than recomputed, so the rho
                    # kill-switch never pays the CSCV cost twice.
                    corrected=(
                        book_verdicts["trials_corrected"]
                        if book_verdicts is not None
                        else None
                    ),
                )
                mark = "⚠" if verdict.defect_carrying else " "
                print(
                    f"  {mark} {strategy:22} {timeframe:4} {symbol:8} "
                    f"{verdict.action:6} cur {_fmt(verdict.current_tp_r, '.1f'):>4} "
                    f"→ {_fmt(verdict.winner_tp_r, '.1f'):>4}  "
                    f"OOS {_fmt(verdict.winner_oos_avg_r)}  n={verdict.winner_oos_n or 0:<4} "
                    f"{verdict.reason}"
                )
                results.append(
                    {
                        "config": str(config_path),
                        "day_filter": day_filter,
                        "strategy": strategy,
                        "timeframe": timeframe,
                        "symbol": symbol,
                        "label": args.label,
                        **asdict(verdict),
                        "books": (
                            {
                                name: {
                                    "decision": v.decision,
                                    "dsr": v.dsr,
                                    "pbo": v.pbo,
                                    "rho": v.rho,
                                    "n_trials_eff": v.n_trials_eff,
                                    "design_effect": v.design_effect,
                                    "n_obs_eff": v.n_obs_eff,
                                    "reasons": v.reasons,
                                }
                                for name, v in book_verdicts.items()
                            }
                            if book_verdicts is not None
                            else None
                        ),
                        # Unconditional, unlike "books" above: it reads only the
                        # current/winner oos_avg_r decide_cell produces every run.
                        "effect_size_oos_avg_r": effect_size(
                            verdict.current_oos_avg_r, verdict.winner_oos_avg_r
                        ),
                        # ST134 section 5 item 2 — the other half of effect size:
                        # how often this cell fires live. Also unconditional: the
                        # four-book gate exists because each book re-runs CSCV/PBO
                        # (C(14,7)=3,432 splits), while this is one COUNT(*) against
                        # the already-open ledger connection, so that cost reasoning
                        # does not transfer here.
                        "live_alerts_per_week": alerts_per_week(
                            conn,
                            symbol=symbol,
                            timeframe=timeframe,
                            strategy=strategy,
                            since_ms=since_ms,
                            now_ms=now_ms,
                        ),
                        "moved_to_insufficient_under_obs_correction": (
                            None
                            if book_verdicts is None
                            else (
                                book_verdicts["raw"].decision != DECISION_INSUFFICIENT
                                and book_verdicts["obs_corrected"].decision
                                == DECISION_INSUFFICIENT
                            )
                        ),
                    }
                )
    finally:
        conn.close()

    total = len(results)
    by_action = {
        a: sum(1 for r in results if r["action"] == a)
        for a in (ACTION_UPDATE, ACTION_KEEP, ACTION_SKIP)
    }
    carrying = sum(1 for r in results if r["defect_carrying"])
    print(f"\n{'=' * 78}\n{total} cells in {time.time() - started:.1f}s")
    for action, count in by_action.items():
        print(f"  {action:6} {count}")
    print(f"  ⚠ DEFECT-CARRYING (current tp_r fails today's filter): {carrying}")
    print(
        "\nNo TOML was written. Applying is a separate act — see the pre-registration §4."
    )

    if args.measure_rho:
        rhos = [r["rho"] for r in results if r.get("rho") is not None]
        med, lo, hi = median_rho_ci(rhos)
        rho_outcome = rho_verdict(lo, hi)
        print(f"\nST134 §4a — arm correlation across {len(rhos)} scoreable cells")
        print(f"  median rho {med:.4f}   95% CI [{lo:.4f}, {hi:.4f}]   bar 0.50")
        print(f"  VERDICT: {rho_outcome}")
        if rho_outcome != "PROCEED":
            print(
                "  ⛔ STOP. This does NOT license 'the gate was right' — failing to\n"
                "     license a correction is a different claim from the refusals\n"
                "     being correct. Route to ST133 on the existing evidence."
            )

    if args.out:
        out_path = Path(args.out.format(label=args.label))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"per-cell JSON: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
