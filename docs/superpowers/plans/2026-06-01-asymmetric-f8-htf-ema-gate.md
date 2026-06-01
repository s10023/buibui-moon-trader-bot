# Asymmetric F8 HTF EMA Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the F8 HTF EMA gate suppress signals only in configured directions per strategy (`suppress_directions`), so it stops dropping net-winning counter-trend shorts while keeping the working counter-trend-long filter.

**Architecture:** Add one field, `suppress_directions: tuple[str, ...]`, to `HtfEmaAnchor` + `BiasConfig`, resolved through the existing TOML parser and the existing `htf_ema_anchor()` resolver. The live gate `_apply_htf_ema_gate` gains a one-clause membership check; the backtest engine inherits the behavior for free because `_apply_htf_ema_gate_to_signals` delegates to that same live function. Ship the config in **soft mode** (log-only) first; the hard-mode flip is a deliberate follow-up gated on OOS confirmation (final task adds the OOS tool).

**Tech Stack:** Python 3.13, Poetry, pytest + unittest.mock, DuckDB, pandas, ruff, mypy strict, TOML (`config/strategy_params.toml`).

---

## Background the engineer needs

- **What F8 does today** (`analytics/signal/gates.py::_apply_htf_ema_gate`): for each `SignalEvent`, resolve a per-strategy HTF EMA anchor, look up that anchor's slope in a pre-computed cache, and drop the event when its direction *opposes* the slope sign (short when slope up, long when slope down). Symmetric: gates both directions identically. `mode="hard"` drops; `mode="soft"` only logs.
- **Why we're changing it** — an ablation (`tools/htf_ema_gate_replay.py`, committed `ffd723a`) over 842K permissive-baseline `backtest_trades` showed the gate is directionally inverted: counter-trend **shorts win** (+0.18 to +0.57R, n=84K on 15m) while being suppressed; only counter-trend **longs** deserve dropping. By `strategy_type`, the short-side relax is safe in 6/7 families; `fib` is the exception (its shorts also lose). Full reasoning: `docs/superpowers/specs/2026-06-01-asymmetric-f8-htf-ema-gate-design.md`.
- **Backward compatibility** — omitting `suppress_directions` anywhere must reproduce today's symmetric behavior, so the field defaults to `("long", "short")` on both `HtfEmaAnchor` and `BiasConfig`. Existing tests construct these objects without the field and must keep passing untouched.
- **Resolution precedence** for a strategy's `suppress_directions`: per-strategy override value → global `[bias.htf_ema].suppress_directions` → built-in default `("long","short")`. The parser bakes override→global at parse time (stored on each `HtfEmaAnchor`); the resolver fills the no-override case from `BiasConfig.htf_ema_default_suppress_directions`.
- **Tuple, not list** — store as `tuple[str, ...]` (immutable; safe as a dataclass default and avoids any accidental mutation of shared config).
- **Run all commands from repo root** `/home/kng/repo/buibui-moon-trader-bot` on branch `feat/asymmetric-f8-htf-ema-gate`. After any Python change run `make lint-py`, `make typecheck`, `make test`.

## File structure

- **Modify** `analytics/signal_config.py` — add the field to `HtfEmaAnchor` + `BiasConfig`, extend `htf_ema_anchor()`, extend the `[bias.htf_ema]` TOML parser with validation. (Task 1, 2, 4.)
- **Modify** `analytics/signal/gates.py` — one membership clause in `_apply_htf_ema_gate`. (Task 3.)
- **Modify** `config/strategy_params.toml` — global `suppress_directions`, `mode="soft"`, per-strategy overrides. (Task 6.)
- **Modify** `tests/test_f8_htf_ema_gate.py`, `tests/test_signal_config.py`, `tests/test_live_parity_htf_ema_gate.py` — new behavior + parity + backward-compat tests. (Tasks 1–5.)
- **Modify** `tools/htf_ema_gate_replay.py` + **create** `tests/test_htf_ema_gate_replay.py` — IS/OOS split for the hard-flip decision. (Task 7.)
- **Modify** `CLAUDE.md`, `.claude/context/analytics.md`, `.claude/skills/signal-watch/SKILL.md` — document `suppress_directions`. (Task 8.)

---

## Task 1: `suppress_directions` field on `HtfEmaAnchor` + `BiasConfig`

**Files:**

- Modify: `analytics/signal_config.py:246-256` (`HtfEmaAnchor`), `analytics/signal_config.py:292-299` (`BiasConfig` F8 fields)
- Test: `tests/test_signal_config.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_signal_config.py` (import `HtfEmaAnchor, BiasConfig` from `analytics.signal_config` if not already imported at top):

```python
def test_htf_ema_anchor_defaults_to_both_directions() -> None:
    from analytics.signal_config import HtfEmaAnchor

    anchor = HtfEmaAnchor()
    assert anchor.suppress_directions == ("long", "short")


def test_bias_config_default_suppress_directions_is_both() -> None:
    from analytics.signal_config import BiasConfig

    bias = BiasConfig()
    assert bias.htf_ema_default_suppress_directions == ("long", "short")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_signal_config.py::test_htf_ema_anchor_defaults_to_both_directions tests/test_signal_config.py::test_bias_config_default_suppress_directions_is_both -v`
Expected: FAIL — `AttributeError: 'HtfEmaAnchor' object has no attribute 'suppress_directions'`.

- [ ] **Step 3: Add the fields**

In `analytics/signal_config.py`, `HtfEmaAnchor` (after `slope_lookback: int = 10`):

```python
    tf: str = "4h"
    period: int = 50
    slope_lookback: int = 10
    # F8 directions the gate is allowed to suppress when a signal opposes the
    # HTF slope. Empty tuple = never suppress (full exempt). Default reproduces
    # the original symmetric gate.
    suppress_directions: tuple[str, ...] = ("long", "short")
```

In `BiasConfig`, alongside the other `htf_ema_*` defaults (after `htf_ema_deadband_pct: float = 0.003`):

```python
    htf_ema_default_suppress_directions: tuple[str, ...] = ("long", "short")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_signal_config.py::test_htf_ema_anchor_defaults_to_both_directions tests/test_signal_config.py::test_bias_config_default_suppress_directions_is_both -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add analytics/signal_config.py tests/test_signal_config.py
git commit -m "feat: add suppress_directions field to HtfEmaAnchor + BiasConfig"
```

---

## Task 2: Resolver fills `suppress_directions` for the no-override case

**Files:**

- Modify: `analytics/signal_config.py:320-329` (`BiasConfig.htf_ema_anchor`)
- Test: `tests/test_signal_config.py`

- [ ] **Step 1: Write the failing test**

```python
def test_htf_ema_anchor_no_override_inherits_global_suppress_directions() -> None:
    from analytics.signal_config import BiasConfig

    bias = BiasConfig(htf_ema_default_suppress_directions=("long",))
    anchor = bias.htf_ema_anchor("bos")  # no override
    assert anchor.tf == "4h"
    assert anchor.suppress_directions == ("long",)


def test_htf_ema_anchor_override_keeps_its_own_suppress_directions() -> None:
    from analytics.signal_config import BiasConfig, HtfEmaAnchor

    bias = BiasConfig(
        htf_ema_default_suppress_directions=("long",),
        htf_ema_per_strategy={"cvd_divergence": HtfEmaAnchor(tf="1d", suppress_directions=())},
    )
    anchor = bias.htf_ema_anchor("cvd_divergence")
    assert anchor.tf == "1d"
    assert anchor.suppress_directions == ()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_signal_config.py::test_htf_ema_anchor_no_override_inherits_global_suppress_directions -v`
Expected: FAIL — anchor returns built-in `("long","short")`, not `("long",)`.

- [ ] **Step 3: Update the resolver**

In `analytics/signal_config.py`, `htf_ema_anchor`, add `suppress_directions` to the default-construction branch:

```python
    def htf_ema_anchor(self, strategy: str) -> HtfEmaAnchor:
        """Resolve the HTF anchor for a strategy (override → default)."""
        override = self.htf_ema_per_strategy.get(strategy)
        if override is not None:
            return override
        return HtfEmaAnchor(
            tf=self.htf_ema_default_tf,
            period=self.htf_ema_default_period,
            slope_lookback=self.htf_ema_default_slope_lookback,
            suppress_directions=self.htf_ema_default_suppress_directions,
        )
```

(The override branch already carries its own `suppress_directions`; Task 4's parser bakes override→global into it.)

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_signal_config.py -k htf_ema_anchor -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add analytics/signal_config.py tests/test_signal_config.py
git commit -m "feat: resolver inherits global suppress_directions when no override"
```

---

## Task 3: Gate honors `suppress_directions`

**Files:**

- Modify: `analytics/signal/gates.py:221-234` (inside `_apply_htf_ema_gate`)
- Test: `tests/test_f8_htf_ema_gate.py`

- [ ] **Step 1: Write the failing tests**

In `tests/test_f8_htf_ema_gate.py`, extend the `_bias` helper to accept `default_suppress_directions` and add tests. Replace the existing `_bias` helper with:

```python
def _bias(
    *,
    enabled: bool = True,
    mode: str = "hard",
    deadband: float = 0.003,
    overrides: dict[str, HtfEmaAnchor] | None = None,
    default_suppress_directions: tuple[str, ...] = ("long", "short"),
) -> BiasConfig:
    return BiasConfig(
        htf_ema_enabled=enabled,
        htf_ema_mode=mode,
        htf_ema_default_tf="4h",
        htf_ema_default_period=50,
        htf_ema_default_slope_lookback=10,
        htf_ema_deadband_pct=deadband,
        htf_ema_per_strategy=overrides or {},
        htf_ema_default_suppress_directions=default_suppress_directions,
    )
```

Add these tests to `class TestHtfEmaGate`:

```python
    def test_long_only_scope_keeps_counter_trend_short(self) -> None:
        # slope up → SHORT opposes, but scope = ["long"] → short is NOT suppressed.
        cache = {("BTCUSDT", "4h", 50, 10): 0.05}
        events = [_evt("bos", "long"), _evt("bos", "short")]
        out = _apply_htf_ema_gate(
            events,
            _bias(mode="hard", default_suppress_directions=("long",)),
            cache,
            "BTCUSDT",
            "1h",
        )
        assert {e.direction for e in out} == {"long", "short"}

    def test_long_only_scope_still_drops_counter_trend_long(self) -> None:
        # slope down → LONG opposes; scope ["long"] still drops it.
        cache = {("BTCUSDT", "4h", 50, 10): -0.05}
        events = [_evt("bos", "long"), _evt("bos", "short")]
        out = _apply_htf_ema_gate(
            events,
            _bias(mode="hard", default_suppress_directions=("long",)),
            cache,
            "BTCUSDT",
            "1h",
        )
        assert [e.direction for e in out] == ["short"]

    def test_empty_scope_exempts_strategy_via_override(self) -> None:
        # cvd_divergence override with suppress_directions=() → never suppressed.
        overrides = {
            "cvd_divergence": HtfEmaAnchor(
                tf="4h", period=50, slope_lookback=10, suppress_directions=()
            )
        }
        cache = {("BTCUSDT", "4h", 50, 10): 0.05}
        events = [_evt("cvd_divergence", "long"), _evt("cvd_divergence", "short")]
        out = _apply_htf_ema_gate(
            events, _bias(mode="hard", overrides=overrides), cache, "BTCUSDT", "1h"
        )
        assert len(out) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_f8_htf_ema_gate.py -k "scope or exempt" -v`
Expected: FAIL — `test_long_only_scope_keeps_counter_trend_short` drops the short (current symmetric gate); `test_empty_scope_exempts_strategy_via_override` drops the short.

- [ ] **Step 3: Add the membership clause**

In `analytics/signal/gates.py`, `_apply_htf_ema_gate`, change the opposing check (currently lines ~229-234):

```python
        opposing = (slope > 0 and event.direction == "short") or (
            slope < 0 and event.direction == "long"
        )
        if not opposing or event.direction not in anchor.suppress_directions:
            kept.append(event)
            continue
```

(`anchor` is already resolved one line above as `bias_cfg.htf_ema_anchor(event.strategy)`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_f8_htf_ema_gate.py -v`
Expected: PASS — the 3 new tests pass and all pre-existing F8 tests still pass (default scope = both directions).

- [ ] **Step 5: Commit**

```bash
git add analytics/signal/gates.py tests/test_f8_htf_ema_gate.py
git commit -m "feat: F8 gate suppresses only configured directions"
```

---

## Task 4: TOML parser reads + validates `suppress_directions`

**Files:**

- Modify: `analytics/signal_config.py:805-824` (htf parser block), `analytics/signal_config.py:852-858` (`BiasConfig(...)` construction)
- Test: `tests/test_signal_config.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_signal_config.py` already loads TOML via a temp-file helper; check the top of the file for the existing pattern (look for `tomllib`/`load_signal_config`/`tmp_path`). Add tests that write a minimal config and parse it. If a `_write_config(tmp_path, text)` helper exists, reuse it; otherwise use this self-contained form:

```python
def test_parser_reads_global_and_override_suppress_directions(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from analytics.signal_config import load_signal_config

    cfg_text = """
symbols = ["BTCUSDT"]
timeframes = ["4h"]

[bias.htf_ema]
enabled = true
mode = "soft"
suppress_directions = ["long"]

[bias.htf_ema.per_strategy]
cvd_divergence = { tf = "1d", suppress_directions = [] }
ema = { tf = "1d" }
"""
    p = tmp_path / "c.toml"
    p.write_text(cfg_text)
    bias = load_signal_config(p).bias
    assert bias.htf_ema_default_suppress_directions == ("long",)
    # Override with explicit [] is exempt.
    assert bias.htf_ema_anchor("cvd_divergence").suppress_directions == ()
    # Override without the key inherits the global ["long"].
    assert bias.htf_ema_anchor("ema").suppress_directions == ("long",)
    # Unlisted strategy inherits the global default too.
    assert bias.htf_ema_anchor("bos").suppress_directions == ("long",)


def test_parser_rejects_invalid_direction_token(tmp_path) -> None:  # type: ignore[no-untyped-def]
    import pytest

    from analytics.signal_config import load_signal_config

    cfg_text = """
symbols = ["BTCUSDT"]
timeframes = ["4h"]

[bias.htf_ema]
enabled = true
suppress_directions = ["sideways"]
"""
    p = tmp_path / "c.toml"
    p.write_text(cfg_text)
    with pytest.raises(ValueError, match="suppress_directions"):
        load_signal_config(p)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_signal_config.py -k suppress_directions -v`
Expected: FAIL — `test_parser_reads_...` returns built-in `("long","short")` (parser ignores the key); `test_parser_rejects_...` does not raise.

- [ ] **Step 3: Implement parser + validation**

In `analytics/signal_config.py`, add a helper near the top of the module (after imports):

```python
_VALID_DIRECTIONS = ("long", "short")


def _parse_suppress_directions(
    raw: object, default: tuple[str, ...], where: str
) -> tuple[str, ...]:
    """Validate a suppress_directions list ⊆ {long, short}; fall back to default."""
    if raw is None:
        return default
    if not isinstance(raw, list):
        raise ValueError(f"{where}.suppress_directions must be a TOML array")
    out: list[str] = []
    for item in raw:
        token = str(item)
        if token not in _VALID_DIRECTIONS:
            raise ValueError(
                f"{where}.suppress_directions has invalid value {token!r} "
                f"(allowed: {_VALID_DIRECTIONS})"
            )
        out.append(token)
    return tuple(out)
```

Then in the htf parser block (currently lines ~811-824), after `htf_default_slope_lb = ...`:

```python
    htf_default_suppress = _parse_suppress_directions(
        raw_htf.get("suppress_directions"), ("long", "short"), "[bias.htf_ema]"
    )
    htf_per_strategy: dict[str, HtfEmaAnchor] = {}
    for strat, ov in raw_htf_overrides.items():
        if not isinstance(ov, dict):
            raise ValueError(
                f"[bias.htf_ema.per_strategy.{strat}] must be a TOML table"
            )
        htf_per_strategy[str(strat)] = HtfEmaAnchor(
            tf=str(ov.get("tf", htf_default_tf)),
            period=int(ov.get("period", htf_default_period)),
            slope_lookback=int(ov.get("slope_lookback", htf_default_slope_lb)),
            suppress_directions=_parse_suppress_directions(
                ov.get("suppress_directions"),
                htf_default_suppress,
                f"[bias.htf_ema.per_strategy.{strat}]",
            ),
        )
```

And in the `BiasConfig(...)` constructor call (currently lines ~852-858), add the new kwarg after `htf_ema_per_strategy=htf_per_strategy,`:

```python
        htf_ema_default_suppress_directions=htf_default_suppress,
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_signal_config.py -k suppress_directions -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add analytics/signal_config.py tests/test_signal_config.py
git commit -m "feat: parse + validate [bias.htf_ema] suppress_directions"
```

---

## Task 5: Engine-parity test (no code change)

**Files:**

- Test: `tests/test_live_parity_htf_ema_gate.py`

Confirms the backtest engine's `_apply_htf_ema_gate_to_signals` honors `suppress_directions` automatically (it delegates to the live `_apply_htf_ema_gate`).

This test mirrors the file's existing `test_hard_mode_drops_opposing_shorts`
(uses the module helpers `_toy_signals` and `pd.Series`; note the helper's
argument order is `(signals, symbol, timeframe, strategy, bias, series_map)`),
but builds the `BiasConfig` directly so it can set
`htf_ema_default_suppress_directions=("long",)`.

- [ ] **Step 1: Write the test**

Add to `class TestApplyHtfEmaGateToSignals` in `tests/test_live_parity_htf_ema_gate.py`:

```python
    def test_long_only_scope_keeps_counter_trend_short(self) -> None:
        # Up-slope → a SHORT opposes; scope ["long"] must keep it (and still
        # keep the aligned long). Proves the engine inherits the live gate's
        # suppress_directions via delegation — no engine code change.
        bias = BiasConfig(
            htf_ema_enabled=True,
            htf_ema_mode="hard",
            htf_ema_default_tf="4h",
            htf_ema_default_period=50,
            htf_ema_default_slope_lookback=10,
            htf_ema_deadband_pct=0.003,
            htf_ema_default_suppress_directions=("long",),
        )
        anchor_key = ("4h", 50, 10)
        slope_series = pd.Series(
            [0.05, 0.05, 0.05, 0.05],
            index=pd.Index([1_000, 2_000, 3_000, 4_000], dtype="int64"),
        )
        signals = _toy_signals([3_500, 4_500], ["long", "short"])
        out = _apply_htf_ema_gate_to_signals(
            signals,
            "BTCUSDT",
            "1h",
            "bos",
            bias,
            {anchor_key: slope_series},
        )
        # Both survive: long aligns with up-slope, short is out-of-scope.
        assert sorted(out["open_time"].tolist()) == [3_500, 4_500]
```

- [ ] **Step 2: Run test to verify it passes**

Run: `poetry run pytest "tests/test_live_parity_htf_ema_gate.py::TestApplyHtfEmaGateToSignals::test_long_only_scope_keeps_counter_trend_short" -v`
Expected: PASS (the engine already delegates to the live gate; this locks in parity). If it FAILS, the engine path is not re-resolving the anchor through `bias_cfg.htf_ema_anchor` — stop and reconcile with Task 3.

- [ ] **Step 3: Commit**

```bash
git add tests/test_live_parity_htf_ema_gate.py
git commit -m "test: engine F8 gate honors suppress_directions via live delegation"
```

---

## Task 6: Apply the config (soft-mode first)

**Files:**

- Modify: `config/strategy_params.toml:55-69`

The three `signal_watch*.toml` configs inherit this block via `extends`, so only this file changes. **This sets `mode="soft"` — F8 stops dropping live during the observation window. The hard-mode flip is a deliberate follow-up (see "After this plan").**

- [ ] **Step 1: Edit the block**

Replace `config/strategy_params.toml` lines 55-69 with:

```toml
[bias.htf_ema]
enabled = true
mode = "soft"                  # soft-first observation; flip to "hard" after OOS confirm
default_tf = "4h"
default_period = 50
default_slope_lookback = 10
deadband_pct = 0.003
suppress_directions = ["long"] # gate counter-trend longs only; counter-trend shorts win (ablation 2026-06-01)

[bias.htf_ema.per_strategy]
# flow family — fully exempt (counter-trend shorts +0.78R, longs ~0):
cvd_divergence  = { tf = "1d", period = 50, slope_lookback = 10, suppress_directions = [] }
smt_divergence  = { tf = "1d", period = 50, slope_lookback = 10, suppress_directions = [] }
# fib family — keep full symmetric gate (both directions lose counter-trend):
fib_golden_zone = { suppress_directions = ["long", "short"] }
ote_entry       = { suppress_directions = ["long", "short"] }
# 1d-anchor strategies inherit the global suppress_directions = ["long"]:
ema             = { tf = "1d", period = 50, slope_lookback = 10 }
orb             = { tf = "1d", period = 50, slope_lookback = 10 }
eqh_eql         = { tf = "1d", period = 50, slope_lookback = 10 }
marubozu        = { tf = "1d", period = 50, slope_lookback = 10 }
```

- [ ] **Step 2: Verify the config loads and resolves as intended**

Run:

```bash
PYTHONPATH=. poetry run python -c "
from analytics.signal_config import load_signal_config
b = load_signal_config('config/strategy_params.toml').bias
assert b.htf_ema_mode == 'soft'
assert b.htf_ema_default_suppress_directions == ('long',)
assert b.htf_ema_anchor('cvd_divergence').suppress_directions == ()
assert b.htf_ema_anchor('fib_golden_zone').suppress_directions == ('long','short')
assert b.htf_ema_anchor('bos').suppress_directions == ('long',)
assert b.htf_ema_anchor('ema').tf == '1d' and b.htf_ema_anchor('ema').suppress_directions == ('long',)
print('config OK')
"
```

Expected: `config OK`.

- [ ] **Step 3: Confirm golden fixtures are unaffected**

No `[live_parity]` block exists in any config, so saved backtests do not apply F8 and the regression goldens must not move.

Run: `make test-regression`
Expected: PASS (or SKIP if fixture parquets are absent) — **no** golden diff. If goldens diff, stop and investigate (something is wiring F8 into the backtest pipeline unexpectedly).

- [ ] **Step 4: Commit**

```bash
git add config/strategy_params.toml
git commit -m "feat: asymmetric F8 config (soft-mode, suppress_directions per family)"
```

---

## Task 7: IS/OOS split for the replay tool (hard-flip decision gate)

**Files:**

- Modify: `tools/htf_ema_gate_replay.py`
- Create: `tests/test_htf_ema_gate_replay.py`

Spec §6 requires OOS confirmation before flipping to hard. Add an `--oos-frac` time-split that reports suppressed-subset avg_r for in-sample vs out-of-sample per `strategy_type × direction`, and a per-family promote verdict (relax short-side only if short-supp avg_r > 0 in **both** IS and OOS).

- [ ] **Step 1: Write the failing test**

Create `tests/test_htf_ema_gate_replay.py`:

```python
"""Tests for the IS/OOS time-split helper in tools/htf_ema_gate_replay.py."""

from __future__ import annotations

import pandas as pd

from tools.htf_ema_gate_replay import split_is_oos


def _df(times: list[int]) -> pd.DataFrame:
    return pd.DataFrame({"entry_time": times, "pnl_r": [0.0] * len(times)})


def test_split_is_oos_partitions_by_time_quantile() -> None:
    df = _df([10, 20, 30, 40, 50, 60, 70, 80, 90, 100])
    is_df, oos_df = split_is_oos(df, oos_frac=0.3)
    # Latest 30% by entry_time go to OOS.
    assert sorted(oos_df["entry_time"]) == [80, 90, 100]
    assert sorted(is_df["entry_time"]) == [10, 20, 30, 40, 50, 60, 70]


def test_split_is_oos_zero_frac_returns_all_in_sample() -> None:
    df = _df([1, 2, 3])
    is_df, oos_df = split_is_oos(df, oos_frac=0.0)
    assert len(is_df) == 3
    assert oos_df.empty
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_htf_ema_gate_replay.py -v`
Expected: FAIL — `ImportError: cannot import name 'split_is_oos'`.

- [ ] **Step 3: Implement `split_is_oos` + wire the CLI flag**

In `tools/htf_ema_gate_replay.py`, add the helper (above `run`):

```python
def split_is_oos(
    trades: pd.DataFrame, oos_frac: float
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split trades by entry_time: latest `oos_frac` fraction → out-of-sample.

    oos_frac <= 0 → all in-sample, empty OOS. Deterministic time split (no
    shuffling) so the OOS window is a genuine forward holdout.
    """
    if oos_frac <= 0 or trades.empty:
        return trades, trades.iloc[0:0]
    cutoff = trades["entry_time"].quantile(1.0 - oos_frac)
    is_df = trades[trades["entry_time"] < cutoff]
    oos_df = trades[trades["entry_time"] >= cutoff]
    return is_df, oos_df
```

Add a per-family IS/OOS renderer:

```python
def render_is_oos(trades: pd.DataFrame, oos_frac: float) -> str:
    from analytics.strategies import STRATEGY_REGISTRY

    types = {n: s.strategy_type for n, s in STRATEGY_REGISTRY.items()}
    trades = trades.copy()
    trades["type"] = trades["strategy"].map(types)
    is_df, oos_df = split_is_oos(trades, oos_frac)
    lines = ["", f"IS/OOS short-side check (oos_frac={oos_frac}):", "-" * 64]
    lines.append(f"{'type':<14} {'IS short_r':>11} {'OOS short_r':>12} {'verdict':>10}")
    lines.append("-" * 64)
    for typ in sorted(t for t in types.values() if isinstance(t, str)):
        def _short_r(df: pd.DataFrame) -> float | None:
            sub = df[(df["type"] == typ) & (df["suppressed"]) & (df["direction"] == "short")]
            return float(sub["pnl_r"].mean()) if len(sub) else None

        is_r, oos_r = _short_r(is_df), _short_r(oos_df)
        if is_r is None or oos_r is None:
            verdict = "n/a"
        elif is_r > 0 and oos_r > 0:
            verdict = "RELAX"
        else:
            verdict = "KEEP"
        is_s = f"{is_r:+.4f}" if is_r is not None else "  --  "
        oos_s = f"{oos_r:+.4f}" if oos_r is not None else "  --  "
        lines.append(f"{typ:<14} {is_s:>11} {oos_s:>12} {verdict:>10}")
    return "\n".join(lines)
```

Wire into `run` (append to the rendered verdict when `oos_frac > 0`) and add the CLI flag in `main`:

```python
    parser.add_argument("--oos-frac", type=float, default=0.0,
                        help="Fraction of latest trades held out for OOS short-side check.")
```

Update `run`'s signature to accept `oos_frac: float = 0.0` and, when `> 0`, append `render_is_oos(trades, oos_frac)` to the returned verdict string; pass `args.oos_frac` from `main`.

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_htf_ema_gate_replay.py -v`
Expected: PASS.

- [ ] **Step 5: Smoke-run the OOS report**

Run: `PYTHONPATH=. poetry run python tools/htf_ema_gate_replay.py --oos-frac 0.3 2>/dev/null | tail -20`
Expected: the existing tables plus an `IS/OOS short-side check` block with per-family `RELAX`/`KEEP` verdicts. (`flow`/`candlestick`/`session`/`structural`/`price_action`/`trend` are expected to read `RELAX`; `fib` `KEEP`. Record the output — it is the evidence for the hard-flip follow-up.)

- [ ] **Step 6: Commit**

```bash
git add tools/htf_ema_gate_replay.py tests/test_htf_ema_gate_replay.py
git commit -m "feat: IS/OOS short-side split in htf_ema_gate_replay for hard-flip gate"
```

---

## Task 8: Documentation

**Files:**

- Modify: `CLAUDE.md`, `.claude/context/analytics.md`, `.claude/skills/signal-watch/SKILL.md`

- [ ] **Step 1: Update CLAUDE.md**

In the `analytics/signal/` bullet's `gates.py` description, where `_apply_htf_ema_gate` is mentioned, note that F8 now honors a per-strategy `suppress_directions` scope (global default + per-strategy override; empty list = exempt). In the `config/strategy_params.toml` bullet, mention the `[bias.htf_ema].suppress_directions` knob.

- [ ] **Step 2: Update analytics.md + signal-watch SKILL.md**

In `.claude/context/analytics.md`, find the F8 HTF EMA gate description and document `suppress_directions` (precedence: per-strategy → global → `("long","short")`; `[]` = full exempt; default-omitted = symmetric/back-compat). In `.claude/skills/signal-watch/SKILL.md`, add `suppress_directions` to the `[bias.htf_ema]` TOML reference with the family rationale (flow exempt, fib symmetric, everything else long-only).

- [ ] **Step 3: Lint the markdown**

Run: `make lint-md`
Expected: PASS (0 errors).

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md .claude/context/analytics.md .claude/skills/signal-watch/SKILL.md
git commit -m "docs: document F8 suppress_directions knob"
```

---

## Final verification (run before opening the PR)

- [ ] **All quality gates green**

Run: `make lint-py && make typecheck && make test && make lint-md`
Expected: ruff format+lint clean; mypy strict 0 issues; full pytest suite passes (existing F8 tests unchanged + new tests); markdownlint clean.

- [ ] **Confirm no behavior drift in saved backtests**

Run: `make test-regression`
Expected: PASS / SKIP, no golden diff (F8 is not in the backtest pipeline).

- [ ] **Open the PR** via `/pr-summary` then `/post-branch` (per repo conventions).

---

## After this plan (out of scope here, tracked for the follow-up)

1. **Soft observation ≥2 weeks** — watch the live F8 logs (the daemon logs each would-suppress decision) and confirm the would-drop short signals' realized outcomes track the ablation.
2. **OOS confirmation** — run `tools/htf_ema_gate_replay.py --oos-frac 0.3`; require `RELAX` for the six relaxed families and `KEEP` for `fib`.
3. **Hard-mode flip** — one-line change `mode = "soft"` → `mode = "hard"` in `config/strategy_params.toml`, only after (1) and (2) pass. This re-activates dropping, now direction-scoped. Pure config; reversible.
4. **Dial A (anchor-TF ladder)** — only if the OOS replay shows suppressed-subset avg_r still varies materially with the signal's TF within a fixed direction (spec §7).
