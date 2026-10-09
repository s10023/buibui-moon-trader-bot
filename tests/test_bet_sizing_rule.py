"""The #915 bet-sizing rule in `portfolio/sizing.py` (#980), one test per rule."""

from __future__ import annotations

import itertools
from pathlib import Path

import pytest

from portfolio.sizing import (
    DAILY_LOSS_CAP_R,
    MANUAL_BOOK_N,
    MEASUREMENT_F,
    P_WIN_CAP,
    REGIME_MEASUREMENT,
    REGIME_UNLOCKED,
    STREAK_TABLE_N,
    BasketLeg,
    BetSizingRule,
    BookUnlock,
    SizingConfig,
    basket_legs,
    half_kelly,
    leg_share_usd,
    open_risk_ceiling_r,
    open_risk_r,
    p_losing_run_at_least,
    resolve_bet_unit,
    round_trip_drag_r,
    rr_net_of_cost,
    size_basket,
    sizing_p,
    streak_k,
    streak_table_n,
    unlocked_f,
    wilson_lower_bound,
)

# #914's alpha = 5% table, k per (N row, p column), copied from
# docs/research/2026-10-07-streak-sizing.md.
# A test fixture, not a source: production computes k, this pins it.
_ISSUE_914_P = (0.35, 0.40, 0.45, 0.50, 0.55, 0.60)
_ISSUE_914_K_ALPHA_5 = {
    100: (15, 13, 12, 10, 9, 8),
    250: (18, 15, 13, 12, 10, 9),
    500: (19, 17, 14, 13, 11, 10),
    1000: (21, 18, 16, 14, 12, 11),
    2000: (23, 19, 17, 15, 13, 11),
}


class TestStreakK:
    def test_reproduces_every_cell_of_the_914_table(self) -> None:
        assert tuple(_ISSUE_914_K_ALPHA_5) == STREAK_TABLE_N
        for n, row in _ISSUE_914_K_ALPHA_5.items():
            for p, k in zip(_ISSUE_914_P, row, strict=True):
                assert streak_k(n, p) == k, (n, p)

    def test_run_probability_reproduces_the_filed_exact_figures(self) -> None:
        """#914's 'exact' column at p = 0.45, k = 10 and p = 0.55, k = 10."""
        assert p_losing_run_at_least(100, 10, 0.55) == pytest.approx(0.10087, abs=5e-6)
        assert p_losing_run_at_least(500, 10, 0.55) == pytest.approx(0.43324, abs=5e-6)
        assert p_losing_run_at_least(1000, 10, 0.55) == pytest.approx(0.68167, abs=5e-6)
        assert p_losing_run_at_least(1000, 10, 0.45) == pytest.approx(0.16982, abs=5e-6)

    def test_run_probability_matches_brute_force_enumeration(self) -> None:
        """An independent route: enumerate all 2^n sequences."""
        q = 0.55
        for n, k in ((10, 3), (12, 4), (9, 1), (8, 8)):
            total = 0.0
            for seq in itertools.product((0, 1), repeat=n):  # 1 = loss
                run = best = 0
                for x in seq:
                    run = run + 1 if x else 0
                    best = max(best, run)
                if best >= k:
                    losses = sum(seq)
                    total += q**losses * (1 - q) ** (n - losses)
            assert p_losing_run_at_least(n, k, q) == pytest.approx(total, abs=1e-12)

    def test_n_rounds_up_to_the_next_table_row(self) -> None:
        assert streak_table_n(1) == 100
        assert streak_table_n(100) == 100
        assert streak_table_n(101) == 250
        assert streak_table_n(1001) == 2000
        # past the last row there is no next row: N is used as is
        assert streak_table_n(5000) == 5000
        # k at 101 bets/year is the 250 row's k, not N = 101's
        assert streak_k(101, 0.45) == 13


class TestP:
    def test_wilson_lower_bound_known_value(self) -> None:
        # 8/10 at 95%: the textbook Wilson interval is (0.4902, 0.9433)
        assert wilson_lower_bound(8, 10) == pytest.approx(0.4902, abs=1e-4)
        assert wilson_lower_bound(0, 10) == 0.0

    def test_cap_binds(self) -> None:
        """Rule 4: a 58/60 journal reads ~0.89 at its Wilson floor; p is 0.45."""
        assert wilson_lower_bound(58, 60) > 0.85
        assert sizing_p(58, 60) == P_WIN_CAP == 0.45
        # below the cap the Wilson bound passes through
        assert sizing_p(20, 60) == pytest.approx(wilson_lower_bound(20, 60))


class TestKelly:
    def test_matches_the_915_examples(self) -> None:
        assert half_kelly(0.40, 1.5) == 0.0  # break-even: exactly zero, not 1e-17
        assert half_kelly(0.40, 2.0) == pytest.approx(0.05)
        assert half_kelly(0.30, 1.5) == 0.0  # negative edge floors at zero
        assert half_kelly(0.45, 0.0) == 0.0
        assert half_kelly(0.45, -1.0) == 0.0

    def test_largest_f_at_the_manual_book_n_is_one_over_twelve(self) -> None:
        """#915: the table's largest f at N = 100 is 8.3% (k = 12, p 0.45)."""
        assert unlocked_f(P_WIN_CAP, 100.0, MANUAL_BOOK_N) == pytest.approx(1 / 12)
        # half-Kelly binds when it is the smaller term
        assert unlocked_f(0.40, 2.0, MANUAL_BOOK_N) == pytest.approx(0.05)

    def test_runs_on_rr_net_of_cost_and_hits_zero_without_net_edge(self) -> None:
        """Rule 4: RR is netted through `round_trip_drag_r`; a construction
        with a gross edge and no net edge sizes to zero."""
        assert rr_net_of_cost(2.0, 0.02) == pytest.approx(
            2.0 - round_trip_drag_r(1.0, 0.98)
        )
        assert half_kelly(0.40, 1.55) > 0.0  # gross: an edge
        assert half_kelly(0.40, rr_net_of_cost(1.55, 0.02)) == 0.0  # net: none
        unlock = BookUnlock(evidence="x", wins=58, n=60, rr_gross=1.2, stop_pct=0.02)
        assert unlock.f == 0.0


class TestRegime:
    def test_default_rule_is_the_measurement_size(self) -> None:
        """Rule 5: nothing is unlocked by default."""
        rule = SizingConfig().bet_rule
        assert rule.regime == REGIME_MEASUREMENT
        assert rule.f == MEASUREMENT_F == 0.01
        assert DAILY_LOSS_CAP_R == 1.0

    def test_unlock_switches_regime_and_f(self) -> None:
        unlock = BookUnlock(evidence="x", wins=58, n=60, rr_gross=3.0, stop_pct=0.02)
        rule = BetSizingRule(unlock=unlock)
        assert rule.regime == REGIME_UNLOCKED
        assert rule.f == pytest.approx(1 / 12)

    def test_resolve_bet_unit_prefers_the_stored_basis(self) -> None:
        """Rule 1: the stored re-base basis, never live equity, when set."""
        cfg = SizingConfig(
            bet_rule=BetSizingRule(basis_usd=1000.0, rebased_at="2026-10-01")
        )
        unit = resolve_bet_unit(cfg, 1201.33)
        assert (unit.basis_usd, unit.basis_source) == (1000.0, "rebase")
        assert unit.r_usd == pytest.approx(10.0)
        live = resolve_bet_unit(SizingConfig(), 1201.33)
        assert live.basis_source == "live_equity"
        assert resolve_bet_unit(SizingConfig(), None).basis_source == "config"


def _legs(btc_step: float) -> list[BasketLeg]:
    return [
        BasketLeg("BTCUSDT", 100.0, 99.0, btc_step),
        BasketLeg("ETHUSDT", 100.0, 99.0, 0.01),
        BasketLeg("SOLUSDT", 100.0, 99.0, 0.01),
    ]


class TestBasket:
    def test_legs_and_share(self) -> None:
        cfg = SizingConfig()
        assert basket_legs("BTCUSDT", cfg) == 3
        assert basket_legs("DOGEUSDT", cfg) == 1
        assert leg_share_usd(30.0, 3) == pytest.approx(10.0)
        with pytest.raises(ValueError):
            leg_share_usd(30.0, 0)

    def test_basket_is_one_bet_split_across_legs(self) -> None:
        """Rule 2: the basket risks one R in total, not one R per leg."""
        units = size_basket(_legs(0.01), 30.0)
        assert units == pytest.approx([10.0, 10.0, 10.0])
        assert sum(units) * 1.0 == pytest.approx(30.0)  # 1.0 risk per unit

    def test_sub_lot_leg_is_skipped_and_its_share_not_reallocated(self) -> None:
        """Rule 3: BTC floors to zero at a 100-unit step. ETH and SOL keep
        exactly the size they had when BTC traded, and the basket trades."""
        full = size_basket(_legs(0.01), 30.0)
        skipped = size_basket(_legs(100.0), 30.0)
        assert skipped[0] == 0.0
        assert skipped[1:] == full[1:]
        assert sum(skipped) == pytest.approx(20.0)  # BTC's 10 left unused


class TestOpenRisk:
    def test_default_ceiling_is_r_open_max_at_the_measurement_size(self) -> None:
        cfg = SizingConfig()
        assert open_risk_ceiling_r(cfg) == pytest.approx(cfg.r_open_max / MEASUREMENT_F)
        assert open_risk_ceiling_r(cfg) == pytest.approx(2.0)

    def test_explicit_ceiling_wins(self) -> None:
        cfg = SizingConfig(bet_rule=BetSizingRule(open_risk_max_r=3.0))
        assert open_risk_ceiling_r(cfg) == 3.0

    def test_one_r_per_cluster_entry(self) -> None:
        cfg = SizingConfig()
        assert open_risk_r([], "BTCUSDT", cfg) == 1
        assert open_risk_r(["ETHUSDT", "SOLUSDT"], "BTCUSDT", cfg) == 1
        assert open_risk_r(["ETHUSDT", "DOGEUSDT"], "BTCUSDT", cfg) == 2
        assert open_risk_r(["DOGEUSDT", "XRPUSDT"], "BTCUSDT", cfg) == 3


class TestConfig:
    def test_from_toml(self, tmp_path: Path) -> None:
        p = tmp_path / "s.toml"
        p.write_text(
            "[bet_sizing]\n"
            "basis_usd = 1000.0\n"
            "rebased_at = 2026-10-01\n"
            "[bet_sizing.unlock]\n"
            'evidence = "journal window"\n'
            "wins = 40\nn = 60\nrr_gross = 2.0\nstop_pct = 0.02\n",
            encoding="utf-8",
        )
        rule = SizingConfig.from_toml(p).bet_rule
        assert rule.basis_usd == 1000.0
        assert rule.rebased_at == "2026-10-01"
        assert rule.unlock is not None
        assert rule.unlock.bets_per_year == MANUAL_BOOK_N
        assert rule.regime == REGIME_UNLOCKED

    def test_without_block_stays_measurement(self, tmp_path: Path) -> None:
        p = tmp_path / "s.toml"
        p.write_text("[portfolio]\ncapital = 500.0\n", encoding="utf-8")
        assert SizingConfig.from_toml(p).bet_rule == BetSizingRule()

    @pytest.mark.parametrize(
        "block",
        [
            "[bet_sizing]\nbasis_usd = 1000.0\n",  # basis without its date
            '[bet_sizing]\nbasis_usd = 1000.0\nrebased_at = "soon"\n',
            "[bet_sizing]\nbasis_usd = -5.0\nrebased_at = 2026-10-01\n",
            "[bet_sizing]\nbasis = 1000.0\n",  # unknown key
            "[bet_sizing]\nopen_risk_max_r = -1.0\n",
            '[bet_sizing.unlock]\nevidence = "x"\nwins = 70\nn = 60\n'
            "rr_gross = 2.0\nstop_pct = 0.02\n",  # wins > n
            '[bet_sizing.unlock]\nevidence = ""\nwins = 1\nn = 60\n'
            "rr_gross = 2.0\nstop_pct = 0.02\n",  # evidence must be named
            '[bet_sizing.unlock]\nevidence = "x"\nwins = 1\nn = 60\n'
            "rr_gross = 2.0\n",  # missing stop_pct
            '[bet_sizing.unlock]\nevidence = "x"\nwins = 1\nn = 60\n'
            "rr_gross = 2.0\nstop_pct = 2.0\n",  # stop_pct is a fraction
        ],
    )
    def test_rejects_degenerate_config(self, tmp_path: Path, block: str) -> None:
        p = tmp_path / "s.toml"
        p.write_text(block, encoding="utf-8")
        with pytest.raises(ValueError):
            SizingConfig.from_toml(p)
