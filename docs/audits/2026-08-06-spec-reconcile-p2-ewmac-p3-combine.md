# Spec-vs-code reconcile — P2 EWMAC trend + P3 trend×XS combine (2026-08-06)

> Defect-4 reconcile. These two were prioritised because they **gate the
> "second edge" call**: the standing verdict is that XS-solo is the deploy core
> and the system needs a second strong edge, and these are the two specs whose
> verdicts (EWMAC shelved, combine Sharpe-dominated) close off that search.
>
> Method: read each spec's success-metric, gate and testing sections against the
> implementation, leg by leg. Carrying H8's two lessons — **(a)** a pre-registered
> gate leg the code never implements is invisible, so check every leg rather than
> the ones the verdict doc happens to mention; **(b)** a guard can be well-named,
> read correctly, and still be unable to fail, so **run the mutation**.

## Verdict summary

| Spec | Gate legs | Causality guard | Result |
| --- | --- | --- | --- |
| `2026-06-15-p2-ewmac-trend-sleeve-design.md` | **2 pre-registered legs unimplemented** | mutation-verified ✓ | **DRIFT — verdict safe, diagnosis incomplete** |
| `2026-06-18-p3-trend-xs-combine-design.md` | all legs implemented | mutation-verified ✓ | **CLEAN** |

**Neither sleeve's headline verdict changes.** EWMAC still FAILS; combine still
clears-but-is-dominated. The findings are about what was never measured, not
about a number being wrong.

## Finding 1 — P2 pre-registers MinTRL as a GATE leg; no sleeve gates on it

The P2 spec says the gate is four legs, in two places:

- §1 success metric: "costs in, **DSR / PBO / bootstrap-CI / MinTRL** gated"
- §6: "**MinTRL** — is the track record long enough to trust Sharpe ≥ 1?"

Every implementation gates on **three**:

```python
clears = bool(rep.dsr >= 0.95 and rep.pbo <= 0.5 and rep.boot_lo > 0.0)
```

`min_trl` is computed (`forecast/report.py:99`), stored on the report dataclass
(`:55`), and printed as a "stamp" — but **no `clears` / `gate` expression in any
sleeve references it**. Verified across all five: `forecast`, `combine`, `xsmom`,
`carry`, `xsrev`.

**This is NOT a repeat of H8, and the difference matters.** H8's missing leg was
*invisible* — nothing disclosed it. Here the audit docs are honest and consistent
with the code: `2026-06-16-p3-xsmom-sleeve.md:54` states the gate as three legs,
and `:105` discloses the MinTRL result as an explicit caveat:

> **MinTRL > sample.** MinTRL to confirm a true Sharpe > 1.0 at 95% is ~7035 obs
> vs 2475 available … the sample **cannot yet statistically confirm the true
> Sharpe exceeds 1.0** — only that it is very likely > 0.

Independently recomputed here and it reproduces: at Sharpe 1.375 daily-scaled
against a 1.0-annualised target, MinTRL ≈ 7043 observations vs ~2475 available.

So the drift is **spec-vs-code**, not a concealed failure. The consequence is
bounded but real: anyone reading P2 §1 believes the published verdicts cleared a
four-leg gate, and the deploy core would **not** have cleared the fourth.

**Recommendation: fix the SPEC, not the code.** The three-leg gate is the
defensible one — MinTRL against a *non-zero* target answers "can I confirm Sharpe
≥ 1", which is a different and much harder question than "is there an edge", and
making it a hard gate would have failed every sleeve this system will ever
produce at realistic sample sizes. Keep it as a reported stamp; amend P2 §1/§6 to
say so.

## Finding 2 — P2's per-regime attribution was never implemented (the substantive one)

Pre-registered in **three** places:

- §2 reusable shelf: "Regime classifier | `analytics/regime.py::classify_series` |
  regime-conditioned attribution"
- §3.1: "`report.py` # terminal renderer (portfolio + per-instrument + **per-regime**)"
- §6: "**Attribution** — per-instrument and **per-regime** (via `classify_series`):
  does the trend Sharpe concentrate in trend regimes, as theory predicts?"

Implemented in **zero**. `grep -rn "regime" analytics/forecast/` returns no hits;
`classify_series` is imported by 10 modules across the repo, none of them the
forecast sleeve or `tools/forecast_audit.py`.

**Why this one matters more than Finding 1.** It is not a stamp — it is the
*theory-confirmation diagnostic*, and it is the difference between two very
different readings of the same +0.36:

- trend is genuinely weak in crypto ⇒ correctly shelved, stop looking; or
- trend works in trend regimes and 2019–2026 simply had few of them ⇒ EWMAC is a
  regime-conditional diversifier, which is exactly the "second edge" slot the
  combine sleeve was built as a socket for.

The standing verdict calls EWMAC "structurally real but FAILS the gate —
**SHELVED as a diversifier candidate**". That phrasing already assumes the answer
the unrun diagnostic would have given. **The FAIL is sound; the shelving rationale
rests on a check that was specified and never run.**

Cost to close is low: `classify_series` exists, the sleeve already emits a daily
net return series, and §6 only ever asked for attribution — no new data, no new
sweep, no gate change. Filed as a follow-up, not fixed here (this branch is a
reconcile, and adding a diagnostic is a behaviour change that deserves its own
review).

## Finding 3 — the causality guards are NOT vacuous (mutation-verified)

The standing warning is `[[xsmom-causality-test-vacuous]]` — an xsmom causality
test that passed under mutation because its fixture was cap-saturated. Both specs
here pre-register the same kind of guard (P2 §8: "perturbing a future bar must not
change an earlier position"; combine §4: "a perturbation test proving `port_{t<T}`
is invariant to `r_*,T`"), so reading them was not enough. **Each shift the guards
protect was removed and the guard re-run:**

| # | Mutation | Guard | Result |
| --- | --- | --- | --- |
| M1 | `combine/idm.py` — drop `.shift(1)` | `test_combine_is_causal_no_lookahead` | FAILED ✓ |
| M2 | `combine/book.py` — drop governor `.shift(1)` | same | FAILED ✓ |
| M3 | `forecast/book.py` — drop governor `.shift(1)` | `test_governor_is_causal` | FAILED ✓ |
| M4 | `forecast/vol.py` — drop vol `.shift(1)` | `test_position_is_causal_no_lookahead` | FAILED ✓ |

4/4 died as they must; tree restored clean after each. **The xsmom vacuity was
specific, not systemic** — worth recording, because the memory entry reads as a
general warning about this family of guard and these two are demonstrably sound.

One weakness survives and is filed rather than fixed: **none of these tests
carries a positive control.** They assert only that quantities did *not* change,
which is equally satisfied by "the invariant holds" and "the stimulus never
reached the code". One hypothesis of that shape was checked and refuted here —
`combine_books` does read `xs_result.portfolio_return` (`book.py:39`), the field
the test perturbs — but that was confirmed by reading, not by the test. Adding
`assert changed(...)` on the one index that must move would make the guards
self-proving.

## Finding 4 — the reconcile counter is wrong on every surface (open question, now answered)

`CLAUDE.md` said **"1 of 44"**; the handoff said **"3 of 44"**; the real corpus is
**46** (`ls docs/superpowers/specs/*.md | wc -l`). Numerator and denominator both
wrong, and CLAUDE.md is the one auto-loaded into every session.

The blocking question was *"does a spec with no implementation count as
unreconciled, or is the denominator only implemented specs?"* — **decided here:
the denominator is the full corpus (46).** A spec with nothing to reconcile still
requires someone to look and confirm that, so it is not free, and any
"implemented-only" denominator needs a per-spec judgement call that nothing
records. Full corpus is the only definition that stays unambiguous as specs land.

**Count after this reconcile: 5 of 46** (H8, xsmom, the ingest-video design doc
amended by #551, plus the two here). Updated in `CLAUDE.md`.

## What this means for the second-edge call

- **Combine's verdict is trustworthy.** Its gate matches its spec exactly, its
  causality guard is mutation-verified, and its DoD doc-sync legs are met. "Clears
  the gate but is Sharpe-dominated by XS-solo" stands as written — it remains a
  validated socket awaiting a second strong edge.
- **EWMAC's FAIL stands, but its SHELVING is under-evidenced.** Finding 2 is the
  actionable output of this reconcile: the one pre-registered check that could
  distinguish "trend is dead" from "trend is regime-conditional" was never run,
  and it is cheap to run.
- **No new edge is unlocked here.** The binding constraint is unchanged: the next
  edge needs genuinely new data. This reconcile only sharpens what is known about
  the two candidates already on the shelf.

## Follow-ups filed (none fixed on this branch)

1. ~~**Run per-regime attribution on the EWMAC sleeve** (P2 §6, unimplemented). The
   one finding here that could move a verdict.~~ **DONE 2026-08-06 (PR #567) —
   verdict `docs/audits/2026-08-06-p2-ewmac-regime-attribution.md`: NO.** The
   concentration does not survive a cross-section t-stat correction (25 perps
   carry ~2.92 effective independent series), there is no dose-response, and an
   optimistically-gated book gains +0.102 Sharpe on a CI of [−0.459, +0.715].
   EWMAC stays shelved; the regime-conditional escape hatch is now closed by
   measurement rather than assumed by phrasing. **Do not re-run this.**
2. **Amend P2 §1/§6** to state the three-leg gate and describe MinTRL as a
   reported stamp.
3. **Add positive controls** to the four causality guards above.
4. Next specs by stakes: ~~`p3-cross-sectional-momentum-sleeve-design` (the deploy
   core, still unreconciled)~~ **DONE 2026-08-06 (PR #568) — verdict
   `docs/audits/2026-08-06-spec-reconcile-p3-xsmom.md`: the implementation is
   CLEAN** (no missing gate leg; the H8/P2 failure mode did not recur), and its
   one defect is in the SPEC — §Causality's pre-registered test is unsatisfiable
   as literally written. Then H9 warning-value and ST1 reference-level.
