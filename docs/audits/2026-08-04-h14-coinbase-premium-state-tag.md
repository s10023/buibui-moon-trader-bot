# H14 — Coinbase-Premium Market-State Tag — Verdict (2026-08-04)

**Question.** Does the Coinbase premium — US-spot demand pressure, measured as
Coinbase `BTC-USD` against Binance `BTC-USDT` — carry information about the
forward performance of this system's existing signals, beyond what the stored
OHLCV already encodes? This is the sixth conditioning axis tested (after
regime / session / combo / direction and H8's ten M1 indicator states), and
the first built from a **different venue's** order flow rather than a
re-slice of the same price series the detectors already see.

**Tool.** `tools/premium_state_audit.py` (read-only, `make
buibui-premium-state-audit`) over `analytics/venue_premium.py`. Tags every
historical trade with the Coinbase-premium **state** — level (`elevated` /
`neutral` / `depressed`, a causal 90-day z-score) and 5-day change
(`rising` / `falling`) — as of the **last completed daily close strictly
before entry**, then runs the same pre-committed gate the house audits use:
`analytics/audit_guard.py::evaluate_audit_cells` (bootstrap CI on ±0.05R,
Holm-adjusted significance) plus a family-level DSR ≥ 0.95 / PBO ≤ 0.5 /
n ≥ MinTRL(0.95) / early-late sign-agreement stamp. `bar = 0.05R`,
`alpha = 0.05`, `min_n = 30` **days**, seed 12345.

**Substrate.** `backtest_trades` is primary and gate-deciding (de-biased,
deep, 849,445 trades across the full history). `signal_alert_outcomes`
(live) is corroboration only — per spec §7 it can support a verdict but
never carry one on its own (4,027 trades, thin and pre-N6-catch-up
session-skewed).

**Spec:**
`docs/superpowers/specs/2026-08-04-h14-coinbase-premium-state-tag-design.md`
· **Plan:**
`docs/superpowers/plans/2026-08-04-h14-coinbase-premium-state-tag.md`

---

## Verdict: NO-EDGE on every pre-registered cell — spec §8 branch 2 fires

**All 10 primary `prem_adj` / backtest cells are NO-EDGE.** No state — not
one of the three level buckets, not either direction of the 5-day change —
produced a per-day average trade outcome that was both reliably different
from its complement (bootstrap CI clearing ±0.05R) and survived Holm
correction across the 10-cell family. Nothing reached `audit_guard`'s
`DISABLE`/`ENABLE` decision, so the family-level DSR / PBO / MinTRL stamp
was never even computed for the primary series — correctly printed as `—`,
not fabricated as a fake pass.

Per spec §8 this is **branch 2**, applied verbatim:

> All cells NO-EDGE → record the sixth conditioning NO, and state the
> stronger conclusion it licenses: new venue-price data does not rescue the
> existing signal book either, which moves the diagnosis from the
> conditioning to the signal book itself.

**What that licenses, plainly.** This is not just "one more axis that
didn't work." Every prior conditioning test (regime, session, combo,
direction, and H8's ten M1 states) was built from a re-slice of the same
OHLCV the detectors already consume — so a null result there only ever said
"this system has already extracted what's extractable from its own price
series." H14 was the first candidate state computed from a genuinely
**different venue's order flow**, chosen specifically because the SoT names
"the next edge needs new data" as the binding constraint across five prior
NOs. It also came up null. That is a materially stronger negative than a
sixth OHLCV re-slice: it says the problem is unlikely to be fixed by finding
a cleverer way to slice the existing signals, and the diagnosis should move
toward the signal book itself (entries, exits, the strategies' underlying
edge) rather than toward a seventh conditioning test.

**This is a genuine negative result, reported plainly.** A strategy — or in
this case a conditioning hypothesis — that loses is a result, not a failure
to hide.

---

## The primary result — `prem_adj`, backtest (gate-deciding)

Reproduced independently for this document
(`make buibui-premium-state-audit ARGS="--series prem_adj --source both"`);
every number below matches Task 5's report bit-for-bit.

Coverage: **2021-05-04 → 2026-08-04** (1,919 daily observations). 849,445
backtest trades loaded.

| axis | state | dir | n_days | mean_r | ci_lo | ci_hi | adj_p | stable | verdict |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| change | falling | long | 148 | −0.226 | −0.443 | +0.015 | 0.048 | True | NO-EDGE |
| change | falling | short | 148 | +0.270 | −0.019 | +0.589 | 0.048 | True | NO-EDGE |
| change | rising | long | 151 | −0.041 | −0.254 | +0.157 | 1.000 | False | NO-EDGE |
| change | rising | short | 151 | −0.060 | −0.283 | +0.233 | 1.000 | False | NO-EDGE |
| level | depressed | long | 65 | −0.227 | −0.495 | +0.052 | 0.440 | True | NO-EDGE |
| level | depressed | short | 65 | +0.244 | −0.065 | +0.591 | 0.463 | True | NO-EDGE |
| level | elevated | long | 44 | +0.046 | −0.219 | +0.312 | 1.000 | False | NO-EDGE |
| level | elevated | short | 44 | −0.092 | −0.441 | +0.378 | 1.000 | True | NO-EDGE |
| level | neutral | long | 190 | −0.142 | −0.346 | +0.059 | 0.308 | True | NO-EDGE |
| level | neutral | short | 190 | +0.100 | −0.153 | +0.395 | 1.000 | True | NO-EDGE |

The two `change/falling` cells are the closest either axis gets to
significant (`adj_p = 0.048 < 0.05`) — but their confidence intervals still
straddle the ±0.05R bar (long's `ci_hi = +0.015`, short's `ci_lo = −0.019`),
so `audit_guard` correctly declines to call either one, and neither reaches
the DSR/PBO family check. This is also the first live evidence, in this
document, that the "powered-null → NO-EDGE, not INSUFFICIENT" fix (spec §7,
amendments.md A1) is actually doing its job: with the pre-fix mapping these
two cells — powered at n=148 days, statistically significant by Holm, just
short of the effect-size bar — would have printed as "not enough data,"
which is false. They correctly print NO-EDGE.

**In money terms:** none of these differences are small. `level/depressed`
long trades average −0.23R/trade vs. +0.24R for shorts in the same state —
that would be a real, tradeable split if it held up. It doesn't: the
confidence intervals are wide enough (n in the tens to low hundreds of
*days*, not trades) that "no reliable difference from the complement" is the
honest read, not "a small effect we can't quite see."

Live (`signal_alert_outcomes`, 4,027 trades, corroboration only) shows the
same pattern — NO-EDGE or INSUFFICIENT on every `prem_adj` cell, no
exceptions.

---

## Coverage window and the unit of observation — read this before the table

**`prem_adj` starts 2021-05-04, not full history.** Coinbase's `USDT-USD`
product — needed to strip the peg confound, see below — does not exist
before that date. Do not read the 2021-05-04 → 2026-08-04 window as "the
whole of crypto history"; it is one full bear (2022) and one full bull
(2023–2025) cycle, but it excludes 2017's mania, 2018's bear, and 2020's
COVID crash. `prem_raw` (the public-index definition) has no such
constraint and runs the full 2017-08-17 → 2026-08-04 — it is carried
partly *because* of this coverage gap, not only for the peg reason below.

**n is measured in days, never trades — this is the H10 lesson applied in
advance.** The premium state is market-wide (one BTC-level tag) and daily.
On any given day, all 25 universe symbols across all 4 timeframes share the
exact same state. Tagging the resulting ~161k–268k trades per cell (the
`n_trades(opt.)` column above, dropped from the published table but visible
in the raw audit output) as though they were independent observations would
manufacture a spuriously precise n in the tens of thousands from what is
really a few hundred *days* of genuinely distinct market conditions — the
same defect H10 (weekly partial-path predictiveness) hit and fixed by
collapsing 6,000 symbol-weeks to one observation per calendar week. H14
applies that fix from the start: the **primary statistic is the per-UTC-day
mean R**, averaging every trade that entered that day into one number, then
running the bootstrap and Holm correction over that day-level series. That
is why every cell above shows `n_days` in the tens to low hundreds (44–190
backtest, 11–69 live) rather than tens of thousands. The trade-level count
is printed by the driver purely as an "optimistic, decides nothing"
comparison column and never reaches the gate — it exists so a reader can
see, side by side, that the day-collapse is actually being applied rather
than silently skipped (Task 5's sanity check #3).

---

## The sign inversion — pinned here, enforced by a test, has bitten twice before

`analytics/audit_guard.py` was written to answer a **suppression** question
("should we drop this slice of trades?"), so its two positive-outcome
decisions point the opposite way from "is this state good":

| `audit_guard` decision | H14 verdict | What it means |
| --- | --- | --- |
| `DISABLE` | **BUILD** | this state's trades are reliably *better* |
| `ENABLE` | **AVOID** | this state's trades are reliably *worse* — a suppression candidate |
| `CONCENTRATE` | **NO-EDGE** | the complement is even better; nothing actionable here |
| `INSUFFICIENT` (n < 30 days) | **INSUFFICIENT** | genuinely underpowered |
| `INSUFFICIENT` (n ≥ 30 days) | **NO-EDGE** | powered, but clears neither side |

This exact inversion has caused real defects twice in this codebase before
H14 reused the pattern deliberately: **ST9** and **H8**. `venue_premium.py`
carries the mapping as a pinned docstring and a dedicated unit test so a
future "fix" cannot silently flip it back.

---

## The peg confound (spec §4) — why three series, not one

The public Coinbase Premium Index — the number CryptoQuant, Coinglass, and
therefore any pundit citing "Coinbase premium" actually reports — is the
**raw** price difference between Coinbase `BTC-USD` and Binance `BTC-USDT`.
That raw number silently mixes two unrelated things: genuine US-spot demand
imbalance, and **USDT peg deviation**. If USDT trades at 0.9989 against the
dollar, every Coinbase-USD price shows roughly a −0.11% "premium" that
carries zero demand information — it is a stablecoin artifact, not a signal
about US buyers.

The design spec measured this directly (2026-08-04, ~01:3x UTC): raw
premium −0.105%, peg-adjusted premium +0.004% — **the peg explained
essentially all of the raw reading at that instant**, and over a 2023
sample (n=244) `USDT-USD` had a 10.6 bps standard deviation ranging
0.9985–1.0078, the same order of magnitude as the premium itself. Any claim
built on the raw public index, including from an outside pundit, is
implicitly a mix of both effects unless it explicitly nets out the peg.

That is why this audit carries three series, each with its own coverage
window and — for `prem_raw` / `peg_dev` — its **own** Holm family, never
pooled with the primary:

| Series | Definition | Interpretation | Backtest result (10 cells) |
| --- | --- | --- | --- |
| `prem_adj` (**primary**) | `CB(BTC-USD) / (BN(BTCUSDT) × CB(USDT-USD)) − 1` | peg-neutral US-spot demand | all 10 NO-EDGE |
| `prem_raw` | `CB(BTC-USD) / BN(BTCUSDT) − 1` | the public index, confound included | all 10 NO-EDGE |
| `peg_dev` | `CB(USDT-USD) − 1` | stablecoin stress, a state in its own right | all 10 NO-EDGE |

All three backtest tables — the gate-deciding substrate — are uniformly
NO-EDGE. The disagreement the design anticipated shows up only on the thin,
non-gate-deciding live substrate (next section).

---

## The one live-only hit — and a second one Task 5's report didn't surface

**`prem_raw`, live, `change/rising/short`, n=46 days: mean_r = −0.284, CI
[−0.430, −0.134], adj_p = 0.041, DSR = 0.979, PBO = 0.000, MinTRL = 19.331
(< 46 ⇒ satisfied), sign-stable early/late → verdict AVOID.** This is the
only cell, across all three series and both substrates, that cleared the
**full** pre-committed gate.

Per spec §7, `signal_alert_outcomes` is corroboration only and can never
carry a verdict by itself — this AVOID is real by the gate's own rules, but
it decides nothing on its own. It is a footnote, reported here so it isn't
lost, not a second finding to act on. Two things temper it further:

1. **It is a replication-arm hit, not a primary one.** `prem_adj` — the
   peg-neutral, primary series — shows **no** corresponding effect on the
   same live cell (`change/rising/short`: n=59, mean_r = −0.080, CI
   [−0.300, +0.157], adj_p = 1.000, NO-EDGE). If the raw-index hit were
   really "US demand," the peg-neutral version should show at least a
   directional echo. It does not, which is closer to spec §8 branch 3's
   framing ("peg stress, not US demand") than to branch 1 — except branch 3
   requires the effect to *survive on `prem_raw`'s own gate*, and this is
   live-only corroboration, not the backtest gate-decider, so it cannot
   formally trigger that branch either. It sits between the branches: real
   by the gate's rules, too thin and too substrate-limited to file as
   anything but a footnote.
2. **It is also the one place the `abs(sharpe)` caveat below could matter
   most**, because it is the family's only AVOID-side (negative-Sharpe)
   winner. Before anyone acts on it, it should be re-checked under a
   signed/mirrored DSR treatment rather than the magnitude-folded one used
   here.

**A second, previously unreported hit surfaced when this document's numbers
were reproduced independently.** `peg_dev`, live, `change/rising/short`,
n=47 days: mean_r = −0.297, CI [−0.429, −0.156], adj_p = 0.036, DSR = 0.984,
PBO = 0.000, MinTRL = 18.560, sign-stable → verdict **AVOID**. Task 5's
report smoke-tested `peg_dev` on backtest only ("Backtest: all 10 cells
NO-EDGE") and never printed its live table, so this cell was not previously
surfaced anywhere in the SDD workspace. It is the same `change/rising/short`
cell, on the *other* peg-linked series (`peg_dev` is pure stablecoin
deviation), clearing the same gate with a very similar magnitude
(−0.297R vs `prem_raw`'s −0.284R) and a nearly identical n (47 vs 46 days —
almost certainly overlapping calendar days, since `prem_raw` and `peg_dev`
share the same underlying Coinbase/Binance closes).

**Read together, these two hits — real on `prem_raw` and `peg_dev`, absent
on `prem_adj` — are directionally consistent with the peg-stress framing
the design spec anticipated, on the live substrate only.** That consistency
is suggestive, not dispositive: it is two non-independent readings of what
is likely close to the same underlying days (both series are peg-linked;
`prem_raw = prem_adj`-like-demand-signal `+` peg, and `peg_dev` is exactly
the peg term), on a thin, session-skewed, non-gate-deciding substrate. It
does not change the headline. It is filed here as the honest full picture,
not smoothed into a single footnote, because the task that produced it
explicitly asked for any number that disagreed with or extended the prior
report to be surfaced rather than absorbed.

---

## The `abs(sharpe)` caveat — consistent, bounded, but measurably more permissive

`analytics/research_guards`'s `deflated_sharpe_ratio` and
`min_track_record_length` are built around a **positive** benchmark Sharpe:
fed a raw negative Sharpe, `deflated_sharpe_ratio` collapses toward 0 and
`min_track_record_length` returns `inf`, regardless of how reliable the
negative effect actually is. Left unfixed, that would make `AVOID`
structurally unreachable for any cell, no matter the evidence — a serious
problem for a gate that is supposed to be symmetric between "good state" and
"bad state." `venue_premium.py`'s `_family_dsr` fixes this by taking the
**magnitude** (`abs()`) of both the target cell's Sharpe and every trial
Sharpe in its family before computing the deflation.

This is **consistent** (both sides of the calculation are abs'd, not just
one) and **empirically bounded** — a synthetic check during Task 4 showed a
genuinely weak effect still fails the DSR floor under the abs'd treatment,
so it is not a rubber stamp. But it has a real, directional side effect:
`deflated_sharpe_ratio`'s benchmark scales with the **variance** of the
trial Sharpes it is compared against, and folding a family that straddles
zero into magnitudes **shrinks that variance** (a synthetic three-cell
family `[+0.8, 0.0, −0.8]` has signed std 0.80 but abs'd std 0.46, which
drops the expected-max-Sharpe benchmark from roughly 0.649 to roughly
0.374). A lower benchmark means the DSR gate is **marginally more
permissive** than a fully signed treatment would be, whenever a family
mixes BUILD-bound (positive) and AVOID-bound (negative) cells — which every
H14 family does, since `elevated` and `depressed` share a family with
opposite expected signs.

**State the direction plainly: this bias runs toward *more* AVOID/BUILD
passes, not fewer.** It did not manufacture the primary result — all 10
`prem_adj` cells failed at the CI/Holm stage, before DSR was ever computed,
so the leniency never had a chance to matter there. It is directly relevant
to the one cell where it could matter: the `prem_raw` live AVOID hit above,
which is flagged for a signed/mirrored re-check before anyone treats it as
actionable.

---

## Evidence the gate is real, not decorative

Two things in this run demonstrate the full pre-committed gate (spec §7,
amendments.md A3) is doing real work, not rubber-stamping whatever clears
the first-stage CI/Holm check:

- **`prem_raw`, live, `level/neutral/long`** cleared bootstrap CI (excludes
  zero: [−0.477, −0.066]) *and* Holm significance (`adj_p = 0.048 < 0.05`)
  — the same bar the AVOID hit above cleared — but was **demoted to
  NO-EDGE** by the family DSR check: `DSR = 0.941`, below the 0.95 floor.
  Under the plan's originally-proposed weaker gate (`audit_guard` mapping
  alone, no DSR/PBO/MinTRL/early-late stamp — flagged and overruled in
  amendments.md A3) this cell would have printed as a second live finding.
  It does not, because the full gate caught it.
- **The two `prem_adj` `change/falling` cells** (above) are Holm-significant
  (`adj_p = 0.048`) yet correctly NO-EDGE, not falsely promoted, because
  their confidence intervals do not clear the ±0.05R effect-size bar. Two
  independent gates, both required, both doing their job on real cells in
  this exact run.

---

## How this could still be wrong

NO-EDGE is evidence of absence within the design's stated scope, not proof
of absence in general. Four specific limits, each already argued through in
the spec rather than discovered after the fact:

1. **BTC-level, market-wide state only — not per-symbol.** Spec §3 probed
   Coinbase's listing depth across the 25-symbol research universe and
   rejected a per-symbol premium panel: only 5 bases (BTC, ETH, BCH, XLM,
   XRP) have Coinbase history back to 2019, 12 reach 2022, and the rest are
   thin or very recent. A premium computed per-alt would mostly measure
   listing/liquidity artifacts, not demand. Every trade in this audit,
   across all 25 universe symbols, is tagged with the **same** BTC-derived
   state on a given day. A genuine alt-specific premium effect, if one
   exists, is invisible to this design by construction.
2. **`prem_adj`'s window is real but short relative to crypto's full
   history.** 2021-05-04 → 2026-08-04 spans one full bear and one full
   bull cycle, which is a reasonable regime mix — but it is not 2017–2021,
   and a US-demand effect specific to that earlier period (larger retail
   share, thinner Coinbase liquidity) would not appear here.
3. **Daily granularity cannot see intraday premium swings.** The premium is
   sampled once per UTC day at the close. A premium spike that forms and
   reverts within a single day — plausibly the more actionable kind of
   signal for an intraday detector — is averaged away before the state is
   even computed.
4. **NO-EDGE is a statement about this specific design, not a general
   proof.** A real but smaller or rarer effect than the ±0.05R bar, or one
   that only appears in the state *cross* (e.g. `depressed` AND `falling`
   together) rather than either axis alone, would not be caught here — the
   two axes were deliberately kept uncrossed (10 cells instead of 30) to
   avoid turning this into the "multiple-testing machine" this repo has
   warned about before. That is a considered trade-off, not an oversight,
   but it does mean a cross-axis effect is a genuine blind spot.

---

## Two defects found in H8's code while auditing what H14 inherited from it

H14's gate implementation deliberately mirrors
`analytics/indicator_condition.py` (H8, shipped 2026-07-24) — amendments.md
calls it "the house template." Auditing that template surfaced two defects
in H8's own code, found while building H14, not while re-auditing H8
itself. **Neither is fixed in this branch** — fixing them would mix two
results and needs its own review. They are filed here because H14 cites
H8's NO as prior evidence toward "conditioning axes are 0-for-5," and both
defects weaken that specific prior.

1. **`indicator_condition.py:81-82`** — the same INSUFFICIENT conflation
   H14 found and fixed in itself (spec §7, amendments.md A1):

   ```python
   if decision == "INSUFFICIENT":
       return "INSUFFICIENT"
   ```

   `audit_guard` returns `INSUFFICIENT` both when a cell is genuinely
   underpowered (`n < min_n`) and when it is powered but clears neither
   side of the CI/Holm test. H8 maps both cases to "INSUFFICIENT" — so
   powered, genuinely-null cells in H8's published table may read as "not
   enough data" when they should read as "tested and no effect found." That
   changes how a reader should weigh H8's NO: some fraction of its
   INSUFFICIENT cells could be call-it-closed nulls rather than open
   questions, and the published table cannot currently distinguish them.

2. **`indicator_condition.py:345-353`** — `_family_dsr` uses the **signed**
   Sharpe, with no `abs()`:

   ```python
   def _family_dsr(
       target_r: npt.NDArray[np.float64], family_arrays: list[npt.NDArray[np.float64]]
   ) -> float:
       trial_srs = [_cell_sharpe(a) for a in family_arrays if a.shape[0] >= 2]
       if not trial_srs:
           trial_srs = [_cell_sharpe(target_r)]
       return deflated_sharpe_ratio(
           _cell_sharpe(target_r), max(int(target_r.shape[0]), 1), trial_srs=trial_srs
       )
   ```

   Reproduced directly: `deflated_sharpe_ratio(-1.5, 80, trial_srs=[1.5,
   -1.5, 0])` returns **exactly 0.0**. Since H8's `AVOID` verdict requires
   `dsr >= cfg.dsr_floor` (0.95), and a negative-Sharpe cell deflates to
   0.0 regardless of how reliable the negative effect is, **H8's AVOID
   path is structurally near-unreachable** — the same failure mode this
   audit fixed for itself with `abs(sharpe)` (see above), left unfixed in
   the template it copied from.

**Why this matters beyond a code fix.** H8's published verdict is a NO, and
it is one of the five prior NOs this document's opening argument leans on
("conditioning axes are 0-for-5"). A test that can only detect one of its
two possible directions — H8 could report "this state is good" or nothing,
but essentially never "avoid this state" — has not fully tested the
question it claims to answer. H8's recorded NO should be read as **weaker
than filed**: real for the BUILD direction, largely untested for the AVOID
direction. This does not change H14's own verdict (H14's abs()-fix keeps
both directions live, and the primary result failed at the CI/Holm stage
before DSR was ever computed on any `prem_adj` cell), but it is filed here,
explicitly, as a follow-up for H8 itself — not resolved in this branch.

---

## Definition of Done

No Python was changed on this branch task — only this document was added,
so the Python gates (`make lint-py`, `make typecheck`, `make test`,
`make test-regression`) do not apply and were not run; stating that
plainly rather than silently skipping it.

- **`make lint-md`** — ✓ (0 issues)
- Three `make buibui-premium-state-audit` runs (`prem_adj`, `prem_raw`,
  `peg_dev`, each `--source both`) reproduced independently for this
  document; every `prem_adj` number matches Task 5's report bit-for-bit,
  and the `prem_raw` live AVOID hit matches exactly. One number was not in
  Task 5's report at all: the `peg_dev` live AVOID hit on
  `change/rising/short` (n=47, mean_r=−0.297) — reported above as a finding
  rather than smoothed over, since Task 5 only ran `peg_dev`'s backtest
  table, not its live one.

## Files

- `docs/audits/2026-08-04-h14-coinbase-premium-state-tag.md` (this document)
- `analytics/venue_premium.py` — pure verdict/labelling library
- `analytics/store/venue_prices.py` — `venue_spot_daily` upsert/getter
- `analytics/venue_fetch.py` — Coinbase + Binance-spot daily fetchers
- `tools/premium_state_audit.py` — read-only DB front door / report driver
- `docs/superpowers/specs/2026-08-04-h14-coinbase-premium-state-tag-design.md`
- `docs/superpowers/plans/2026-08-04-h14-coinbase-premium-state-tag.md`

---

## ⚠ AMENDED 2026-08-13 — every NO-EDGE cell here is now INSUFFICIENT

The verdict map above split `audit_guard`'s single `INSUFFICIENT` decision on
`n_supp >= MIN_N`, glossed as *"powered, but clears neither side"*. **That is a
sample-size floor, not power.** The gate this audit actually applies is a bootstrap
CI that must clear ±0.05R, so a genuinely powered null needs the CI to sit
*inside* ±0.05 — the condition that rules a tradeable effect out. Against the
published table:

- **0 of 10 cells had a CI excluding the bar.** Every one was consistent with an
  effect exactly the size this audit was hunting.
- **Median CI half-width 0.274R = 5.5x the bar** (range 4.0x-8.2x).
- **8 of 10 point estimates EXCEEDED the bar**, up to 0.270R = 5.4x.
  `change/falling/long` read -0.226R at n=148 days and printed NO-EDGE.

Corrected in code 2026-08-13 (`audit_guard.CellVerdict.powered_null`) and **re-run:
all ten primary `prem_adj` cells now read INSUFFICIENT.**

**The verdict DIRECTION still stands, on other grounds** — nothing was significant,
the peg-confound decomposition is unaffected, and the long/short sign symmetry
inside one state (`level/depressed` -0.227 long vs +0.244 short) is the signature
of the **direction** axis, already the one OOS-robust axis here, rather than of a
premium mechanism. **What does not stand is "NO-EDGE on 10 cells => the axis is
closed."** The honest reading is that this panel could not resolve an effect worth
trading, so the axis is *undecidable at available n*, not proven dead.

⚠ **Also correct the propagated n.** This audit is quoted elsewhere as NO-EDGE
"over 849,445 trades"; the test ran on **44-190 day-observations**, as the body of
this document says. Quote the test's n.

Rationale and the full arithmetic:
`docs/audits/2026-08-13-h19-equity-btc-axis-power.md`.
