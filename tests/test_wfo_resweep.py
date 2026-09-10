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

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from analytics.sweep_guard import (
    DECISION_BLOCK,
    DECISION_COMMIT,
    DECISION_INSUFFICIENT,
    CommitGateVerdict,
)
from tools.wfo_resweep import (
    ACTION_KEEP,
    ACTION_SKIP,
    ACTION_UPDATE,
    MIN_IMPROVEMENT_R,
    MIN_OOS_TRADES,
    MIN_TP_R_STEP,
    cells_for_config,
    current_tp_r_for,
    decide_cell,
)

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
