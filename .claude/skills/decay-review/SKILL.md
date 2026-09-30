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

## Before any leg — read what the legs touch

A "new" finding about a surface an Issue or an earlier report already owns is a re-sighting until
proved otherwise, and a check placed at close-out runs after the wrong finding is written. So read
these first, not last:

1. **The previous report's carry-forward list** (its "What this changes" section) — the questions
   this run owes an answer to.
2. **The Issues owning each leg's surface** — `gh issue list -R s10023/buibui-moon-trader-bot
   --state all --search "decay in:title,body"` finds them (at 2026-09-30: #837 = ST137 owns Leg 3's
   method; ST122, Leg 1's flat readings, closed before the move and lives in memory's closed archive).
3. **Every cell the previous report names, across ALL prior reports** —
   `grep -l '<cell>' docs/plans/scratch/decay-review-*.md` — before counting its readings or calling
   it new.

Both misses this prevents have happened: 09-19 filed the ratings freeze as new with ST122 already
falsifying it, and called `morning_evening_star 4h short` "the first cell to be worst twice" when
it had been worst since the first review (08-11).

## Step 0 — TAKE THE DB COPY FIRST, before any leg

```bash
# Default: the newest VERIFIED backup snapshot (MANIFEST.json beside it) — zero contact with the live lock.
SNAP="$(ls -d "${BUIBUI_BACKUP_ROOT:-$HOME/backups/buibui}"/daily/*/ | tail -1)analytics.db"
D="$SCRATCH/decay.db"
cp "$SNAP" "$D"
command -v cygpath >/dev/null && D="$(cygpath -m "$D")"   # Windows: forward slashes, see below
```

⚠ **On Windows, `DB=` must be a forward-slash path.** `$SCRATCH`/`$TEMP` carry backslashes, and the
target's recipe shell strips them, so Leg 1 dies `cannot find the path specified` on a path glued
into the repo root (measured 2026-09-28). `cygpath -m` converts; on Linux it is absent and the line
is a no-op.

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
make buibui-decay-review DB="$D"                           # against the Step 0 copy
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
make buibui-portfolio-replay DB="$D"                  # the Step 0 copy
make buibui-portfolio-replay                          # live DB — only outside a write window
```

⛔ **Pass `DB=`, or Step 0 was pointless.** `DB=` forwards to the CLI's `--db`; without it this
leg silently reads the LIVE database while Leg 1 read the copy, so the two describe different
instants and are no longer one reading — the exact failure Step 0 exists to prevent, reached
from the other side. The target carried no `DB=` at all until 2026-09-06: the skill said
"take the copy first" and then handed you a command that could not use it, so the 09-06 review
had to bypass `make` to stay consistent.

⚠ **The headline metrics are at the TOP, above a ~60-row attribution table — do not `tail` this.**
Sharpe / Sortino / Calmar / max drawdown print immediately after the era check, then the
per-strategy×tf×direction rows run to the bottom. Piping through `tail -45` looks reasonable and
silently discards every number the leg exists to produce, costing a full re-run (measured
2026-08-29). Redirect the whole thing to a file, or `grep -A12 'HEADLINE'`.

Read-only replay of the live outcome ledger through the Carver sizing model. Join the
attribution extremes to the star rating **of the same cell** — that join is the point
of the leg, because it puts [[star-ratings-no-live-signal]] into *sized P&L* rather
than per-alert R.

Four traps:

- **Capital basis is the configured `[portfolio] capital`, not live equity** unless
  you override it. Absolute dollars are therefore not the account's dollars.
- **Positive rows are mostly n≤3. Do not read them as edges.** Sort by n before
  reading any avg_r.
- ⚠ **This leg is NOT a pure accumulation — a cell's n can go DOWN between reviews.**
  Carver sizing is portfolio-state-dependent, so adding later trades changes which
  *earlier* ones get sized at all. Measured 2026-09-06: `pin_bar 15m short` went n=36 →
  **35** while the ledger grew ~700 rows (`skipped` 5,964 → 6,648 against 402 sized).
  **So a per-cell n delta is not ledger movement** — Leg 3's control is what says whether
  the ledger moved, and that week it was 0/0/0. ⛔ **Do not build a "loss concentration"
  narrative from one week's attribution tail either.** The two cells 08-29 named as the
  concentration had rotated out entirely eight days later (`inside_bar 15m short` −114.56
  → −29.08, `pin_bar 15m short` −106.86 → −2.87), replaced by a new worst cell at n=7.
  At these n the tail rotates; only a cell that stays worst across **three** reports is
  worth naming.
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
236 to 745 rows and its expired share fell 0.767 → 0.409. Confirmed a fourth time 2026-09-06:
2026-08-b matured 1,258 → 1,562 rows and **improved +0.0062**.

**Bucket in UTC, explicitly.** Population `outcome IS NOT NULL`; bucket
`strftime(to_timestamp(outcome_filled_at_ms/1000) AT TIME ZONE 'UTC', '%Y-%m')` plus `-a` when
the UTC day ≤ 15, else `-b`. ⚠ **Without `AT TIME ZONE 'UTC'` DuckDB buckets in the HOST's local
zone** — its `TimeZone` setting defaults to the machine's, `Asia/Kuala_Lumpur` on the laptop. That
is the whole of the 19–25-row cross-report bucket gap (2026-03-b 791 vs 810, 2026-08-a 1,173 vs
1,198): on one snapshot, MYT bucketing reproduces 09-19's table to the row and UTC reproduces
09-06's (measured 2026-09-28, SoT ST137). Pooled figures are timezone-independent, which is why
they always reproduced while buckets did not.

**Reproduce the previous report's headline figure off its own snapshot before quoting a
week-over-week delta** — it costs one query and it is what makes the delta a finding rather than
an artifact.

## Report + close out

Write to `docs/plans/scratch/decay-review-<YYYY-MM-DD>.md` (gitignored — no PR, no
CI, no repo flip). Structure that has worked: Headline · Leg 1 · Leg 2 · Leg 3 status ·
What this changes. Then stamp the marker.

Lead with what **moved** since the previous report in that directory. If nothing moved,
say so plainly — "unchanged" is the correct finding most weeks and is worth one line,
not a rebuilt table.

**Then reconcile the numbers this report just superseded.** Nothing else propagates
a decay-review figure — it is written once and quoted forever — so grep the three
always-read surfaces (`MEMORY.md`, the handoff, the open Issues) for the population figures
this run replaced and update them in place. Measured 2026-08-13c: the report recorded
★≥4 `66 → 55` stored and `58 → 47` declared while all three surfaces still carried
`66 → 58` hours later, and a session verifying the prune spent a detour proving its own
correct measurement right against a filed number that was one run stale.

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
