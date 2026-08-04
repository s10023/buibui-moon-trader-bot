# H15 — USD/JPY carry-unwind state tag (design + pre-committed gate)

Status: DESIGN — pre-registered, written before any outcome was measured.
Date: 2026-08-04.
Source thesis: `docs/plans/thesis-inbox.md` (2026-08-03, @CakeBaBa, video ingest round 8).

**Naming.** H15 is the next entry in the **H-series of hypothesis audits** (H8 M1
indicator conditioning, H9 warning value, H10 partial path, H14 Coinbase premium).
It is **not** ST15, the liquidity-heatmap capture task in the ST-series. The SoT
naming decoder already records three prefix collisions between series; this note
exists so a reader who meets the bare number "15" does not add a fourth.

---

## 1. The question

Does **yen strength** — expressed as a causally-observable weekly state of USD/JPY —
carry information about forward crypto returns, and is that information strong enough
to justify a risk-off position-size governor?

The filed thesis states it as: a weekly false breakout plus bearish engulfing on
USD/JPY, followed by **2+ consecutive weeks of yen strength**, is a severe risk
warning for crypto; **3+ consecutive weeks** is the point where "the landmine has gone
off." The stated precedent is the August 2024 carry unwind, which preceded a ~25% BTC
drawdown.

**What this spec tests, and what it drops.** The consecutive-week counter is
mechanizable and carries the actual claim. The "false breakout + bearish engulfing"
half is **dropped outright**: it cannot be mechanized without handing the researcher
enough degrees of freedom to produce whichever answer they prefer, and dropping it
costs nothing because the counter already expresses the thesis.

---

## 2. Why this axis is worth testing after H14 returned a clean NO

The binding constraint is recorded five times over: the system needs a second strong
edge, and the cheap price-only free-data levers are exhausted. Every shelved sleeve so
far — trend, carry, XS-reversal — re-expressed price or funding data already held.

USD/JPY is **genuinely new data**. It sits outside the frozen 22-detector family and
off the tested-axes list (seasonality, reference-level proximity, structural touches,
carry, trend, XS momentum). The conditioning scoreboard reads five-for-five-plus-one-
amended NO, but every one of those tested a *re-slicing of data already held*. This is
the second cross-asset axis ever put through the gate, after H14.

**The honest prior: H14 is the one axis of this shape that has been tested, and it came
back a clean NO.** The expected outcome here is NO-EDGE. The value is in *closing* the
axis either way — the filed-to-tested conversion rate for ingested hypotheses is
currently ~0, and that, not the hit rate, is the number this work is meant to move.

---

## 3. Data — measured 2026-08-04, not assumed

Source: the keyless Yahoo Finance chart endpoint for `JPY=X`.

**The thesis assumed `yfinance` was available here because the wifey fork runs it in
production. It is not a dependency of this repo.** Measured facts:

| Property | Measured value |
| --- | --- |
| Endpoint auth | none — keyless |
| Request without a `User-Agent` | **HTTP 429** |
| Request with a `User-Agent` | HTTP 200 |
| FX daily bars, 2017-01-02 → 2026-08-04 | 2,494 |
| Null closes | 7 daily, 1 weekly |
| Bars per year | ~261 (five-day FX week) |
| Longest inter-bar gap | 3.04 days (weekend) |

**The audit window is bounded by the crypto side, not the FX side.** Measured in the
live DB: `BTCUSDT` 1d holds **2,523 bars from 2019-09-08 to 2026-08-04**, and all 25
universe symbols have 1d coverage. FX is therefore fetched from **2017-01-01**
(mirroring H14's `_REFRESH_START_MS`) purely so the 52-week rolling z-score is fully
warm before the audit window opens — measured: **139 FX weeks precede 2019-09-08,
against the 52 required**. No crypto observation is sacrificed to warm-up.

**State balance over the actual audit window** (361 FX weeks, 2019-09-08 → 2026-08-04),
measured on the predictor only:

| Run state | Weeks | Share | Approx. tagged crypto days |
| --- | --- | --- | --- |
| `run_le1` — 0 or 1 consecutive yen-strength weeks | 296 | 82.0% | ~2,072 |
| `run_eq2` — exactly 2 | 36 | 10.0% | ~252 |
| `run_ge3` — 3 or more | 29 | 8.0% | ~203 |

**This overturns the filed thesis's own central caveat.** The inbox entry predicted
"n is tiny (carry-unwind events are rare) ... this will almost certainly fail MinTRL
as a standalone tradeable signal." That is not what the data says. A run of ≥2 fires
in **18.0% of weeks** — roughly one week in six — and the *smallest* cell carries ~203
tagged days against a `MIN_N` of 30. **Every cell is well-powered.**

What the thesis actually did was conflate a *common counter state* ("≥2 consecutive
down weeks", 1-in-6) with a *rare regime event* ("the August 2024 carry unwind", n=1).
Those are not the same thing. The consequence for this design is a changed expected
failure mode: not underpowered, but **powered and probably null**.

The August 2024 window does behave as advertised, and it survives the causality rule.
Under our own week derivation the run climbed 1 → 4 across July, standing at **4 as of
the Friday 2024-08-02 close** — so the tag was already live when the 2024-08-05 crash
landed, and reached 5 the following week. This is a real causal hit, not a
retrospective one. It is also **one draw from 65**, and precisely the
vivid-single-instance shape that pre-registration exists to discipline.

**Week derivation is not cosmetic.** Reading Yahoo's own `1wk` feed instead gives a
different run count for this same episode (peak 5 the week of 2024-07-29 rather than 4
at 2024-08-02) and a different full-window balance (17.4% / 6.6% versus 18.0% / 8.0%).
Two defensible anchorings disagree by a whole run step on the thesis's headline event
— which is exactly why §4 makes the week boundary our own explicit decision.

**Method note.** Only the *predictor* series was measured at design time — coverage,
alignment, and state balance. This mirrors H14 §3 and its level-balance report.
Crypto returns conditional on these states were **deliberately not examined**, because
measuring the effect before committing the gate would hollow out the pre-registration.

---

## 4. The alignment trap — the central design finding

Yahoo's `JPY=X` bar timestamps are anchored to **Europe/London midnight, not UTC**.
Measured distribution of daily bar timestamps:

| Timestamp hour (UTC) | Bars | Regime |
| --- | --- | --- |
| 23:00 | 1,157 | BST (London UTC+1) — bar lands on the *previous* UTC date |
| 00:00 | 824 | GMT (London UTC+0) — bar lands on its own UTC date |
| 11:00 | 1 | current partial bar |

Taking the UTC calendar date of a bar therefore yields the **previous** day for ~58%
of the sample, and the error **flips with the DST boundary** — so it is not a constant
offset that would wash out, it is a season-correlated shift that would interact with
any seasonality in the outcome.

The same artifact is visible in the weekday histogram: 232 daily bars fall on a UTC
Sunday and 165 on a UTC Friday, which is one FX week's worth of bars split across the
boundary by DST rather than by any real change in trading hours.

**Two consequences bind the design:**

1. Every bar is keyed by `london_trading_date(ts_ms)` — its Europe/London calendar
   date, which is the true FX trading day.
2. **Weekly bars are derived from daily inside our own code, never taken from Yahoo's
   `1wk` feed.** Yahoo's weekly anchoring inherits this ambiguity; deriving weeks
   ourselves makes the week boundary an explicit, testable decision.

**The week definition, pre-committed.** A week is the **ISO-8601 week of the bar's
London trading date**; that week's close is the **last available daily close within
it**, and the week's effective timestamp is that final bar's London date. ISO weeks are
chosen because they are Monday-anchored, unambiguous across year boundaries, and
independent of both DST and Yahoo's anchoring. Partial weeks — holidays, the current
in-progress week — resolve naturally to their last available close, and the causality
rule in §5 prevents an in-progress week from ever tagging a day.

---

## 5. States — pre-registered, a-priori, causal

Two axes, **never crossed**. Crossing takes the family from 6 cells to 9 plus
complements, and this repo's own H14 spec names that a multiple-testing machine.

**Run axis** (the thesis in its own terms). Count of consecutive weeks in which
USD/JPY closed below the prior week's close. Three mutually exclusive states:
`run_le1`, `run_eq2`, `run_ge3`. Balance as measured in §3.

**Magnitude axis** (the robust form). Causal z-score of the trailing 4-week USD/JPY
log return over a rolling 52-week window, labelled at ±1.0σ: `yen_strong`, `neutral`,
`yen_weak`. Reuses `causal_zscore` unchanged from the H14 implementation.

The two axes are reported side by side and never combined. **If they disagree, that
disagreement is itself the finding** — a counter that fires without magnitude behind
it is the signature of a spurious pattern.

### The causality rule — pre-committed

The tag for UTC day *D* is derived from the **most recent FX week whose close is
strictly before *D* 00:00 UTC**.

FX weeks close Friday ~21:00–22:00 UTC while crypto trades continuously, so week *W*'s
state first becomes applicable on Saturday. Nothing about day *D*, nor about the FX
week containing *D*, may enter *D*'s tag.

This is the single highest-risk correctness property in the design and it gets a
mutation-proof test (§10).

---

## 6. The unit of observation — one day, not a forward window

**One observation per UTC day, non-overlapping.**

**Forward N-day windows are explicitly rejected.** Overlapping windows autocorrelate
and inflate effective n; that is the standard mechanism by which a cross-asset study
manufactures significance. Conditioning non-overlapping daily returns on a state tag
avoids it entirely and preserves the H14 §6 precedent.

### Units — the forward panel is vol-normalised, and this is load-bearing

`audit_guard.evaluate_audit_cells` gates on an effect-size floor `bar` expressed **in
the units of the observation**. H14's unit was per-day mean R, so its `bar = 0.05`
meant 0.05R. A raw BTC log return is not on that scale: measured over the audit
window, the daily log return has **std 3.23%/day** and an unconditional mean of
**+0.072%/day**, so `bar = 0.05` would demand a **5% per-day** mean shift — roughly 70×
the unconditional mean, and unreachable by any state. Applying H14's numeral directly
would reproduce the H8 defect in a new location: a verdict that cannot fire regardless
of the data.

**The forward panel's observation is therefore `return_t / sigma_{t-1}`**, where
`sigma` is a **causal** trailing 30-day realized volatility of daily log returns. This
is the same normalisation R already applies to trades, so both panels become
commensurate and `bar` means the same thing in each. Measured: the normalised series
has std 1.157, the excess over 1.0 being the expected fat tails and volatility
clustering.

**Pre-registered: `BAR_VOL = 0.02` sigma-units** for the forward panel — equivalent to
a 0.065%/day mean shift, or a **0.38 annualised Sharpe** difference. Rationale: it is
the smallest effect that would actually change a sizing decision. For scale, the XS
deploy core runs +1.375 Sharpe and BTC buy-and-hold +0.426 over this window. The
0.05-sigma alternative implies a 0.96 Sharpe shift, which sits above nearly every
regime effect documented in liquid markets and would be near-unreachable in practice.

The **ledger panel keeps `bar = 0.05` in R-units**, unchanged from H14, because its
observation is already an R multiple.

> **Amendment record.** `BAR_VOL` and the vol-normalisation were added on 2026-08-04
> *after* the spec's first approval, when reading `audit_guard`'s signature revealed
> the unit mismatch. Still pre-registered: no conditional outcome had been computed,
> and the only measurements taken were the outcome series' *unconditional* mean and
> dispersion, which fix a unit without revealing an effect.

| Panel | Series | Standing |
| --- | --- | --- |
| **Primary** | BTCUSDT daily log return **÷ causal trailing 30d vol**, UTC day — **n = 2,523**, 2019-09-08 → 2026-08-04 (measured) | decides the verdict |
| Secondary | Equal-weight 25-perp universe daily return | printed; **carries survivorship** — the universe was selected in 2026 and applied back to 2019 — so it decides nothing |
| Secondary | Trade-ledger split (backtest + live), per-UTC-day mean R | gated identically, but **inherits the frozen 22-detector family** whose pooled live avg_r is already −0.085R |
| Descriptive | Per-state realized vol, downside semideviation, worst single day | **reported, never gated** |

BTCUSDT alone is the primary rather than the universe aggregate for two reasons: it
carries no survivorship confound, and the thesis is stated about BTC.

The risk descriptives serve the drawdown half of the thesis and the governor framing.
They are deliberately **outside the gate**, because gating them would silently enlarge
the family — the same mechanism that turns a cell count into a false finding count.

---

## 7. The gate — pre-committed

A cell earns **BUILD** or **AVOID** only by clearing **all seven** legs:

| # | Leg | Threshold |
| --- | --- | --- |
| 1 | Sample size | n ≥ 30 tagged days (`MIN_N`) |
| 2 | Effect | block-bootstrap 95% CI on the cell's mean clears the ±`bar` floor — `BAR_VOL = 0.02` sigma-units (forward panel) or `0.05` R (ledger panel), per §6 |
| 3 | Multiplicity | Holm-adjusted p < 0.05 within the axis family |
| 4 | Track record | **n ≥ MinTRL(0.95)** |
| 5 | Deflation | DSR ≥ 0.95 over the axis sub-family |
| 6 | Overfitting | PBO ≤ 0.5 over the axis sub-family |
| 7 | Stability | early/late sign agreement — split the window at its midpoint; both halves share the effect's sign |

Anything powered that clears neither side is **NO-EDGE**.

**Leg 4 appears in this table because H8 put it in its spec and the code never
implemented it.** Every BUILD cell H8 published cleared a gate silently missing a
pre-registered condition, and no grep could have found it — only reading the spec
against the implementation could. Leg 4 therefore carries its own test (§10), not
merely a mention.

### DSR and MinTRL are directional — the AVOID path must fold to magnitude

Both metrics answer "is this *positive* performance credible". DSR of a raw negative
Sharpe collapses toward 0; MinTRL of one is `inf`.

**The thesis predicts AVOID** — yen strength implies *worse* crypto returns — so the
negative-direction verdict is the one this audit most expects to reach. Gating it on
unfolded metrics would make that verdict **structurally unreachable**: the audit would
report "no negative effect found" regardless of what the data says. This defect
shipped in **H8** and stood for weeks there, where a reliably-negative cell scored DSR
0.0000 against 0.9980 for its mirror-image positive cell (fixed in PR #546).

**H14 already carries the fix**, deliberately and with the reasoning recorded in its
docstrings — `venue_premium.py:401` folds MinTRL via `abs(sharpe)`, and `:278-284`
folds both target and trial Sharpes for DSR. H15 therefore **inherits correct
behaviour** through the §9 extraction rather than introducing it. The rule is restated
here because the extraction moves this code, and a behaviour-preserving move is
exactly the operation during which a subtle guarantee is most easily dropped.

**Rule:** for any negative-direction cell, fold **both** the target Sharpe and the
trial Sharpes to `abs()` before computing DSR and MinTRL.

**Disclosed cost:** folding shrinks trial dispersion in a mixed-sign family, so the
gate becomes marginally **more permissive** than the signed form. The bias runs toward
more passes, never fewer.

### Honest finding count

Each axis has three mutually exclusive states, so its three cell-vs-complement tests
are not independent — `run_le1` versus its complement is very nearly `run_ge2`
sign-flipped. **The verdict doc reports ~2 effective comparisons per axis, not 3.**
A cell count is not a finding count; that is the H8 amendment's core lesson.

---

## 8. Pre-committed decision rule

Committed now, before any outcome has been observed.

| Verdict | Meaning | What happens |
| --- | --- | --- |
| **AVOID** on a yen-strength cell | Thesis confirmed — those days are reliably worse | Tag becomes a risk-off input to Carver position sizing. Spec'd as a **follow-up branch**, not built here. |
| **BUILD** on a yen-strength cell | Reversal — those days are reliably *better* | Report as a reversal finding. Do **not** ship a governor whose sign contradicts its own rationale without a second independent confirmation. |
| **NO-EDGE** | Powered, clears neither side | Thesis falsified. Close the axis in `thesis-inbox.md`, record on the conditioning scoreboard, do not revisit without genuinely new data. |
| **INSUFFICIENT** | n < 30 days | Only reachable for `run_ge3` if coverage shrinks; measured balance says it will not. |

Mapping onto the existing `audit_guard` decisions, as H14 did:

| `audit_guard` | H15 verdict |
| --- | --- |
| `DISABLE` | **BUILD** |
| `ENABLE` | **AVOID** |
| `CONCENTRATE` | **NO-EDGE** |
| `INSUFFICIENT` | **INSUFFICIENT** |

---

## 9. Architecture

| Module | Status | Role |
| --- | --- | --- |
| `analytics/state_audit.py` | **NEW (extraction)** | The shared gate helpers moved out of `venue_premium.py`: `collapse_to_daily`, `causal_zscore`, `family_pbo`, `family_dsr`, `sign_agrees_early_late`, `map_verdict`, plus `BAR`, `ALPHA`, `MIN_N`, `DSR_FLOOR`, `PBO_CEIL`, `MINTRL_CONFIDENCE`, `VERDICT_*`. Behaviour-preserving move, no logic change. |
| `analytics/venue_fetch.py` | extend | `fetch_yahoo_daily(symbol, start_ms, end_ms, *, get=http_get_json)` beside the Coinbase/Binance fetchers. Same injectable `get` so tests stay network-free. Explicit `User-Agent`; **429 raises a named error**, never an empty frame. |
| `analytics/fx_carry.py` | **NEW** | Pure logic, no IO: `london_trading_date`, `build_weekly_from_daily`, `yen_strength_runs`, `label_run_states`, `label_magnitude_states`, `expand_to_days`. |
| `tools/carry_unwind_audit.py` | **NEW** | CLI runner mirroring `premium_state_audit.py` — refresh, coverage header, balance report, cells, gate, table, verdict. Flags: `--db`, `--source {forward,ledger,both}`, `--min-n`, `--refresh`. |

`venue_premium.py` keeps its own series-building; only the shared gate helpers move.

**Why extract rather than copy.** The directional-DSR defect shipped in **two**
separate audits because the gate logic was duplicated rather than shared. A third copy
would be a third site for the same class of defect, and the queued equity-volatility
axis would make a fourth. Consolidating to one implementation is the only structural
fix. The extraction is proven inert by re-running `premium_state_audit` and diffing
against the published H14 table.

Storage: daily `JPY=X` bars **from 2017-01-01** cached into DuckDB (`fx_prices`),
mirroring the existing venue-price refresh pattern, so a re-run does not re-hit a
rate-limited endpoint. The 2017 start is the z-score warm-up margin established in §3,
not an extension of the audit window — the audit window remains crypto-bounded at
2019-09-08.

**Fetcher choice.** A thin fetcher in the `venue_fetch.py` idiom rather than adding
`yfinance`: it matches the house pattern H14 already proved, keeps the injectable-`get`
testability, and avoids a heavy transitive dependency tree for a single series. The
tripwire, pre-committed: **if the raw endpoint starts failing, swap to `yfinance`
behind the same interface rather than hand-patching the fetcher.**

---

## 10. Testing

Standard suite, plus five tests that exist because a specific past defect demands them:

1. **`london_trading_date` across both DST boundaries** — GMT→BST and BST→GMT. Guards
   the measured 58%-of-sample one-day shift (§4).
2. **Causality guard, mutation-proof.** Shifting the tag forward by one week must turn
   the test **RED**. Paired with a **positive control** asserting the perturbation
   actually moved a discriminating row — because "nothing changed in the protected
   window" is satisfied both by correct causality *and* by a perturbation that did
   nothing at all. The xsmom causality guard passed under mutation for weeks on exactly
   this hole, and reading the test could not reveal it; only running the mutation could.
3. **One test per gate leg.** Remove the leg, confirm the verdict changes. This is the
   only defence against a pre-registered leg that the code never implements.
4. **Fetch failure modes.** 429 raises the named error; the 7 measured null daily
   closes and 1 null weekly close are handled explicitly, not silently dropped.
5. **Extraction inertness.** `premium_state_audit` output diffs clean against the
   published H14 table, and the existing `venue_premium` tests stay green. If the table
   moves, the extraction is wrong.

---

## 11. Deliverables

- This spec.
- `analytics/state_audit.py`, `analytics/fx_carry.py`, `venue_fetch.fetch_yahoo_daily`.
- `tools/carry_unwind_audit.py` plus a `make` target.
- Verdict doc at `docs/audits/2026-08-04-h15-usdjpy-carry-unwind.md`.
- `thesis-inbox.md` status moved off `NEW`, whichever way the verdict lands.
- Full Definition of Done: `make lint-py`, `make typecheck`, `make test`,
  `make test-regression`.

**Out of scope, deliberately:**

- Carver sizing wiring — follow-up branch, gated on an AVOID verdict.
- The @tiabtc equity-volatility axis — same cross-asset family, its own spec.
- Any generic state-tag framework — designing against one hypothetical future
  customer would turn a research task into a framework task.
