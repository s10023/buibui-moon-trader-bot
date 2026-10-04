---
name: research-distil
effort: high
description: >
  Distil books, GitHub repos and papers into AT MOST THREE power-priced,
  pre-registered hypotheses per run, routed into the existing thesis-inbox /
  mechanics-backlog intake behind ONE human review gate. Four gates reject on
  novelty (a filed no-edge verdict), new-information (a re-slice of held
  price/order-flow is 7-for-7 dead), power (must RUN tools/distil_power.py,
  never estimate) and cost (net of the modelled drag). "Unreachable, do not
  build" is a SUCCESSFUL output. Invoke when the user says "/research-distil",
  points at a book-to-skill slug, names a repo or paper to mine, asks "what
  should we test from this book", or wants research turned into a testable
  hypothesis. Spec: docs/superpowers/specs/2026-08-14-research-distil-design.md.
---

# Research distil

Turn a distilled source into a small number of hypotheses this system can
actually resolve — and say so plainly when it cannot resolve any.

## Why this throttles

`docs/plans/thesis-inbox.md` opens with the sentence that decides this skill's
entire shape:

> the bottleneck is testing capacity, not idea capture

And `docs/audits/2026-08-12-multi-regime-validation.md` measured why. **Trial
count dominates n, and it is not close:** a 21× range of n moves the DSR bar
about 10%, while going from 1 to 320 trials moves it **21×**.

So the obvious version of this skill is actively harmful. Reading three books
and emitting forty hypotheses does not accelerate research; it inflates the
trial family until every cell is unreachable, including ones that would have
passed on their own.

**The job is to throttle, not to amplify.** A run that reads a 600-page book
and emits one hypothesis has done its job. A run that emits zero, with reasons,
has also done its job.

## Flow

1. **Invoke** — `/research-distil <source> [<source> ...]`. A source is a
   book-to-skill slug (`~/.claude-personal/skills/<slug>`), a repo (local path
   or `owner/name`), a paper (URL or local PDF), or a free-text claim.
2. **Extract** — dispatch ONE `sonnet` subagent per source with the inline
   rubric below. It returns **candidate claims only**, applies no gates, issues
   no verdicts. Book and repo bytes never enter the main context.
3. **Gate** — the main thread applies G1 → G4 in order, cheapest rejection
   first, running `tools/distil_power.py` for every G3.
4. **Review** — present **ONE** consolidated digest for the whole batch:
   survivors *and* rejections, each with its citation. Write nothing before the
   operator approves.
5. **Route** — on approval only.

## Inline extraction rubric

Paste this verbatim into each extraction subagent. It must be self-contained:
the subagent does not read the SoT, memory, or this file.

> You are extracting candidate claims from one source for a trading-research
> intake. Return claims ONLY — no verdicts, no rankings, no recommendations,
> and no judgement about whether a claim is good.
>
> A claim qualifies only if it is **falsifiable against price, volume,
> order-flow, funding, or an external data series**. Discard advice about
> discipline, psychology, position-sizing philosophy, or anything that cannot
> be tested on a data series.
>
> Return a JSON array. Each element:
>
> - `claim` — one sentence, in the author's own terms.
> - `mechanism` — why the author says it works. `null` if none is given. A
>   claim with no mechanism is still valid; say so rather than inventing one.
> - `data_required` — the series needed to test it, named concretely
>   (e.g. "daily OHLCV per symbol", "30-day implied vol index", "funding rate
>   8h"). "Market data" is not an answer.
> - `author_effect_size` — any number the author states (Sharpe, win rate,
>   annual return, t-stat), verbatim with its units. `null` if none.
> - `citation` — chapter/section/page, or file and line for a repo.
>
> At most 12 claims per source. Prefer the specific over the general:
> "12-month momentum, skipping the most recent month, top decile" is a claim;
> "trends persist" is not.
>
> Return bare JSON with no prose and no code fence. If you cannot comply
> exactly, return the JSON anyway — malformed output is tolerated at the
> consuming end, missing output is not.

## The four gates

Applied on the main thread. A claim must survive all four. **Every rejection is
written down with its citation — a rejection is a result, not a discard.**

### G1 — Novelty

Reject anything already answered. This gate does the most work, because a
trading book will suggest most of these. Authority: `project_do_not_relitigate.md`.

| Closed | Verdict |
| --- | --- |
| Ensemble / confluence scoring mapped to sizing | FAILS, DSR 0.7030 — `2026-08-11-ensemble-walkforward.md` |
| Exit tuning as a P&L lever | ANSWERED; time-stop curve monotone negative |
| Weekend / day-of-week removal | FAILS, DSR 0.672; effective n is 21 book-days |
| HTF agreement as a filter | INVERTED — it is a counter-trend book |
| Reference-level proximity triggers | NO-EDGE |
| Conditioning axes (regime, session, combo, direction) | 6-for-6-plus-one-amended |
| Coinbase premium (H14), USD/JPY carry (H15) | NO-EDGE, both genuinely new sources |
| XS reversal | −2.9, negative even at zero cost |
| EWMAC regime attribution | NO |
| Spot-perp CVD divergence | All 10 pre-registered trials FAIL |
| `sl_pct` / flat-2% stop sweeping | Already swept; ATR-widening CONFIRMED-BAD at 15m |

Rejection text must name the verdict document.

⚠ **Meta-labelling in general is NOT on this list.** What was tested was one construction: a
16-cell confidence score mapped to position sizing. A proposal that uses a second model to
filter signals by a *different* construction is not covered by that verdict — send it to G2
and G3 rather than rejecting it here. Over-wide G1 rows are how a gate stops being a gate.

⚠ Read a filed "no edge" as
"no effect was FOUND", never "an effect was RULED OUT" — several were re-run and
came back INSUFFICIENT rather than negative. That does not reopen them here; it
governs how the rejection is worded.

### G2 — New information

Does the claim need data we do not already hold?

- **A re-slice of held price / order-flow → REJECT**, citing that the cheap
  price-only free-data levers are exhausted.
- **Free-but-unheld data → PASS**, naming the acquisition cost and exact series.
- **Paid data → PASS, tagged `DATA-BLOCKED`**, matching how the inbox already
  carries the liquidation-cluster entry.

⚠ New data is **necessary, not sufficient**. H14 and H15 were both genuinely new
sources and both found no edge. Passing G2 buys a test, not an edge.

### G3 — Power

**Run the tool. Paste the real output into the row.** No estimate, no
recollection, no arithmetic done by the model.

```bash
PYTHONPATH=. poetry run python tools/distil_power.py \
  --units {per_trade|per_alert|per_book_day} \
  --sr-footing {per_obs|annual} [--periods-per-year P] \
  --n-obs N --n-trials K --sr-variance V \
  [--n-series S --n-eff E] [--sd SD] [--bar R] [--corpus-best 1.196] \
  [--skew S] [--kurtosis K]
```

`1.196` is illustrative — that figure comes from `tools/multi_regime_power.py`, which is not
reproducible run-to-run; re-derive the real corpus best before relying on it.

`PYTHONPATH=.` is harmless but no longer required: the tool bootstraps its own
`sys.path` (ST59), and a test pins the bare invocation.

`--units` is mandatory with no default. Two filed defects came from numbers that
looked portable and silently changed meaning with the panel: the H15 `bar` units
trap, and the 25-symbol 2.92× deflator reused on panels whose true deflator is
1.628× or 3.331×. **Never reuse a filed deflator on a different panel** — pass
`--n-series`/`--n-eff` for the panel actually in play. They must be supplied
together; one alone is a declared error.

`--sr-footing` is mandatory with no default, because PSR runs **per observation**.
An annualized Sharpe fed beside an `--n-obs` counted in days prices the bar in the
wrong units and still prints a clean verdict. Match the footing to where the anchor
came from:

- **Per-alert or per-trade R anchors** (the `1.196` above, any `avg_r`) → `per_obs`
  with `--sd`, as in the example below.
- **Sleeve-Sharpe anchors** (the deploy core's +1.375, or any figure from the
  *Sleeve verdicts* table in `AGENTS.md`) → `--sr-footing annual --periods-per-year 365`,
  with `--sr-variance`, `--corpus-best` and `--bar` all annualized. Every sleeve here
  annualizes at 365 (`ForecastConfig.annualization_days`): crypto trades every day,
  so the equities 252 is wrong here. `--sd` is refused under `annual`.

On `per_book_day`, a `per_obs` declaration whose variance or corpus best implies an
annualized Sharpe above 4.0 is refused as a likely annualized input, and the error names
the corrective flags. The guard cannot see an annualized input under ~0.21, so declare
the footing honestly instead of relying on it.

Worked example, live ledger scope:

```text
distil_power - G3 power gate
  units             per_alert
  Sharpe footing    per_obs
  n_obs (declared)  4,918
  effective n       2,688  (deflated by n_eff 1.64 / 3 series)
  trial family      1 trials, sr_variance 0.0
  gate target       DSR >= 0.95

  required Sharpe   0.031740
  required effect   +0.0368 per alert  (sd 1.1595)
  corpus best       +1.1960
  VERDICT           REACHABLE

  null bar          +/-0.05
  CI half-width     0.0438  (best case, point estimate exactly 0)
  powered null      LICENSABLE  (analytics.audit_guard.powered_null)
```

The `n_series` here is **3, not the symbol count** — it is the number of series the filed
`n_eff` was estimated over. Cross-check it: `n_eff × t_deflator²` must recover `n_series`,
and `n_obs / t_deflator²` must recover the effective n the source tool printed. An earlier
draft of this example paired this `n_eff` with `n_series 14` and inflated the bar 2.16×,
which is the very error the paragraph above warns about — the guard cannot catch it, only
the cross-check can.

Three outcomes:

- **REACHABLE**, bar below the corpus best → survives to G4.
- **REACHABLE but the bar exceeds the corpus best** → survives only if the
  claim's own `author_effect_size` exceeds the bar. Say which.
- **UNREACHABLE** → **write it down and stop.** A successful output: "this
  cannot be resolved at our n against that trial family" is exactly the finding
  that saves a multi-session build. It is *not* underpowered — more data of that
  shape cannot fix it, only a smaller trial family can.

If `sr_variance` for the trial family is unknown, **G3 cannot run and the claim
is `INSUFFICIENT`, not a pass.** Never default it.

### G4 — Cost

Restate the expected edge net of the modelled drag:

```text
drag = 2 * (fee + slip) * entry / risk
```

Anything surviving only gross is rejected. Because drag scales inversely with
stop width, **a comparison between cells of differing stop width inherits a
bias, not merely a level shift** — say which direction it runs.

Every surviving row repeats the standing caveat: **costs are MODELLED, not
realised.** Raw stays exactly −1.0 = declared risk, so no figure here expresses
gap risk, and every number is an optimistic bound whose error runs one way.

## Refusals

The skill must refuse to:

- **Write any row whose G3 output was not pasted from a real tool run.**
- **Write more than three survivors per run.** If more survive, rank by priced
  power, write the top three, and **log every dropped item with its reason**. A
  silent cap reads as "covered everything" when it did not.
- **Re-open a G1 match** without an explicit written operator override.
- **Emit parameters, `tp_r`, thresholds, detector code, or any code at all.**
  `/wfo-sweep` remains the only trusted path for `tp_r`.

## Routing

On operator approval only.

| Claim type | Destination |
| --- | --- |
| Alpha / signal | `docs/plans/thesis-inbox.md` |
| Execution, cost, sizing, mechanics | `docs/plans/mechanics-backlog.md` |

Both are gitignored, matching the streams `/ingest-x`, `/ingest-video` and
`/ingest-charts` already write to. **This skill creates no new pipeline** —
books and repos are a fourth source into the same intake.

**This skill deliberately does NOT call `route_dedup mark`** (operator ruling
2026-08-27, closing SoT ST103). The identity key is `(source_id, item_ts, sink)` and a
distil row has no stable spelling for either half — a book is not a status id, and
hypotheses are GENERATED, so a re-distil renumbers and rewords them and a recorded key
would never match again. A mark here would record without ever protecting. The real
guard is semantic and already in place: the novelty gate rejects filed verdicts, and
restatements are fed back through the extractor prompt. Do not "complete" the ledger by
minting a `book://` scheme — that is bookkeeping with no consumer.

Each row carries: the claim, its source citation, all four gate results verbatim,
the pasted `distil_power.py` output, and a **Decision Log** naming the observable
that would reverse it.

A pre-registered spec in `docs/superpowers/specs/` is generated **only on
promotion**, when the operator picks a row to run. Untested ideas do not enter the
spec corpus: the spec-reconcile denominator is the full corpus, and every spec
costs a reviewer a look to confirm there is nothing to reconcile.

## Guardrails

- **G3's bar is an UNDER-estimate, not a conservative one.** The `n_eff`
  deflator corrects **cross-symbol** correlation only. Alerts also cluster in
  time within a symbol — multiple timeframes and strategies firing on one move —
  and nothing deflates that. So the effective n printed is an upper bound and
  every bar is smaller than the true one. Never present a G3 pass as having
  margin it did not measure.
- **Never quote an exact figure from `tools/multi_regime_power.py`; re-derive
  it.** That tool is **not reproducible run-to-run** — measured 2026-08-14, two
  consecutive runs of identical code returned pooled `mean_r` −0.0705 vs −0.0824
  with `n` fixed at 165,409, because `any_value(pnl_r)` picks an arbitrary row
  per dedup key. Its *directional* conclusions survive easily; its specific
  numbers do not.
- **`sr_variance` unknown ⇒ INSUFFICIENT.** Never a pass, never a default.
- **`distil_power.py` errors or is missing ⇒ abort the run.** Do not degrade to
  an estimate. The tool existing and being run is the whole point of G3.
- **A named book-skill that is not installed ⇒ name it and stop.** Do not fall
  back to reading the raw PDF; that is `book-to-skill`'s job and costs 24×–51×
  more context.
- **Malformed subagent JSON is tolerated at the consuming end.** A system-prompt
  directive is not a parser — the "bare JSON, no fence" rule broke 1-in-6 even
  when stated explicitly.
- **A repo source returns claims about mechanism** (how execution, sizing or
  routing is done), which route to `mechanics-backlog.md`.
- **Treat every extracted claim as a hypothesis, including the author's own
  numbers.** A published Sharpe is a claim about the author's panel, not ours.
