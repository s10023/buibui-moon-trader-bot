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

## Step 0 — TAKE THE DB COPY FIRST, before any leg

```bash
cp analytics.db "$SCRATCH/decay.db"        # or use today's daily/<date>/analytics.db
```

⛔ **Not optional, and not a fallback for when a leg fails.** The 15-min signal-watch writer
takes the **exclusive** DuckDB lock at `:01/16/31/46`, so a leg started at the wrong minute dies
with `IOException: Conflicting lock is held ... by user kng` — and a review is 3+ legs over
several minutes, so it *will* straddle a write window. Measured 2026-08-29: Legs 1 and 2 got
through on the live DB and Leg 3 was refused mid-review, which is the worst outcome because it is
silent about the real cost — **the legs then describe different instants and are no longer one
reading.** The 08-23 review took one isolated copy at 09:06 UTC for exactly this reason and said
so in its own header.

⚠ **Do NOT `cp` while the writer holds the lock** — that risks a torn copy. Either copy between
windows, or use the verified `daily/<date>/analytics.db` snapshot, which the backup script has
already checked. A backup-to-backup comparison is *better* than live-vs-backup for Leg 3, because
both ends are then verified snapshots taken the same way.

## Leg 1 — DSR-suspect list + gate reachability

```bash
make buibui-decay-review DB="$SCRATCH/decay.db"            # against the Step 0 copy
make buibui-decay-review                                   # live DB — only outside a write window
make buibui-decay-review CONFIG=config/signal_watch.toml   # one config
```

Read-only. Rebuilds the exact pools `compute_dsr_ratings` uses via the shared
`recalibrate_lib.select_rated_run_ids` — never a local copy of the ranking.

**Scope resolution: `day_filter` and `adr_suppress_threshold` travel TOGETHER.**
The bare invocation resolves both from each of the three live configs
(0.75 / 0.65 / 0.70), mirroring `recalibrate_runner`. `DAY_FILTER=` alone now
**exits 1** rather than running: it leaves the threshold at `None`, which
`select_rated_run_ids` renders as `adr_suppress_threshold IS NULL` — a pool no
live config writes, frozen since 2026-04-09. **That was the default until
2026-08-13, so every review before then, including the 08-11 first run, audited
it.** The verdict direction survived (0 cells clear either way), which is exactly
why it went unnoticed; every *named cell* was wrong.

**Each config scope now opens with an ERA CHECK** (`analytics.eras`, added
2026-08-14d). It names how many behaviour-changing commits the rated pool spans and
how large its largest single-era sub-sample is. First reading: **56 boundaries, only
55% of runs post-dating the newest** — so roughly half the pool feeding the star
ratings was produced by older code or config, and every Sharpe below it is an
average ACROSS rule changes rather than a measurement of the current book. Read it
before the cells, not after. It is advisory by design (every sample here straddles
something, so a hard failure would be switched off within a week), and it keys on
`backtest_runs.run_at_ms` — **never `entry_time`, which is simulated market time**.

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

**⚠ The gap between the two closes on the next `recalibrate --apply`** (2026-08-13):
`prune_undeclared_ratings` now deletes orphan rows, where previously nothing did and
a full `/db-update` left the set at 206 → 206. So a stored-vs-declared gap is no
longer permanent — a *reappearing* one means a config shed cells since the last
recalibrate, and a *large* one means the prune's share guard refused. Do not read
either as the old standing condition. The first prune moves the ★≥4 population from
66 to 58, so a week-over-week delta that straddles it is measuring the prune, not decay.

**Expected result, so you are not surprised into a finding:** 0 cells clear DSR 0.95,
in every scope, and `REACHABLE: NO`. That is the standing verdict, not news. The
week-over-week *delta* is the deliverable.

## Leg 2 — per-sleeve attribution (P1 replay)

```bash
make buibui-portfolio-replay          # CONFIG= / CAPITAL= / VOL_TARGET= override
```

⚠ **The headline metrics are at the TOP, above a ~60-row attribution table — do not `tail` this.**
Sharpe / Sortino / Calmar / max drawdown print immediately after the era check, then the
per-strategy×tf×direction rows run to the bottom. Piping through `tail -45` looks reasonable and
silently discards every number the leg exists to produce, costing a full re-run (measured
2026-08-29). Redirect the whole thing to a file, or `grep -A12 'HEADLINE'`.

Read-only replay of the live outcome ledger through the Carver sizing model. Join the
attribution extremes to the star rating **of the same cell** — that join is the point
of the leg, because it puts [[star-ratings-no-live-signal]] into *sized P&L* rather
than per-alert R.

Three traps:

- **Capital basis is the configured `[portfolio] capital`, not live equity** unless
  you override it. Absolute dollars are therefore not the account's dollars.
- **Positive rows are mostly n≤3. Do not read them as edges.** Sort by n before
  reading any avg_r.
- ⚠ **Do NOT compare this leg against a decay review run before 2026-08-14.** Since
  PR #628 the replay restates the pre-`e5d92bb` half of the ledger onto the net-of-cost
  basis, so every figure here shifts down by roughly the cost drag (~0.098R per losing
  row, and *more* for narrow-stop cells because drag scales as `entry / risk`). **That
  shift is an accounting correction, not decay** — reading it as decay is the one
  misdiagnosis this leg is least equipped to catch, since a uniform downward move across
  all sleeves is exactly what real decay would look like. Older reports are on the mixed
  basis; re-run rather than compare, or pass `restate_cost_basis=False` to reproduce one.

## Leg 3 — live-ledger delta

**LIVE since 2026-08-13**, when the soak verdict passed 7/7 and lifted the recording-bias
hold (the live sample had been 35% complete with a session-skewed run-hour distribution — a
*biased* base, not merely a thin one). Key on `outcome_filled_at_ms`, never `candle_ts_ms`:
fired-keyed overstates a 7-day window by 0.114R.

Run the **row-level diff** against the previous snapshot — restatements vs new resolutions —
not the byte-identical check, which conflates the two.

⚠ **The newest half-month bucket is structurally the worst-looking one and is not a reading
until its expired share falls.** Three consecutive reports flagged it as decay and were wrong
each time; measured 2026-08-23, the 2026-08-b bucket improved **+0.157R** as it filled from
236 to 745 rows and its expired share fell 0.767 → 0.409.

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
