# A31 re-run — H8 VWAP gating at 4h, priced on the UTC-day cluster

> **SUPERSEDED 2026-10-08 by `docs/audits/2026-10-08-h8-a31-causal-tagger-rerun.md` (#952).** Every number below was
> tagged by a tagger that read the in-progress 1d bar's close, up to 24h after
> entry. On the causal tagger the 4h `vwap_weekly/below/short` cell reads +0.160R
> INSUFFICIENT, not BUILD. Kept as the record of what was filed.

Re-runs H8's 4h backtest tier and its live corroboration
(`docs/audits/2026-07-24-h8-m1-indicator-conditioning.md`) with the one change its register
row carried: `audit_guard` now prices every verdict on the UTC-day cluster
(`utc_day_keys`, PR #696, 2026-08-25). Every filing of A31 (2026-07-24, 2026-08-04, 2026-08-13) predates that
change. Asked by #943 because #921 named C836 (shorts below the weekly VWAP at 4h) for round
1's slot 2, which makes A31 relied on under the retest rule (#908). Read-only against a
2026-10-08 snapshot of `analytics.db`. A faithful re-run, so no trial is spent from the round
budget.

## Verdict

The cell still reads BUILD. On the gate-deciding backtest substrate `vwap_weekly/below/short`
at 4h holds +0.467R on n=2,355 against the filed +0.485R, with a with-vs-without lift of
+0.903R on a cluster-priced CI of [+0.798, +1.010], DSR 1.000, PBO 0.000 and MinTRL 47. The
day cluster did not move it, and neither did either sensitivity leg: cut at the last filing's
date it reads +0.470R, and with the `bos` and `liquidity_sweep` rows removed (the pool still
carries their pre-causality-fix runs) it reads +0.429R with a lift CI of [+0.751, +0.983].
C836 therefore stays in round 1, and #921's withdrawal rule does not trigger. Two readings
weaken it, and both belong to C836's pre-registration (#944) rather than to this verdict. The
filed live corroboration does not survive the cluster: pooled across timeframes as filed, the
cell moves from BUILD +0.286R to INSUFFICIENT (+0.224R on the filing's window, +0.142R on
today's ledger). Read at 4h alone, which the filing never did, live shorts below the weekly
VWAP lose −0.417R on n=224, and among live 4h shorts the split cannot be told apart from
zero (lift +0.011R on [−0.236, +0.272], a CI five times the bar, so this is not a powered
null either). The backtest's +0.467R is an in-sample reading on the same ten months that produced
every other rating in this repo; the live 4h book is the only out-of-sample look the cell has,
and it points the other way.

## What changed from the filed run

| | filed (last 2026-08-13) | this re-run |
| --- | --- | --- |
| `audit_guard` CI and Holm unit | trade | UTC day (`utc_day_keys`), both legs |
| backtest 4h pool | 7,720 tagged | 7,925 tagged (entries to 2026-08-17; no backtest has run since 2026-08-18) |
| live ledger | 4,034 rows | 9,318 tagged |
| cells, legs, `bar` 0.05, `alpha` 0.05, `min_n` 30, `n_boot` 2000, seed 12345, one tier at a time | | unchanged |

## Results

Pooled cells on the `vwap_weekly` axis. `lift` is with-state minus without-state, in R per
trade.

| run | n tagged | below/short | lift [CI] | verdict | above/short | below/long |
| --- | --- | --- | --- | --- | --- | --- |
| filed, backtest 4h | 7,720 | +0.485 | — | BUILD | −0.425 AVOID | −0.439 AVOID |
| backtest 4h | 7,925 | +0.467 | +0.903 [+0.798, +1.010] | BUILD | −0.435 AVOID | −0.436 AVOID |
| backtest 4h, entries before 2026-08-13 | 7,919 | +0.470 | +0.905 [+0.802, +1.011] | BUILD | −0.435 AVOID | −0.436 AVOID |
| backtest 4h, no `bos` / `liquidity_sweep` | 6,649 | +0.429 | +0.864 [+0.751, +0.983] | BUILD | −0.435 AVOID | −0.437 AVOID |
| filed, live (all tf) | 4,034 | +0.286 | — | BUILD | −0.322 AVOID | −0.654 AVOID |
| live (all tf), before 2026-08-13 | 4,961 | +0.224 | +0.567 [+0.495, +0.641] | INSUFFICIENT | −0.343 AVOID | −0.661 AVOID |
| live (all tf) | 9,318 | +0.142 | +0.574 [+0.523, +0.627] | INSUFFICIENT | −0.432 AVOID | −0.440 AVOID |
| live 4h (diagnostic, not filed) | 639 | −0.417 | +0.011 [−0.236, +0.272] | NO-EDGE | −0.428 INSUFFICIENT | −0.379 INSUFFICIENT |

`above/short` is the mirror of `below/short` (they partition the shorts), so the BUILD and its
AVOID twin are one finding, as the filing already said. No sign inversion: BUILD here comes
with a positive with-state mean and a positive lift, so the `DISABLE → BUILD` mapping #836
warned about did not produce it.

The live 4h row's NO-EDGE is the tool's label for a cell that fails the family gate (PBO
1.000), not a CI that ruled an effect out: its lift CI is about five times the ±0.05R bar.

The live window cut at 2026-08-13 holds 4,961 rows against the filing's 4,034 because live
outcomes resolve late: alerts fired before the cut have kept resolving since.

## Why the leak-free leg exists

The 4h pool is a deduplicated union of 898 runs made between 2026-03-25 and 2026-08-18, and
the tool keeps one row per `(symbol, tf, strategy, direction, entry_time)`. The 2026-08-18
`bos` causality fix moved `bos` entries to the confirmation bar, so a pre-fix row and its
post-fix replacement have different entry times and both survive the dedup. Of 849 `bos` 4h
trades, 629 come only from pre-fix runs. All 427 `liquidity_sweep` trades come from runs
last made 2026-05-22, also before its fix. Removing both detectors moves the cell by
−0.038R and leaves the verdict standing, so the leak does not carry it. The same pool feeds
every other 4h cell in the filed audit, and nothing here re-reads them.

## Caveats

- **In-sample.** The backtest leg spans 2025-09-12 to 2026-08-17, the window every star
  rating and decay cell was fitted inside. C836's out-of-sample test is the generated
  pre-2025-09 leg #921 specified, and that leg has not run.
- **The live 4h reading is post-hoc and thin.** The filing pooled live across timeframes; the
  4h-only cut was added here because C836 is a 4h construction. n=224 on one cell, live is
  not independent of the backtest, and live rows straddle the `outcome_r` cost-basis change
  (#432), so read −0.417R as a warning for #944, not a verdict.
- **The pool mixes engine configurations.** Runs carry 1–5 `tp_r` values and up to 6
  `day_filter`s per strategy, and the dedup keeps one row per trade arbitrarily. That is the
  filed construction, unchanged here.
- **Holm p and the `audit_guard` CI on the with-state mean are not printed by the tool.**
  The verdict column is the tool's own and already applies both.

## Reproduce

Against a snapshot, one tier at a time:

```bash
PYTHONPATH=. poetry run python tools/indicator_condition_audit.py --db <snapshot> --source backtest --timeframes 4h
PYTHONPATH=. poetry run python tools/indicator_condition_audit.py --db <snapshot> --source backtest --timeframes 4h --until 2026-08-13
PYTHONPATH=. poetry run python tools/indicator_condition_audit.py --db <snapshot> --source backtest --timeframes 4h --exclude-strategies bos liquidity_sweep
PYTHONPATH=. poetry run python tools/indicator_condition_audit.py --db <snapshot> --source live
PYTHONPATH=. poetry run python tools/indicator_condition_audit.py --db <snapshot> --source live --until 2026-08-13
PYTHONPATH=. poetry run python tools/indicator_condition_audit.py --db <snapshot> --source live --timeframes 4h
```

`--until` and `--exclude-strategies` were added for this re-run; neither changes a default
run.
