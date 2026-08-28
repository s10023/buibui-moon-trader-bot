from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from portfolio.sizing import round_to_tick
from trade.binance_futures import BinanceFuturesAdapter
from trade.routing import OrderIntent


class _APIError(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(f"code {code}")
        self.code = code


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
    client = MagicMock()
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
    client = MagicMock()
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
    client = MagicMock()
    adapter = BinanceFuturesAdapter(client, mode="live")
    intent = OrderIntent("AAAUSDT", "SELL", 2.0, True, -200.0, "close", "MARKET")
    adapter.submit(intent)
    kwargs = client.futures_create_order.call_args.kwargs
    assert kwargs["type"] == "MARKET"
    assert "price" not in kwargs and "timeInForce" not in kwargs
    assert kwargs["reduceOnly"] is True


def test_submit_limit_without_price_raises() -> None:
    """A LIMIT with no price is a caller bug — fail loudly, never silently market."""
    client = MagicMock()
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


def test_get_open_order_symbols_dedups() -> None:
    client = MagicMock()
    client.futures_get_open_orders.return_value = [
        {"symbol": "AAAUSDT", "orderId": 1},
        {"symbol": "AAAUSDT", "orderId": 2},
        {"symbol": "BBBUSDT", "orderId": 3},
    ]
    adapter = BinanceFuturesAdapter(client, mode="live")
    assert adapter.get_open_order_symbols() == {"AAAUSDT", "BBBUSDT"}


def test_get_open_order_symbols_dry_run_hits_the_api() -> None:
    """Not dry-run-guarded: a dry run must see real resting orders, since
    that's the exact condition `cancel_open_orders` exists to handle."""
    client = MagicMock()
    client.futures_get_open_orders.return_value = [
        {"symbol": "AAAUSDT", "orderId": 1},
        {"symbol": "BBBUSDT", "orderId": 2},
    ]
    adapter = BinanceFuturesAdapter(client, mode="dry_run")
    assert adapter.get_open_order_symbols() == {"AAAUSDT", "BBBUSDT"}
    client.futures_get_open_orders.assert_called_once()


def test_cancel_open_orders_calls_per_symbol() -> None:
    client = MagicMock()
    adapter = BinanceFuturesAdapter(client, mode="live")
    adapter.cancel_open_orders("AAAUSDT")
    client.futures_cancel_all_open_orders.assert_called_once_with(symbol="AAAUSDT")


def test_cancel_open_orders_dry_run_is_a_noop() -> None:
    client = MagicMock()
    adapter = BinanceFuturesAdapter(client, mode="dry_run")
    adapter.cancel_open_orders("AAAUSDT")
    client.futures_cancel_all_open_orders.assert_not_called()


def test_submit_limit_hedge_mode_sends_position_side_and_omits_reduce_only() -> None:
    client = MagicMock()
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
    client = MagicMock()
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
