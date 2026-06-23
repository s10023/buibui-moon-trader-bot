from __future__ import annotations

from typing import Any

from analytics.xsmom.live import TargetBook, TargetPosition
from trade.overlay import AccountState, RiskLimits, evaluate_overlay
from trade.routing import OrderIntent, OrderPlan


def _limits(**kw: float) -> RiskLimits:
    base: dict[str, Any] = {
        "max_gross_leverage": 3.0,
        "max_position_notional_frac": 0.5,
        "max_drawdown_frac": 0.25,
        "max_run_turnover_frac": 1.0,
        "max_data_staleness_hours": 36.0,
        "min_active_positions": 0,
    }
    base.update(kw)
    return RiskLimits(**base)


def _book(
    positions: list[TargetPosition],
    gross: float = 1.0,
    net: float = 0.0,
    capital: float = 10_000.0,
) -> TargetBook:
    return TargetBook(
        as_of_date="2026-06-21",
        next_period_date="2026-06-22",
        capital=capital,
        governor=1.0,
        active_count=len(positions),
        gross_leverage=gross,
        net_leverage=net,
        positions=positions,
    )


def _plan(
    intents: list[OrderIntent], gross: float = 1.0, net: float = 0.0
) -> OrderPlan:
    return OrderPlan(
        intents=intents,
        skipped=[],
        target_gross_leverage=gross,
        target_net_leverage=net,
    )


def _intent(notional: float) -> OrderIntent:
    return OrderIntent("AAAUSDT", "BUY", 1.0, False, notional, "open")


def test_all_pass_is_allowed() -> None:
    v = evaluate_overlay(
        _plan([_intent(100.0)]),
        _book([]),
        AccountState(10_000.0, 10_000.0, False),
        _limits(),
        data_age_hours=1.0,
    )
    assert v.allowed is True and v.aborts == []


def test_kill_switch_aborts() -> None:
    v = evaluate_overlay(
        _plan([]),
        _book([]),
        AccountState(10_000.0, 10_000.0, True),
        _limits(),
        data_age_hours=1.0,
    )
    assert v.allowed is False and any("kill" in a.lower() for a in v.aborts)


def test_drawdown_aborts() -> None:
    # equity 7000 < peak 10000 * (1-0.25) = 7500
    v = evaluate_overlay(
        _plan([]),
        _book([]),
        AccountState(7_000.0, 10_000.0, False),
        _limits(),
        data_age_hours=1.0,
    )
    assert v.allowed is False and any("drawdown" in a.lower() for a in v.aborts)


def test_gross_leverage_cap_aborts() -> None:
    v = evaluate_overlay(
        _plan([], gross=3.5),
        _book([], gross=3.5),
        AccountState(10_000.0, 10_000.0, False),
        _limits(),
        data_age_hours=1.0,
    )
    assert v.allowed is False and any("gross" in a.lower() for a in v.aborts)


def test_per_instrument_notional_cap_aborts() -> None:
    # leg notional 6000 > 0.5 * 10000 = 5000
    book = _book([TargetPosition("AAAUSDT", "long", 0.6, 6_000.0, 0.0)])
    v = evaluate_overlay(
        _plan([]),
        book,
        AccountState(10_000.0, 10_000.0, False),
        _limits(),
        data_age_hours=1.0,
    )
    assert v.allowed is False and any("notional" in a.lower() for a in v.aborts)


def test_run_turnover_guard_aborts() -> None:
    # total |delta_notional| = 12000 > 1.0 * 10000
    v = evaluate_overlay(
        _plan([_intent(7_000.0), _intent(-5_000.0)]),
        _book([]),
        AccountState(10_000.0, 10_000.0, False),
        _limits(),
        data_age_hours=1.0,
    )
    assert v.allowed is False and any("turnover" in a.lower() for a in v.aborts)


def test_staleness_aborts() -> None:
    v = evaluate_overlay(
        _plan([]),
        _book([]),
        AccountState(10_000.0, 10_000.0, False),
        _limits(),
        data_age_hours=48.0,
    )
    assert v.allowed is False and any("stale" in a.lower() for a in v.aborts)


def test_multiple_breaches_collected() -> None:
    v = evaluate_overlay(
        _plan([], gross=5.0),
        _book([]),
        AccountState(1_000.0, 10_000.0, True),
        _limits(),
        data_age_hours=99.0,
    )
    assert v.allowed is False and len(v.aborts) >= 3


def test_breadth_guard_aborts_thin_book() -> None:
    # active_count = 1 (one position) < min 15
    book = _book([TargetPosition("AAAUSDT", "long", 0.1, 100.0, 0.0)])
    v = evaluate_overlay(
        _plan([]),
        book,
        AccountState(10_000.0, 10_000.0, False),
        _limits(min_active_positions=15),
        data_age_hours=1.0,
    )
    assert v.allowed is False and any("thin book" in a for a in v.aborts)


def test_breadth_guard_passes_full_book() -> None:
    positions = [
        TargetPosition(f"S{i}USDT", "long", 0.1, 100.0, 0.0) for i in range(15)
    ]
    v = evaluate_overlay(
        _plan([_intent(100.0)]),
        _book(positions),
        AccountState(10_000.0, 10_000.0, False),
        _limits(min_active_positions=15),
        data_age_hours=1.0,
    )
    assert v.allowed is True


def test_cold_start_allows_full_build() -> None:
    # establishing (current gross 0 < half target): turnover capped at the
    # gross cap (4.5x), not the tight 1.0x steady-state cap.
    # target_gross_notional = 2.88 * 10000 = 28800; turnover = 28800 < 45000.
    v = evaluate_overlay(
        _plan([_intent(28_800.0)], gross=2.88),
        _book([], gross=2.88),
        AccountState(10_000.0, 10_000.0, False),
        _limits(max_gross_leverage=4.5),
        data_age_hours=1.0,
        current_gross_notional=0.0,
    )
    assert v.allowed is True


def test_cold_start_still_bounded_by_gross_cap() -> None:
    # even establishing, an over-leveraged target trips the separate gross guard
    v = evaluate_overlay(
        _plan([_intent(50_000.0)], gross=5.0),
        _book([], gross=5.0),
        AccountState(10_000.0, 10_000.0, False),
        _limits(max_gross_leverage=4.5),
        data_age_hours=1.0,
        current_gross_notional=0.0,
    )
    assert v.allowed is False and any("gross" in a.lower() for a in v.aborts)


def test_steady_state_turnover_still_capped() -> None:
    # current gross == target gross -> NOT establishing -> tight 1.0x cap.
    # turnover 28800 > 1.0 * 10000 -> abort.
    v = evaluate_overlay(
        _plan([_intent(28_800.0)], gross=2.88),
        _book([], gross=2.88),
        AccountState(10_000.0, 10_000.0, False),
        _limits(max_gross_leverage=4.5),
        data_age_hours=1.0,
        current_gross_notional=28_800.0,
    )
    assert v.allowed is False and any("turnover" in a.lower() for a in v.aborts)
