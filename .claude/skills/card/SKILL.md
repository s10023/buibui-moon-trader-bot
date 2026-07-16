---
name: card
description: >
  Run one or more AI trade cards (`make buibui-card`) and digest the results
  back to the operator as a second opinion.
  Invoke when the user says "/card", "run a card", "cards for BTC/ETH/SOL",
  names symbols × directions to card, or asks for a card batch reviewed —
  especially any request for MORE THAN ONE card in a session.
allowed-tools: Bash, Read
---

# Card — Trade-Card Runner + Digest

`buibui card SYMBOL` composes a MarketState (brief panel + pundit board + XS
target + recent fires + live account), asks `claude -p` for a card-v2 trade
card, then a deterministic post-pass sizes the trade and enforces hard rules
in code. Advisory-only — it routes no orders. This skill wraps it: run one
card or a batch, collect the results, and digest them.

Invocation grammar: `/card SYM[,SYM...] [long|short|both]`. Omitted
direction = the card chooses; `both` = two runs per symbol.

## Execution rules (the part that breaks)

Measured 2026-07-16: state composition is 3.6 s wall — per-card latency is
~all in the `claude -p` call (60–90 s+). Six chained cards ≈ 6–12 min, past
the 600 s Bash ceiling.

1. **One card per Bash call, `run_in_background: true`.** Never `&&`-chain
   card runs in one foreground call — that is the recorded timeout failure.
2. **Sequential, not parallel.** Wait for each background task to complete
   before launching the next (`claude -p` shares the subscription rate
   limit; the harness notifies you when a task exits).
3. Collect each task's stdout (the rendered card) for the digest.

## Pre-flight (once, before the first card)

- Sync OHLCV if it is staler than the newest external snapshot
  (`ls -lt docs/plans/external-context/ | head`):
  `poetry run python buibui.py analytics sync --timeframes 1h 4h 1d` —
  External side-splits mislead on stale closes.
- External snapshots expire 48 h after capture. All stale → the card still
  runs (External block absent, one fewer confluence input); say so in the
  digest rather than blocking.
- `DRY=1` prints state + prompt with no LLM call and no ledger write — a
  free state sanity check, not a substitute for a real card.

## Digest rubric (after all cards complete)

Per card:

1. **Verdict + hard rules** — a VETOED card must name the tripped rule;
   confirm the reason matches the state it was given.
2. **Cluster citations** — every liquidity cluster the card cites must exist
   in a verified snapshot (`docs/plans/external-context/*.json`, same symbol,
   fresh). Flag invented or mispriced clusters. card-v2 caps external
   liquidity at ONE confluence input — more than one is a rubric violation.
3. **Sizing** — the post-pass is deterministic (P1 sizing reuse); check
   entry/SL/TP/size are internally consistent in R terms.
4. **Style bar** — prose should read human (`/humanizer` bar): no inflated
   symbolism, rule-of-three padding, or negative parallelisms.

Cross-card:

1. **Consistency** — long AND short both TRADE on one symbol, or two cards
   reading the same cluster in opposite directions, is a flag.

Report to the operator: one line per card (symbol · direction · verdict ·
confluence inputs · flags), then the flags explained in prose. File real
rubric defects as card-v3 candidates in the digest.

## Ledger

- Every non-DRY card appends to gitignored `docs/plans/ai-cards.jsonl` —
  `tail -n <batch size>` to collect a batch.
- TRADE cards dual-write a pundit-calls row (author `buibui_card`), scored
  by `make buibui-pundit-score`; card-v1 vs card-v2 cohorts accrue
  automatically.
- Exploration runs that must not pollute the cohorts: call the CLI directly
  with `--no-ledger` (the make target has no such variable):
  `poetry run python buibui.py card SYM --no-ledger`.

## Quick reference

| Want | Command |
| ---- | ------- |
| Single card (background) | `make buibui-card SYMBOL=BTCUSDT` |
| Directional | `make buibui-card SYMBOL=ETHUSDT DIRECTION=short` |
| Reproducible inputs | `... AS_OF=2026-07-16T02:00:00Z` |
| Free state smoke | `... DRY=1` |
| Cohort-safe exploration | `poetry run python buibui.py card SYM --no-ledger` |
| Collect batch rows | `tail -n <N> docs/plans/ai-cards.jsonl` |
| Score the AI cohorts | `make buibui-pundit-score` |

## Common mistakes

| Mistake | Fix |
| ------- | --- |
| `&&`-chaining cards in one Bash call | One `run_in_background` call per card |
| Verifying citations from memory | Read the snapshot JSONs on disk |
| Batch on stale closes | `analytics sync` first (pre-flight) |
| Using `DRY=1` as the real smoke | DRY skips the LLM — verdict/prose untested |
| Treating a TRADE card as an order | Advisory only; the operator trades manually |
