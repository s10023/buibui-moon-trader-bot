# T6 — Backtest-Live Parity (Design)

**Status:** DRAFT (2026-05-15). Scope: port live-only gates into `analytics/backtest/engine.py` so backtest output reflects what the daemon would actually have fired. Default off; per-gate flags; meta shortcut.

**Not in scope:** `min_avg_r` and `backtest hard filter` (recursive — they consult prior backtest stats). Already covered by replay tools.

## Engine signature

Add a single `LiveParityConfig` dataclass passed to `run_backtest()`. Keeps the existing positional args untouched so all current callers stay green.

```python
# analytics/backtest/live_parity_config.py  (NEW)
from dataclasses import dataclass, field

@dataclass(frozen=True)
class LiveParityConfig:
    """Toggle live-only gates inside run_backtest(). All default False.

    Set `enabled=True` to flip every individual flag on at once (still respects
    explicit False overrides). Logged once per backtest run.
    """
    enabled: bool = False
    regime: bool = False
    direction_filter: bool = False
    f8_htf_ema: bool = False
    adr_bias: bool = False           # ADR-consumption suppress (chasing direction)
    conflict_resolver: bool = False  # per-candle long-vs-short resolution
    cooldown: bool = False           # N-bar same-(sym,tf,strat,dir) suppression

    def is_on(self, gate: str) -> bool:
        return self.enabled or bool(getattr(self, gate))
```

```python
# analytics/backtest/engine.py — additive signature change
def run_backtest(
    ohlcv_df: pd.DataFrame,
    signals_df: pd.DataFrame,
    *,
    sl_pct: float = 0.02,
    tp_r: float = 2.0,
    fee_pct: float = 0.0,
    min_sl_pct: float = 0.0,
    atr_sl_multiplier: float | None = None,
    atr_sl_floor: bool = False,
    # NEW — all default values preserve current behaviour
    live_parity: LiveParityConfig | None = None,
    bias_cfg: "BiasConfig | None" = None,         # required when any gate on
    strategy_params: "dict[str, StrategyOverride] | None" = None,
    htf_slope_cache: "Mapping[tuple[str,str,int,int], float | None] | None" = None,
    regime_cache: "Mapping[str, Regime] | None" = None,
    cooldown_bars_per_tf: "Mapping[str, int] | None" = None,
) -> BacktestResult: ...
```

Gates run **before** trade simulation, on the signals frame. Live operates on `list[SignalEvent]`; the engine adapts via two small helpers:

```python
def _df_to_events(df: pd.DataFrame) -> list[SignalEvent]: ...
def _events_to_df(events: list[SignalEvent], original_df: pd.DataFrame) -> pd.DataFrame: ...
```

This keeps gate logic single-sourced in `analytics/signal/gates.py` — no re-implementation, no drift risk.

### Apply order (mirror `run_scan_cycle`)

```text
detector output
  → _apply_regime_gate              (live Step −1)
  → _apply_direction_filter_gate    (live Step −0.5)
  → _apply_htf_ema_gate             (live Step 0)
  → _filter_signals_by_adr          (live ADR step)
  → _apply_conflict_resolver        (NEW — needs lift from run_scan_cycle)
  → _apply_cooldown                 (NEW — engine-side dedup state)
  → existing volume/day_filter gates (already in engine)
  → simulate_trades
```

`_apply_conflict_resolver` and `_apply_cooldown` are the only new functions; the rest are imported as-is from `analytics/signal/gates.py`.

## TOML schema

Lives under `[backtest.live_parity]` so it never collides with the live gate config (which sits at `[bias]` and `[strategy_params.*]`). Backtest **reuses** the live `[bias]` + `[strategy_params]` blocks for gate parameters — same anchors, same regime allowlist, same suppress flags. The new section only carries the on/off switches.

```toml
# Top-level — applies to single backtests and sweeps. Inherits via `extends`
# like every other [backtest.*] block.

[backtest.live_parity]
enabled = false                  # master switch
regime = false
direction_filter = false
f8_htf_ema = false
adr_bias = false
conflict_resolver = false
cooldown = false
# Optional override; falls back to baked-in defaults
# (15m=4 bars, 1h=3, 4h=2, 1d=1 — same as live cooldown_store unless overridden)
[backtest.live_parity.cooldown_bars]
"15m" = 4
"1h"  = 3
"4h"  = 2
"1d"  = 1
```

`BacktestSweepConfig` grows one field:

```python
live_parity: LiveParityConfig = field(default_factory=LiveParityConfig)
```

…populated in `load_backtest_config()` from the `[backtest.live_parity]` block.

## CLI flags

```text
--live-parity                    # master switch (all gates on)
--with-regime                    # individual toggles (additive on top of master)
--with-direction-filter
--with-f8-htf-ema
--with-adr-bias
--with-conflict-resolver
--with-cooldown
--without-<gate>                 # negate when master is on (e.g. `--live-parity --without-cooldown`)
```

CLI overrides TOML (existing precedence rule). Logged once at run start:

```text
INFO live_parity: regime=on direction_filter=on f8_htf_ema=off adr_bias=off conflict_resolver=on cooldown=on
```

## Measurement protocol (one-at-a-time)

Run on a fixed cell — e.g. `BTCUSDT 1h bos --since 2025-09-12 --save 0 --tp-r 1.5`:

| Run | Flags | Output |
| --- | --- | --- |
| 0 | baseline | n_baseline, avg_r_baseline |
| 1 | `--with-regime` | Δn, suppressed_avg_r, kept_avg_r, ΔR_total |
| 2 | `--with-direction-filter` (only) | … |
| 3 | `--with-f8-htf-ema` (only) | … |
| 4 | `--with-adr-bias` (only) | … |
| 5 | `--with-conflict-resolver` (only) | … |
| 6 | `--with-cooldown` (only) | … |
| 7 | `--live-parity` (stacked) | compare to sum-of-deltas for interaction |

Per gate verdict:

- `suppressed_avg_r <= −0.05R` → gate is doing work, ship to live hard mode.
- `suppressed_avg_r >= +0.05R` → gate is killing winners, **pull from live** or keep soft.
- `|suppressed_avg_r| < 0.05R` → noise; defer to confluence with other gates.

Existing `tools/regime_gate_replay.py` and `tools/direction_filter_replay.py` get a sibling `tools/parity_sweep_report.py` that runs the 8-run grid and prints a single table.

## Regression-suite impact

All 3 goldens in `tests/fixtures/` are produced with `live_parity` **off** by construction (default-off config). No fixture regeneration needed at merge. Add one **new** golden per gate in a follow-up branch once empirics dictate which gates ship enabled.

## Build order (5 PRs)

1. **PR-1 (foundation)** — `LiveParityConfig` + `BacktestSweepConfig` field + TOML loader + CLI flags + `_df_to_events`/`_events_to_df` adapters + logging. Zero gate ports yet. All flags default off; regression suite green.
2. **PR-2 (cheap gates)** — wire `_apply_direction_filter_gate` + `_apply_regime_gate`. These already exist in `signal/gates.py`; engine just calls them. Add unit tests at the engine boundary.
3. **PR-3 (F8 HTF EMA)** — port HTF candle loading into engine; reuse `_apply_htf_ema_gate`.
4. **PR-4 (ADR + conflict resolver)** — `_filter_signals_by_adr` already pure pandas; conflict resolver lifted from `run_scan_cycle` into `analytics/signal/gates.py` so engine imports it.
5. **PR-5 (cooldown)** — engine-side state machine; the hardest one because of state.

`/parity-sweep` skill ships with PR-5 to automate the 8-run grid.

## Open questions

- **Q1** — Cooldown semantics in backtest: live tracks `signal_state.json` *across cycles*. Backtest is one pass over history. Decision: in-memory dict scoped to the run, keyed by `(symbol, tf, strategy, direction)`; resets per `run_backtest()` call.
- **Q2** — Should `--live-parity` also flip `atr_sl_floor` on? Currently off in every prod config. **Tentative no**: ATR floor is structural-SL logic, not a live-only gate. Keep separate.
- **Q3** — Conflict resolver currently picks the higher-confidence side. Backtest doesn't have confidence at run time (it's set by recalibrate after the fact). Need a deterministic tiebreaker — propose `direction with higher prior avg_r from confidence_ratings table`, falling back to alphabetical strategy name.
