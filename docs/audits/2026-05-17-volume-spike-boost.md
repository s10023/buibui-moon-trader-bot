# T6 Phase A — `volume_spike_boost` Audit Findings (2026-05-17)

**Gate**: `volume_spike_boost` (per-strategy boolean; default `false`). When `true`, trades whose entry candle has `volume_spike = TRUE` bypass the `volume_suppress` filter — high-volume entries are protected even if low-volume gating would otherwise drop the cell.

Currently exactly **one** strategy in the codebase has the boost enabled: `engulfing` (`config/strategy_params.toml:161`, A15 sweep note "spike +0.59R vs normal +0.23R, 73 spikes").

**Tool**: `tools/gate_audit.py volume-spike-boost --config <toml> --grain strategy_tf_dir --min-n 30`. Masks `volume_spike = TRUE` trades on currently-boosted strategies — semantics: "if we flip the flag to `false`, these trades stop benefiting from boost and are subject to suppression". NULL `volume_spike` (pre-PR#371 backfill rows) fillna(False) per `_gate_volume_spike_boost`.

**Decision rule** (per cell, n_supp ≥ 30):

- `supp_avg_r ≤ −0.05R` → **ENABLE** the flip (boost is protecting losers → remove the boost).
- `supp_avg_r ≥ +0.05R` → **DISABLE** the flip (boost is protecting winners → keep the boost ON).
- else → INSUFFICIENT.

**Cross-check protocol** (PR #377 dual-view): every actionable cell is verified against `confidence_ratings` (winning-params view). Audit and CR must agree before any TOML flip.

## Outcome — no TOML edits

`engulfing` retains `volume_spike_boost = true` in `config/strategy_params.toml`. The two actionable cells (tue_thu 15m long and 15m short) both **DISABLE** — the boost is protecting profitable spike trades. All remaining cells across the 3 production configs are INSUFFICIENT (`n_supp < 30`).

## Audit scope

| Config | day_filter | Sweep run_ids | engulfing trades | engulfing spike trades |
| --- | --- | --- | --- | --- |
| `signal_watch.toml` | `tue_thu` | 192 | 1,903 | 86 |
| `signal_watch_weekdays.toml` | `mon_fri` | 192 | 741 | 40 |
| `signal_watch_all.toml` | `weekend` | 214 | 860 | 58 |

Sweep IDs picked by `tools/gate_audit.py::_resolve_config_run_ids` — most-recent sweep whose `day_filter` matches the config (disjoint across configs since PR #372).

Spike-trade counts confirm the pre-audit suspicion (`/tmp/next-conversation-prompt.md`): even across the full sweep, spike-tagged rows are thin once split per (tf × direction). Pre-PR#371 trades have NULL `volume_spike` → fillna(False) → never enter the suppressed pool.

## Tue_thu (signal_watch.toml) — engulfing

Only cells with `n_supp ≥ 30` are decision-bearing. Cells with `n_supp = 0` mean no spike-tagged trades; cells with `n_supp < 30` are insufficient evidence.

| tf | dir | n_kept | kept_avg_r | n_supp | supp_avg_r | Audit verdict | CR star / avg_r | Decision |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 15m | long | 634 | −0.17 | 38 | **+0.27** | DISABLE | 3★ +0.25R | **KEEP `volume_spike_boost = true`** |
| 15m | short | 724 | +0.43 | 32 | **+0.36** | DISABLE | 3★ +0.42R | **KEEP `volume_spike_boost = true`** |
| 1h | long | 166 | −0.09 | 5 | n/a | INSUFFICIENT | 2★ +0.18R | (status quo) |
| 1h | short | 205 | +0.58 | 10 | n/a | INSUFFICIENT | 4★ +0.62R | (status quo) |
| 4h | long | 8 | −0.05 | 1 | n/a | INSUFFICIENT | 2★ +0.06R | (status quo) |
| 4h | short | 50 | +0.88 | 0 | n/a | INSUFFICIENT | 4★ +0.87R | (status quo) |
| 1d | long | 2 | +2.95 | 0 | n/a | INSUFFICIENT | (combined 5★ +0.95R) | (status quo) |
| 1d | short | 5 | +1.35 | 0 | n/a | INSUFFICIENT | 4★ +0.51R | (status quo) |

### `engulfing 15m` — audit confirms boost is doing its job

Both directions show DISABLE — the spike-tagged trades have **higher** avg_r than the non-spike pool, matching the original A15 sweep finding (spike +0.59R vs normal +0.23R). Cross-check with `confidence_ratings` agrees (15m long 3★ +0.25R, 15m short 3★ +0.42R — both profitable cells worth protecting from low-volume gating).

Counterfactual: flipping `volume_spike_boost = false` would (a) re-expose spike trades to `volume_suppress_long = true` (the existing engulfing rule) → long-side spike trades would be dropped → −10.10R impact (38 trades × +0.27R). Short-side is already keep-by-default (`volume_suppress_short` is unset → default false), so the impact figure on shorts (−11.40R) is theoretical: a `volume_suppress_short = true` flip would be needed to realise it. The number is the upper-bound R cost if both directions were brought under suppression.

### Higher TFs — boost neutral by sample

1h / 4h / 1d engulfing cells generate too few spike-tagged trades to verdict (5 / 1 / 0 long, 10 / 0 / 0 short). CR ratings on these cells are strong on shorts (1h 4★ +0.62R, 4h 4★ +0.87R, 1d 4★ +0.51R). With the boost set globally on `engulfing`, these cells inherit it — no per-tf flip is expressible in the current schema, and no actionable evidence pushes either way.

## Mon_fri (signal_watch_weekdays.toml) — engulfing INSUFFICIENT everywhere

`strategy_timeframes` (PR #379) restricts live engulfing to `["4h", "1d"]` on mon_fri after the 15m + 1h cells were cut. The audit runs against the full sweep so all 4 TFs appear:

| tf | dir | n_kept | kept_avg_r | n_supp | supp_avg_r | Verdict | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 15m | long | 60 | +0.12 | 13 | −0.28 | INSUFFICIENT | not in live timeframes (PR #379) |
| 15m | short | 478 | −0.10 | 20 | +0.70 | INSUFFICIENT | not in live timeframes |
| 1h | long | 14 | −0.69 | 6 | −0.22 | INSUFFICIENT | not in live timeframes |
| 1h | short | 120 | −0.09 | 0 | n/a | INSUFFICIENT | not in live timeframes |
| 4h | long | 3 | +0.62 | 0 | n/a | INSUFFICIENT | live, but n=3 |
| 4h | short | 13 | −0.36 | 0 | n/a | INSUFFICIENT | live, but n_supp=0 |
| 1d | short | 1 | −1.05 | 0 | n/a | INSUFFICIENT | live, but n=1 |

CR ratings tell the broader story: every mon_fri engulfing cell is **1★ with avg_r ∈ [−0.05R, −0.77R]** — the strategy is fundamentally weak on Mon+Fri. Boost or no boost, mon_fri engulfing is barely surviving and PR #379 already culled its two highest-volume TFs.

**No actionable change.** The live `["4h", "1d"]` cells inherit `volume_spike_boost = true` via the base config; with `n_supp = 0` on both, removing the flag would be a no-op on observable trades — but the option to protect a future spike on these sparse cells stays available.

## Weekend (signal_watch_all.toml) — engulfing INSUFFICIENT everywhere

`strategy_timeframes` (PR #379) restricts live engulfing to `["15m", "1d"]` on weekend after 1h + 4h were cut:

| tf | dir | n_kept | kept_avg_r | n_supp | supp_avg_r | Verdict | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 15m | long | 61 | −0.07 | 20 | +0.20 | INSUFFICIENT | live, n_supp = 20 (below 30) |
| 15m | short | 510 | +0.05 | 26 | **−0.09** | INSUFFICIENT | live, n_supp = 26 (just below 30; trend hints ENABLE if data grows) |
| 1h | long | 26 | −0.47 | 9 | +0.62 | INSUFFICIENT | not in live timeframes |
| 1h | short | 153 | −0.04 | 3 | +0.62 | INSUFFICIENT | not in live timeframes |
| 4h | long | 1 | −1.05 | 0 | n/a | INSUFFICIENT | not in live timeframes |
| 4h | short | 42 | −0.19 | 0 | n/a | INSUFFICIENT | not in live timeframes |
| 1d | short | 2 | +0.70 | 0 | n/a | INSUFFICIENT | live, but n=2 |

CR ratings: 15m combined 2★ +0.04R (long 1★ −0.01R, short 2★ +0.04R), all 1-2★ neutral-losing. The only cell trending toward "boost killing winners" is **15m short** (n_supp=26, supp_avg_r=−0.09R), but it's below `min_n=30` so not actionable. Worth a re-audit after the next sweep accumulates more weekend spike trades.

**No actionable change** under current data.

## Replay-only limitations (carry-over)

Two known gaps that this audit cannot close, same shape as PR #375 / PR #380:

1. **OFF → ON inverse not auditable for non-engulfing strategies.** The `_gate_volume_spike_boost` handler only masks trades where `strategy.isin(boosted)` — for strategies currently at `volume_spike_boost = false`, n_supp is structurally 0. Adding the boost elsewhere would need a different sweep mode (e.g., compare spike vs non-spike avg_r per strategy × tf without conditioning on the current flag). Out of scope here.
2. **Sparse spike-row count on mon_fri / weekend cells.** Even with the full sweep loaded, n_supp on weekend 15m short is 26 — close enough that the next sweep cycle may unlock a verdict. Re-visit after the next `make db-update` run accumulates trades.

Both gaps would close under T6 backtest-live-parity engine work (`docs/redesign/buibui-redesign-t6-plan.md`).

## Bucket C / cross-config carry-over

No new Bucket C strategies added (the per-direction schema gap that bucketed `bos`, `inside_bar` et al. doesn't apply here — engulfing's actionable cells share their verdict across both directions). Bucket C remains 8 strategies as of PR #380.

## Sequencing forward

PR merges → no `make db-update` needed (no behaviour change). Next Phase A cell is the **final** one: `adr_suppress_threshold` (Task 2 of the handoff prompt) — a global threshold sweep, requires a new ad-hoc script per `/tmp/next-conversation-prompt.md` Task 2.
