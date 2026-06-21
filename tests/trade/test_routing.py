from __future__ import annotations

from analytics.xsmom.live import TargetBook, TargetPosition
from trade.routing import ExchangeFilters, build_order_plan


def _filters(
    sym: str, step: float = 0.001, min_qty: float = 0.001, min_notional: float = 5.0
) -> ExchangeFilters:
    return ExchangeFilters(
        symbol=sym, qty_step=step, min_qty=min_qty, min_notional=min_notional
    )


def _book(positions: list[TargetPosition], capital: float = 10_000.0) -> TargetBook:
    gross = sum(abs(p.leverage) for p in positions)
    net = sum(p.leverage for p in positions)
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


def _pos(sym: str, lev: float, capital: float = 10_000.0) -> TargetPosition:
    return TargetPosition(
        symbol=sym,
        side="long" if lev > 0 else "short",
        leverage=lev,
        notional_usd=lev * capital,
        forecast=0.0,
    )


def test_open_from_flat_rounds_qty_down_to_step() -> None:
    # target notional = 0.1*10000 = $1000 at mark 100 -> 10.0 units; step 0.001
    book = _book([_pos("AAAUSDT", 0.1)])
    plan = build_order_plan(
        book,
        current_positions={},
        marks={"AAAUSDT": 100.0},
        filters={"AAAUSDT": _filters("AAAUSDT")},
        no_trade_band_frac=0.005,
        capital=10_000.0,
    )
    assert len(plan.intents) == 1
    o = plan.intents[0]
    assert o.symbol == "AAAUSDT" and o.side == "BUY"
    assert o.qty == 10.0 and o.reduce_only is False
    assert o.reason == "open"


def test_below_band_is_skipped() -> None:
    # delta notional $20 < band 0.005*10000 = $50
    book = _book([_pos("AAAUSDT", 0.1)])
    plan = build_order_plan(
        book,
        current_positions={"AAAUSDT": 9.8},
        marks={"AAAUSDT": 100.0},
        filters={"AAAUSDT": _filters("AAAUSDT")},
        no_trade_band_frac=0.005,
        capital=10_000.0,
    )
    assert plan.intents == []
    assert any(s.reason == "skip:band" for s in plan.skipped)


def test_below_min_notional_is_skipped() -> None:
    book = _book([_pos("AAAUSDT", 0.0006)])  # $6 notional, min_notional 10
    plan = build_order_plan(
        book,
        current_positions={},
        marks={"AAAUSDT": 100.0},
        filters={"AAAUSDT": _filters("AAAUSDT", min_notional=10.0)},
        no_trade_band_frac=0.0,
        capital=10_000.0,
    )
    assert plan.intents == []
    assert any(s.reason == "skip:min_notional" for s in plan.skipped)


def test_trim_same_side_is_reduce_only() -> None:
    # target long 5 units, currently long 10 -> SELL 5, reduce_only
    book = _book([_pos("AAAUSDT", 0.05)])  # $500 -> 5 units @ 100
    plan = build_order_plan(
        book,
        current_positions={"AAAUSDT": 10.0},
        marks={"AAAUSDT": 100.0},
        filters={"AAAUSDT": _filters("AAAUSDT")},
        no_trade_band_frac=0.0,
        capital=10_000.0,
    )
    o = plan.intents[0]
    assert o.side == "SELL" and o.qty == 5.0 and o.reduce_only is True


def test_close_absent_symbol_full_reduce_only() -> None:
    # held long 3 units, not in target -> SELL 3 reduce_only, bypasses band
    book = _book([_pos("AAAUSDT", 0.1)])
    plan = build_order_plan(
        book,
        current_positions={"AAAUSDT": 10.0, "ZZZUSDT": 3.0},
        marks={"AAAUSDT": 100.0, "ZZZUSDT": 50.0},
        filters={"AAAUSDT": _filters("AAAUSDT"), "ZZZUSDT": _filters("ZZZUSDT")},
        no_trade_band_frac=0.99,
        capital=10_000.0,  # huge band; close must still fire
    )
    closes = [o for o in plan.intents if o.symbol == "ZZZUSDT"]
    assert len(closes) == 1
    assert closes[0].side == "SELL" and closes[0].qty == 3.0
    assert closes[0].reduce_only is True and closes[0].reason == "close"


def test_flip_long_to_short_is_not_reduce_only() -> None:
    # currently long 10, target short 5 -> SELL 15, NOT reduce_only (must flip)
    book = _book([_pos("AAAUSDT", -0.05)])  # -$500 -> -5 units @ 100
    plan = build_order_plan(
        book,
        current_positions={"AAAUSDT": 10.0},
        marks={"AAAUSDT": 100.0},
        filters={"AAAUSDT": _filters("AAAUSDT")},
        no_trade_band_frac=0.0,
        capital=10_000.0,
    )
    o = plan.intents[0]
    assert o.side == "SELL" and o.qty == 15.0 and o.reduce_only is False


def test_missing_mark_is_skipped() -> None:
    book = _book([_pos("AAAUSDT", 0.1)])
    plan = build_order_plan(
        book,
        current_positions={},
        marks={},
        filters={"AAAUSDT": _filters("AAAUSDT")},
        no_trade_band_frac=0.0,
        capital=10_000.0,
    )
    assert plan.intents == []
    assert any(s.reason == "skip:no_mark" for s in plan.skipped)


def test_leverage_aggregates_come_from_book() -> None:
    book = _book([_pos("AAAUSDT", 0.1), _pos("BBBUSDT", -0.2)])
    plan = build_order_plan(
        book,
        current_positions={},
        marks={"AAAUSDT": 100.0, "BBBUSDT": 100.0},
        filters={"AAAUSDT": _filters("AAAUSDT"), "BBBUSDT": _filters("BBBUSDT")},
        no_trade_band_frac=0.0,
        capital=10_000.0,
    )
    assert abs(plan.target_gross_leverage - 0.3) < 1e-12
    assert abs(plan.target_net_leverage - (-0.1)) < 1e-12


def test_trim_short_same_side_is_reduce_only() -> None:
    # currently short 10 units, target short 5 -> BUY 5, reduce_only (covers, no cross)
    book = _book([_pos("AAAUSDT", -0.05)])  # -$500 -> -5 units @ 100
    plan = build_order_plan(
        book,
        current_positions={"AAAUSDT": -10.0},
        marks={"AAAUSDT": 100.0},
        filters={"AAAUSDT": _filters("AAAUSDT")},
        no_trade_band_frac=0.0,
        capital=10_000.0,
    )
    o = plan.intents[0]
    assert o.side == "BUY" and o.qty == 5.0 and o.reduce_only is True


def test_flip_short_to_long_is_not_reduce_only() -> None:
    # currently short 10, target long 5 -> BUY 15, NOT reduce_only (must flip)
    book = _book([_pos("AAAUSDT", 0.05)])  # +$500 -> +5 units @ 100
    plan = build_order_plan(
        book,
        current_positions={"AAAUSDT": -10.0},
        marks={"AAAUSDT": 100.0},
        filters={"AAAUSDT": _filters("AAAUSDT")},
        no_trade_band_frac=0.0,
        capital=10_000.0,
    )
    o = plan.intents[0]
    assert o.side == "BUY" and o.qty == 15.0 and o.reduce_only is False
