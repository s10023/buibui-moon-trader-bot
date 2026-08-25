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
target + recent fires + live account), asks `claude -p` for a card-v5 trade
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

- **Check the circuit breaker FIRST — it is account-level, so one breach
  blocks the ENTIRE batch, not one symbol.** `card.py:265` vetoes on
  `state.account.daily_r <= cfg.daily_loss_limit_r`, which `state.py:282`
  computes account-wide as `daily_pnl_usd / (capital × r_base)` — nothing
  there is symbol-scoped, and `prompt.py:80` tells the model to answer
  NO_TRADE on it as well. The check is free and takes seconds; skipping it
  costs ~4.9 min of quota per foregone card.

  ```bash
  poetry run python -c "
  import argparse, time
  from cli.card import _account_provider_for
  from portfolio.sizing import SizingConfig, resolve_capital
  from card.config import CardConfig
  p, _ = _account_provider_for(argparse.Namespace(dry_run=False, as_of=None))
  sc, cfg = SizingConfig(), CardConfig()
  cap, _live = resolve_capital(sc, p.equity_usd() if p else None)
  now = int(time.time() * 1000)
  pnl = p.daily_pnl_usd(now - now % 86_400_000, now)
  print(f'daily_r {pnl / (cap * sc.r_base):.2f} vs limit {cfg.daily_loss_limit_r}')
  "
  ```

  Breached ⇒ every card in the batch returns NO_TRADE. Report it and ask
  before spending the batch; `daily_r` resets at 00:00 UTC.

  **⚠ A clean NO_TRADE batch does NOT verify the breaker — do not report it as
  if it did.** `card/card.py:233` gates the ENTIRE code-side veto block,
  including the breaker check at `:265`, behind `if card.verdict == "TRADE":`,
  while `card/prompt.py` separately instructs the model to answer NO_TRADE on a
  breach. So the model complies on its own and the deterministic leg never
  runs: measured 2026-08-11 on the first live firing, all three cards rendered
  a `gate:` line sourced from the model's own `no_trade_reason` while carrying
  **`veto_reasons: []`** in `ai-cards.jsonl`. The short-circuit is correct — a
  NO_TRADE needs no veto — but what you observed is the PROMPT working, not the
  code net. Exercising the code leg needs a card that returns TRADE while the
  breaker is tripped, which the prompt is designed to prevent. Same trap shape
  as the capital one in rubric item 3 below, and the same tell: an empty field
  that reads as either "nothing to report" or "nothing checked". **Measured
  2026-08-07: R is `capital × r_base` = USD 1,111 × 0.0025 = USD 2.78, so a
  −USD 12.96 day read −4.66R against a −2.0R limit** — at operator-scale equity
  a ~1% down day locks out the whole UTC day. That is the capital-resolution
  fix biting (pre-fix, R was USD 25 and a genuine −2R day scored −0.24R and
  sailed through), not a bug to route around. This pre-flight exists because
  a 3-symbol batch on 2026-08-07 spent a full card to discover it.
- **Then price the SUB-LOT WALL — it is PER SYMBOL, so unlike the breaker it
  forecloses individual cards rather than the batch.** The post-pass vetoes with
  `size floors to zero at qty_step <step>` when the risk budget cannot buy one
  lot, and the widest stop that still clears is
  `risk_budget / (qty_step × price)`. Wider-stop horizons hit it first, so a
  symbol can pass intraday and veto on swing at the same equity.

  ```bash
  poetry run python -c "
  import argparse
  from cli.card import _account_provider_for, _fetch_qty_step
  from portfolio.sizing import SizingConfig, resolve_capital
  import duckdb
  from analytics.store import DEFAULT_DB_PATH
  p, _ = _account_provider_for(argparse.Namespace(dry_run=False, as_of=None))
  sc = SizingConfig()
  cap, _live = resolve_capital(sc, p.equity_usd() if p else None)
  risk = cap * sc.r_base
  c = duckdb.connect(str(DEFAULT_DB_PATH), read_only=True)
  print(f'capital {cap:.2f}  1R {risk:.2f} USD')
  for sym in ('BTCUSDT', 'ETHUSDT', 'SOLUSDT'):   # the batch's symbols
      step = _fetch_qty_step(sym)
      px = c.execute(
          \"SELECT close FROM ohlcv WHERE symbol=? AND timeframe='1h' \"
          \"ORDER BY open_time DESC LIMIT 1\", [sym]).fetchone()[0]
      if step:
          print(f'{sym}: 1 lot = {step * px:.2f} USD -> max stop {risk / (step * px) * 100:.2f}%')
      else:
          print(f'{sym}: qty_step UNAVAILABLE (card degrades to an unrounded qty + warning)')
  "
  ```

  **Measured 2026-08-25 at USD 450.40 equity (1R = USD 1.13): BTCUSDT vetoes on
  any stop wider than 1.43%, against ETH 45.50% and SOL 114.08%** — the wall is
  set by one lot's NOTIONAL, so it bites hardest on the highest-priced symbol
  and is invisible on the cheap ones. It predicted that batch's BTCUSDT *swing*
  veto exactly (stop 1.58%) while BTCUSDT *intraday* passed at 0.55%.
  ⚠ **Report it, do not skip the card on it.** A sub-lot VETO still returns full
  reasoning and a four-angle steelman, and it is the one routine way the
  deterministic veto block is exercised at all — the breaker leg cannot be,
  because the prompt answers NO_TRADE first and short-circuits it. Skip only if
  the operator says so.
- Sync OHLCV if it is staler than the newest external snapshot
  (`ls -lt docs/plans/external-context/ | head`):
  `poetry run python buibui.py analytics sync --timeframes 1h 4h 1d` —
  External side-splits mislead on stale closes.
- External snapshots expire 48 h after capture. All stale → the card still
  runs (External block absent, one fewer confluence input); say so in the
  digest rather than blocking.
- `DRY=1` prints state + prompt with no LLM call and no ledger write — a
  free state sanity check, not a substitute for a real card.
- `TG=1` also pushes the rendered card to Telegram, every verdict included.
  Opt-in per run, so a multi-symbol batch does not send the operator one
  message per card unasked — add it to the cards worth interrupting for.

## Digest rubric (after all cards complete)

Per card:

1. **Verdict + hard rules** — a VETOED card must name the tripped rule;
   confirm the reason matches the state it was given.
2. **Cluster citations** — every liquidity cluster the card cites must exist
   in a verified snapshot (`docs/plans/external-context/*.json`, same symbol,
   fresh). Flag invented or mispriced clusters. card-v3 caps external
   liquidity at ONE confluence input — more than one is a rubric violation.
   ⚠ **You cannot verify that cap from any artifact on disk.** `FinalCard`
   emits a scalar `confluence_score` and no input list, so "how many inputs was
   external liquidity" is answerable only by reading the prose, where a cluster
   cited in two reasoning bullets is indistinguishable from one input used
   twice. Measured 2026-08-25: all 6 cards in a batch touched external liquidity
   in 2–3 bullets, which reads as either 6/6 breaching or a cap that means
   something narrower — and nothing recorded can tell those apart. **Report the
   ambiguity rather than filing 6 violations or clearing all 6.** The fix is an
   emitted `confluence_inputs: [...]`; until it lands this leg is advisory.
   **card-v4: a cluster is a BAND.** Its edges reproduce to only ~16% on a
   same-input re-extraction (mean drift 20–43% of band width, measured
   2026-08-12), so a card placing an entry/SL/TP exactly on a cluster edge is
   a rubric violation even when the number is real. Intensity IS reliable.
   **Judge a cluster's SIDE against the snapshot's own `spot_price_hint`,
   never `ref_close` or the live mark** — every cluster is positioned relative
   to the price at capture, so ordinary drift since looks exactly like an
   extraction sign-flip. False alarm 2026-08-07l: BTC short-liq clusters at
   64830–64980 read as inverted against `ref_close` 64996.1, and were correct
   against the hint of 64630 (price +0.57% in 3.8h).
2b. **Pundit citations** — from card-v4 the board carries **no per-author
   `avg_r`**; a card quoting one is reading a field that no longer exists, and
   a card quoting `avg_atr_r` as though it were R has confused ATR units for
   stop units. Several authors reading an identical −1.0 was the tell that
   killed the old field.
2c. **Steelman quality (card-v5)** — read all four angles. Code counts them
   (exactly four non-empty bullets on a TRADE, `_STEELMAN_ANGLES`); only you
   can see whether they ARGUE. Three defects to flag: an angle that restates
   the card's own thesis in the negative, "no case" on all four (the source's
   own warning — a padded steelman is worse than none, since it reads as a
   card that was challenged), and a steelman that lands hard while the verdict
   and reasoning log never answer it, which rubric step 5 requires. ⚠ **A
   steelman is NOT a reason to expect more NO_TRADEs** — the rubric's stated
   non-goal is that it exists so the other side never surprises you, not to
   talk the card out of the trade, so a v5 cohort skewing toward NO_TRADE is
   itself a defect to report rather than the feature working.
3. **Sizing** — the post-pass is deterministic (P1 sizing reuse); check
   entry/SL/TP/size are internally consistent in R terms. **LOT_SIZE rounding
   is now ENFORCED in code** — the post-pass floors the quantity to the
   symbol's exchange step and restates `risk_usd`/`risk_frac` from the
   *rounded* size, so the printed risk is the risk actually taken. An
   unrounded quantity should now appear only alongside the explicit warning
   "quantity is not LOT_SIZE-rounded (exchange filters unavailable)" — if you
   see a raw float **without** that warning, that is a regression, not the old
   defect. A risk budget below one lot is a VETO, not a silent zero. Capital is
   resolved via `portfolio.sizing.resolve_capital` — live account equity when
   available, else the configured `[portfolio] capital` — and every card records
   `capital_used`/`capital_source`; check the size line's percentage names a
   real capital figure (close to actual account equity), not `10,000.00`.

   **A NO_TRADE or VETOED card cannot answer this, and that is a trap.** There
   is no size line, and the row carries `capital_used: None` /
   `capital_source: None` — indistinguishable from a capital bug. So **never
   verify the capital path by running a card**: the verdict is the model's to
   choose, and roughly half the outcome space silently fails to answer. Verify
   it deterministically instead — free, no quota, no ledger row:

   ```bash
   poetry run python -c "
   import argparse
   from cli.card import _account_provider_for
   from portfolio.sizing import SizingConfig, resolve_capital
   p, reason = _account_provider_for(argparse.Namespace(dry_run=False, as_of=None))
   cap, live = resolve_capital(SizingConfig(), p.equity_usd() if p else None)
   print(cap, 'live_equity' if live else 'config', reason)
   "
   ```

   **`DRY=1` cannot do this either** — it returns `account: null` by design
   (`no provider (--dry-run)`), so it proves nothing about the live path.
   Real capital is smaller than the old constant (~USD 1,200 vs USD 10,000 measured),
   so the sub-lot veto is now common, not a corner case — expect BTCUSDT
   VETOes on stops wider than roughly 2.7% at that equity, not a bug.
4. **`valid_until_utc` is now a VETO when it does not postdate the card's own
   generation time** (or is unparseable). Checked against `generated_at_ms`,
   not wall-clock now, so re-reading an old card does not retroactively void
   it. Historical case: a SOL long generated 2026-08-04T14:13Z carried
   `valid until 13:20:00Z`, 53 minutes in the past.
5. **Style bar** — prose should read human (`/humanizer` bar): no inflated
   symbolism, rule-of-three padding, or negative parallelisms.

Cross-card:

1. **Consistency** — long AND short both TRADE on one symbol, or two cards
   reading the same cluster in opposite directions, is a flag.

Report to the operator: one line per card (symbol · direction · verdict ·
confluence inputs · flags), then the flags explained in prose. File real
rubric defects as card-v5 candidates in the digest. **The LOT_SIZE-rounding
and `valid_until_utc` defects are CLOSED** — both are enforced in the
deterministic post-pass, mutation-verified. Do not re-file them; do flag a
recurrence, which would now be a regression.

**A card verdict is one draw, not a measurement.** Measured 2026-08-05: two
baseline runs on a byte-identical `state_digest` returned **opposite
directions**. Never treat a single card, or a single-card difference between
two configs, as evidence about anything. → memory `[[card-reproducibility-verdict]]`

## Ledger

- Every non-DRY card appends to gitignored `docs/plans/ai-cards.jsonl` —
  `tail -n <batch size>` to collect a batch.
- TRADE cards dual-write a pundit-calls row (author `buibui_card`), scored
  by `make buibui-pundit-score`. **The scorer prints ONE `buibui_card` row
  pooling every cohort — it does NOT split by `prompt_version`.** The versions
  accrue in `ai-cards.jsonl` and nothing surfaces them, so a v3-vs-v4 read
  needs a hand join on `generated_at_ms` + `symbol`. Measured 2026-08-14:
  card-v3 is 8 resolved at 38% / −0.17R, **card-v4 is 2 resolved with 7 open**,
  so no version comparison is available yet. **card-v5 (2026-08-20) starts a
  THIRD pool rather than extending v4's** — it changed both the instruction and
  the emitted schema, so v4 rows do not carry a `steelman` at all and pooling
  the two answers no question about either.
- Both ledgers carry `horizon`, read from one stamp on the card — so a batch
  splits intraday vs swing straight from `tail`. **#619 (`f52ebae`) added the
  field and backfilled every historical row**, so nothing is unstamped. Of the
  43 rows as of 2026-08-14, **31 carry a horizon recovered from their
  pundit-call url and 12 carry a defaulted `intraday`** — all 12 defaulted rows
  are NO_TRADE, which never reach the pundit ledger and are never scored, so
  every scored row's horizon is genuine.
- **Swing has never resolved.** The window is 30d, so the first swing cards
  (2026-08-13) score around 2026-09-12. Any swing verdict before then is
  unmeasured, not neutral.
- Exploration runs that must not pollute the cohorts: call the CLI directly
  with `--no-ledger` (the make target has no such variable):
  `poetry run python buibui.py card SYM --no-ledger`.

## Quick reference

**`HORIZON=swing` changes how the card is SCORED.** The ledger row resolves against
`WINDOWS_MS[horizon]` — 48h vs 30d — so the wrong one books a good call wrong. It also
drops 1h from the fires scan and re-anchors the rubric to 4h/1d. ⚠ **External liquidity
is NOT horizon-filtered**: every fresh capture is a 24h heatmap or 1d liq map, so a swing
card is handed intraday liquidity and only *told* to discount it. Fix the capture set
before adding a filter — filtering today empties the block and reads as "no data".

| Want | Command |
| ---- | ------- |
| Single card (background) | `make buibui-card SYMBOL=BTCUSDT` |
| Directional | `make buibui-card SYMBOL=ETHUSDT DIRECTION=short` |
| Swing rather than intraday | `make buibui-card SYMBOL=BTCUSDT HORIZON=swing` |
| Reproducible INPUTS (never the card) | `... AS_OF=2026-07-16T02:00:00Z` |
| Free state smoke | `... DRY=1` |
| Push it to the phone | `... TG=1` |
| Cohort-safe exploration | `poetry run python buibui.py card SYM --no-ledger` |
| Timeout / reasoning knobs, no repo change | `make buibui-card SYMBOL=BTCUSDT CONFIG=/path/to/card.toml` |
| Collect batch rows | `tail -n <N> docs/plans/ai-cards.jsonl` |
| Score the AI cohorts | `make buibui-pundit-score` |

**`AS_OF` pins the INPUTS, not the verdict.** It composes state as of that
moment — admitting only bars that had **closed** by then, and omitting the live
account, which cannot be pinned because Binance serves only current positions
and equity (the state carries an `account:` health note saying so). The model
stays nondeterministic regardless: two runs on a byte-identical `state_digest`
have returned **opposite directions**, so a pinned anchor makes a comparison
*possible*, never conclusive at n=1. → memory `[[card-reproducibility-verdict]]`

Until 2026-08-05 `--as-of` pinned `now_ms` alone and its help text still claimed
"reproducible inputs": `recent_fires` was filtered by bar **open**, so a bar
still forming at the anchor was admitted the moment the daemon wrote its row —
look-ahead, not merely drift.

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
| Spending a batch with the breaker already breached | Run the pre-flight `daily_r` check — it is account-level, so it blocks every symbol |
