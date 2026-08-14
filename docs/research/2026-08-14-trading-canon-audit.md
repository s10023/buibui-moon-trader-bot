# Trading canon audit — books, people, repos

**Date:** 2026-08-14 · **Scope:** buibui (crypto) + wifey (US equities) · **Question asked:**
*where is our edge, what is the system missing, what can we integrate without starting over.*

**Status of the numbers.** Every repo figure in §3 was measured against the GitHub API on
2026-08-14 and is reproducible. Every book/person pick is a **judgement**, not a
measurement — treat it as a hypothesis, per [[filed-artifact-is-a-hypothesis]]. I have not
read these books inside this session; the claims about their *contents* come from training
knowledge and should be checked against the book once it is ingested (§5).

---

## 1. Verdict up front

The two ChatGPT docs recommend building the system we already built. Carver sizing, DSR/PBO
gates, walk-forward, journalling, hypothesis→backtest→robustness loop — all shipped. So the
answer to "what are we missing" is **not** in their top-line picks. Three things are:

### 1a. The largest *measured* loss channel is execution, and nothing in either doc touches it

Our own audit (`project_pnl_self_audit.md`) is unambiguous: **−70,633 over 4.2 years, 41% of
it commission, and ZERO maker fills across 3,390 trades.** Passive execution alone is ~25% of
the loss. That is not a signal problem and no amount of strategy research fixes it.

Neither ChatGPT doc names a single book or repo about market microstructure, order placement,
adverse selection, or maker/taker economics. The canon here is deep and we have never opened
it: **Harris, *Trading and Exchanges*** for why you get filled and what it costs;
**Cartea/Jaimungal/Penalva, *Algorithmic and High-Frequency Trading*** for optimal execution
and market making; **hummingbot** (19,457★, 234 contributors, pushed today) as the reference
implementation of quoting logic on the exact exchanges we trade.

This is the highest expected-value item in the whole audit, because the effect size is already
measured rather than hoped for, and the fix does not need a new edge.

### 1b. There is no canonical crypto trading *book* — the live literature is papers

Verified by search, and it is a real finding rather than a gap in the search. The crypto
equivalent of a canon is the factor literature: Liu & Tsyvinski, *Risks and Returns of
Cryptocurrency* (RFS 2021); Liu, Tsyvinski & Wu, *Common Risk Factors in Cryptocurrency*
(JF 2022); *A Trend Factor for the Cross Section of Cryptocurrency Returns* (JFQA 2024); and
a 2022 replication of **34 anomalies across 3,900 coins**.

Two consequences. First, our deploy core (XS momentum, +1.375 Sharpe, DSR 0.997) is the single
most-replicated crypto anomaly in the literature — that is independent corroboration we did not
have. Second, this literature is a source of **pre-registered hypotheses with published effect
sizes**, which is exactly the shape our DSR maths demands: per
`2026-08-12-multi-regime-validation.md`, trial count dominates n (1→320 trials moves the bar
+0.049R→+1.035R against a corpus best of +1.196R), so one hypothesis carrying a prior beats a
scan every time.

### 1c. The cheapest untested *new* data axis is options/vol, and it is free

Standing conclusion in `CLAUDE.md`: conditioning axes are 6-for-6 no-edge, the price-only
free-data levers are exhausted, and a new sleeve needs genuinely new information. The memory
index says the three remaining axes are PAID. **That is not true of options.** Deribit's public
API serves DVOL (30-day annualised IV index) since **2021-04-01** and per-instrument mark IV
since **2019-10-01**, keyless.

Implied vol, term structure and 25-delta skew are a genuinely different information source —
forward-looking and positioning-derived, not a re-slice of the price/order-flow we already hold.
Caveat that bounds the design: Deribit liquidity is BTC/ETH only, so this can serve as a
**regime/state gate or a TS signal**, never as a cross-sectional factor over the 25-perp
universe. Pre-register it as one hypothesis before touching code
([[price-the-power-before-designing]]).

---

## 2. Books and people

Ranked for *our* goal, not for a general reader. Where the conventional pick differs, it is
named so the disagreement is visible.

### 2.1 Trading (the craft)

| Ask | Pick | Why this one |
| --- | --- | --- |
| **1 best book** | **Larry Harris — *Trading and Exchanges*** | The only book that explains mechanically where the money goes: order types, liquidity provision vs taking, adverse selection, spread cost. Names the exact channel our own audit measured. |
| **1 best person** | **Larry Harris** | Same reason; his corpus is the microstructure foundation everything else assumes. |
| **3 best books** | Harris, *Trading and Exchanges* · Mark Douglas, *Trading in the Zone* · Jack Schwager, *Market Wizards* | Douglas is the conventional #1 and earns it for the discretionary book (3,390 manual trades); Schwager for breadth of how real money is actually made. |
| **3 best people** | Larry Harris · Mark Douglas · Adam Grimes | Grimes is the rare discretionary-adjacent teacher who tests claims statistically and publishes negative results. |

*Conventional pick we are demoting:* Douglas at #1. Psychology is a real constraint on the
manual book, but the bot has no psychology and the measured loss is mechanical.

### 2.2 Crypto trading

| Ask | Pick | Why this one |
| --- | --- | --- |
| **1 best book** | **None — read Liu & Tsyvinski instead** | Honest answer. No crypto trading book clears the bar set by the papers. If one physical book must be bought, Carver's *Advanced Futures Trading Strategies* transfers better to perps than any crypto-branded title. |
| **1 best person** | **Aleh Tsyvinski (with Yukun Liu)** | Anchors the crypto factor literature; the source of the cross-sectional evidence our deploy core rests on. |
| **3 best "books"** | Liu & Tsyvinski (RFS 2021) · Liu/Tsyvinski/Wu (JF 2022) · *A Trend Factor for the Cross Section of Crypto Returns* (JFQA 2024) | The actual canon. All three are testable against our existing 25-perp panel. |
| **3 best people** | Tsyvinski/Liu (factors) · Arthur Hayes (perp/basis/funding *mechanics* — the plumbing essays only, not the macro calls) · Coin Metrics research desk, Nic Carter & Lucas Nuzzi (on-chain data done rigorously) | Split deliberately: one academic, one mechanic, one data. |

*Caveat on Hayes:* read for how funding, basis and liquidation cascades actually work. His
directional macro is not evidence and must not enter the ledger.

### 2.3 Building a trading system

| Ask | Pick | Why this one |
| --- | --- | --- |
| **1 best book** | **Robert Carver — *Advanced Futures Trading Strategies* (2023)** | ChatGPT picked Carver's *Systematic Trading* (2015). Right author, wrong book **for where we are now**: we already implement his sizing. AFTS supplies 30 pre-tested strategies with published Sharpes (pre-registered hypotheses, §1b) *and* explicit cost handling (§1a) — it hits both open problems. Read *Systematic Trading* only if the framework itself is ever rebuilt. |
| **1 best person** | **Robert Carver** | And critically, his code: `pysystemtrade` is the reference implementation of the model we already partially copied. See §3 — ChatGPT recommended the author and missed the repo. |
| **3 best books** | Carver, *AFTS* · López de Prado, *Advances in Financial Machine Learning* · Cartea/Jaimungal/Penalva, *Algorithmic and High-Frequency Trading* | We already use López de Prado's DSR and PBO and nothing else from that book — purged/embargoed CV, sample uniqueness weights, fractional differentiation and meta-labelling are all unread. |
| **3 best people** | Robert Carver · Marcos López de Prado · Ernest Chan | Chan is ChatGPT's pick and is fair for practical implementation. Álvaro Cartea is the substitute if execution becomes the priority, which §1a argues it should. |

*Reference shelf, not a read-through:* Perry Kaufman, *Trading Systems and Methods* — a
1,200-page toolbox, correct as ChatGPT's #5, wrong as a book to read front to back.

⚠ **Do-not-relitigate check:** *AFML*'s meta-labelling is "use a second model to decide which
signals to take". We have run that idea and it **failed the gate** — ensemble/confluence
scoring, DSR 0.7030, verdict `2026-08-11-ensemble-walkforward.md`. Read the chapter for the
CV machinery, not as a re-opening.

### 2.4 US stocks (drives wifey)

| Ask | Pick | Why this one |
| --- | --- | --- |
| **1 best book** | **Wesley Gray & Jack Vogel — *Quantitative Momentum*** | wifey is architecturally a cross-sectional momentum book on equities. This is that literature made implementable, including the frog-in-the-pan path-quality screen — a concrete, cheap, pre-registered test for wifey. |
| **1 best person** | **Wesley Gray (Alpha Architect)** | Publishes replication code and negative results, which is the house style we already enforce. |
| **3 best books** | Gray & Vogel, *Quantitative Momentum* · Antti Ilmanen, *Expected Returns* · Stefan Jansen, *Machine Learning for Algorithmic Trading* | Ilmanen is the best single book on *what actually earns returns and why*, cross-asset. Jansen ships a 20,440★ code repo alongside. |
| **3 best people** | Wesley Gray · Antti Ilmanen · Stefan Jansen | AQR's research library (Asness, Ilmanen, Israel) is free and is the highest-quality public equity-factor output there is. |

*Conventional pick we are demoting:* **William O'Neil, *How to Make Money in Stocks*** — both
ChatGPT docs put it at #1 for US stocks. CAN SLIM is a coherent discretionary growth framework,
but its out-of-sample record is contested and it is not evidence-first. It fails our own
persona rule (de-biased OOS evidence over in-sample optimism). Keep it as background reading on
how momentum feels; do not build wifey on it.

### 2.5 If you read only one thing

**Carver, *Advanced Futures Trading Strategies*** — for the second edge and the cost discipline.
**If execution is prioritised instead (and §1a says it should be), Harris, *Trading and
Exchanges*.**

---

## 3. GitHub repos — measured, not asserted

All figures pulled from the GitHub API on **2026-08-14**. Guardrails applied: stars,
contributor count, recency of last push, archive status.

### 3.1 The health table

| Repo | ★ | Contribs | Last push | Commits/90d | Verdict |
| --- | ---: | ---: | --- | ---: | --- |
| freqtrade/freqtrade | 53,271 | 340 | 2026-08-14 | 100+ | ✅ healthiest in the field |
| ccxt/ccxt | 43,625 | 350 | 2026-08-14 | 100+ | ✅ already a dependency-class tool |
| nautechsystems/nautilus_trader | 25,485 | 179 | 2026-08-14 | 100+ | ✅ best architecture reference |
| QuantConnect/Lean | 21,203 | 210 | 2026-08-13 | 100+ | ✅ healthy, but C# |
| hummingbot/hummingbot | 19,457 | 234 | 2026-08-13 | 100+ | ✅ **the §1a repo** |
| microsoft/qlib | 47,403 | 139 | 2026-07-23 | 1 | ⚠ big but nearly idle this quarter |
| robcarver17/pysystemtrade | 3,434 | 57 | 2026-07-18 | 31 | ✅ study; solo-maintainer risk |
| kernc/backtesting.py | 8,830 | 45 | 2026-08-05 | 27 | ✅ small and honest |
| jesse-ai/jesse | 8,317 | 51 | 2026-08-12 | 100+ | ✅ crypto-native alternative |
| polakowo/vectorbt | 8,674 | 21 | 2026-08-02 | 34 | ⚠ **maintenance mode**; dev moved to closed-source vectorbt PRO (~$25/mo) |
| mementum/backtrader | 22,843 | — | **2024-08-19** | 0 | ❌ unmaintained ~2 years |
| hudson-and-thames/mlfinlab | 4,906 | — | **2023-10-02** | 0 | ❌ dead; went commercial |
| stefan-jansen/zipline-reloaded | 1,919 | — | 2026-01-06 | 0 | ⚠ maintenance only |
| paperswithbacktest/awesome-systematic-trading | 13,297 | 6 | 2025-01-22 | 0 | ⚠ stale list, still a useful index |
| **quantstart/qstrader** | **142** | — | **2019-03-08** | 0 | ❌ **dead 7 years** |
| **DaruFinance/quant-research-framework** | **2** | 1 | 2026-07-31 | — | ❌ **2 stars — fails every guardrail** |
| twopirllc/pandas-ta | — | — | — | — | ❌ **repo no longer exists** |

### 3.2 The picks

| Ask | Pick | Why |
| --- | --- | --- |
| **1 best repo overall (for us)** | **robcarver17/pysystemtrade** | We already implement Carver's two-layer sizing from the book. This is the production reference for that exact model — forecast scaling, forecast diversification multiplier, instrument diversification multiplier, buffering, dynamic optimisation. **Read it, do not depend on it**: 57 contributors and one maintainer. Note the IDM is the same idea as our measured 2.92× correlation deflator, arrived at independently. |
| **1 best repo to learn architecture from** | **nautechsystems/nautilus_trader** | Event-driven, Rust core with a Python API, and correct backtest/live parity — the property our ST9 fidelity gate keeps failing. Lean is the equal-quality alternative but is C#, so it stays a reading exercise. |
| **1 best crypto repo** | **freqtrade/freqtrade** | ChatGPT got this right. 340 contributors, pushed daily, and the closest thing to our own daemon's shape. |
| **3 best repos** | freqtrade · nautilus_trader · pysystemtrade | Execution lifecycle, architecture, portfolio/risk. |
| **3 best for building a system** | nautilus_trader (architecture) · pysystemtrade (sizing/risk/portfolio) · hummingbot (order placement and maker economics) | hummingbot replaces vectorbt from the ChatGPT list — vectorbt is in maintenance mode, and we do not have a research-speed problem, we have a cost problem. |

### 3.3 Where the ChatGPT repo doc is wrong

Four of its recommendations fail the user's own stated guardrails, and one is a serious miss:

1. **`DaruFinance/quant-research-framework` — 2 stars, 1 fork.** Listed as an "important
   alternative". This is the failure mode the guardrails exist to catch.
2. **`quantstart/qstrader` — 142 stars, last commit 2019.** Listed as a **top-3 repo for
   building a trading system**.
3. **`vectorbt` at #2 overall.** Open-source is maintenance-mode; development moved to
   closed-source vectorbt PRO. Fine to read, wrong as an architecture bet.
4. **`backtrader`** — correctly hedged in the doc, and the data confirms it: nothing since
   August 2024.
5. **The miss: `pysystemtrade`.** The doc names Carver as the #1 author across both documents
   and never mentions that his production system is open source. That is the single
   highest-relevance repo for us and it is absent.

Also absent and relevant: **hummingbot** (§1a) and **`shiyu-coder/Kronos`** (37,145★, a
foundation model for financial time series — worth one look as a candidate new-information
axis, but it is a model rather than data and would need the full gate).

The doc's §6 argument — *"stars are a weak proxy for suitability"* — is correct and worth
keeping. It just was not applied to its own list.

---

## 4. The book-reading skill: adopt, do not build

**Already in your stars:** [`virgiliojr94/book-to-skill`](https://github.com/virgiliojr94/book-to-skill)
— 21,431★, 24 contributors, pushed 2026-08-13, 100+ commits in 90 days.

It does exactly what was asked and its architecture matches our house style: a **deterministic
Python extractor** (document → clean text + metadata) plus a **spec-driven generator** (the
agent follows a `SKILL.md` to build a structured skill). Output is a skill directory with
per-chapter files loaded on demand — frameworks, decision rules and anti-patterns rather than a
summary. Claimed **24×–51× fewer tokens** than dumping a book into context. Accepts a file,
folder or glob; has analyze-only, generate-from-analysis and fold-in modes.

Two integration notes before installing:

- It installs to `~/.claude/skills/<slug>/`. We run `CLAUDE_CONFIG_DIR=~/.claude-personal`, so
  the skill must land in `~/.claude-personal/skills/<slug>/` or it will never load.
- Account-level skills **must be directories containing `SKILL.md`** — flat `.md` files never
  load (12 already inert). book-to-skill emits directories, so it is compatible; just verify
  the destination after the first run.

**Recommendation: install it as-is, wrap nothing.** Wrapping a healthy 21k-star tool is how we
end up maintaining a fork.

---

## 5. The synthesizer skill: `/research-distil`

### The binding constraint, from our own files

`docs/plans/thesis-inbox.md` opens with: *"the bottleneck is testing capacity, not idea
capture."* And `2026-08-12-multi-regime-validation.md` measured that **trial count dominates
n** — 1→320 trials moves the DSR bar from +0.049R to +1.035R against a corpus best of +1.196R.

Therefore the obvious design is actively harmful. A skill that reads three books and emits
forty hypotheses into `thesis-inbox.md` does not help us — it inflates the trial family and
pushes every cell out of reach. **The synthesizer's job is to throttle, not to amplify.**

### Design

`/research-distil <book-skill|repo|paper> [...]` — reads distilled sources, emits **at most
three** candidate hypotheses per run, and each one must survive four gates before it is written:

1. **Novelty gate** — reject anything matching `project_do_not_relitigate.md` or a filed
   no-edge verdict. Meta-labelling, ensemble scoring, DOW/weekend, exit tuning, conditioning
   axes: all closed. This gate alone kills most of what a trading book will suggest.
2. **New-information gate** — does it need data we do not hold? If it is a re-slice of
   price/order-flow, it is 7-for-7 dead and gets rejected with that citation.
3. **Power gate** — invert DSR at the *real* n before writing anything
   ([[price-the-power-before-designing]]). Emit the required effect size next to the corpus
   best. If the bar exceeds anything ever observed, the output is "unreachable, do not build",
   which is a success for this skill.
4. **Cost gate** — express the expected edge net of the modelled drag
   `2(fee+slip)·entry/risk`. Anything that survives only gross is rejected.

Survivors are written in the existing thesis-inbox format with a **Decision Log** naming the
observable that reverses them, and routed to the existing Stream A / Stream B lanes.
Execution and cost findings route to `mechanics-backlog.md` instead, since they change how we
trade rather than what we trade.

**It creates no new pipeline.** `/ingest-x`, `/ingest-video` and `/ingest-charts` already feed
these files; books and repos become a fourth source into the same intake, with the same single
human review gate.

---

## 6. wifey: research once, hand over the delta

**Recommendation: mix, weighted to reuse.** Do not re-run this research in wifey.

Roughly 80% of §2 and §3 is market-agnostic — Carver, López de Prado, Harris, Cartea,
nautilus, pysystemtrade, hummingbot, book-to-skill and the whole synthesizer design apply
unchanged. Re-running the research there would burn a second quota to reach the same place,
and the two answers would then drift apart.

**What to send wifey:** this file's absolute path plus a short delta brief covering only what
differs — §2.4 is wifey's core section rather than a footnote, and §1b's crypto factor papers
swap for the equity factor literature, where wifey's XS momentum book has a far deeper
replication base (and a correspondingly worse crowding/decay prior).

Both repos are on this machine, so a path is sufficient — this is not the cross-repo pipe that
`project_cross_repo_noncrypto_handoff.md` says DON'T BUILD. It is one message with one path.

### Two corrections from wifey, applied

The brief was sent and came back with two corrections. Both are recorded here because they
change what the delta says, and the second narrows a claim in §1c's framing.

**§1a does NOT port, and it is not wifey's highest-EV item.** The finding is real in buibui
because there are 3,390 realised fills to interrogate. **wifey has zero** — `trade/` is empty in
both files, the Binance opener was dropped at fork time and nothing replaced it. More to the
point, wifey already shipped the answer as an assumption:
`analytics/backtest/cost_model.py` decomposes the equity stack — half-spread bucketed by
trailing dollar ADV (20/8/3/1 bps, unknown ADV falling to the widest bucket), sqrt-law impact
charged per leg, flat 1%/yr short borrow, and **commission at 0.0 bps**. Our dominant channel is
structurally zero there. Its residuals — flat rather than per-symbol borrow, and an unmodelled
auction/PFOF choice — both make cost too *high*, so tightening them can only push verdicts down,
never rescue one. Harris and Cartea stay worth reading; the urgency does not transfer.

**§1c's options axis is NOT richer for equities — it is narrower.** The brief claimed "full
listed chains, VIX term structure and skew, all with long free history". On the free stack that
is wrong: `yfinance` exposes only *current* expirations via `option_chain()`, with no historical
chain endpoint, so **no historical skew and no historical surface — a skew signal cannot be
backtested on free equity data at all.** What is genuinely free with long daily history is the
VIX complex as index series (`^VIX`, `^VIX3M`, `^VIX9D`, `^VVIX`; CBOE's `^SKEW` is a further
candidate, unverified). That is real term structure, but it is a handful of daily series rather
than a surface.

Which **inverts the comparison in §1c**: Deribit's free API serves per-instrument mark IV — an
actual surface — since 2019-10-01, so *crypto* has the richer free option data and equities the
one-dimensional version. The door in §1c is still open and still the cheapest new axis; it is
just narrower on the equity side than first written, and the power calculation is
correspondingly less favourable.

---

## 7. What to actually do, ranked

1. **Install `book-to-skill`** into `~/.claude-personal/skills/`, verify the load path, and
   convert **Harris, *Trading and Exchanges*** first — it targets the one loss channel we have
   already measured. Carver's *AFTS* second.
2. **Build `/research-distil`** with the four gates in §5. Its first job is to prove it can say
   "unreachable, do not build".
3. **Pre-register the Deribit DVOL/IV hypothesis** (§1c) — one hypothesis, power-priced before
   any code, as a TS/regime gate and explicitly not a cross-sectional factor.
4. **Open the execution question properly** (§1a): read `hummingbot`'s quoting logic against
   `trade/routing.py`, and re-price the XS sleeve's gate verdict with a realistic maker
   assumption. Zero maker fills in 3,390 trades is the loudest number we own.
5. **Send wifey the delta brief** (§6). Do not re-research.

**Not recommended:** adopting Lean, vectorbt, backtrader or qstrader as architecture; rebuilding
around any framework at all. The system is built. The gaps are execution cost, one new data
axis, and a throttle on idea intake.
