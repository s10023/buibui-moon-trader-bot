# Exit-policy A/B v1 — fixed (#0) vs composite — sub-project B verdict

**Date:** 2026-06-15 · **Status:** v1 result (read-only replay) · **Parent:**
`docs/redesign/2026-06-05-exit-improvement-spec.md` §3–§5 · **Builds on:** N2
diagnostic (`docs/audits/2026-06-11-mfe-mae-diagnostic.md`) + MFE-timing oracle
(`docs/audits/2026-06-15-mfe-timing.md`) · **Reproduce:**
`PYTHONPATH=. poetry run python tools/exit_audit.py --replay`.

## What was built

A pluggable exit-replay layer (`analytics/exits/policies.py` + `replay.py` +
`audit.py`) that re-resolves the live alert ledger under an exit policy and feeds
the re-resolved `(realized_r, exit_ts)` through the **same P1 `PaperBook`** the
sizing baseline used. Headline = portfolio Sharpe, not per-trade avg_r (spec §5).
v1 compares **policy #0 (fixed: today's tp_r + time-expiry)** vs a **global
composite** (#1 time-stop at the per-tf floor + #2 breakeven-at-1R + #6
partial-50%-at-1R; runner targets the alert's own rr_ratio). Same entries + same
SL → apples-to-apples; only exit management differs.

## Result (gross of costs, default SizingConfig)

| policy | n_sized | n_skip | Sharpe | Sortino | maxDD | pop avg_r | sized avg_r | sized win | sized BE |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| fixed | 217 | 2344 | **+0.22** | +0.25 | −11.9% | −0.098 | +0.037 | 13% | 0% |
| composite | 260 | 2301 | **−0.19** | −0.19 | −7.6% | −0.058 | +0.037 | 7% | 12% |

P1 net baseline (DB `outcome_r`, net of costs, same config): Sharpe **−0.78**,
200 sized.

## Verdict

**The global composite does NOT lift portfolio Sharpe above policy #0** (+0.22 →
−0.19). Two reasons, and they matter more than the headline:

1. **Caps dominate, not exits (the P1 problem again).** Only ~8–10% of the 2,561
   alerts get sized — the live universe is one BTC/ETH/SOL majors cluster, so the
   1% cluster cap throttles the book (same as the P1 baseline). The cap-admitted
   subset has the **identical** sized avg_r (+0.037) under *both* policies, so no
   exit change can move the portfolio bottom-line while breadth is this narrow.
2. **Global composite cuts the right tail.** It converts wins → breakevens (sized
   win 13% → 7%, BE 0% → 12%) and halves drawdown (−11.9% → −7.6%), but the few
   big tp_r wins that carried the positive subset Sharpe get truncated → Sharpe
   falls even as DD and population avg_r improve.

**But exits do help the per-trade economics** (the N2 thesis holds at the
population level): full-population avg_r improves −0.098 → −0.058 and drawdown
halves. The benefit is real; it just can't reach the portfolio headline through a
90%-throttling cap on a single-cluster universe.

### Caveats (before any G1 call)

- **Gross of costs.** Fixed +0.22 is optimistic; the net baseline is −0.78. The
  honest signal is the *delta* (composite − fixed ≈ −0.4 Sharpe), and netting
  would penalise the composite slightly more (extra partial fills).
- **Global, not per-edge.** The spec predicts a single policy "helps one book and
  hurts the other" — location wants fast/partial exits, momentum wants to run.
  Applying one composite to all cells (incl. long "just-wrong" cells N2 said to
  prune) is the spec's one-size-fits-none anti-pattern.
- **Single time-stop value** (the per-tf floor), not yet swept.
- Composite "expired" includes deliberate time-stops (not #0's failure mode).

## G1 read + next levers

**G1 = "pruned TA + direction overlay + sizing + best exit → paper Sharpe > 0":
NOT cleared by global exits, leaning NO for rescuing the TA book by
sizing+exits alone.** The binding constraint is **breadth** — caps throttle 90%
of a one-cluster stream — which is direct, independent confirmation (alongside
P1) that **P2 (cross-sectional breadth / forecast reframe) is the priority**, not
more TA exit-tuning.

Remaining exit levers (cheap — the engine + harness now exist), in order:

1. **Per-edge-type assignment** (location = partial + short time-stop; momentum =
   let-run / wider) instead of global — the spec's explicit prediction.
2. **Direction overlay** — prune long "just-wrong" cells (N2) before exit-tuning;
   apply the composite only to short-skewed survivors.
3. **Cost-netting** in the replay; **time-stop sweep** over `[floor → max_hold]`.
4. Re-evaluate G1 only after (1)–(3) **and** with a breadth-relieved universe.
