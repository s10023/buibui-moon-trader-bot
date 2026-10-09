# Forward-recorded Binance liquidations

Everything else in this directory is gitignored recorder output; this file is the only
tracked one. The recorder is `monitor/liq_recorder.py`, the reader and gap logic are in
`analytics/liquidations.py` (Issue #984).

## The known property: a sample by construction

Binance's all-market liquidation stream `!forceOrder@arr` (USD-M futures, 1000ms update
speed) pushes, for each symbol, only the latest liquidation order within each 1000ms
window, as a snapshot. At most one liquidation per symbol per second reaches the file.

Every study on this data must state that. Counts and notional are lower bounds, and the
shortfall is largest inside a cascade, which is the case a liquidation study cares about.

Source: <https://developers.binance.com/docs/derivatives/usds-margined-futures/websocket-market-streams/All-Market-Liquidation-Order-Streams>
(confirmed 2026-10-09).

## Files

- One file per UTC day, `YYYY-MM-DD.jsonl`, gzipped to `YYYY-MM-DD.jsonl.gz` when the day
  rotates. Roughly 2.8 MB a day raw.
- One JSON object per line, `t` first, `ts` the local receive time in epoch ms:
  `msg` (the raw frame, parsed, under `msg`), `heartbeat` (every 60s while connected),
  `connect`, `reconnect`, `disconnect` (with `reason`) and `connect_error`.
- The recorder only ever appends. A torn last line after a crash is skipped by the reader.

## Gaps

A gap is a window in which coverage cannot be vouched for, never a stretch without
messages (a quiet market sends none): no heartbeat for more than 2 minutes, or a down
connection (a `disconnect` until the next `connect`/`reconnect`, or a restart with no
`disconnect` on record). The reader flags every order with `in_gap`; a study excludes or
reports them.

```python
from analytics.liquidations import load_orders, load_gaps, register_duckdb

orders = load_orders()  # one row per liquidation, with in_gap
clean = load_orders(exclude_gaps=True)  # gap rows dropped
gaps = load_gaps()  # start/end/minutes/cause
```

## Routing trap

Market streams are served under `/market`. The legacy `/ws/` and `/stream` paths still
accept the connection and push nothing (measured 2026-10-09: 0 frames against 88 on the
same stream in 120 seconds). `recorder_status` raises a separate alarm after 3 hours of
heartbeats with zero frames.
