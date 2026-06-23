from __future__ import annotations

from analytics.xsmom.live import TargetBook, TargetPosition
from tools.xsmom_execute import check_live_gate, format_result
from trade.overlay import OverlayVerdict
from trade.routing import OrderIntent, OrderPlan
from trade.xsmom_executor import ExecutionResult


def _result(
    allowed: bool,
    intents: list[OrderIntent],
    *,
    book_positions: list[TargetPosition] | None = None,
    skipped: list[OrderIntent] | None = None,
    marks: dict[str, float] | None = None,
    positions: dict[str, float] | None = None,
    aborts: list[str] | None = None,
) -> ExecutionResult:
    legs = book_positions or []
    gross = sum(abs(p.leverage) for p in legs)
    net = sum(p.leverage for p in legs)
    book = TargetBook(
        "2026-06-21", "2026-06-22", 10_000.0, 1.0, len(legs), gross, net, legs
    )
    plan = OrderPlan(intents, skipped or [], gross, net)
    verdict = OverlayVerdict(
        allowed, aborts if aborts is not None else ([] if allowed else ["x"])
    )
    return ExecutionResult(
        verdict,
        plan,
        book,
        intents if allowed else [],
        [],
        10_000.0,
        "dry_run",
        marks or {},
        positions or {},
    )


def test_live_gate_blocks_without_flag_and_env() -> None:
    assert (
        check_live_gate("live", i_understand_live=False, allow_live_env=None)
        is not None
    )


def test_live_gate_blocks_with_only_flag() -> None:
    assert (
        check_live_gate("live", i_understand_live=True, allow_live_env=None) is not None
    )


def test_live_gate_opens_with_flag_and_env() -> None:
    assert check_live_gate("live", i_understand_live=True, allow_live_env="1") is None


def test_non_live_modes_never_gated() -> None:
    assert (
        check_live_gate("dry_run", i_understand_live=False, allow_live_env=None) is None
    )
    assert (
        check_live_gate("testnet", i_understand_live=False, allow_live_env=None) is None
    )


def test_format_result_renders_counts() -> None:
    pos = TargetPosition("AAAUSDT", "long", 0.50, 5000.0, 3.2)
    out = format_result(
        _result(
            True,
            [OrderIntent("AAAUSDT", "BUY", 1.0, False, 100.0, "open")],
            book_positions=[pos],
            marks={"AAAUSDT": 100.0},
        )
    )
    assert "AAAUSDT" in out and "submitted" in out.lower()


def test_format_result_shows_aborts_when_blocked() -> None:
    out = format_result(_result(False, []))
    assert "abort" in out.lower() or "blocked" in out.lower()


def test_parser_defaults_recalibrated() -> None:
    from tools.xsmom_execute import build_parser

    args = build_parser().parse_args([])
    assert args.max_gross_leverage == 4.5
    assert args.vol_target == 0.20
    assert args.min_active_positions == 15


def test_parser_overrides() -> None:
    from tools.xsmom_execute import build_parser

    args = build_parser().parse_args(
        ["--vol-target", "0.10", "--min-active-positions", "20"]
    )
    assert args.vol_target == 0.10
    assert args.min_active_positions == 20


def test_fmt_price_adaptive_precision() -> None:
    from tools.xsmom_execute import _fmt_price

    assert _fmt_price(62140.0) == "62,140"  # >= 1000 -> no decimals, thousands
    assert _fmt_price(148.2) == "148.20"  # >= 1 -> 2 decimals
    assert _fmt_price(0.1234) == "0.12340"  # < 1 -> 5 decimals
    assert _fmt_price(None) == "—"  # absent
    assert _fmt_price(0.0) == "—"  # non-positive


def test_format_result_shows_inband_leg_and_leverage() -> None:
    legs = [
        TargetPosition("AAAUSDT", "long", 0.50, 5000.0, 3.2),
        TargetPosition("BBBUSDT", "short", -0.30, -3000.0, -2.1),
    ]
    intents = [OrderIntent("AAAUSDT", "BUY", 10.0, False, 5000.0, "open")]
    skipped = [OrderIntent("BBBUSDT", "SELL", 0.0, True, 40.0, "skip:band")]
    out = format_result(
        _result(
            True,
            intents,
            book_positions=legs,
            skipped=skipped,
            marks={"AAAUSDT": 100.0, "BBBUSDT": 50.0},
        )
    )
    assert "hold (band)" in out  # in-band leg is shown, not hidden
    assert "+0.50" in out and "-0.30" in out  # signed leverage column


def test_format_result_renders_close_only_row() -> None:
    out = format_result(
        _result(
            True,
            [OrderIntent("ZZZUSDT", "SELL", 5.0, True, -500.0, "close")],
            book_positions=[],
            positions={"ZZZUSDT": 5.0},
            marks={"ZZZUSDT": 100.0},
        )
    )
    assert "ZZZUSDT" in out and "close" in out


def test_format_result_blocked_still_shows_book_table() -> None:
    legs = [TargetPosition("AAAUSDT", "long", 0.50, 5000.0, 3.2)]
    out = format_result(
        _result(
            False,
            [],
            book_positions=legs,
            marks={"AAAUSDT": 100.0},
            aborts=["gross leverage 5.0x > cap 4.5x"],
        )
    )
    assert "AAAUSDT" in out  # table still rendered when blocked
    assert "blocked" in out.lower()  # banner present
    assert "gross leverage" in out  # abort reason shown
