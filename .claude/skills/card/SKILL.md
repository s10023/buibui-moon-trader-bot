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
target + recent fires + live account), asks `claude -p` for a card-v3 trade
card, then a deterministic post-pass sizes the trade and enforces hard rules
in code. Advisory-only — it routes no orders. This skill wraps it: run one
card or a batch, collect the results, and digest them.

Invocation grammar: `/card SYM[,SYM...] [long|short|both]`. Omitted
direction = the card chooses; `both` = two runs per symbol.

## Execution rules (the part that breaks)

Measured 2026-08-04 across six real cards: **271 / 349 / 299 / 291 s**, mean
**4.9 min** — so six chained cards ≈ **30 min**, far past the 600 s Bash
ceiling. State composition is only **3.4 s** of that (re-measured, still
accurate); the rest is the `claude -p` call. It is output-token volume, not
hidden overhead: **23,116 output tokens to produce a 2,365-character card**, so
~97% of what is generated never reaches the card.

**Those numbers replace a stale "60–90 s+" from 2026-07-16, and the staleness
caused a real outage.** `card/config.py` had `timeout_s = 180.0` set from that
figure — below the *fastest* real card — so both attempts timed out
(`LLMClient.generate` retries once) and every card in a batch returned nothing
while burning ~6 min. Nothing failed loudly; the operator saw empty output. The
default is now **480 s**, guarded by a test that asserts headroom over the
measured 349 s worst case.

If a batch ever times out again, override per-run with no repo change —
`timeout_s` is settable from a `[card]` TOML block and `--config` takes any
path. This is what unblocked the 2026-08-04 batch:

```bash
make buibui-card SYMBOL=BTCUSDT DIRECTION=short CONFIG=/path/to/card.toml
```

### The 8× lever, measured — opt-in, NOT the default

Measured 2026-08-05 on one real BTCUSDT short prompt. **The intuitive fix does
nothing and the real one is elsewhere**, so do not re-derive this:

| Config | wall | output tokens | turns |
| ------ | ---- | ------------- | ----- |
| baseline | 245.6 s | 21,705 | 4 |
| tools off + MCP stripped | 254.6 s | 23,645 | 1 |
| **+ `max_thinking_tokens = 0`** | **30.7 s** | **2,040** | 2 |

Restricting the toolset — the obvious lever — bought **zero** latency; it is
worth having for determinism (4 turns → 1) and input cost (~200K → ~25.7K
tokens), not speed. Extended thinking was the whole five minutes.

Both are opt-in via a `[card]` block, and both default OFF:

```toml
[card]
max_thinking_tokens = 0
restrict_tools = true
```

**Why they are not the default:** the fast card was good on that sample — 8
substantive reasons, and 8/8 spot-checked citations exact against the input
state, nothing invented — but it is **n = 1**, and its verdict differed from
baseline (NO_TRADE vs TRADE) in a way one sample cannot separate from ordinary
model variance. Validate across a real batch before anyone flips the default.

Enable them **together**: with tools reachable the model may call one and then
narrate the fact, and a preamble ahead of the JSON fence used to destroy the
whole card. `_strip_fences` now tolerates a preamble, but not needing it is
better.

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
   fresh). Flag invented or mispriced clusters. card-v3 caps external
   liquidity at ONE confluence input — more than one is a rubric violation.
3. **Sizing** — the post-pass is deterministic (P1 sizing reuse); check
   entry/SL/TP/size are internally consistent in R terms. **Quantity must be
   LOT_SIZE-rounded.** Raw floats (`0.03898939488459157` BTC,
   `23.148148148148184` SOL — both real, 2026-08-04) are not orderable, and the
   stated `risk $25.00` is only true *before* rounding, so the card understates
   its own risk. `trade/routing.py` already implements this for the XS executor
   and the card's post-pass does not reuse it — flag every unrounded quantity.
4. **`valid_until_utc` must be in the future.** The field is model-emitted and
   unchecked, so a card can arrive already expired: the SOL long generated
   2026-08-04T14:13Z carried `valid until 2026-08-04T13:20:00Z`, **53 minutes
   in the past**. That one was NO TRADE so nothing was lost, but a TRADE card
   can carry it. Compare against the card's own generation time, not "now".
5. **Style bar** — prose should read human (`/humanizer` bar): no inflated
   symbolism, rule-of-three padding, or negative parallelisms.

Cross-card:

1. **Consistency** — long AND short both TRADE on one symbol, or two cards
   reading the same cluster in opposite directions, is a flag.

Report to the operator: one line per card (symbol · direction · verdict ·
confluence inputs · flags), then the flags explained in prose. File real
rubric defects as card-v4 candidates in the digest — the LOT_SIZE rounding and
`valid_until_utc` defects above are the two currently open.

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
| Timeout / reasoning knobs, no repo change | `make buibui-card SYMBOL=BTCUSDT CONFIG=/path/to/card.toml` |
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
| Budgeting ~1 min per card | Mean is 4.9 min; six ≈ 30 min. Plan the batch around it |
| Reading an empty card as "no setup" | An empty result is a TIMEOUT, not a verdict — check `timeout_s` |
