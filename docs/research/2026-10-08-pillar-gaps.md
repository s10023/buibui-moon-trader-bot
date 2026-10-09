# Pillar gaps: exits, costs, signal, sizing, for the manual book and the bot book

Ticket: GitHub Issue #916 (map #864). Measured 2026-10-08. R and percentages only; no account figures. Scripts and raw output are local-only (gitignored) in `docs/plans/scratch/wayfinder-864/research/pillar/`: `pillar_manual.py` (journal, 15m OHLCV), `pillar_bot.py` (ledger), `lifetime_commission.py` (income export, shares only); each has a `.out` with the printed numbers.

Follow-on: #981 (exit manager v1).

## Answer

**Manual book: exits are NOT confirmed as the largest gap; the two available frames disagree.** On the journal, in-hold giveback (0.23 to 0.26R a trade, 95% CI 0.14 to 0.36) is about four times the fee drag (0.052R), but that sample is 28 legs (11 baskets) with 27 wins and a net +0.35R a trade: a winning, selected sample that measures leakage, not a loss source, and the lifetime record shows a net loss the journal does not. A resting TP1 would have added 0.15R a trade, 82% of it from two trades; without them the exit lever is about 0.03R. On the lifetime income record (all trades, USD shares, no R), commission is the largest named leak at 41.2% of the net loss, rising to 62.5% in 2025 and 52.4% in 2026 to date; exits cannot be separated there. So the ruling's trigger ("exits confirmed as the largest gap") is contested, not met. **Bot book: there is no detectable gross edge, and costs turn that into a loss.** The "-0.17R" is today -0.148R (n=9,309): modelled execution costs 0.080R (54%) plus a gross edge of -0.068R (46%) whose day-clustered CI [-0.148, +0.018] spans zero. No cost fix makes the book positive: all-maker execution saves 0.034R. Bot exits cannot be a share of the loss: they sit inside the gross edge, and the 0.40R a trade given back on trades that reach +1R is a hindsight bound the filed time-stop sweep says cannot be banked. Sizing contributes no expectation loss in either book; streak exposure is a tail (bot: 72 to 162 losing alerts in a row; manual: no streak to measure).

## Table (R per trade; share = share of the identified gap or net loss; n per cell)

Manual window: 28 canonical-basis journal entries, entries 2026-07-26 to 2026-09-08 (n=28 legs, 11 baskets). Bot window: 9,309 resolved alerts, resolved 2026-03-25 to 2026-10-07 (192 resolution days), restated to the net basis.

| Pillar | Manual book | Bot book |
| --- | --- | --- |
| Exits (giveback) | In-hold giveback +0.229R (inner bars) to +0.259R (outer bars), CI [0.14, 0.36], n=28; 81-83% of exits+costs. Rested-TP1 credit +0.148R over all 28 (+0.378R over the 11 with a declared TP1, CI [0.04, 0.81]). Hindsight 24h post-exit peak minus taken: +0.83R, CI [0.53, 1.23] | Not a loss share (inside gross). Hindsight bound 0.403R a trade from the 3,321 rows (35.7%) that reached +1R; total giveback 1.046R is mostly stop-outs and must not be quoted (ST17) |
| Execution costs | Fee 0.052R (CI 0.042-0.070), n=28; 13.1% of gross edge, 8.3% of in-hold MFE. Taker share: fee mass is 97.5% of the all-taker model; all-maker would save at most 0.031R | Modelled 0.080R (fee 0.057 + slippage 0.023), 54.1% of the -0.1475R net, n=9,309. 100% taker by construction. All-maker fee would save 0.034R |
| Signal (gross edge) | +0.398R gross, trade CI [0.23, 0.57], basket-clustered CI [0.15, 0.68]. Positive, so no signal loss; the one loss contributes -0.036R a trade | -0.068R gross, day-clustered CI [-0.148, +0.018]; 46.0% of the net loss, indistinguishable from zero |
| Sizing (streak) | Not measurable: 1 loss in 28, longest run 1, deepest drawdown 1.0R | Longest losing run 162 alerts (-167R) pre-parity, 72 (-71R) post-parity; 17 losing days in a row (-300R in alert-R); max drawdown 1,746R, which is 0.19R per alert and larger than the whole cumulative loss. A tail, not an expectation: constant risk per alert changes none of the means above |
| Net | +0.346R (CI [0.16, 0.53]) | -0.1475R (CI [-0.228, -0.062]) |

Also, the lifetime income export (a different frame: USD shares, no R, ends 2026-08-04): commission 41.2% of the net loss, gross trading 58.1%. Exits, signal and sizing cannot be separated there.

## Method notes

- **Identity (both books, exact, no residual).** net = MFE - giveback - cost, where giveback = MFE - gross and gross = net + modelled drag. Manual: 0.627 - 0.229 - 0.052 = 0.346. Bot: 0.979 - 1.046 - 0.080 = -0.148 (identity error 0 in `pillar_bot.out`). The three terms are disjoint by construction, but gross edge and exits **overlap**: gross = MFE - giveback, so "signal" in the table is the net edge after the exit rule as built, and the bot's exit rule cannot be separated from its signal. For the bot, MFE/giveback is a hindsight bound, so exits are reported outside the sum.
- **Manual R.** From `docs/plans/journal/*.md` frontmatter `r_realized` on the "canonical" basis (initial stop; from 2026-07-26 on). The nine May-June entries use mixed soft/hard stop bases and no fee data, so they are excluded from the pillar cells (their mean is +0.78R, 7 of 9 wins, not comparable). Three 2026-10-08 entries are open and excluded. 09-08 entries are net in the frontmatter; gross is read from their comment. Stop distance in price is derived as |exit - entry| / |gross R|.
- **Manual fees in R.** fee$ / risk$, with risk$ = gross$ / |gross R|, read from the journals' own gross/fees lines and printed as R only. Funding is ignored. The 07-28 first attempts (scratch trades inside the same files) are not separate entries and are not in n.
- **Manual giveback.** MFE from 15m bars: "inner" = bars wholly inside [entry, exit] (understates), "outer" = bars overlapping it (overstates); both floored at the realised R. No data below 15m exists locally. Post-exit peak uses the 24h after exit. The rested-TP1 counterfactual credits TP1 R minus taken R only if a later 15m bar touches TP1 before the initial stop (same-bar ties count as stop first); it is a full-TP1 credit, so a half partial earns about half.
- **Bot net and gross.** `analytics.giveback.load_giveback_rows` (restates pre-e5d92bb rows via `portfolio.replay.restate_on_resolution_clock`, split on `outcome_filled_at_ms`), drag from `portfolio.sizing.round_trip_drag_r` with `fee_pct=0.0005` and `slippage_bps=2.0` from `config/strategy_params.toml`. Funding is modelled inside the native net half and not separable, so it sits in "gross". CIs are bootstrap over UTC resolution days (192 clusters) because most alerts share a timestamp.
- **Not run:** the `replay_ledger` portfolio replay itself (it adds sizing caps; the per-row helper it calls was used instead).

## Filed numbers re-measured

| Filed | Re-measured | Verdict |
| --- | --- | --- |
| 14.7x give-back "against the operator's own TP1" | 14.64x, reproduced (26 Aug long basket, mean peak R 1.58 vs mean taken R 0.108, peak to 27 Aug 07:45 MYT) | Number holds, label is wrong: it is against a **post-exit hindsight peak**; BTC and SOL had no TP1 on record. A typical trade gives 3.1x (mean 24h post-exit peak 1.23R vs 0.40R taken), median excess 0.55R |
| 4.6x against own TP1 | 4.57x dollar-weighted, 4.58x on mean R, reproduced (25 Aug short basket) | Holds and is genuinely against TP1, but BTC's TP1 was touched about 33h after exit, outside my 24h window |
| Both are the two biggest waves | Across the 11 declared-TP1 trades, 4 reached TP1 after exit, 6 did not within 24h, 1 exited beyond it | n=2 baskets of 11; the 08-27 BTC leg gave back 0.19R with 64.6% captured (journal) |
| Commission 41% of lifetime loss | 41.2%, reproduced (income export through 2026-08-04) | Holds as a lifetime average, but by year it was 24%, 30%, 35%, **62.5% (2025)**, **52.4% (2026 YTD)**; commission/\|gross trading\| is 0.71 lifetime, 1.63 in 2025 |
| Zero maker in 3,390 fills | Not re-measurable: the income feed has no maker flag. Journal fills with a recorded role: 1 maker of 19 recorded legs (rows) on closed trades (08-27 SOL entry), plus 3 maker GTX entries on the open 10-08 trades (4 of 22 legs) | Stale as stated, but exit legs are still all taker, and the fee mass of closed trades is 97.5% of an all-taker model |
| Bot nets about -0.17R | -0.1475R restated (n=9,309), -0.1434R post-parity only; the no-restate mixture is -0.1285R | Drifted to -0.15R; the filed -0.1665R was n=4,907 |
| 28.9% of +1R reachers finish <=0; median giveback +0.813R | 26.9%; median +0.856R; median capture 0.539; n=3,321 (35.7%) | Same shape, not re-run on a fresh pre-registration |

## Caveats

- **The manual sample is tiny and selected.** 28 legs are 11 baskets of three correlated symbols, and 27 are wins, so the basket CI [0.15, 0.68] is the honest width. Journaled trades are not all trades: the lifetime export shows a net loss while the 13 journaled legs inside it (07-26 to 08-04) all won but one, so the journal's +0.40R gross cannot be read as the operator's edge.
- **The exit gap is partly hindsight.** Excursion comes from bar extremes, an upper bound; the 82% concentration in two trades of the TP1 credit means the realistic exit lever may be near 0.03R once those two are removed. This is the weakest leg of the ruling's trigger.
- **Costs are modelled.** Bot costs assume 100% taker and 2bps slippage on every leg and are an optimistic bound; the manual fee drag is realised but sits at 2 to 2.5% stops. At tighter stops it scales up inversely (a 0.5% stop is four times larger), so the cost pillar is larger for scalps than in this window.
- **The two frames disagree.** In R on the journal, exits beat costs; in the lifetime USD frame, costs are the largest named share and exits are unmeasurable. They agree only that both are leaks.
- **Unresolved rows:** bot 104 alerts unresolved (excluded); 0 unmeasurable for giveback. Manual: 3 open entries excluded, 9 early-basis entries excluded from cells.
- **Not measured, as opposed to zero:** manual sizing exposure; bot realised (non-modelled) costs and gap risk; funding as its own pillar; the time-stop sweep (filed 2026-08-07, cited, not re-run).
