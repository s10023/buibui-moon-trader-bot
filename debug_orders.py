"""Debug script — exhaustive search for SL orders across all Binance endpoints."""

import json

from utils.binance_client import create_client

SYMBOLS = ["BTCUSDT", "ETHUSDT"]

client = create_client()


def dump(label: str, data: object) -> None:
    print(f"\n=== {label} ===")
    print(json.dumps(data, indent=2))


# ── Standard open orders ──────────────────────────────────────────────────────
dump("openOrders (no symbol)", client.futures_get_open_orders())

for sym in SYMBOLS:
    dump(f"openOrders symbol={sym}", client.futures_get_open_orders(symbol=sym))

# ── Recent order history (all types, last 500) ────────────────────────────────
for sym in SYMBOLS:
    orders = client.futures_get_all_orders(symbol=sym, limit=500)
    stop_orders = [
        o
        for o in orders
        if o.get("type")
        in (
            "STOP_MARKET",
            "STOP",
            "TRAILING_STOP_MARKET",
            "TAKE_PROFIT_MARKET",
            "TAKE_PROFIT",
        )
    ]
    dump(f"allOrders STOP/TP types (last 500) symbol={sym}", stop_orders)

# ── Binance SAPI algo futures endpoint (position card TP/SL in newer UI) ──────
# These use a different base URL (api.binance.com/sapi) not the futures API
try:
    result = client._request_margin_api("get", "algo/futures/openOrders", True, data={})
    dump("SAPI /algo/futures/openOrders", result)
except Exception as e:
    print(f"\n=== SAPI /algo/futures/openOrders ===\nFailed: {e}")

for sym in SYMBOLS:
    try:
        result = client._request_margin_api(
            "get", "algo/futures/openOrders", True, data={"symbol": sym}
        )
        dump(f"SAPI /algo/futures/openOrders symbol={sym}", result)
    except Exception as e:
        print(f"\n=== SAPI /algo/futures/openOrders symbol={sym} ===\nFailed: {e}")

# ── Binance futures algo orders ────────────────────────────────────────────────
for endpoint in ("algo/orders/openOrders",):
    try:
        result = client._request_futures_api("get", endpoint, True, data={})
        dump(f"futures /{endpoint}", result)
    except Exception as e:
        print(f"\n=== futures /{endpoint} ===\nFailed: {e}")

# ── Hedge mode status ──────────────────────────────────────────────────────────
try:
    result = client._request_futures_api("get", "positionSide/dual", True, data={})
    dump("futures /positionSide/dual (hedge mode status)", result)
except Exception as e:
    print(f"\n=== futures /positionSide/dual ===\nFailed: {e}")

# ── Full position risk (every field) ──────────────────────────────────────────
print("\n=== /fapi/v2/positionRisk FULL (open positions only) ===")
for p in client.futures_position_information():
    if float(p["positionAmt"]) != 0:
        print(json.dumps(p, indent=2))

# ── SL-relevant fields from positionRisk ──────────────────────────────────────
# Checks whether stopPrice / liquidationPrice / notionalValue carry SL info
# so we can use positionRisk as a fallback when no standalone STOP_MARKET order exists.
SL_FIELDS = ("stopPrice", "liquidationPrice", "breakEvenPrice", "notionalValue")
print("\n=== SL-relevant fields from positionRisk (open positions only) ===")
for p in client.futures_position_information():
    if float(p["positionAmt"]) != 0:
        summary = {k: p.get(k) for k in SL_FIELDS}
        summary["symbol"] = p["symbol"]
        summary["positionAmt"] = p["positionAmt"]
        print(json.dumps(summary, indent=2))
