"""Tests for tools/journal_fetch.py — the read-only trade-candidate reconstructor.

All network-free: dict fixtures for the pure grouping/enrichment functions and a
MagicMock client for the I/O orchestrator. Binance returns numeric fields as
strings, so fixtures use strings to exercise the real contract.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from tools.journal_fetch import (
    _algo_source,
    _direction_for,
    _iso_utc,
    _print_table,
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
_DAY_MS = 86_400_000
# A fixed "now" so window arithmetic is deterministic — never wall-clock.
_NOW = BASE_MS + 60 * _DAY_MS


@pytest.fixture(autouse=True)
def _pin_the_clock(monkeypatch: Any) -> None:
    """Pin `journal_fetch`'s clock to the fixture epoch, for the WHOLE file.

    Fixtures here sit at fixed offsets from `_NOW` while `fetch_candidates`
    computes its cutoff from the REAL clock, so a test expires once wall-clock
    drifts more than its own `days` window past the fixture. That is not
    hypothetical: `test_algo_history_failure_does_not_break_the_fetch` uses a
    7-day window and started failing on 2026-08-21 while its 30-day siblings
    kept passing — a staggered fuse rather than one break.

    `286305c` pinned the clock for one CLASS, which left the longest fuse
    burning: `test_fetch_candidates_orchestrator_no_network` asks for
    `days=3650` against fills at `BASE_MS`, so it comes due around 2036, for
    whoever is on call then rather than for whoever wrote it.

    Scope was MEASURED by shifting this clock rather than by reading the file.
    At +3650d that test is the only failure here; at −30d and −400d nothing
    breaks, so no test passes merely because a window is generously wide.

    File-wide and autouse on purpose: per-test pinning is what produced the
    staggered fuse, because it makes determinism something each new test has to
    remember.
    """
    monkeypatch.setattr("tools.journal_fetch._now_ms", lambda: _NOW)


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


_HOUR_MS = 3_600_000


def _entry(
    path: Any, *, stem: str, symbol: str, direction: str, when: str | None
) -> None:
    """Write a minimal journal file; `when` None omits `entry_ts_utc` (legacy shape)."""
    stated = f'entry_ts_utc: "{when}"\n' if when else ""
    path.write_text(
        f"---\nid: {stem}\nsymbol: {symbol}\ndirection: {direction}\n"
        f"{stated}---\n## Thesis\n",
        encoding="utf-8",
    )


def _short(open_ms: int, symbol: str = "BTCUSDT") -> Any:
    return group_fills(
        symbol,
        [
            _fill("SELL", "100", "0.01", open_ms),
            _fill("BUY", "90", "0.01", open_ms + 60_000, realized="0.1"),
        ],
    )[0]


def _long(open_ms: int, symbol: str = "BTCUSDT") -> Any:
    return group_fills(
        symbol,
        [
            _fill("BUY", "100", "0.01", open_ms),
            _fill("SELL", "110", "0.01", open_ms + 60_000, realized="0.1"),
        ],
    )[0]


class TestAlreadyJournaledKeysOnTheEntryInstant:
    """The key is symbol + direction + entry INSTANT, not the entry DATE.

    Until 2026-08-27 it keyed on the date alone, so a second wave on the same
    symbol/direction/day read as journaled the moment the first was filed and then
    vanished from the default listing. Measured: filing the 25-Aug 05:47 UTC
    BTC/ETH/SOL short hid the 03:17 UTC one.
    """

    def test_the_journaled_wave_is_flagged(self, tmp_path: Any) -> None:
        journal = tmp_path / "journal"
        journal.mkdir()
        _entry(
            journal / "2026-06-18-0547-btc-short.md",
            stem="2026-06-18-0547-btc-short",
            symbol="BTCUSDT",
            direction="short",
            when="2026-06-18 05:47",
        )
        c = _short(BASE_MS + 5 * _HOUR_MS + 47 * 60_000)
        mark_already_journaled([c], journal)
        assert c.already_journaled is True

    def test_a_second_wave_the_same_day_is_not_hidden(self, tmp_path: Any) -> None:
        """THE regression. Both waves are BTCUSDT shorts on one UTC day."""
        journal = tmp_path / "journal"
        journal.mkdir()
        _entry(
            journal / "2026-06-18-0547-btc-short.md",
            stem="2026-06-18-0547-btc-short",
            symbol="BTCUSDT",
            direction="short",
            when="2026-06-18 05:47",
        )
        earlier = _short(BASE_MS + 3 * _HOUR_MS + 17 * 60_000)  # 03:17, unjournaled
        filed = _short(BASE_MS + 5 * _HOUR_MS + 47 * 60_000)  # 05:47, journaled

        mark_already_journaled([earlier, filed], journal)

        assert earlier.already_journaled is False
        assert filed.already_journaled is True

    def test_the_basket_prefix_skew_still_matches(self, tmp_path: Any) -> None:
        """A wave filed as `-0547-` whose own leg entered at 05:55 is one trade."""
        journal = tmp_path / "journal"
        journal.mkdir()
        _entry(
            journal / "2026-06-18-0547-btc-short.md",
            stem="2026-06-18-0547-btc-short",
            symbol="BTCUSDT",
            direction="short",
            when="2026-06-18 05:55",
        )
        c = _short(BASE_MS + 5 * _HOUR_MS + 47 * 60_000)
        mark_already_journaled([c], journal)
        assert c.already_journaled is True

    def test_the_window_is_narrow_not_wide(self, tmp_path: Any) -> None:
        """Erring narrow offers a trade twice; erring wide HIDES one. Pin the direction."""
        journal = tmp_path / "journal"
        journal.mkdir()
        _entry(
            journal / "2026-06-18-0000-btc-short.md",
            stem="2026-06-18-0000-btc-short",
            symbol="BTCUSDT",
            direction="short",
            when="2026-06-18 00:00",
        )
        c = _short(BASE_MS + 31 * 60_000)  # 31 min later
        mark_already_journaled([c], journal)
        assert c.already_journaled is False

    def test_the_id_hhmm_prefix_is_the_fallback(self, tmp_path: Any) -> None:
        """No `entry_ts_utc`, but the id carries the operator's own `-HHMM-` prefix."""
        journal = tmp_path / "journal"
        journal.mkdir()
        _entry(
            journal / "2026-06-18-0547-btc-short.md",
            stem="2026-06-18-0547-btc-short",
            symbol="BTCUSDT",
            direction="short",
            when=None,
        )
        matched = _short(BASE_MS + 5 * _HOUR_MS + 47 * 60_000)
        earlier = _short(BASE_MS + 3 * _HOUR_MS + 17 * 60_000)

        mark_already_journaled([matched, earlier], journal)

        assert matched.already_journaled is True
        assert earlier.already_journaled is False

    def test_a_legacy_entry_stating_no_time_still_matches_on_the_day(
        self, tmp_path: Any
    ) -> None:
        """Back-compat: the 23 pre-2026-08-27 entries must stay recognised."""
        journal = tmp_path / "journal"
        journal.mkdir()
        _entry(
            journal / "2026-06-18-btc-short.md",
            stem="2026-06-18-btc-short",
            symbol="BTCUSDT",
            direction="short",
            when=None,
        )
        c = _short(BASE_MS + 5 * _HOUR_MS + 47 * 60_000)
        mark_already_journaled([c], journal)
        assert c.already_journaled is True

    def test_direction_still_separates(self, tmp_path: Any) -> None:
        journal = tmp_path / "journal"
        journal.mkdir()
        _entry(
            journal / "2026-06-18-0000-btc-short.md",
            stem="2026-06-18-0000-btc-short",
            symbol="BTCUSDT",
            direction="short",
            when="2026-06-18 00:00",
        )
        c = _long(BASE_MS + 1000)
        mark_already_journaled([c], journal)
        assert c.already_journaled is False


def test_iso_utc_and_suggested_filename() -> None:
    assert _iso_utc(BASE_MS) == "2026-06-18T00:00:00Z"

    c = group_fills("ETHUSDT", [_fill("SELL", "3000", "0.5", BASE_MS + 1000)])[0]
    assert _suggested_filename(c) == "2026-06-18-0000-eth-short.md"
    assert c.suggested_filename == "2026-06-18-0000-eth-short.md"


def test_two_waves_the_same_day_get_different_filenames() -> None:
    """The write half of the same defect: a collision here OVERWRITES an entry."""
    first = _short(BASE_MS + 3 * _HOUR_MS + 17 * 60_000)
    second = _short(BASE_MS + 5 * _HOUR_MS + 47 * 60_000)
    assert _suggested_filename(first) == "2026-06-18-0317-btc-short.md"
    assert _suggested_filename(second) == "2026-06-18-0547-btc-short.md"
    assert _suggested_filename(first) != _suggested_filename(second)


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

        assert _sl_cell(c) == "98.00->104.00"

    def test_untrailed_stop_renders_one_number(self) -> None:
        c = self._candidate()
        c.initial_sl, c.exchange_sl = 98.0, 98.0

        assert _sl_cell(c) == "98.00"

    def test_no_stop_renders_the_placeholder(self) -> None:
        assert _sl_cell(self._candidate()) == "-"


class TestHedgeModeDirectionSurvivesAMidPositionWindow:
    """A hedge-mode SHORT book read from a window that opened mid-position.

    Measured live 2026-08-27 on `--days 3`: all three of the account's same-day
    shorts came back `long` and `open` against a FLAT account, with the entry and
    exit legs swapped, and SOL's single +$22.81 round-trip split into a +$9.57
    "short" plus a +$13.23 "long open".

    One cause, two halves. `_build_candidate` inferred direction from the first
    leg's side, and `group_fills` signed every BUY positive — but on a SHORT book
    a BUY is a COVER. A lookback that opens mid-position therefore starts on a
    close, which the walk read as an entry and then merged with the NEXT trade.
    """

    @staticmethod
    def _short_book_opening_on_a_cover() -> list[dict[str, Any]]:
        return [
            # A cover of a short opened BEFORE the window — its entry legs are
            # not in this fill list at all.
            _fill(
                "BUY",
                "100",
                "0.05",
                BASE_MS + 1000,
                realized="5",
                commission="0.05",
                position_side="SHORT",
            ),
            # ...then a complete short round-trip that IS inside the window.
            _fill("SELL", "200", "0.02", BASE_MS + 2000, position_side="SHORT"),
            _fill("SELL", "198", "0.01", BASE_MS + 3000, position_side="SHORT"),
            _fill(
                "BUY",
                "190",
                "0.03",
                BASE_MS + 4000,
                realized="0.3",
                commission="0.03",
                position_side="SHORT",
            ),
        ]

    def test_the_direction_is_short_not_long(self) -> None:
        candidates = group_fills("BTCUSDT", self._short_book_opening_on_a_cover())

        assert [c.direction for c in candidates] == ["short", "short"]

    def test_the_leading_cover_does_not_seed_a_phantom_trade(self) -> None:
        candidates = group_fills("BTCUSDT", self._short_book_opening_on_a_cover())

        assert len(candidates) == 2
        trunc = candidates[0]
        assert trunc.status == "truncated"
        assert trunc.truncated is True
        assert [leg.role for leg in trunc.legs] == ["exit"]
        # Its size is what it CLOSED — the entry legs are outside the window.
        assert trunc.qty_total == pytest.approx(0.05)
        assert trunc.avg_entry == 0.0
        assert trunc.avg_exit == pytest.approx(100.0)

    def test_the_real_round_trip_is_intact_and_not_merged(self) -> None:
        real = group_fills("BTCUSDT", self._short_book_opening_on_a_cover())[1]

        assert real.status == "closed"
        assert real.truncated is False
        # SELLs OPEN a short and the BUY closes it — the roles are not swapped.
        assert [leg.role for leg in real.legs] == ["entry", "add", "exit"]
        assert real.qty_total == pytest.approx(0.03)
        assert real.avg_entry == pytest.approx((200 * 0.02 + 198 * 0.01) / 0.03)
        assert real.avg_exit == pytest.approx(190.0)
        # The pre-window cover's $5 must NOT land in this trade's P&L.
        assert real.realized_pnl_usd == pytest.approx(0.3)

    def test_a_short_book_opening_FLAT_is_unaffected(self) -> None:
        """Specificity: the fix must not change the case that already worked."""
        fills = [
            _fill("SELL", "200", "0.02", BASE_MS + 1000, position_side="SHORT"),
            _fill(
                "BUY",
                "190",
                "0.02",
                BASE_MS + 2000,
                realized="0.2",
                position_side="SHORT",
            ),
        ]

        candidates = group_fills("BTCUSDT", fills)

        assert len(candidates) == 1
        assert candidates[0].direction == "short"
        assert candidates[0].status == "closed"
        assert candidates[0].truncated is False
        assert [leg.role for leg in candidates[0].legs] == ["entry", "exit"]

    def test_a_long_book_opening_on_a_sell_is_the_mirror_case(self) -> None:
        fills = [
            _fill(
                "SELL",
                "100",
                "0.05",
                BASE_MS + 1000,
                realized="5",
                position_side="LONG",
            ),
            _fill("BUY", "90", "0.02", BASE_MS + 2000, position_side="LONG"),
            _fill(
                "SELL",
                "95",
                "0.02",
                BASE_MS + 3000,
                realized="0.1",
                position_side="LONG",
            ),
        ]

        candidates = group_fills("BTCUSDT", fills)

        assert [c.direction for c in candidates] == ["long", "long"]
        assert [c.status for c in candidates] == ["truncated", "closed"]

    def test_position_side_beats_the_fill_side_outright(self) -> None:
        """`positionSide` is stated by the exchange; the fill side is inferred.

        Where they disagree the exchange wins — that disagreement IS the bug.
        """
        assert _direction_for("SHORT", []) == "short"
        assert _direction_for("LONG", []) == "long"

    def test_one_way_mode_keeps_the_fill_side_heuristic(self) -> None:
        """`BOTH` carries no positionSide, so the first entry leg is all there is."""
        fills = [
            _fill("SELL", "200", "0.02", BASE_MS + 1000),
            _fill("BUY", "190", "0.02", BASE_MS + 2000, realized="0.2"),
        ]

        candidates = group_fills("BTCUSDT", fills)

        assert len(candidates) == 1
        assert candidates[0].direction == "short"
        # No truncation bookkeeping in one-way mode: net is a SIGNED position
        # there, so a leading close is indistinguishable from an opposite entry.
        assert candidates[0].truncated is False

    def test_truncated_reaches_the_json_payload(self) -> None:
        candidates = group_fills("BTCUSDT", self._short_book_opening_on_a_cover())

        assert candidates[0].to_json_dict()["truncated"] is True
        assert candidates[1].to_json_dict()["truncated"] is False

    def test_the_table_flags_a_truncated_row(self, capsys: Any) -> None:
        candidates = group_fills("BTCUSDT", self._short_book_opening_on_a_cover())
        for i, c in enumerate(candidates, 1):
            c.index = i

        _print_table(candidates)
        out = capsys.readouterr().out

        assert "truncated" in out
        # The unknown entry renders as `?`, never as a plausible 0.00.
        assert "?->100.00" in out
        assert "0.00->100.00" not in out

    def test_the_table_encodes_on_a_cp1252_console(self, capsys: Any) -> None:
        # #932: the Windows console is cp1252 and the terminal may decode as UTF-8,
        # so the table stays pure ASCII (no arrow, tick or em dash).
        candidates = group_fills("BTCUSDT", self._short_book_opening_on_a_cover())
        for i, c in enumerate(candidates, 1):
            c.index = i
            c.already_journaled = True

        _print_table(candidates)

        capsys.readouterr().out.encode("ascii")


class TestBareInvocation:
    """`/journal-trade`'s default path shells out to this fetcher by hand, and the Make
    targets set PYTHONPATH — so a green Make target proves nothing about the bare form.
    Per ST129 the guarantee is THIS test, not the bootstrap line in the module.

    `--help` is the one network-free invocation, and it still covers the defect: the
    `monitor.*` import runs at module load, before argparse ever sees the flag.
    """

    def test_bare_invocation_works(self) -> None:
        proc = subprocess.run(
            [sys.executable, "tools/journal_fetch.py", "--help"],
            cwd=Path(__file__).resolve().parent.parent,
            capture_output=True,
            text=True,
            env={k: v for k, v in os.environ.items() if k != "PYTHONPATH"},
        )

        assert proc.returncode == 0, proc.stderr
        assert "monitor" not in proc.stderr  # the ModuleNotFoundError it used to die on
        assert "--include-journaled" in proc.stdout
