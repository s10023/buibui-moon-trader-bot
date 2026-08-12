# Spec reconcile — F2 AI Trade Card v1 (2026-08-12)

**Spec:** `docs/superpowers/specs/2026-07-08-f2-trade-card-design.md` (+ its 2026-07-11
addendum, which the spec declares wins on conflict).

**Verdict: the IMPLEMENTATION reconciles CLEAN — every pre-registered element is present,
including the test claims. The gap is the SUCCESS METRIC: F2 shipped "with its own
kill-test attached", the kill test is now measurable, and nobody has ever read it. When
read for the first time here, it says the card has no edge.**

This is the fourth spec reconcile on record and the second to come back clean on code
(after P3-xsmom). The H8 failure mode — a pre-registered gate leg the code never
implements — did **not** recur.

## 1. Module specifications — all present

The spec's `card/` tree is implemented exactly as drawn: `config.py`, `state.py`,
`prompt.py`, `client.py`, `card.py`, `ledger.py`, `render.py`, `errors.py`, `run.py`,
plus `cli/card.py`. The two addendum-added modules (`run.py` orchestrator, `errors.py`)
both exist. `card/` imports no `trade/` — the CLI injects the account provider, as
specced.

## 2. The five hard rules — each implemented AND each individually tested

Spec §`card.py` step 2 pre-registers five rules. This is the section where H8 failed, so
it was checked line by line rather than by grep.

| spec rule | implementation | test that trips it |
| --- | --- | --- |
| (a) SL on correct side; TPs ordered | `card.py:241-251` | `test_sl_wrong_side_vetoes`, `test_tp_disorder_vetoes` |
| (b) planned RR(tp1) ≥ `min_rr` | `card.py:254-258` | `test_min_rr_floor_vetoes` |
| (c) no conflicting open position | `card.py:262-264` | `test_conflicting_position_vetoes` |
| (d) circuit breaker on `daily_r` | `card.py:265-268` | `test_circuit_breaker_vetoes` |
| (e) entry within ±band of `ref_close` | `card.py:275-280` | `test_entry_band_vetoes` |

Step 3 (any violation ⇒ `VETOED` regardless of the LLM) and step 4 (degraded account ⇒
**warn, not veto**) are both implemented, the latter with the spec's warning string
verbatim and a `test_degraded_account_warns_not_vetoes` guard. LLM-declared `NO_TRADE`
passes through unsized (`test_no_trade_passes_through_unsized`), as specced.

**One thing that LOOKS like a deviation and is not.** `card.py:233` gates the whole veto
block behind `verdict == "TRADE"`, so a NO_TRADE batch exercises none of it. That is the
spec's own step 3 ("LLM-declared NO_TRADE passes through with its named gate"), not a bug
— but it does mean **a clean NO_TRADE run verifies nothing about the hard rules**, which
is worth knowing before treating a quiet batch as evidence the breaker works.

## 3. Validation + ledger contract — as written

- 5–8 reasoning bullets and `confluence_score` ∈ [0,9] are enforced at
  `card.py:92-101`; verdict/direction enum membership and TRADE's positive-price
  requirement likewise. One re-ask then `CardError` (`run.py`).
- Every card → `ai-cards.jsonl`; **TRADE only** → a pundit-calls row carrying exactly the
  specced keys, with the synthetic unique `url` (`ai-card://<ms>-<symbol>`) the spec
  requires because `url` is the override key.
- `horizon` is `"intraday"`, which is what the addendum told the implementer to confirm
  against `WINDOWS_MS` rather than invent.
- The spec's sharpest test claim — *"the pundit-calls row parses through
  `tools/pundit_score.py`'s loader"* — is real, not paraphrased: `test_card_ledger.py:78`
  imports the actual `load_ledger` and runs the written row through it.
- The `client.py` env-strip claim is likewise real and is a genuine negative assertion:
  the test sets `ANTHROPIC_API_KEY` and asserts it is absent from the child env.

## 4. THE GAP — the success metric was never read

Spec §Goal:

> **Success metric:** the card ledger accumulates resolvable calls whose hit-rate/avg_r
> can be measured by the existing pundit scorer — F2 ships with its own kill-test
> attached.

**No audit has ever reported that number.** It became measurable at some point between
July and now, and it surfaced here only incidentally, while re-ranking pundits for an
unrelated task. Read from `pundit-priors.json` (regenerated 2026-08-12T13:58:07Z):

| `buibui_card` | value |
| --- | ---: |
| n (resolved) | **19** |
| hit_rate | **0.308** |
| `avg_atr_r` | **+0.010** |
| `avg_r` | **−0.279** |
| `r_coverage` | **1.000** |

**The card has no measurable edge.** +0.010 ATR-R over 19 resolved calls is
indistinguishable from zero, and the hit rate is below the corpus's own.

Two things sharpen it:

- **This is the second-largest sample in the entire pundit corpus** (only one author
  reaches n≥30), so it is not an unusually thin read by this corpus's standards — it is
  one of the better-supported ones.
- **`r_coverage` is 1.000, which is diagnostic.** Cards always state a stop by
  construction, so `buibui_card` is one of the few authors whose `avg_r` is *not*
  winner-censored. The sign disagreement between `avg_r` (−0.279) and `avg_atr_r`
  (+0.010) here is therefore **not** the censoring artifact documented elsewhere — it is
  the two metrics answering different questions (per unit of named risk, vs. distance in
  volatility units). Do not "reconcile" them.

**The deeper finding is about the metric's wording.** "Calls whose hit-rate/avg_r *can be
measured*" is a **plumbing** criterion — it is satisfied the moment the dual-write and the
scorer agree, which was true on day one. It cannot fail for the reason anyone cares about.
The question the kill test was meant to answer — *is the card any good* — was never
pre-registered with a threshold, so there is no number the card could have missed. Same
defect family as an audit gate whose effect-size floor is expressed in the wrong units:
the criterion looks rigorous and is structurally unfalsifiable.

**Recommendation, not actioned here:** if F2 is to keep its kill test, pre-register a
threshold and a horizon now, before more data accrues — and price the power first, because
at ~19 resolved calls per five weeks the reachable effect size is large.

## 5. Definition of Done — met

`make lint-py`, `make typecheck`, `make test` and the README / CLAUDE.md `card/` entries
are all in place. The spec's "regression goldens untouched (no detector/backtest/stats
change)" holds and still holds: `card/` is outside the regression trigger set.

## 6. Follow-ups the spec named and did not build — correctly still unbuilt

Items 1–5 in §Follow-ups are explicitly v1.1+ backlog, so their absence is not drift.
Worth flagging that **#3 — "feed the AI's own scored track record back into the rubric" —
should stay unbuilt**: §4 shows that track record is ~zero, and wiring a null signal into
the prompt would add noise while looking like a feature. It is also the same one-way-loop
hazard the golden-signal rule exists to prevent.

## Scope — and a defect found IN THE COUNTER while writing this

This reconcile covers the F2 v1 design spec only. Its two siblings — the 2026-07-13
card-pundit self-citation fix design, and the 2026-08-06 card capital-and-invalidation
design — were **not** walked and remain unreconciled.

**⚠ Naming them by filename here would have counted them as RECONCILED.** A first draft of
this section cited both `.md` filenames in order to state plainly that they were *not*
done. `make docs-index` then moved the derived count from **3 of 52 to 6 of 52** — it
credited all three specs, because the derivation rule is "an audit that names a spec's
filename and also discusses reconciling", and it cannot tell a citation from a
disclaimer. The filenames were removed and the count settled at the correct **4 of 52**.

This is a **false-positive** mode, and it is new. `INDEX.md`'s own caveats describe only
under-counting — it cannot see a partial reconcile as partial, and cannot see one that was
never written down. It can also **over**-count, and the trigger is the most natural
sentence an honest audit writes: *"spec X was not covered."*

Consequences, both worth carrying:

- **Never write a sibling spec's filename in an audit except to claim you walked it.**
  Refer to it by date and title instead, as this section now does.
- **The index is a floor in one direction and NOT a ceiling in the other.** CLAUDE.md
  records this counter as wrong every single time anyone has checked it — 4 for 4 — and
  the standing advice is to treat it as a floor. That advice is now incomplete: a floor
  assumes error runs one way, and this one runs both. Treat the number as a claim to
  verify, which is what CLAUDE.md's stronger sentence already says.
