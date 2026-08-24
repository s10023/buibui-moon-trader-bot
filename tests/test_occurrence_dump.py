"""ST63 occurrence dump — the no-verdict contract is the load-bearing test here.

The dump exists BECAUSE the gated variant is unreachable by trial count. That
split only holds if the tool never grows a verdict column, so the absence is
pinned structurally rather than left to prose — and the pin itself is
mutation-tested, since a check that cannot fail is not a check.
"""

from __future__ import annotations

import pandas as pd
import pytest

from analytics.indicator_condition import _AXES
from tools.occurrence_dump import (
    _VERDICT_WORDS,
    build_occurrence_rows,
    format_report,
    summarize,
    tf_rank,
)


def _tagged(rows: list[dict[str, object]]) -> pd.DataFrame:
    """A tagged frame with every axis present, defaulting to None."""
    base: list[dict[str, object]] = []
    for r in rows:
        rec: dict[str, object] = dict.fromkeys(_AXES)
        rec.update(r)
        base.append(rec)
    return pd.DataFrame(base)


def _fire(
    *,
    tf: str = "1d",
    strategy: str = "fvg",
    direction: str = "long",
    symbol: str = "BTCUSDT",
    entry_time: int = 1_700_000_000_000,
    pnl_r: float = 1.0,
    **axes: object,
) -> dict[str, object]:
    rec: dict[str, object] = {
        "symbol": symbol,
        "tf": tf,
        "strategy": strategy,
        "direction": direction,
        "entry_time": entry_time,
        "pnl_r": pnl_r,
    }
    rec.update(axes)
    return rec


class TestTfRank:
    def test_highest_timeframe_ranks_first(self) -> None:
        assert tf_rank("1w") < tf_rank("1d") < tf_rank("4h") < tf_rank("1h")
        assert tf_rank("1h") < tf_rank("15m")

    def test_unknown_timeframe_sorts_last_rather_than_raising(self) -> None:
        assert tf_rank("3d") > tf_rank("1m")


class TestBuildOccurrenceRows:
    def test_one_row_per_fire_and_column_order_is_stable(self) -> None:
        out = build_occurrence_rows(
            _tagged([_fire(), _fire(entry_time=1_700_000_001_000)])
        )
        assert len(out) == 2
        assert list(out.columns) == [
            "symbol",
            "tf",
            "strategy",
            "direction",
            "entry_time",
            "entry_utc",
            "pnl_r",
            *_AXES,
        ]

    def test_walks_highest_timeframe_down(self) -> None:
        out = build_occurrence_rows(
            _tagged([_fire(tf="15m"), _fire(tf="1w"), _fire(tf="4h")])
        )
        assert list(out["tf"]) == ["1w", "4h", "15m"]

    def test_missing_axis_column_is_filled_not_dropped(self) -> None:
        # A tagged frame that never saw one axis must still emit its column,
        # or the CSV silently changes shape between runs.
        thin = pd.DataFrame([_fire()])
        out = build_occurrence_rows(thin)
        assert all(axis in out.columns for axis in _AXES)

    def test_empty_input_keeps_the_schema(self) -> None:
        out = build_occurrence_rows(pd.DataFrame())
        assert list(out.columns)[:7] == [
            "symbol",
            "tf",
            "strategy",
            "direction",
            "entry_time",
            "entry_utc",
            "pnl_r",
        ]
        assert out.empty


class TestSummarize:
    def test_cell_arithmetic(self) -> None:
        rows = build_occurrence_rows(
            _tagged(
                [
                    _fire(pnl_r=2.0, vwap_weekly="above"),
                    _fire(pnl_r=-1.0, vwap_weekly="above"),
                    _fire(pnl_r=-1.0, vwap_weekly="below"),
                    _fire(pnl_r=-1.0, vwap_weekly="below"),
                ]
            )
        )
        s = summarize(rows, min_n=1)
        above = s[(s["axis"] == "vwap_weekly") & (s["state"] == "above")].iloc[0]
        assert above["n"] == 2
        assert above["avg_r"] == pytest.approx(0.5)
        assert above["win_rate"] == pytest.approx(0.5)
        # base rate is the (tf, strategy, direction) mean: (2 -1 -1 -1)/4 = -0.25
        assert above["base_r"] == pytest.approx(-0.25)
        assert above["delta"] == pytest.approx(0.75)

    def test_rows_with_a_null_axis_are_excluded_from_that_axis_only(self) -> None:
        rows = build_occurrence_rows(
            _tagged(
                [
                    _fire(pnl_r=1.0, vwap_weekly="above", regime="trend"),
                    _fire(pnl_r=1.0, vwap_weekly=None, regime="trend"),
                ]
            )
        )
        s = summarize(rows, min_n=1)
        vwap = s[s["axis"] == "vwap_weekly"]
        regime = s[s["axis"] == "regime"]
        assert int(vwap["n"].sum()) == 1
        assert int(regime["n"].sum()) == 2

    def test_min_n_filters_the_summary_and_never_the_dump(self) -> None:
        rows = build_occurrence_rows(
            _tagged([_fire(pnl_r=1.0, vwap_weekly="above") for _ in range(3)])
        )
        assert summarize(rows, min_n=1).shape[0] > 0
        assert summarize(rows, min_n=99).empty
        # the dump itself is untouched by min_n — it has no such argument
        assert len(rows) == 3


class TestNoVerdictContract:
    """The reason this tool is allowed to exist at all."""

    def _report(self) -> str:
        rows = build_occurrence_rows(
            _tagged(
                [
                    _fire(pnl_r=2.0, vwap_weekly="above", regime="trend"),
                    _fire(pnl_r=-1.0, vwap_weekly="below", regime="range"),
                ]
            )
        )
        return format_report(
            rows,
            summarize(rows, min_n=1),
            source="backtest",
            min_n=1,
            n_cells_total=2,
        )

    def test_report_emits_no_verdict_vocabulary(self) -> None:
        report = self._report()
        for word in _VERDICT_WORDS:
            assert word not in report, f"{word!r} leaked into a diagnostic-only report"

    def test_report_states_that_it_is_not_a_recommendation(self) -> None:
        report = self._report()
        assert "NO VERDICT" in report
        assert "not a recommendation" in report

    def test_report_prints_the_trial_count(self) -> None:
        # The cell count is the whole reason a gated variant is unreachable, so
        # it must be on screen rather than inferred.
        assert "trial count" in self._report()

    def test_the_no_verdict_pin_has_teeth(self) -> None:
        # Mutation control: the assertion above must actually fail on a report
        # that DOES carry a verdict, or it is green by construction.
        poisoned = self._report() + "\nverdict: BUILD"
        leaked = [w for w in _VERDICT_WORDS if w in poisoned]
        assert leaked == ["BUILD"]

    def test_summary_frame_carries_no_verdict_column(self) -> None:
        rows = build_occurrence_rows(_tagged([_fire(vwap_weekly="above")]))
        cols = {c.lower() for c in summarize(rows, min_n=1).columns}
        assert "verdict" not in cols
        assert not cols & {"build", "avoid", "keep", "kill"}
