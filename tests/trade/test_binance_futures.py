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


def _intent() -> OrderIntent:
    return OrderIntent("AAAUSDT", "BUY", 2.0, False, 200.0, "open")


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


def test_get_marks_parses_prices() -> None:
    client = MagicMock()
    client.futures_mark_price.return_value = [
        {"symbol": "AAAUSDT", "markPrice": "100.0"},
        {"symbol": "BBBUSDT", "markPrice": "50.0"},
    ]
    adapter = BinanceFuturesAdapter(client, mode="dry_run")
    assert adapter.get_marks(["AAAUSDT"]) == {"AAAUSDT": 100.0}


def test_submit_market_is_noop_in_dry_run() -> None:
    client = MagicMock()
    adapter = BinanceFuturesAdapter(client, mode="dry_run")
    res = adapter.submit_market(_intent())
    assert res["dryRun"] is True
    client.futures_create_order.assert_not_called()


def test_submit_market_calls_create_order_on_testnet() -> None:
    client = MagicMock()
    client.futures_create_order.return_value = {"orderId": 1}
    adapter = BinanceFuturesAdapter(client, mode="testnet")
    adapter.submit_market(_intent())
    client.futures_create_order.assert_called_once_with(
        symbol="AAAUSDT",
        side="BUY",
        type="MARKET",
        quantity=2.0,
        reduceOnly=False,
    )


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
