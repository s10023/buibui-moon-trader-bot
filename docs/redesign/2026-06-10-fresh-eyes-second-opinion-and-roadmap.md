# Fresh-Eyes Second Opinion — How Buibui Becomes a Durable Automated Trading System

**Date:** 2026-06-10 · **Status:** strategy review (no code/config change) ·
**Companion to:** `docs/redesign/2026-06-05-top-tier-quant-redesign.md` (the 11-layer
reference). This document does not repeat that doc layer-by-layer — it independently
re-derives the diagnosis from outside evidence, profiles how the best crypto traders
actually make money, audits the current plan, and converts everything into one
sequenced roadmap with decision gates.

---

## 0. TL;DR

1. **Your frustration is diagnosable and fixable.** Months of effort went into the
   *signal-selection* half of the system (detectors, gates, sweeps, parity) — the
   half with the lowest payoff per hour. The *monetization* half (sizing, exits,
   portfolio, risk, execution) is where mature systems earn most of their
   risk-adjusted return, and it sat at zero until the P1 spec five days ago.
2. **The honest scoreboard says the TA book loses:** −0.12R/alert live (−0.134R with
   honest costs), 5.6% win rate, ~40% of alerts expire, only ~5/20 strategies
   positive, and **0/123 rated cells clear DSR ≥ 0.95**. The conditional-edge test
   (2026-06-04) already proved context doesn't rescue it. This is a category
   verdict, not a tuning problem. Stop drilling this well.
3. **The 2026-06-05 redesign's two-pivot thesis is correct** and matches outside
   evidence: (a) move alpha to crypto's structurally supported edges — vol-scaled
   trend, cross-sectional momentum, funding carry, basis — expressed as continuous
   forecasts; (b) build the sizing → portfolio → risk → execution stack. I confirm
   the plan and adjust its sequencing in four places (§4).
4. **Your goal statement maps cleanly onto two sleeves:** trend following is the
   "okay win rate, high RR" sleeve (~30–40% WR, fat right tail); carry/basis is the
   "high win rate, small RR" sleeve. A mature system runs both, vol-targeted, plus
   the surviving TA signals demoted to low-weight confirmation. No single-pattern
   system sustains long-term — diversification across uncorrelated return streams
   is the only structural free lunch.
5. **AI-suggested setups are an output layer, not an alpha source.** 2025–2026
   benchmark evidence says LLM agents trading on their own judgment mostly
   underperform with weak risk control; LLM-as-analyst over a structured,
   point-in-time market-state JSON (exactly what `buibui-ai-trade-suggestion-gaps.md`
   designs) is the defensible use. Sequence it after forecasts + sizing exist.
6. **Automation last, deliberately.** Auto-execution before portfolio risk controls
   is how accounts die. The bridge: sized trade cards executed manually + the
   journal + positions-write merge for real-PnL feedback, then automate only after
   ≥3 months of positive paper-book Sharpe.

The next 90 days, in one line: **close the cost loop (P0b PR-3) → run the MFE/MAE
exit diagnostic → build P1 sizing + paper portfolio → reframe trend as EWMAC
forecasts (P2) while backfilling a 10–30 perp universe in parallel.**

---

## 1. Your goal, restated precisely

> "Automated trading bot with a mature framework that sustains long-term — high win
> rate, or okay win rate with high RR — potentially with AI-suggested setups."

Reframe the success metric now, because the win-rate/RR framing hides the thing
that actually compounds:

```text
growth ≈ f( expectancy_after_costs × frequency × sizing ) − drawdown_drag
```

- **Win rate and RR are not goals; they are sleeve personalities.** Trend: 30–40%
  WR, avg winner ≥ 2.5R (right-tail capture). Carry: 70–90% of periods positive,
  small carry income, occasional sharp loss. Both are "mature"; neither dominates.
- **The number to manage is portfolio-level risk-adjusted return**: paper-book
  Sharpe ≥ 1.0 net of costs over ≥ 3 months, max drawdown inside a pre-set budget
  (e.g. ≤ 15% at a 20% annual vol target), with per-sleeve attribution.
- **Your current book's profile — 5.6% WR with high tp_r — is a lottery-ticket
  book** that nets negative after costs. It is neither of your two target shapes.

Everything below serves that reframed metric.

---

## 2. How the best crypto traders actually make money (research)

### 2.1 The edge taxonomy, with evidence

Ranked by robustness × retail accessibility:

| Edge | Evidence | Win-rate/RR shape | Retail-accessible? |
| --- | --- | --- | --- |
| **Time-series momentum (trend), vol-scaled** | A decade of evidence across crypto; recent 150-pair study (2022–24): Sharpe ~2.4, max DD −12.7%, still >2.0 Sharpe at 8 bps/trade costs; intermediate frequency (hours-to-days) beats both faster and slower | Low WR, high RR | **Yes — best first build.** Needs breadth (universe), not cleverness |
| **Cross-sectional momentum** | Academic crypto factor literature (size/momentum factors persist); the standard second sleeve for CTAs | Moderate WR | Yes, after universe + portfolio layer exists |
| **Carry (funding harvest)** | Spectacular historically (full-sample Sharpe ~6 in 2020–25 academic samples) but **decaying — weaker from 2024, negative stretches in 2025**; practitioner consensus: net 10–15% APY in favorable regimes, 0–5% neutral; funding flips sign and basis compresses | High WR, small RR | Yes at small size (capacity constraints hurt funds, not you), but it is an **operations** problem: two legs, margin, monitoring |
| **Basis / cash-and-carry, stat-arb** | Historical Sharpe ~4.8 for cash-and-carry; BTC–ETH cointegration arb ~14.9%/yr Sharpe 2.2 after costs in recent studies | High WR, small RR | Partially — same ops burden; sized small it diversifies the book |
| **Market making / latency** | Where Wintermute/Jump/GSR live: spread + inventory + speed | High WR, tiny RR | **No.** You cannot win the latency race; don't try |
| **Single-symbol TA patterns** (candles, FVG/OB, sweeps, BOS) | The most crowded, fastest-decaying category; *your own* 10-week de-biased ledger and conditional-edge test independently reproduce the academic null | — | The market's tuition collector. Keep only as low-weight confirmation |

Two implications worth internalizing:

- **The edges that work are boring and structural.** They monetize slow
  institutional flows (trend), persistent positioning imbalances (carry/basis), or
  breadth (XS momentum) — not pattern recognition on a single chart.
- **Every durable edge answers "who is paying me and why do they keep paying?"**
  Trend: under-reacting/late-rebalancing holders. Carry: leveraged longs paying for
  leverage. Basis: directional demand for synthetic exposure. Your TA book never
  had an answer to this question — that's *why* it decays.

### 2.2 Four archetypes worth studying (picked from the top)

**GCR (GiganticRebirth) — the discretionary apex.** Turned small capital into a
fortune via contrarian positioning bets (famously short LUNA at $90 before the
collapse). Method: reflexivity — find where the *crowd* is leveraged, offside, and
emotionally committed; bet against it with size; otherwise sit out. Lessons for
you: (1) his raw material is **positioning data — funding, OI, sentiment — which
you already ingest and reduce to a TA boolean**; (2) extreme selectivity — a
handful of monster trades a year, the exact opposite of ~34 unsized alerts/day;
(3) discretionary genius doesn't scale into a bot, but "trade only when the crowd
is provably offside" *can* be encoded as a forecast tilt.

**Renaissance / Jim Simons — the systematic ceiling.** No single signal; thousands
of weak, uncorrelated predictors **combined** into positions, with obsessive cost
modeling and risk control. Nobody at RenTec ships "the engulfing-candle strategy."
Lesson: the value is in **L5 combination + L6 sizing + cost honesty**, which is
precisely the stack you haven't built. Also their humility ritual: every signal is
assumed to decay; measurement, not conviction, decides retirement.

**Robert Carver / systematic CTA tradition — the realistic template for one
person.** Ex-AHL; his framework (continuous vol-normalized forecasts on −20…+20,
forecast combination, volatility targeting, instrument diversification multiplier,
fractional-Kelly discipline) is the single best documented blueprint for a solo
systematic trader. The repo already cites him; the P1 sizing spec is already
Carver-shaped. Lesson: **diversification + sizing contribute more risk-adjusted
return than signal selection** — his central empirical finding, and the inverse of
how this project spent its first months. Notably, industry research finds little
long-run return difference between systematic and discretionary *professionals* —
because the pros' discretion is rule-bound. The losing configuration is the
middle: many signals, no rules for size and risk. That middle is where this repo
currently sits.

**Alameda / FTX — the cautionary tale.** Made enormous money on basis/carry and
cross-venue arb when those were wide (2018–21 Kimchi premium, perp basis), then
died not from alpha failure but from **risk management and counterparty failure**.
Lessons: (1) carry edges decay as they crowd — size them, don't worship them;
(2) L8 (drawdown control, counterparty limits, custody discipline) is existential,
not optional; (3) never let one venue hold your solvency.

### 2.3 The seven principles they share (your checklist)

1. **Know who pays you.** Every sleeve must name its counterparty's forced error.
2. **Expectancy after costs is the only score.** (You just built this — P0b. Keep going: PR-3.)
3. **Sizing and survival dominate entries.** Vol targeting + drawdown governor + caps.
4. **Diversify across uncorrelated return streams** — sleeves, not more patterns within one stream.
5. **Few-and-large or many-and-small-and-systematic.** Never many, unsized, and discretionary at execution.
6. **Assume decay; measure it; retire edges without grief.** (Your OOS ledger + DSR guards are exactly this — genuinely top-decile for retail.)
7. **Operational and counterparty risk kills faster than bad alpha.** Kill-switch, reconciliation, venue diversification before scale.

Score yourself honestly: today you fully satisfy #2 (new) and #6, partially #1
(you can now *name* which strategies have no payer), and none of #3/#4/#5/#7.
That scorecard *is* the roadmap.

### 2.4 What a solo engineer-trader can realistically claim

- Trend + XS momentum at intermediate frequency on a 10–30 perp universe: the core.
- A small funding-carry/basis sleeve: the diversifier (ops-gated, start manual and tiny).
- Surviving location-type TA signals (liquidity_sweep, fib_golden_zone, eqh_eql)
  as **confirmation tilts** on the above — never standalone position-makers.
- An LLM analyst layer for synthesis/explanation — never for alpha.
- **Not claimable:** latency games, order-book market making, news-speed trading.

---

## 3. What this repo is today (fresh eyes)

### 3.1 The system, briefly

A 20-strategy TA **alert engine**: OHLCV/funding/OI ingestion (Binance/OKX →
DuckDB) → per-strategy detectors → gates (regime, direction, F8 HTF-EMA, ADR,
cooldown, conflict resolver) → Telegram alerts with stats context → an outcome
ledger that forward-walks every alert to win/loss/expiry. Around it: a
sweep/combo/cross-TF backtest engine with live-parity gates, WFO param tools,
star-rating recalibration, a FastAPI+Svelte UI, and — since last week — a
research-integrity layer (DSR/PBO/MinTRL commit gates, bootstrap-CI audit
verdicts) plus honest costs (fees + slippage + funding) in the backtest P&L.

**Engineering quality is top-decile** for a project this size: mypy strict, 1,693
tests, golden regression fixtures, typed frozen configs, an honest de-biased OOS
ledger most retail shops never build. None of that is the problem.

### 3.2 The honest scoreboard (the problem)

| Measure | Value | Source |
| --- | --- | --- |
| Live ledger expectancy | **−0.12R/alert** (~10 wk, 2,410 resolved) | `signal_alert_outcomes` |
| Backtest book, honest costs | **−0.134R/alert** (was −0.084R pre-costs) | P0b PR-2 db-update |
| Win rate | **5.6%**; ~40% of alerts expire unresolved | ledger |
| Last 30 days | 2,499 alerts → 156 wins / 1,327 losses / 967 expired | `live_outcomes_report.py` 2026-06-10 |
| Strategies net-positive | ~5/20 (liquidity_sweep, fib_golden_zone, smt_divergence, eqh_eql, bos-short) | edge audit |
| Cells clearing DSR ≥ 0.95 | **0 / 123** | P0a-3 acceptance proof |
| Position sizing / exits / portfolio risk | **absent** (P1 spec written 2026-06-05, unbuilt) | — |
| Real-PnL feedback | absent (`feat/positions-write` unmerged; 1 journal entry) | — |

The 30-day per-strategy table looks tempting in places (morning_evening_star
+0.41R, inside_bar +0.36R — 15m shorts). Don't chase it: those are exactly the
small-n, DSR-failing cells the guards exist to block, and the short-side tilt is
already captured by the direction overlay. The system now *correctly* refuses to
let you fool yourself — when the commit gate stamps DO-NOT-COMMIT on everything,
that is the answer, not an obstacle.

### 3.3 Why months of work produced little progress (the honest diagnosis)

Read the PR history as an effort ledger: ~430 PRs — monolith splits, gate
machinery, parity ports, sweeps, audits, UI — overwhelmingly improving **how well
the system selects and ships TA signals**. The alpha category was never going to
pay regardless of how well-selected, and nothing downstream existed to convert
even the good signals into compounded capital. You optimized the cheapest lever to
near-perfection while the expensive levers stayed at zero. That is the entire
explanation. It is also *recoverable*: the infrastructure built along the way
(data layer, backtest engine, ledger, guards) is exactly what the real system
needs — you were not wasting time, you were building the lab before the product.

The last five sessions (P0a guards → P0b costs → P1/exit specs) already turned
toward the right half. This report's job is to keep the turn from stalling.

---

## 4. Independent verdict on the existing redesign plan

The 2026-06-05 doc's diagnosis and phase plan are **confirmed** — outside evidence
(§2.1) independently supports every ranked edge and the two-pivot thesis. Four
adjustments:

1. **Pull universe data forward (parallelize, don't sequence).** P2 (EWMAC trend)
   evaluated on only BTC/ETH/SOL will look mediocre — trend's Sharpe comes from
   breadth across imperfectly-correlated instruments. Start the 10–30-perp OHLCV +
   funding backfill *now* (the `analytics backfill` machinery already does this);
   handle symbol lifecycle/survivorship at ingest. Then P2's decision gate is
   judged on breadth, not on three correlated majors.
2. **Exits are part of P1, not later.** The 40%-expiry leak and sizing interact
   (both reshape the equity curve); the exit spec already says to judge them
   jointly. Run the **MFE/MAE diagnostic first** — it is days of work and decides
   whether the TA book is exit-fixable or entry-broken before you spend more on it.
3. **Carry can start as a tiny manual sleeve in Q2.** It is operations-gated, not
   research-gated: one spot leg + one perp short leg + a funding dashboard. Doing
   it manually at small size teaches the failure modes (funding flips, basis
   compression, margin ops) before any code automates it. Code-side carry (P4)
   stays where it is.
4. **Add an explicit weekly decay-review ritual** (not code): ledger deltas,
   DSR-suspect list, per-sleeve attribution once P1 lands. The guards only bite if
   someone reads them on a cadence.

One more framing the doc undersells: **P1 is not just a multiplier, it is the
measurement device.** Until a paper book with sizing exists, you literally cannot
observe the number your goal is defined in (risk-adjusted return). That is why P1
outranks all alpha work, including P2.

---

## 5. Gap analysis vs your stated goal

### 5.1 "Automated" — the path to safe automation

Missing today: execution layer (L7), live risk overlay (L8), reconciliation, and
real-PnL feedback. Right order (unchanged from the redesign, made explicit):

```text
paper book (P1) → sized trade-card alerts, manual execution + journal
   → positions-write merge (T4): bot reads your real fills/PnL
      → ≥3 months paper Sharpe ≥ 1.0 net of costs, DD inside budget
         → L8 first (kill-switch, drawdown governor, margin/liq monitor)
            → L7 auto-execution, small size, one sleeve at a time
```

Automating before L8 + reconciliation is the one mistake in this domain you cannot
iterate out of.

### 5.2 "AI-suggested trade setup" — what the evidence supports

2025–26 benchmarks (LiveTradeBench, AI-Trader, "Can LLM strategies outperform
long-run?") converge on: most LLM agents trading on their own judgment
underperform in live markets with weak risk control; *architecture* (what data the
agent sees, what rules bound it) matters far more than model choice; and
backtests of LLM strategies are systematically contaminated by look-ahead bias.

So: **LLM as analyst/synthesizer, never as alpha source.** The repo's own
`buibui-ai-trade-suggestion-gaps.md` already designs exactly the right thing — a
structured, point-in-time market-state JSON (indicators, zones, regime,
positioning, open risk) → LLM emits a TRADE/NO-TRADE card with cited numbers,
bounded by hard rules (circuit breaker, n<30 downgrade, no invented values). Its
gap list (G1–G14) stands. Sequencing: after P1 (so the card can carry a *size*)
and ideally P2 (so it carries a *forecast*, not a boolean). The card is also your
manual-execution bridge in §5.1 — it is on the critical path to automation, which
is a stronger justification than "nice AI feature."

### 5.3 Remaining unmeasured leaks

- Live ledger still cost-free (`outcome_backfill._scan_forward`) — P0b PR-3, do first.
- Combo/cross-TF/param-sweep/web backtests still cost-free or partial — finish the bundle so no surface shows flattering numbers.
- `dsr` column populated but invisible downstream (config router reads only `stars`) — surface it before anyone trusts a star again.
- Only 1 journal entry; the bot is traded manually today, so the journal *is* the real-PnL ledger until T4 merges.

---

## 6. The roadmap

### 6.1 North star

> **A vol-targeted, multi-sleeve paper portfolio — trend (EWMAC, universe-wide) +
> carry pilot + TA-confirmation tilts — with Sharpe ≥ 1.0 net of honest costs over
> ≥ 3 consecutive months and max DD ≤ 15% at 20% vol target, with per-sleeve
> attribution. Only then: automation, smallest viable size, one sleeve at a time.**

### 6.2 Phases with decision gates

| Phase | Content | Gate to pass before next |
| --- | --- | --- |
| **Now (wks 1–2)** | P0b PR-3 (live-ledger costs); MFE/MAE diagnostic; start universe backfill (10–30 perps, OHLCV+funding, lifecycle-aware); optional live-outcomes Stats card | Costs honest everywhere; MFE/MAE verdict in hand |
| **P1 (wks 3–6)** | `portfolio/` package per spec (fixed-fractional on stop, vol governor, concurrent + cluster caps); exit-policy replay (time-stop, BE, one trail, partial) judged jointly; L11 metrics on paper curve | **G1:** pruned TA book + direction overlay + sizing + best exits → paper Sharpe > 0? If **no** → TA book demoted to confirmation-only, stop all TA tuning forever |
| **P2 (wks 7–12)** | EWMAC multi-speed TS-momentum as continuous vol-normalized forecasts; L5 combination; TA survivors as low-weight confirmation; evaluate on the *universe*, DSR/PBO-gated | **G2:** trend sleeve OOS Sharpe ≥ ~1 on breadth, costs in. If marginal on 3 majors but positive on breadth → proceed (breadth is the mechanism) |
| **Q2** | P3 XS momentum + full portfolio layer (IDM, correlation); P4 carry: manual pilot sleeve (tiny) + funding dashboard; T4 positions-write merge; F2 AI trade card v1 (market-state JSON + LLM client, G11/G12) | **G3:** multi-sleeve paper book ≥ 3 months Sharpe ≥ 1.0, DD in budget |
| **Q3** | L8 risk overlay (kill-switch, drawdown governor, margin/liq, de-peg/counterparty caps) → L7 auto-execution, smallest size, trend sleeve first; reconciliation | **G4:** live tracks paper within tolerance for 4+ weeks before size increases |

### 6.3 The next 90 days, concretely

1. **Week 1–2:** P0b PR-3 (small, specced §4). Run exit-spec §2 MFE/MAE study —
   it answers "is the 40% expiry an exit problem or an entry problem" with days of
   work. Kick off universe backfill in the background.
2. **Week 3–6:** Build P1 exactly per spec (it is already Carver-correct: 0.25%
   r_base, 20% vol target, 2% concurrent cap, 1% majors-cluster cap). Wire exit
   replay into it. Produce the first-ever Sharpe/Sortino/max-DD of this system.
   Evaluate gate G1 and **write the verdict down**.
3. **Week 7–12:** P2 forecast reframe. Reuse the guards (DSR/PBO) you already
   built as the promotion gate — they are precisely the right tool, now pointed at
   a category with a real prior. Meanwhile run the carry sleeve on paper from the
   funding data you already store.
4. **Throughout:** weekly decay review (30 min: ledger delta, DSR suspects,
   attribution); journal every manual trade; no new TA detectors.

### 6.4 Stop-doing list (protects the 90 days)

- **No new boolean TA detectors.** Freeze master-todo Tier 3 (D1, D2, D6, D7,
  D11–D16, H5, H7…). Each is another cell for the guards to reject.
- **No more tp_r/gate/threshold sweeps on the TA book.** 0/123 DSR pass is the
  final answer; the audits are done; the well is dry. (Guard maintenance excepted.)
- **No UI work** except the live-outcomes card and, later, the trade card.
- **No LLM-as-signal experiments** (evidence: §5.2).
- **Wifey fork stays gated** on the parent having measurable edge (already agreed).
- **No automation before G3/G4** — no matter how good one month looks.

### 6.5 What "done" looks like in a year

A small, boring, diversified book: a trend sleeve that loses often and small and
wins rarely and big; a carry sleeve that pays steadily and occasionally gets
slapped; TA tilts that nudge entries at levels; everything vol-targeted, capped,
attributed, and killable by one switch — with an AI card explaining each position
in plain language from numbers it cannot invent. That is what "mature framework
that sustains long-term" actually means in practice. Nothing about your
infrastructure needs to be thrown away to get there — it is, genuinely, the hard
40% you already built.

---

## 7. Sources

- [Systematic Trend-Following with Adaptive Portfolio Construction in Crypto (arXiv 2602.11708)](https://arxiv.org/html/2602.11708v1) — 150+ pairs, 2022–24, Sharpe ~2.4, cost-robust to 8 bps.
- [A Decade of Evidence of Trend Following in Cryptocurrencies (arXiv 2009.12155)](https://arxiv.org/pdf/2009.12155)
- [Cryptocurrency as an Investable Asset Class: Coming of Age (arXiv 2510.14435)](https://arxiv.org/html/2510.14435v2) — carry Sharpe full-sample vs 2024–25 decay; basis Sharpe.
- [The Two-Tiered Structure of Cryptocurrency Funding Rate Markets (MDPI)](https://www.mdpi.com/2227-7390/14/2/346)
- [Trading Games: Beating Passive Strategies in the Bullish Crypto Market (J. Futures Markets, 2025)](https://onlinelibrary.wiley.com/doi/full/10.1002/fut.70018)
- [Crypto Funding Rate Arbitrage: A Delta-Neutral Guide (ArbitrageScanner)](https://arbitragescanner.io/blog/crypto-funding-rate-arbitrage-guide) and [Delta-Neutral Crypto Strategies: Risks Included (Blofin Academy)](https://blofin.com/en/academy/education/delta-neutral-crypto-strategies) — practitioner net-APY and failure modes.
- [When Agents Trade: Live Multi-Market Trading Benchmark for LLM Agents (arXiv 2510.11695)](https://arxiv.org/abs/2510.11695)
- [LiveTradeBench: Seeking Real-World Alpha with LLMs (arXiv 2511.03628)](https://arxiv.org/pdf/2511.03628)
- [AI-Trader: Benchmarking Autonomous Agents in Real-Time Markets (arXiv 2512.10971)](https://arxiv.org/abs/2512.10971)
- [Can LLM-based Financial Investing Strategies Outperform the Market in the Long Run? (arXiv 2505.07078)](https://arxiv.org/html/2505.07078v5)
- [Look-Ahead-Bench: Look-ahead Bias in Point-in-Time LLMs for Finance (arXiv 2601.13770)](https://arxiv.org/pdf/2601.13770)
- [GCR: The Most Famous Crypto Trader — Profile & History (Gate Learn)](https://www.gate.com/learn/articles/gcr-the-most-famous-crypto-trader/2640) and [7 Lessons from GCR (ExpressTech)](https://www.expresstechsoftwares.com/gcr-crypto-trader-strategies/)
- [The Trader: Discretionary v Systematic (ShareScope)](https://knowledge.sharescope.co.uk/2024/11/15/the-trader-discretionary-v-systematic/) — pro sub-index return parity.
- Robert Carver, *Systematic Trading* / *Advanced Futures Trading Strategies* — forecast scaling, vol targeting, IDM (the project's existing anchor).
