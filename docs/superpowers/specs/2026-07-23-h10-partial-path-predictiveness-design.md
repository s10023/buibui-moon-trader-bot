# H10 — Partial-path predictiveness (design)

**Status:** design approved 2026-07-23, not yet implemented
**Hypothesis:** H10 (SoT `project_todo_master.md`, hypothesis inbox)
**Gates:** ST6 ("is this a bullish week?" framing) and ST7 (idea generation +
invalidation). ST7 is the stated product vision — "the bot filters which charts
matter, identifies the setups" — so this audit sits directly upstream of it.
**Priority:** HIGH. It runs on pure history, so the dark-accumulator problem
(memory `live-system-dark-since-2026-06-24`) does not touch it, and it is
runnable today.

## 1. The question

The weekly and daily cones are conditional **on outcome**. A "bull week" is
defined by its own close, so the bull cone sits above the unconditional one *by
construction* and that separation carries exactly zero predictive information.
`analytics/stats/weekly_cone.py` says so in its own docstring, and the SoT
records ST6 as "NOT shipped with the cone — the conditional-vs-unconditional
separation is tautological, not evidence".

H10 asks the real thing: **given the week's partial path observable at hour `h`,
does it predict the return from `h` to the week's close?**

**Success metric.** A pre-committed, de-biased verdict per hour `h`:
PREDICTIVE / REVERTING / NO-EDGE / INSUFFICIENT — with the consequence for ST6
and ST7 agreed *before* any result exists (§8).

## 2. The null, and why the SoT's original framing had to change

The SoT specifies the test as `P(week closes bull | normalized path at h >
unconditional p50)` compared against the unconditional bull base rate.

**That comparison is against the wrong null and would have produced a false
positive.** The terminal path is the partial path plus what remains:

```text
terminal = path_at_h + remaining
```

Under a driftless random walk the correlation between the partial path and the
terminal value is already `sqrt(h/168)` — about 0.38 by end of Monday and 0.85
by end of Friday. Tested against a base-rate null, hour 120 reads as strongly
predictive while carrying no information whatsoever. It is arithmetic, not
evidence: the same defect class as the cone itself, one level deeper.

**Resolution (approved).** Test the **remaining-path return** directly. Under a
random walk its expectation is exactly zero, so:

- the null needs no simulation or permutation engine,
- `analytics/audit_guard.py`'s mean-vs-±bar machinery applies unmodified,
- and it is the tradeable question an operator actually asks: *"it's Wednesday
  and we're up — does that tell me anything about Thursday through Sunday?"*

### 2.1 The second contaminant: drift

A signed statistic is not automatically drift-free. With `s = sign(path[h])` and
a market that drifts upward, `s = +1` almost always and `remaining` is positive
on average, so `mean(v) > 0` and the audit reads PREDICTIVE. But that return was
available by going long blind — no path information is involved.

**Resolution (approved).** Sign the **demeaned** remaining return:

```text
v = s * (remaining - E[remaining])
```

where `E[remaining]` is an **expanding** mean over weeks strictly before `t`.
The mean must be causal; a full-sample mean is look-ahead, which this repo
guards against by convention (`analytics/forecast/`, `analytics/xsmom/` and
`analytics/carry/` each carry a perturbation test asserting the shift). Under
pure drift this yields `v = s * noise`, whose mean is zero — the contamination
is removed rather than merely disclosed.

## 3. Architecture

Two new files plus one targeted improvement to the module being reused. Mirrors
the established audit pattern (`sl_horizon.py` + `tools/sl_horizon_audit.py`,
`structural_touch.py` + `tools/structural_touch_decay_audit.py`).

| File | Role |
| --- | --- |
| `analytics/weekly_path.py` | **New.** Pure library: no DB, no I/O, no network. Statistic construction, pooling, gate, verdicts. |
| `tools/weekly_path_audit.py` | **New.** DB front door — `duckdb.connect(..., read_only=True)`, renders the report tables. |
| `analytics/stats/weekly_cone.py` | **Modified.** Add a public `week_records()` wrapper. Cone behaviour byte-identical. |
| `Makefile` | **Modified.** Add `buibui-weekly-path-audit`. |

Rejected alternatives:

- *Extend `weekly_cone.py` with the audit itself.* That module is in the live
  `StatsBundle` path feeding the Brief. Adding research code couples a
  user-facing surface to an audit and grows a focused 263-line file into a
  mixed-purpose one.
- *A one-shot script under `tools/` only.* The statistical core would then have
  no unit tests, which is precisely where the defects in this class of work live
  (see §2 — both contaminants are unit-testable).

### 3.1 The shared-normalization invariant

`weekly_cone.py` already computes, per symbol-week: the AWR14-normalized 168-point
path, the week's Monday date, and the direction label. H10 needs exactly those.

Add a public wrapper over the existing private helpers:

```python
def week_records(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    *,
    now_ms: int | None = None,
) -> list[WeekRecord]:
    """The cone's own week population — shared with the H10 audit."""
```

The existing `_WeekRecord` dataclass is renamed to the public `WeekRecord`, since
a public function must not return a private type; the private name stays as a
module-local alias so nothing inside `weekly_cone.py` has to change.
`compute_weekly_cone` and `compute_current_week_path` keep their current bodies;
the wrapper only exposes what they already build. **The audit and the Brief then
cannot disagree on AWR normalization or week-population rules** — the same
invariant `sl_horizon.py` established by delegating ATR to the engine's
`_compute_atr14` rather than recomputing it.

## 4. Population

A symbol-week enters the population only if it satisfies the cone's existing
rule, inherited rather than restated:

- exactly 168 hourly bars (a complete Monday 00:00 → Sunday 23:00 UTC week),
- a strictly positive week open,
- 14 complete prior weeks available to form the trailing AWR window.

**Point-in-time correctness.** A symbol contributes to the weeks it was actually
live. Survival is never required or assumed, so there is no survivorship leak —
the same argument that holds for the XS book's NaN active-set demean. Delisted
names contribute their live weeks and then stop.

Measured coverage as of 2026-07-23: 25 symbols on 1h; BTCUSDT from 2019-09-08
(~358 weeks), ETHUSDT from 2019-11-27, down to HYPEUSDT from 2025-05-30
(~57 weeks). Pooled that is roughly 6,000 symbol-weeks.

## 5. The statistic

Paths are indexed `0..167`, so **hour `h` means index `h-1`** (the close of the
`h`-th completed bar).

Per symbol-week `(i, t)`:

| Step | Definition |
| --- | --- |
| Signal | `s = sign(path[h-1])`; weeks with exactly `0` are dropped |
| Remaining | `rem = path[167] - path[h-1]`, in AWR units |
| Causal baseline | `mu_t = mean(rem)` over weeks strictly before `t` (expanding) |
| Observation | `v = s * (rem - mu_t)` |

### 5.1 Pooling — one observation per calendar week

Pooled symbol-weeks look like `n ≈ 6,000`, but in a week when BTC runs, 24 alts
run with it. Those are nowhere near 6,000 independent draws, and a naive pool
would give a bootstrap CI far too narrow — a false PREDICTIVE by construction.

**Collapse the cross-section first:** average `v` across the symbols live in
week `t`, yielding **one observation per calendar week**, `n ≈ 344`. That is the
honest effective sample. A circular block bootstrap over the resulting week
series then handles week-to-week serial correlation.

BTC-only and majors-only runs are reported alongside as a **breadth contrast**,
matching the house presentation in `forecast_audit.py` and `xsmom_audit.py`.

## 6. The gate

Evaluated at `h ∈ {24, 48, 72, 96, 120}` — end of Monday through end of Friday,
week anchored Monday 00:00 UTC.

The hour set is **a-priori and decision-shaped**: it matches when an operator
actually asks the question, so the Holm family is 5 rather than 168. Testing all
168 hours and reporting the best is exactly the multiple-testing trap the guards
exist to catch; and with `n ≈ 344`, Holm over 168 tests requires the smallest
adjusted p-value to clear `alpha/168`, which is close to guaranteed INSUFFICIENT
regardless of the truth.

| Parameter | Value | Justification |
| --- | --- | --- |
| `bar` | **0.05 AWR / week** | Round-trip fee + slippage ≈ 0.2% against a ~8% weekly range ≈ 0.02 AWR, so the bar is ~2.5× cost and the effect must survive execution with margin. Detection needs a mean near 0.10 AWR — an implied annual Sharpe around 1.4, where XS-solo cleared at +1.375. |
| `alpha` | 0.05 | Repo default. |
| `min_n` | 52 weeks | One year of observations. |
| `n_boot` | `audit_guard` default | Circular block bootstrap. |
| `seed` | `audit_guard.DEFAULT_SEED` | Reproducibility. |

All five values are **pre-committed** — fixed in this document before any result
is computed, never tuned against an outcome.

Consumed via `analytics/audit_guard.py::evaluate_audit_cells` with
`enable_concentrate=False` (there is no kept-vs-suppressed split here; the
CONCENTRATE branch is meaningless for this shape).

Reported alongside, per the `sl_horizon.py` precedent: **DSR**, **PBO** and
**MinTRL** over the 5-hour family. The five hours are reported together and none
is selected as "best", but the stamps make the family size visible.

### 6.1 Verdicts

| Verdict | Condition | Meaning |
| --- | --- | --- |
| **PREDICTIVE** | boot CI low > `+bar`, Holm p < `alpha`, both time halves agree in sign | The partial path predicts continuation. |
| **REVERTING** | boot CI high < `-bar`, Holm p < `alpha`, both halves agree in sign | The partial path predicts **reversal** — a genuine finding, not a null. H9 landed exactly this shape (`w6_consecutive/short`, a gate-clearing REVERSE at +0.084R). |
| **NO-EDGE** | `n >= min_n`, neither branch clears | Tested and found absent. |
| **INSUFFICIENT** | `n < min_n`, **or** the headline clears but the two time halves disagree in sign | Not a null — an unresolved test. |

The early/late time split is a **verdict condition, not a footnote**: the week
series is split at its median week, and both halves' point estimates must carry
the **same sign as the headline** for PREDICTIVE or REVERTING. Magnitude is not
required to match — only sign — because each half has roughly `n/2` observations
and is not individually powered to clear the bar. Any sign disagreement demotes
the hour to INSUFFICIENT with the reason recorded.

## 7. Reported but explicitly non-gating

These inform reading and must never enter the Holm family or move a verdict:

- **Magnitude breakdown** — terciles of `|path[h-1]|`, satisfying the SoT's
  "then magnitude, not only sign" without inflating the family.
- **The full 168-hour curve** — `mean(v)` at every hour, as a descriptive shape.
- **Breadth contrast** — universe vs majors vs BTC-only (§5.1).

## 8. Pre-committed consequences

Agreed before any result exists. This is the part worth real money; it only
counts if it is fixed in advance.

| Outcome | Consequence |
| --- | --- |
| **Any hour PREDICTIVE** | ST6 may proceed — predictive framing is permitted to reach the Brief's language, scoped to the hours that cleared. ST7 (idea generation + invalidation) gets designed. |
| **All five NO-EDGE** | **ST6 and ST7 close permanently.** The Brief's weekly language stays descriptive forever — it may say where the week sits, never what it implies. The F2 card gains no predictive weekly framing. |
| **Any hour REVERTING** | A new hypothesis, filed to the inbox. It is *not* ST6, which asks the continuation question; a reversal edge is a different product. |
| **Mixed / INSUFFICIENT** | ST6 and ST7 stay open but unbuilt. Record the n required to resolve, and revisit when the week count reaches it — the disposition used for ST1's underpowered-positive verdict. |

Expect a null given this repo's base rate. **A null is the valuable outcome
here** — it closes ST6 and ST7 permanently, which is worth real money in avoided
work on the product vision's most expensive branch.

## 9. Testing

Pure unit tests against synthetic paths, no DB. The first three are the defect
guards from §2 — each one fails if the corresponding contaminant returns.

| Test | Assertion |
| --- | --- |
| Continuation series | A constructed persistent path reads clearly positive. |
| Driftless random walk | `mean(v) ≈ 0` — the arithmetic-tautology guard. Must stay near zero at every `h`, including 120. |
| **Pure-drift series** | A monotone-up population must **not** produce PREDICTIVE. The drift-contamination guard (§2.1); red without the demean. |
| Causality perturbation | Bumping week `k`'s return leaves every observation before `k` unchanged. Red without the expanding-mean shift. |
| Cross-sectional collapse | A week with 25 identical symbols yields the same observation as that week with 1 symbol — the pooling in §5.1 is an average, not a count. |
| Index convention | `h = 24` reads index 23 and remaining spans 24..167. Off-by-one is the likeliest silent defect. |
| Population rules | A 167-bar week, a zero-open week, and a week without 14 priors are each excluded. |
| Verdict boundaries | Each of the four verdicts is produced by a constructed input, incl. the time-split-disagreement demotion. |

Plus `make lint-py`, `make typecheck`, `make test`, and `make test-regression`
goldens unmoved — this is additive and read-only, so any golden movement is a
defect, not an intentional behavioural change.

## 10. Out of scope

- **No detector, no signal, no order routing.** Read-only research.
- **No schema change**, no writes to `analytics.db`.
- **ST7's design.** Gated on this verdict, per §8.
- **The permutation-null variant** of the original terminal-direction framing
  (§2). Considered and set aside: the remaining-path test has an exact zero null
  and needs no simulation engine.
- **Daily-cone equivalent.** The same question one horizon down is a follow-up,
  not this spec.

## 11. Known limitations, stated up front

- **One market's history.** Crypto weeks since 2019 are a single draw, and the
  cross-sectional collapse (§5.1) makes `n ≈ 344` honest but not large. A NO-EDGE
  verdict at `bar = 0.05` does not exclude a genuine effect below ~0.10 AWR; §8's
  mixed/INSUFFICIENT row exists for that case.
- **AWR14 normalization is inherited**, not independently validated here. If the
  cone's normalization is wrong, this audit is wrong the same way — which is the
  intended trade for the shared-invariant guarantee in §3.1.
- **Sign-based signal discards magnitude** in the gated statistic by choice (§7
  reports it separately). If the true relationship is strongly linear, the sign
  test is less efficient than a covariance statistic and biases toward a null.
