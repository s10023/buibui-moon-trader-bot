"""TradeCard parse/validation matrix (post-pass tests arrive in Task 7)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from card.card import FinalCard, parse_trade_card, post_pass, validate_card_obj
from card.config import CardConfig
from card.errors import CardValidationError
from card.state import AccountState, MarketState, OpenPosition
from portfolio.sizing import SizingConfig


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
        "reasoning": ["a 1", "b 2", "c 3", "d 4", "e 5"],
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

    def test_trade_requires_prices_and_direction(self) -> None:
        assert validate_card_obj(_trade_obj(entry=None))
        assert validate_card_obj(_trade_obj(sl=-1.0))
        assert validate_card_obj(_trade_obj(direction="up"))
        assert validate_card_obj(_trade_obj(valid_until_utc=None))

    def test_no_trade_requires_reason(self) -> None:
        obj = _trade_obj(verdict="NO_TRADE", no_trade_reason=None)
        assert any("no_trade_reason" in e for e in validate_card_obj(obj))


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


def _post(card_obj: dict[str, Any], state: MarketState) -> FinalCard:
    card = parse_trade_card(json.dumps(card_obj))
    return post_pass(
        card,
        state,
        SizingConfig(),
        CardConfig(),
        digest="d" * 64,
        model="sonnet",
        generated_at_ms=1,
    )


class TestPostPass:
    def test_clean_trade_sized_deterministically(self) -> None:
        final = _post(_trade_obj(), _state_for_post())
        assert final.verdict == "TRADE"
        # r_base 0.25% of 10k = 25 USD risk; |entry-sl| = 2 -> 12.5 units
        assert final.risk_usd == 25.0
        assert final.size_units == 12.5
        assert final.notional_usd == 1250.0
        assert final.rr_tp1 == 1.5
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

    def test_cluster_cap_consumes_headroom(self) -> None:
        # ETHUSDT open long is in the majors cluster with BTCUSDT:
        # cluster headroom 1% - 0.25% = 0.75% >= r_eff 0.25% -> still sized,
        # but the approximation warning is present.
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
        assert any("approximated" in w for w in final.warnings)
