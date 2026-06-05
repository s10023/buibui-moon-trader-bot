# P0a-2 sub-PR 1 — sweep commit-gate (param-sweep) — plan

**Date:** 2026-06-06 · **Status:** in progress · **Parent:** `docs/redesign/2026-06-05-p0-research-guardrails-spec.md` §3 · **Sibling specs:** P1 sizing, exit-improvement.

## Goal

Give the research-guards (PR #421) teeth at the **single highest-leverage commit point**: the
`tp_r` chosen by `/wfo-sweep` + `/param-sweep-apply`. After the WFO grid picks a recommended
config, refuse to commit it unless it clears the overfitting gate:

```text
COMMIT  ⟺  DSR ≥ 0.95  ∧  PBO ≤ 0.5  ∧  n_obs ≥ MinTRL(0.95)
```

Anything short of all three → **DO-NOT-COMMIT** (failed a hard check) or **INSUFFICIENT**
(too few trades/trials to even evaluate — never commit).

This is **additive to** the existing OOS filter the skill already applies (drop `⚠ OVERFIT`,
require OOS `avg_r > 0` and OOS `n ≥ floor`). The gate adds the multiple-testing correction the
project currently lacks, on top of that filter.

## Scope (this PR)

Wire the gate into the **deep per-cell sweep only** — the path that picks the committed `tp_r`:

- `analytics/param_sweep.py::run_param_sweep` → `format_sweep_results` (consumed by
  `buibui param-sweep`, driven by `/wfo-sweep` + `/param-sweep-apply`).

Deferred to later sub-PRs (per the P0a-2 handoff):

- `run_strategy_audit` (param-audit) — triage only, does not pick the committed `tp_r`.
- `config-refresh` full-dataset `tp_r` sweep (`backtest_runner.run_backtest_sweep`).
- Audit-tool bootstrap-CI + haircut (`gate_audit.py`, `adr_threshold_audit.py`) — sub-PR 2.
- Recalibrate DSR-annotation — sub-PR 3.

## Inputs available (verified)

- `SweepRow` carries `is_result` + `oos_result` (`BacktestResult`); each `BacktestResult.closed_trades`
  is a list of `Trade` with `.pnl_r` (R after fees) and `.entry_time` (ms).
- The full grid lives in `run_param_sweep` **before** the `rows[:top_n]` truncation → that is where
  N (= grid size) and the cross-trial Sharpe variance are honest. `--top-n` default is 10 vs a
  99-combo default grid, so the gate **must** be computed pre-truncation.

## Design

### New pure module `analytics/sweep_guard.py` (no DB/IO; numpy + research_guards only)

```text
@dataclass(frozen=True) TrialPerf:   label: str; returns: list[float]; times: list[int]
@dataclass(frozen=True) CommitGateVerdict:
    decision: "COMMIT" | "DO_NOT_COMMIT" | "INSUFFICIENT"
    dsr, pbo, min_trl: float | None ; n_obs, n_trials: int ; reasons: list[str]
    committable: bool  (property: decision == "COMMIT")

evaluate_commit_gate(chosen, all_trials, *, n_grid,
                     dsr_threshold=0.95, pbo_threshold=0.5, mintrl_confidence=0.95,
                     n_splits=14) -> CommitGateVerdict
```

- **Sharpe** of a trial = `mean(R) / std(R, ddof=1)` over its full-window (IS+OOS) closed-trade R;
  `0.0` when `< 2` trades or `std == 0`.
- **DSR**: `deflated_sharpe_ratio(sr=chosen_sharpe, n_obs=len(chosen.returns),
  trial_srs=[sharpe(t) for t in all_trials])`. `n_grid` (true grid size, ≥ len(all_trials)) is the
  N-floor; when `n_grid > len(all_trials)` (top_n truncation) pass `n_trials=n_grid` +
  `sr_variance=var(trial_srs)` so N is honest and only V is sampled — the conservative direction.
- **PBO**: build a `(2·n_splits, n_trials)` calendar-binned perf matrix over the union trade-time
  span (each cell = Σ pnl_r of that trial's trades in that bin) → `cscv_pbo(matrix, n_splits=14)`.
- **MinTRL**: `min_track_record_length(chosen_sharpe, confidence=0.95)`; pass ⟺ `n_obs ≥ ceil(MinTRL)`.
- **INSUFFICIENT** when: `< 2` trials, or chosen trial `< 2·n_splits` trades (can't fill the PBO
  matrix / Sharpe unstable), or chosen Sharpe `≤ 0` (MinTRL = ∞). Never commits.

### Wiring

- `run_param_sweep` returns `ParamSweepReport(rows=rows[:top_n], gate=verdict, n_grid=len(grid))`
  (was `list[SweepRow]`). Only caller `cli/param.py::run_param_sweep` updated; no test calls it.
- Shared `_recommended_row(rows)` extracts the existing `clean[0]` selection so the formatter and
  the gate agree on the chosen trial.
- `format_sweep_results(..., *, gate=None)` renders a `COMMIT / ✗ DO-NOT-COMMIT / INSUFFICIENT`
  stamp under the Recommended block with DSR/PBO/MinTRL/n. `gate=None` → no stamp (back-compat;
  existing tests unchanged).

### Skills

- `param-sweep-apply` + `wfo-sweep`: add a hard pre-write rule — **do not write a cell's `tp_r`
  unless the sweep output stamps `COMMIT`**; `DO-NOT-COMMIT` / `INSUFFICIENT` → skip + note the
  failing metric. `--override` escape hatch documented but off by default.

## DoD

- `make lint-py` + `make typecheck` (mypy strict) + `make test` green; **`make test-regression`
  3/3 unmoved** (additive — no DB/cost/selection-value change, only added stdout + refusal step).
- New `tests/test_sweep_guard.py` (worked-example + property anchors: noise → DO-NOT-COMMIT via low
  DSR; a clean strong edge → COMMIT; thin cell → INSUFFICIENT).
- `/pr-summary` + `/post-branch`.
