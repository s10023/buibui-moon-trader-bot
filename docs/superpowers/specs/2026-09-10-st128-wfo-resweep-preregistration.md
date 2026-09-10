# ST128 — pre-registration for the corrected WFO re-sweep

**Date:** 2026-09-10 · **Status:** pre-registered, unrun · **SoT row:** ST128

This file fixes the decision rule **before** the corrected sweep runs. It exists because the
alternative — sweep, then decide what to apply — conditions the rule on the answer, which is
the failure mode memory `feedback_post_hoc_robustness_conditioned_on_answer.md` records: you
only bound a result you dislike, so pessimistic rules get audited and optimistic ones ship.

## 1. What is being corrected

Three defects, all in the path `AGENTS.md` calls the trusted production source of `tp_r`.
Every live `tp_r` in the three `signal_watch*.toml` was fitted through it.

| # | Defect | Status |
| --- | --- | --- |
| a | `min_sl_pct` never passed — the sweep book had **no stop floor**, admitting sub-0.5% structural stops each paying over 1R of drag | plumbing FIXED, PR #764 |
| b | `slippage_pct` never passed — drag priced `2(fee+0) = 0.0010` against production's `0.0014`, **28.6% light** | plumbing FIXED, PR #764 |
| c | `--day-filter` refused `mon_fri` and `weekend` — **2 of the 3 live configs could not be swept on their own population at all** | FIXED this branch |

(c) was found 2026-09-10 while pricing the re-sweep and is new. The engine was always
capable — `analytics/param_sweep.py:426` and `:833` delegate to `_day_filter_to_weekdays`,
which knows all six modes — but `cli/param.py` validated against a **restated**
`["off", "weekdays", "tue_thu"]` at both WFO sites, so both were argparse errors:

```text
mon_fri    REJECTED: invalid choice: 'mon_fri' (choose from 'off', 'weekdays', 'tue_thu')
weekend    REJECTED: invalid choice: 'weekend' (choose from 'off', 'weekdays', 'tue_thu')
```

⚠ **No available choice isolates either population.** `weekdays` (Mon–Fri) and `tue_thu`
both contain **zero** weekend days; `weekdays` carries Mon and Fri plus three days the
`mon_fri` config never runs on. So the substitution is not an approximation — it is a
different book.

⚠ **The list was restated away from the function that interprets it, and that is the defect
rather than its narrowness.** `_day_filter_to_weekdays` answers an unknown mode with `None`,
which means *no filter*. The two halves therefore fail in opposite silent directions: a mode
the CLI refuses is unreachable, and a typo the CLI accepts sweeps every day. The fix defines
`DAY_FILTER_MODES` once, beside that function, and both CLI sites derive from it.

### 1b. A COMPETING HYPOTHESIS for how the weekdays config got its values — not established

`/wfo-sweep` Step 4 carries a **Cross-config sync** rule: *"For TFs active in weekdays
config, apply the same tp_r changes (they share the same market)"*, while correctly refusing
the same propagation into `signal_watch_all.toml` on the grounds of *"different day_filter
distribution"*. But `tue_thu` and `mon_fri` are **disjoint**, so that reason applies to the
propagation the rule permits. That is a coherent mechanism for values the CLI could not have
produced directly.

⛔ **File it as a lead, not a finding.** What is established is that the rule says to do it
and that a direct `mon_fri` sweep was impossible. Whether it is what actually happened is
recoverable only from the TOML comment provenance (`# WFO OOS (weekdays)` on
`signal_watch_weekdays.toml` cells is *suggestive* — it names a filter that is neither the
config's own nor a valid isolation of it) and is **out of scope for this run**.

## 2. The decision rule — pre-committed

Reused verbatim from `/wfo-sweep` Step 4 rather than reinvented, with two corrections. A cell
is `(config × strategy × timeframe × symbol)`.

**Hard refusal, checked first.** Every `param-sweep` emits a `COMMIT-GATE` line (DSR + PBO +
MinTRL).

- `✓ PASS` → continue to the pick.
- `✗ DO-NOT-COMMIT` → **skip; never write its `tp_r`.** Record the failing metric.
- `⚠ INSUFFICIENT` → **skip.**
- No override without an explicit operator instruction naming the cell.

**Picking `tp_r`,** for gate-passing cells only, in order:

1. Drop rows flagged `⚠ OVERFIT`.
2. Drop rows with OOS n below the per-TF floor: `15m→20, 1h→12, 4h→5, 1d→2`.
3. Drop rows with OOS `avg_r ≤ 0`.
4. Take the highest OOS `avg_r`; its `tp_r` wins.
5. All rows dropped → skip the cell, keep the current value, record why.

**Writing to the TOML.** Update only when the winner differs from the current value by
**≥ 0.5** *or* improves OOS `avg_r` by **> +0.05R**. Below both, leave it. Never add a
`strategy_timeframes` entry on sweep evidence alone.

**Global vs per-symbol.** Symbols agreeing within one 0.5 step → strategy-level or TF-level
key. Any symbol diverging by more than 0.5 → per-symbol override.

### Correction 1 — cross-config sync is REVOKED

Each config is swept on **its own `day_filter`** and its results apply to **that config
only**. No propagation between configs in any direction. The three populations are disjoint
by construction (`tue_thu` = Tue–Thu, `mon_fri` = Mon+Fri, `weekend` = Sat+Sun), which is why
they are three files.

### Correction 2 — a cell that loses its edge is REPORTED, never silently kept

The rule above skips a cell whose every row fails, leaving the old value in place. Under
(a)–(c) that old value was fitted on a cheaper, floorless, and sometimes wrong-population
book, so "keep current" is not neutral — it preserves the defect. Every skipped cell is
therefore listed in the run's output with its reason, and a cell whose **current** `tp_r`
would now fail the OOS filter is flagged **DEFECT-CARRYING**. What to do about those is an
operator decision this file does not pre-empt; the requirement is that they be visible rather
than absorbed into a "no change" line.

## 3. What this run does NOT decide

- **It does not write any TOML.** See §4 — that is a live deployment, not a diff.
- **It does not touch `live_parity`.** ST128 filed it unpassed and deliberately unfixed: a
  WFO sweep may legitimately want ungated signal. Still owed a ruling; not settled here.
- **It does not recalibrate.** See §5.
- **It does not re-open ST122.** ST122's premise is falsified and it stays closed.

## 4. ⛔ Writing these TOMLs IS the deployment

`buibui-signal-watch.service` sets `WorkingDirectory=/home/kng/repo/buibui-moon-trader-bot`
and runs `buibui.py` from the repo root — **the working tree**, on a 15-minute timer. So the
moment `config/signal_watch*.toml` is written, the next scan alerts on the new values: days
before any PR merges, with no review window between the edit and live behaviour.

⇒ **Applying is a separate, deliberate act, taken with the delta already in front of the
operator**, and never a step that happens to follow a sweep. A session that has the table is
not thereby authorised to write the files.

## 5. Sequencing against ST122

ST122's remedy is a production backtest, sequenced *against* ST128 — so the ST128 backtest is
its prerequisite, not its competitor. They must not share a change: if the TOMLs move and a
recalibrate runs together, neither movement is attributable. Order:

1. This pre-registration lands. *(this branch)*
2. Corrected sweep runs; delta reported. **No writes.**
3. Operator rules on applying. If yes, TOMLs move in a change of their own.
4. Only then the production backtest, and only then ST122's recalibrate.

## 6. Disclosure — one cell was run before this rule was written

Pricing (c) required proving a `weekend` sweep produces a real population end to end. That
run was `engulfing / BTCUSDT / 15m`, `--day-filter weekend`, `--since 2025-09-12`, against a
snapshot: 392 IS / 168 OOS trades, and every `tp_r` row failed OOS validation, against a
filed `+0.24R` in `signal_watch_all.toml`.

It is disclosed rather than discarded because the rule above was written after it. Two things
limit the contamination: the rule is **mechanical**, taking no per-cell judgement that a
glimpsed cell could bias; and that cell is one symbol where the filed figure is cross-symbol,
so it is not a comparable reading. ⚠ **It is not evidence for anything and must not be quoted
as a result** — it is a capability check that happened to print numbers.

## 7. Cost

~350 cells at **1.8s** measured wall each ⇒ roughly 10 minutes, single-threaded, read-only
against a snapshot copy of `analytics.db` so the daemon keeps its write lock. Cheap enough
that a re-run under a revised rule is affordable; that is deliberate, so the rule can be
revised openly rather than stretched to fit one run.

## Decision Log

| Decision | Observable that REVERSES it |
| --- | --- |
| Sweep each config on its own `day_filter`; no cross-config propagation | A measurement showing the three populations' `tp_r` optima agree within one 0.5 step across a majority of cells — then one sweep serves all three and the split is ceremony |
| Decision rule reused from `/wfo-sweep` Step 4 rather than redesigned | The corrected sweep skipping so many cells on the COMMIT-GATE that the run yields no actionable delta — that would make the gate, not `tp_r`, the thing to re-examine |
| Applying is a separate act from sweeping | The signal-watch timer ceasing to run the working tree (e.g. running a pinned commit or a container image) — the hazard in §4 is entirely a consequence of that coupling |
| `DAY_FILTER_MODES` as a shared tuple rather than a `Literal` type | mypy strict gaining the ability to check TOML-sourced strings against the type, which would move the guarantee from a test to the type checker |
| One glimpsed cell disclosed rather than the run restarted clean | Evidence that the mechanical rule admits per-cell discretion after all — then the glimpse matters and the sweep needs a fresh operator-set rule |
