"""TradeCard parse/validation matrix (post-pass tests arrive in Task 7)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from card.card import FinalCard, parse_trade_card, post_pass, validate_card_obj
from card.config import CardConfig
from card.errors import CardValidationError
from card.state import AccountState, MarketState, OpenPosition
from portfolio.sizing import (
    MEASUREMENT_F,
    BetSizingRule,
    BookUnlock,
    SizingConfig,
)


def _trade_obj(**overrides: Any) -> dict[str, Any]:
    obj: dict[str, Any] = {
        "verdict": "TRADE",
        "direction": "long",
        "entry": 100.0,
        "sl": 98.0,
        "tp1": 103.0,
        "tp2": 105.0,
        "tp3": 108.0,
        "confluence_score": 6,
        "confluence_inputs": [
            {"input": k, "evidence": f"{k} 1"}
            for k in ("zone_level", "indicator", "indicator", "session", "xs", "pundit")
        ],
        "reasoning": ["a 1", "b 2", "c 3", "d 4", "e 5"],
        "steelman": ["htf 1", "underweighted 2", "catalyst 3", "other 4"],
        "invalidation": "close below 97",
        "expected_hold": "12h",
        "valid_until_utc": "2026-07-11T12:00:00Z",
        "no_trade_reason": None,
    }
    obj.update(overrides)
    return obj


class TestValidation:
    def test_valid_trade_parses(self) -> None:
        card = parse_trade_card(json.dumps(_trade_obj()))
        assert card.verdict == "TRADE"
        assert card.direction == "long"
        assert card.entry == 100.0
        assert card.reasoning[0] == "a 1"

    def test_valid_no_trade_parses(self) -> None:
        obj = _trade_obj(
            verdict="NO_TRADE",
            direction=None,
            entry=None,
            sl=None,
            tp1=None,
            tp2=None,
            tp3=None,
            no_trade_reason="regime conflict",
        )
        card = parse_trade_card(json.dumps(obj))
        assert card.verdict == "NO_TRADE"
        assert card.no_trade_reason == "regime conflict"

    def test_not_json_raises(self) -> None:
        with pytest.raises(CardValidationError):
            parse_trade_card("here is your card: buy")

    def test_bad_verdict(self) -> None:
        assert any(
            "verdict" in e for e in validate_card_obj(_trade_obj(verdict="MAYBE"))
        )

    def test_reasoning_count_bounds(self) -> None:
        assert validate_card_obj(_trade_obj(reasoning=["a"] * 4))
        assert validate_card_obj(_trade_obj(reasoning=["a"] * 9))
        assert not validate_card_obj(_trade_obj(reasoning=["a"] * 8))

    def test_confluence_bounds_and_type(self) -> None:
        assert validate_card_obj(_trade_obj(confluence_score=10))
        assert validate_card_obj(_trade_obj(confluence_score="6"))
        assert validate_card_obj(_trade_obj(confluence_score=True))


def _inputs(*kinds: str) -> list[dict[str, str]]:
    return [{"input": k, "evidence": f"{k} 1"} for k in kinds]


class TestConfluenceInputs:
    """card-v8 (#821): the score's inputs are listed, so the cap is checkable."""

    def test_inputs_parse_into_the_card(self) -> None:
        card = parse_trade_card(json.dumps(_trade_obj()))
        assert len(card.confluence_inputs) == card.confluence_score == 6
        assert card.confluence_inputs[0].input == "zone_level"
        assert card.confluence_inputs[0].evidence == "zone_level 1"

    def test_count_must_match_the_score(self) -> None:
        obj = _trade_obj(confluence_score=3, confluence_inputs=_inputs("xs", "pundit"))
        assert any("must match" in e for e in validate_card_obj(obj))

    def test_zero_score_with_no_inputs_is_valid(self) -> None:
        assert not validate_card_obj(
            _trade_obj(confluence_score=0, confluence_inputs=[])
        )

    def test_one_external_input_is_allowed(self) -> None:
        obj = _trade_obj(
            confluence_score=2, confluence_inputs=_inputs("external_liquidity", "xs")
        )
        assert not validate_card_obj(obj)

    def test_external_liquidity_counted_twice_is_rejected(self) -> None:
        """The cap the scalar score could not see: two clusters, one input."""
        obj = _trade_obj(
            confluence_score=3,
            confluence_inputs=_inputs("external_liquidity", "external_liquidity", "xs"),
        )
        errors = validate_card_obj(obj)
        assert any("external_liquidity 2 times" in e for e in errors)
        with pytest.raises(CardValidationError):
            parse_trade_card(json.dumps(obj))

    def test_other_kinds_may_repeat(self) -> None:
        """Two indicators can be two independent agreeing inputs."""
        obj = _trade_obj(
            confluence_score=2, confluence_inputs=_inputs("indicator", "indicator")
        )
        assert not validate_card_obj(obj)

    @pytest.mark.parametrize(
        "bad",
        [
            None,
            "zone_level",
            [{"input": "vibes", "evidence": "x 1"}],
            [{"input": "xs", "evidence": "   "}],
            [{"input": "xs"}],
            ["xs"],
        ],
    )
    def test_malformed_inputs_are_rejected(self, bad: object) -> None:
        obj = _trade_obj(confluence_score=1, confluence_inputs=bad)
        assert any("confluence_inputs" in e for e in validate_card_obj(obj))

    def test_no_trade_also_lists_its_inputs(self) -> None:
        """The score is required on both verdicts, so its inputs are too."""
        obj = _trade_obj(verdict="NO_TRADE", no_trade_reason="chop")
        del obj["confluence_inputs"]
        assert any("confluence_inputs" in e for e in validate_card_obj(obj))

    def test_trade_requires_prices_and_direction(self) -> None:
        assert validate_card_obj(_trade_obj(entry=None))
        assert validate_card_obj(_trade_obj(sl=-1.0))
        assert validate_card_obj(_trade_obj(direction="up"))
        assert validate_card_obj(_trade_obj(valid_until_utc=None))

    def test_no_trade_requires_reason(self) -> None:
        obj = _trade_obj(verdict="NO_TRADE", no_trade_reason=None)
        assert any("no_trade_reason" in e for e in validate_card_obj(obj))

    def test_non_finite_prices_rejected(self) -> None:
        # json.dumps emits NaN/Infinity literals; json.loads reads them back,
        # so an LLM emitting "entry": NaN must be caught by validation.
        nan_obj = _trade_obj(entry=float("nan"))
        assert any("entry" in e for e in validate_card_obj(nan_obj))
        with pytest.raises(CardValidationError):
            parse_trade_card(json.dumps(nan_obj))

        inf_obj = _trade_obj(tp1=float("inf"))
        assert any("tp1" in e for e in validate_card_obj(inf_obj))
        with pytest.raises(CardValidationError):
            parse_trade_card(json.dumps(inf_obj))

    def test_trade_rejects_a_steelman_that_is_not_four_angles(self) -> None:
        """card-v5: the four angles are fixed, so a dropped one must fail.

        The steelman exists to add a reasoning step the card lacked. A free
        count makes a silently-skipped angle indistinguishable from a card
        that argued all four, which is the failure this field exists to make
        visible.
        """
        assert validate_card_obj(_trade_obj(steelman=["a", "b", "c"]))
        assert validate_card_obj(_trade_obj(steelman=["a", "b", "c", "d", "e"]))
        assert validate_card_obj(_trade_obj(steelman=None))

    def test_trade_rejects_a_blank_steelman_angle(self) -> None:
        # an empty string is a skipped angle wearing the right shape
        assert validate_card_obj(_trade_obj(steelman=["a", "b", "c", "   "]))

    def test_steelman_parses_into_the_card(self) -> None:
        card = parse_trade_card(json.dumps(_trade_obj()))
        assert card.steelman == ["htf 1", "underweighted 2", "catalyst 3", "other 4"]

    def test_no_trade_may_omit_the_steelman(self) -> None:
        """A declined trade has nothing to argue against, so it is optional.

        Requiring it there would buy a fourth-angle paragraph on every card
        the model already rejected, against a testing-capacity bottleneck.
        """
        obj = _trade_obj(
            verdict="NO_TRADE",
            direction=None,
            entry=None,
            sl=None,
            tp1=None,
            tp2=None,
            tp3=None,
            no_trade_reason="regime conflict",
        )
        del obj["steelman"]
        assert not validate_card_obj(obj)
        assert parse_trade_card(json.dumps(obj)).steelman == []


def _state_for_post(
    *,
    ref_close: float = 100.0,
    account: AccountState | None = None,
) -> MarketState:
    from analytics.brief.types import error_panel

    panel = error_panel("BTCUSDT", "x")
    # error_panel has ref_close 0.0; build a usable panel via dataclasses.replace
    import dataclasses

    panel = dataclasses.replace(panel, ref_close=ref_close, error=None)
    return MarketState(
        symbol="BTCUSDT",
        now_ms=1_760_000_000_000,
        direction_hint=None,
        panel=panel,
        session_clock=None,
        pundit=None,
        xs=None,
        recent_fires=[],
        account=account,
        health=[],
    )


def _post(
    card_obj: dict[str, Any],
    state: MarketState,
    *,
    qty_step: float | None = None,
    generated_at_ms: int = 1,
    cfg: CardConfig | None = None,
    sizing: SizingConfig | None = None,
) -> FinalCard:
    card = parse_trade_card(json.dumps(card_obj))
    return post_pass(
        card,
        state,
        sizing or SizingConfig(),
        cfg or CardConfig(),
        digest="d" * 64,
        model="sonnet",
        generated_at_ms=generated_at_ms,
        qty_step=qty_step,
    )


class TestHorizonStamp:
    """`post_pass` is where the operator's `--horizon` becomes part of the
    record. Stamping it here (rather than re-reading the config at each
    ledger write) is what lets both ledgers agree by construction."""

    def test_stamps_the_configured_horizon(self) -> None:
        for horizon in ("intraday", "swing"):
            final = _post(
                _trade_obj(), _state_for_post(), cfg=CardConfig(horizon=horizon)
            )
            assert final.horizon == horizon

    def test_stamp_survives_a_veto(self) -> None:
        """A veto blanks sizing; it must not blank the cohort key, or the
        VETOED rows drop out of any intraday-vs-swing comparison."""
        obj = _trade_obj(valid_until_utc="2020-01-01T00:00:00Z")
        final = _post(
            obj,
            _state_for_post(),
            generated_at_ms=1_760_000_000_000,
            cfg=CardConfig(horizon="swing"),
        )
        assert final.verdict == "VETOED"
        assert final.risk_usd is None  # the veto really did blank sizing
        assert final.horizon == "swing"


class TestPostPass:
    def test_clean_trade_sized_deterministically(self) -> None:
        final = _post(_trade_obj(), _state_for_post())
        assert final.verdict == "TRADE"
        # #915: R = 1% of 10k = 100 USD, one leg of the 3-symbol majors
        # cluster -> 33.33 USD; |entry-sl| = 2 -> 16.67 units
        assert final.risk_usd == pytest.approx(100.0 / 3)
        assert final.size_units == pytest.approx(100.0 / 3 / 2)
        assert final.notional_usd == pytest.approx(100.0 / 3 / 2 * 100.0)
        assert final.rr_tp1 == 1.5
        assert final.sizing_regime == "measurement"
        assert final.veto_reasons == []

    def test_no_trade_passes_through_unsized(self) -> None:
        obj = _trade_obj(
            verdict="NO_TRADE",
            direction=None,
            entry=None,
            sl=None,
            tp1=None,
            tp2=None,
            tp3=None,
            no_trade_reason="gate",
        )
        final = _post(obj, _state_for_post())
        assert final.verdict == "NO_TRADE"
        assert final.size_units is None
        assert final.risk_usd is None

    def test_sl_wrong_side_vetoes(self) -> None:
        final = _post(_trade_obj(sl=101.0), _state_for_post())
        assert final.verdict == "VETOED"
        assert any("SL" in r for r in final.veto_reasons)
        # rr_tp1 was computed against an invalid stop distance — must be nulled
        assert final.rr_tp1 is None

    def test_tp_disorder_vetoes(self) -> None:
        final = _post(_trade_obj(tp2=102.0), _state_for_post())
        assert final.verdict == "VETOED"

    def test_min_rr_floor_vetoes(self) -> None:
        final = _post(_trade_obj(tp1=101.0), _state_for_post())  # RR 0.5
        assert final.verdict == "VETOED"
        assert any("min_rr" in r for r in final.veto_reasons)

    def test_conflicting_position_vetoes(self) -> None:
        account = AccountState(
            positions=[
                OpenPosition(
                    symbol="BTCUSDT",
                    side="short",
                    qty=1.0,
                    entry=100.0,
                    mark=100.0,
                    upnl_usd=0.0,
                )
            ],
            daily_pnl_usd=0.0,
            daily_r=0.0,
            equity_usd=None,
        )
        final = _post(_trade_obj(), _state_for_post(account=account))
        assert final.verdict == "VETOED"
        assert any("conflicting" in r for r in final.veto_reasons)

    def test_circuit_breaker_vetoes(self) -> None:
        account = AccountState(
            positions=[], daily_pnl_usd=-100.0, daily_r=-2.5, equity_usd=None
        )
        final = _post(_trade_obj(), _state_for_post(account=account))
        assert final.verdict == "VETOED"
        assert any("daily loss" in r for r in final.veto_reasons)

    def test_entry_band_vetoes(self) -> None:
        final = _post(
            _trade_obj(entry=120.0, sl=118.0, tp1=124.0, tp2=126.0, tp3=130.0),
            _state_for_post(),
        )
        assert final.verdict == "VETOED"
        assert any("entry" in r for r in final.veto_reasons)

    def test_degraded_account_warns_not_vetoes(self) -> None:
        final = _post(_trade_obj(), _state_for_post(account=None))
        assert final.verdict == "TRADE"
        assert any("account state unavailable" in w for w in final.warnings)

    def test_degraded_panel_warns_ref_unavailable_not_vetoes(self) -> None:
        # A panel that failed to compute (error set, ref_close 0) must skip the
        # entry-band sanity check with a warning, never veto a valid trade.
        import dataclasses

        from analytics.brief.types import error_panel

        state = dataclasses.replace(
            _state_for_post(), panel=error_panel("BTCUSDT", "no data")
        )
        final = _post(_trade_obj(), state)
        assert final.verdict == "TRADE"
        assert any("ref price unavailable" in w for w in final.warnings)

    def test_live_negative_fire_warns_not_vetoes(self) -> None:
        """A live-losing cell is surfaced deterministically, never trusted to
        the LLM alone. Display only — promotion/veto needs the n>=30 gate."""
        import dataclasses

        from card.state import RecentFire

        state = dataclasses.replace(
            _state_for_post(),
            recent_fires=[
                RecentFire(
                    strategy="morning_evening_star",
                    tf="4h",
                    direction="short",
                    open_time=1,
                    entry_price=100.0,
                    stars=5,
                    avg_r=0.946,
                    win_rate=0.7,
                    dsr=0.26,
                    live_n=28,
                    live_avg_r=-0.605,
                )
            ],
        )
        final = _post(_trade_obj(), state)
        assert final.verdict == "TRADE"  # display only, never a veto
        warning = next(w for w in final.warnings if "morning_evening_star" in w)
        assert "5" in warning and "-0.605" in warning and "28" in warning

    def test_live_positive_and_thin_cells_do_not_warn(self) -> None:
        """Only the contradiction warns: a winning cell and an under-powered
        cell must both stay silent, or the warning becomes noise."""
        import dataclasses

        from card.state import RecentFire

        def fire(**kw: Any) -> Any:
            base: dict[str, Any] = {
                "strategy": "bos",
                "tf": "1h",
                "direction": "short",
                "open_time": 1,
                "entry_price": 100.0,
                "stars": 2,
                "avg_r": 0.037,
                "win_rate": 0.5,
                "dsr": 0.5,
                "live_n": 40,
                "live_avg_r": 1.46,
            }
            base.update(kw)
            return RecentFire(**base)

        state = dataclasses.replace(
            _state_for_post(),
            recent_fires=[
                fire(),  # live-positive
                fire(strategy="fvg", live_n=4, live_avg_r=-0.9),  # thin
                fire(strategy="doji", live_n=None, live_avg_r=None),  # no live
            ],
        )
        final = _post(_trade_obj(), state)
        assert not [w for w in final.warnings if "live record contradicts" in w]

    def test_open_sibling_leg_does_not_resize_this_leg(self) -> None:
        """#915 rule 2: an open ETHUSDT leg is a sibling in the same cluster
        entry, so BTCUSDT still takes its fixed 1/3 share — the old r_base
        headroom approximation is gone."""
        account = AccountState(
            positions=[
                OpenPosition(
                    symbol="ETHUSDT",
                    side="long",
                    qty=1.0,
                    entry=100.0,
                    mark=100.0,
                    upnl_usd=0.0,
                )
            ],
            daily_pnl_usd=0.0,
            daily_r=0.0,
            equity_usd=None,
        )
        final = _post(_trade_obj(), _state_for_post(account=account))
        assert final.verdict == "TRADE"
        assert final.risk_usd == pytest.approx(100.0 / 3)
        assert not any("approximated" in w for w in final.warnings)

    def test_sizes_off_live_equity_when_present(self) -> None:
        account = AccountState(
            positions=[], daily_pnl_usd=0.0, daily_r=0.0, equity_usd=1201.33
        )
        final = _post(_trade_obj(), _state_for_post(account=account))
        assert final.verdict == "TRADE"
        # no re-base basis: 1% of live 1201.33 = 12.0133, 1/3 leg = 4.0044
        assert final.risk_usd == pytest.approx(1201.33 * MEASUREMENT_F / 3)
        assert final.size_units == pytest.approx(1201.33 * MEASUREMENT_F / 3 / 2)
        assert final.capital_used == pytest.approx(1201.33)
        assert final.capital_source == "live_equity"
        assert any("no re-base basis configured" in w for w in final.warnings)

    def test_falls_back_to_config_capital_and_warns(self) -> None:
        final = _post(_trade_obj(), _state_for_post())
        assert final.risk_usd == pytest.approx(100.0 / 3)
        assert final.capital_used == pytest.approx(10_000.0)
        assert final.capital_source == "config"
        assert any("configured capital" in w for w in final.warnings)

    def test_vetoed_card_clears_capital_fields(self) -> None:
        # min_rr veto: tp1 too close to entry for the 2.0 risk-per-unit. This
        # veto fires in the `rr_tp1 < cfg.min_rr` check, BEFORE the sizing
        # block ever assigns capital_used/capital_source — so this only checks
        # the two fields against their dataclass initialiser (both already
        # None), never against the `if veto:` clearing line. Kept because
        # it still documents the NO_TRADE-ish path; the real defence of the
        # clearing line is TestLotSizeRounding.test_quantity_rounding_to_zero_vetoes,
        # the only veto reachable AFTER capital_used is assigned.
        final = _post(_trade_obj(tp1=100.5), _state_for_post())
        assert final.verdict == "VETOED"
        assert final.capital_used is None
        assert final.capital_source is None


class TestLotSizeRounding:
    """A quantity that is not a LOT_SIZE multiple is not orderable, and the
    risk it claims is only true before rounding (card-v4 defect, 5/5 live)."""

    def test_quantity_rounds_down_to_qty_step(self) -> None:
        # 33.33 USD leg / |100-98| = 16.67 raw units; step 1.0 floors to 16.
        final = _post(_trade_obj(), _state_for_post(), qty_step=1.0)
        assert final.size_units == 16.0

    def test_stated_risk_is_true_after_rounding(self) -> None:
        # The bug: risk stayed at the pre-rounding 33.33 while 16 units at
        # $2 risk/unit only actually risk 32.0.
        final = _post(_trade_obj(), _state_for_post(), qty_step=1.0)
        assert final.risk_usd == 32.0
        assert final.notional_usd == 1600.0
        # risk_frac must agree with risk_usd, not stay at the pre-rounding share
        assert final.risk_frac == pytest.approx(0.0032)

    def test_quantity_rounding_to_zero_vetoes(self) -> None:
        # 12.5 raw units against a 100-unit step floors to 0 — unsubmittable.
        # This is the ONLY veto reachable after capital_used/capital_source
        # are assigned (they are set inside the `if not veto:` sizing block)
        # — every other veto in this module fires earlier and never reaches
        # that assignment, so this is the real defence of the `if veto:`
        # clearing line. Deleting that line leaves this test failing
        # (verified by mutation).
        final = _post(_trade_obj(), _state_for_post(), qty_step=100.0)
        assert final.verdict == "VETOED"
        assert any("qty_step" in r for r in final.veto_reasons)
        assert final.size_units is None
        assert final.capital_used is None
        assert final.capital_source is None
        assert final.sizing_regime is None

    def test_absent_qty_step_warns_rather_than_silently_unrounded(self) -> None:
        final = _post(_trade_obj(), _state_for_post())
        assert final.size_units == pytest.approx(100.0 / 3 / 2)
        assert any("not LOT_SIZE-rounded" in w for w in final.warnings)

    def test_rounded_risk_frac_uses_resolved_capital_not_the_config_constant(
        self,
    ) -> None:
        """The rounding branch restates risk_frac; it must divide by the SAME
        capital that produced risk_usd. With account=None (as every other
        qty_step test has it) resolved capital equals sizing.capital and a
        mutation to the constant is invisible — live equity is what separates
        them."""
        account = AccountState(
            positions=[], daily_pnl_usd=0.0, daily_r=0.0, equity_usd=1201.33
        )
        final = _post(_trade_obj(), _state_for_post(account=account), qty_step=1.0)
        assert final.verdict == "TRADE"
        assert final.capital_used == pytest.approx(1201.33)
        assert final.size_units == 2.0  # floored from 2.0022
        assert final.risk_usd == pytest.approx(4.0)  # 2.0 units x |100-98|
        assert final.risk_frac == pytest.approx(4.0 / 1201.33)


def _unlock(**kw: Any) -> BookUnlock:
    base: dict[str, Any] = {
        "evidence": "journal 2026-10 window, #915 manual unlock",
        "wins": 58,
        "n": 60,
        "rr_gross": 3.0,
        "stop_pct": 0.02,
    }
    base.update(kw)
    return BookUnlock(**base)


class TestBetSizingRule:
    """#980: the card sizes under the #915 rule. Each test pins one rule."""

    _BASIS = 1000.0

    def _rule(self, **kw: Any) -> SizingConfig:
        return SizingConfig(
            bet_rule=BetSizingRule(basis_usd=self._BASIS, rebased_at="2026-10-01", **kw)
        )

    def test_default_is_the_measurement_size(self) -> None:
        """Rule 5: no book is unlocked, so f is 1% unless config names one."""
        final = _post(_trade_obj(), _state_for_post(), sizing=self._rule())
        assert final.sizing_regime == "measurement"
        assert final.risk_usd == pytest.approx(self._BASIS * 0.01 / 3)

    def test_cluster_entry_is_one_bet_not_one_per_leg(self) -> None:
        """Rule 2: a cluster member takes 1/n of R. The control is the same
        symbol outside any cluster, which takes the whole R — so a mutation
        sizing each leg at a full R reads 3x here and fails."""
        in_cluster = _post(_trade_obj(), _state_for_post(), sizing=self._rule())
        alone = _post(
            _trade_obj(),
            _state_for_post(),
            sizing=SizingConfig(
                clusters=(),
                bet_rule=BetSizingRule(basis_usd=self._BASIS, rebased_at="2026-10-01"),
            ),
        )
        assert alone.risk_usd == pytest.approx(self._BASIS * 0.01)
        assert in_cluster.risk_usd == pytest.approx(self._BASIS * 0.01 / 3)

    def test_rebase_basis_wins_over_live_equity(self) -> None:
        """Rule 1: R holds between re-bases instead of tracking equity."""
        account = AccountState(
            positions=[], daily_pnl_usd=0.0, daily_r=0.0, equity_usd=1201.33
        )
        final = _post(
            _trade_obj(), _state_for_post(account=account), sizing=self._rule()
        )
        assert final.capital_used == self._BASIS
        assert final.capital_source == "rebase"
        assert final.risk_usd == pytest.approx(self._BASIS * 0.01 / 3)
        assert not any("re-base" in w for w in final.warnings)

    def test_sub_lot_leg_is_skipped_and_names_the_rule(self) -> None:
        """Rule 3: 3.33 USD / 2 = 1.67 units floors to 0 at step 5 — skip,
        never size up to one lot."""
        final = _post(
            _trade_obj(), _state_for_post(), qty_step=5.0, sizing=self._rule()
        )
        assert final.verdict == "VETOED"
        assert final.size_units is None
        assert any("sub-lot leg skipped" in r for r in final.veto_reasons)
        assert any("left unused" in r for r in final.veto_reasons)

    def test_unlocked_book_sizes_at_its_rule_four_f(self) -> None:
        """Rule 4 + 6: an unlock in config sizes at min(1/k, half-Kelly) and
        the card says so."""
        unlock = _unlock()
        final = _post(_trade_obj(), _state_for_post(), sizing=self._rule(unlock=unlock))
        assert final.sizing_regime == "unlocked"
        assert unlock.f > MEASUREMENT_F
        assert final.risk_usd == pytest.approx(self._BASIS * unlock.f / 3)

    def test_unlocked_book_with_no_net_edge_vetoes(self) -> None:
        """Rule 4: p capped at 0.45 against RR 1.2 gross has no edge, so f is
        0 and the card refuses to size rather than trading at some default."""
        unlock = _unlock(rr_gross=1.2)
        assert unlock.f == 0.0
        final = _post(_trade_obj(), _state_for_post(), sizing=self._rule(unlock=unlock))
        assert final.verdict == "VETOED"
        assert any("f = 0" in r for r in final.veto_reasons)

    def test_daily_cap_is_one_bet_r(self) -> None:
        """Rule 2: the default breaker fires at exactly -1R and not above."""
        at_cap = AccountState(
            positions=[], daily_pnl_usd=-10.0, daily_r=-1.0, equity_usd=None
        )
        under = AccountState(
            positions=[], daily_pnl_usd=-9.9, daily_r=-0.99, equity_usd=None
        )
        assert _post(_trade_obj(), _state_for_post(account=at_cap)).verdict == (
            "VETOED"
        )
        assert _post(_trade_obj(), _state_for_post(account=under)).verdict == "TRADE"


class TestValidUntilExpiry:
    """A card generated after its own valid_until is expired on arrival
    (observed 2026-08-04: emitted 14:13Z, valid until 13:20Z)."""

    _GEN_MS = 1_760_000_000_000  # 2025-10-09T09:33:20Z

    def test_already_expired_valid_until_vetoes(self) -> None:
        obj = _trade_obj(valid_until_utc="2020-01-01T00:00:00Z")
        final = _post(obj, _state_for_post(), generated_at_ms=self._GEN_MS)
        assert final.verdict == "VETOED"
        assert any("valid_until_utc" in r for r in final.veto_reasons)

    def test_future_valid_until_does_not_veto(self) -> None:
        obj = _trade_obj(valid_until_utc="2099-01-01T00:00:00Z")
        final = _post(obj, _state_for_post(), generated_at_ms=self._GEN_MS)
        assert final.verdict == "TRADE"
        assert final.veto_reasons == []

    def test_unparseable_valid_until_vetoes(self) -> None:
        obj = _trade_obj(valid_until_utc="whenever")
        final = _post(obj, _state_for_post(), generated_at_ms=self._GEN_MS)
        assert final.verdict == "VETOED"
        assert any("valid_until_utc" in r for r in final.veto_reasons)


class TestNetRRFloor:
    """ST93 — `min_rr` was a GROSS floor with no cost term, so a tight-stop card
    cleared the hard rule while being negative-EV at its own first target. The
    bias runs ONE way: it only ever passed trades that should fail, and it bit
    hardest exactly where stops were tightest.
    """

    def test_gross_pass_net_fail_vetoes(self) -> None:
        # 0.5% stop, gross RR 1.10 → drag 0.28R → net 0.82
        final = _post(_trade_obj(sl=99.5, tp1=100.55), _state_for_post())
        assert final.verdict == "VETOED"
        assert any("min_rr" in r for r in final.veto_reasons)

    def test_the_veto_names_the_net_number(self) -> None:
        """A reason quoting the gross number would send the operator to look at
        a figure that clears the floor it is being vetoed by."""
        final = _post(_trade_obj(sl=99.5, tp1=100.55), _state_for_post())
        reason = next(r for r in final.veto_reasons if "min_rr" in r)
        assert "0.82" in reason

    def test_same_tight_stop_clears_when_the_net_number_clears(self) -> None:
        """Scoping proof: the veto is the NET floor, not a blanket rule on tight
        stops. Same 0.5% stop, gross RR 1.40 → net 1.12."""
        final = _post(_trade_obj(sl=99.5, tp1=100.70), _state_for_post())
        assert final.verdict == "TRADE"

    def test_both_numbers_are_emitted(self) -> None:
        """Gross stays on the card: dropping it would make every historical
        `rr_tp1` in `ai-cards.jsonl` incomparable to the new rows."""
        final = _post(_trade_obj(), _state_for_post())  # 2% stop, gross 1.5
        assert final.rr_tp1 == pytest.approx(1.5)
        assert final.rr_tp1_net == pytest.approx(1.43)

    def test_net_is_nulled_alongside_gross_on_an_invalid_stop(self) -> None:
        final = _post(_trade_obj(sl=101.0), _state_for_post())
        assert final.rr_tp1 is None
        assert final.rr_tp1_net is None
