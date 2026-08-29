"""Tests for tools/decay_review.py and the shared run-selection it audits through.

The load-bearing assertions here are the two defects that made this tool worth
promoting out of `docs/plans/scratch/`:

* **Sweep-first selection.** The live gate writes a `backtest_runs` row every 15
  minutes, so plain recency hands the deliberate sweep's cell to the daemon. The
  scratch copy of this tool had drifted back to recency-only and was silently
  auditing a different run population than the gate it reports on.
* **Headroom at each cell's OWN n.** The required Sharpe falls as n rises, so a
  bar computed at the median n can report a gate as reachable while every
  individual cell fails it.
"""

from __future__ import annotations

import duckdb
import pytest

from analytics.recalibrate_lib import select_rated_run_ids
from analytics.store.schema import init_schema
from tools.decay_review import (
    DEFAULT_CONFIGS,
    GATE,
    CellHeadroom,
    ScopeReport,
    analyse_scope,
    pools_by_scope,
    render_scope,
    required_sharpe,
    resolve_config_scopes,
    scopes_to_review,
)

# Every NOT NULL column the production schema declares but this test does not
# exercise. Supplying them here (rather than trimming the fixture to the columns
# under test) is what keeps `init_schema` meaningful — a hand-rolled subset would
# accept rows the real table rejects.
_RUN_DEFAULTS: dict[str, object] = {
    "data_start_ms": 0,
    "data_end_ms": 1,
    "days": 1,
    "sl_pct": 0.02,
    "tp_r": 2.0,
    "fee_pct": 0.0004,
    "day_filter": "all",
    "smt_trend_filter": 0,
    "total_signals": 40,
    "win_count": 20,
    "loss_count": 20,
    "win_rate": 0.5,
    "avg_r": 0.1,
    "total_r": 4.0,
    "max_drawdown_r": -1.0,
    # _build_run_filter(None, None) appends "AND adr_suppress_threshold IS NULL".
    "adr_suppress_threshold": None,
}
_TRADE_DEFAULTS: dict[str, object] = {
    "signal_time": 0,
    "entry_time": 0,
    "entry_price": 100.0,
    "sl_price": 98.0,
    "tp_price": 104.0,
}


def _insert(
    conn: duckdb.DuckDBPyConnection, table: str, row: dict[str, object]
) -> None:
    cols = ", ".join(row)
    placeholders = ", ".join("?" * len(row))
    conn.execute(
        f"INSERT INTO {table} ({cols}) VALUES ({placeholders})", list(row.values())
    )


@pytest.fixture
def conn() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect(":memory:")
    init_schema(c)  # real production schema, never a hand-rolled subset
    return c


def _add_run(
    conn: duckdb.DuckDBPyConnection,
    run_id: str,
    *,
    run_at_ms: int,
    sweep_id: str | None,
    strategy: str = "bos",
    tf: str = "15m",
    symbol: str = "BTCUSDT",
    closed_trades: int = 40,
    detector_params: str | None = None,
) -> None:
    row: dict[str, object] = {
        "run_id": run_id,
        "symbol": symbol,
        "timeframe": tf,
        "strategy": strategy,
        "run_at_ms": run_at_ms,
        "sweep_id": sweep_id,
        "closed_trades": closed_trades,
        **_RUN_DEFAULTS,
    }
    # Omitted (not merely None) when unset, matching every other test row —
    # DuckDB leaves the column NULL, which is what `AND detector_params IS
    # NULL` in `_build_run_filter` (ST104 P1) already expects.
    if detector_params is not None:
        row["detector_params"] = detector_params
    _insert(conn, "backtest_runs", row)


def _add_trades(
    conn: duckdb.DuckDBPyConnection,
    run_id: str,
    *,
    marker: float,
    count: int = 40,
    strategy: str = "bos",
    tf: str = "15m",
    direction: str = "long",
) -> None:
    """Seed `count` closed trades whose pnl_r is centred on `marker`.

    Dispersion is deliberate: `_sharpe` returns None on sd == 0.0 exactly, so a
    constant pool would drop out of the family and make the test vacuous.
    """
    for i in range(count):
        _insert(
            conn,
            "backtest_trades",
            {
                "trade_id": f"{run_id}-{direction}-{i}",
                "run_id": run_id,
                "symbol": "BTCUSDT",
                "timeframe": tf,
                "strategy": strategy,
                "direction": direction,
                "outcome": "win" if i % 2 else "loss",
                "pnl_r": marker + (0.1 if i % 2 else -0.1),
                **_TRADE_DEFAULTS,
            },
        )


class TestSweepFirstSelection:
    """Regression cover for the writer collision (#606)."""

    def test_sweep_row_beats_a_newer_daemon_row(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        _add_run(conn, "sweep", run_at_ms=1_000, sweep_id="sweep-42")
        _add_run(conn, "daemon", run_at_ms=9_999, sweep_id=None)
        assert select_rated_run_ids(conn) == ["sweep"]

    def test_recency_breaks_ties_within_the_sweep_class(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        _add_run(conn, "old", run_at_ms=1_000, sweep_id="sweep-1")
        _add_run(conn, "new", run_at_ms=2_000, sweep_id="sweep-2")
        assert select_rated_run_ids(conn) == ["new"]

    def test_daemon_row_is_used_when_no_sweep_exists(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        _add_run(conn, "daemon", run_at_ms=5_000, sweep_id=None)
        assert select_rated_run_ids(conn) == ["daemon"]

    def test_selection_is_per_symbol(self, conn: duckdb.DuckDBPyConnection) -> None:
        _add_run(conn, "btc", run_at_ms=1_000, sweep_id="s1", symbol="BTCUSDT")
        _add_run(conn, "eth", run_at_ms=1_000, sweep_id="s1", symbol="ETHUSDT")
        assert sorted(select_rated_run_ids(conn)) == ["btc", "eth"]

    def test_runs_with_no_closed_trades_are_excluded(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        _add_run(conn, "empty", run_at_ms=1_000, sweep_id="s1", closed_trades=0)
        assert select_rated_run_ids(conn) == []


class TestDetectorParamsExcludedFromRatedSelection:
    """ST104 P1: a retuned study row must never silently join production ratings.

    Mirrors ``live_parity``'s exclusion (ST86) exactly — a row saved under
    non-default detector params answers "how would this cell have scored
    under a retune", not "how did it score", so `_build_run_filter` (shared
    by `select_rated_run_ids` and `get_backtest_win_rates`) drops it by
    default even when it is the newest and only row for the cell.
    """

    def test_detector_params_row_is_excluded_even_as_the_only_row(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        _add_run(
            conn,
            "retune",
            run_at_ms=1_000,
            sweep_id="sweep-1",
            detector_params='{"lookback": 400}',
        )
        assert select_rated_run_ids(conn) == []

    def test_default_row_still_selected_beside_an_excluded_retune(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        _add_run(conn, "default", run_at_ms=1_000, sweep_id="sweep-1")
        _add_run(
            conn,
            "retune",
            run_at_ms=9_999,
            sweep_id="sweep-2",
            detector_params='{"lookback": 400}',
        )
        assert select_rated_run_ids(conn) == ["default"]


class TestPoolsReadThroughTheSelection:
    def test_daemon_trades_are_excluded_when_a_sweep_row_exists(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        """The assertion the drifted scratch copy would have failed."""
        _add_run(conn, "sweep", run_at_ms=1_000, sweep_id="sweep-42")
        _add_run(conn, "daemon", run_at_ms=9_999, sweep_id=None)
        _add_trades(conn, "sweep", marker=1.0)
        _add_trades(conn, "daemon", marker=-5.0)

        pools = pools_by_scope(conn)
        returns = pools["combined"][("bos", "15m")]

        assert len(returns) == 40
        assert all(r > 0 for r in returns), "daemon trades leaked into the pool"

    def test_direction_scopes_split_out(self, conn: duckdb.DuckDBPyConnection) -> None:
        _add_run(conn, "sweep", run_at_ms=1_000, sweep_id="s1")
        _add_trades(conn, "sweep", marker=1.0, count=20, direction="long")
        _add_trades(conn, "sweep", marker=1.0, count=20, direction="short")

        pools = pools_by_scope(conn)
        cell = ("bos", "15m")
        assert len(pools["combined"][cell]) == 40
        assert len(pools["long"][cell]) == 20
        assert len(pools["short"][cell]) == 20

    def test_no_runs_yields_empty_scopes(self, conn: duckdb.DuckDBPyConnection) -> None:
        pools = pools_by_scope(conn)
        assert pools["combined"] == {}
        assert set(pools) == {"combined", "long", "short"}


class TestRequiredSharpe:
    def test_bar_falls_as_n_rises(self) -> None:
        """The monotonicity that makes a median-n bar the wrong comparison."""
        bars = [required_sharpe(0.4, n) for n in (30, 100, 500, 2000)]
        assert bars == sorted(bars, reverse=True)

    def test_bar_never_undercuts_the_benchmark(self) -> None:
        assert required_sharpe(0.4, 10_000) >= 0.4


class TestHeadroomUsesEachCellsOwnN:
    """The decay review's central finding, as a test."""

    def _report(self) -> ScopeReport:
        # Best Sharpe (0.50) clears the median-n bar (0.30) — so a median-n read
        # reports the gate REACHABLE. Every cell fails its own bar.
        return ScopeReport(
            scope="combined",
            family=(0.50, 0.10),
            sr0=0.40,
            n_median=500,
            bar_at_median_n=0.30,
            cells=(
                CellHeadroom(
                    ("a", "15m"), sharpe=0.50, n_obs=40, required=0.90, dsr=0.10
                ),
                CellHeadroom(
                    ("b", "1h"), sharpe=0.10, n_obs=900, required=0.25, dsr=0.05
                ),
            ),
        )

    def test_median_n_bar_would_say_reachable(self) -> None:
        report = self._report()
        best = max(c.sharpe for c in report.cells)
        assert best > report.bar_at_median_n  # the misleading comparison

    def test_own_n_bar_says_not_reachable(self) -> None:
        assert self._report().reachable is False

    def test_closest_is_by_headroom_not_by_sharpe(self) -> None:
        report = self._report()
        assert report.closest.cell == ("b", "1h")
        assert max(report.cells, key=lambda c: c.sharpe).cell == ("a", "15m")

    def test_reachable_when_a_cell_clears_its_own_bar(self) -> None:
        report = ScopeReport(
            scope="combined",
            family=(1.20, 0.10),
            sr0=0.40,
            n_median=500,
            bar_at_median_n=0.30,
            cells=(
                CellHeadroom(
                    ("a", "15m"), sharpe=1.20, n_obs=900, required=0.25, dsr=0.99
                ),
            ),
        )
        assert report.reachable is True
        assert report.passing == report.cells

    def test_render_marks_the_median_bar_as_a_stamp_not_a_verdict(self) -> None:
        lines = "\n".join(render_scope(self._report()))
        assert "scale stamp, NOT a verdict" in lines
        assert "REACHABLE: NO" in lines


class TestCellHeadroom:
    def test_headroom_is_sharpe_minus_its_own_bar(self) -> None:
        cell = CellHeadroom(
            ("a", "15m"), sharpe=0.80, n_obs=100, required=0.50, dsr=0.9
        )
        assert cell.headroom == pytest.approx(0.30)

    def test_clears_gate_is_inclusive_at_the_boundary(self) -> None:
        cell = CellHeadroom(("a", "15m"), sharpe=1.0, n_obs=100, required=0.5, dsr=GATE)
        assert cell.clears_gate is True


class TestAnalyseScope:
    def test_family_smaller_than_two_returns_none(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        """One trial has no dispersion to deflate against — report nothing."""
        _add_run(conn, "sweep", run_at_ms=1_000, sweep_id="s1")
        _add_trades(conn, "sweep", marker=1.0)
        pools = pools_by_scope(conn)
        assert analyse_scope("combined", pools["combined"]) is None

    def test_cells_below_min_trades_are_excluded(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        _add_run(conn, "a", run_at_ms=1_000, sweep_id="s1", strategy="bos")
        _add_run(conn, "b", run_at_ms=1_000, sweep_id="s1", strategy="fvg")
        _add_trades(conn, "a", marker=1.0, count=40, strategy="bos")
        _add_trades(conn, "b", marker=0.5, count=5, strategy="fvg")

        report = analyse_scope("combined", pools_by_scope(conn)["combined"])
        assert report is None  # only one cell survives the min-trades floor

    def test_scores_every_eligible_cell(self, conn: duckdb.DuckDBPyConnection) -> None:
        _add_run(conn, "a", run_at_ms=1_000, sweep_id="s1", strategy="bos")
        _add_run(conn, "b", run_at_ms=1_000, sweep_id="s1", strategy="fvg")
        _add_trades(conn, "a", marker=1.0, count=40, strategy="bos")
        _add_trades(conn, "b", marker=0.4, count=40, strategy="fvg")

        report = analyse_scope("combined", pools_by_scope(conn)["combined"])
        assert report is not None
        assert len(report.cells) == 2
        assert {c.cell for c in report.cells} == {("bos", "15m"), ("fvg", "15m")}
        assert all(c.n_obs == 40 for c in report.cells)
        # Every cell is scored against a bar computed at its own n.
        assert all(
            c.required == required_sharpe(report.sr0, c.n_obs) for c in report.cells
        )


class TestConfigScopeResolution:
    """Regression cover for the dead-pool defect found 2026-08-13.

    `--adr-suppress-threshold` defaulted to None, which `select_rated_run_ids`
    turns into `adr_suppress_threshold IS NULL`. All three live configs save
    under 0.75 / 0.65 / 0.70, so the default selected only pre-May runs — a pool
    whose trades ended 2026-04-09. Every decay review to that date, the 08-11
    first run included, audited it. The verdict direction survived (0 cells clear
    either way), which is exactly why nobody caught it.
    """

    def test_live_configs_resolve_to_their_real_day_filter_and_adr_pairs(
        self,
    ) -> None:
        scopes = resolve_config_scopes(DEFAULT_CONFIGS)
        assert {(s.day_filter, s.adr_suppress_threshold) for s in scopes} == {
            ("tue_thu", 0.75),
            ("mon_fri", 0.65),
            ("weekend", 0.70),
        }

    def test_no_live_scope_resolves_a_null_adr_threshold(self) -> None:
        """The defect itself: a None threshold selects the dead pre-May pool."""
        for scope in resolve_config_scopes(DEFAULT_CONFIGS):
            assert scope.adr_suppress_threshold is not None, (
                f"{scope.name} resolved a NULL ADR threshold — that selects runs "
                "saved with no ADR gate, which no live config uses"
            )

    def test_scope_carries_the_config_name_for_reporting(self) -> None:
        assert {s.name for s in resolve_config_scopes(DEFAULT_CONFIGS)} == {
            "signal_watch",
            "signal_watch_weekdays",
            "signal_watch_all",
        }


class TestScopesToReview:
    """What the tool audits for a given invocation.

    The defect was in the DEFAULT path, not the flags — `make
    buibui-decay-review` takes no arguments and `SKILL.md` documents it that
    way, so a correct-but-optional flag would have fixed nothing.
    """

    def test_bare_invocation_audits_every_live_config(self) -> None:
        scopes = scopes_to_review(config=None, day_filter=None, adr=None)
        assert {(s.day_filter, s.adr_suppress_threshold) for s in scopes} == {
            ("tue_thu", 0.75),
            ("mon_fri", 0.65),
            ("weekend", 0.70),
        }

    def test_config_flag_resolves_both_halves_from_that_config(self) -> None:
        scopes = scopes_to_review(
            config="config/signal_watch_weekdays.toml", day_filter=None, adr=None
        )
        assert len(scopes) == 1
        assert scopes[0].day_filter == "mon_fri"
        assert scopes[0].adr_suppress_threshold == 0.65

    def test_explicit_flags_are_honoured_verbatim_for_ad_hoc_scoping(self) -> None:
        scopes = scopes_to_review(config=None, day_filter="tue_thu", adr=0.80)
        assert len(scopes) == 1
        assert scopes[0].day_filter == "tue_thu"
        assert scopes[0].adr_suppress_threshold == 0.80

    def test_a_bare_day_filter_no_longer_silently_nulls_the_adr_threshold(
        self,
    ) -> None:
        """`--day-filter tue_thu` alone used to select the dead pre-May pool."""
        with pytest.raises(SystemExit):
            scopes_to_review(config=None, day_filter="tue_thu", adr=None)
