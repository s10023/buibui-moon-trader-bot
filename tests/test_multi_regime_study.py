"""Tests for the multi-regime validation study and its powered-null criterion.

The load-bearing assertions are the two defects that made this tool worth
promoting out of `docs/plans/scratch/`:

* **A powered null is CI containment.** The scratch original declared one from
  `|Δ| < MDE` on a hardcoded `tf in ("15m", "1h")` whitelist. Since
  `MDE = 2.802 * SE`, that is a significance test wearing a power label — it can
  report that an effect was not seen, never that one was ruled out.
* **An empty population is not a null result.** Both regime legs are generated
  by re-running backtests and are overwritten by `/db-update`; the original
  printed `eligible: 0` and exited 0 either way.

`test_filed_cells_*` are the fixtures that span the defect: they carry the real
2026-08-12 numbers and assert that the two criteria **disagree** on them. A
fixture on which old and new agree would pass against the bug and is exactly the
vacuous-guard trap this repo keeps re-finding.
"""

from __future__ import annotations

import duckdb
import pytest

from analytics.audit_guard import DEFAULT_BAR, powered_null
from analytics.store.schema import init_schema
from tools.multi_regime_study import (
    VERDICT_DETECTED,
    VERDICT_INSUFFICIENT,
    VERDICT_POWERED_NULL,
    Z80,
    Z95,
    bonferroni_threshold,
    load_legs,
    ms,
    score_cell,
    select_cells,
)

# The four pre-registered cells exactly as filed in
# docs/audits/2026-08-12-multi-regime-validation.md: (label, delta, SE, filed verdict).
FILED_CELLS = [
    ("cvd_divergence/15m/short", 0.0331, 0.1805, "powered null"),
    ("eqh_eql/15m/short", 0.1577, 0.0676, "nominal hit"),
    ("engulfing/15m/short", -0.0017, 0.0544, "powered null"),
    ("fib_golden_zone/15m/long", -0.0819, 0.0971, "powered null"),
]


def _legs(mean: float, sd: float, n_pairs: int) -> list[float]:
    """A list of ``2 * n_pairs`` values with exactly ``mean`` and ~``sd``."""
    return [mean + sd] * n_pairs + [mean - sd] * n_pairs


# --------------------------------------------------------------------------
# powered_null — the extracted criterion
# --------------------------------------------------------------------------


def test_powered_null_true_only_when_ci_inside_bar() -> None:
    # Positive control: a genuinely contained CI. Without one of these the suite
    # cannot distinguish "correct" from "always False".
    assert powered_null(-0.02, 0.03, bar=0.05) is True
    # Touching the bar is not inside it — the predicate is strict on both ends.
    assert powered_null(-0.05, 0.03, bar=0.05) is False
    assert powered_null(-0.02, 0.05, bar=0.05) is False
    # Straddles the bar on one side.
    assert powered_null(-0.02, 0.30, bar=0.05) is False


def test_powered_null_untested_cell_is_never_powered() -> None:
    """A missing or non-finite bound means untested, which establishes nothing."""
    assert powered_null(None, 0.01, bar=0.05) is False
    assert powered_null(-0.01, None, bar=0.05) is False
    assert powered_null(float("-inf"), 0.01, bar=0.05) is False
    assert powered_null(-0.01, float("nan"), bar=0.05) is False


def test_powered_null_scales_with_the_bar() -> None:
    """`bar` carries the units, so the same CI flips with it (the H15 trap)."""
    assert powered_null(-0.10, 0.10, bar=0.05) is False
    assert powered_null(-0.10, 0.10, bar=0.50) is True


# --------------------------------------------------------------------------
# The filed cells — the fixtures that span the defect
# --------------------------------------------------------------------------


@pytest.mark.parametrize(("label", "delta", "se", "filed"), FILED_CELLS)
def test_filed_cells_none_are_powered_nulls(
    label: str, delta: float, se: float, filed: str
) -> None:
    """0 of 4 filed cells contain their CI inside ±0.05R — 3 were filed as powered."""
    ci_lo, ci_hi = delta - Z95 * se, delta + Z95 * se
    assert powered_null(ci_lo, ci_hi, bar=DEFAULT_BAR) is False, (
        f"{label} was filed as '{filed}' but its CI is "
        f"[{ci_lo:+.4f}, {ci_hi:+.4f}], half-width {Z95 * se / DEFAULT_BAR:.1f}x the bar"
    )


def test_filed_cells_old_and_new_criteria_disagree() -> None:
    """The criteria must disagree here, or this fixture proves nothing.

    Reverting `score_cell` to either old form must flip cells that the corrected
    form does not — that is what makes the mutation detectable.

    Three rules, three different answers on the same four cells:

    * **as the scratch script ran it** — detect at `|t| >= 1.96`, else powered
      null on a `tf in ("15m","1h")` whitelist. Gives the filed 3 + 1 hit.
    * **as the spec's prose kill rule reads** — detect at `|Δ| >= MDE`, i.e.
      `|t| >= 2.802`. Gives **4** powered nulls and no hit: under its own spec
      the `eqh_eql` cell the verdict reported as a nominal hit was never a hit.
      Code and spec diverged and neither noticed.
    * **CI containment** — gives 0.
    """
    mde_z = Z95 + Z80
    as_scripted = [
        label
        for label, delta, se, _ in FILED_CELLS
        if abs(delta / se) < Z95 and abs(delta) < mde_z * se
    ]
    as_specced = [
        label for label, delta, se, _ in FILED_CELLS if abs(delta) < mde_z * se
    ]
    corrected = [
        label
        for label, delta, se, _ in FILED_CELLS
        if powered_null(delta - Z95 * se, delta + Z95 * se, bar=DEFAULT_BAR)
    ]
    assert len(as_scripted) == 3  # the filed table
    assert len(as_specced) == 4  # the spec's own rule, never run
    assert corrected == []


def test_filed_nominal_hit_still_fails_multiplicity() -> None:
    """The verdict DIRECTION is unchanged: the one hit still dies on Bonferroni."""
    threshold = bonferroni_threshold(4)
    assert threshold == pytest.approx(2.498, abs=0.001)
    t_eqh_eql = 0.1577 / 0.0676
    assert abs(t_eqh_eql) >= Z95  # nominally significant
    assert abs(t_eqh_eql) < threshold  # but not after correcting for 4 tests


# --------------------------------------------------------------------------
# score_cell — verdict branch selection
# --------------------------------------------------------------------------


def test_score_cell_tight_null_is_powered() -> None:
    legs = {"bull": _legs(0.0, 0.10, 1000), "bear": _legs(0.0, 0.10, 1000)}
    result = score_cell(("engulfing", "15m", "short"), legs, bar=DEFAULT_BAR)
    assert result.verdict == VERDICT_POWERED_NULL
    assert abs(result.ci_hi) < DEFAULT_BAR


def test_score_cell_wide_null_is_insufficient_not_powered() -> None:
    """The defect's blast radius: a noisy cell must NOT read as 'no effect'."""
    legs = {"bull": _legs(0.0, 1.85, 25), "bear": _legs(0.0, 1.85, 25)}
    result = score_cell(("cvd_divergence", "15m", "short"), legs, bar=DEFAULT_BAR)
    assert result.verdict == VERDICT_INSUFFICIENT
    # Not significant either — the two failures that the old rule conflated.
    assert abs(result.t_stat) < Z95
    assert "does not clear" in result.reason


def test_score_cell_large_effect_is_detected() -> None:
    legs = {"bull": _legs(0.50, 0.10, 500), "bear": _legs(0.0, 0.10, 500)}
    result = score_cell(("eqh_eql", "15m", "short"), legs, bar=DEFAULT_BAR)
    assert result.verdict == VERDICT_DETECTED
    assert result.delta == pytest.approx(0.50, abs=1e-9)
    assert "BULL > bear" in result.reason


def test_score_cell_mde_is_reported_but_does_not_decide() -> None:
    """A cell can be inside its MDE and still not be a powered null."""
    legs = {"bull": _legs(0.0, 1.85, 25), "bear": _legs(0.0, 1.85, 25)}
    result = score_cell(("cvd_divergence", "15m", "short"), legs, bar=DEFAULT_BAR)
    assert abs(result.delta) < result.mde  # the old rule's whole test
    assert result.verdict == VERDICT_INSUFFICIENT  # the new rule disagrees


def test_score_cell_applies_the_grain_deflator() -> None:
    """4h carries 3.331x against 15m's 1.628x — a wider CI on identical data."""
    legs = {"bull": _legs(0.0, 0.50, 200), "bear": _legs(0.0, 0.50, 200)}
    fine = score_cell(("engulfing", "15m", "short"), legs, bar=DEFAULT_BAR)
    coarse = score_cell(("engulfing", "4h", "short"), legs, bar=DEFAULT_BAR)
    assert coarse.se > fine.se
    assert coarse.se / fine.se == pytest.approx(3.331 / 1.628, rel=1e-6)


# --------------------------------------------------------------------------
# select_cells — phase 1 is blind to the split
# --------------------------------------------------------------------------


def test_select_cells_ranks_on_pooled_avg_r() -> None:
    cells = {
        ("low", "15m", "long"): {
            "bull": _legs(0.01, 0.5, 50),
            "bear": _legs(0.01, 0.5, 50),
        },
        ("high", "15m", "long"): {
            "bull": _legs(0.20, 0.5, 50),
            "bear": _legs(0.20, 0.5, 50),
        },
    }
    eligible, _, _ = select_cells(cells)
    assert [key[0] for key, *_ in eligible] == ["high", "low"]


def test_select_cells_drops_thin_and_degenerate() -> None:
    cells = {
        ("thin", "15m", "long"): {
            "bull": _legs(0.1, 0.5, 5),
            "bear": _legs(0.1, 0.5, 50),
        },
        # bos/1d/long's real shape: 36 trades all ~= -1.0076R, sd 0.0022.
        ("degenerate", "15m", "long"): {
            "bull": _legs(-1.0076, 0.0022, 50),
            "bear": _legs(-1.0076, 0.0022, 50),
        },
        ("good", "15m", "long"): {
            "bull": _legs(0.1, 0.5, 50),
            "bear": _legs(0.1, 0.5, 50),
        },
    }
    eligible, dropped_n, dropped_disp = select_cells(cells)
    assert [key[0] for key, *_ in eligible] == ["good"]
    assert (dropped_n, dropped_disp) == (1, 1)


# --------------------------------------------------------------------------
# load_legs — an empty population must not read as a null
# --------------------------------------------------------------------------


def _conn_with_trades(rows: list[tuple[str, int, float]]) -> duckdb.DuckDBPyConnection:
    """`rows` are (strategy, entry_time, pnl_r); other columns are schema filler."""
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    for i, (strategy, entry_time, pnl_r) in enumerate(rows):
        conn.execute(
            "INSERT INTO backtest_trades VALUES "
            "(?, 'run', 'BTCUSDT', '15m', ?, 'short', ?, ?, 100.0, 98.0, 104.0, "
            "?, 104.0, 'tp', ?, false, false)",
            [f"t{i}", strategy, entry_time, entry_time, entry_time + 1, pnl_r],
        )
    return conn


def test_load_legs_splits_the_two_regime_windows() -> None:
    conn = _conn_with_trades(
        [("engulfing", ms(2021) + 1000, 0.5), ("engulfing", ms(2022) + 1000, -0.5)]
    )
    cells = load_legs(conn)
    assert cells[("engulfing", "15m", "short")] == {"bull": [0.5], "bear": [-0.5]}


def test_load_legs_raises_when_a_leg_is_missing() -> None:
    """The real post-/db-update state: trades exist, but not in either window."""
    conn = _conn_with_trades([("engulfing", ms(2026) + 1000, 0.5)])
    with pytest.raises(RuntimeError, match="Regenerate them"):
        load_legs(conn)


def test_load_legs_deduplicates_repeated_runs() -> None:
    """~5.29x duplication in backtest_trades inflates every n and t if kept."""
    entry = ms(2021) + 1000
    conn = _conn_with_trades(
        [
            ("engulfing", entry, 0.5),
            ("engulfing", entry, 0.5),
            ("engulfing", ms(2022), -0.5),
        ]
    )
    cells = load_legs(conn)
    assert cells[("engulfing", "15m", "short")]["bull"] == [0.5]
