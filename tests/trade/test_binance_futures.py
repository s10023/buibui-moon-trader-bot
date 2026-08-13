from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

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
