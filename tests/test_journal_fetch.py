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
    _algo_source,
    _iso_utc,
    _sl_cell,
    _suggested_filename,
    attach_funding,
    attach_stop_history,
    fetch_algo_orders,
    fetch_candidates,
    group_fills,
    mark_already_journaled,
    merge_open_position,
    stop_records_from_algo_orders,
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


def test_fetch_candidates_attaches_conditional_algo_sl() -> None:
    """UI-placed SLs live on /fapi/v1/openAlgoOrders (post-2025-12-09 Binance
    migration), not in classic openOrders — the open-position SL/TP pre-fill
    must query both sources or every exchange stop shows as absent."""
    client = MagicMock()
    client.futures_account_trades.return_value = [
        _fill("SELL", "64978", "0.155", BASE_MS + 1000, position_side="SHORT"),
    ]
    client.futures_position_information.return_value = []
    client.futures_income_history.return_value = []

    def fake_orders(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        if kwargs.get("conditional"):
            return [
                {
                    "algoType": "CONDITIONAL",
                    "orderType": "STOP_MARKET",
                    "symbol": "BTCUSDT",
                    "side": "BUY",
                    "positionSide": "SHORT",
                    "algoStatus": "NEW",
                    "triggerPrice": "66303.0",
                    "price": "0.0",
                    "reduceOnly": True,
                    "closePosition": False,
                }
            ]
        return []

    client.futures_get_open_orders.side_effect = fake_orders

    candidates = fetch_candidates(
        client,
        symbols=["BTCUSDT"],
        days=3650,
        journal_dir=None,
        include_journaled=True,
    )

    assert len(candidates) == 1
    c = candidates[0]
    assert c.status == "open"
    assert c.exchange_sl == 66303.0


# ---------------------------------------------------------------------------
# J1 — stop-loss history from /fapi/v1/allAlgoOrders
# ---------------------------------------------------------------------------

_DAY_MS = 86_400_000
# A fixed "now" so window arithmetic is deterministic — never wall-clock.
_NOW = BASE_MS + 60 * _DAY_MS


def _algo(
    trigger: str,
    create_ms: int,
    *,
    client_algo_id: str = "stToAg_OTO_1_2",
    status: str = "CANCELED",
    order_type: str = "STOP_MARKET",
    position_side: str = "LONG",
    qty: str = "0.010",
    symbol: str = "BTCUSDT",
    close_position: bool = False,
) -> dict[str, Any]:
    """One /fapi/v1/allAlgoOrders row.

    Field names and types are copied from a live 2026-08-07 response: algo rows
    spell the trigger `triggerPrice` (not `stopPrice`) and the size `quantity`
    (not `origQty`), and BOTH arrive as strings.
    """
    return {
        "symbol": symbol,
        "algoId": abs(hash((client_algo_id, create_ms, trigger))) % 10**9,
        "clientAlgoId": client_algo_id,
        "orderType": order_type,
        "triggerPrice": trigger,
        "algoStatus": status,
        "positionSide": position_side,
        "quantity": qty,
        "closePosition": close_position,
        "createTime": create_ms,
        "updateTime": create_ms + 1000,
    }


class TestAlgoSource:
    """`clientAlgoId` prefixes identify who moved the stop — verified live."""

    def test_auto_attached_stop(self) -> None:
        assert _algo_source("stToAg_OTO_620615180_2") == "auto"

    def test_phone_placed_stop(self) -> None:
        assert _algo_source("ios_4EHNyB1sWV1U2oRCzGBE") == "phone"

    def test_unknown_prefix_is_manual(self) -> None:
        assert _algo_source("web_abc123") == "manual"

    def test_missing_id_is_manual(self) -> None:
        assert _algo_source("") == "manual"


class TestFetchAlgoOrders:
    """The endpoint caps the interval at 7 DAYS, and how you hit that cap
    decides whether you get an error or silent data loss.

    Measured live 2026-08-07: an explicit `startTime`+`endTime` wider than 7d
    fails LOUDLY with `-4165` ("Maximum time interval is 7 days"), but
    `startTime` ALONE is silently clamped to `[startTime, startTime+7d]` — so
    a 30-day lookback returned a ~6-day slice from the START of the window and
    dropped every recent stop. `limit` was nowhere near reached, so nothing
    about the response looked truncated. Same trap `futures_account_trades`
    already carries in this file.
    """

    def test_pages_in_windows_no_wider_than_seven_days(self) -> None:
        client = MagicMock()
        client._request_futures_api.return_value = []

        fetch_algo_orders(client, "BTCUSDT", now_ms=_NOW, lookback_days=30)

        assert client._request_futures_api.called
        for call in client._request_futures_api.call_args_list:
            data = call.kwargs["data"]
            span = data["endTime"] - data["startTime"]
            assert span <= 7 * _DAY_MS, f"window {span / _DAY_MS:.2f}d exceeds the cap"

    def test_always_sends_an_explicit_end_time(self) -> None:
        """A `startTime` with no `endTime` is the silent-truncation form."""
        client = MagicMock()
        client._request_futures_api.return_value = []

        fetch_algo_orders(client, "BTCUSDT", now_ms=_NOW, lookback_days=30)

        for call in client._request_futures_api.call_args_list:
            assert "endTime" in call.kwargs["data"], (
                "startTime without endTime is silently clamped to +7d"
            )

    def test_windows_tile_the_whole_lookback(self) -> None:
        """Coverage, not just window width — gaps would lose stops silently."""
        client = MagicMock()
        client._request_futures_api.return_value = []

        fetch_algo_orders(client, "BTCUSDT", now_ms=_NOW, lookback_days=30)

        spans = sorted(
            (c.kwargs["data"]["startTime"], c.kwargs["data"]["endTime"])
            for c in client._request_futures_api.call_args_list
        )
        assert spans[0][0] <= _NOW - 30 * _DAY_MS
        assert spans[-1][1] >= _NOW
        for (_, prev_end), (next_start, _) in zip(spans, spans[1:], strict=False):
            assert next_start <= prev_end, "gap between windows drops stops"

    def test_calls_the_right_endpoint_signed(self) -> None:
        client = MagicMock()
        client._request_futures_api.return_value = []

        fetch_algo_orders(client, "BTCUSDT", now_ms=_NOW, lookback_days=7)

        args = client._request_futures_api.call_args_list[0].args
        assert args[0] == "get"
        assert args[1] == "allAlgoOrders"
        assert args[2] is True  # signed

    def test_a_failed_window_does_not_lose_the_others(self) -> None:
        """One bad window must degrade to partial history, never to a crash."""
        client = MagicMock()
        client._request_futures_api.side_effect = [
            [_algo("100", _NOW - 10 * _DAY_MS)],
            RuntimeError("boom"),
            [_algo("200", _NOW - 2 * _DAY_MS)],
            [],
            [],
        ]

        rows = fetch_algo_orders(client, "BTCUSDT", now_ms=_NOW, lookback_days=30)

        assert len(rows) == 2


class TestStopRecords:
    def test_keeps_only_stop_orders_for_this_position(self) -> None:
        orders = [
            _algo("100", _NOW - 5 * _DAY_MS),
            _algo("999", _NOW - 5 * _DAY_MS, order_type="TAKE_PROFIT_MARKET"),
            _algo("888", _NOW - 5 * _DAY_MS, position_side="SHORT"),
            _algo("777", _NOW - 5 * _DAY_MS, symbol="ETHUSDT"),
        ]

        recs = stop_records_from_algo_orders(
            orders, "BTCUSDT", "LONG", opened_ms=_NOW - 6 * _DAY_MS, closed_ms=_NOW
        )

        assert [r.trigger_price for r in recs] == [100.0]

    def test_orders_ascending_and_parses_strings(self) -> None:
        orders = [
            _algo("300", _NOW - 2 * _DAY_MS, client_algo_id="ios_x"),
            _algo("100", _NOW - 5 * _DAY_MS),
            _algo("200", _NOW - 3 * _DAY_MS, client_algo_id="ios_y"),
        ]

        recs = stop_records_from_algo_orders(
            orders, "BTCUSDT", "LONG", opened_ms=_NOW - 6 * _DAY_MS, closed_ms=_NOW
        )

        assert [r.trigger_price for r in recs] == [100.0, 200.0, 300.0]
        assert [r.source for r in recs] == ["auto", "phone", "phone"]
        assert recs[0].qty == pytest.approx(0.010)

    def test_excludes_stops_outside_the_position_lifetime(self) -> None:
        """A previous trade on the same symbol has its own stops."""
        orders = [
            _algo("50", _NOW - 30 * _DAY_MS),
            _algo("100", _NOW - 5 * _DAY_MS),
            _algo("150", _NOW - 1 * _DAY_MS),
        ]

        recs = stop_records_from_algo_orders(
            orders,
            "BTCUSDT",
            "LONG",
            opened_ms=_NOW - 6 * _DAY_MS,
            closed_ms=_NOW - 2 * _DAY_MS,
        )

        assert [r.trigger_price for r in recs] == [100.0]

    def test_open_position_has_no_close_bound(self) -> None:
        orders = [_algo("100", _NOW - 5 * _DAY_MS), _algo("150", _NOW - 1 * _DAY_MS)]

        recs = stop_records_from_algo_orders(
            orders, "BTCUSDT", "LONG", opened_ms=_NOW - 6 * _DAY_MS, closed_ms=None
        )

        assert [r.trigger_price for r in recs] == [100.0, 150.0]


class TestAttachStopHistory:
    """The J1 defect: `exchange_sl` was None for EVERY closed round-trip,
    because only `openAlgoOrders` was wired and a closed position has none.

    Both ends of the history are kept on purpose. The R basis differs by up to
    4.7x between them, and the settled journal rule is to score against the
    stop in force AT ENTRY while noting the trailed R beside it — which needs
    both numbers, not one.
    """

    def _closed_candidate(self) -> Any:
        c = group_fills(
            "BTCUSDT",
            [
                _fill("BUY", "100", "0.01", _NOW - 6 * _DAY_MS),
                _fill("SELL", "110", "0.01", _NOW - 2 * _DAY_MS, realized="0.1"),
            ],
        )[0]
        c.position_side = "LONG"
        return c

    def test_closed_trade_gets_a_stop_after_all(self) -> None:
        c = self._closed_candidate()
        assert c.exchange_sl is None  # the defect, before the fix

        attach_stop_history(
            c,
            [
                _algo("98", _NOW - 6 * _DAY_MS),
                _algo("104", _NOW - 3 * _DAY_MS, client_algo_id="ios_a"),
            ],
        )

        assert c.exchange_sl == pytest.approx(104.0)
        assert c.initial_sl == pytest.approx(98.0)
        assert len(c.sl_history) == 2

    def test_initial_and_last_differ_when_the_stop_was_trailed(self) -> None:
        c = self._closed_candidate()

        attach_stop_history(
            c,
            [
                _algo("98", _NOW - 6 * _DAY_MS),
                _algo("101", _NOW - 4 * _DAY_MS, client_algo_id="ios_a"),
                _algo("106", _NOW - 3 * _DAY_MS, client_algo_id="ios_b"),
            ],
        )

        assert c.initial_sl == pytest.approx(98.0)
        assert c.exchange_sl == pytest.approx(106.0)

    def test_no_history_leaves_the_fields_alone(self) -> None:
        c = self._closed_candidate()

        attach_stop_history(c, [])

        assert c.sl_history == []
        assert c.exchange_sl is None
        assert c.initial_sl is None

    def test_history_reaches_the_json_payload(self) -> None:
        c = self._closed_candidate()
        attach_stop_history(c, [_algo("98", _NOW - 6 * _DAY_MS)])

        payload = c.to_json_dict()

        assert payload["initial_sl"] == pytest.approx(98.0)
        assert payload["sl_history"] == [
            {
                "ts": _iso_utc(_NOW - 6 * _DAY_MS),
                "trigger_price": 98.0,
                "qty": pytest.approx(0.010),
                "status": "CANCELED",
                "source": "auto",
            }
        ]


class TestFetchCandidatesWiresStopHistory:
    """The units above can all pass while `fetch_candidates` never calls them.

    That is the whole J1 bug in miniature: `_stops_from_orders` worked fine,
    but the orchestrator only invoked it for OPEN candidates, so every closed
    round-trip came back with `exchange_sl: null`.
    """

    def _client(self, algo_rows: list[dict[str, Any]]) -> Any:
        client = MagicMock()
        client.futures_account_trades.return_value = [
            _fill("BUY", "100", "0.01", _NOW - 6 * _DAY_MS),
            _fill("SELL", "110", "0.01", _NOW - 2 * _DAY_MS, realized="0.1"),
        ]
        client.futures_position_information.return_value = []
        client.futures_get_open_orders.return_value = []
        client.futures_income_history.return_value = []
        client._request_futures_api.return_value = algo_rows
        return client

    def test_closed_candidate_gets_stop_history(self) -> None:
        client = self._client(
            [
                _algo("98", _NOW - 6 * _DAY_MS, position_side="BOTH"),
                _algo(
                    "104",
                    _NOW - 3 * _DAY_MS,
                    position_side="BOTH",
                    client_algo_id="ios_a",
                ),
            ]
        )

        cands = fetch_candidates(
            client,
            symbols=["BTCUSDT"],
            days=30,
            journal_dir=None,
            include_journaled=True,
        )

        assert len(cands) == 1
        c = cands[0]
        assert c.status == "closed"
        assert c.exchange_sl == pytest.approx(104.0)
        assert c.initial_sl == pytest.approx(98.0)
        assert [r.source for r in c.sl_history] == ["auto", "phone"]

    def test_still_read_only(self) -> None:
        client = self._client([])

        fetch_candidates(
            client,
            symbols=["BTCUSDT"],
            days=30,
            journal_dir=None,
            include_journaled=True,
        )

        assert not client.futures_create_order.called
        assert not client.futures_cancel_order.called
        # Every algo call is a GET against the history endpoint.
        for call in client._request_futures_api.call_args_list:
            assert call.args[0] == "get"
            assert call.args[1] == "allAlgoOrders"

    def test_algo_history_failure_does_not_break_the_fetch(self) -> None:
        """Enrichment must never cost the fills that stand on their own."""
        client = self._client([])
        client._request_futures_api.side_effect = RuntimeError("endpoint down")

        cands = fetch_candidates(
            client,
            symbols=["BTCUSDT"],
            days=7,
            journal_dir=None,
            include_journaled=True,
        )

        assert len(cands) == 1
        assert cands[0].exchange_sl is None


def test_fetch_algo_orders_dedups_the_shared_window_boundary() -> None:
    """`endTime` is inclusive, so consecutive windows share one instant.

    A stop armed exactly on a boundary comes back in BOTH windows. Without a
    dedup it reads as two trail steps at the same price — inventing a stop
    move that never happened.
    """
    client = MagicMock()
    boundary_row = _algo("100", _NOW - 7 * _DAY_MS)
    client._request_futures_api.return_value = [boundary_row]

    rows = fetch_algo_orders(client, "BTCUSDT", now_ms=_NOW, lookback_days=21)

    assert client._request_futures_api.call_count == 3
    assert len(rows) == 1


class TestSLColumnShowsTheTrail:
    """A single SL number hides the fact that the stop MOVED.

    That matters because the two R bases differ by up to 4.7x, and the journal
    rule scores against the stop at entry while noting the trailed R beside
    it. A column showing only the last stop makes the trail invisible at
    exactly the moment the operator is picking which trade to journal.
    """

    def _candidate(self) -> Any:
        return group_fills(
            "BTCUSDT",
            [
                _fill("BUY", "100", "0.01", _NOW - 6 * _DAY_MS),
                _fill("SELL", "110", "0.01", _NOW - 2 * _DAY_MS, realized="0.1"),
            ],
        )[0]

    def test_trailed_stop_renders_both_ends(self) -> None:
        c = self._candidate()
        c.initial_sl, c.exchange_sl = 98.0, 104.0

        assert _sl_cell(c) == "98.00→104.00"

    def test_untrailed_stop_renders_one_number(self) -> None:
        c = self._candidate()
        c.initial_sl, c.exchange_sl = 98.0, 98.0

        assert _sl_cell(c) == "98.00"

    def test_no_stop_renders_the_placeholder(self) -> None:
        assert _sl_cell(self._candidate()) == "—"
