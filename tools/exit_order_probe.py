"""Dump the raw Binance response to a stop placed against a LIVE position (#829).

The question #829 left open: on 2026-08-29 a conditional placed against a live
TRXUSDT position came back as a dict with no `orderId`, and no order showed in
`openOrders`. python-binance 1.0.37 routes every conditional type to
`/fapi/v1/algoOrder`, which acknowledges with `algoId` and lists under
`openAlgoOrders`, so the likely reading is "the stop WAS placed, and the code
looked in the wrong place". This probe settles it on the exchange.

Read-only by default: it prints the parameters it would send. With `--go` it
places ONE reduce-only STOP_MARKET for the whole position side, triggered at
half (long) or 1.5x (short) of mark so it cannot fire, prints the raw ack and
the `openAlgoOrders` listing, then cancels it by `algoId` and lists again.
The operator runs `--go`; it places a real order on the live account.

`--close-position` sends the stop the exit manager (#981) rests instead: a
`closePosition` STOP_MARKET with no quantity, which Binance accepts only
against an open position (#829 measured -4509 without one).

    python tools/exit_order_probe.py TRXUSDT --position-side LONG [--close-position] [--go]
"""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

# A bare `python tools/exit_order_probe.py` puts `tools/` on sys.path rather
# than the repo root, so repo imports need the root added first.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:  # pragma: no cover - import bootstrap
    sys.path.insert(0, str(REPO_ROOT))

from portfolio.sizing import round_to_tick  # noqa: E402

# Far enough from mark that the probe stop cannot trigger while it rests.
_TRIGGER_FRAC = {"LONG": 0.5, "SHORT": 1.5}


def _tick_size(client: Any, symbol: str) -> float:
    for s in client.futures_exchange_info()["symbols"]:
        if s["symbol"] == symbol:
            for f in s["filters"]:
                if f["filterType"] == "PRICE_FILTER":
                    return float(f["tickSize"])
    raise SystemExit(f"{symbol}: no PRICE_FILTER in exchange info")


def plan_probe(
    client: Any, symbol: str, position_side: str, *, close_position: bool = False
) -> dict[str, Any]:
    """The STOP_MARKET params for the live `position_side` leg, or SystemExit."""
    rows = client.futures_position_information(symbol=symbol)
    amt = sum(
        float(r["positionAmt"]) for r in rows if r.get("positionSide") == position_side
    )
    if amt == 0.0:
        raise SystemExit(
            f"{symbol} {position_side}: no open position - the probe needs a "
            "live one (a stop with no position is rejected -2022, which #829 "
            "already measured)"
        )
    mark = float(client.futures_mark_price(symbol=symbol)["markPrice"])
    side = "SELL" if position_side == "LONG" else "BUY"
    trigger = round_to_tick(
        mark * _TRIGGER_FRAC[position_side], _tick_size(client, symbol), side
    )
    params: dict[str, Any] = {
        "symbol": symbol,
        "side": side,
        "positionSide": position_side,
        "type": "STOP_MARKET",
        "stopPrice": trigger,
        "workingType": "MARK_PRICE",
    }
    if close_position:
        params["closePosition"] = "true"
    else:
        params["quantity"] = str(Decimal(str(abs(amt))))
    return params


def run_probe(client: Any, params: dict[str, Any]) -> list[tuple[str, Any]]:
    """Place, list, cancel, list. Returns each raw response with its label."""
    symbol = params["symbol"]
    out: list[tuple[str, Any]] = []
    ack = client.futures_create_order(**dict(params))
    out.append(("create (raw)", ack))
    out.append(
        (
            "openAlgoOrders",
            client.futures_get_open_orders(symbol=symbol, conditional=True),
        )
    )
    algo_id = ack.get("algoId") if isinstance(ack, dict) else None
    if algo_id is None:
        out.append(
            (
                "NO algoId",
                "cannot cancel by id - check openAlgoOrders and openOrders BY HAND",
            )
        )
        return out
    out.append(("cancel", client.futures_cancel_order(symbol=symbol, algoId=algo_id)))
    out.append(
        (
            "openAlgoOrders after cancel",
            client.futures_get_open_orders(symbol=symbol, conditional=True),
        )
    )
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("symbol")
    ap.add_argument("--position-side", choices=sorted(_TRIGGER_FRAC), required=True)
    ap.add_argument(
        "--close-position",
        action="store_true",
        help="probe the exit manager's closePosition stop (no quantity)",
    )
    ap.add_argument(
        "--go", action="store_true", help="place and cancel a REAL order (live account)"
    )
    args = ap.parse_args(argv)

    from utils.binance_client import create_client

    client = create_client()
    params = plan_probe(
        client,
        args.symbol.upper(),
        args.position_side,
        close_position=args.close_position,
    )
    print("params:", json.dumps(params, default=str))
    if not args.go:
        print("read-only: re-run with --go to place and cancel the probe stop")
        return 0
    for label, body in run_probe(client, params):
        print(f"--- {label}\n{json.dumps(body, indent=2, default=str)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
