---
name: decay-review
description: >
  Weekly decay review — run the DSR-suspect list + gate-reachability check
  (`make buibui-decay-review`), join it to per-sleeve P1 replay attribution, write
  a dated report to `docs/plans/scratch/`, and stamp the task marker. Read-only
  against `analytics.db`; changes no star, gate or golden.
  Invoke when the user says "/decay-review", "weekly decay review", "are the
  ratings decaying", "which cells are overfit", or when
  `docs/plans/task-marks/decay-review` reads overdue (the daily check surfaces it).
allowed-tools: Bash, Read, Write
---

# Weekly decay review

**Goal + success metric, restate it before starting:** decide whether the rated
(★≥4) population's overfit picture *changed since the last run* — not whether it is
bad. It is already bad and that is a settled finding.

## Cadence

Tracked by marker, not memory: `docs/plans/task-marks/decay-review`. A missing or
stale marker reads as overdue **on purpose**. Nothing auto-runs — this needs an LLM
session to interpret. Stamp it only after the report is written:

```bash
date -u +%FT%TZ > docs/plans/task-marks/decay-review
```

## Leg 1 — DSR-suspect list + gate reachability

```bash
make buibui-decay-review                      # all scopes
make buibui-decay-review DAY_FILTER=tue_thu   # scope to one bucket
```

Read-only. Rebuilds the exact pools `compute_dsr_ratings` uses via the shared
`recalibrate_lib.select_rated_run_ids` — never a local copy of the ranking.

**Three reading traps. All three have already produced a wrong reading once.**

1. **Absence from the Suspect list is NOT a clean bill.** Only a cell in *neither*
   the suspect nor the unscoreable list has passed anything. The suspect warning
   once skipped `dsr is None`, so 42 of 66 rated cells could never appear in it and
   an unrun check read as a passed one (fixed #603 — the `⚠ Unscoreable` companion
   line exists because of this).
2. **The `bar at n median` line is a SCALE STAMP, not a verdict** — the tool labels
   it so. Headroom is measured at each cell's **own** n, because the required Sharpe
   falls as n rises; comparing a max Sharpe against a median-n bar mixes two bars and
   can report a gate as reachable while every cell fails it.
3. **`MIN_DSR_TRADES` gates COUNT, not DISPERSION.** `_sharpe` rejects only
   `sd == 0.0` exactly, so a degenerate near-constant cell clears the floor and can
   inflate the trial-family variance by orders of magnitude.

**Quote BOTH populations.** The tool prints the stored count and the declared count.
Cells no config declares any more are orphans (`tools/dead_surface_check.py`) and are
not part of the live gate. Reporting only the stored number overstates the population.

**Expected result, so you are not surprised into a finding:** 0 cells clear DSR 0.95,
in every scope, and `REACHABLE: NO`. That is the standing verdict, not news. The
week-over-week *delta* is the deliverable.

## Leg 2 — per-sleeve attribution (P1 replay)

```bash
make buibui-portfolio-replay          # CONFIG= / CAPITAL= / VOL_TARGET= override
```

Read-only replay of the live outcome ledger through the Carver sizing model. Join the
attribution extremes to the star rating **of the same cell** — that join is the point
of the leg, because it puts [[star-ratings-no-live-signal]] into *sized P&L* rather
than per-alert R.

Two traps:

- **Capital basis is the configured `[portfolio] capital`, not live equity** unless
  you override it. Absolute dollars are therefore not the account's dollars.
- **Positive rows are mostly n≤3. Do not read them as edges.** Sort by n before
  reading any avg_r.

## Leg 3 — live-ledger delta

**HELD.** This is the only leg the recording-bias suspension ever covered: the live
sample was 35% complete with a session-skewed run-hour distribution, which is a
*biased* base, not merely a thin one. It unholds on the soak verdict — check the
handoff's opener list before assuming it is still held, and do not substitute
runs/24h for the trailing weekday-coverage metric the verdict actually reads.

## Report + close out

Write to `docs/plans/scratch/decay-review-<YYYY-MM-DD>.md` (gitignored — no PR, no
CI, no repo flip). Structure that has worked: Headline · Leg 1 · Leg 2 · Leg 3 status ·
What this changes. Then stamp the marker.

Lead with what **moved** since the previous report in that directory. If nothing moved,
say so plainly — "unchanged" is the correct finding most weeks and is worth one line,
not a rebuilt table.

## Footguns

- **The driver is `tools/decay_review.py`.** `docs/plans/scratch/decay_review.py` is a
  stub that exits with a pointer. The scratch copy silently reverted to recency-only run
  selection after #606 and spent a run auditing the live daemon's rows instead of the
  deliberate sweeps — which is why the ranking now lives once, in
  `recalibrate_lib.select_rated_run_ids`. Never re-derive that ranking locally.
- **This skill writes nothing to the DB.** If a step wants to write, stop — a decay
  review that mutates ratings is the GOLDEN feedback loop the project forbids.
- **Do not run it against a moving DB during a `/db-update`.** The chain takes the
  exclusive DuckDB lock; wait, or point `--db` at a copy (`DEFAULT_DB_PATH` is
  relative, so a scratch cwd plus a DB copy isolates a long job from the 15-min writer).
- Report a negative or unchanged result honestly. A decayed book is a result.
