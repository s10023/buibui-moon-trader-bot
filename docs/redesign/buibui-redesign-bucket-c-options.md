# Bucket C — Options Scoping

**Status:** SCOPING 2026-05-18. No code changes proposed; this doc gates the decision between schema-extension and T6 engine work for the 8 strategies the Phase A audits flagged as inexpressible under the current TOML schema.

**Inputs:** PRs #375 (`volume_suppress`), #377 (`day_filter`), #379 (`strategy_timeframes`), #380 (`adr_exempt`), #381 (`volume_spike_boost`), #382 (`adr_suppress_threshold`); `buibui-redesign-t6-phase-a-plan.md`; `buibui-redesign-t6-plan.md`; `analytics/signal_config.py` (current schema); `project_bos_routing_audit.md` (T2a routing memo, 2026-05-13).

**TL;DR:** Schema extension and T6 engine work are **not alternatives** — they solve different problems. Schema gaps prevent **expressing** the decisions audits have already made. T6 unblocks **measuring** the three replay-only ON↔OFF inverse questions. The 8 Bucket C strategies need schema, not T6. Recommendation at the end.

## What Bucket C is

A cell is "Bucket C" when an audit produced a clean, decision-bearing per-(strategy × tf × direction) verdict, but the current TOML schema has no field to express the decision. The 8 strategies in Bucket C all have at least one such cell across PRs #375 / #377 / #379 / #380.

Bucket C is **not** "we're unsure" — it is "we know what to do, but the config can't say it."

## Inventory — 8 strategies, source PR, schema gap

| # | Strategy | PR(s) flagging | Verdict shape that's unexpressible | Schema gap |
| - | -------- | -------------- | ---------------------------------- | ---------- |
| 1 | `bos` | #379, #380 | 1h long ENABLE `adr_exempt` (−0.54R suppressed) / 1h short DISABLE (+0.42R suppressed). T2a routing memo: 131 of 644 sub-cells positive — misrouting, not bad detector. | `adr_exempt` is per-strategy bool; needs per-direction (or per-tf-direction) |
| 2 | `eqh_eql` | #375, #379 | `volume_suppress` flips within-config across tfs (e.g. tue_thu 15m long ENABLE vs 1h short DISABLE) | `volume_suppress_long/short` exist; per-tf does not |
| 3 | `fib_golden_zone` | #375 | Within-config tf-level flips for `volume_suppress` | Same as eqh_eql |
| 4 | `hammer_hanging_man` | #375, #379 | Weekend 1h short edge (cr_S=+0.217R 3★) masked by losing long; also `volume_suppress` tf-level flips | Per-tf-direction `strategy_timeframes` AND per-tf `volume_suppress` |
| 5 | `inside_bar` | #375, #377, #379 | 4h cross-config split (long KILL all 3, short KEEP all 3); weekdays 4h cr_S=+0.151R 2★ above Bucket C threshold | Per-tf-direction `strategy_timeframes` (or per-direction `day_filter` carve-out) |
| 6 | `morning_evening_star` | #375 | Within-config tf-level `volume_suppress` flips | Per-tf `volume_suppress` |
| 7 | `order_block` | #375 | Within-config tf-level `volume_suppress` flips | Per-tf `volume_suppress` |
| 8 | `pin_bar` | #375 | mon_fri 15m long ENABLE / 15m short DISABLE (and other tf splits across configs) | Per-tf-direction `volume_suppress` (long-and-short exist, per-tf does not) |

Most of Bucket C is one of two shapes:

- **Per-tf gap for `volume_suppress`** (6 strategies): `volume_suppress_long` / `volume_suppress_short` exist on `StrategyOverride`, but there is no `volume_suppress_long_per_tf` analogue. `tp_r_per_tf` / `sl_pct_per_tf` already exist as precedent.
- **Per-direction gap for `adr_exempt` and `strategy_timeframes`** (2 strategies, `bos` + `inside_bar`): `adr_exempt` is a single bool; `strategy_timeframes` is `dict[strategy, list[tf]]` with no direction split.

Counting distinct cells across the audits: **roughly 18–22 (config × strategy × tf × direction) cells** are blocked. Most concentrate on `volume_suppress` per-tf (~12 cells) with the remainder on `adr_exempt` directional (3 cells, all `bos`), `strategy_timeframes` directional (4 cells, mostly `inside_bar` 4h × 3 configs + `hammer_hanging_man` 1h weekend), and `day_filter` per-direction carve-out (3 cells, `inside_bar` 4h again).

## Current schema vs needed schema

Current `StrategyOverride` (in `analytics/signal_config.py`) has these toggle / gate fields:

```python
adr_exempt:                  bool                    # single, not per-direction
volume_suppress:             bool | None             # inherits global
volume_suppress_long/short:  bool | None             # per-direction; per-tf MISSING
volume_spike_boost:          bool | None             # inherits global
volume_spike_boost_long/short: bool | None           # per-direction; per-tf MISSING
suppress_long/short:         bool                    # T2c direction filter; per-tf MISSING
```

Current `SignalWatchConfig`:

```python
strategy_timeframes:  dict[str, list[str]]           # per-strategy list; no per-direction split
day_filter:           str                            # global per-config; no per-strategy carve-out
```

Current `BiasConfig`:

```python
adr_suppress_threshold:  float | None                # global per-config; not per-strategy
```

Bucket C asks for: per-tf splits on `volume_suppress[_long/_short]`, per-direction (and per-tf) splits on `adr_exempt`, per-direction splits on `strategy_timeframes`, and per-direction carve-outs on `day_filter`.

## What T6 actually solves

T6 (`buibui-redesign-t6-plan.md`) ports six live-only gates into `analytics/backtest/engine.py`:

```text
regime → direction_filter → f8_htf_ema → adr_bias → conflict_resolver → cooldown
```

It introduces a new `LiveParityConfig` dataclass under `[backtest.live_parity]`, **reuses** the existing live `[bias]` + `[strategy_params.*]` blocks for gate parameters (per the plan: "same anchors, same regime allowlist, same suppress flags"), and adds CLI flags / a `/parity-sweep` skill for the 8-run measurement grid.

**What T6 does:** lets backtest sweeps run with arbitrary gate combinations on/off, including a permissive-baseline run that turns every gate OFF. Suppressed-side trades appear in `backtest_trades` and become measurable. This **unblocks the 3 ON→OFF inverse questions** (volume_suppress mon_fri/weekend, adr_exempt mon_fri/weekend, volume_spike_boost non-engulfing).

**What T6 does NOT do:** add new TOML fields to `StrategyOverride` or `BiasConfig`. The plan explicitly reuses existing schema. So T6 alone:

- cannot encode per-tf `volume_suppress` (the 6-strategy bulk of Bucket C),
- cannot encode per-direction `adr_exempt` (`bos`),
- cannot encode per-direction `strategy_timeframes` (`inside_bar`, `hammer_hanging_man`).

This corrects the "schema extension **or** T6 engine work" framing in the prior PR bodies and MEMORY.md entries. They are **orthogonal**.

## Option 1 — Schema extension

Add the missing fields to `StrategyOverride` / `BiasConfig`. Concrete shapes (precedent: `tp_r_per_tf`):

```python
@dataclass
class StrategyOverride:
    ...
    # Per-tf directional volume_suppress; falls back to volume_suppress_long/short → volume_suppress
    volume_suppress_long_per_tf:  dict[str, bool] = field(default_factory=dict)
    volume_suppress_short_per_tf: dict[str, bool] = field(default_factory=dict)
    # Per-direction adr_exempt; falls back to adr_exempt
    adr_exempt_long:  bool | None = None
    adr_exempt_short: bool | None = None
```

```python
# In SignalWatchConfig
strategy_timeframes_long:  dict[str, list[str]] = field(default_factory=dict)
strategy_timeframes_short: dict[str, list[str]] = field(default_factory=dict)
```

**Cost:**

- ~50–80 LOC in `signal_config.py` (new fields + `effective_*` helpers + TOML parsing).
- ~30–50 LOC in `analytics/signal/gates.py` resolvers to honor per-tf fallback chains.
- 8–12 unit tests for the resolution order (per-tf-dir → per-dir → strategy → global).
- TOML diff per Bucket C cell to actually encode the decisions (~20 entries across 3 configs).
- One golden refresh (regression suite re-runs the engine but with a different live gate state).

**Risk:**

- TOML clutter — each strategy block grows by 2–6 lines. Manageable; precedent already set by `tp_r_per_tf`.
- Resolution-order ambiguity — needs explicit per-tf-dir > per-dir > strategy precedence in code and tests. Cheap to specify, must not skip.
- Does **not** address the 3 ON→OFF inverse questions. Mon_fri / weekend `volume_suppress` and `adr_exempt` cells stay un-measured.

**Effort:** 1–2 days, single PR. Closes 8 of 8 Bucket C strategies (~18–22 cells) immediately with data already on disk.

## Option 2 — T6 engine work

Ship the 5-PR build order from `buibui-redesign-t6-plan.md`. Once shipped, run a permissive-baseline sweep (every `LiveParityConfig` flag off) per config, then re-audit the 3 inverse questions against the new `backtest_trades` rows.

**Cost:**

- 5 PRs, ~2–4 days each per the plan's PR sizing.
- One additional permissive-baseline sweep per config after merge (~30–60 min compute each).
- `/parity-sweep` skill + measurement protocol.
- New golden per gate as empirics dictate (deferred to follow-ups per the plan).

**Risk:**

- Multi-week scope. Each PR has its own review window.
- Cooldown PR (PR-5) is the hardest because of in-engine state.
- **Does not solve Bucket C schema gaps.** Even with T6 live, the 8 strategies still cannot encode per-tf-direction decisions in TOML.

**Effort:** 2–3 weeks across 5 PRs. Closes 3 inverse questions, 0 of 8 Bucket C strategies.

## Option 3 — Hybrid (recommended)

Do both, **in sequence**:

1. **Schema extension first** (1–2 days). Closes the 8 Bucket C strategies immediately. Each gate's audit already produced the verdict — schema just lets us encode it.
2. **T6 next** (when prioritized). Unblocks the 3 inverse questions and gives backtest output that reflects what the daemon actually fires. Independent of Bucket C; T6's plan already says it reuses `[strategy_params.*]` so any schema fields added in step 1 are picked up automatically.

**Why this order:**

- Schema is cheap and decisive. Bucket C cells have signed avg_r evidence at `n ≥ 30` on data we already collected. Letting that evidence sit unencoded leaves R on the table every week.
- T6 is expensive and serves a different goal (measurement parity, not expression). Delaying it doesn't make Bucket C cells less actionable — they were actionable the day the audits landed.
- The two streams don't conflict. The schema PR adds fields under `StrategyOverride`; T6 reuses `StrategyOverride` as-is.

**Order cost:** ~1–2 days + ~2–3 weeks = same as Option 2 alone, plus the Bucket C R lift earned in week 1.

## What's NOT in scope here

- `tp_r` per-direction-per-tf — covered by `/wfo-sweep` (continuous param; different methodology).
- `atr_sl_floor` per-direction-per-tf — covered by `/atr-sweep` (already runs per-cell).
- Combo / cross-TF gate fields — covered by `/confluence-backtest`.
- The 3 ON→OFF inverse questions — those need T6 regardless of schema.

## Decisions

- **Q-BC-1 — DECIDED**: Per-tf precedence is **per-tf > per-direction > strategy > global**, matching `tp_r_per_tf`. The new `effective_volume_suppress_long_per_tf(strategy, tf)` / `effective_volume_suppress_short_per_tf(strategy, tf)` helpers walk the chain: per-tf-dir dict → directional flag → strategy flag → backtest global. Same shape for `volume_spike_boost_*` and `adr_exempt_*`.
- **Q-BC-2 — DECIDED**: `strategy_timeframes_long` / `strategy_timeframes_short` are **additive (narrowing)**. The base `strategy_timeframes[strategy]` list is the allowlist; the directional lists, when set, restrict the cell to that directional intersection (i.e. only emit `long` events for TFs that appear in **both** `strategy_timeframes[strategy]` AND `strategy_timeframes_long[strategy]`). When a directional list is empty/unset, the strategy emits that direction on the full base list. Mirrors the `volume_suppress_long` overlay on `volume_suppress`.
- **Q-BC-3 — DECIDED**: Keep TOML **flat**. New per-tf-direction fields live as sibling sub-tables under `[strategy_params.X]`, matching `tp_r_per_tf`:

  ```toml
  [strategy_params.pin_bar]
  volume_suppress = false
  [strategy_params.pin_bar.volume_suppress_long_per_tf]
  "15m" = true     # mon_fri 15m long ENABLE (PR #375 deferred)
  ```

  No `[gates]` namespace migration. The existing surface stays additive-compatible with older configs.

## Recommendation

Ship **Option 3** (Hybrid). Start with the schema-extension PR — it earns measurable R from the 8 Bucket C strategies immediately and is independent of the 5-PR T6 stream. Then begin T6 PR-1 (foundation) when bandwidth allows.

Next concrete step: open `feat/bucket-c-schema-extension` — schema fields, resolvers, gate plumbing, tests. Follow-up PR encodes the 8 strategies' verdicts in TOML.
