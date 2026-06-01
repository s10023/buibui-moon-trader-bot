# Asymmetric F8 HTF EMA gate — design spec

> Status: **DESIGN — approved, no code yet.** Supersedes the framing in
> `docs/redesign/buibui-ema-per-tf-anchor-design.md`. The per-TF / multi-anchor
> EMA exploration ran an ablation first (go/no-go, per the draft's deferred
> validation plan) and the data redirected the work: the win is **not** anchor
> laddering — it is making F8 **direction- and strategy-aware**. The anchor-TF
> ladder is demoted to a deferred layer-on (see §7).

## 1. Problem & evidence

### 1.1 What F8 does today

The F8 HTF EMA bias gate (`config/strategy_params.toml [bias.htf_ema]`; logic
`analytics/signal/gates.py::_apply_htf_ema_gate`; engine mirror
`analytics/backtest/engine.py::_apply_htf_ema_gate_to_signals`) suppresses any
signal whose direction **opposes** the per-strategy HTF EMA slope:

- `slope > 0` (HTF up) → drop **short** signals.
- `slope < 0` (HTF down) → drop **long** signals.
- `|slope| < deadband (0.3%)` or `slope is None` → allow (no opinion).
- Anchor keyed by **strategy only** (default `4h EMA-50, slope_lookback 10`; six
  strategies pinned to `1d`). Mode is **hard** (drops live). The suppression is
  **symmetric** — it gates both directions identically.

### 1.2 The ablation (`tools/htf_ema_gate_replay.py`, 2026-06-01)

Replayed the F8 suppression decision against the permissive baseline in
`backtest_trades` (842,616 closed trades; valid because no config sets
`[live_parity]` → `f8_htf_ema=False`, so F8-dropped signals are present in the
table, not pre-filtered). Measured realized `avg_r` on the **would-suppress**
subset.

**Headline:** suppressed `avg_r = −0.0666` (n=345,743) vs kept `−0.0312`. At the
aggregate the gate looks healthy — but the directional split shows it is
**half-broken**:

| signal TF | long supp | short supp |
| --------- | --------- | ---------- |
| 15m       | -0.2055   | +0.1790    |
| 1h        | -0.1615   | +0.2189    |
| 4h        | -0.1745   | +0.4599    |
| 1d        | +0.2172   | +0.5668    |

n is large where it matters: long-supp n=175K / short-supp n=84K on 15m; the 1d
long cell is thin (n=1.1K).

Counter-trend **longs** (knife-catching) lose → correctly dropped. Counter-trend
**shorts** (fading rallies) **win** (+0.18 → +0.57R, monotonically rising with
TF, n=84K on 15m alone) → **F8 throws these winners away.** The −0.067 aggregate
only looks acceptable because the large correctly-dropped-long bucket outweighs
the wrongly-dropped-short bucket. This is the 2026-05-10 "inverted regime
mapping" caveat, now direction-resolved: the mean-reversion edge lives on shorts.

### 1.3 Type × direction split (the design's grounding)

Suppressed-subset `avg_r` by `strategy_type` × direction:

| type         | long supp | short supp |
| ------------ | --------- | ---------- |
| candlestick  | -0.119    | +0.431     |
| price_action | -0.236    | +0.101     |
| session      | -0.187    | +0.453     |
| structural   | -0.233    | +0.028     |
| trend        | -0.220    | +0.120     |
| flow         | +0.021    | +0.776     |
| fib          | -0.080    | -0.113     |

Reading: the long-side filter is correct in **every** family (all negative or
~0). The short-side filter is wrong in **6 of 7** families (counter-trend shorts
positive); the lone exception is **`fib`**, where shorts also lose (−0.113) so
its short filter should stay.

**Caveats (honest):** in-sample / full-dataset; 3 symbols (BTC/ETH/SOL); reflects
current `tp_r`/SL exits; `fib` short n=1575 is modest. Strong enough to direct the
design, **not** yet to commit a live config — §6 gates that on WFO + soft mode.

## 2. Goal & non-goals

**Goal:** stop F8 from suppressing net-winning trades by making its suppression
**direction-scoped** (and therefore strategy/family-scoped), recovering the
counter-trend short edge while keeping the working counter-trend-long filter.

**Non-goals (this spec):**

- Anchor-TF laddering / per-TF anchors (Dial A) — deferred, §7.
- EMA period variation or golden/death cross (Dial B) — out of scope.
- Touching the regime gate, ADR, cooldown, or conflict resolver.
- Reviving the EMA pullback detector.

## 3. Mechanism

Add a single field, `suppress_directions: list[str]`, threaded through the F8
config and both gate implementations. Backward-compatible: **omitting the key ⇒
`["long", "short"]`** (today's symmetric behavior, byte-identical).

### 3.1 Config schema (`config/strategy_params.toml`)

```toml
[bias.htf_ema]
enabled = true
mode = "soft"                    # ship SOFT first (§6); flip to "hard" after observe
default_tf = "4h"
default_period = 50
default_slope_lookback = 10
deadband_pct = 0.003
suppress_directions = ["long"]   # NEW global default: gate counter-trend longs only

[bias.htf_ema.per_strategy]
# flow family — fully exempt (counter-trend shorts +0.78R, longs ~0):
cvd_divergence  = { tf = "1d", period = 50, slope_lookback = 10, suppress_directions = [] }
smt_divergence  = { tf = "1d", period = 50, slope_lookback = 10, suppress_directions = [] }
# fib family — keep full symmetric gate (both directions lose counter-trend):
fib_golden_zone = { suppress_directions = ["long", "short"] }
ote_entry       = { suppress_directions = ["long", "short"] }
# existing 1d-anchor overrides retain default suppress_directions (= global ["long"]):
ema             = { tf = "1d", period = 50, slope_lookback = 10 }
orb             = { tf = "1d", period = 50, slope_lookback = 10 }
eqh_eql         = { tf = "1d", period = 50, slope_lookback = 10 }
marubozu        = { tf = "1d", period = 50, slope_lookback = 10 }
```

Resolution precedence for `suppress_directions`: per-strategy override → global
`[bias.htf_ema].suppress_directions` → built-in default `["long","short"]`.

### 3.2 Data model (`analytics/signal_config.py`)

- `HtfEmaAnchor` gains `suppress_directions: tuple[str, ...]` (frozen; tuple for
  hashability — F8 anchors are cache keys). Default `("long", "short")`.
- `BiasConfig` gains `htf_ema_default_suppress_directions: tuple[str, ...]`.
- `BiasConfig.htf_ema_anchor(strategy)` fills `suppress_directions` from the
  per-strategy override else the global default. The slope **cache key**
  (`symbol, tf, period, slope_lookback`) is **unchanged** — `suppress_directions`
  is a post-lookup filter decision, not part of slope identity.
- TOML parser: read `suppress_directions` in both the `[bias.htf_ema]` block and
  each `[bias.htf_ema.per_strategy]` entry; validate values ⊆ `{"long","short"}`.

### 3.3 Gate logic (one-line change, both sites)

`analytics/signal/gates.py::_apply_htf_ema_gate` — the opposing check gains a
membership test:

```python
opposing = (slope > 0 and event.direction == "short") or (
    slope < 0 and event.direction == "long"
)
if not opposing or event.direction not in anchor.suppress_directions:
    kept.append(event)
    continue
# ...existing log + hard/soft drop...
```

`analytics/backtest/engine.py::_apply_htf_ema_gate_to_signals` gets the identical
guard so backtest replay stays at live parity (`live_parity.f8_htf_ema`).

## 4. Architecture & data flow

No new modules; no new data flow. The change is confined to:

1. `signal_config.py` — schema field + parser + resolver (the only file that
   grows in responsibility; already owns F8 config, so this is cohesive).
2. `analytics/signal/gates.py` — one guard clause.
3. `analytics/backtest/engine.py` — the mirrored guard clause.
4. `config/strategy_params.toml` (+ the three `signal_watch*.toml` inherit via
   `extends`) — the values from §3.1.

Slope computation, caching, anchor resolution, and the live scan fan-out are
untouched.

## 5. Error handling & backward compatibility

- Omitted `suppress_directions` ⇒ `("long","short")` ⇒ **byte-identical** to
  today. Existing tests and golden fixtures must not move until the config values
  in §3.1 are applied.
- Unknown direction tokens in TOML → raise at config-load (fail fast), consistent
  with existing F8 parser validation.
- An empty list `[]` is valid and means "never suppress" (full exempt) — distinct
  from disabling F8 globally.
- Unknown strategy → falls open (no override) → global default, matching current
  defensive behavior.

## 6. Validation & rollout (mandatory — gates any live behavior change)

The §1 numbers are in-sample. **No hard-mode live change ships without:**

1. **OOS confirmation.** Extend `tools/htf_ema_gate_replay.py` (or a param-sweep
   variant) with an IS/OOS **time split**; confirm the short-side asymmetry holds
   OOS per `strategy_type × direction`. Specifically: OOS short-suppressed
   `avg_r > 0` for the 6 relaxed families and `fib` short-suppressed `avg_r ≤ 0`
   (keep). Promote only families that survive OOS.
2. **Soft first.** Ship §3.1 with `mode = "soft"` (log-only). Observe ≥2 weeks of
   live signals; confirm the soft-flagged short signals' realized outcomes track
   the replay before flipping `mode = "hard"`.
3. **`make db-update`** after the config lands (the relaxed gate changes the
   backtest rowset under `live_parity.f8_htf_ema` if/when enabled, and the
   recalibrate/star ratings + Stats UI must re-derive). Regression goldens refresh
   only after the intentional behavior change is accepted.

Rollback: revert the `suppress_directions` values (or set global back to
`["long","short"]`) — pure config, no code revert needed.

## 7. Deferred: anchor-TF ladder (Dial A)

The original "different EMA per timeframe" idea. Only revisit **after** §6 ships
and **if** the OOS replay shows the suppressed-subset `avg_r` still varies
materially with the **signal's** TF *within a fixed direction* (the §1.2 table
hints short-side edge rises with TF: +0.18→+0.57). If so, a later spec adds
`htf_ema_anchor(strategy, signal_tf)` + a `[bias.htf_ema.per_tf]` ladder, layered
on top of the direction scope — never as a replacement for it. Period/cross
(Dial B) remains out of scope (WFO already punished period-fitting).

## 8. Testing

- **Unit (gate):** new direction-scope branch — (a) `["long"]` drops a
  counter-trend long but keeps a counter-trend short; (b) `[]` keeps both;
  (c) `["long","short"]` reproduces current drops; (d) omitted key ⇒ default
  symmetric.
- **Unit (config):** parser reads global + per-strategy `suppress_directions`;
  precedence resolves override → global → default; invalid token raises.
- **Parity:** `_apply_htf_ema_gate` (live) and `_apply_htf_ema_gate_to_signals`
  (engine) produce identical keep/drop sets for the same `suppress_directions`
  and slope series.
- **Regression:** with §3.1 values absent, backtest golden output is unchanged.
- **Tool:** `tools/htf_ema_gate_replay.py` IS/OOS split returns per-cell verdicts.

## 9. Quality gates

`make lint-py`, `make typecheck` (mypy strict), `make test`, `make lint-md`.
Update `CLAUDE.md` (F8 description), `.claude/context/analytics.md`, and the
`signal-watch` skill's TOML reference to document `suppress_directions`.
