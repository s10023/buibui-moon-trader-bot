from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from analytics.xsmom.live import TargetBook, TargetPosition
from tools.xsmom_execute import check_live_gate, correct_peak_equity, format_result
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


def test_format_result_shows_order_type_for_actionable_rows() -> None:
    """Dry-run is the only mode the daily timer runs and the operator's only
    pre-testnet check, so the maker/taker split must be visible in it — a
    LIMIT open and a MARKET close must render distinctly."""
    intents = [
        OrderIntent("AAAUSDT", "BUY", 1.0, False, 5000.0, "open", "LIMIT"),
        OrderIntent("ZZZUSDT", "SELL", 5.0, True, -500.0, "close", "MARKET"),
    ]
    out = format_result(
        _result(
            True,
            intents,
            book_positions=[TargetPosition("AAAUSDT", "long", 0.50, 5000.0, 3.2)],
            positions={"ZZZUSDT": 5.0},
            marks={"AAAUSDT": 100.0, "ZZZUSDT": 100.0},
        )
    )
    assert "open LIMIT" in out
    assert "close MARKET" in out


def test_format_result_skip_reason_omits_order_type() -> None:
    """A skipped intent's `order_type` defaults to MARKET regardless of what
    it would have been — `routing.py`'s skip branches never pass it through
    — so folding it into a skip label would print an unearned, misleading
    MARKET on what may well have been a LIMIT-eligible open."""
    legs = [TargetPosition("BBBUSDT", "short", -0.10, -1000.0, -1.0)]
    skipped = [OrderIntent("BBBUSDT", "SELL", 0.0, True, 10.0, "skip:min_qty")]
    out = format_result(
        _result(
            True,
            [],
            book_positions=legs,
            skipped=skipped,
            marks={"BBBUSDT": 50.0},
        )
    )
    assert "skip:min_qty" in out
    assert "skip:min_qty MARKET" not in out


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


# --- `--set-peak`: the audited correction of a poisoned high-water mark -------
#
# Context: on 2026-08-06 a one-off `--capital 5000` run ratcheted `peak_equity`
# 2350.80 -> 5000.00 against an account that never passed 1201.33. The ratchet
# fix (`new_peak = prior_peak if capital_override is not None`) stopped new
# poisoning but heals nothing already on disk, and the state file sat wrong for
# 14 days. A hand-edit would repeat the original incident's defect -- that one
# was UNRECOVERABLE precisely because nothing recorded which invocation did it.


def _state(peak: float = 5000.0) -> dict[str, Any]:
    return {
        "peak_equity": peak,
        "kill_switch": False,
        "last_run": {
            "ts": "2026-08-20T02:21:18+00:00",
            "submitted": 0,
            "mode": "dry_run",
        },
    }


_NOW = datetime(2026, 8, 20, 5, 30, tzinfo=UTC)


def test_set_peak_lowers_the_stored_high_water_mark() -> None:
    state, _ = correct_peak_equity(
        _state(),
        2350.80,
        "heal the 08-06 --capital 5000 poisoning",
        now=_NOW,
        drawdown_frac=0.25,
    )
    assert state["peak_equity"] == 2350.80


def test_set_peak_records_an_audit_trail_naming_from_to_and_reason() -> None:
    state, _ = correct_peak_equity(
        _state(),
        2350.80,
        "heal the 08-06 --capital 5000 poisoning",
        now=_NOW,
        drawdown_frac=0.25,
    )
    trail = state["last_run"]["peak_correction"]
    assert trail["from"] == 5000.0
    assert trail["to"] == 2350.80
    assert trail["reason"] == "heal the 08-06 --capital 5000 poisoning"
    assert trail["ts"] == _NOW.isoformat()


def test_set_peak_summary_names_both_the_old_and_new_floor() -> None:
    # The floor is what the operator actually acts on -- the peak is only its
    # anchor -- so a summary that prints the peak alone buries the consequence.
    _, summary = correct_peak_equity(
        _state(),
        2350.80,
        "heal",
        now=_NOW,
        drawdown_frac=0.25,
    )
    assert "5000.00" in summary and "2350.80" in summary
    assert "3750.00" in summary and "1763.10" in summary


def test_set_peak_refuses_to_RAISE_the_peak() -> None:
    # Raising is the ratchet's job and it already does it from real equity. A
    # CLI that could raise the mark is the 08-06 footgun rebuilt by hand.
    with pytest.raises(ValueError, match="(?i)lower"):
        correct_peak_equity(
            _state(2350.80),
            5000.0,
            "nope",
            now=_NOW,
            drawdown_frac=0.25,
        )


def test_set_peak_refuses_an_equal_peak() -> None:
    with pytest.raises(ValueError, match="(?i)lower"):
        correct_peak_equity(
            _state(2350.80),
            2350.80,
            "nope",
            now=_NOW,
            drawdown_frac=0.25,
        )


def test_set_peak_refuses_a_non_positive_peak() -> None:
    # peak 0 makes floor 0, and `equity < 0` is never true -- it would disable
    # the drawdown breaker outright while looking like a correction.
    with pytest.raises(ValueError, match="positive"):
        correct_peak_equity(_state(), 0.0, "nope", now=_NOW, drawdown_frac=0.25)


def test_set_peak_requires_a_reason() -> None:
    with pytest.raises(ValueError, match="reason"):
        correct_peak_equity(_state(), 2350.80, "  ", now=_NOW, drawdown_frac=0.25)


def test_set_peak_touches_neither_the_kill_switch_nor_the_prior_run_record() -> None:
    before = _state()
    before["kill_switch"] = True
    state, _ = correct_peak_equity(
        before,
        2350.80,
        "heal",
        now=_NOW,
        drawdown_frac=0.25,
    )
    # The invariants...
    assert state["kill_switch"] is True
    assert state["last_run"]["submitted"] == 0
    assert state["last_run"]["ts"] == "2026-08-20T02:21:18+00:00"
    # ...paired with a positive control, or the three above are satisfied just
    # as well by a function that did nothing at all.
    assert state["peak_equity"] == 2350.80
