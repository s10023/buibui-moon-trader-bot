from __future__ import annotations

from analytics.xsmom.live import TargetBook
from tools.xsmom_execute import check_live_gate, format_result
from trade.overlay import OverlayVerdict
from trade.routing import OrderIntent, OrderPlan
from trade.xsmom_executor import ExecutionResult


def _result(allowed: bool, intents: list[OrderIntent]) -> ExecutionResult:
    book = TargetBook(
        "2026-06-21", "2026-06-22", 10_000.0, 1.0, len(intents), 1.0, 0.0, []
    )
    plan = OrderPlan(intents, [], 1.0, 0.0)
    return ExecutionResult(
        OverlayVerdict(allowed, [] if allowed else ["x"]),
        plan,
        book,
        intents if allowed else [],
        [],
        10_000.0,
        "dry_run",
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
    out = format_result(
        _result(True, [OrderIntent("AAAUSDT", "BUY", 1.0, False, 100.0, "open")])
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
