# Framework execution read: hummingbot, freqtrade and nautilus_trader against ours

Date: 2026-10-10. Owner Issue: #1011. Commits read: hummingbot `9af100d`, freqtrade `9a4794c`,
nautilus_trader `12eb790` (shallow clones, read only; nothing from them was executed).

## Why this read

The 2026-08-14 trading-canon audit ranked "read hummingbot's quoting logic against
`trade/routing.py`" among its first actions and named freqtrade and nautilus_trader as
read-not-depend repos. Nothing showed the reads happened. They matter now because ADR 0001 names
commission on all-taker execution as the manual book's cleanly measured leak, and the XS sleeve's
live path (#1013) is the only route to an autonomous book.

Each framework was compared file-by-file against `trade/` (routing, executor, overlay, exit
manager, adapter), `card/orders.py`, and the backtest fill path (`analytics/backtest/engine.py`,
`fills.py`, `analytics/exits/replay.py`). Every row below cites code on both sides; the two
defects were re-verified by hand.

## Verdict

Nothing here changes the canon audit's verdict: read these frameworks, do not depend on them, and
do not rebuild around one. Every behaviour worth having is a small or medium port into files we
already own, and on gap pricing, tie counting, slippage, operator-edit handling and the
exchange-side `closePosition` stop our code is already the stronger of the four. The read did find
two live defects in our own code (a clock offset that does nothing, and unquantised XS order
quantities) and one design gap that all three frameworks close and we do not: we never send a
client order id, so an order whose submit outcome is unknown can only be resolved by hand.

## Defects found in our code

1. **The Binance clock offset is never applied.** `utils/binance_client.py:25` sets
   `client.TIME_OFFSET`. python-binance 1.0.37 reads only `self.timestamp_offset`
   (`base_client.py:222, 408, 520`), so the sync is a no-op and the only protection against
   -1021 (timestamp outside `recvWindow`) is the library's default 10-second window.
   `tests/test_price_monitor.py:175,182` assert on the same dead attribute, which is why the
   suite stays green. Hummingbot keeps a median-of-5 offset, refreshed every poll and retried
   once on -1021 (`connector/time_synchronizer.py:57-70`).
2. **XS order quantities reach the wire unquantised.** `trade/routing.py:107` takes its delta
   from `round_down_to_step`, which returns `nearest * step` (`portfolio/sizing.py:430`) and can
   carry float noise (`0.8170000000000001`). `trade/binance_futures.py:333` sends it as-is.
   `card/orders.py:310-324` documents exactly this -1111 rejection and fixes it for cards only,
   and `trade/exit_manager.py:498-499` quantises its TP1. XS has never submitted live, so this is
   latent, but the first live rebalance would hit it.

## What the frameworks have that we lack

| Behaviour | Where it lives there | Ours | Size |
| --- | --- | --- | --- |
| Client order id generated before submit, recorded, and used to look up an UNKNOWN submit and to cancel by id | hummingbot `connector/utils.py:50-80`, `client_order_tracker.py:221-249`; nautilus `live/src/execution/manager.rs:1785-1860`; freqtrade `freqtradebot.py:570-615` | absent: `trade/binance_futures.py:323-349` sends none, so `exit_manager.py:425-436` and `card/orders.py:441-487` stand down for manual resolution, and XS cancels symbol-wide (`xsmom_executor.py:114-122`) | S-M |
| Bounded limit chaser: re-price a resting maker order when the touch drifts, then escalate to taker after T minutes | hummingbot `order_executor.py:138-159`, `position_executor.py:554-562` | one-shot GTX at the touch (`xsmom_executor.py:164-176`); reduce-only trims and closes go straight to MARKET (`routing.py:120-123`) | M |
| Clamp a post-only price to the touch instead of rejecting | hummingbot `position_executor.py:296-307` | TP1 rejected with -5022 is recorded `gtx_rejected` and never rests (`exit_manager.py:565-577`) | S |
| Reduce-only trading state on a drawdown halt (closes allowed, opens denied) | nautilus `risk/src/engine/mod.rs:1845-1890` | `trade/overlay.py:56-64` aborts the whole plan, closes included, so a latched halt freezes a full-gross book | S |
| Fine-bar re-walk inside the main backtester | freqtrade `--timeframe-detail`, `optimize/backtesting.py:1733-1747` | `replay_exits` re-walks tied bars (`analytics/exits/replay.py:171-185`); `run_backtest` only takes `tie_break` as `"adverse"` or `"target"` (`engine.py:802, 1115`), and every published number comes from it | M |
| A touch is not a fill for a resting limit (queue position) | nautilus `matching_engine/mod.rs:4941-4972`, `models/fill.rs:198-199` | TP fills on `h >= tp` (`engine.py:1096`) | S |
| Limit entries that can fail to fill and expire in backtest | freqtrade `backtesting.py:1330-1406` | every entry fills at the next open (`engine.py:976, 998`), while live entries are post-only limits | M |

## Where ours is better, or different on purpose

- **Gap pricing** (`analytics/backtest/fills.py:53-55`) is stricter than freqtrade's, and matches
  nautilus's.
- **Tie handling.** Adverse-first treats both directions alike and counts ambiguous bars.
  Nautilus's default O→H→L→C resolves longs target-first and shorts stop-first, and its adaptive
  ordering is one heuristic guess inside the adverse/target bracket. It is usable at most as a
  labelled third reading.
- **Operator edits.** The exit manager stands down when the operator moves, resizes or cancels an
  exit (`exit_manager.py:625-708`). Freqtrade and hummingbot both repair or re-place, which would
  fight a human.
- **The stop lives on the exchange.** `closePosition` STOP_MARKET on mark price covers partial
  fills. Hummingbot's Binance perpetual connector cannot place it, and its stop is a client-side
  barrier that vanishes if the process dies.
- **Intent rows before every submit**, plus `UnconfirmedOrderError` for a 2xx with no order id,
  are more durable than hummingbot's in-memory tracker, which marks such an order FAILED.
- **Slippage is modelled** in our backtest; freqtrade has none. Our size-aware XS cost model
  beats nautilus's one-tick slippage coin.
- **The stateless XS diff** removes the need for an order state machine or startup order
  reconciliation, because position truth is re-read every run.

## Not worth porting here

Weighted rate-limit throttling, hanging orders and order age (market-maker features), per-order
notional caps, latency models, and order-book depth checks. A daily rebalance and a 15-second poll
sit far below the exchange's limits, and the position-level caps in `overlay.py` are the right
invariant for a diff book.

## Open

- **Mark vs last price.** The live stop triggers on mark (`binance_futures.py:348`) while the
  backtest triggers on last-price wicks (`engine.py:1095`). None of the three frameworks solves
  this for bar data. The gap is unmeasured and rides the backtest-fidelity Issue below.
- **The user stream.** Hummingbot's `ORDER_TRADE_UPDATE` stream would cut the exit manager's
  15-second window between an entry fill and the stop. It is large, and unjustified until a
  journaled episode shows that window costing anything.

## Issues filed from this read

- Clock offset never applied: #1021
- XS quantities unquantised: #1022
- Client order ids, with lookup and cancel by id: #1023
- XS maker chaser with a taker fallback, and the TP1 clamp: #1024
- Backtest fill fidelity readings (fine bars in `run_backtest`, touch-is-not-a-fill, limit-entry
  expiry, mark vs last): #1025
- The reduce-only state on a drawdown halt is a decision inside #1013 and is posted there as a
  comment, not a new Issue.
