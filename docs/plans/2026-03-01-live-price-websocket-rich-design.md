# Design: Live Price Monitor — WebSocket + Rich Terminal

**Date:** 2026-03-01
**Status:** Approved

## Problem

The current `--live` mode polls Binance REST every 5 seconds for all coins. This is slow,
causes screen flicker (clear + reprint), and is unnecessarily expensive. PR #64 proposed
WebSocket + Rich but was abandoned: it targeted the old monolithic structure, had a missing
import bug, used the wrong WebSocket library, and left Rich disconnected from the WS data.

## Solution

Replace the live mode polling loop with:

- **Binance WebSocket** (`ThreadedWebsocketManager` + `@miniTicker` multiplex streams) for
  real-time price updates
- **Rich `Live` table** for flicker-free terminal rendering

Snapshot mode (`--live` off) is untouched.

## Architecture

### New files

**`utils/live_store.py`** — `LiveDataStore` class

Thread-safe shared state between the WebSocket thread and the render thread.
Uses a single `threading.Lock`. Holds:

- `_tickers: dict[str, TickerData]` — last price + 24h % per symbol (from WebSocket)
- `_klines: dict[str, KlineData]` — 15m/1h/Asia open prices per symbol (from REST)
- `ws_connected: bool` — True after first WS message, False on error/close
- `last_update: datetime | None` — timestamp of last WebSocket message

Public API:

- `update_ticker(symbol, last, open_24h)` — computes change_24h, acquires lock, writes
- `update_klines(symbol, open_15m, open_1h, asia_open)` — acquires lock, writes
- `set_ws_status(connected: bool)` — updates connection flag
- `snapshot(symbols)` — acquires lock once, returns immutable copy of all data

**`monitor/live_price.py`** — orchestration

- `run(client, coins, sort_col, sort_order)` — entry point called by `price_monitor.py`
- `_handle_ws_msg(msg, store)` — pure function, handles one WebSocket message dict
- `_refresh_klines(client, coins, store)` — fetches klines via existing batch functions
- `_build_table(coins, store, sort_col, sort_order)` — builds Rich `Table` from snapshot
- `_kline_refresh_loop(client, coins, store, interval)` — background daemon thread

### Modified files

**`monitor/price_lib.py`** — additive only, no changes to existing functions

- `format_pct_rich(pct: float) -> Text` — Rich `Text` object (green/red/yellow)
- `sort_table_raw(rows, col_idx, reverse)` — sorts on raw float values before formatting

**`monitor/price_monitor.py`** — 2-line change in live branch

```python
# before
else:
    try:
        while True:
            # ...
            time.sleep(5)

# after
else:
    live_price.run(client, coins, sort_col, sort_order)
```

**`pyproject.toml`** — add `rich >= 14.0.0`

## Data Flow

### Startup sequence

1. `batch_get_klines()` + `batch_get_asia_open()` — blocking REST, fills kline opens in store
2. `ThreadedWebsocketManager().start()`
3. `start_multiplex_socket(["btcusdt@miniTicker", ...])` — public stream, no API keys needed
4. Kline refresh daemon thread starts (refreshes every 60s)
5. Rich `Live` render loop starts (refreshes every 1s)

### WebSocket message handling

```text
msg["data"]["s"]  → symbol
msg["data"]["c"]  → last price
msg["data"]["o"]  → 24h open price
change_24h = (last - open_24h) / open_24h * 100
store.update_ticker(symbol, last, open_24h)
store.set_ws_status(connected=True)
store.last_update = datetime.now()
```

### Render tick (every 1s)

```text
snapshot = store.snapshot(coins)      ← one lock acquisition
for each coin:
    if no ticker yet → row shows "..." in price column, blanks elsewhere
    if ticker + klines → compute pct changes from raw floats
    if ticker + no klines → show price + 24h%, other columns "N/A"
sort rows on raw floats (before formatting)
format pct columns with format_pct_rich() → Text objects
build Rich Table
live.update(table)
```

### Kline refresh (every 60s, background thread)

```text
batch_get_klines() + batch_get_asia_open()  ← REST
store.update_klines(symbol, open_15m, open_1h, asia_open) for each symbol
```

## Error Handling

| Scenario | Behaviour |
| --- | --- |
| WS not yet connected at render | `...` in price cell, blanks for pct columns |
| WS disconnect / reconnecting | Title shows `⚠️ WebSocket reconnecting... \| Last update: HH:MM:SS`; prices frozen |
| WS error (all retries exhausted) | Same stale indicator; prices frozen at last known values |
| Kline fetch error for a coin | Price + 24h% show normally; 15m/1h/Asia columns show `N/A` |
| KeyboardInterrupt | `twm.stop()`, Rich Live exits cleanly, prints "Exiting gracefully. Goodbye!" |

## Table Display

```text
📈 Live Crypto Price Monitor — Buibui Moon Bot  |  Last update: 14:32:05
┌─────────┬────────────┬───────┬───────┬────────────────┬───────┐
│ Symbol  │ Last Price │  15m% │   1h% │ Since Asia 8AM │  24h% │
├─────────┼────────────┼───────┼───────┼────────────────┼───────┤
│ BTCUSDT │   67450.12 │ +0.3% │ +1.2% │         +2.1%  │ +3.4% │
│ ETHUSDT │       ...  │       │       │                │       │
└─────────┴────────────┴───────┴───────┴────────────────┴───────┘
```

- Positive % → green, Negative % → red, Zero → yellow
- `--sort` flag works identically to snapshot mode

## Testing

**`tests/test_live_store.py`**

- `update_ticker()` computes `change_24h` correctly
- `update_klines()` stores and retrieves correctly
- `snapshot()` returns a copy (mutations don't affect the store)
- `set_ws_status()` transitions correctly
- Thread safety: two threads writing simultaneously don't corrupt state

**`tests/test_live_price.py`**

- `_handle_ws_msg()` with valid miniTicker dict → correct store.update_ticker call
- `_handle_ws_msg()` with error dict → no store write, sets ws_connected=False
- `_build_table()` renders `...` for coins with no ticker data
- `_build_table()` renders `N/A` for coins with ticker but no kline data
- `_build_table()` shows stale title when `ws_connected=False`
- `sort_table_raw()` sorts ascending and descending correctly

**`tests/test_price_lib.py`** (extend existing)

- `format_pct_rich()` returns green Text for positive, red for negative, yellow for zero

## Dependencies

- `rich >= 14.0.0` — new production dependency
- `ThreadedWebsocketManager` — from `python-binance`, already in dep tree
- No other new dependencies
