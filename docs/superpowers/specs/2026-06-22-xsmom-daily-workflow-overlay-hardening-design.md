# XS-solo daily-workflow integration + overlay hardening — design

**Date:** 2026-06-22
**Status:** approved (brainstorm) → ready for implementation plan
**Scope:** P3 XS-solo live wiring, sub-project #3 — operational hardening (Task B,
items 1–4). Scheduling/alerting (item 5) is **deferred** to the going-live/ops phase.
**Predecessors:** PR #451 (read-only daily targets), PR #452 (order routing + risk overlay).

## Problem

The XS-solo executor (PR #452) is merged but not yet usable day-to-day. A 2026-06-22
dry-run against a partially-synced DB surfaced three binding gaps plus one mis-calibration:

1. **Stale daily bars silently degenerate the book.** `analytics sync` defaults to
   `--timeframes 1h 4h` and never syncs `1d` — but the XS book runs on `1d` only. The
   live daemon keeps only the `coins.json` majors (BTC/ETH/SOL) `1d` fresh, so the 22
   universe alts' daily bars go stale. The book collapsed to a degenerate 3-name
   majors-only cross-section. There is no `make` wrapper for the correct sync.

2. **No breadth guard.** The 3-name book was *not* blocked: the overlay's staleness
   guard passed because the *active set* (the 3 fresh names) was fresh. A thin/degenerate
   cross-section is not the validated strategy and must be refused.

3. **The full book can never be established.** The per-run turnover guard
   (`max_run_turnover_frac = 1.0×`) correctly fits steady-state daily rebalancing but
   blocks the initial full-book build (day-1 trades ≈ full gross), so the overlay aborts
   every cold start.

4. **Overlay envelope mis-calibrated to the real book.** The field test showed the full
   25-leg book at `gross 2.88× governed / 5.76× ungoverned`, with the causal vol governor
   at its **0.5× floor** (2.88 = 5.76 × 0.5). The governor clips to [0.5×, 1.5×], so at
   the validated 20% vol target the **governed gross ranges ~2.88× up to ~8.64×**. The
   `max_gross_leverage = 3.0×` check only *passed* because the governor happened to be at
   its floor; on a calmer day it would abort legitimately.

## Goal & success metric

Make `make buibui-xsmom-execute` (dry-run) a trustworthy daily tool: a freshly-synced
full-breadth book builds and passes the overlay on a cold start, while degenerate or
anomalous books are refused. **Success = on a fresh `1d` universe sync, a cold-start
dry-run produces the full ~25-leg book and the overlay reports `allowed=True`; a stale
3-name book reports `allowed=False` with a breadth abort.**

Non-goals: no unattended scheduling, no mainnet writes, no engine/golden changes. Sharpe
is scale-invariant to vol target, so the sizing change below costs nothing in
risk-adjusted terms — it only scales absolute exposure.

## Design

### Item 1 — `make buibui-universe-sync` (the missing daily `1d` sync)

New Makefile target wrapping the already-valid CLI invocation:

```text
buibui-universe-sync:  ## Incremental 1d sync for the full research universe (XS book input)
    PYTHONPATH=. poetry run python buibui.py analytics sync --universe --timeframes 1d
```

(Recipe bodies are shown space-indented for the doc linter; the actual Makefile uses tabs.)

- Incremental (`sync`, not `backfill`); deep history already exists via `universe-backfill`.
- `1d` only — the XS book uses no other timeframe (`1w` not needed).
- Add the target to `.PHONY` and to CLAUDE.md's `buibui-*` wrapper inventory (closes the
  "every CLI invocation has a wrapper" gap).
- This is the **prevention** for the degenerate-book bug; the breadth guard (Item 2) is the
  fail-safe for when it is forgotten.

### Item 2 — Min-breadth overlay guard (fail-safe)

`TargetBook` already exposes `active_count` (count of active, non-NaN legs).

- Add field `min_active_positions: int` to the frozen `RiskLimits` dataclass.
- In `evaluate_overlay`, append a breach when `book.active_count < limits.min_active_positions`:
  `f"thin book: active_count {book.active_count} < min {limits.min_active_positions}"`.
- Default **15** (~60% of the 25-name universe). The degenerate 3-name book is refused;
  a healthy full book (~25 active) passes. Configurable via a new
  `--min-active-positions` CLI arg on `tools/xsmom_execute.py` (default 15).

### Item 3 — Overlay recalibration + cold-start turnover allowance

**3a. `vol_target` config knob.** `ForecastConfig.vol_target_annual` (default `0.20`) is the
single value feeding both sleeve books and the governor. Expose it as a per-run override:

- Add `--vol-target` to `tools/xsmom_execute.py` (and, for parity, `tools/xsmom_targets.py`),
  default `0.20`.
- Thread it into the `ForecastConfig` used by `replay_targets` via
  `dataclasses.replace(cfg, vol_target_annual=<override>)`.
- **Deploy recommendation (documented in the driver `--help` and the spec):** ship default
  `0.20` (validated), but run the first supervised live cycles at **`0.10`** → governed gross
  ~1.4–4.3× instead of ~2.9–8.6×, same Sharpe, half the blast radius on a ~$1.8k account.
  Raise toward 0.20 once the live path is trusted.

**3b. Recalibrated overlay defaults** (in `tools/xsmom_execute.py` argparse), sized for the
recommended 10%-vol deploy so the dry-run stops spuriously aborting:

| limit | current | proposed | rationale |
| --- | --- | --- | --- |
| `max_gross_leverage` | 3.0 | **4.5** | above the 10%-vol book's max governed gross (~4.3×). Run at 20% → raise to ~9.0 (documented). |
| `max_run_turnover_frac` | 1.0 | 1.0 | unchanged; preserves tight steady-state churn detection (cold-start handled by 3c). |
| `min_active_positions` | — | **15** | new breadth guard (Item 2). |
| `max_drawdown_frac` | 0.25 | 0.25 | unchanged. |
| `max_position_notional_frac` | 0.5 | 0.5 | unchanged; a single leg of a 25-name book is ≈ gross/25 ≪ 0.5. |
| `max_data_staleness_hours` | 36.0 | 36.0 | unchanged. |

**3c. Cold-start turnover allowance (auto-detected, no manual flag).** `evaluate_overlay`
gains a required parameter `current_gross_notional: float` (the executor computes it from the
positions it already fetched: `sum(abs(p.notional) for p in current_positions)`). Replace the
single turnover check with a two-branch cap:

```text
target_gross_notional = plan.target_gross_leverage * book.capital
establishing = current_gross_notional < 0.5 * target_gross_notional
turnover_cap = (limits.max_gross_leverage if establishing
               else limits.max_run_turnover_frac) * book.capital
```

- **Establishing** (current gross < half the target gross — a fresh start, post-liquidation,
  or post-kill-switch resume): allow turnover up to `max_gross_leverage × capital`. The build
  is already bounded by the separate gross-leverage guard, so this is not a new exposure risk —
  it only stops the turnover guard from double-blocking a legitimate build.
- **Steady state:** the tight `max_run_turnover_frac × capital` (1.0×) cap is unchanged, so
  anomalous daily churn is still caught.
- Rejected alternative — **tranched bootstrap** (build to target over ~3 days under the 1.0×
  cap): more state, under-invested for days, and does not match the "fully invested daily" book
  the strategy was validated as.

The executor (`run_once`) passes the freshly-computed `current_gross_notional` into
`evaluate_overlay`. The 8 existing `evaluate_overlay` test call sites and the single
production caller are updated for the new parameter.

### Item 4 — `make buibui-xsmom-daily` (convenience)

```text
buibui-xsmom-daily:  ## Daily XS workflow: sync universe 1d, then executor dry-run
    $(MAKE) buibui-universe-sync
    $(MAKE) buibui-xsmom-execute
```

One command for a 3rd terminal. Inherits the executor's dry-run default — submits nothing.

## Components & boundaries

- `trade/overlay.py` — `RiskLimits` (+`min_active_positions`), `evaluate_overlay`
  (+`current_gross_notional` param, +breadth breach, two-branch turnover). Stays pure /
  no-I/O; every check remains a one-line unit test.
- `trade/xsmom_executor.py::run_once` — computes `current_gross_notional` from fetched
  positions; passes it + the (vol-target-overridden) config through; otherwise unchanged.
- `tools/xsmom_execute.py` — new args (`--vol-target`, `--min-active-positions`),
  recalibrated overlay defaults, threads vol-target into the replay config.
- `tools/xsmom_targets.py` — `--vol-target` for parity (read-only).
- `Makefile` — `buibui-universe-sync`, `buibui-xsmom-daily` (+`.PHONY`).
- Engine (`analytics/`) and goldens — **untouched** (additive overlay/CLI/Makefile only).

## Error handling

- Fail-closed semantics preserved: all overlay breaches accumulate into `aborts`; any breach
  blocks the whole plan. Breadth and cold-start branches add to (never bypass) the existing
  checks.
- `current_gross_notional` is derived from data the executor already holds; no new I/O, no new
  failure mode. A read failure upstream already aborts before the overlay.

## Testing

- `tests/trade/test_overlay.py`: new cases — breadth guard blocks `active_count < min` and
  passes at/above it; cold-start branch allows establishing turnover up to the gross cap;
  steady-state branch still blocks > `max_run_turnover_frac`; the gross-leverage guard still
  independently catches an over-leveraged target. Update existing call sites for the new param.
- An executor test asserts `current_gross_notional` is computed from positions and forwarded.
- A vol-target override test asserts a lower `vol_target_annual` produces proportionally lower
  book gross (sanity, not a golden).
- No regression-golden movement expected (engine untouched).

## Definition of Done

- `make lint-py` ✓ · `make typecheck` ✓ · `make test` green · `make test-regression` goldens
  **unmoved** (additive change).
- A fresh `make buibui-universe-sync` followed by a cold-start `make buibui-xsmom-execute`
  dry-run yields the full ~25-leg book with `allowed=True`; a stale-DB run yields a breadth
  abort. (Manual acceptance, dry-run only — no writes.)
- CLAUDE.md + README updated for the two new `make` targets and the `--vol-target` /
  `--min-active-positions` flags.

## Deferred (not in this spec)

- Item 5 — scheduling (cron / GH Actions) + Telegram alerting on submitted/failed/aborted
  runs. Pairs with the supervised mainnet flip (Task A), not with a dry-run-only tool.
- Survivorship magnitude check (Task C) — independent pre-capital rigor gate.
