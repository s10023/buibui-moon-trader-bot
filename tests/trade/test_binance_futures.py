from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from portfolio.sizing import round_to_tick
from trade.binance_futures import (
    ORDER_NOT_FOUND,
    APIError,
    BinanceFuturesAdapter,
    UnconfirmedOrderError,
    find_order_by_client_id,
    make_client_order_id,
    require_ack,
)
from trade.routing import OrderIntent


class _APIError(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(f"code {code}")
        self.code = code


def _live_client() -> MagicMock:
    """A client whose order endpoint acknowledges like Binance's classic route."""
    client = MagicMock()
    client.futures_create_order.return_value = {"orderId": 1, "status": "NEW"}
    return client


def test_get_positions_parses_signed_amt() -> None:
    client = MagicMock()
    client.futures_position_information.return_value = [
        {"symbol": "AAAUSDT", "positionAmt": "1.5"},
        {"symbol": "BBBUSDT", "positionAmt": "-2.0"},
        {"symbol": "CCCUSDT", "positionAmt": "0"},
    ]
    adapter = BinanceFuturesAdapter(client, mode="dry_run")
    pos = adapter.get_positions()
    assert pos == {"AAAUSDT": 1.5, "BBBUSDT": -2.0}  # zero dropped


def test_get_net_positions_sums_both_hedge_legs() -> None:
    """A dual-side account returns a LONG row AND a SHORT row per symbol.

    `get_positions` keeps whichever arrives last, so with both legs open it
    records one leg as the whole position -- which is what the card order
    ledger's `position_at_placement` would have carried. Pinned as a
    DIFFERENCE against `get_positions` on the same rows so a future edit
    cannot quietly collapse the two methods back together.
    """
    client = MagicMock()
    client.futures_position_information.return_value = [
        {"symbol": "AAAUSDT", "positionSide": "LONG", "positionAmt": "1.5"},
        {"symbol": "AAAUSDT", "positionSide": "SHORT", "positionAmt": "-0.5"},
        {"symbol": "BBBUSDT", "positionSide": "LONG", "positionAmt": "2.0"},
        {"symbol": "BBBUSDT", "positionSide": "SHORT", "positionAmt": "0"},
        {"symbol": "CCCUSDT", "positionSide": "SHORT", "positionAmt": "0"},
    ]
    adapter = BinanceFuturesAdapter(client, mode="dry_run")
    assert adapter.get_net_positions() == {"AAAUSDT": 1.0, "BBBUSDT": 2.0}
    # the defect this exists to fix: last-row-wins reads AAAUSDT as -0.5
    assert adapter.get_positions()["AAAUSDT"] == -0.5


def test_get_equity_uses_total_margin_balance() -> None:
    client = MagicMock()
    client.futures_account.return_value = {"totalMarginBalance": "10250.5"}
    adapter = BinanceFuturesAdapter(client, mode="dry_run")
    assert adapter.get_equity() == 10250.5


def test_get_filters_extracts_lot_and_notional() -> None:
    client = MagicMock()
    client.futures_exchange_info.return_value = {
        "symbols": [
            {
                "symbol": "AAAUSDT",
                "filters": [
                    {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
                    {"filterType": "MIN_NOTIONAL", "notional": "5"},
                ],
            },
        ]
    }
    adapter = BinanceFuturesAdapter(client, mode="dry_run")
    f = adapter.get_filters(["AAAUSDT"])["AAAUSDT"]
    assert f.qty_step == 0.001 and f.min_qty == 0.001 and f.min_notional == 5.0


def test_get_filters_extracts_price_tick() -> None:
    client = MagicMock()
    client.futures_exchange_info.return_value = {
        "symbols": [
            {
                "symbol": "AAAUSDT",
                "filters": [
                    {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
                    {"filterType": "MIN_NOTIONAL", "notional": "5"},
                    {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
                ],
            },
        ]
    }
    adapter = BinanceFuturesAdapter(client, mode="dry_run")
    assert adapter.get_filters(["AAAUSDT"])["AAAUSDT"].price_tick == 0.01


def test_get_filters_missing_price_filter_leaves_tick_zero() -> None:
    """A zero tick means 'unknown filter' and round_to_tick passes through."""
    client = MagicMock()
    client.futures_exchange_info.return_value = {
        "symbols": [
            {
                "symbol": "AAAUSDT",
                "filters": [
                    {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
                ],
            },
        ]
    }
    adapter = BinanceFuturesAdapter(client, mode="dry_run")
    assert adapter.get_filters(["AAAUSDT"])["AAAUSDT"].price_tick == 0.0


def test_get_marks_parses_prices() -> None:
    client = MagicMock()
    client.futures_mark_price.return_value = [
        {"symbol": "AAAUSDT", "markPrice": "100.0"},
        {"symbol": "BBBUSDT", "markPrice": "50.0"},
    ]
    adapter = BinanceFuturesAdapter(client, mode="dry_run")
    assert adapter.get_marks(["AAAUSDT"]) == {"AAAUSDT": 100.0}


def test_get_book_tops_returns_bid_ask_for_wanted_symbols() -> None:
    client = MagicMock()
    client.futures_orderbook_ticker.return_value = [
        {"symbol": "AAAUSDT", "bidPrice": "99.98", "askPrice": "100.02"},
        {"symbol": "BBBUSDT", "bidPrice": "10.1", "askPrice": "10.2"},
        {"symbol": "ZZZUSDT", "bidPrice": "1.0", "askPrice": "1.1"},
    ]
    adapter = BinanceFuturesAdapter(client, mode="dry_run")
    tops = adapter.get_book_tops(["AAAUSDT", "BBBUSDT"])
    assert tops == {"AAAUSDT": (99.98, 100.02), "BBBUSDT": (10.1, 10.2)}


def test_get_book_tops_drops_non_positive_quotes() -> None:
    """A zero or missing side is unusable — the caller must skip that symbol."""
    client = MagicMock()
    client.futures_orderbook_ticker.return_value = [
        {"symbol": "AAAUSDT", "bidPrice": "0", "askPrice": "100.02"},
        {"symbol": "BBBUSDT", "bidPrice": "10.1", "askPrice": "10.2"},
    ]
    adapter = BinanceFuturesAdapter(client, mode="dry_run")
    assert adapter.get_book_tops(["AAAUSDT", "BBBUSDT"]) == {"BBBUSDT": (10.1, 10.2)}


def test_submit_limit_sends_gtx_post_only() -> None:
    client = _live_client()
    adapter = BinanceFuturesAdapter(client, mode="live")
    intent = OrderIntent("AAAUSDT", "BUY", 2.0, False, 200.0, "open", "LIMIT")
    adapter.submit(intent, price=99.98)
    kwargs = client.futures_create_order.call_args.kwargs
    assert kwargs["type"] == "LIMIT"
    assert kwargs["timeInForce"] == "GTX"  # post-only: reject rather than cross
    assert kwargs["price"] == 99.98
    assert kwargs["quantity"] == 2.0


def test_submit_limit_price_from_realistic_quote_is_not_over_precise() -> None:
    """The assertion that actually models the wire: python-binance serialises
    `params["price"]` with a bare `str()`, so any float error `round_to_tick`
    leaves behind goes straight onto the wire and Binance rejects it (-1111).

    The quote is parsed the same way `get_book_tops` parses a real one
    (`float(r["bidPrice"])`), not built as `mult * tick` — see
    `round_to_tick`'s own test suite for why that distinction is load-bearing.
    Pre-fix this price is `45817.600000000006`; post-fix `45817.6`.
    """
    client = _live_client()
    adapter = BinanceFuturesAdapter(client, mode="live")
    price = round_to_tick(float("45817.6"), 0.1, "BUY")
    intent = OrderIntent("BTCUSDT", "BUY", 0.01, False, 458.176, "open", "LIMIT")
    adapter.submit(intent, price=price)
    kwargs = client.futures_create_order.call_args.kwargs
    sent = str(kwargs["price"])
    decimals = len(sent.split(".")[1]) if "." in sent else 0
    assert decimals <= 1, (
        f"price {sent!r} carries more precision than the 0.1 tick allows"
    )


def test_submit_market_omits_price_and_tif() -> None:
    client = _live_client()
    adapter = BinanceFuturesAdapter(client, mode="live")
    intent = OrderIntent("AAAUSDT", "SELL", 2.0, True, -200.0, "close", "MARKET")
    adapter.submit(intent)
    kwargs = client.futures_create_order.call_args.kwargs
    assert kwargs["type"] == "MARKET"
    assert "price" not in kwargs and "timeInForce" not in kwargs
    assert kwargs["reduceOnly"] is True


def test_submit_limit_without_price_raises() -> None:
    """A LIMIT with no price is a caller bug — fail loudly, never silently market."""
    client = _live_client()
    adapter = BinanceFuturesAdapter(client, mode="live")
    intent = OrderIntent("AAAUSDT", "BUY", 2.0, False, 200.0, "open", "LIMIT")
    with pytest.raises(ValueError, match="LIMIT order requires a price"):
        adapter.submit(intent, price=None)
    client.futures_create_order.assert_not_called()


def test_submit_dry_run_reports_type_and_price_without_calling() -> None:
    client = MagicMock()
    adapter = BinanceFuturesAdapter(client, mode="dry_run")
    intent = OrderIntent("AAAUSDT", "BUY", 2.0, False, 200.0, "open", "LIMIT")
    out = adapter.submit(intent, price=99.98)
    assert out["dryRun"] is True
    assert out["orderType"] == "LIMIT" and out["price"] == 99.98
    client.futures_create_order.assert_not_called()


def test_ensure_account_config_raises_on_hedge_mode() -> None:
    client = MagicMock()
    client.futures_get_position_mode.return_value = {"dualSidePosition": True}
    adapter = BinanceFuturesAdapter(client, mode="testnet")
    with pytest.raises(RuntimeError, match="hedge"):
        adapter.ensure_account_config(["AAAUSDT"], leverage=5)


def test_ensure_account_config_swallows_4046(monkeypatch: Any) -> None:
    import trade.binance_futures as mod

    monkeypatch.setattr(mod, "APIError", _APIError, raising=False)
    client = MagicMock()
    client.futures_get_position_mode.return_value = {"dualSidePosition": False}
    client.futures_change_margin_type.side_effect = _APIError(-4046)
    adapter = BinanceFuturesAdapter(client, mode="testnet")
    adapter.ensure_account_config(["AAAUSDT"], leverage=5)  # must not raise
    client.futures_change_leverage.assert_called_once_with(symbol="AAAUSDT", leverage=5)


def test_ensure_account_config_noop_in_dry_run() -> None:
    client = MagicMock()
    adapter = BinanceFuturesAdapter(client, mode="dry_run")
    adapter.ensure_account_config(["AAAUSDT"], leverage=5)
    client.futures_get_position_mode.assert_not_called()
    client.futures_change_leverage.assert_not_called()


def test_get_open_orders_returns_rows_with_client_ids_even_in_dry_run() -> None:
    """Not dry-run-guarded: a dry run must see the real resting orders, since
    the XS executor picks its own stale ones out of them by client id."""
    client = MagicMock()
    client.futures_get_open_orders.return_value = [
        {"symbol": "AAAUSDT", "orderId": 1, "clientOrderId": "xs-1-AAAUSDT"},
        {"symbol": "BBBUSDT", "orderId": 2, "clientOrderId": "web_abc"},
    ]
    adapter = BinanceFuturesAdapter(client, mode="dry_run")
    rows = adapter.get_open_orders()
    assert [r["clientOrderId"] for r in rows] == ["xs-1-AAAUSDT", "web_abc"]
    client.futures_get_open_orders.assert_called_once_with()


def test_the_adapter_can_no_longer_cancel_symbol_wide() -> None:
    """#1023 removed `cancel_open_orders`: every cancel now names one order.

    A symbol-wide cancel also killed the operator's own resting orders, which
    is what forced the card-place XS veto. Pinned structurally so it cannot
    quietly come back as a convenience.
    """
    assert not hasattr(BinanceFuturesAdapter, "cancel_open_orders")
    assert not hasattr(BinanceFuturesAdapter, "get_open_order_symbols")


# ----- #1023: our own client order ids -----


def test_make_client_order_id_joins_parts_and_enforces_binances_rule() -> None:
    assert make_client_order_id("ex", 1_760_000_000_000, "L", "stop") == (
        "ex-1760000000000-L-stop"
    )
    with pytest.raises(ValueError, match="36-char"):
        make_client_order_id("xs", "x" * 40)
    with pytest.raises(ValueError, match="36-char"):
        make_client_order_id("cd", "has space")


def test_submit_sends_new_client_order_id_on_the_classic_route() -> None:
    client = _live_client()
    intent = OrderIntent(
        "AAAUSDT", "BUY", 2.0, False, 200.0, "card", "LIMIT", client_order_id="cd-1"
    )
    BinanceFuturesAdapter(client, mode="live").submit(intent, price=99.98)
    kwargs = client.futures_create_order.call_args.kwargs
    assert kwargs["newClientOrderId"] == "cd-1"
    assert "clientAlgoId" not in kwargs


def test_submit_sends_client_algo_id_on_the_algo_route() -> None:
    client = MagicMock()
    client.futures_create_order.return_value = {"algoId": 5}
    intent = OrderIntent(
        "AAAUSDT", "SELL", 0.0, True, 0.0, "exit_stop", "STOP_MARKET",
        stop_price=95.0, close_position=True, client_order_id="ex-1-L-stop",
    )  # fmt: skip
    BinanceFuturesAdapter(client, mode="live").submit(intent)
    kwargs = client.futures_create_order.call_args.kwargs
    assert kwargs["clientAlgoId"] == "ex-1-L-stop"
    assert "newClientOrderId" not in kwargs


def test_the_installed_library_keeps_our_id_only_under_each_routes_own_key() -> None:
    """Why the adapter switches keys, measured on python-binance itself.

    The library DROPS `newClientOrderId` on the algo route and invents a
    random `clientAlgoId`, so a stop sent under the classic key loses the id
    the exit manager wrote into its intent row. This drives the real
    `Client.futures_create_order` with only the HTTP layer stubbed; a library
    upgrade that changes either route's handling fails here first.
    """
    from binance.client import Client

    sent: list[tuple[str, dict[str, Any]]] = []

    def _capture(
        method: str, path: str, signed: bool, data: dict[str, Any]
    ) -> dict[str, Any]:
        sent.append((path, dict(data)))
        return {"algoId": 7} if path == "algoOrder" else {"orderId": 8}

    client = Client("key", "secret", ping=False)
    client._request_futures_api = _capture
    adapter = BinanceFuturesAdapter(client, mode="live")
    adapter.submit(
        OrderIntent(
            "AAAUSDT",
            "SELL",
            0.0,
            True,
            0.0,
            "exit_stop",
            "STOP_MARKET",
            stop_price=95.0,
            close_position=True,
            client_order_id="ex-1-L-stop",
        )  # fmt: skip
    )
    adapter.submit(
        OrderIntent(
            "AAAUSDT",
            "SELL",
            1.0,
            True,
            0.0,
            "exit_tp1",
            "LIMIT",
            client_order_id="ex-1-L-tp1",
        ),  # fmt: skip
        price=110.0,
    )
    (algo_path, algo), (classic_path, classic) = sent
    assert (algo_path, algo["clientAlgoId"]) == ("algoOrder", "ex-1-L-stop")
    assert (classic_path, classic["newClientOrderId"]) == ("order", "ex-1-L-tp1")

    # The control: the classic key on a conditional is silently replaced.
    sent.clear()
    client.futures_create_order(
        symbol="AAAUSDT", side="SELL", type="STOP_MARKET", stopPrice=95.0,
        newClientOrderId="ex-1-L-stop",
    )  # fmt: skip
    assert sent[0][1]["clientAlgoId"] != "ex-1-L-stop"
    assert "newClientOrderId" not in sent[0][1]


def test_submit_without_an_id_sends_neither_key() -> None:
    """Callers that send no id keep the wire shape they had before #1023."""
    client = _live_client()
    intent = OrderIntent("AAAUSDT", "BUY", 2.0, False, 200.0, "open", "LIMIT")
    BinanceFuturesAdapter(client, mode="live").submit(intent, price=99.98)
    kwargs = client.futures_create_order.call_args.kwargs
    assert "newClientOrderId" not in kwargs and "clientAlgoId" not in kwargs


def test_submit_refuses_a_malformed_id_before_any_request() -> None:
    client = _live_client()
    intent = OrderIntent(
        "AAAUSDT", "BUY", 2.0, False, 200.0, "card", "LIMIT", client_order_id="bad id"
    )
    with pytest.raises(ValueError, match="client order id"):
        BinanceFuturesAdapter(client, mode="live").submit(intent, price=99.98)
    client.futures_create_order.assert_not_called()


def test_find_order_queries_each_route_by_its_own_client_id_key() -> None:
    client = MagicMock()
    client.futures_get_order.return_value = {"orderId": 4, "status": "NEW"}
    adapter = BinanceFuturesAdapter(client, mode="dry_run")
    assert adapter.find_order("A", "cd-1", conditional=False) == {
        "orderId": 4,
        "status": "NEW",
    }
    assert client.futures_get_order.call_args.kwargs == {
        "symbol": "A",
        "origClientOrderId": "cd-1",
    }
    client.futures_get_order.return_value = {"algoId": 9, "algoStatus": "NEW"}
    found = adapter.find_order("A", "ex-1-L-stop", conditional=True)
    assert found is not None and found["algoId"] == 9
    assert client.futures_get_order.call_args.kwargs == {
        "symbol": "A",
        "clientAlgoId": "ex-1-L-stop",
    }


def test_find_order_reads_2013_as_absent_and_raises_on_anything_else(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import trade.binance_futures as mod

    monkeypatch.setattr(mod, "APIError", _APIError, raising=False)
    client = MagicMock()
    client.futures_get_order.side_effect = _APIError(ORDER_NOT_FOUND)
    assert find_order_by_client_id(client, "A", "cd-1", conditional=False) is None
    client.futures_get_order.side_effect = _APIError(-1021)
    with pytest.raises(_APIError):
        find_order_by_client_id(client, "A", "cd-1", conditional=False)


def test_find_order_body_with_no_id_is_unconfirmed_not_found() -> None:
    """A 2xx body naming no order proves nothing, so it never reads as found."""
    client = MagicMock()
    client.futures_get_order.return_value = {"code": -1, "msg": "?"}
    with pytest.raises(UnconfirmedOrderError):
        find_order_by_client_id(client, "A", "ex-1-L-stop", conditional=True)


def test_submit_limit_hedge_mode_sends_position_side_and_omits_reduce_only() -> None:
    client = _live_client()
    adapter = BinanceFuturesAdapter(client, mode="live")
    intent = OrderIntent(
        "AAAUSDT", "BUY", 2.0, False, 200.0, "card", "LIMIT", position_side="LONG"
    )
    adapter.submit(intent, price=99.98)
    kwargs = client.futures_create_order.call_args.kwargs
    assert kwargs["positionSide"] == "LONG"
    assert "reduceOnly" not in kwargs  # hedge mode rejects the parameter
    assert kwargs["timeInForce"] == "GTX"


def test_submit_one_way_shape_is_unchanged_when_position_side_absent() -> None:
    client = _live_client()
    adapter = BinanceFuturesAdapter(client, mode="live")
    intent = OrderIntent("AAAUSDT", "SELL", 2.0, False, -200.0, "open", "LIMIT")
    adapter.submit(intent, price=99.98)
    kwargs = client.futures_create_order.call_args.kwargs
    assert kwargs["reduceOnly"] is False
    assert "positionSide" not in kwargs


def test_api_error_binds_to_the_client_library_not_the_local_shim() -> None:
    """ST37 R2: the installed python-binance exposes `BinanceAPIException`,

    not `APIError` — so the old bare `from binance.exceptions import APIError`
    always raised ImportError and silently bound the local dead shim, which
    meant every `except APIError` in this module could only ever catch that
    shim, never a real exception the client raises. Pin the fix rather than
    the exact class name, since the library's own spelling has already
    varied by version.
    """
    import trade.binance_futures as mod

    assert mod.APIError.__module__.startswith("binance")


# ----- #829: a 2xx response is accepted only when it carries its route's id -----


def test_require_ack_reads_the_id_each_route_acknowledges_with() -> None:
    """python-binance routes conditionals to /algoOrder, which answers `algoId`."""
    assert require_ack("X", "LIMIT", {"orderId": 7})["orderId"] == 7
    assert require_ack("X", "STOP_MARKET", {"algoId": 9})["algoId"] == 9
    with pytest.raises(UnconfirmedOrderError):
        require_ack("X", "STOP_MARKET", {"orderId": 9})
    with pytest.raises(UnconfirmedOrderError):
        require_ack("X", "LIMIT", {"algoId": 7})


def test_submit_raises_unconfirmed_on_an_error_body_served_as_http_200() -> None:
    """python-binance raises only on a non-2xx status, so this body arrives as a dict.

    It must not read as an accepted order, and it must not read as an APIError
    either: an APIError tells callers nothing exists, which this does not prove.
    """
    client = MagicMock()
    body = {"code": -2022, "msg": "ReduceOnly Order is rejected."}
    client.futures_create_order.return_value = body
    adapter = BinanceFuturesAdapter(client, mode="live")
    intent = OrderIntent("AAAUSDT", "BUY", 2.0, False, 200.0, "open", "LIMIT")
    with pytest.raises(UnconfirmedOrderError) as info:
        adapter.submit(intent, price=99.98)
    assert info.value.body == body
    assert "-2022" in str(info.value)
    assert not isinstance(info.value, APIError)


# ----- #981: conditional exits, per-id cancel and the exit manager's reads -----


def _algo_client() -> MagicMock:
    client = MagicMock()
    client.futures_create_order.return_value = {"algoId": 5}
    return client


def test_close_position_stop_sends_trigger_and_omits_quantity() -> None:
    client = _algo_client()
    adapter = BinanceFuturesAdapter(client, mode="live")
    intent = OrderIntent(
        "AAAUSDT",
        "SELL",
        0.0,
        True,
        0.0,
        "exit_stop",
        "STOP_MARKET",
        position_side="LONG",
        stop_price=95.0,
        close_position=True,
    )
    assert adapter.submit(intent)["algoId"] == 5
    kwargs = client.futures_create_order.call_args.kwargs
    assert kwargs["closePosition"] == "true"
    assert kwargs["stopPrice"] == 95.0
    assert kwargs["workingType"] == "MARK_PRICE"
    assert "quantity" not in kwargs and "reduceOnly" not in kwargs


def test_close_position_on_a_one_way_account_still_omits_reduce_only() -> None:
    client = _algo_client()
    adapter = BinanceFuturesAdapter(client, mode="live")
    intent = OrderIntent(
        "AAAUSDT", "SELL", 0.0, True, 0.0, "x", "STOP_MARKET",
        stop_price=95.0, close_position=True,
    )  # fmt: skip
    adapter.submit(intent)
    assert "reduceOnly" not in client.futures_create_order.call_args.kwargs


def test_conditional_without_trigger_and_misplaced_close_position_raise() -> None:
    adapter = BinanceFuturesAdapter(_algo_client(), mode="dry_run")
    with pytest.raises(ValueError, match="stop_price"):
        adapter.submit(OrderIntent("A", "SELL", 1.0, True, 0.0, "x", "STOP_MARKET"))
    with pytest.raises(ValueError, match="conditional"):
        adapter.submit(
            OrderIntent("A", "SELL", 1.0, True, 0.0, "x", "LIMIT", close_position=True),
            price=1.0,
        )


def test_unacknowledged_conditional_raises_unconfirmed() -> None:
    client = MagicMock()
    client.futures_create_order.return_value = {"orderId": 9}  # wrong route's id
    adapter = BinanceFuturesAdapter(client, mode="live")
    intent = OrderIntent(
        "A", "SELL", 0.0, True, 0.0, "x", "STOP_MARKET",
        stop_price=1.0, close_position=True,
    )  # fmt: skip
    with pytest.raises(UnconfirmedOrderError):
        adapter.submit(intent)


def test_cancel_order_is_by_one_id_and_a_dry_run_noop() -> None:
    client = MagicMock()
    BinanceFuturesAdapter(client, mode="live").cancel_order("A", algo_id=3)
    BinanceFuturesAdapter(client, mode="live").cancel_order("A", order_id=4)
    calls = [c.kwargs for c in client.futures_cancel_order.call_args_list]
    assert calls == [{"symbol": "A", "algoId": 3}, {"symbol": "A", "orderId": 4}]
    BinanceFuturesAdapter(client, mode="dry_run").cancel_order("A", order_id=4)
    assert client.futures_cancel_order.call_count == 2
    with pytest.raises(ValueError):
        BinanceFuturesAdapter(client, mode="live").cancel_order("A")


def test_get_side_position_reads_the_side_in_either_mode() -> None:
    client = MagicMock()
    client.futures_position_information.return_value = [
        {"symbol": "A", "positionSide": "LONG", "positionAmt": "2", "entryPrice": "10"},
        {
            "symbol": "A",
            "positionSide": "SHORT",
            "positionAmt": "-3",
            "entryPrice": "11",
        },
    ]
    adapter = BinanceFuturesAdapter(client, mode="dry_run")
    assert adapter.get_side_position("A", "SHORT", dual_side=True) == (3.0, 11.0)
    client.futures_position_information.return_value = [
        {
            "symbol": "A",
            "positionSide": "BOTH",
            "positionAmt": "-3",
            "entryPrice": "11",
        },
    ]
    assert adapter.get_side_position("A", "LONG", dual_side=False) == (0.0, 0.0)
    assert adapter.get_side_position("A", "SHORT", dual_side=False) == (3.0, 11.0)


def test_get_account_trades_tiles_seven_days_with_an_explicit_end() -> None:
    client = MagicMock()
    client.futures_account_trades.return_value = []
    adapter = BinanceFuturesAdapter(client, mode="dry_run")
    day = 86_400_000
    adapter.get_account_trades("A", 0, 10 * day)
    windows = [
        (c.kwargs["startTime"], c.kwargs["endTime"])
        for c in client.futures_account_trades.call_args_list
    ]
    assert windows == [(0, 7 * day), (7 * day + 1, 10 * day)]


def test_get_account_trades_pages_a_full_tile_and_dedups() -> None:
    page = [{"id": i, "time": i} for i in range(1000)]
    client = MagicMock()
    client.futures_account_trades.side_effect = [page, [{"id": 999, "time": 999}]]
    adapter = BinanceFuturesAdapter(client, mode="dry_run")
    out = adapter.get_account_trades("A", 0, 5_000)
    assert len(out) == 1000
    assert client.futures_account_trades.call_args.kwargs["startTime"] == 999
