from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tools.exit_order_probe import plan_probe, run_probe


def _client(long_amt: str = "10", short_amt: str = "0") -> MagicMock:
    client = MagicMock()
    client.futures_position_information.return_value = [
        {"symbol": "TRXUSDT", "positionSide": "LONG", "positionAmt": long_amt},
        {"symbol": "TRXUSDT", "positionSide": "SHORT", "positionAmt": short_amt},
    ]
    client.futures_mark_price.return_value = {"markPrice": "0.40000"}
    client.futures_exchange_info.return_value = {
        "symbols": [
            {
                "symbol": "TRXUSDT",
                "filters": [{"filterType": "PRICE_FILTER", "tickSize": "0.00001"}],
            }
        ]
    }
    return client


def test_plan_rests_a_reduce_only_stop_far_from_mark_for_the_live_leg() -> None:
    params = plan_probe(_client(), "TRXUSDT", "LONG")
    assert params["type"] == "STOP_MARKET"
    assert params["side"] == "SELL" and params["positionSide"] == "LONG"
    assert params["quantity"] == "10.0"
    assert params["stopPrice"] == pytest.approx(0.2)  # half of mark: cannot fire


def test_plan_close_position_variant_matches_the_exit_managers_stop() -> None:
    params = plan_probe(_client(), "TRXUSDT", "LONG", close_position=True)
    assert params["closePosition"] == "true"
    assert "quantity" not in params


def test_plan_refuses_without_a_live_position() -> None:
    with pytest.raises(SystemExit, match="no open position"):
        plan_probe(_client(long_amt="0"), "TRXUSDT", "LONG")


def test_run_cancels_the_probe_by_algo_id() -> None:
    client = _client()
    client.futures_create_order.return_value = {"algoId": 123, "algoStatus": "NEW"}
    labels = [label for label, _ in run_probe(client, {"symbol": "TRXUSDT"})]
    client.futures_cancel_order.assert_called_once_with(symbol="TRXUSDT", algoId=123)
    assert labels[0] == "create (raw)" and labels[-1] == "openAlgoOrders after cancel"


def test_run_without_algo_id_never_guesses_a_cancel() -> None:
    client = _client()
    client.futures_create_order.return_value = {"code": -2022, "msg": "rejected"}
    out = run_probe(client, {"symbol": "TRXUSDT"})
    client.futures_cancel_order.assert_not_called()
    assert out[-1][0] == "NO algoId"


def test_bare_invocation_works() -> None:
    proc = subprocess.run(
        [sys.executable, "tools/exit_order_probe.py", "--help"],
        cwd=Path(__file__).resolve().parent.parent,
        capture_output=True,
        text=True,
        env={k: v for k, v in os.environ.items() if k != "PYTHONPATH"},
    )
    assert proc.returncode == 0, proc.stderr
    assert "--go" in proc.stdout
