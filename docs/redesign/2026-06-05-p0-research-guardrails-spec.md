# P0 — Research Guardrails (overfitting control + honest costs) — spec

**Date:** 2026-06-05 · **Status:** design / discussion (no code yet) · **Parent:** `docs/redesign/2026-06-05-top-tier-quant-redesign.md` (Layer L3 + L9) · **Siblings:** P1 sizing, exit-improvement specs.

## 0. Goal

Stop the system from committing **in-sample mirages** to live configs. The codebase runs thousands of trials (20 strategies × directions × TFs × symbols × param grids, across `/wfo-sweep`, `/config-refresh`, `/atr-sweep`, gate/adr audits) with **no multiple-testing correction**. That is the textbook generator of the exact pathology already observed: in-sample-positive cells that decay to ≤0 OOS (conditional-edge test, F8 replay, EMA WFO). P0 adds the statistical layer that **gates every commit/promotion decision** in the project — sweeps, audit-tool verdicts, recalibrate, and the P1 paper book — and makes backtest costs honest.

P0 is genuinely *first* in the roadmap: it is the gate every later phase (P1 sizing, exits, new alpha) must report through before anything is committed.

**Validation target (acceptance test):** P0 should *independently reproduce* the OOS-decay verdicts the live ledger already caught — i.e. the in-sample positives that decayed should show **low Deflated Sharpe / high PBO**. If the guard flags the cells the ledger already flagged, it works.

## 1. Two sub-phases (split by blast radius)

- **P0a — pure statistics layer (additive, zero behavioral drift).** New stats module + wire it in as *reporting + commit-gates*. Regression goldens **unchanged**. Low-risk; ships first.
- **P0b — honest costs (behavioral).** Add funding accrual + slippage to the sim P&L. Changes backtest R → **goldens move → `make db-update` + recalibrate**. Higher blast radius; ships second, with a before/after avg_r delta report.

## 2. P0a — the overfitting-control statistics

New module `analytics/research_guards/` (distinct from `analytics/stats/`, which is market seasonality):

| File | Function | What it gives |
| --- | --- | --- |
| `psr.py` | `probabilistic_sharpe_ratio(sr, T, skew, kurt, sr_benchmark)` | P(true SR > benchmark), correcting for non-normality + sample length |
| `dsr.py` | `deflated_sharpe_ratio(sr, trial_srs, T, skew, kurt)` + `expected_max_sharpe(N, var_sr)` | PSR with the benchmark set to the **expected max SR from N trials** — deflates for the search |
| `pbo.py` | `cscv_pbo(perf_matrix, n_splits)` → `(pbo, logits, degradation_slope)` | **Probability of Backtest Overfitting** via Combinatorially Symmetric CV |
| `haircut.py` | `haircut_sharpe(pvalues, method)` (Bonferroni / Holm / BHY) | Harvey-Liu multiple-testing Sharpe haircut |
| `mintrl.py` | `min_track_record_length(sr, skew, kurt, target_sr, conf)` | how many obs are needed before a Sharpe claim is significant |
| `bootstrap.py` | `block_bootstrap_ci(returns, stat_fn, n_boot, block, alpha)` | stationary/block-bootstrap CI on avg_r / Sharpe / max-DD (serial-corr aware) |

### Formula anchors (for the implementer)

- **PSR:** `PSR(SR*) = Φ( (SR̂ − SR*)·√(T−1) / √(1 − γ₃·SR̂ + ((γ₄−1)/4)·SR̂²) )`, γ₃ = skew, γ₄ = kurtosis, Φ = normal CDF.
- **Deflation benchmark:** `SR₀ = √V · [ (1−γ)·Φ⁻¹(1 − 1/N) + γ·Φ⁻¹(1 − 1/(N·e)) ]`, V = variance across the N trials' Sharpes, γ ≈ 0.5772 (Euler-Mascheroni). **DSR = PSR(SR₀).**
- **PBO (CSCV):** split T periods into S even blocks; over all `C(S, S/2)` train/test combinations, pick the IS-best trial, record its OOS rank → logit `λ = ln(rank/(1−rank))`; **PBO = fraction with λ < 0** (IS-best lands below OOS median).
- **N must reflect the full search,** not just kept cells (see §5 honesty note).

## 3. P0a wire-in (the guard must have teeth)

Reporting alone is theater — these gate **commit/promotion**, enforced in the skills:

- **Sweep selection** (`param_sweep.py` / `backtest_runner.py`, consumed by `/wfo-sweep`, `/config-refresh`, `/param-sweep-apply`): after picking best tp_r per cell, compute **DSR** (N = # param candidates; V = variance of their Sharpes) and **PBO** over the trial × period matrix (built from `backtest_trades` per candidate). **Commit rule:** do not write a cell's tp_r unless `DSR ≥ 0.95` **and** `PBO ≤ 0.5` **and** `n ≥ MinTRL`. Else flag **DO-NOT-COMMIT**. The apply-skills must read this and refuse.
- **Audit tools** (`gate_audit.py`, `adr_threshold_audit.py`, new `exit_audit.py`): replace the crude ±0.05R bar with a **bootstrap CI** on the verdict statistic + a **haircut** for the number of cells tested → ENABLE only if the CI excludes the bar *after* haircut.
- **Recalibrate** (`recalibrate_lib.py`): annotate each star rating with its DSR; a 5-star cell with low DSR is flagged suspect (feeds the Backtest UI + alert quality gate).
- **P1 paper book** (`portfolio/metrics.py`): bootstrap CI on Sharpe / max-DD so the first risk-adjusted number ships *with* error bars, not as a point estimate.

## 4. P0b — honest costs (funding + slippage)

**Current state (verified 2026-06-05):** `engine.py::Trade.pnl_r` applies a round-trip taker fee — `fee_drag_r = 2·fee_pct·entry_price/risk` — and already notes "fees consume a large fraction of actual risk" on tight SLs. **Missing: funding accrual and slippage.** Both bias avg_r upward, disproportionately on the high-turnover / tight-SL TA book — i.e. exactly where the false positives live.

Extend the P&L to: **`net_R = raw_R − fee_R − slippage_R − funding_R`** (all in R units, ÷ `risk`):

- **Funding accrual:** sum perp funding over `[entry_ts, exit_ts]` from the already-ingested funding table — `funding_R = Σ(funding_rate · notional · side_sign) / risk`. Directional: longs pay positive funding, shorts receive — materially relevant given the short-edge finding ([[direction-axis-hard-flip]]), and currently invisible.
- **Slippage:** per-leg `slippage_bps` (or `×ATR` fraction), conservative default, configurable via `[backtest]` TOML. Hits 15m hardest → corrects the fast-strategy upward bias.
- **Parity:** mirror the same cost deduction in `analytics/signal/outcome_backfill.py::_scan_forward` so live-ledger R and backtest R both reflect costs (existing live-parity discipline — engine ↔ `_scan_forward` must not diverge).

**Consequence:** many cells get *more* negative. **That is the feature** — it removes a systematic upward bias and will reinforce the prune-to-core direction. Ship with a before/after avg_r delta table; run `make db-update` + recalibrate + regression-update (goldens intentionally move).

## 5. Methodology notes / honesty caveats

- **N-bookkeeping:** true N spans the lifetime of every sweep ever run, not one invocation. Per-sweep N **undercounts** → DSR is optimistic. Interim: treat per-sweep N as a floor and require a strict DSR threshold (≥0.95). Stretch goal: a cumulative trial ledger. Erring conservative (toward skepticism) is the correct direction.
- **Small-n cells:** DSR/PBO are undefined or unstable at tiny n → fall back to `MinTRL` + the existing n-floor and emit **INSUFFICIENT** (never commit).
- **Bootstrap block length:** auto-select (Politis-White) or `T^(1/3)`; returns are serially correlated, so naive iid bootstrap understates CIs.
- **Cost conservatism:** over-estimate slippage rather than under — false skepticism is cheap, false confidence is not.

## 6. Module + rollout

```text
analytics/research_guards/
  __init__.py · psr.py · dsr.py · pbo.py · haircut.py · mintrl.py · bootstrap.py
tests/  test_psr.py test_dsr.py test_pbo.py test_haircut.py test_mintrl.py test_bootstrap.py
```

1. **P0a-1** ship the stats module + tests (pure functions, golden-stable, validated against published worked examples).
2. **P0a-2** wire DSR/PBO into the sweep output + the commit-gate in `/config-refresh` / `/wfo-sweep` / `/param-sweep-apply`; bootstrap CI into the audit tools and P1 metrics. (Additive; goldens unchanged.)
3. **P0a-3 (validation)** run the guard over the current configs → confirm it flags the known OOS-decay cells (acceptance test §0).
4. **P0b** add funding + slippage to `engine.py` + `_scan_forward`; `make db-update` + recalibrate + regression-update; publish the avg_r delta.

## 7. Scope boundary (defer)

- **White's Reality Check / Hansen SPA** ("is the *best* of N strategies better than benchmark?") — `bootstrap.py` provides the machinery; defer to **P0.5**.
- **Purged k-fold + embargo CV** (López de Prado) as a replacement for the current WFO split — defer; the WFO split + PBO covers the immediate need.

## 8. Open decisions

1. **Thresholds:** commit-gate at `DSR ≥ 0.95`, `PBO ≤ 0.5` (or stricter ≤0.2)? Haircut method = Holm default?
2. **Enforcement point:** hard-block in the apply-skills, or surface a loud warning the user must override? Recommend hard-block with explicit override flag.
3. **P0b cost defaults:** slippage_bps per tf (e.g. 1–3 bps majors) — pick conservative starting values.
4. **Cumulative trial ledger** (true N) — build now or accept per-sweep-N floor for v1? Recommend floor for v1.
5. **Sequencing vs P1:** P0a before P1 (so P1's Sharpe ships with CIs + the sweep gate is live), P0b can run in parallel or just after. Recommend P0a-1/2 → P1 → P0b.
