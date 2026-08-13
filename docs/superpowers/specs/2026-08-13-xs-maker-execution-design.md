# XS maker execution — design (2026-08-13)

**Goal + success metric, one line:** move the XS sleeve's order path from unconditional
taker fills to post-only maker fills on risk-increasing orders — **judged on order
mechanics, never on realised savings**, because the saving can only be measured live.

## Why this is worth doing

`trade/binance_futures.py:104` hardcodes `type="MARKET"`. Every order the XS sleeve will
ever send is a taker fill, by construction.

ST14's account audit (2026-08-06, full 4.2-year history, 154,648 fills) measured the same
defect on the operator's discretionary book:

- **Zero maker fills out of 3,390 sampled** — every entry and exit a market order.
- **Commission was 29,069 of a 70,633 net loss — 41%.**
- The same trades executed passively ≈ **17,400 saved, ~25% of the lifetime loss, with no
  change to what was traded.** ST14's own words: *the one unambiguous, no-strategy-decision
  fix.*

That evidence is about the discretionary book, not this sleeve — but the sleeve inherits
the identical defect from the same code path, and it inherits it **before** it trades, so
fixing it now costs nothing and compounds later.

`analytics/xsmom/execution.py:39` charges `fee_pct = 0.0005` (the taker rate) plus a 1–8
bps half-spread tier by ADV. So the sleeve's **+1.375 Sharpe is computed at taker cost**:
maker execution is upside the backtest does not currently claim.

## Scope

**In:** order type selection, limit price derivation, tick rounding, stale-order
cancellation, the exchange reads those need.

**Out:** any change to sizing, the target book, the overlay guards, or the backtest's cost
model. This changes *how* an order reaches the exchange, never *what* is ordered.

## Design

### 1. The maker/taker decision lives in the pure planner

`routing.py` is explicitly pure ("No I/O, no Binance imports — fully unit-testable"). It
keeps that property and gains one field:

```text
OrderIntent.order_type: "LIMIT" | "MARKET"
```

set from the `reduce_only` flag the planner already computes at `routing.py:114`
(`reduce_only = is_close or same_side_trim`). Risk-reducing orders take; risk-increasing
orders make. The policy is therefore unit-testable with no client.

**Price does not go in the plan.** It needs live book data, and a price pinned into a pure
planner is stale by the time it is submitted.

### 2. The exchange adapter grows two reads and one write

| addition | shape |
| --- | --- |
| `get_book_tops(symbols)` | `futures_orderbook_ticker()` — batch, returns bid/ask for all symbols; sits beside `get_marks` (`binance_futures.py:69`) and mirrors its shape |
| `price_tick` on `ExchangeFilters` | `get_filters` (`:47`) currently reads **LOT_SIZE and MIN_NOTIONAL only** — `PRICE_FILTER` / `tickSize` is absent from the repo entirely, because nothing has ever needed a price |
| `cancel_open_orders(symbol)` | new write; see §3 |

`submit_market(intent)` becomes `submit(intent, price)`, branching on `intent.order_type`.
LIMIT orders send `timeInForce="GTX"` (post-only: the exchange **rejects** rather than
crossing). `dry_run` keeps returning its dict without touching the network.

### 3. Limit price — join the touch

BUY rests at **best bid**, SELL at **best ask**. Standard maker placement and the highest
fill probability available to a passive order. A passive offset (bid − N ticks) was
rejected: under fire-and-forget an unfilled order costs a day of tracking error against
the target book, which is worth more than the couple of bps the offset buys.

**⚠ Tick rounding is side-dependent, and getting it backwards is fail-loud-but-total.**
A BUY price must round **down** to the tick and a SELL **up**. Round the wrong way and the
order crosses the spread, and GTX rejects it outright — no fill, no position, no partial.

The helper must reuse the snap-before-floor treatment `portfolio/sizing.py::round_down_to_step`
already documents as load-bearing. The naive `floor(p / tick) * tick` is float-fragile in
exactly this input class: `0.29 / 0.01` computes as `28.999999999999996`. A $3,000 ETH
price against a 0.01 tick is that class.

### 4. Unfilled orders — fire and forget, with a hard precondition

An order that does not fill is left alone. The next daily rebalance re-plans from whatever
position actually exists, and `build_order_plan` already derives every delta from
`current_positions`, so an unfilled leg self-corrects on the next run.

**That is only true if stale orders are cancelled first.** Cancel all open orders at the
**start of every run, before positions are read and before planning**. A resting order is not a
position: yesterday's unfilled BUY sitting on the book while today's plan submits another
one **overshoots the target silently**, because both orders are individually correct and
neither is visible to `current_positions`.

**Which symbols, and in what order — both are load-bearing:**

- **Symbol set = the union of target-book symbols and current-position symbols**, i.e. the
  same set `build_order_plan` computes at `routing.py:62`. Cancelling only *today's* book
  symbols would leave a resting order on a symbol that dropped out of the book yesterday —
  the exact case with no position to reveal it.
- **Cancel before positions are read.** A partially-filled resting order is still moving
  the position while it sits there; cancelling first makes the subsequent read settled
  rather than a moving target.

This precondition is the whole cost of choosing fire-and-forget. It is not an optimisation
and must not be dropped as one.

### 5. What deliberately does not change

**The backtest keeps `fee_pct = 0.0005`.** Maker execution makes realised cost *lower* than
modelled, so the sleeve's gate verdict stays a floor rather than becoming a claim that
depends on fills landing passively. Re-tuning it to the 2 bps maker rate would inflate the
backtest on the strength of a fill rate nobody has measured — and the sleeve's DSR 0.997 is
the last number in this repo worth making fragile.

**The halt is unaffected.** `overlay.py:59` *aborts the run* on a drawdown halt rather than
flattening, so post-only can never strand a halt-driven liquidation — the halt never
submits anything.

## Failure modes

| mode | handling |
| --- | --- |
| GTX rejection (price crossed) | log with symbol/side/price/tick and skip the leg; the next run re-plans. A rejection *rate* above noise means the rounding is wrong-sided — see the reversal trigger below |
| Partial fill | no special handling; the residue is just a smaller delta tomorrow. This is the property fire-and-forget buys |
| Book top missing / zero | skip the symbol, same as the existing `skip:no_mark` path (`routing.py:74`) |
| `cancel_open_orders` fails | **abort the run before planning.** Planning against un-cancelled orders is the overshoot in §4 |

## Testing

- **Tick rounding is enumerated, never spot-checked** — every (price, tick, side) class,
  asserting BUY never rounds up and SELL never rounds down. CLAUDE.md's record here is
  explicit: the pre-fix spot-checks on `round_down_to_step` (`0.0571951498512928`, `12.5`)
  all passed while the defect stood. Enumeration is what found it.
- Order-type selection is tested in `routing.py`'s existing pure-unit style, with no client.
- Adapter reads/writes are tested against a `MagicMock` client, per the repo convention.

**⚠ Testnet cannot validate the economics.** Testnet fills are synthetic — thin book,
non-representative queue position — so a maker fill rate observed there measures the
testnet, not the strategy. Testnet validates **mechanics only**: orders accepted rather
than GTX-rejected, correct side rounding, stale orders actually cancelled, no overshoot.
Same constraint the testnet rehearsal plan already carries for P&L.

## Decision log

Recorded per the standing rule: a spec records what was decided, almost never what was
*considered* — and when the build misbehaves, the fix is usually a rejected alternative.

| # | decision | alternatives rejected | why | what would REVERSE it |
| --- | --- | --- | --- | --- |
| 1 | Treat as **architectural**, not a bounded flag | bounded — "just change the order type" | post-only adds a state the system has never had: an order that does not fill. That changes the contract `routing.py`'s delta convergence depends on | nothing — this one is spent. Recorded so a future reader knows the bounded reading was considered |
| 2 | **Fire and forget** on unfilled orders | (a) post, wait N min, cancel remainder, cross with MARKET; (b) post-only hard, never cross | the sleeve rebalances daily and has no urgency — the property that makes maker viable at all. `build_order_plan` re-derives from live positions, so an unfilled leg self-corrects | **tracking error.** If realised gross exposure drifts persistently below target — or a symbol goes >2 consecutive runs unfilled — switch to (a). The executor logs submitted-vs-filled per run so this is measurable, not a hunch |
| 3 | **MARKET on `reduce_only`**, maker on opens/rebalances | (a) maker on everything; (b) maker on everything except full closes, trims stay maker | asymmetry: an unfilled *open* is bounded opportunity cost; an unfilled *close* is open directional risk the book has already decided against | if closes turn out to be a large share of order volume, their forfeited saving may outweigh the risk — revisit at (b). Measure: reduce-only share of total order notional per run. Below ~15% this decision is not worth re-opening |
| 4 | **Join the touch** (BUY at bid, SELL at ask) | (a) price off the mark; (b) touch minus a passive offset | (a) sits between bid and ask so it frequently crosses and gets GTX-rejected — cheapest to build, worst behaviour. (b) trades a couple of bps for materially lower fill rate, which decision 2 makes expensive | **maker fill rate measured LIVE** (not testnet). Persistently high fill rate ⇒ try (b) to capture more spread. Persistently low ⇒ decision 2 is the thing to change, not this |
| 5 | Backtest cost model **unchanged** at 5 bps taker | re-tune `fee_pct` to the 2 bps maker rate | keeps the gate verdict a floor. Inflating a backtest on an unmeasured fill rate is the failure mode this repo has filed repeatedly | a live maker fill rate stable over a meaningful sample. Even then, prefer leaving the model conservative and taking the saving as realised upside |

| 6 | **Two position reads per run**: discover open-order symbols → cancel → *then* read the positions used for planning | (a) one read, cancel after; (b) cancel every open order in the account without intersecting the managed set | §4 requires cancelling before the planning read, but the managed symbol set (`symbols ∪ positions`) needs a position read to compute — a genuine ordering cycle. Breaking it with a cheap throwaway read is the smallest fix. (b) was rejected as too wide a blast radius: it would cancel an operator's hand-placed order on an unmanaged symbol, the same hazard that already forces a dedicated sub-account | if the extra read ever costs latency that matters, or if the sub-account becomes strictly enforced so no unmanaged orders can exist, collapse to (b) and drop a read |

**Correction folded in while speccing, not a decision:** the testnet rehearsal plan's
step 3 said "enable **HEDGE** mode". That is inverted — `binance_futures.py:80` *raises*
on `dualSidePosition: True` ("XS executor requires one-way mode"), and it raises in
`ensure_account_config`, **before** any order is submitted, so the plan's criterion 1 was
unreachable as written. Fixed in the plan and the handoff on 2026-08-13.

## What this does not answer

- The live maker fill rate. Unknowable before live capital; it is the input decisions 2 and
  4 both hinge on, which is why both name it as their reversal trigger.
- Whether the XS sleeve's own rebalances are large enough to move the touch. At ~$1,130
  equity they are not, but this becomes real if capital grows.
