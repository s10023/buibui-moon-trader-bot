"""Tests for the era-study power pricing.

The properties worth pinning are the ones whose failure would produce a
*confidently wrong* pricing rather than a crash — a bar that does not rise with
trial count, or a null that licenses itself. A pricing script is as capable of a
confident wrong answer as the study it prices.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from analytics.eras import EraBoundary
from tools import era_power_price as epp


def _boundary(ts_ms: int, ref: str) -> EraBoundary:
    return EraBoundary(
        ts_ms=ts_ms, label=f"b{ref}", scope="ledger", source="declared", ref=ref
    )


def _sample(n: int = 400, deflator: float = 1.0) -> epp.Sample:
    return epp.Sample(
        r=[0.1, -0.1] * (n // 2),
        fired_ms=list(range(n)),
        symbols=["BTCUSDT"] * n,
        n_raw=n,
        n_eff=3.0,
        t_deflator=deflator,
        sd=1.16,
        skew=0.0,
        kurtosis=3.0,
    )


class TestRequiredSharpe:
    def test_bar_rises_with_trial_count(self) -> None:
        """Trial count dominates n — the whole reason the scan shape is priced."""
        bars = [
            epp.required_sharpe(
                500, n_trials=k, sr_variance=0.15, skew=0.0, kurtosis=3.0
            )
            for k in (1, 12, 27, 44)
        ]
        assert bars == sorted(bars)
        assert bars[0] < bars[-1]

    def test_bar_falls_as_n_grows(self) -> None:
        big = epp.required_sharpe(2000, n_trials=1, sr_variance=0.15, skew=0.0)
        small = epp.required_sharpe(100, n_trials=1, sr_variance=0.15, skew=0.0)
        assert big < small

    def test_single_trial_is_undeflated(self) -> None:
        """k=1 must carry no deflation term, so the floor is assumption-free."""
        a = epp.required_sharpe(500, n_trials=1, sr_variance=0.15, skew=0.0)
        b = epp.required_sharpe(500, n_trials=1, sr_variance=9.99, skew=0.0)
        assert a == pytest.approx(b)

    def test_returned_bar_actually_clears_the_gate(self) -> None:
        from analytics.research_guards import deflated_sharpe_ratio

        sr = epp.required_sharpe(500, n_trials=12, sr_variance=0.15, skew=0.0)
        got = deflated_sharpe_ratio(
            sr, 500, n_trials=12, sr_variance=0.15, skew=0.0, kurtosis=3.0
        )
        assert got >= epp.GATE_DSR


class TestContainment:
    def test_half_width_shrinks_with_n(self) -> None:
        assert epp.containment_half_width(1000, 1.16) < epp.containment_half_width(
            100, 1.16
        )

    def test_null_licensed_only_when_ci_fits_inside_bar(self) -> None:
        sd = 1.16
        n = 1000
        hw = epp.containment_half_width(n, sd)
        assert epp.null_is_licensable(n, sd, bar=hw * 2)
        assert not epp.null_is_licensable(n, sd, bar=hw / 2)

    def test_bar_equal_to_half_width_is_not_licensed(self) -> None:
        """Containment is strict — a CI touching the bar has ruled nothing out."""
        sd, n = 1.16, 1000
        hw = epp.containment_half_width(n, sd)
        assert not epp.null_is_licensable(n, sd, bar=hw)


class TestSample:
    def test_deflated_n_shrinks_by_the_squared_deflator(self) -> None:
        s = _sample(n=400, deflator=2.0)
        assert s.n_deflated == 100

    def test_no_deflation_leaves_n_untouched(self) -> None:
        assert _sample(n=400, deflator=1.0).n_deflated == 400

    def test_empty_ledger_raises_rather_than_reading_as_clean(
        self, tmp_path: Path
    ) -> None:
        db = tmp_path / "empty.db"
        conn = duckdb.connect(str(db))
        conn.execute(
            "create table signal_alert_outcomes ("
            "fired_at_ms bigint, symbol varchar, outcome_r double)"
        )
        conn.close()
        with pytest.raises(RuntimeError, match="nothing to price"):
            epp.load_sample(db)


class TestEraSharpes:
    def test_skips_eras_below_min_n(self) -> None:
        bounds = [_boundary(0, "a"), _boundary(1_000, "b")]
        s = epp.Sample(
            r=[0.5, -0.5] * 20,
            fired_ms=[0] * 5 + [2_000] * 35,
            symbols=["BTCUSDT"] * 40,
            n_raw=40,
            n_eff=1.0,
            t_deflator=1.0,
            sd=0.5,
            skew=0.0,
            kurtosis=3.0,
        )
        assert len(epp.era_sharpes(bounds, s, min_n=30)) == 1

    def test_zero_dispersion_era_is_dropped(self) -> None:
        """A constant era has no Sharpe; including it would poison the variance."""
        bounds = [_boundary(0, "a")]
        s = epp.Sample(
            r=[0.25] * 40,
            fired_ms=[1_000] * 40,
            symbols=["BTCUSDT"] * 40,
            n_raw=40,
            n_eff=1.0,
            t_deflator=1.0,
            sd=0.0,
            skew=0.0,
            kurtosis=3.0,
        )
        assert epp.era_sharpes(bounds, s, min_n=2) == []


class TestReport:
    def test_report_names_both_legs_and_stays_finite(self) -> None:
        bounds = [_boundary(0, "a"), _boundary(200, "b")]
        lines = epp.build_report(_sample(n=400), bounds)
        text = "\n".join(lines)
        assert "LEG A" in text and "LEG B" in text
        # A bar that overflowed to nan/inf would still render as a tidy table.
        assert "nan" not in text.lower()
        assert "inf" not in text.lower()
