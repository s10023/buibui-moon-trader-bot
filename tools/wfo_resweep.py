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
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

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

from analytics.live_exposure import AlertExposure, alerts_per_week  # noqa: E402
from analytics.param_sweep import (  # noqa: E402
    MIN_TRADES_BY_TF,
    ParamRange,
    ParamSweepReport,
    SweepRow,
    _compute_sweep_gate,
    _cost_defaults,
    _default_param_ranges,
    _recommended_row,
    _row_to_trialperf,
    min_trades_for,
    run_param_sweep,
)
from analytics.sweep_guard import (  # noqa: E402
    DECISION_INSUFFICIENT,
    DEFAULT_N_SPLITS,
    MIN_EFFECTIVE_TRIALS,
    MIN_OBS_FACTOR,
    CommitGateVerdict,
    TrialPerf,
    _build_perf_matrix,
    _effective_trial_count,
)
from tools.st134_null_calibration import (  # noqa: E402
    ARM_CORRECTIONS,
    DEFAULT_REPLICATES,
    DEFAULT_SEED,
    NullCalibrationResult,
    null_pass_rate,
)

if TYPE_CHECKING:
    # Real `import duckdb` stays inside `main()`, after `reexec_into_venv` —
    # a bare `python3` has no duckdb, and ST101's guarantee is that a bare
    # `--help` invocation still works. This branch is never True at runtime
    # (only mypy sees it), so it cannot reintroduce that failure.
    import duckdb

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


def _sweep_cell(
    conn: duckdb.DuckDBPyConnection,
    *,
    strategy: str,
    symbol: str,
    timeframe: str,
    day_filter: str,
    fee_pct: float,
    min_sl_pct: float,
    slippage_pct: float,
    since_ms: int,
) -> ParamSweepReport:
    """The one ``run_param_sweep`` call every per-cell path in this file makes.

    Previously duplicated byte-identically between the resweep loop and the
    §4b calibration loop (``wfo_split=0.7`` and ``top_n=20`` literals
    included): change one copy's value and not the other, and the
    calibration would silently measure a DIFFERENT book from the one the
    resweep decides on, with no test able to catch it — both call sites
    were internally consistent, which is exactly how the ``|t| >= 1.96`` vs
    ``2.802`` divergence this file's own module docstring names went
    unnoticed at ST28's sixth site.
    """
    return run_param_sweep(
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


_ROUTE_TO_ST133_STOP_MESSAGE = (
    "  ⛔ STOP. This does NOT license 'the gate was right' — failing to\n"
    "     license a correction is a different claim from the refusals\n"
    "     being correct. Route to ST133 on the existing evidence."
)
"""Shared between §4a (`--measure-rho`) and §4b (`--null-calibration`) — both
kill-switches route the same way on a non-PROCEED verdict, and a second copy
of the string is exactly how "same voice" silently drifts to "same voice,
until someone edits one and not the other"."""


def median_rho_ci(
    rhos: list[float],
    *,
    n_boot: int = 10_000,
    seed: int = DEFAULT_SEED,
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


# ---------------------------------------------------------------------------
# ST134 section 4b — null calibration runner
#
# Task 7 (`tools/st134_null_calibration.py`) built the sign-flip null and its
# pass-rate calibration but nothing on the branch called it, so its only
# numbers lived in a gitignored scratch script — the exact pattern AGENTS.md
# names by name at ST28's sixth site. This section gives it a tracked caller.
#
# ⛔ Decides nothing, writes no TOML, and does not run the 273 cells: it draws
# a stratified SAMPLE and stops there. Reachable only via `--null-calibration`.
# ---------------------------------------------------------------------------

_NULL_CALIBRATION_K = 30
_NULL_CALIBRATION_SEED = DEFAULT_SEED
_NULL_CALIBRATION_REPLICATES = DEFAULT_REPLICATES
_NULL_CALIBRATION_SPLITS = DEFAULT_N_SPLITS
"""Aliases, NOT second copies — the same relationship ``DSR_THRESHOLD = GATE_DSR``
already has in :mod:`analytics.sweep_guard`.

All three restated a literal until ST134's fix wave, and ``n_splits`` was the one that
bit: it sets ``min_obs = MIN_OBS_FACTOR * n_splits``, which sets BOTH the bin count rho
is measured over and the effective-observation floor. Move ``DEFAULT_N_SPLITS`` and the
calibration would have gone on measuring a different book from the gate it calibrates,
each half internally consistent and no test able to fail — the ``|t| >= 1.96`` versus
``2.802`` shape this file's own module docstring names.
"""

_NULL_CALIBRATION_BAR = 0.10
_NOMINAL_NULL_RATE = 0.05
# "Far below 5%" (spec §4b) is not itself a pre-registered number -- this file
# reads it as under half the nominal rate, stated once here rather than as a
# silent literal in the print statement that uses it.
_FAR_BELOW_NOMINAL_FACTOR = 0.5
_POOLING_DESCRIPTION = (
    "replicate-weighted: total passes / total evaluated across sampled "
    "cells, per arm -- never an unweighted per-cell average"
)
"""Named once so the artifact can carry the rule rather than only its output —
a reader of the JSON alone has no other way to tell which of the two
defensible poolings (this one, or a plain per-cell average) produced the
number on the page."""


@dataclass(frozen=True)
class ResweepCell:
    """One `(config, day_filter, strategy, timeframe, symbol)` candidate for
    the §4b draw.

    `day_filter` is carried alongside the config path because sampling
    happens *before* any sweep runs, and `run_param_sweep` needs it —
    recomputing it later would mean re-opening and re-parsing the TOML the
    sample was drawn from.
    """

    config_path: str
    day_filter: str
    strategy: str
    timeframe: str
    symbol: str


def stratified_cell_sample(
    cells: Sequence[ResweepCell],
    *,
    k: int = _NULL_CALIBRATION_K,
    seed: int = _NULL_CALIBRATION_SEED,
) -> list[ResweepCell]:
    """Deterministic draw of ``k`` cells, stratified by timeframe.

    ST134 §4b: the live ledger is 64.4% 15m, so an unstratified draw of ``k``
    would be dominated by one timeframe. Quotas are apportioned by largest
    remainder (Hamilton's method) so per-timeframe counts sum to exactly
    ``k`` — independent per-stratum rounding would not.

    A population no larger than ``k`` is returned WHOLE (sorted, so the
    artifact is stable), which is the caller's own signal — by comparing
    lengths — to report "used everything" rather than "sampled".
    """
    ordered = sorted(
        cells, key=lambda c: (c.timeframe, c.strategy, c.symbol, c.config_path)
    )
    if len(ordered) <= k:
        return ordered

    by_tf: dict[str, list[ResweepCell]] = {}
    for cell in ordered:
        by_tf.setdefault(cell.timeframe, []).append(cell)

    total = len(ordered)
    quotas = {tf: k * len(group) / total for tf, group in by_tf.items()}
    base = {tf: int(q) for tf, q in quotas.items()}  # floor; every quota >= 0
    remaining = k - sum(base.values())
    # Largest-remainder order. Cycling through it (rather than taking a single
    # pass) skips a stratum already at its own population cap instead of
    # stalling — guaranteed to terminate because `total > k` here (the
    # smaller-population case already returned above), so some stratum always
    # has spare capacity somewhere in the cycle.
    order = sorted(by_tf, key=lambda tf: quotas[tf] - base[tf], reverse=True)
    pos = 0
    while remaining > 0:
        tf = order[pos % len(order)]
        if base[tf] < len(by_tf[tf]):
            base[tf] += 1
            remaining -= 1
        pos += 1

    rng = np.random.default_rng(seed)
    sample: list[ResweepCell] = []
    for tf in sorted(by_tf):
        group = by_tf[tf]
        n = base[tf]
        if n <= 0:
            continue
        idx = rng.choice(len(group), size=n, replace=False)
        sample.extend(group[i] for i in sorted(idx.tolist()))
    return sample


def null_calibration_verdict(
    corrected_rate: float,
    uncorrected_rate: float,
    *,
    bar: float = _NULL_CALIBRATION_BAR,
) -> str:
    """ST134 §4b's pre-committed bar: ``"PROCEED"`` at or below it, else ``"ABANDON"``.

    ``uncorrected_rate`` gates nothing here — §4b's decision rule is the
    corrected arm against the bar alone; the uncorrected arm is disclosed
    beside it (a rate far below the nominal 5% is evidence the uncorrected
    gate is over-conservative), never folded into this verdict as a second
    condition. It stays a required parameter so a caller cannot obtain a
    verdict without having already computed both numbers.

    NaN (nothing evaluated — every null replicate skipped, or fewer than two
    trials) reads as ``"ABANDON"``: proceeding on no evidence is exactly the
    licence this control exists to withhold.
    """
    if math.isnan(corrected_rate):
        return "ABANDON"
    return "ABANDON" if corrected_rate > bar else "PROCEED"


def _both_null_calibration_arms(
    trials: Sequence[TrialPerf],
    *,
    seed: int = _NULL_CALIBRATION_SEED,
    n_replicates: int = _NULL_CALIBRATION_REPLICATES,
    n_splits: int = _NULL_CALIBRATION_SPLITS,
) -> tuple[NullCalibrationResult, NullCalibrationResult, NullCalibrationResult]:
    """Corrected, uncorrected and BOTH §4b results for one cell's trial family.

    Requirement 3: every arm runs on the SAME nulls — same seed, same family.
    ``null_pass_rate`` computes ``rho_after`` before branching on
    ``correct_trials``/``correct_obs``, so with a shared seed all three returned
    results carry IDENTICAL ``rho_after`` whenever anything was evaluated; that
    identity is the observable proof the seed was actually shared, not merely
    passed.

    I1: ``corrected`` here means TRIALS-corrected only (``correct_obs=False``) —
    it was the only corrected arm calibrated before ST134's C1 fed
    ``correct_obs`` into the MinTRL leg too, so it is no longer "the gate as
    shipped". ``both`` (``correct_trials=True, correct_obs=True``) IS the gate
    as shipped and is the one this control's headline verdict should ultimately
    answer for. It is reported here rather than substituted for ``corrected``
    because correction (b) (day-clustering the observation count) only ever
    LOWERS the deflated Sharpe relative to the trials-only book: it can turn a
    trials-only PASS into a ``both`` FAIL, never the reverse, in the region a
    null could pass at all. So the trials-only pass rate is an upper bound on
    the ``both`` pass rate, and a corrected-arm PROCEED already implies
    ``both`` would PROCEED too — ``ARM_CORRECTIONS`` names which correction
    each of the three arms actually applies, so a reader is never left
    inferring it from this docstring alone.
    """
    corrected = null_pass_rate(
        trials,
        correct_trials=True,
        correct_obs=False,
        n_replicates=n_replicates,
        n_splits=n_splits,
        seed=seed,
    )
    uncorrected = null_pass_rate(
        trials,
        correct_trials=False,
        correct_obs=False,
        n_replicates=n_replicates,
        n_splits=n_splits,
        seed=seed,
    )
    both = null_pass_rate(
        trials,
        correct_trials=True,
        correct_obs=True,
        n_replicates=n_replicates,
        n_splits=n_splits,
        seed=seed,
    )
    return corrected, uncorrected, both


def _pool_null_results(
    results: Sequence[NullCalibrationResult],
) -> NullCalibrationResult:
    """Pool per-cell §4b results into one arm-level record.

    ``rate`` pools by REPLICATE — total passes over total evaluated across
    every cell — because "the corrected gate must pass <= 10% of null
    replicates" is a statement about the pooled population of null draws, not
    an average of per-cell rates that would let a handful of thin cells
    outvote the bulk of evaluated replicates. ``passes`` is recovered as
    ``round(rate * evaluated)`` since :class:`NullCalibrationResult` does not
    carry it directly; both operands were exact integers before division, so
    the round-trip through the float ``rate`` is exact bar the last ULP,
    which ``round()`` absorbs.

    ``rho_before`` / ``rho_after`` are the plain mean over cells that
    produced a value (NaN excluded) — a description of the sampled
    population's typical correlation, not a replicate-weighted quantity.

    Pools to ``rate=NaN`` when every cell had ``evaluated == 0``, matching
    ``null_pass_rate``'s own "nothing could be evaluated" convention.
    """
    evaluated = sum(r.evaluated for r in results)
    skipped = sum(r.skipped for r in results)
    passes = sum(
        round(r.rate * r.evaluated)
        for r in results
        if r.evaluated and not math.isnan(r.rate)
    )
    rate = passes / evaluated if evaluated else math.nan
    before = [r.rho_before for r in results if not math.isnan(r.rho_before)]
    after = [r.rho_after for r in results if not math.isnan(r.rho_after)]
    rho_before = sum(before) / len(before) if before else math.nan
    rho_after = sum(after) / len(after) if after else math.nan
    return NullCalibrationResult(rate, evaluated, skipped, rho_before, rho_after)


def _result_to_json(result: NullCalibrationResult) -> dict[str, float | int | None]:
    """NaN is not valid JSON; every NaN-able field becomes ``null`` on write.

    That is the same meaning ``null`` already carries on this file's other
    optional numeric fields (e.g. ``CellVerdict.winner_tp_r``), so a reader
    does not need a second convention for "nothing here" depending on which
    field they are looking at.
    """

    def nn(value: float) -> float | None:
        return None if math.isnan(value) else value

    return {
        "rate": nn(result.rate),
        "evaluated": result.evaluated,
        "skipped": result.skipped,
        "rho_before": nn(result.rho_before),
        "rho_after": nn(result.rho_after),
    }


def _far_below_nominal(
    rate: float,
    *,
    nominal: float = _NOMINAL_NULL_RATE,
    factor: float = _FAR_BELOW_NOMINAL_FACTOR,
) -> tuple[bool | None, str]:
    """Whether the (pooled) uncorrected rate sits far below the nominal null rate.

    Returns ``(None, ...)`` on NaN rather than ``False`` — "not clearly
    below" is a claim about a comparison, and no comparison was made when
    nothing was evaluated. The returned line carries the actual numbers
    compared and states plainly that the threshold is this run's reading
    rather than a pre-registered bar, so the disclaimer travels with the
    number into both stdout and the JSON artifact instead of living only in
    a source comment nobody reading the output can see.
    """
    threshold = nominal * factor
    if math.isnan(rate):
        return None, (
            f"uncorrected rate is NaN (nothing evaluated) — the far-below "
            f"comparison against {threshold:.4f} is undetermined"
        )
    far_below = rate < threshold
    cmp_symbol = "<" if far_below else ">="
    return far_below, (
        f"uncorrected {rate:.4f} {cmp_symbol} {threshold:.4f} "
        f"({factor:.0%} of the nominal {nominal:.0%} — this run's reading, "
        f"not a pre-registered bar)"
    )


def _null_calibration_out_path(label: str) -> Path:
    """§4b artifact path — under ``docs/plans/scratch/``, formatted from ``--label``.

    ST128's run JSON went to ``/tmp`` and is gone, and its central claim can
    no longer be verified — the reason requirement 6 exists at all.
    """
    return Path(f"docs/plans/scratch/st134-null-calibration-{label}.json")


_CalibrateOneCellResult = tuple[
    dict[str, Any],
    NullCalibrationResult | None,
    NullCalibrationResult | None,
    NullCalibrationResult | None,
]


def _calibrate_one_cell(
    conn: duckdb.DuckDBPyConnection,
    cell: ResweepCell,
    *,
    fee_pct: float,
    min_sl_pct: float,
    slippage_pct: float,
    since_ms: int,
    seed: int,
    n_replicates: int,
    n_splits: int,
) -> _CalibrateOneCellResult:
    """One sampled cell's §4b record, plus its three results for pooling.

    Always returns a record — including on failure. The record used to be
    dropped on a dead cell (``print(...); continue``), which meant
    ``k_drawn`` in the artifact counted cells the JSON carried no trace of:
    a reader saw e.g. ``"k_drawn": 30`` against 27 cells, with no record of
    which three failed or why. Returns ``(record, None, None, None)`` on
    failure so the caller can tell a dead cell from a scored one without
    inspecting the record's shape.

    I4: the ``try`` wraps the WHOLE body, not just the ``_sweep_cell`` call —
    ``_row_to_trialperf`` and ``_both_null_calibration_arms`` both reach
    ``probabilistic_sharpe_ratio``, which raises ``ValueError`` on
    ``n_obs < 2`` or degenerate higher moments, reachable from a one-trade
    arm. Before this fix a raise there was UNCAUGHT and killed the whole
    §4b run rather than just this cell.
    """
    base: dict[str, Any] = {
        "config": cell.config_path,
        "day_filter": cell.day_filter,
        "strategy": cell.strategy,
        "timeframe": cell.timeframe,
        "symbol": cell.symbol,
    }
    try:
        report = _sweep_cell(
            conn,
            strategy=cell.strategy,
            symbol=cell.symbol,
            timeframe=cell.timeframe,
            day_filter=cell.day_filter,
            fee_pct=fee_pct,
            min_sl_pct=min_sl_pct,
            slippage_pct=slippage_pct,
            since_ms=since_ms,
        )
        trials = [_row_to_trialperf(r) for r in report.all_rows]
        corrected, uncorrected, both = _both_null_calibration_arms(
            trials, seed=seed, n_replicates=n_replicates, n_splits=n_splits
        )
    except Exception as exc:  # a dead cell must not kill the run
        print(f"  !! {cell.strategy:22} {cell.timeframe:4} {cell.symbol:8} ERROR {exc}")
        return (
            {
                **base,
                "n_arms": None,
                "error": repr(exc),
                "corrected": None,
                "uncorrected": None,
                "both": None,
                "corrections": ARM_CORRECTIONS,
            },
            None,
            None,
            None,
        )

    print(
        f"  {cell.strategy:22} {cell.timeframe:4} {cell.symbol:8} arms={len(trials):3} "
        f"corrected rate={_fmt(corrected.rate, '.4f')} "
        f"(n={corrected.evaluated}/{corrected.evaluated + corrected.skipped})  "
        f"uncorrected rate={_fmt(uncorrected.rate, '.4f')} "
        f"(n={uncorrected.evaluated}/{uncorrected.evaluated + uncorrected.skipped})  "
        f"both rate={_fmt(both.rate, '.4f')} "
        f"(n={both.evaluated}/{both.evaluated + both.skipped})"
    )
    record = {
        **base,
        "n_arms": len(trials),
        "error": None,
        "corrected": _result_to_json(corrected),
        "uncorrected": _result_to_json(uncorrected),
        "both": _result_to_json(both),
        "corrections": ARM_CORRECTIONS,
    }
    return record, corrected, uncorrected, both


def _run_null_calibration(
    conn: duckdb.DuckDBPyConnection,
    *,
    configs: list[Path],
    symbols: tuple[str, ...],
    fee_pct: float,
    min_sl_pct: float,
    slippage_pct: float,
    since_ms: int,
    label: str,
    k: int = _NULL_CALIBRATION_K,
    seed: int = _NULL_CALIBRATION_SEED,
    n_replicates: int = _NULL_CALIBRATION_REPLICATES,
    n_splits: int = _NULL_CALIBRATION_SPLITS,
) -> None:
    """ST134 §4b: sample, run the null on the sample, report, write the artifact.

    Read-only and additive: writes no TOML, decides nothing, and — unlike the
    per-config loop in ``main`` — never touches more than ``k`` cells.
    ``--out`` / ``--measure-rho`` / ``--books`` are ignored in this mode.
    """
    print("  (--out / --measure-rho / --books are ignored in --null-calibration mode)")

    candidate_population: list[ResweepCell] = []
    for config_path in configs:
        with config_path.open("rb") as fh:
            config = tomllib.load(fh)
        day_filter = config.get("day_filter", "off")
        candidate_population.extend(
            ResweepCell(str(config_path), day_filter, strategy, timeframe, symbol)
            for strategy, timeframe, symbol in cells_for_config(config, symbols)
        )

    print(
        f"\nST134 §4b — null calibration: {len(candidate_population)} candidate cell(s)"
    )
    if len(candidate_population) <= k:
        print(
            f"  candidate population <= k={k}; using all "
            f"{len(candidate_population)} cells, not a sample"
        )
    sample = stratified_cell_sample(candidate_population, k=k, seed=seed)
    composition = dict(sorted(Counter(c.timeframe for c in sample).items()))
    print(
        f"  drew {len(sample)} cell(s)  composition (timeframe -> count): {composition}"
    )

    out_path = _null_calibration_out_path(label)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    def _write_artifact(
        per_cell: list[dict[str, Any]],
        correcteds: list[NullCalibrationResult],
        uncorrecteds: list[NullCalibrationResult],
        boths: list[NullCalibrationResult],
        *,
        complete: bool,
    ) -> tuple[
        str, NullCalibrationResult, NullCalibrationResult, NullCalibrationResult
    ]:
        """Pool, print (only when ``complete``) and write — shared by the I4
        per-cell checkpoint and the final write, so the two can never drift
        into two different artifact shapes."""
        pooled_corrected = _pool_null_results(correcteds)
        pooled_uncorrected = _pool_null_results(uncorrecteds)
        pooled_both = _pool_null_results(boths)
        verdict = null_calibration_verdict(
            pooled_corrected.rate, pooled_uncorrected.rate
        )
        uncorrected_far_below, far_below_line = _far_below_nominal(
            pooled_uncorrected.rate
        )
        out_path.write_text(
            json.dumps(
                {
                    "label": label,
                    "seed": seed,
                    "n_replicates": n_replicates,
                    "n_splits": n_splits,
                    "k_requested": k,
                    "k_drawn": len(sample),
                    "candidate_population_size": len(candidate_population),
                    "composition": composition,
                    "bar": _NULL_CALIBRATION_BAR,
                    "nominal_null_rate": _NOMINAL_NULL_RATE,
                    "far_below_factor": _FAR_BELOW_NOMINAL_FACTOR,
                    "uncorrected_far_below_nominal": uncorrected_far_below,
                    "pooling": _POOLING_DESCRIPTION,
                    "corrections": ARM_CORRECTIONS,
                    "corrected": _result_to_json(pooled_corrected),
                    "uncorrected": _result_to_json(pooled_uncorrected),
                    "both": _result_to_json(pooled_both),
                    "verdict": verdict,
                    # I4: a crash mid-loop still leaves every cell scored so far
                    # on disk — `complete` distinguishes a checkpoint from the
                    # final write for a reader inspecting the file mid-run.
                    "complete": complete,
                    "cells": per_cell,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return verdict, pooled_corrected, pooled_uncorrected, pooled_both

    per_cell: list[dict[str, Any]] = []
    correcteds: list[NullCalibrationResult] = []
    uncorrecteds: list[NullCalibrationResult] = []
    boths: list[NullCalibrationResult] = []
    for cell in sample:
        record, corrected, uncorrected, both = _calibrate_one_cell(
            conn,
            cell,
            fee_pct=fee_pct,
            min_sl_pct=min_sl_pct,
            slippage_pct=slippage_pct,
            since_ms=since_ms,
            seed=seed,
            n_replicates=n_replicates,
            n_splits=n_splits,
        )
        per_cell.append(record)
        if corrected is not None and uncorrected is not None and both is not None:
            correcteds.append(corrected)
            uncorrecteds.append(uncorrected)
            boths.append(both)
        # I4: checkpoint after EVERY cell, not just at the end — a crash on
        # cell 27 of 30 used to discard everything, since the only write sat
        # after the loop. Cheap at k<=30: a full JSON rewrite per iteration.
        _write_artifact(per_cell, correcteds, uncorrecteds, boths, complete=False)

    verdict, pooled_corrected, pooled_uncorrected, pooled_both = _write_artifact(
        per_cell, correcteds, uncorrecteds, boths, complete=True
    )
    _, far_below_line = _far_below_nominal(pooled_uncorrected.rate)

    print(f"\n{'=' * 78}")
    print(
        f"ST134 §4b — {len(correcteds)} of {len(sample)} sampled cell(s) completed "
        f"(candidate population {len(candidate_population)}, requested k={k}, "
        f"seed={seed})"
    )
    print(
        f"  corrected    rate {_fmt(pooled_corrected.rate, '.4f')}  "
        f"evaluated {pooled_corrected.evaluated}  skipped {pooled_corrected.skipped}  "
        f"rho_before {_fmt(pooled_corrected.rho_before)}  "
        f"rho_after {_fmt(pooled_corrected.rho_after)}"
    )
    print(
        f"  uncorrected  rate {_fmt(pooled_uncorrected.rate, '.4f')}  "
        f"evaluated {pooled_uncorrected.evaluated}  skipped {pooled_uncorrected.skipped}  "
        f"rho_before {_fmt(pooled_uncorrected.rho_before)}  "
        f"rho_after {_fmt(pooled_uncorrected.rho_after)}"
    )
    # I1: `both` (trials AND obs corrected) is the gate as actually shipped
    # since C1. `corrected` above is trials-only and an upper bound on this
    # rate — see `_both_null_calibration_arms`'s docstring for why that
    # licenses reading a `corrected` PROCEED as implying a `both` PROCEED.
    print(
        f"  both         rate {_fmt(pooled_both.rate, '.4f')}  "
        f"evaluated {pooled_both.evaluated}  skipped {pooled_both.skipped}  "
        f"rho_before {_fmt(pooled_both.rho_before)}  "
        f"rho_after {_fmt(pooled_both.rho_after)}"
    )
    print(f"  {far_below_line}")
    print(f"  VERDICT: {verdict}  (bar: corrected rate <= {_NULL_CALIBRATION_BAR:.0%})")
    if verdict != "PROCEED":
        print(_ROUTE_TO_ST133_STOP_MESSAGE)
    print(f"per-cell JSON: {out_path}")


def _population_rho(
    report: ParamSweepReport, *, n_splits: int = DEFAULT_N_SPLITS
) -> tuple[float | None, float | None]:
    """ST134 §4a's rho, measured on the PRE-REGISTERED population.

    I2: §4a says "measure rho across arms on all 273 cells" and its own warning
    names the decision statistic as the distribution across all 273 — never
    conditioned on whether the gate happened to reach the correlation step.
    Reading `verdict.rho` instead (populated only when a corrected book was
    computed) restricts the population to the ~82 cells that clear the trade-count
    floor, which is exactly the selection the kill-switch must not carry.

    Computed DIRECTLY rather than through `_compute_sweep_gate` /
    `evaluate_commit_gate`: `_effective_trial_count` only needs the binned
    performance matrix, never PBO's CSCV or DSR's variance term, so this costs no
    extra backtests and is safe to run UNCONDITIONALLY on every cell — including
    the 191 that never reach a scoreable gate verdict. Reads `report.all_rows` —
    the full grid, never the top-N `rows` truncation — mirroring the gate's own
    re-score (`TestMeasureRhoRescoresTheFullGrid`).

    Returns ``(None, None)`` when fewer than 2 arms carry any trade at all —
    nothing pairwise to correlate. `nan` rho (measured, but no estimable pair —
    see `_mean_arm_correlation`) is normalised to `None` here too, so a reader of
    the JSON has ONE "nothing to report" spelling rather than two.
    """
    trials = [_row_to_trialperf(r) for r in report.all_rows]
    if len(trials) < 2:
        return None, None
    perf = _build_perf_matrix(trials, MIN_OBS_FACTOR * n_splits)
    rho, n_trials_eff = _effective_trial_count(perf)
    return (None if math.isnan(rho) else rho), n_trials_eff


def _ledger_has_alert_table(conn: duckdb.DuckDBPyConnection) -> bool:
    """I5: probe once, before the loop, rather than let a missing ledger table
    kill the whole run from inside it.

    `alerts_per_week` queries `signal_alert_outcomes` unconditionally and raises a
    DuckDB `CatalogException` when the table does not exist. That call sat outside
    the per-cell `try`/`except` in `main`'s loop, so a database lacking the ledger
    raised on cell one and discarded every cell after it — total blast radius for a
    guard that costs one query. Production always has the ledger (this is latent,
    not observed), but the fix is cheap enough not to wait for it to bite.
    """
    try:
        conn.execute("SELECT 1 FROM signal_alert_outcomes LIMIT 0")
        return True
    except Exception:
        return False


_MISSING_LEDGER_EXPOSURE = AlertExposure(
    rate=math.nan, window_start_ms=0, window_days=0.0, first_fired_ms=None
)
"""I5's degraded reading when `signal_alert_outcomes` is absent — `nan` rather than
``0.0`` so a reader cannot mistake "the ledger table does not exist" for "this cell
never fires live", which is a different claim reached the same way `NullCalibrationResult`
already keeps `evaluated` and `rate` apart."""


def _format_out_path(template: str, label: str) -> Path | None:
    """Resolve ``--out``'s ``{label}`` template, or ``None`` on a bad template.

    Minor fix: a user-supplied ``--out`` path containing a stray, unescaped
    ``{`` (e.g. a shell glob typo) raised an uncaught ``KeyError`` straight out
    of ``str.format`` and crashed the run AFTER every cell had already been
    swept — the JSON write is a nicety at the end of a multi-minute run, not a
    precondition for it, so this degrades to a printed message and a
    non-writing exit rather than losing the console output the run already
    produced.
    """
    try:
        return Path(template.format(label=label))
    except KeyError as exc:
        print(
            f"  !! --out {template!r} is not a valid format string ({exc}) — "
            "skipping the JSON write. Escape a literal '{' as '{{' if that "
            "was intended."
        )
        return None


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
    parser.add_argument(
        "--null-calibration",
        action="store_true",
        help=(
            "ST134 section 4b kill-switch: draw a stratified sample of cells "
            "(never the full 273), run the corrected and uncorrected commit gate "
            "against a shared sign-flip null family for each, pool the pass rate "
            "across the sample, and report it against the pre-committed 10%% bar. "
            "STOPS after reporting -- the standard per-config sweep loop below "
            "does not run at all in this mode, and --out / --measure-rho / --books "
            "are ignored. Writes no TOML and decides nothing."
        ),
    )
    parser.add_argument(
        "--null-calibration-k",
        type=int,
        default=_NULL_CALIBRATION_K,
        help=(
            "Override the --null-calibration sample size (default "
            f"{_NULL_CALIBRATION_K}). Minor fix: a SMOKE run (e.g. --k 2 "
            "--n-replicates 5) exercises the whole per-cell/pooling/artifact "
            "path in seconds, so the first real k=30/replicates=200 invocation "
            "is not also that path's first end-to-end execution."
        ),
    )
    parser.add_argument(
        "--null-calibration-replicates",
        type=int,
        default=_NULL_CALIBRATION_REPLICATES,
        help=(
            "Override the --null-calibration replicate count per cell "
            f"(default {_NULL_CALIBRATION_REPLICATES}). See --null-calibration-k."
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
    try:
        if args.null_calibration:
            # Requirement 1: opt-in, and the standard per-config loop below
            # never executes in this mode — the early `return` is what makes
            # "does not run the 273 cells" true rather than merely intended.
            _run_null_calibration(
                conn,
                configs=configs,
                symbols=symbols,
                fee_pct=fee_pct,
                min_sl_pct=min_sl_pct,
                slippage_pct=slippage_pct,
                since_ms=since_ms,
                label=args.label,
                k=args.null_calibration_k,
                n_replicates=args.null_calibration_replicates,
            )
            return 0

        has_ledger = _ledger_has_alert_table(conn)
        if not has_ledger:
            print(
                "  ⚠ health note: signal_alert_outcomes is absent from this DB — "
                "live_alerts_per_week reports NaN for every cell rather than 0.0"
            )

        results: list[dict[str, Any]] = []
        started = time.time()
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
                    report = _sweep_cell(
                        conn,
                        strategy=strategy,
                        symbol=symbol,
                        timeframe=timeframe,
                        day_filter=day_filter,
                        fee_pct=fee_pct,
                        min_sl_pct=min_sl_pct,
                        slippage_pct=slippage_pct,
                        since_ms=since_ms,
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
                # C2: `--books` alone (or with `--measure-rho`) is the ONLY path
                # that pays the full 2x2's CSCV cost — one call per book, four
                # books. `--measure-rho` ALONE computes just the ONE book its own
                # kill-switch summary used to read (`trials_corrected`), and
                # `_population_rho` below covers the rho/n_trials_eff numbers on
                # the pre-registered population without going through the gate at
                # all. Before this fix `book_verdicts` computed all four books
                # under EITHER flag and their decision/dsr/pbo always ended up in
                # the "books" artifact field — the step-3 (§6) corrected re-run
                # result the §4a/§4b kill-switches exist to gate, landing on disk
                # whatever the kill-switch verdict turned out to be.
                if args.books:
                    book_verdicts = {
                        name: _compute_sweep_gate(
                            report.all_rows,
                            chosen_row,
                            report.n_grid,
                            correct_trials=ct,
                            correct_obs=co,
                        )
                        for name, ct, co in BOOKS
                    }
                elif args.measure_rho:
                    book_verdicts = {
                        "trials_corrected": _compute_sweep_gate(
                            report.all_rows,
                            chosen_row,
                            report.n_grid,
                            correct_trials=True,
                            correct_obs=False,
                        )
                    }
                else:
                    book_verdicts = None

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
                # Rider (Task 10 review): the window is emitted alongside the
                # rate rather than only the rate, so a reader can tell "thin
                # exposure" from "the ledger's history starts after --since"
                # without re-deriving it — see `alerts_per_week`'s docstring.
                # I5: probed once before the loop rather than per cell — a
                # missing ledger table degrades to NaN instead of killing the run.
                exposure = (
                    alerts_per_week(
                        conn,
                        symbol=symbol,
                        timeframe=timeframe,
                        strategy=strategy,
                        since_ms=since_ms,
                        now_ms=now_ms,
                    )
                    if has_ledger
                    else _MISSING_LEDGER_EXPOSURE
                )
                # I2: unconditional and cheap (no CSCV) — the §4a population is
                # every cell reaching this point, never conditioned on `--books` /
                # `--measure-rho` or on gate scoreability.
                population_rho, population_n_trials_eff = _population_rho(report)
                results.append(
                    {
                        "config": str(config_path),
                        "day_filter": day_filter,
                        "strategy": strategy,
                        "timeframe": timeframe,
                        "symbol": symbol,
                        "label": args.label,
                        **asdict(verdict),
                        "population_rho": population_rho,
                        "population_n_trials_eff": population_n_trials_eff,
                        # C2: the corrected 2x2 (decision/dsr/pbo per book) is the
                        # step-3 result the §4a/§4b kill-switches gate — write it
                        # only when `--books` was explicitly asked for, never as a
                        # side effect of `--measure-rho` alone.
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
                            if args.books and book_verdicts is not None
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
                        "live_alerts_per_week": exposure.rate,
                        "live_alerts_window_start_ms": exposure.window_start_ms,
                        "live_alerts_window_days": exposure.window_days,
                        "live_alerts_first_fired_ms": exposure.first_fired_ms,
                        # Needs "raw" and "obs_corrected" both present, so this is
                        # `args.books`-gated too, never merely `book_verdicts is not
                        # None` (true, but with only "trials_corrected" present,
                        # under `--measure-rho` alone).
                        "moved_to_insufficient_under_obs_correction": (
                            None
                            if not (args.books and book_verdicts is not None)
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
        # I2: the PRE-REGISTERED population — every cell with >= 2 arms
        # carrying a trade, never conditioned on gate scoreability. Reading
        # `r["rho"]` (populated only when `book_verdicts` reached the
        # correlation step) would restrict this to the ~82 scoreable cells,
        # exactly the selection §4a's kill-switch must not carry.
        rhos = [
            r["population_rho"] for r in results if r.get("population_rho") is not None
        ]
        med, lo, hi = median_rho_ci(rhos)
        rho_outcome = rho_verdict(lo, hi)
        print(
            f"\nST134 §4a — arm correlation across {len(rhos)} of {total} cells "
            "(pre-registered population: >=2 arms carrying a trade)"
        )
        print(f"  median rho {med:.4f}   95% CI [{lo:.4f}, {hi:.4f}]   bar 0.50")
        print(f"  VERDICT: {rho_outcome}")
        if rho_outcome != "PROCEED":
            print(_ROUTE_TO_ST133_STOP_MESSAGE)

    if args.books:
        # I7: at k=9, n_trials_eff floors to MIN_EFFECTIVE_TRIALS at rho ~=
        # 0.4375 — BELOW §4a's own rho* = 0.5 bar — so any family clearing
        # that bar has already floor-bound. Counted here so a reader does
        # not have to re-derive it from the per-cell "books" block by hand.
        floor_bound = sum(
            1
            for r in results
            if r.get("books") is not None
            and r["books"]["trials_corrected"]["n_trials_eff"] is not None
            and r["books"]["trials_corrected"]["n_trials_eff"] <= MIN_EFFECTIVE_TRIALS
        )
        moved_to_insufficient = sum(
            1 for r in results if r.get("moved_to_insufficient_under_obs_correction")
        )
        print(f"\nST134 §5 — --books summary across {total} cells")
        print(
            f"  n_trials_eff FLOOR-BOUND (<= {MIN_EFFECTIVE_TRIALS:g}) under "
            f"trials_corrected: {floor_bound}"
        )
        print(
            "  moved scoreable -> INSUFFICIENT under obs correction: "
            f"{moved_to_insufficient}"
        )

    if args.out:
        out_path = _format_out_path(args.out, args.label)
        if out_path is None:
            return 1
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"per-cell JSON: {out_path}")
    return 0


if __name__ == "__main__":
    from utils.stdio import utf8_stdio

    utf8_stdio()
    raise SystemExit(main())
