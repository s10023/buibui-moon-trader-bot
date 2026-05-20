# T6 Phase A — Engine-Side Gate Audit (Plan)

**Status:** DRAFT 2026-05-15. Runs **in parallel** with T6 Phase B (live-gate port).

**Goal:** prove every currently-enabled engine-side toggle actually earns its keep. Zero-based budget: each toggle must re-justify itself empirically against the +0.05R bar from `[[feedback_data_driven_strategy_cuts]]`, or be removed.

**Not in scope (deferred to existing skills):**

- `tp_r_*` / `tp_r_long` / `tp_r_short` overrides → `/wfo-sweep` (continuous param, different methodology).
- `atr_sl_floor` cells → `/atr-sweep` (already done for `liquidity_sweep 1h`; rest are off).
- Combo / cross-TF gate → `/confluence-backtest`.

## Inventory — toggles to audit

Pulled from `config/strategy_params.toml` + 3 prod configs (signal_watch, signal_watch_weekdays, signal_watch_all). Justification dates in parens.

### A1. `volume_suppress` (per-strategy, optionally directional)

**Default**: `false`. **A14b sweep dates**: ~early 2026 — likely 6+ months stale.

Currently ENABLED:

| Strategy | Config | Direction | A14b note |
| --- | --- | --- | --- |
| `bos` | base (all 3) | both | normal-vol wins Δ=+0.11R |
| `orb` | base (all 3) | both | normal-vol wins Δ=+0.33R |
| `liquidity_sweep` | base (all 3) | both | normal-vol wins Δ=+0.17R |
| `engulfing` | base (all 3) | **long only** | LONG low-vol -0.13R→+0.22R; SHORT low-vol +0.72R (keep) |
| `doji` | signal_watch + weekdays | both | tue_thu Δ=+0.24R; weekdays Δ=+0.11R |
| `fib_golden_zone` | signal_watch | both | tue_thu Δ=+0.11R |
| `cvd_divergence` | weekdays | both | weekdays Δ=+0.13R **(conflicts with signal_watch which sets false!)** |
| `smt_divergence` | signal_watch + all | both | Δ=+0.78R (signal_watch) / +0.28R (all) |
| `fvg` | weekdays | both | Δ=+0.08R |
| `wick_fill` | all | both | Δ=+0.12R |

Currently DISABLED (explicit override):

| Strategy | Config | A14b note |
| --- | --- | --- |
| `pin_bar` | base | low-vol edge Δ=-0.22R |
| `hammer_hanging_man` | base | low-vol edge Δ=-0.35R |
| `morning_evening_star` | base | low-vol edge confirmed |
| `marubozu` | base | low-vol edge Δ=-0.43R |
| `cvd_divergence` | signal_watch | low-vol edge Δ=-0.24R |
| `engulfing` | weekdays + all | low-vol edge for SHORT (long handled by directional flag) |

**Smell**: `cvd_divergence` is volume_suppress=false in signal_watch.toml but volume_suppress=true in signal_watch_weekdays.toml. Same A14b methodology should produce the same answer unless the day filter genuinely changes the volume regime. **Worth verifying.**

### A2. `volume_spike_boost` — **DEPRECATED 2026-05-20**

The flag was structurally inert (boost branch unreachable under suppression because `is_spike` and `is_low_vol` thresholds are mathematically disjoint). Removed from engine, configs, resolvers, audit tool, and tests. See [docs/audits/2026-05-20-volume-spike-boost-structural-inertness.md](../audits/2026-05-20-volume-spike-boost-structural-inertness.md). PR #381's audit findings retained for the historical record under a forward-reference banner; verdicts there should not be acted on.

### A3. `adr_exempt` (per-strategy)

| Strategy | Config | Justification |
| --- | --- | --- |
| `bos` | signal_watch | breakout — fires in continuation direction |
| `fib_golden_zone` | signal_watch | BOS + retracement pullback entry |
| `eqh_eql` | signal_watch | liquidity sweep at structure extremes |
| `cvd_divergence` | signal_watch | divergence in trending conditions |
| `smt_divergence` | signal_watch | trend_filter=1 → pullback-in-trend entry |

**Note**: only set in signal_watch.toml (tue_thu). Weekdays + all configs do **not** set these. **Inconsistency: same strategies, different gate state across configs.** Either tue_thu needs them and the others don't (then the comments should say why), or all configs should align.

### A4. `day_filter` (global per-config)

| Config | Value | Rationale (from comments) |
| --- | --- | --- |
| signal_watch | `tue_thu` | ICT weekly cycle (Mon/Fri = manipulation/distribution) |
| signal_watch_weekdays | `weekdays` | Skip weekend signals |
| signal_watch_all | `off` | Reference / backtest profile |

**Question to answer**: does `tue_thu` outperform `weekdays` outperform `off` on aggregate avg_r? If yes, by how much per strategy? Should this be **per-strategy** (some strategies might thrive on Mon/Fri)?

### A5. `strategy_timeframes` (TF allowlist per strategy)

Big block — each cell is "this strategy only fires on these TFs in this config." Mostly derived from one-off audits (Phase 1 cuts, 2026-05-07). Audit question: are any **excluded** TFs now profitable that we're missing?

Examples worth re-checking:

- `bos`: 1d excluded across all configs (mean_r ≤ -0.30R historic). Has the 2025-09+ regime changed this?
- `liquidity_sweep`: 4h excluded in signal_watch + all; 1h excluded in weekdays. Phase 1 cut.
- `order_block`: only 1d enabled; 4h dropped in Phase 1 (audit CONFLUENCE_ONLY).
- `cvd_divergence`: only 1h in signal_watch + weekdays; only 15m/1h/4h in all.

### A6. `[bias] adr_suppress_threshold = 0.80` (global)

Single number, currently 0.80. Sweep `{0.60, 0.70, 0.80, 0.90, 0.95}` × suppressed_avg_r per strategy. Could be a per-strategy override.

## Methodology — single tool, one sweep per gate

**One new tool** — `tools/gate_audit.py`. Same shape as `tools/regime_gate_replay.py`. Per-gate run:

1. Load current `backtest_trades` (or generate fresh via `make buibui-backtest`).
2. Re-tag each trade with `would_be_suppressed_by_<gate>` flag, simulating the candidate toggle change.
3. Emit per-(strategy × tf × direction × symbol) table:

   ```text
   strategy   tf   direction symbol   n_kept  kept_avg_r   n_suppressed  suppressed_avg_r   ΔR_total
   ```

4. Aggregate verdicts at each grain (strategy/tf/direction/symbol):
   - `suppressed_avg_r ≤ −0.05R` AND `n ≥ 30` → **ENABLE at this scope**.
   - `suppressed_avg_r ≥ +0.05R` AND `n ≥ 30` → **DISABLE** (gate is killing winners).
   - else → noise / insufficient data → demote, don't ship.

5. Pick the **smallest scope** where the verdict is unambiguous across the next-coarser level (per-direction beats per-strategy when sign differs across long/short).

Production TOML is **not** edited until each gate's audit PR lands. Live behaviour preserved during the audit window.

## Recommended sweep order

| # | Gate | Reason for ordering |
| --- | --- | --- |
| **1** | `volume_suppress` | Biggest blast radius (15+ active cells). A14b data is stalest. cvd_divergence cross-config conflict already smells. |
| 2 | `day_filter` | Single global toggle; cheap. If `tue_thu` doesn't outperform, the whole signal_watch.toml premise wobbles. |
| 3 | `strategy_timeframes` | Likely some excluded TFs are now positive (regime shift since Phase 1 cuts). |
| 4 | `adr_exempt` | Cross-config inconsistency; either align or document why tue_thu is special. |
| 5 | ~~`volume_spike_boost`~~ | **DEPRECATED 2026-05-20** — structural-inertness finding (see A2 above). |
| 6 | `adr_suppress_threshold` | Threshold sweep; might unlock per-strategy. |

## Cheapest first measurement — "strip baseline"

Before per-cell audits, one 10-minute experiment to scope the rest:

```bash
# 1. Snapshot current avg_r per config
make buibui-backtest CONFIG=config/signal_watch.toml SAVE=0 SINCE=2025-09-12 > /tmp/audit_baseline_signal_watch.txt

# 2. Strip all volume_suppress + adr_exempt overrides (temp branch)
# 3. Rerun
make buibui-backtest CONFIG=config/signal_watch.toml SAVE=0 SINCE=2025-09-12 > /tmp/audit_stripped_signal_watch.txt

# 4. Diff aggregate avg_r
```

Three outcomes:

- **Stripped > Current** → overrides collectively HURT. Audit individually to find which.
- **Stripped < Current** → overrides collectively HELP. Audit individually to find which contribute.
- **Stripped ≈ Current** → overrides are noise. Consider deleting for simplicity.

This tells us whether Phase A is worth the per-cell work before we build `tools/gate_audit.py`.

## Output / artefacts per audit gate

Each gate produces a PR with:

1. The full audit table (committed under `docs/audits/<date>-<gate>.md`).
2. The TOML diff (per-strategy / per-tf / per-direction additions or deletions).
3. A 1-line summary in MEMORY.md.

## Open questions to resolve before starting

- **Q-A1**: do we use existing `backtest_trades` rows (fast, may be slightly stale) or regenerate with `make buibui-backtest` per audit (slow, fresh)? **Tentative**: existing rows for the strip-baseline sniff test; regenerate for per-cell verdicts.
- **Q-A2**: `tools/gate_audit.py` or extend `tools/regime_gate_replay.py` / `tools/direction_filter_replay.py`? **Tentative**: new tool; the others are per-gate; this is per-toggle (different shape).
- **Q-A3**: aggregate-table format — same as `/backtest-findings` skill output, or new schema? **Tentative**: reuse `/backtest-findings` shape so the existing skill can consume it.
