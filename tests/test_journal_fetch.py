"""Tests for tools/journal_fetch.py — the read-only trade-candidate reconstructor.

All network-free: dict fixtures for the pure grouping/enrichment functions and a
MagicMock client for the I/O orchestrator. Binance returns numeric fields as
strings, so fixtures use strings to exercise the real contract.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from tools.journal_fetch import (
    _iso_utc,
    _suggested_filename,
    attach_funding,
    fetch_candidates,
    group_fills,
    mark_already_journaled,
    merge_open_position,
)

# A fixed UTC instant: 2026-06-18T00:00:00Z in epoch ms.
BASE_MS = 1_781_740_800_000


def _fill(
    side: str,
    price: str,
    qty: str,
    t_ms: int,
    *,
    realized: str = "0",
    commission: str = "0",
    position_side: str = "BOTH",
) -> dict[str, Any]:
    return {
        "side": side,
        "price": price,
        "qty": qty,
        "time": t_ms,
        "realizedPnl": realized,
        "commission": commission,
        "commissionAsset": "USDT",
        "positionSide": position_side,
    }


def test_group_fills_entry_add_full_close() -> None:
    fills = [
        _fill("BUY", "100", "0.01", BASE_MS + 1000, commission="0.1"),
        _fill("BUY", "102", "0.01", BASE_MS + 2000, commission="0.1"),
        _fill(
            "SELL", "110", "0.02", BASE_MS + 3000, realized="0.36", commission="0.22"
        ),
    ]

    candidates = group_fills("BTCUSDT", fills)

    assert len(candidates) == 1
    c = candidates[0]
    assert c.symbol == "BTCUSDT"
    assert c.direction == "long"
    assert c.status == "closed"
    assert c.qty_total == 0.02
    assert c.avg_entry == 101.0  # qty-weighted (100, 102)
    assert c.avg_exit == 110.0
    assert round(c.realized_pnl_usd, 6) == 0.36
    assert round(c.fees_usd, 6) == 0.42
    assert [leg.role for leg in c.legs] == ["entry", "add", "exit"]
    assert c.opened_ts_utc == _iso_utc(BASE_MS + 1000)
    assert c.closed_ts_utc == _iso_utc(BASE_MS + 3000)


def test_group_fills_partial_exits_before_close() -> None:
    fills = [
        _fill("BUY", "100", "0.04", BASE_MS + 1000),
        _fill("SELL", "110", "0.01", BASE_MS + 2000, realized="0.1"),
        _fill("SELL", "120", "0.03", BASE_MS + 3000, realized="0.6"),
    ]

    candidates = group_fills("BTCUSDT", fills)

    assert len(candidates) == 1
    c = candidates[0]
    assert c.status == "closed"
    assert [leg.role for leg in c.legs] == ["entry", "partial_exit", "exit"]
    assert c.qty_total == pytest.approx(0.04)
    # avg exit qty-weighted: (110*0.01 + 120*0.03) / 0.04 = 117.5
    assert c.avg_exit == pytest.approx(117.5)
    assert c.realized_pnl_usd == pytest.approx(0.7)


def test_group_fills_hedge_mode_long_and_short_independent() -> None:
    fills = [
        # LONG leg
        _fill("BUY", "100", "0.01", BASE_MS + 1000, position_side="LONG"),
        # SHORT leg interleaved
        _fill("SELL", "200", "0.02", BASE_MS + 1500, position_side="SHORT"),
        _fill(
            "SELL", "110", "0.01", BASE_MS + 2000, realized="0.1", position_side="LONG"
        ),
        _fill(
            "BUY", "190", "0.02", BASE_MS + 2500, realized="0.2", position_side="SHORT"
        ),
    ]

    candidates = group_fills("BTCUSDT", fills)

    assert len(candidates) == 2
    by_dir = {c.direction: c for c in candidates}
    assert set(by_dir) == {"long", "short"}
    assert by_dir["long"].position_side == "LONG"
    assert by_dir["long"].avg_entry == 100.0
    assert by_dir["long"].avg_exit == 110.0
    assert by_dir["short"].position_side == "SHORT"
    assert by_dir["short"].avg_entry == 200.0
    assert by_dir["short"].avg_exit == 190.0
    assert all(c.status == "closed" for c in candidates)


def test_group_fills_flip_through_zero() -> None:
    # Long 0.01, then a single SELL 0.03 that closes the long and opens a 0.02 short.
    fills = [
        _fill("BUY", "100", "0.01", BASE_MS + 1000, commission="0.1"),
        _fill("SELL", "110", "0.03", BASE_MS + 2000, realized="0.1", commission="0.33"),
    ]

    candidates = group_fills("BTCUSDT", fills)

    assert len(candidates) == 2
    closed = next(c for c in candidates if c.status == "closed")
    opened = next(c for c in candidates if c.status == "open")

    assert closed.direction == "long"
    assert closed.qty_total == pytest.approx(0.01)
    assert closed.avg_exit == pytest.approx(110.0)
    # realizedPnl from the flipping fill belongs to the closing leg
    assert closed.realized_pnl_usd == pytest.approx(0.1)
    # commission split by qty: close portion 0.01 of 0.03 -> 0.11
    assert closed.fees_usd == pytest.approx(0.1 + 0.33 * (0.01 / 0.03))

    assert opened.direction == "short"
    assert opened.qty_total == pytest.approx(0.02)
    assert opened.avg_exit is None
    assert opened.realized_pnl_usd == 0.0


def test_group_fills_open_position_merged_with_position_info() -> None:
    fills = [_fill("BUY", "100", "0.01", BASE_MS + 1000)]

    candidates = group_fills("BTCUSDT", fills)
    assert len(candidates) == 1
    assert candidates[0].status == "open"
    assert candidates[0].avg_exit is None
    assert candidates[0].closed_ts_utc is None

    positions = [
        {
            "symbol": "BTCUSDT",
            "positionSide": "BOTH",
            "positionAmt": "0.010",
            "entryPrice": "100.5",
            "markPrice": "105.0",
        }
    ]
    merge_open_position(candidates, positions)

    assert candidates[0].avg_entry == 100.5  # authoritative entry from position info
    assert candidates[0].mark_price == 105.0


def test_mark_already_journaled(tmp_path: Any) -> None:
    journal = tmp_path / "journal"
    journal.mkdir()
    (journal / "2026-06-18-btc-short.md").write_text(
        "---\nid: 2026-06-18-btc-short\nsymbol: BTCUSDT\ndirection: short\n"
        'entry_ts_utc: "2026-06-18 00:57"\n---\n## Thesis\n'
    )

    # Matching candidate (BTCUSDT short opened 2026-06-18) -> journaled.
    matched = group_fills(
        "BTCUSDT",
        [
            _fill("SELL", "100", "0.01", BASE_MS + 1000),
            _fill("BUY", "90", "0.01", BASE_MS + 2000, realized="0.1"),
        ],
    )
    # Non-matching candidate (different direction).
    other = group_fills(
        "BTCUSDT",
        [
            _fill("BUY", "100", "0.01", BASE_MS + 1000),
            _fill("SELL", "110", "0.01", BASE_MS + 2000, realized="0.1"),
        ],
    )

    mark_already_journaled(matched + other, journal)

    assert matched[0].already_journaled is True
    assert other[0].already_journaled is False


def test_iso_utc_and_suggested_filename() -> None:
    assert _iso_utc(BASE_MS) == "2026-06-18T00:00:00Z"

    c = group_fills("ETHUSDT", [_fill("SELL", "3000", "0.5", BASE_MS + 1000)])[0]
    assert _suggested_filename(c) == "2026-06-18-eth-short.md"
    assert c.suggested_filename == "2026-06-18-eth-short.md"


def test_attach_funding_sums_window_only() -> None:
    c = group_fills(
        "BTCUSDT",
        [
            _fill("BUY", "100", "0.01", BASE_MS + 1000),
            _fill("SELL", "110", "0.01", BASE_MS + 5000, realized="0.1"),
        ],
    )[0]

    income = [
        {
            "incomeType": "FUNDING_FEE",
            "income": "-0.05",
            "time": BASE_MS + 500,
        },  # before open
        {
            "incomeType": "FUNDING_FEE",
            "income": "-0.10",
            "time": BASE_MS + 2000,
        },  # in window
        {
            "incomeType": "FUNDING_FEE",
            "income": "0.03",
            "time": BASE_MS + 4000,
        },  # in window
        {
            "incomeType": "COMMISSION",
            "income": "-9.9",
            "time": BASE_MS + 3000,
        },  # wrong type
        {
            "incomeType": "FUNDING_FEE",
            "income": "-0.20",
            "time": BASE_MS + 9000,
        },  # after close
    ]
    attach_funding(c, income)

    assert round(c.funding_usd, 6) == round(-0.10 + 0.03, 6)


def test_fetch_candidates_orchestrator_no_network() -> None:
    client = MagicMock()
    client.futures_account_trades.return_value = [
        _fill("SELL", "100", "0.01", BASE_MS + 1000, commission="0.1"),
        _fill(
            "BUY", "90", "0.01", BASE_MS + 9_000_000, realized="0.1", commission="0.1"
        ),
    ]
    client.futures_position_information.return_value = []
    client.futures_get_open_orders.return_value = []
    client.futures_income_history.return_value = []

    candidates = fetch_candidates(
        client,
        symbols=["BTCUSDT"],
        days=3650,
        journal_dir=None,
        include_journaled=True,
    )

    assert len(candidates) == 1
    c = candidates[0]
    assert c.index == 1
    assert c.symbol == "BTCUSDT"
    assert c.direction == "short"
    assert c.status == "closed"
    # Read-only: never placed or cancelled an order.
    assert not client.futures_create_order.called


def test_fetch_candidates_requests_most_recent_trades() -> None:
    """Must NOT constrain futures_account_trades with startTime.

    Binance returns only [startTime, startTime+7d] when startTime is sent, so a
    >7-day lookback would drop the MOST RECENT trades — backwards for journaling.
    We pass limit only (most-recent fills) and filter by cutoff in code.
    """
    client = MagicMock()
    client.futures_account_trades.return_value = []
    client.futures_position_information.return_value = []
    client.futures_get_open_orders.return_value = []
    client.futures_income_history.return_value = []

    fetch_candidates(
        client,
        symbols=["BTCUSDT"],
        days=14,
        journal_dir=None,
        include_journaled=True,
    )

    _, kwargs = client.futures_account_trades.call_args
    assert "startTime" not in kwargs
