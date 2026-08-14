# `/research-distil` — design

**Date:** 2026-08-14 · **Status:** approved, not yet implemented
**Companion:** `docs/research/2026-08-14-trading-canon-audit.md` (the audit that motivated it)
**Related:** `.claude/skills/ingest-x/SKILL.md` (the pipeline this feeds), SoT ST11/ST17

---

## 1. What this answers

We now have a way to turn books into agent skills (`book-to-skill`, installed account-level
2026-08-14) and a measured shortlist of repos and papers worth reading. That creates a new
problem, not a solved one: **a source of arbitrarily many plausible trading ideas, pointed at a
system whose bottleneck is testing capacity.**

Two filed measurements make the naive design actively harmful:

- `docs/plans/thesis-inbox.md` states it in its own header — *"the bottleneck is testing
  capacity, not idea capture."*
- `docs/audits/2026-08-12-multi-regime-validation.md` measured that **trial count dominates n,
  and it is not close**: a 21× range of n moves the DSR bar ~10%, while 1 → 320 trials moves it
  21× (+0.049R → +1.035R against a corpus best of +1.196R — illustrative; that tool is not
  reproducible run-to-run — re-derive before relying on it).

A skill that reads three books and emits forty hypotheses therefore does not accelerate
research — it inflates the trial family until every cell is unreachable. **The job is to
throttle, not to amplify.** Every design decision below follows from that sentence.

## 2. Non-goals

- **Not a new pipeline.** `/ingest-x`, `/ingest-video` and `/ingest-charts` already route into
  `thesis-inbox.md` and `mechanics-backlog.md` behind one human review gate. Books, repos and
  papers become a fourth *source* into that same intake.
- **Not a strategy generator.** It emits pre-registered hypotheses with priced power, never
  code, parameters or detectors.
- **Not a replacement for reading.** It consumes already-distilled sources; it does not read raw
  PDFs (that is `book-to-skill`'s job).
- **No `tp_r` or parameter output of any kind.** `/wfo-sweep` remains the only trusted path.

## 3. Architecture

Three units with one purpose each.

### 3.1 `analytics/research_guards/power.py` — new, pure math, no I/O

**Scope correction, made during design after the `context-guard` hook fired.** The first draft
proposed moving the null-licensing criterion here too. That was wrong and would have created a
*third* site. `analytics.audit_guard.powered_null` already owns the containment criterion, was
extracted for exactly that reason on 2026-08-14, and `tools/era_power_price.py` already **calls**
it rather than restating it — `null_is_licensable` and `containment_half_width` are thin local
helpers that delegate. `CLAUDE.md`'s claim that the criterion lives in one function is **true**,
and nothing in this spec touches it. The one-sided best-of-k variant stays where it is, at
`analytics.sl_horizon.negative_claim_licensed`.

What *is* genuinely duplicated is the **required-effect-size inversion**, and the two copies are
not the same implementation:

- `tools/era_power_price.required_sharpe` numerically inverts the production
  `deflated_sharpe_ratio`, on the stated reasoning that inverting the production function "keeps
  the bar and the gate in agreement by construction".
- `tools/multi_regime_power.required_sr` re-derives the PSR z-formula by hand and bisects on it —
  which is the "closed form re-derived by hand" that its sibling's docstring names as *"how a
  spec and a driver come to disagree"*.

**Measured before writing this spec: they agree to 0.0000% across 16 cells** spanning
`n_obs ∈ {200, 1000, 4000, 20000}` × `n_trials ∈ {1, 16, 44, 320}`. So this is a **latent
fragility, not a live defect** — no filed research number is wrong, and the ST28 and
multi-regime figures stand. (The differing z-constants are also both correct: `Z_95 = 1.960`
is the two-sided CI half-width, `Z_GATE = 1.645` the one-sided DSR inversion.) The case for
promotion is that nothing *enforces* the agreement, and the hand-derived copy is the fragile one.

This module therefore exports exactly one function:

```python
def required_sharpe(
    n_obs: int,
    *,
    n_trials: int | None = None,
    sr_variance: float | None = None,
    benchmark_sr: float | None = None,
    skew: float = 0.0,
    kurtosis: float = 3.0,
    target: float = GATE_DSR,
) -> float:
    """Smallest Sharpe whose DSR reaches ``target`` at this ``n_obs``.

    Provide **exactly one** source of the benchmark, mirroring
    ``deflated_sharpe_ratio``'s own contract:
      * ``n_trials`` + ``sr_variance`` — inverts ``deflated_sharpe_ratio``
      * ``benchmark_sr``               — inverts ``probabilistic_sharpe_ratio``
    Returns ``math.inf`` when unreachable at any finite Sharpe.
    """
```

**The two-path signature is load-bearing, not convenience.** The self-review caught that
`required_sr` takes a precomputed `sr0` while `required_sharpe` takes `(n_trials,
sr_variance)`, and `sr0` cannot be uniquely inverted back into that pair — so a single-path
function could not have absorbed both callers, and "derive the arguments before delegating" was
impossible as first written. Both paths still invert a **production** function, so the
principle that the bar and the gate agree by construction is preserved.

`tools/era_power_price.py` delegates via the multiplicity path;
`tools/multi_regime_power.required_sr` delegates via `benchmark_sr`, keeps its own signature,
and preserves its `None`-on-unreachable return by mapping `math.inf → None` at the call site.
Both CLIs are unchanged.

`analytics/research_guards/__init__.py` re-exports `required_sharpe`.

### 3.2 `tools/distil_power.py` — new, thin CLI

The thing the skill actually **runs**. A hand walk is not the walk.

```text
tools/distil_power.py --units {per_trade|per_alert|per_book_day}
                      --n-obs N --n-trials K --sr-variance V
                      [--n-eff E] [--bar R] [--corpus-best R]
```

Prints the required Sharpe and the required effect size in the declared units, beside the
corpus best, and a one-word verdict `REACHABLE` / `UNREACHABLE`. When `--bar` is supplied it
also prints the CI half-width and calls **`analytics.audit_guard.powered_null`** for the
containment verdict — the predicate is called, never restated inline.

**`--units` is mandatory and has no default.** This repo has two filed defects from numbers
that looked portable and silently changed meaning with the panel — the H15 `bar`-units trap, and
the 25-symbol 2.92× deflator being reused on panels whose true deflator is 1.628× or 3.331×.
An undeclared unit is how that recurs.

`--n-eff` applies the correlation deflator. Omitting it on a pooled multi-symbol panel is a
declared error, not a default.

### 3.3 `.claude/skills/research-distil/SKILL.md` — the workflow

Single file, matching `/ingest-x` (329 lines, inline self-contained rubric, one review gate).
Project-level and committed, because gates G1 and G4 cite this repo's verdicts and cost model.

## 4. The four gates

Applied on the **main thread**, in order, cheapest rejection first. A claim must survive all
four. Every rejection is written down with its citation — a rejection is a result.

### G1 — Novelty

Reject anything matching a filed verdict. The SKILL.md carries the closed list inline (so the
gate cannot silently drift when memory is not loaded) and cites
`project_do_not_relitigate.md` as the authority:

meta-labelling and ensemble/confluence scoring (DSR 0.7030) · exit tuning as a P&L lever ·
weekend/DOW removal (DSR 0.672) · HTF agreement · reference-level proximity · conditioning axes
(regime/session/combo/direction, Coinbase premium, USD/JPY carry) · XS reversal · EWMAC regime
attribution · spot-perp CVD · `sl_pct` sweeping.

Rejection text must name the verdict document. **A book will suggest most of these** — G1 is the
gate that does the most work.

### G2 — New information

Does the claim require data we do not already hold? A re-slice of price or order-flow is
rejected citing the 7-for-7 record and the standing conclusion that a new sleeve needs
genuinely new information.

Claims requiring data we *could* obtain free (options/IV, funding term structure) pass with the
acquisition cost named. Claims requiring paid data pass but are tagged `DATA-BLOCKED`, matching
how `thesis-inbox.md` already handles the liquidation-cluster entry.

### G3 — Power

**Must run `tools/distil_power.py` and paste its real output into the row.** No estimate, no
recollection, no arithmetic done by the model. ST28's spec and its driver disagreed on the
detection threshold (`|t| ≥ 2.802` versus `1.96`) and both halves were internally consistent, so
nothing caught it; running the one tracked tool is the only structural defence.

If `sr_variance` for the relevant trial family is unknown, **G3 cannot run and the claim is
`INSUFFICIENT`, not a pass.** Never default it.

`UNREACHABLE` is a successful outcome and is still written — "this cannot be resolved at our n
against that trial family" is exactly the finding that saves a multi-session build.

### G4 — Cost

Restate the expected edge net of the modelled drag `2(fee + slip) · entry / risk` at the stop
width the claim implies. Anything surviving only gross is rejected.

The row must repeat the standing caveat: costs are **modelled, not realised** — raw stays
exactly −1.0 = declared risk, so no figure here expresses gap risk, and every number is an
optimistic bound whose error runs one way.

## 5. Flow

1. **Invoke** — `/research-distil <source> [<source> ...]` where a source is a book-skill slug,
   a repo (path or `owner/name`), a paper (URL or PDF), or a free-text claim.
2. **Extract** — one `sonnet` subagent per source, with a drift-proof self-contained brief (goal,
   success metric, rubric inline; no SoT or memory re-reads). It returns **candidate claims
   only**: `{claim, mechanism, data_required, author_effect_size, citation}`. It applies no
   gates and issues no verdicts. Book bytes never enter main context.
3. **Gate** — main thread applies G1→G4, running `tools/distil_power.py` for each G3.
4. **Review** — **ONE** consolidated digest for the whole batch, listing survivors *and*
   rejections with citations. Nothing is written before the operator approves.
5. **Route** — alpha and signal claims to `docs/plans/thesis-inbox.md`; execution, cost and
   mechanics claims to `docs/plans/mechanics-backlog.md`. Both are gitignored, matching the
   existing streams.

Each written row carries: the claim, its source citation, all four gate results verbatim, the
pasted `distil_power.py` output, and a **Decision Log** naming the observable that reverses it.

A **spec doc is generated only on promotion**, when the operator picks a row to run. Untested
ideas do not enter `docs/superpowers/specs/`, because the spec-reconcile denominator is the full
corpus and every spec costs someone a look.

## 6. Refusals — the throttle

The skill must refuse to:

- Write any row whose G3 output was not pasted from a real tool run.
- Write more than **three** survivors per run. If more survive, rank by priced power and write
  the top three — and **log every dropped item with its reason**. A silent cap reads as
  "covered everything" when it did not.
- Re-open a claim matching G1 without the operator explicitly overriding, in writing, in that
  session.
- Emit parameters, `tp_r`, thresholds or code of any kind.

## 7. Testing and acceptance

**`tests/test_research_guards_power.py`:**

- `required_sharpe` is monotone increasing in `n_trials` and decreasing in `n_obs`.
- It returns `math.inf` rather than a large finite number when unreachable.
- **The equivalence pin:** the 16-cell grid measured during design
  (`n_obs ∈ {200, 1000, 4000, 20000}` × `n_trials ∈ {1, 16, 44, 320}`, with `sr_variance` fixed
  at `0.0` when `n_trials=1` and `0.05` otherwise — a 4×4 grid, not a free cross-product with
  `sr_variance`) is committed as a table of expected values. Both delegating call sites must
  reproduce it. This is what turns today's 0.0000% agreement from an observation into an
  invariant.
- Known-value checks pinned to the pre-promotion outputs of both existing tools.

**The real proof the refactor is safe** — **AMENDED during execution.** As written this leg
required both `tools/era_power_price.py` and `tools/multi_regime_power.py` to reproduce
byte-identical reports across the promotion. `era_power_price` passed: its report is
byte-identical, 2,849 bytes, before and after. `multi_regime_power` cannot satisfy this as
written — it is **nondeterministic run-to-run**, independent of the promotion: two runs of
identical code returned pooled `mean_r` −0.0705 vs −0.0824 at fixed `n=165,409`, because
`any_value(pnl_r)` picks an arbitrary row per dedup key. A byte-identical check on that tool
would fail on unrelated grounds and prove nothing about the refactor.

For `multi_regime_power`, the byte-identical leg is replaced by a **function-level equivalence
check**: old `required_sr` vs new `required_sharpe` (via the `benchmark_sr` path) across a
288-cell grid spanning the real panel sizes (152 / 1,038 / 3,447 / 4,038) and real skew/kurtosis
pairs the tool actually uses — 288 agreed, 0 mismatched — plus the algebraic equivalence
argument in §3.1 (both invert the same production PSR formula). Recorded here so a later reader
does not read the missing byte-diff as a skipped leg.

**Gate chain:** `make lint-py`, `make typecheck`, `make test`. The diff touches
`analytics/research_guards/`, `tools/` and `tests/` — **not** the backtest surface
(`analytics/backtest/`, `analytics/strategies/`, `analytics/signal_config.py`,
`config/*signal_watch*.toml`, `config/strategy_params.toml`, `tests/fixtures/`, `poetry.lock`),
so `make test-regression` does not apply and that branch is to be stated explicitly.

`make docs-index` after this spec lands, or `tests/test_docs_index.py` fails.

**Skill acceptance:** one live dry run against a real source, which must produce at least one
`UNREACHABLE` or G1 rejection. A first run that emits three glowing survivors is evidence the
gates are not biting.

## 8. Failure modes

| Failure | Handling |
| --- | --- |
| Named book-skill not installed | Name it and stop. Do not fall back to reading a PDF. |
| `sr_variance` unavailable | `INSUFFICIENT`. Never default. |
| Subagent returns malformed JSON | Tolerate at the consuming end. A system-prompt directive is not a parser — the "bare JSON, no fence" rule broke 1-in-6 despite being explicit. |
| `distil_power.py` errors or is absent | Abort the run. Do not degrade to an estimate. |
| More than three survivors | Rank, write three, log the rest with reasons. |
| Source is a repo, not prose | Same flow; the subagent reads code and returns claims about *mechanism* (how execution, sizing or routing is done), which route to `mechanics-backlog.md`. |

## 9. Files

**New:** `analytics/research_guards/power.py` · `tools/distil_power.py` ·
`.claude/skills/research-distil/SKILL.md` · `tests/test_research_guards_power.py`

**Edited:** `analytics/research_guards/__init__.py` (exports) · `tools/era_power_price.py` and
`tools/multi_regime_power.py` (delegate) · `CLAUDE.md` (cadence line only — the skill list is
deliberately not duplicated there)

## 10. Decision Log

| # | Decision | Rejected | Why | Observable that REVERSES it |
| --- | --- | --- | --- | --- |
| 1 | Promote **only** `required_sharpe` into `research_guards/power.py`; leave the null criterion alone | (a) Shell out to `tools/era_power_price.py` as-is; (b) the first draft's plan to move `null_is_licensable` here too | Two inversions of the effect-size bar exist and one is hand-derived, which its sibling's docstring names as how a spec and a driver come to disagree — `passes_gate` has the identical fourth-copy history. The null criterion is **already** consolidated in `audit_guard.powered_null`, so moving it would have created a third site | The byte-diff test fails, or the 16-cell equivalence pin cannot be satisfied by both call sites — either means the semantics were never actually shared and the abstraction is wrong |
| 2 | Project-level skill in buibui | Account-level, shared with wifey like `book-to-skill` | G1 and G4 cite this repo's verdict corpus and drag formula; a generic version is weaker in both repos | wifey's port changes less than ~20% of the SKILL.md — the gates were shared after all, so promote |
| 3 | Hard cap of three survivors per run | Unlimited output with ranking | Testing capacity is the bottleneck and trial count dominates n | The dropped-item log shows we repeatedly discard claims that are later independently re-discovered and pass — the cap is throttling signal |
| 4 | Inbox row now, spec only on promotion | A pre-registered spec for every survivor | The spec-reconcile denominator is the full corpus; untested ideas inflate it and each costs a reviewer a look | Promoted rows routinely need re-derivation because the inbox row lost pre-registration detail |
| 5 | Subagent extracts claims only; gates run on the main thread | Subagent applies the gates itself | The gates need the verdict corpus and a tool run; a drift-proof inline brief carrying all of it would be enormous, and a subagent cannot be relied on to run the tool | Main-thread gating becomes the context bottleneck on a normal-sized batch |
| 6 | G3 must paste real tool output | Let the model estimate required power | A hand walk is not the walk; ST28's spec and driver disagreed on the threshold while both halves stayed internally consistent | Nothing reverses this. If the tool is unavailable the run aborts rather than degrading |
| 7 | `--units` mandatory, no default | Infer units from the panel | Two filed defects from portable-looking numbers that changed meaning with the panel: the H15 `bar` trap and the 2.92× deflator reused on 1.628×/3.331× panels | A units registry exists that makes inference provably safe |

## 11. Open questions

None. Every branch above is decided.
