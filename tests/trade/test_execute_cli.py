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
