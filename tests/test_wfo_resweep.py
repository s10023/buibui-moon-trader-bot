"""ST128 — the corrected re-sweep driver implements the PRE-REGISTERED rule.

The rule is stated as prose in
`docs/superpowers/specs/2026-09-10-st128-wfo-resweep-preregistration.md` §2 and as
code in `tools/wfo_resweep.py::decide_cell`. ST28's sixth powered-null site is the
reason both exist and the reason this file does: its spec said `|t| >= 2.802` and
its driver did `|t| >= 1.96`, and because each half was internally consistent no
gate could catch the divergence. So these tests assert the CONSTANTS against the
spec's numbers and the BEHAVIOUR against the spec's ordering, not merely that the
function returns something.
"""

from __future__ import annotations

import math
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from analytics.sweep_guard import (
    DECISION_BLOCK,
    DECISION_COMMIT,
    DECISION_INSUFFICIENT,
    MIN_OBS_FACTOR,
    CommitGateVerdict,
    TrialPerf,
    _build_perf_matrix,
    _effective_trial_count,
)
from tools.st134_null_calibration import ARM_CORRECTIONS, NullCalibrationResult
from tools.wfo_resweep import (
    ACTION_KEEP,
    ACTION_SKIP,
    ACTION_UPDATE,
    BOOKS,
    MIN_IMPROVEMENT_R,
    MIN_OOS_TRADES,
    MIN_TP_R_STEP,
    ResweepCell,
    _both_null_calibration_arms,
    _calibrate_one_cell,
    _far_below_nominal,
    _format_out_path,
    _ledger_has_alert_table,
    _null_calibration_out_path,
    _pool_null_results,
    _population_rho,
    _result_to_json,
    cells_for_config,
    current_tp_r_for,
    decide_cell,
    effect_size,
    median_rho_ci,
    null_calibration_verdict,
    rho_verdict,
    stratified_cell_sample,
)

_MS_PER_DAY = 86_400_000


def _trial_family(k: int = 6, n: int = 80, drift: float = 0.30) -> list[TrialPerf]:
    """Mirrors `tests/test_st134_null_calibration.py::_family` — a family with
    real dispersion but no genuine edge, correlated enough that the corrected
    and uncorrected gates both refuse most nulls."""
    rng = np.random.default_rng(4242)
    base = rng.normal(loc=drift, scale=1.0, size=n)
    times = [i * _MS_PER_DAY for i in range(n)]
    return [TrialPerf(f"tp{j}", list(base + 0.02 * j), times) for j in range(k)]


def _underdeflated_trial_family(
    k: int = 30,
    n: int = 200,
    drift: float = 0.1,
    idio_scale: float = 0.5,
    seed: int = 7,
) -> list[TrialPerf]:
    """Mirrors `tests/test_st134_null_calibration.py::_underdeflated_family` — a
    positive control where the corrected gate is measurably OVER-permissive, so
    a test asserting the two arms can diverge is not just asserting a stub."""
    rng = np.random.default_rng(seed)
    base = rng.normal(loc=drift, scale=1.0, size=n)
    times = [i * _MS_PER_DAY for i in range(n)]
    return [
        TrialPerf(f"tp{j}", list(base + rng.normal(scale=idio_scale, size=n)), times)
        for j in range(k)
    ]


def _cell(tf: str, strategy: str = "bos", symbol: str = "BTCUSDT") -> ResweepCell:
    return ResweepCell("config/signal_watch.toml", "off", strategy, tf, symbol)


_SPEC = Path("docs/superpowers/specs/2026-09-10-st128-wfo-resweep-preregistration.md")


@dataclass
class _Row:
    """Minimal stand-in for SweepRow — only what `decide_cell` reads."""

    tp_r: float
    oos: float | None
    n: int
    overfit: bool = False

    @property
    def params(self) -> dict[str, Any]:
        return {"tp_r": self.tp_r}

    @property
    def oos_avg_r(self) -> float | None:
        return self.oos

    @property
    def oos_trades(self) -> int:
        return self.n


@dataclass
class _Report:
    rows: list[_Row]
    gate: CommitGateVerdict
    n_grid: int = 9


def _gate(decision: str = DECISION_COMMIT) -> CommitGateVerdict:
    return CommitGateVerdict(
        decision,
        0.97,
        0.10,
        300.0,
        1000,
        9,
        [] if decision == DECISION_COMMIT else ["DSR 0.81 < 0.95"],
    )


def _report(rows: list[_Row], decision: str = DECISION_COMMIT) -> Any:
    return _Report(rows, _gate(decision))


class TestConstantsMatchTheSpec:
    """The numbers in code must be the numbers in the pre-registration."""

    def test_per_tf_oos_floors(self) -> None:
        assert MIN_OOS_TRADES == {"15m": 20, "1h": 12, "4h": 5, "1d": 2}

    def test_step_and_improvement_bars(self) -> None:
        assert MIN_TP_R_STEP == 0.5
        assert MIN_IMPROVEMENT_R == 0.05

    def test_the_spec_prose_still_carries_them(self) -> None:
        # Cheap divergence alarm: if someone retunes the code constants without
        # touching the spec (or vice versa), this fails and forces both to move.
        prose = _SPEC.read_text(encoding="utf-8")
        assert "`15m→20, 1h→12, 4h→5, 1d→2`" in prose
        assert "**≥ 0.5**" in prose
        assert "**> +0.05R**" in prose


class TestCommitGateIsCheckedFirst:
    """A hard refusal: an in-sample winner failing the gate is an overfit mirage."""

    @pytest.mark.parametrize("decision", [DECISION_BLOCK, DECISION_INSUFFICIENT])
    def test_a_failing_gate_skips_however_good_the_rows_look(
        self, decision: str
    ) -> None:
        rows = [_Row(tp_r=4.0, oos=+9.99, n=10_000)]
        verdict = decide_cell(_report(rows, decision), timeframe="1h", current_tp_r=2.0)
        assert verdict.action == ACTION_SKIP
        assert decision in verdict.reason
        assert verdict.winner_tp_r is None, "a refused cell must nominate no winner"


class TestTheThreeFilters:
    def test_overfit_rows_are_dropped(self) -> None:
        rows = [
            _Row(tp_r=5.0, oos=+0.90, n=500, overfit=True),
            _Row(tp_r=2.0, oos=+0.10, n=500),
        ]
        verdict = decide_cell(_report(rows), timeframe="1h", current_tp_r=4.0)
        assert verdict.winner_tp_r == 2.0

    def test_thin_rows_are_dropped_at_the_tf_floor(self) -> None:
        # 15m floor is 20: n=19 must not win even though it scores best.
        rows = [_Row(tp_r=5.0, oos=+0.90, n=19), _Row(tp_r=2.0, oos=+0.10, n=20)]
        verdict = decide_cell(_report(rows), timeframe="15m", current_tp_r=4.0)
        assert verdict.winner_tp_r == 2.0

    def test_non_positive_oos_rows_are_dropped(self) -> None:
        rows = [_Row(tp_r=5.0, oos=0.0, n=500), _Row(tp_r=2.0, oos=+0.01, n=500)]
        verdict = decide_cell(_report(rows), timeframe="1h", current_tp_r=4.0)
        assert verdict.winner_tp_r == 2.0

    def test_all_rows_failing_is_a_skip_not_a_null(self) -> None:
        rows = [_Row(tp_r=5.0, oos=-0.40, n=500), _Row(tp_r=2.0, oos=-0.10, n=500)]
        verdict = decide_cell(_report(rows), timeframe="1h", current_tp_r=5.0)
        assert verdict.action == ACTION_SKIP
        assert "no row survives" in verdict.reason
        # The wording must stay a failure-to-clear; "no edge" would be a claim
        # this tool has not earned.
        assert "no edge" not in verdict.reason.lower()


class TestWriteWorthiness:
    def test_a_half_step_move_updates(self) -> None:
        rows = [_Row(tp_r=3.5, oos=+0.11, n=500), _Row(tp_r=3.0, oos=+0.10, n=500)]
        verdict = decide_cell(_report(rows), timeframe="1h", current_tp_r=3.0)
        assert verdict.action == ACTION_UPDATE

    def test_a_marginal_move_keeps(self) -> None:
        # Same tp_r as current and only +0.01R better → under both bars.
        rows = [_Row(tp_r=3.0, oos=+0.10, n=500)]
        verdict = decide_cell(_report(rows), timeframe="1h", current_tp_r=3.0)
        assert verdict.action == ACTION_KEEP

    def test_a_big_improvement_at_the_same_tp_r_updates(self) -> None:
        rows = [_Row(tp_r=3.0, oos=+0.50, n=500)]
        # current row IS this row, so improvement is 0 — must KEEP, not UPDATE.
        verdict = decide_cell(_report(rows), timeframe="1h", current_tp_r=3.0)
        assert verdict.action == ACTION_KEEP

    def test_a_cell_with_no_current_value_updates(self) -> None:
        rows = [_Row(tp_r=3.0, oos=+0.10, n=500)]
        verdict = decide_cell(_report(rows), timeframe="1h", current_tp_r=None)
        assert verdict.action == ACTION_UPDATE


class TestDefectCarrying:
    """The cells a bare "no change" line would hide."""

    def test_current_value_failing_the_filter_is_flagged(self) -> None:
        rows = [_Row(tp_r=5.0, oos=-0.40, n=500), _Row(tp_r=2.0, oos=+0.30, n=500)]
        verdict = decide_cell(_report(rows), timeframe="1h", current_tp_r=5.0)
        assert verdict.defect_carrying is True

    def test_a_healthy_current_value_is_not_flagged(self) -> None:
        rows = [_Row(tp_r=5.0, oos=+0.20, n=500)]
        verdict = decide_cell(_report(rows), timeframe="1h", current_tp_r=5.0)
        assert verdict.defect_carrying is False

    def test_it_is_reported_even_when_the_gate_refuses(self) -> None:
        # A refused cell still tells you its current value is unfit.
        rows = [_Row(tp_r=5.0, oos=-0.40, n=500)]
        verdict = decide_cell(
            _report(rows, DECISION_BLOCK), timeframe="1h", current_tp_r=5.0
        )
        assert verdict.action == ACTION_SKIP
        assert verdict.defect_carrying is True


class TestConfigResolution:
    def test_per_symbol_beats_per_tf_beats_fallback(self) -> None:
        params = {
            "engulfing": {
                "tp_r": 3.0,
                "tp_r_4h": 3.5,
                "SOLUSDT": {"tp_r_4h": 2.5},
            }
        }
        assert current_tp_r_for(params, "engulfing", "4h", "SOLUSDT") == 2.5
        assert current_tp_r_for(params, "engulfing", "4h", "BTCUSDT") == 3.5
        assert current_tp_r_for(params, "engulfing", "1d", "BTCUSDT") == 3.0
        assert current_tp_r_for(params, "absent", "1d", "BTCUSDT") is None

    def test_cells_are_the_intersection_of_active_tfs(self) -> None:
        config = {
            "timeframes": ["15m", "1h"],
            # 4h is declared for the strategy but the config does not run it.
            "strategy_timeframes": {"engulfing": ["1h", "4h"]},
        }
        cells = cells_for_config(config, ("BTCUSDT",))
        assert cells == [("engulfing", "1h", "BTCUSDT")]


class TestTheGridActuallySweepsTpR:
    """The axis the whole decision is about must be IN the grid.

    Caught live: the driver first called `_strategy_param_ranges`, which reads
    like the right function and documents itself as "strategy-specific params
    only (excludes tp_r/sl_pct)". The sweep then searched `swing_n` and
    `lookback`, every winner's `tp_r` came back None, and each cell printed
    `cur 3.0 → —`. Nothing else in the suite could see it, because `decide_cell`
    is correct on rows that HAVE a tp_r and the fault was in what it was fed.
    """

    def test_every_strategy_grid_has_a_tp_r_axis(self) -> None:
        from analytics.strategies import KNOWN_STRATEGIES
        from tools.wfo_resweep import _sweep_ranges

        for strategy in sorted(KNOWN_STRATEGIES):
            names = {r.name for r in _sweep_ranges(strategy)}
            assert "tp_r" in names, f"{strategy} grid has no tp_r axis: {names}"

    def test_the_grid_is_not_the_strategy_specific_one(self) -> None:
        # Mutation guard: the wrong function must FAIL this, so the test cannot
        # be satisfied by any range-builder that happens to return something.
        from analytics.param_sweep import _strategy_param_ranges

        wrong = {r.name for r in _strategy_param_ranges("engulfing")}
        assert "tp_r" not in wrong, (
            "_strategy_param_ranges gained a tp_r axis; this guard is now vacuous"
        )


class TestDriverMatchesTheSanctionedCliPath:
    """Every argument the driver passes must be what `/wfo-sweep` would pass.

    Two parity bugs of this exact class were found in one session: the grid was
    built from the function that EXCLUDES tp_r, and `min_trades` was a flat 20
    where the CLI is per-timeframe. Both were silent — the sweep ran, printed
    tables, and answered the wrong question. So parity is asserted directly
    rather than left to review.
    """

    def test_min_trades_is_per_timeframe_not_flat(self) -> None:
        from analytics.param_sweep import MIN_TRADES_FALLBACK, min_trades_for

        assert min_trades_for("15m") == 20
        assert min_trades_for("1h") == 12
        assert min_trades_for("4h") == 5
        assert min_trades_for("1d") == 2
        assert min_trades_for("3m") == MIN_TRADES_FALLBACK
        # A flat floor is the bug: 4h and 1d must NOT inherit 15m's.
        assert min_trades_for("4h") != min_trades_for("15m")

    def test_the_driver_passes_the_per_tf_floor_to_the_sweep(self) -> None:
        import ast
        from pathlib import Path

        src = Path("tools/wfo_resweep.py").read_text(encoding="utf-8")
        call = next(
            n
            for n in ast.walk(ast.parse(src))
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "run_param_sweep"
        )
        passed = {k.arg: k.value for k in call.keywords}
        assert "min_trades" in passed
        # It must be a CALL (min_trades_for(tf)), never a bare literal.
        assert isinstance(passed["min_trades"], ast.Call), (
            "min_trades is a constant again — the flat-20 bug has returned"
        )

    def test_the_decision_floor_is_the_same_object_not_a_copy(self) -> None:
        from analytics.param_sweep import MIN_TRADES_BY_TF
        from tools.wfo_resweep import MIN_OOS_TRADES

        assert MIN_OOS_TRADES is MIN_TRADES_BY_TF


class TestBareInvocation:
    """ST101: the guarantee is the test, never the bootstrap line."""

    def test_bare_invocation_works(self) -> None:
        result = subprocess.run(
            [sys.executable, "tools/wfo_resweep.py", "--help"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, result.stderr
        assert (
            "--day-filter" not in result.stdout
        )  # it reads the config's, never a flag
        assert "--db" in result.stdout
        assert "--measure-rho" in result.stdout


class TestMedianRhoCi:
    def test_ci_brackets_the_median(self) -> None:
        rhos = [0.60, 0.62, 0.65, 0.68, 0.70, 0.72, 0.75]
        med, lo, hi = median_rho_ci(rhos)
        assert lo <= med <= hi
        assert med == pytest.approx(0.68)

    def test_tight_sample_gives_a_tight_ci(self) -> None:
        _, lo, hi = median_rho_ci([0.70] * 50)
        assert hi - lo == pytest.approx(0.0, abs=1e-9)

    def test_single_value_is_degenerate_not_a_crash(self) -> None:
        med, lo, hi = median_rho_ci([0.42])
        assert med == lo == hi == pytest.approx(0.42)

    def test_empty_is_nan(self) -> None:
        med, lo, hi = median_rho_ci([])
        assert math.isnan(med) and math.isnan(lo) and math.isnan(hi)


class TestRhoVerdict:
    def test_lower_bound_above_the_bar_proceeds(self) -> None:
        assert rho_verdict(0.55, 0.80) == "PROCEED"

    def test_upper_bound_below_the_bar_is_not_licensed(self) -> None:
        assert rho_verdict(0.10, 0.40) == "NOT_LICENSED"

    def test_straddling_the_bar_is_insufficient(self) -> None:
        assert rho_verdict(0.40, 0.60) == "INSUFFICIENT"

    def test_touching_the_bar_is_insufficient_not_a_pass(self) -> None:
        """A boundary reading is untested, never cleared — the six-site defect."""
        assert rho_verdict(0.50, 0.90) == "INSUFFICIENT"
        assert rho_verdict(0.10, 0.50) == "INSUFFICIENT"

    def test_nan_is_insufficient(self) -> None:
        assert rho_verdict(float("nan"), float("nan")) == "INSUFFICIENT"


class TestDecideCellCarriesTheCorrectedGate:
    """R12: ``corrected`` is optional so the existing 3-arg call sites are untouched."""

    def test_no_corrected_gate_leaves_rho_and_n_trials_eff_none(self) -> None:
        rows = [_Row(tp_r=2.0, oos=+0.30, n=50)]
        verdict = decide_cell(_report(rows), timeframe="1h", current_tp_r=2.0)
        assert verdict.rho is None
        assert verdict.n_trials_eff is None

    def test_a_corrected_gate_populates_rho_and_n_trials_eff(self) -> None:
        rows = [_Row(tp_r=2.0, oos=+0.30, n=50)]
        corrected = CommitGateVerdict(
            DECISION_COMMIT, 0.97, 0.10, 300.0, 1000, 9, [], rho=0.62, n_trials_eff=4.5
        )
        verdict = decide_cell(
            _report(rows), timeframe="1h", current_tp_r=2.0, corrected=corrected
        )
        assert verdict.rho == pytest.approx(0.62)
        assert verdict.n_trials_eff == pytest.approx(4.5)

    def test_decide_cell_never_computes_the_corrected_gate_itself(self) -> None:
        """Pure/total/cheap: `decide_cell` must not import or call `_compute_sweep_gate`."""
        import ast

        src = Path("tools/wfo_resweep.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        fn = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "decide_cell"
        )
        calls = {
            n.func.id
            for n in ast.walk(fn)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        assert "_compute_sweep_gate" not in calls


class TestPopulationRho:
    """I2: rho measured on the PRE-REGISTERED population, unconditionally and
    without going through the commit gate (no CSCV/DSR)."""

    def _fake_report(self, trials: list[TrialPerf]) -> Any:
        return SimpleNamespace(all_rows=list(range(len(trials))))

    def test_matches_the_direct_effective_trial_count_computation(self) -> None:
        """The spec's own formula, cross-checked byte-for-byte rather than
        merely "returns a number in range"."""
        trials = _trial_family()
        with patch(
            "tools.wfo_resweep._row_to_trialperf", side_effect=lambda i: trials[i]
        ):
            rho, n_trials_eff = _population_rho(self._fake_report(trials))
        expected_rho, expected_n_eff = _effective_trial_count(
            _build_perf_matrix(trials, MIN_OBS_FACTOR * 14)
        )
        assert rho == pytest.approx(expected_rho)
        assert n_trials_eff == pytest.approx(expected_n_eff)

    def test_fewer_than_two_arms_is_none_not_nan(self) -> None:
        trials = _trial_family(k=1)
        with patch(
            "tools.wfo_resweep._row_to_trialperf", side_effect=lambda i: trials[i]
        ):
            rho, n_trials_eff = _population_rho(self._fake_report(trials))
        assert rho is None
        assert n_trials_eff is None

    def test_never_conditioned_on_the_min_obs_floor(self) -> None:
        """The whole point of I2: a family whose trades fall BELOW the gate's
        own min_obs floor (and would therefore make `evaluate_commit_gate`
        return INSUFFICIENT with rho=None) still gets a rho reading here."""
        trials = _trial_family(k=4, n=6)  # 6 trades/arm < 2*14 = 28
        with patch(
            "tools.wfo_resweep._row_to_trialperf", side_effect=lambda i: trials[i]
        ):
            rho, n_trials_eff = _population_rho(self._fake_report(trials))
        assert rho is not None
        assert n_trials_eff is not None


class TestFormatOutPath:
    """Minor fix: a stray unescaped '{' in --out must not crash a multi-minute
    run at the very last step, after every cell has already been swept."""

    def test_the_label_placeholder_is_substituted(self) -> None:
        path = _format_out_path("docs/plans/scratch/st134-resweep-{label}.json", "raw")
        assert path == Path("docs/plans/scratch/st134-resweep-raw.json")

    def test_a_stray_brace_returns_none_not_a_raised_keyerror(self) -> None:
        assert _format_out_path("docs/plans/scratch/{oops}.json", "raw") is None

    def test_a_literal_escaped_brace_still_resolves(self) -> None:
        path = _format_out_path("docs/plans/scratch/{{literal}}-{label}.json", "raw")
        assert path == Path("docs/plans/scratch/{literal}-raw.json")


class TestLedgerHasAlertTable:
    """I5: probe once, degrade rather than crash the 273-cell run."""

    def test_present_table_reads_true(self) -> None:
        conn = MagicMock()
        conn.execute.return_value = None
        assert _ledger_has_alert_table(conn) is True

    def test_missing_table_reads_false_not_raises(self) -> None:
        conn = MagicMock()
        conn.execute.side_effect = RuntimeError("Catalog Error: Table does not exist")
        assert _ledger_has_alert_table(conn) is False


class TestMeasureRhoRescoresTheFullGrid:
    """R3: the re-score must read `all_rows`, never the top-N `rows` truncation.

    `run_param_sweep` truncates to `rows[:top_n]`; deflating over that instead of
    `all_rows` would conflate top-N truncation with correlation and corrupt this
    task's own headline number.
    """

    def test_the_corrected_gate_call_reads_all_rows(self) -> None:
        import ast

        src = Path("tools/wfo_resweep.py").read_text(encoding="utf-8")
        call = next(
            n
            for n in ast.walk(ast.parse(src))
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "_compute_sweep_gate"
        )
        first_arg = call.args[0]
        assert isinstance(first_arg, ast.Attribute) and first_arg.attr == "all_rows", (
            "the re-score must deflate over all_rows (the full grid), not the "
            "top-N `rows` truncation"
        )

    def test_the_measure_rho_or_books_flags_gate_the_corrected_computation(
        self,
    ) -> None:
        """Default behaviour stays byte-identical: neither flag set, no re-score.

        C2 (2026-09-11): ``--measure-rho`` ALONE must not pay the full 2x2's CSCV
        cost and must not write the corrected 2x2 into the artifact — that "books"
        block is the step-3 (§6) result the §4a/§4b kill-switches exist to gate,
        so it landing on disk regardless of the kill-switch's own verdict is
        exactly the violation this test now pins shut. So the shape changed from
        a single ternary (``{...} if measure_rho or books else None``) to:

        * ``if args.books:`` → the full 4-book dict comprehension over ``BOOKS``
        * ``elif args.measure_rho:`` → a ONE-entry dict, ``{"trials_corrected":
          _compute_sweep_gate(..., correct_trials=True, correct_obs=False)}``
        * ``else:`` → ``None``

        R19 (carried forward): this guard must pin the shape's polarity with
        equal strength to the one it replaces — a substring-only check on names
        mentioned passes an inverted condition unnoticed, which is the class of
        guard the ORIGINAL version of this test was.
        """
        import ast

        src = Path("tools/wfo_resweep.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        main_fn = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "main"
        )

        def _is_args_attr(node: ast.AST, attr: str) -> bool:
            return (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "args"
                and node.attr == attr
            )

        def _book_verdicts_assign(stmts: list[ast.stmt]) -> ast.Assign:
            return next(
                s
                for s in stmts
                if isinstance(s, ast.Assign)
                and any(
                    isinstance(t, ast.Name) and t.id == "book_verdicts"
                    for t in s.targets
                )
            )

        # --- if args.books: book_verdicts = {full 2x2} -------------------------
        # `main` also has a SECOND, unrelated `if args.books:` (the --books
        # summary printed after the loop) — disambiguated by which one actually
        # assigns book_verdicts in its body, not by which is found first.
        outer_if = next(
            n
            for n in ast.walk(main_fn)
            if isinstance(n, ast.If)
            and _is_args_attr(n.test, "books")
            and any(
                isinstance(s, ast.Assign)
                and any(
                    isinstance(t, ast.Name) and t.id == "book_verdicts"
                    for t in s.targets
                )
                for s in n.body
            )
        )
        books_assign = _book_verdicts_assign(outer_if.body)
        assert isinstance(books_assign.value, ast.DictComp), (
            "under --books, book_verdicts must be the full 4-book dict "
            "comprehension — the only path allowed to pay all four CSCV calls"
        )
        comp_call = books_assign.value.value
        assert (
            isinstance(comp_call, ast.Call)
            and isinstance(comp_call.func, ast.Name)
            and comp_call.func.id == "_compute_sweep_gate"
        ), "the --books comprehension must call _compute_sweep_gate per book"

        # --- elif args.measure_rho: book_verdicts = {ONE entry} ----------------
        assert len(outer_if.orelse) == 1 and isinstance(outer_if.orelse[0], ast.If), (
            "the FALSE branch of `if args.books` must be a single nested "
            "if/else (an `elif`) — never a second independent top-level branch"
        )
        inner_if = outer_if.orelse[0]
        assert _is_args_attr(inner_if.test, "measure_rho"), (
            "the elif must test args.measure_rho, not a restatement of "
            "args.books — that inversion would make --measure-rho alone pay "
            "the full 2x2 cost again"
        )
        rho_assign = _book_verdicts_assign(inner_if.body)
        assert isinstance(rho_assign.value, ast.Dict), (
            "under --measure-rho ALONE, book_verdicts must be a single-entry "
            "dict LITERAL, never the 4-book comprehension — computing all four "
            "here is exactly the CSCV-cost violation this guard exists to catch"
        )
        keys = [ast.unparse(k).strip("'\"") for k in rho_assign.value.keys if k]
        assert keys == ["trials_corrected"], (
            "the --measure-rho-alone book must be exactly one entry, "
            "'trials_corrected' — not the full BOOKS family"
        )
        only_call = rho_assign.value.values[0]
        assert (
            isinstance(only_call, ast.Call)
            and isinstance(only_call.func, ast.Name)
            and only_call.func.id == "_compute_sweep_gate"
        )
        call_kwargs = {kw.arg: kw.value for kw in only_call.keywords}
        correct_trials_kwarg = call_kwargs.get("correct_trials")
        assert (
            isinstance(correct_trials_kwarg, ast.Constant)
            and correct_trials_kwarg.value is True
        ), "the --measure-rho-alone book must set correct_trials=True"
        correct_obs_kwarg = call_kwargs.get("correct_obs")
        assert (
            isinstance(correct_obs_kwarg, ast.Constant)
            and correct_obs_kwarg.value is False
        ), "the --measure-rho-alone book must leave correct_obs=False"

        # --- else: book_verdicts = None -----------------------------------------
        else_assign = _book_verdicts_assign(inner_if.orelse)
        assert (
            isinstance(else_assign.value, ast.Constant)
            and else_assign.value.value is None
        ), "neither flag set must leave book_verdicts as None"

        # --- the "books" artifact field is gated on args.books, never on
        #     `book_verdicts is not None` (true under --measure-rho alone too) --
        books_dictcomp = next(
            n
            for n in ast.walk(main_fn)
            if isinstance(n, ast.DictComp)
            and isinstance(n.generators[0].iter, ast.Call)
            and isinstance(n.generators[0].iter.func, ast.Attribute)
            and n.generators[0].iter.func.attr == "items"
        )
        books_field_ifexp = next(
            n
            for n in ast.walk(main_fn)
            if isinstance(n, ast.IfExp) and n.body is books_dictcomp
        )

        def _names_args_books(node: ast.expr) -> bool:
            """True for `args.books` alone, or for a mypy-narrowing `and`
            that still carries `args.books` as one of its operands -- never
            merely `book_verdicts is not None` on its own, which is true
            under --measure-rho alone too."""
            if _is_args_attr(node, "books"):
                return True
            return (
                isinstance(node, ast.BoolOp)
                and isinstance(node.op, ast.And)
                and any(_is_args_attr(v, "books") for v in node.values)
            )

        assert _names_args_books(books_field_ifexp.test), (
            "the 'books' artifact field must be gated on args.books directly — "
            "gating it on `book_verdicts is not None` alone would write the 2x2 "
            "under --measure-rho alone too, since that also leaves book_verdicts "
            "non-None"
        )
        assert (
            isinstance(books_field_ifexp.orelse, ast.Constant)
            and books_field_ifexp.orelse.value is None
        )

        # --- "moved_to_insufficient_under_obs_correction" is gated the same way -
        results_dict = next(
            n
            for n in ast.walk(main_fn)
            if isinstance(n, ast.Dict)
            and any(
                isinstance(k, ast.Constant)
                and k.value == "moved_to_insufficient_under_obs_correction"
                for k in n.keys
                if k is not None
            )
        )
        moved_idx = next(
            i
            for i, k in enumerate(results_dict.keys)
            if isinstance(k, ast.Constant)
            and k.value == "moved_to_insufficient_under_obs_correction"
        )
        moved_expr = results_dict.values[moved_idx]
        assert isinstance(moved_expr, ast.IfExp)
        assert (
            isinstance(moved_expr.test, ast.UnaryOp)
            and isinstance(moved_expr.test.op, ast.Not)
            and _names_args_books(moved_expr.test.operand)
        ), (
            "moved_to_insufficient_under_obs_correction must be gated on "
            "`not args.books` (or the mypy-narrowing `not (args.books and "
            "book_verdicts is not None)`) — it reads book_verdicts['raw'] and "
            "['obs_corrected'], neither of which exists under --measure-rho alone"
        )

        # --- corrected=book_verdicts["trials_corrected"] if ... else None -----
        decide_call = next(
            n
            for n in ast.walk(main_fn)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "decide_cell"
        )
        corrected_kw = next(kw for kw in decide_call.keywords if kw.arg == "corrected")
        assert isinstance(corrected_kw.value, ast.IfExp), (
            "the corrected gate must be computed conditionally on book_verdicts, "
            "never unconditionally on every run"
        )
        # Pin the polarity here too: `book_verdicts is None` (same names, inverted
        # meaning) would pass a substring check on "book_verdicts" and "None"
        # unchanged while swapping which branch reads the dict.
        cond = corrected_kw.value.test
        assert (
            isinstance(cond, ast.Compare)
            and isinstance(cond.left, ast.Name)
            and cond.left.id == "book_verdicts"
            and len(cond.ops) == 1
            and isinstance(cond.ops[0], ast.IsNot)
            and isinstance(cond.comparators[0], ast.Constant)
            and cond.comparators[0].value is None
        ), (
            "the corrected gate's condition must be exactly "
            "`book_verdicts is not None` — not `is None` or any other spelling "
            "that a substring check on the two names alone would miss"
        )
        body = corrected_kw.value.body
        assert isinstance(body, ast.Subscript) and isinstance(body.value, ast.Name)
        assert body.value.id == "book_verdicts", (
            "the TRUE branch must read the already-computed book_verdicts — "
            "recomputing _compute_sweep_gate here would pay the CSCV cost twice"
        )
        assert ast.unparse(body.slice).strip("'\"") == "trials_corrected", (
            "decide_cell's corrected gate must be the SAME value Task 6 "
            "computed — book_verdicts['trials_corrected'] — never a different book"
        )
        orelse = corrected_kw.value.orelse
        assert isinstance(orelse, ast.Constant) and orelse.value is None, (
            "the FALSE branch (book_verdicts is None) must be None — no "
            "corrected-gate computation when neither flag is set"
        )


class TestBooks:
    def test_all_four_books_are_declared(self) -> None:
        assert BOOKS == (
            ("raw", False, False),
            ("trials_corrected", True, False),
            ("obs_corrected", False, True),
            ("both", True, True),
        )

    def test_raw_book_is_first_so_it_is_the_baseline(self) -> None:
        assert BOOKS[0][0] == "raw"


class TestEffectSize:
    def test_delta_is_winner_minus_current(self) -> None:
        assert effect_size(0.10, 0.32) == pytest.approx(0.22)

    def test_negative_delta_is_reported_not_clamped(self) -> None:
        assert effect_size(0.40, 0.15) == pytest.approx(-0.25)

    def test_missing_current_is_none(self) -> None:
        assert effect_size(None, 0.32) is None

    def test_missing_winner_is_none(self) -> None:
        assert effect_size(0.10, None) is None


class TestStratifiedCellSample:
    """ST134 §4b requirement 2."""

    def _population(self) -> list[ResweepCell]:
        # 70 15m + 20 1h + 10 4h = 100, chosen so k=30 apportions with NO
        # remainder (21/6/3) — isolates the sampling mechanism from the
        # largest-remainder tie-break, which gets its own test below.
        pop = [_cell("15m", symbol=f"S{i}") for i in range(70)]
        pop += [_cell("1h", symbol=f"S{i}") for i in range(20)]
        pop += [_cell("4h", symbol=f"S{i}") for i in range(10)]
        return pop

    def test_respects_k(self) -> None:
        sample = stratified_cell_sample(self._population(), k=30, seed=1)
        assert len(sample) == 30

    def test_preserves_proportions_where_the_population_allows(self) -> None:
        sample = stratified_cell_sample(self._population(), k=30, seed=1)
        composition = {
            tf: sum(1 for c in sample if c.timeframe == tf)
            for tf in ("15m", "1h", "4h")
        }
        assert composition == {"15m": 21, "1h": 6, "4h": 3}

    def test_is_deterministic_under_its_seed(self) -> None:
        pop = self._population()
        a = stratified_cell_sample(pop, k=30, seed=42)
        b = stratified_cell_sample(pop, k=30, seed=42)
        assert a == b

    def test_the_seed_actually_governs_the_draw(self) -> None:
        """Mutation guard: a stub ignoring `seed` (e.g. always `group[:n]`)
        would pass every test above."""
        pop = self._population()
        a = stratified_cell_sample(pop, k=30, seed=1)
        b = stratified_cell_sample(pop, k=30, seed=2)
        assert a != b

    def test_smaller_population_than_k_returns_everything(self) -> None:
        pop = [
            _cell("15m", symbol="A"),
            _cell("1h", symbol="B"),
            _cell("4h", symbol="C"),
        ]
        sample = stratified_cell_sample(pop, k=30, seed=1)
        assert len(sample) == 3
        assert set(sample) == set(pop)

    def test_largest_remainder_apportions_to_sum_exactly_k(self) -> None:
        # 7 15m + 2 1h + 1 4h = 10, k=3: raw quotas 2.1 / 0.6 / 0.3 -- floors
        # sum to 2, and the largest remainder (1h, frac 0.6) must take the
        # third slot rather than leaving the draw short.
        pop = [_cell("15m", symbol=f"S{i}") for i in range(7)]
        pop += [_cell("1h", symbol=f"S{i}") for i in range(2)]
        pop += [_cell("4h", symbol="S0")]
        sample = stratified_cell_sample(pop, k=3, seed=1)
        assert len(sample) == 3
        composition = {
            tf: sum(1 for c in sample if c.timeframe == tf)
            for tf in ("15m", "1h", "4h")
        }
        assert composition == {"15m": 2, "1h": 1, "4h": 0}

    def test_defaults_reference_the_constants_never_restate_them(self) -> None:
        """Fix 4 (Task 10 review): the signature previously wrote `k: int = 30,
        seed: int = 20260910` as bare literals, restating
        `_NULL_CALIBRATION_K` / `_NULL_CALIBRATION_SEED` — the exact
        restated-constant defect this branch exists to remove, sitting in the
        one sibling function that did not already reference them.

        A value-equality check (`default == 30`) cannot tell "references the
        constant" from "coincidentally restates the same literal" — CPython
        interns small ints, so even an identity check on the resolved value
        would pass either way. Reading the AST default nodes is the only way
        to pin the SOURCE-level fact.
        """
        import ast

        src = Path("tools/wfo_resweep.py").read_text(encoding="utf-8")
        fn = next(
            n
            for n in ast.walk(ast.parse(src))
            if isinstance(n, ast.FunctionDef) and n.name == "stratified_cell_sample"
        )
        defaults = {
            arg.arg: default
            for arg, default in zip(
                fn.args.kwonlyargs, fn.args.kw_defaults, strict=True
            )
        }
        assert isinstance(defaults["k"], ast.Name) and defaults["k"].id == (
            "_NULL_CALIBRATION_K"
        ), "k's default must be the named constant, not a restated literal"
        assert isinstance(defaults["seed"], ast.Name) and defaults["seed"].id == (
            "_NULL_CALIBRATION_SEED"
        ), "seed's default must be the named constant, not a restated literal"


class TestNullCalibrationVerdict:
    """ST134 §4b requirement 4: the pre-committed 10% bar."""

    def test_at_or_below_the_bar_proceeds(self) -> None:
        assert null_calibration_verdict(0.05, 0.01) == "PROCEED"

    def test_above_the_bar_abandons(self) -> None:
        assert null_calibration_verdict(0.11, 0.01) == "ABANDON"

    def test_the_boundary_is_pinned_to_proceed(self) -> None:
        """<= 10% passes; the spec's own wording ('must pass <= 10%')."""
        assert null_calibration_verdict(0.10, 0.01) == "PROCEED"
        assert null_calibration_verdict(0.1000001, 0.01) == "ABANDON"

    def test_a_custom_bar_is_honoured(self) -> None:
        assert null_calibration_verdict(0.15, 0.01, bar=0.20) == "PROCEED"
        assert null_calibration_verdict(0.25, 0.01, bar=0.20) == "ABANDON"

    def test_nan_corrected_rate_abandons_rather_than_proceeding(self) -> None:
        """Nothing evaluated must not silently read as 'passed <= 10%' — the
        naive `nan > bar` comparison is False in Python, which would proceed
        on no evidence if this guard were missing."""
        assert null_calibration_verdict(float("nan"), 0.01) == "ABANDON"

    def test_uncorrected_rate_never_gates_the_verdict(self) -> None:
        """§4b: the uncorrected arm is disclosed beside the verdict, never
        folded into it as a second condition."""
        assert null_calibration_verdict(0.05, 0.99) == "PROCEED"
        assert null_calibration_verdict(0.05, 0.0) == "PROCEED"


class TestBothNullCalibrationArms:
    """ST134 §4b requirement 3: every arm runs on the same nulls.

    I1: a third arm (``both`` — ``correct_trials=True, correct_obs=True``) was
    added because ``corrected`` alone means TRIALS-corrected only, not the
    gate as actually shipped since C1 fed ``correct_obs`` into the MinTRL leg
    too.
    """

    def test_all_three_arms_evaluate_every_replicate_on_this_family(self) -> None:
        corrected, uncorrected, both = _both_null_calibration_arms(
            _trial_family(), seed=99, n_replicates=40, n_splits=4
        )
        assert corrected.evaluated == uncorrected.evaluated == both.evaluated == 40
        assert corrected.skipped == uncorrected.skipped == both.skipped == 0

    def test_the_shared_seed_is_observable_in_rho_after(self) -> None:
        """`rho_after` is computed from the sign-flipped family BEFORE either
        result branches on `correct_trials`, so identical `rho_after` across
        all three returned results is the observable proof they drew the same
        nulls — not merely that the same seed value was passed somewhere."""
        corrected, uncorrected, both = _both_null_calibration_arms(
            _trial_family(), seed=99, n_replicates=40, n_splits=4
        )
        assert corrected.rho_after == pytest.approx(uncorrected.rho_after)
        assert corrected.rho_after == pytest.approx(both.rho_after)

    def test_the_two_arms_can_genuinely_diverge(self) -> None:
        """A stub returning the same NullCalibrationResult thrice would pass
        the identical-rho_after test above too; this fixture (proven in Task
        7's own suite) makes the corrected arm measurably more permissive."""
        fam = _underdeflated_trial_family()
        corrected, uncorrected, _both = _both_null_calibration_arms(
            fam, seed=20260910, n_replicates=200, n_splits=14
        )
        assert corrected.rate > 0.10
        assert uncorrected.rate <= 0.10

    def test_both_is_never_more_permissive_than_trials_only(self) -> None:
        """I1: correction (b) can only ever LOWER the deflated Sharpe relative
        to the trials-only book, so `both`'s pass rate is bounded above by
        `corrected`'s on the SAME nulls — a trials-only PROCEED licenses
        reading a `both` PROCEED, never the reverse."""
        fam = _underdeflated_trial_family()
        corrected, _uncorrected, both = _both_null_calibration_arms(
            fam, seed=20260910, n_replicates=200, n_splits=14
        )
        assert both.rate <= corrected.rate + 1e-9

    def test_empty_trials_is_nan_on_all_three_arms(self) -> None:
        corrected, uncorrected, both = _both_null_calibration_arms(
            [], seed=1, n_replicates=10, n_splits=4
        )
        assert math.isnan(corrected.rate)
        assert math.isnan(uncorrected.rate)
        assert math.isnan(both.rate)


class TestPoolNullResults:
    """Aggregation across the sampled cells into one arm-level record."""

    def test_pools_by_replicate_not_by_cell_average(self) -> None:
        a = NullCalibrationResult(
            rate=0.05, evaluated=100, skipped=0, rho_before=0.5, rho_after=0.6
        )
        b = NullCalibrationResult(
            rate=0.20, evaluated=50, skipped=0, rho_before=0.7, rho_after=0.8
        )
        pooled = _pool_null_results([a, b])
        # 5 passes + 10 passes over 150 evaluated = 0.1, NOT the per-cell
        # average of 0.05 and 0.20 (0.125) -- a thin cell must not outvote
        # the bulk of evaluated replicates.
        assert pooled.rate == pytest.approx(0.10)
        assert pooled.evaluated == 150
        assert pooled.skipped == 0
        assert pooled.rho_before == pytest.approx(0.6)
        assert pooled.rho_after == pytest.approx(0.7)

    def test_a_fully_skipped_cell_contributes_evaluated_zero_not_a_zero_rate(
        self,
    ) -> None:
        dead = NullCalibrationResult(
            rate=float("nan"),
            evaluated=0,
            skipped=200,
            rho_before=float("nan"),
            rho_after=float("nan"),
        )
        live = NullCalibrationResult(
            rate=0.10, evaluated=200, skipped=0, rho_before=0.4, rho_after=0.5
        )
        pooled = _pool_null_results([dead, live])
        assert pooled.rate == pytest.approx(0.10)
        assert pooled.evaluated == 200
        assert pooled.skipped == 200
        assert pooled.rho_before == pytest.approx(0.4)
        assert pooled.rho_after == pytest.approx(0.5)

    def test_every_cell_skipped_pools_to_nan_not_zero(self) -> None:
        dead = NullCalibrationResult(
            rate=float("nan"),
            evaluated=0,
            skipped=200,
            rho_before=float("nan"),
            rho_after=float("nan"),
        )
        pooled = _pool_null_results([dead, dead])
        assert math.isnan(pooled.rate)
        assert math.isnan(pooled.rho_before)
        assert math.isnan(pooled.rho_after)
        assert pooled.evaluated == 0
        assert pooled.skipped == 400

    def test_empty_input_pools_to_nan(self) -> None:
        pooled = _pool_null_results([])
        assert math.isnan(pooled.rate)
        assert pooled.evaluated == 0
        assert pooled.skipped == 0


class TestResultToJson:
    def test_nan_fields_become_none(self) -> None:
        result = NullCalibrationResult(
            rate=float("nan"),
            evaluated=0,
            skipped=10,
            rho_before=float("nan"),
            rho_after=float("nan"),
        )
        assert _result_to_json(result) == {
            "rate": None,
            "evaluated": 0,
            "skipped": 10,
            "rho_before": None,
            "rho_after": None,
        }

    def test_real_values_pass_through(self) -> None:
        result = NullCalibrationResult(
            rate=0.05, evaluated=200, skipped=0, rho_before=0.6, rho_after=0.65
        )
        as_json = _result_to_json(result)
        assert as_json["rate"] == pytest.approx(0.05)
        assert as_json["evaluated"] == 200
        assert as_json["rho_before"] == pytest.approx(0.6)
        assert as_json["rho_after"] == pytest.approx(0.65)


class TestFarBelowNominal:
    """Fix 2 / cheap fix 4 (Task 10 review): the far-below reading must carry
    its own comparison and denominators, and NaN must read as a third,
    undetermined state — never as "not clearly below", which is a claim
    about a comparison that was never made."""

    def test_well_below_the_threshold_reads_true(self) -> None:
        far_below, line = _far_below_nominal(0.005)
        assert far_below is True
        assert "0.0050" in line
        assert "0.0250" in line  # the threshold: 0.05 * 0.5
        assert "<" in line

    def test_at_or_above_the_threshold_reads_false(self) -> None:
        far_below, line = _far_below_nominal(0.04)
        assert far_below is False
        assert ">=" in line

    def test_nan_reads_as_a_third_undetermined_state_not_false(self) -> None:
        far_below, line = _far_below_nominal(float("nan"))
        assert far_below is None
        assert "undetermined" in line
        assert "not clearly below" not in line

    def test_the_threshold_and_disclaimer_travel_with_the_line(self) -> None:
        """The line is what gets quoted into a tracking row, so the number
        judged against and the fact that it is this run's reading (not a
        pre-registered bar) must both be IN the string, not only in a source
        comment nobody reading the output can see."""
        _, line = _far_below_nominal(0.01, nominal=0.05, factor=0.5)
        assert "0.0250" in line
        assert "not a pre-registered bar" in line

    def test_a_custom_factor_changes_the_threshold(self) -> None:
        far_below, line = _far_below_nominal(0.03, nominal=0.05, factor=0.8)
        assert far_below is True  # 0.03 < 0.04
        assert "0.0400" in line


class TestCalibrateOneCell:
    """Fix 3 (Task 10 review): an errored cell must still produce a record,
    so `len(cells) == k_drawn` always holds in the artifact."""

    def _cell_arg(self) -> ResweepCell:
        return ResweepCell("config/signal_watch.toml", "off", "bos", "1h", "BTCUSDT")

    def test_a_failing_sweep_still_returns_a_record(self) -> None:
        with patch(
            "tools.wfo_resweep._sweep_cell", side_effect=RuntimeError("no data")
        ):
            record, corrected, uncorrected, both = _calibrate_one_cell(
                MagicMock(),
                self._cell_arg(),
                fee_pct=0.0004,
                min_sl_pct=0.001,
                slippage_pct=0.0002,
                since_ms=0,
                seed=1,
                n_replicates=10,
                n_splits=4,
            )
        assert corrected is None
        assert uncorrected is None
        assert both is None
        assert record["error"] == "RuntimeError('no data')"
        assert record["corrected"] is None
        assert record["uncorrected"] is None
        assert record["both"] is None
        assert record["corrections"] == ARM_CORRECTIONS
        assert record["strategy"] == "bos"
        assert record["timeframe"] == "1h"
        assert record["symbol"] == "BTCUSDT"

    def test_a_successful_sweep_returns_populated_results(self) -> None:
        # `_row_to_trialperf` expects a `SweepRow`; stubbing it out (rather
        # than building real `SweepRow`/`BacktestResult`/`Trade` fixtures)
        # keeps this test scoped to `_calibrate_one_cell`'s own wiring, which
        # is what Fix 3 is about — the trial-family math is Task 7's, tested
        # in `tests/test_st134_null_calibration.py`.
        trials = _underdeflated_trial_family()
        fake_report = SimpleNamespace(all_rows=list(range(len(trials))))
        with (
            patch("tools.wfo_resweep._sweep_cell", return_value=fake_report),
            patch(
                "tools.wfo_resweep._row_to_trialperf",
                side_effect=lambda i: trials[i],
            ),
        ):
            record, corrected, uncorrected, both = _calibrate_one_cell(
                MagicMock(),
                self._cell_arg(),
                fee_pct=0.0004,
                min_sl_pct=0.001,
                slippage_pct=0.0002,
                since_ms=0,
                seed=20260910,
                n_replicates=50,
                n_splits=4,
            )
        assert record["error"] is None
        assert corrected is not None and uncorrected is not None and both is not None
        assert record["corrected"]["evaluated"] == 50
        assert record["both"]["evaluated"] == 50
        assert record["corrections"] == ARM_CORRECTIONS
        assert record["n_arms"] == len(trials)


class TestNullCalibrationOutPath:
    def test_path_is_formatted_from_the_label_under_scratch(self) -> None:
        assert _null_calibration_out_path("corrected") == Path(
            "docs/plans/scratch/st134-null-calibration-corrected.json"
        )

    def test_different_labels_do_not_collide(self) -> None:
        a = _null_calibration_out_path("raw-book")
        b = _null_calibration_out_path("attribution")
        assert a != b
        assert "docs/plans/scratch/" in str(a)
        assert "docs/plans/scratch/" in str(b)


class TestTheStopMessageIsSharedNotDuplicated:
    """Cheap fix (Task 10 review): the §4a and §4b kill-switches printed the
    SAME string from two copy-pasted literals, so "same voice" was enforced
    only by the strings happening to match."""

    def test_exactly_one_stop_message_literal_exists_in_the_source(self) -> None:
        src = Path("tools/wfo_resweep.py").read_text(encoding="utf-8")
        occurrences = src.count("This does NOT license 'the gate was right'")
        assert occurrences == 1, (
            f"found {occurrences} copies of the STOP message text — it must "
            "live in exactly one place (_ROUTE_TO_ST133_STOP_MESSAGE)"
        )

    def test_both_kill_switches_print_the_shared_constant(self) -> None:
        import ast

        src = Path("tools/wfo_resweep.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        print_calls_naming_it = [
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "print"
            and n.args
            and isinstance(n.args[0], ast.Name)
            and n.args[0].id == "_ROUTE_TO_ST133_STOP_MESSAGE"
        ]
        # One in `main`'s --measure-rho block, one in `_run_null_calibration`.
        assert len(print_calls_naming_it) == 2


class TestSweepCellIsTheOneCallSite:
    """Cheap fix (Task 10 review): `run_param_sweep` was called with the same
    15 arguments, byte-identically, in both the resweep loop and the §4b
    calibration loop — a change to one copy silently stops describing the
    same book the other decides on."""

    def test_run_param_sweep_is_called_exactly_once_in_the_whole_file(self) -> None:
        import ast

        src = Path("tools/wfo_resweep.py").read_text(encoding="utf-8")
        calls = [
            n
            for n in ast.walk(ast.parse(src))
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "run_param_sweep"
        ]
        assert len(calls) == 1, (
            f"found {len(calls)} call sites for run_param_sweep — there must "
            "be exactly one, inside _sweep_cell"
        )

    def test_the_one_call_site_is_inside_sweep_cell(self) -> None:
        import ast

        src = Path("tools/wfo_resweep.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        fn = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "_sweep_cell"
        )
        calls = [
            n
            for n in ast.walk(fn)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "run_param_sweep"
        ]
        assert len(calls) == 1

    def test_both_per_cell_paths_call_sweep_cell(self) -> None:
        import ast

        src = Path("tools/wfo_resweep.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        calling_functions = {
            fn.name
            for fn in ast.walk(tree)
            if isinstance(fn, ast.FunctionDef)
            for n in ast.walk(fn)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "_sweep_cell"
        }
        assert "main" in calling_functions
        assert "_calibrate_one_cell" in calling_functions


class TestRunNullCalibrationIntegration:
    """End-to-end (with `_sweep_cell` and the DB mocked out) proof that the
    artifact carries what the Task 10 review required: an errored cell still
    gets a record so `len(cells) == k_drawn`, and every interpretive field
    travels with the pooled numbers rather than living only in a comment."""

    def test_artifact_carries_an_errored_cell_and_every_interpretive_field(
        self, tmp_path: Path
    ) -> None:
        import json

        from tools.wfo_resweep import _run_null_calibration

        trials = _underdeflated_trial_family()
        fake_report = SimpleNamespace(all_rows=list(range(len(trials))))
        calls = {"n": 0}

        def fake_sweep_cell(conn: object, **kw: object) -> SimpleNamespace:
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("no OHLCV data")
            return fake_report

        out_path = tmp_path / "artifact.json"
        config = tmp_path / "signal_watch_test.toml"
        config.write_text(
            'day_filter = "off"\n'
            'timeframes = ["1h"]\n'
            "[strategy_timeframes]\n"
            'bos = ["1h"]\n'
        )

        with (
            patch("tools.wfo_resweep._sweep_cell", side_effect=fake_sweep_cell),
            patch(
                "tools.wfo_resweep._row_to_trialperf",
                side_effect=lambda i: trials[i],
            ),
            patch(
                "tools.wfo_resweep._null_calibration_out_path",
                return_value=out_path,
            ),
        ):
            _run_null_calibration(
                MagicMock(),
                configs=[config],
                symbols=("BTCUSDT", "ETHUSDT"),
                fee_pct=0.0004,
                min_sl_pct=0.001,
                slippage_pct=0.0002,
                since_ms=0,
                label="test",
                n_replicates=20,
                n_splits=4,
            )

        data = json.loads(out_path.read_text())
        # Fix 3: the errored cell is IN the artifact, not dropped.
        assert len(data["cells"]) == data["k_drawn"] == 2
        errored = [c for c in data["cells"] if c["error"] is not None]
        assert len(errored) == 1
        assert errored[0]["corrected"] is None
        assert errored[0]["uncorrected"] is None
        # Fix 2: the artifact can interpret its own headline numbers.
        for key in (
            "bar",
            "nominal_null_rate",
            "far_below_factor",
            "uncorrected_far_below_nominal",
            "pooling",
            "candidate_population_size",
        ):
            assert key in data, f"missing {key!r} from the artifact"
        assert "population_size" not in data, "the renamed key must not linger"
        assert data["candidate_population_size"] == 2
        # I1: a third arm, and a record of which corrections each arm applies.
        assert "both" in data
        assert data["corrections"] == ARM_CORRECTIONS
        for cell in data["cells"]:
            assert cell["corrections"] == ARM_CORRECTIONS
        # I4: the final write is marked complete.
        assert data["complete"] is True

    def test_a_crash_outside_calibrate_one_cell_still_leaves_a_partial_artifact(
        self, tmp_path: Path
    ) -> None:
        """I4: the artifact is checkpointed after EVERY cell, not written once
        at the end — so a crash that ``_calibrate_one_cell``'s own (now
        whole-body) ``try`` cannot catch still leaves every cell scored before
        it on disk, marked incomplete, rather than losing the whole run."""
        import json

        from tools.wfo_resweep import _run_null_calibration

        trials = _underdeflated_trial_family()

        out_path = tmp_path / "artifact.json"
        config = tmp_path / "signal_watch_test.toml"
        config.write_text(
            'day_filter = "off"\n'
            'timeframes = ["1h"]\n'
            "[strategy_timeframes]\n"
            'bos = ["1h"]\n'
        )

        calls = {"n": 0}

        def crashing_calibrate_one_cell(
            conn: object, cell: object, **kw: object
        ) -> object:
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("simulated crash outside the per-cell guard")
            return (
                {
                    "config": "x",
                    "day_filter": "off",
                    "strategy": "bos",
                    "timeframe": "1h",
                    "symbol": "BTCUSDT",
                    "n_arms": len(trials),
                    "error": None,
                    "corrected": {
                        "rate": 0.05,
                        "evaluated": 20,
                        "skipped": 0,
                        "rho_before": 0.5,
                        "rho_after": 0.5,
                    },
                    "uncorrected": {
                        "rate": 0.05,
                        "evaluated": 20,
                        "skipped": 0,
                        "rho_before": 0.5,
                        "rho_after": 0.5,
                    },
                    "both": {
                        "rate": 0.05,
                        "evaluated": 20,
                        "skipped": 0,
                        "rho_before": 0.5,
                        "rho_after": 0.5,
                    },
                    "corrections": ARM_CORRECTIONS,
                },
                NullCalibrationResult(0.05, 20, 0, 0.5, 0.5),
                NullCalibrationResult(0.05, 20, 0, 0.5, 0.5),
                NullCalibrationResult(0.05, 20, 0, 0.5, 0.5),
            )

        with (
            patch(
                "tools.wfo_resweep._calibrate_one_cell",
                side_effect=crashing_calibrate_one_cell,
            ),
            patch(
                "tools.wfo_resweep._null_calibration_out_path",
                return_value=out_path,
            ),
            pytest.raises(RuntimeError, match="simulated crash"),
        ):
            _run_null_calibration(
                MagicMock(),
                configs=[config],
                symbols=("BTCUSDT", "ETHUSDT"),
                fee_pct=0.0004,
                min_sl_pct=0.001,
                slippage_pct=0.0002,
                since_ms=0,
                label="test",
                n_replicates=20,
                n_splits=4,
            )

        data = json.loads(out_path.read_text())
        assert data["complete"] is False
        assert len(data["cells"]) == 1, (
            "the checkpoint must have written the FIRST cell's record before "
            "the second cell's crash propagated"
        )


class TestNullCalibrationIsOptIn:
    """ST134 §4b requirement 1 and the global constraint "does not run the
    273 cells" — pinned structurally, since exercising the branch for real
    needs a live DB and a full grid backtest."""

    def test_the_flag_appears_in_help(self) -> None:
        result = subprocess.run(
            [sys.executable, "tools/wfo_resweep.py", "--help"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, result.stderr
        assert "--null-calibration" in result.stdout

    def test_smoke_size_overrides_appear_in_help(self) -> None:
        """Minor fix: --null-calibration-k / --null-calibration-replicates let
        a smoke run (e.g. k=2, replicates=5) exercise the whole per-cell /
        pooling / artifact path in seconds, so the first real k=30 /
        replicates=200 invocation is not also that path's first end-to-end
        run."""
        result = subprocess.run(
            [sys.executable, "tools/wfo_resweep.py", "--help"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, result.stderr
        assert "--null-calibration-k" in result.stdout
        assert "--null-calibration-replicates" in result.stdout

    def test_the_overrides_are_threaded_into_the_call(self) -> None:
        """The flags exist AND actually reach `_run_null_calibration` — a
        parser argument nobody reads would still show up in --help."""
        import ast

        src = Path("tools/wfo_resweep.py").read_text(encoding="utf-8")
        main_fn = next(
            n
            for n in ast.walk(ast.parse(src))
            if isinstance(n, ast.FunctionDef) and n.name == "main"
        )
        call = next(
            n
            for n in ast.walk(main_fn)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "_run_null_calibration"
        )
        kwargs = {kw.arg: kw.value for kw in call.keywords}
        k_value = kwargs.get("k")
        assert (
            isinstance(k_value, ast.Attribute) and k_value.attr == "null_calibration_k"
        ), "k= must read args.null_calibration_k, not the module default directly"
        n_replicates_value = kwargs.get("n_replicates")
        assert (
            isinstance(n_replicates_value, ast.Attribute)
            and n_replicates_value.attr == "null_calibration_replicates"
        ), "n_replicates= must read args.null_calibration_replicates"

    def test_the_help_text_names_the_flags_it_ignores(self) -> None:
        """Cheap fix (Task 10 review): a reader must be able to tell, without
        running it, that --out / --measure-rho / --books have no effect
        under --null-calibration."""
        result = subprocess.run(
            [sys.executable, "tools/wfo_resweep.py", "--help"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, result.stderr
        # argparse wraps help text, so check the words rather than one phrase.
        assert "--out" in result.stdout
        assert "--measure-rho" in result.stdout
        assert "--books" in result.stdout
        assert "ignored" in result.stdout

    def test_the_branch_returns_before_the_per_config_loop_ever_runs(self) -> None:
        """The `if args.null_calibration` branch must be a SIBLING statement
        preceding the `for config_path in configs` loop inside the same
        `try:`, and its body must end in a `return` — never nested INSIDE the
        loop, which would still execute it first before skipping."""
        import ast

        src = Path("tools/wfo_resweep.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        main_fn = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "main"
        )
        try_node = next(n for n in ast.walk(main_fn) if isinstance(n, ast.Try))
        body = try_node.body

        if_idx, if_node = next(
            (i, n)
            for i, n in enumerate(body)
            if isinstance(n, ast.If)
            and isinstance(n.test, ast.Attribute)
            and n.test.attr == "null_calibration"
        )
        for_idx = next(
            i
            for i, n in enumerate(body)
            if isinstance(n, ast.For)
            and isinstance(n.iter, ast.Name)
            and n.iter.id == "configs"
        )
        assert if_idx < for_idx, (
            "the null_calibration branch must precede the per-config loop as "
            "a SIBLING statement in the try body, not be nested inside it"
        )
        assert any(isinstance(s, ast.Return) for s in if_node.body), (
            "the null_calibration branch must end in a return — otherwise "
            "the per-config loop still runs after it"
        )

    def test_the_null_calibration_branch_calls_the_dedicated_runner(self) -> None:
        import ast

        src = Path("tools/wfo_resweep.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        main_fn = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "main"
        )
        try_node = next(n for n in ast.walk(main_fn) if isinstance(n, ast.Try))
        if_node = next(
            n
            for n in try_node.body
            if isinstance(n, ast.If)
            and isinstance(n.test, ast.Attribute)
            and n.test.attr == "null_calibration"
        )
        calls = {
            n.func.id
            for n in ast.walk(if_node)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        assert "_run_null_calibration" in calls
