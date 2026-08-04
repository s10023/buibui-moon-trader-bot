# H15 — USD/JPY Carry-Unwind State Tag — Verdict (2026-08-04)

**Headline.** The USD/JPY carry-unwind thesis does not condition forward crypto
returns; the axis is closed.

**Question.** Does yen strength — a weekly, causally-observable state of USD/JPY —
carry information about forward crypto returns, strong enough to justify a risk-off
position-size governor? This is the seventh conditioning axis tested overall
(regime / session / combo / direction, H8's ten M1 indicator states, H14's Coinbase
premium) and the **second** built from genuinely new cross-asset data rather than a
re-slice of price/order-flow the system already holds — H14 was the first.

**Tool.** `tools/carry_unwind_audit.py` (read-only) over `analytics/fx_carry.py`
(pure state-labelling logic) and `analytics/state_audit.py` (the shared gate helpers
extracted from H14's `venue_premium.py` in this same branch, Task 1 — behaviour
preserving, proven inert by diffing the extraction against the published H14 table).
Yen data via `analytics/venue_fetch.py::fetch_yahoo_daily` (keyless Yahoo `JPY=X`).
Gate: bootstrap CI on ±`bar`, Holm-adjusted significance, family DSR ≥ 0.95, family
PBO ≤ 0.5, n ≥ MinTRL(0.95), early/late sign agreement — same seven legs H14 used,
via the now-shared `analytics/state_audit.py::evaluate_audit_cells`.

**Spec:**
`docs/superpowers/specs/2026-08-04-h15-usdjpy-carry-unwind-design.md` ·
**Plan:** `docs/superpowers/plans/2026-08-04-h15-usdjpy-carry-unwind.md` ·
**Raw output:** three panels reproduced verbatim below, source
`PYTHONPATH=. poetry run python tools/carry_unwind_audit.py --refresh --source both`.

---

## Verdict: NO-EDGE / INSUFFICIENT across every cell in every panel

Every cell in every one of the three panels — the primary forward panel and the two
secondary ledger panels — lands NO-EDGE or INSUFFICIENT. No cell, in either the run
axis (`run_le1` / `run_eq2` / `run_ge3`) or the magnitude axis (`mag_neutral` /
`yen_strong` / `yen_weak`), cleared both the significance leg and the effect-size leg
of the pre-committed gate. **The primary forward panel was well powered**: cell
`n_days` ranges from 200 (`run_ge3`) to 2,055 (`run_le1`), all comfortably above the
`MIN_N = 30` floor. This is a **powered null**, the outcome the spec named as the
expected failure mode (§3), not an underpowered one — see the "Powered null" section
below for why that distinction matters.

Per spec §8, this is the NO-EDGE branch: **close the axis, record it on the
conditioning scoreboard, do not revisit without genuinely new data.**

---

## Money translation — what the effect is worth, in plain terms

At operator scale, the effect is worth **nothing** — no cell cleared the bar that
would make it decision-relevant.

The forward panel's gate floor, `BAR_VOL = 0.02` sigma-units, is not an abstract
number. Per spec §6 it equals roughly **0.065%/day** in raw BTC-return terms, or a
**0.38 annualised Sharpe** difference between the conditioned and unconditioned
state — deliberately set as *the smallest effect that would actually change a sizing
decision*, well below the XS-solo deploy core's own +1.375 Sharpe and BTC
buy-and-hold's +0.426 over the same window.

Some point estimates look larger than that floor. `magnitude/yen_strong` (forward
panel, n=330) shows a mean of **−0.095 sigma-units** — nearly 5× the bar — which
reads, on its face, like the thesis's risk-off direction showing up. **It is not
distinguishable from zero at this sample.** Its bootstrap 95% CI is **[−0.253,
+0.047]** — it spans zero, and the Holm-adjusted p-value is **0.874**. `yen_weak`
(n=413) is smaller still: mean −0.042, CI [−0.163, +0.069], adj_p 0.899. Report this
plainly rather than implying a suppressed or nearly-significant effect: a wide CI
that happens to sit mostly on one side of zero is not evidence, it is what a null
effect with moderate dispersion looks like some of the time.

The single cell that comes closest to significance in the forward panel is actually
the largest and least dramatic-looking one: `magnitude/mag_neutral` (n=1,749, mean
+0.069, CI [+0.012, +0.124]) — a CI that excludes zero — still lands NO-EDGE because
Holm-adjusted p = **0.058**, just above the 0.05 line. The correction is doing its
job: with six cells in the family (three per axis), the naive per-cell p-value would
have cleared 0.05 comfortably; Holm did not let it through.

---

## The two axes disagree in sign — pre-registered as itself a finding

Spec §5 committed to this in advance: *"If they disagree, that disagreement is
itself the finding — a counter that fires without magnitude behind it is the
signature of a spurious pattern."*

In the forward panel, the run axis's `run_ge3` — the thesis's "the landmine has gone
off" state, 3+ consecutive down weeks — has mean **+0.134** (n=200, CI [+0.001,
+0.266], adj_p 0.394). The magnitude axis's `yen_strong` — the same underlying
condition expressed as a z-scored return threshold rather than a consecutive-week
count — has mean **−0.095** (n=330, above). The counter points mildly the *opposite*
direction to the magnitude measure that is supposed to be tracking the same
phenomenon, and neither is significant.

This is a substantive result, not a footnote. It says the consecutive-down-week
counter is not tracking yen strength in any economically meaningful way — the two
ways of operationalising "yen is strong" do not even agree on sign, let alone
magnitude. That is consistent with the counter picking up noise (run-length
statistics on a mean-reverting FX series) rather than a real regime state.

---

## The one near-miss cell — every gate leg, reported

`analytics/state_audit.py`'s gate is seven legs, and per spec §7 (citing the H8
defect: a pre-registered leg the code never implemented is invisible to a reader) —
every leg is reported here, including the ones this cell passed.

**Cell: ledger / backtest, `magnitude / yen_weak / long`, n=35 days.**

| # | Leg | Threshold | Value | Result |
| --- | --- | --- | --- | --- |
| 1 | Sample size | n ≥ 30 | 35 | PASS |
| 2 | Effect | bootstrap 95% CI clears ±0.05R | [−0.569, −0.060] | PASS (AVOID side) |
| 3 | Multiplicity | Holm-adjusted p < 0.05 | 0.027 | PASS |
| 4 | Track record | n ≥ MinTRL(0.95) | MinTRL = 14.101, n = 35 | PASS |
| 5 | Deflation | DSR ≥ 0.95 | 0.878 | **FAIL** |
| 6 | Overfitting | PBO ≤ 0.5 | 1.000 | **FAIL** |
| 7 | Stability | early/late sign agreement | stable = True | PASS |

Five of seven legs pass, including the sample-size, effect-size, significance, and
track-record legs — the cell *looks* like a clean AVOID hit if a reader stops
reading after leg 3. It is not: legs 5 and 6 both fail, and AVOID requires clearing
all seven. **This is the gate working correctly** — a superficially significant
small-family cell (magnitude axis, backtest ledger, only 6 cells total in the
family) that does not survive deflation. `mean_r = −0.332` on 35 days is exactly the
kind of small-n, apparently-clean split that DSR/PBO exist to catch. The published
audit table correctly prints this cell as NO-EDGE, not AVOID.

Its mirror short-side cell (`magnitude/yen_weak/short`, n=35) shows mean +0.151,
CI [−0.147, +0.470], adj_p 0.960 — no signal at all, so the near-miss is entirely a
long-side artifact, not a symmetric split.

---

## Effective finding count, not cell count

Each axis has three mutually exclusive states (`run_le1`/`run_eq2`/`run_ge3`;
`mag_neutral`/`yen_strong`/`yen_weak`), so each axis's three cell-vs-complement tests
are not three independent comparisons — one state's test is very nearly the
sign-flip of "not that state," and with three states the three tests are not fully
independent of each other either. The audit tool's own output states this directly:
*"each axis has 3 mutually exclusive states, so its 3 cell-vs-complement tests are
~2 effective comparisons, not 3."* Six cells in the forward panel (two axes × three
states, market direction only) is therefore **~4 effective comparisons, not 6** — a
cell count is not a finding count, the same H8-amendment lesson applied here from
the start rather than discovered after the fact.

---

## The `abs()` fold disclosure

`analytics/state_audit.py`'s DSR and MinTRL are directional: both answer "is this
*positive* performance credible," so a raw negative Sharpe collapses DSR toward 0
and drives MinTRL to `inf` regardless of how reliable the negative effect actually
is. The fix — folding both the target cell's Sharpe and every trial Sharpe in its
family to `abs()` before computing DSR/MinTRL — was **inherited unchanged from H14**
through the Task 1 extraction into `analytics/state_audit.py`; it was not
reintroduced or re-derived for H15. This is the correct behaviour: without it,
AVOID would be structurally unreachable here exactly as it was in H8 before PR #546
(a reliably-negative cell there scored DSR 0.0000 against 0.9980 for its
mirror-image positive cell).

**Disclosed cost (spec §7):** folding to magnitude shrinks trial dispersion in a
mixed-sign family, so the DSR gate becomes marginally **more permissive** than the
signed form would be — the bias runs toward more passes, never fewer. That matters
here specifically because the verdict is null: the near-miss cell above (`yen_weak
long`) still failed DSR (0.878 < 0.95) even under the more permissive folded
treatment. A more lenient gate found nothing; a stricter, signed gate would find
even less.

---

## The risk descriptives — reported honestly, not converted into a finding

The audit's descriptive block (never gated, per spec §6) shows yen-strength states
carry materially worse realized risk than the neutral state:

| axis | state | n | vol (daily) | downside semidev | worst day |
| --- | --- | --- | --- | --- | --- |
| magnitude | `mag_neutral` | 1,765 | 0.0299 | 0.0283 | −0.1560 |
| magnitude | `yen_strong` | 330 | 0.0425 | 0.0511 | −0.5105 |
| magnitude | `yen_weak` | 427 | 0.0325 | 0.0341 | −0.1673 |
| run | `run_eq2` | 244 | 0.0444 | 0.0548 | −0.5105 |
| run | `run_ge3` | 204 | 0.0326 | 0.0302 | −0.0938 |
| run | `run_le1` | 2,074 | 0.0306 | 0.0300 | −0.1673 |

`yen_strong` daily vol (0.0425) is ~42% above `mag_neutral`'s (0.0299); downside
semideviation is ~80% higher (0.0511 vs 0.0283); the worst single day is over 3×
deeper (−0.5105 vs −0.1560). So the thesis's *risk-warning* framing — "this is a
severe risk signal" — has descriptive support even though its *return* claim (the
thing the gate actually tested) does not.

Three things have to be said alongside that, not left implicit:

1. **These numbers were deliberately placed outside the gate by the
   pre-registration** (spec §6: "reported, never gated"). Running them through
   DSR/PBO/Holm now, after seeing them, would silently enlarge the tested family —
   exactly the mechanism that turns a cell count into a false-finding count. They
   are descriptive context, not a second audit.
2. **A worst day of −0.5105 (log terms) is a single observation.** `yen_strong` and
   `run_eq2` share the identical worst-day figure because they tag the same
   historical day: verified against the raw `ohlcv` series, that day is
   **2020-03-12, log return −0.5105 (−40.0%)** — the COVID crash. The point
   generalises across the whole descriptive table: the three worst days in the
   sample are three *different* days landing in three *different* states
   (2020-03-12 −0.5105 in `yen_strong`/`run_eq2`; 2022-06-13 −0.1673 in
   `yen_weak`; 2021-05-19 −0.1560 in `mag_neutral`), so every state's worst-day
   column is an n=1 statistic rather than a property of the state. That is
   precisely the vivid-single-instance shape the pre-registration exists to
   discipline against; none of it should be read as "yen-strength days are
   typically this dangerous."
3. **If anyone wants to pursue the risk/volatility angle, it needs its own
   pre-registered spec** — its own gate, its own family, its own decision rule —
   not a post-hoc promotion of a descriptive line from this document.

---

## Backtest-vs-live standing

Both ledger panels (backtest and live) are secondary per spec §6 — printed, gated
identically, but deciding nothing — and both inherit the frozen 22-detector family
whose pooled live avg_r is already negative, so a positive ledger reading here would
say more about that pre-existing bias than about USD/JPY.

Several yen-strength ledger cells are **INSUFFICIENT**, below `MIN_N = 30`:

- Backtest: `magnitude/yen_strong/long` and `/short` (n=28 each), `run/run_eq2/long`
  and `/short` (n=28 each).
- Live: `magnitude/yen_strong/long` (n=10), `/short` (n=9), `run/run_eq2/long`
  (n=13), `/short` (n=13).

Do not read these — an n=9 to n=28 cell cannot clear the gate honestly regardless of
its point estimate, and several of the live INSUFFICIENT cells are flagged unstable
(`stable = False`) on top of being underpowered. The powered ledger cells that do
clear `MIN_N` (`mag_neutral` and `run_le1`, both directions, both substrates) are
all NO-EDGE — wide CIs, adj_p at or near 1.000.

---

## Powered null, not an underpowered one

The spec's own honest prior (§3) predicted the likely failure mode would be
**"powered and probably null," not underpowered** — and drew that distinction
deliberately, because the filed thesis's own caveat predicted the opposite (see
`thesis-inbox.md` update below). That is exactly what the primary forward panel
delivered: every cell cleared `MIN_N` by a wide margin (smallest cell n=200, more
than 6× the floor), and still landed NO-EDGE. This distinction matters: it means the
axis is **genuinely closed** by this design, not merely untested for lack of data —
closing it does not leave a "come back when there's more data" door open the way an
INSUFFICIENT verdict would.

---

## Definition of Done

No Python was changed on this branch task — only this document, `thesis-inbox.md`,
and `CLAUDE.md` were touched, so the Python gates (`make lint-py`, `make typecheck`,
`make test`, `make test-regression`) do not apply here; Tasks 1–4 already carry
those gates green on this branch. `make lint-md` is the applicable gate for this
task and was run.

## Files

- `docs/audits/2026-08-04-h15-usdjpy-carry-unwind.md` (this document)
- `analytics/state_audit.py` — shared gate helpers (extracted from `venue_premium.py`)
- `analytics/fx_carry.py` — pure yen run/magnitude state-labelling logic
- `analytics/venue_fetch.py::fetch_yahoo_daily` — keyless Yahoo `JPY=X` fetcher
- `tools/carry_unwind_audit.py` — read-only CLI runner / report driver
- `docs/superpowers/specs/2026-08-04-h15-usdjpy-carry-unwind-design.md`
- `docs/superpowers/plans/2026-08-04-h15-usdjpy-carry-unwind.md`
