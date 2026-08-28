# ST37 — post-card GTX limit-order helper (design)

**Date:** 2026-08-28 · **Status:** APPROVED, unimplemented · **SoT:** ST37, priced by ST111
**Owner surface:** `card/` + `trade/binance_futures.py` + `cli/`

## Goal and success metric

After a card batch, offer the operator a picklist of the batch's unexpired TRADE cards and
place the chosen ones as **post-only (`GTX`) limit entry orders**, instrumented well enough
that every fill joins back to its card.

Success is measured by the LEDGER, not by P&L: (1) every placed order produces a placement
row and a terminal row in `docs/plans/card-orders.jsonl`; (2) the first maker-fill-rate
measurement on card-sourced entries exists against a baseline of literally zero (ST111:
1 maker fill in 360; lifetime 1 in 3,390; 41% of lifetime loss is commission). ⛔ **No
validation study is budgeted** — the fee saving is deterministic arithmetic
(`2·fee_pct·entry/risk`, ~0.030R at a 2% stop) and sits BELOW the live ledger's +0.0359R
detection floor, and the fill half is confounded because a non-fill skips a loser. The
instrumentation is the deliverable; an expected P&L improvement is not.

## Constraints that shaped the design

- **The account is HEDGE-MODE for its entire retained history** (ST109, post-dates the SoT
  row). `BinanceFuturesAdapter.ensure_account_config` RAISES on hedge, and `submit()` sends
  no `positionSide`, which a dual-side account rejects. The helper must be hedge-aware and
  must never call `ensure_account_config`.
- **The XS collision is latent** (ST37's original blocker): `cancel_open_orders` calls
  `futures_cancel_all_open_orders(symbol=…)` — symbol-WIDE, not scoped to the bot's own
  orders — and `config/universe.toml` leads with BTCUSDT/ETHUSDT/SOLUSDT, exactly the
  carded set. Inert today (XS is dry-run, halt latched); eats card orders the day XS goes
  live. The guard is free now and is the whole point later.
- **A card verdict is ONE DRAW** — identical `state_digest` has returned opposite
  directions. A picklist makes acting on one draw a keystroke cheaper, so the UI must say
  this where the operator reads it, not only in docs.
- **Advisory-only is a documented invariant of the card surface** — this feature changes it
  deliberately, so `/card` SKILL.md and CLAUDE.md's card section flip in the same branch.

## Decisions (operator-approved 2026-08-28)

1. **Entry-only.** No stop, no TP, no fill watcher. The operator places the stop on
   Binance's fill notification, as in the current manual flow. The resting-TP1-partial
   belongs to the ST104–ST108 exit spec; a stop placed beside an unfilled entry re-opens
   the EXPIRED-on-flat ambiguity ST106 documented.
2. **CLI-interactive picklist.** Zero-token, testable with mocked stdin; the `/card` skill
   points at it after a batch rather than re-rendering risk arithmetic in prose.

## Design

### CLI surface

Two new **sibling** commands — `buibui card` keeps its positional `SYMBOL`, so nesting
`place` under it would parse as a symbol; the existing invocation stays byte-identical:

- `buibui card-place` — scan → picklist → guards → GTX submit → ledger append.
- `buibui card-orders [--refresh]` — list placements; `--refresh` polls terminal states
  and appends terminal rows.

Make wrappers `buibui-card-place` / `buibui-card-orders`, following `buibui-card`'s shape.
Logic lives in a new `card/orders.py`; exchange calls reuse `BinanceFuturesAdapter` with a
`MagicMock`-able client. No Telegram in v1.

### Candidate scan

Tail of `docs/plans/ai-cards.jsonl`: `verdict == "TRADE"`, unexpired
(`valid_until_utc` vs wall clock), anti-joined on `(symbol, generated_at_ms)` against
`card-orders.jsonl` so an already-placed card never re-presents.

### Picklist

Numbered table — symbol, direction, entry, SL, quantity, `risk_usd`/`risk_frac`, expiry
countdown — with the one-draw hazard line in the header. Multi-select by number;
**empty input = none; nothing is ever pre-selected.** After selection, echo the
**aggregate**: total risk in USD and % of LIVE equity (`resolve_capital`), plus
per-symbol×side stacking — the 08-17e batch read as six 0.25% choices but was three bets
doubled (~$2,045 notional on $1,051 equity), and a flat list hides that. Then an explicit
`y/N` confirm.

### Placement guards — in code, veto-style, each naming its numbers

| # | Guard | Rule |
| --- | --- | --- |
| a | Expiry | Re-check `valid_until_utc` at the PLACEMENT instant. The existing card veto compares only against `generated_at_ms`; a card can expire while the operator thinks. |
| b | XS collision | REFUSE when symbol ∈ `config/universe.toml` `symbols` AND `docs/plans/xsmom_targets/execution_state_live.json` exists (the executor writes `execution_state_{mode}.json`, so a live-mode run leaves this marker; testnet orders live on a different venue and cannot collide). Always WARN on any managed-set symbol so the hazard is heard before XS go-live. |
| c | Position mode | Probe `futures_get_position_mode()` once per run. Hedge: send `positionSide` (LONG/SHORT from card direction), omit `reduceOnly`. One-way: today's parameter shape. Never call `ensure_account_config`. |
| d | Exchange rounding | Re-round quantity via `round_down_to_step` against LIVE filters and price via `round_to_tick`; restate `risk_usd`/`risk_frac` from the rounded values when they move (the card's own sizing rule, applied at placement time). |
| e | GTX rejection | Caught, never raised: report "price already through the level — the setup is stale, not the order broken", record terminal reason `gtx_rejected`. A rejection is INFORMATION. |

### Adapter delta

`OrderIntent` gains `position_side: str | None = None`; `submit()` sends `positionSide`
and omits `reduceOnly` when it is set. The XS executor passes nothing and its behaviour is
byte-identical (pinned by test).

### Instrumentation ledger — `docs/plans/card-orders.jsonl`

Gitignored, single-copy; the backup's `LEDGERS` glob (`docs/plans/*`) covers it from day
one with no script edit. Append-only, two row kinds joined by `order_id`:

- **Placement row:** card key (`symbol`, `generated_at_ms`, `state_digest`), `order_id`,
  placement ts, limit price, mark AND best bid/ask at placement, position at placement,
  equity, position mode, the guard warnings that fired.
- **Terminal row** (written by `card-orders --refresh`): status, terminal ts, `avgPrice`,
  mark at terminal, reason ∈ `filled | cancelled | expired | gtx_rejected`. Age-at-terminal
  and price-drift-at-cancel are derivable, which is the whole of the `max_order_age` /
  `hanging_orders_cancel_pct` / `order_refresh_tolerance` claims.

This is ST33's field list verbatim: six of the twelve hummingbot-repo claims are blocked
only because no resting order has ever been placed, and this ledger is what decides
whether they reopen.

### Docs flip (same branch)

`/card` SKILL.md drops "treating a TRADE card as an order" from common mistakes and names
`card-place` as the one deliberate exception, one-draw hazard stated; CLAUDE.md's card
section gets the same two lines. `README.md` gains the two commands.

### Testing

- Unit: MagicMock client throughout; mocked stdin for the picklist; no network, no real DB.
- Mutation-style guard cases: (b) fires ONLY on managed ∩ live-marker (four quadrants);
  (c) parameter shape asserted in both account modes; (a) a card expiring between scan and
  confirm is vetoed; (e) the GTX-reject path records and does not raise.
- Ledger row schemas pinned; XS executor `submit` calls asserted byte-identical.
- No backtest surface is touched → `make test-regression` branch: outside the filter set.

## Decision Log

| Decision | Reversed by (observable) |
| --- | --- |
| Entry-only, no stop | A fill sits unprotected because the operator missed the notification — one occurrence logged in the journal upgrades v2 to stop-on-place or a watcher. |
| CLI-interactive picklist | The order ledger shows placements happen only inside sessions anyway — add the `--select` flag for skill-driven use then. |
| Build premise (fee lever) | Placed GTX orders measure ~0 fill rate over ≥20 placements — the lever fails on the fill half, and the non-fill-skips-a-loser confound means this needs reading against which setups went unfilled, not raw P&L. |
| XS guard keyed on the live-state marker | XS go-live lands on a dedicated sub-account (the standing hard blocker), making the collision unreachable — the guard then relaxes to the WARN leg only. |
