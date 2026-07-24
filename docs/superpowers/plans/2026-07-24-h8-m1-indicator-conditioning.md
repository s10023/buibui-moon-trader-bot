# H8 — M1 Indicator-State Conditioning Audit — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a read-only audit that tags every historical `backtest_trades` row with the M1 market-indicator state that held at its entry, then emits a pre-committed BUILD / AVOID / NO-EDGE / INSUFFICIENT verdict per (indicator-state × direction) under the de-biased gate.

**Architecture:** A near-clone of `tools/warning_value_audit.py`. A pure library `analytics/indicator_condition.py` (config + verdict types + inverted-verdict mapping + axis extraction + as-of-entry tagging + gate) and a DB-front-door driver `tools/indicator_condition_audit.py` that reuses `warning_value_audit`'s trade-load / dedup / market-load helpers. No engine, schema, or golden change.

**Tech Stack:** Python 3.11, pandas, duckdb, `analytics/brief/indicators.py::build_indicator_state`, `analytics/audit_guard.py`, `analytics/research_guards/*`.

**Spec:** `docs/superpowers/specs/2026-07-24-h8-m1-indicator-conditioning-design.md` (read it first).

---

## File Structure

- **Create** `analytics/indicator_condition.py` — pure lib (no I/O beyond a passed conn): `IndicatorConditionConfig`, `ConditionVerdict`, `_AXES`, `axis_states`, `tag_trades`, `build_condition_cells`, `_map_verdict`, `evaluate_conditions`.
- **Create** `tools/indicator_condition_audit.py` — driver; reuses `tools/warning_value_audit.py`'s `_tf_ms`, `normalize_backtest`, `normalize_live`, `_load_entries`, `_load_market`.
- **Create** `tests/test_indicator_condition.py` — unit tests (mapping, axis extraction, look-ahead mutation, gate).
- **Modify** `Makefile` — add `buibui-indicator-condition-audit` to `.PHONY` and a target wrapping the CLI.

**Reused verbatim** (import, do NOT re-implement): from `tools.warning_value_audit` — `_tf_ms(tf)`, `normalize_backtest(df)`, `normalize_live(df)`, `_load_entries(db, src, since_ms)`, `_load_market(entries, db)`. Read that file before Task 5 so the reuse is exact.

---

## Task 1: Config + verdict types + the inverted-verdict mapping

**Files:**

- Create: `analytics/indicator_condition.py`
- Test: `tests/test_indicator_condition.py`

The `audit_guard` verdict is **sign-inverted** (`DISABLE ⇔ reliably positive`, `ENABLE ⇔ reliably negative`). This task encodes the mapping and locks it with a mutation-proof test — the intuitive map inverts every result (it bit ST9).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_indicator_condition.py
from analytics.indicator_condition import IndicatorConditionConfig, _map_verdict

CFG = IndicatorConditionConfig()


def test_map_disable_positive_lift_is_build() -> None:
    # audit_guard DISABLE == with-state slice reliably POSITIVE -> BUILD.
    assert (
        _map_verdict("DISABLE", lift=0.20, lift_lo=0.05, lift_hi=0.35, dsr=0.97, pbo=0.2, cfg=CFG)
        == "BUILD"
    )


def test_map_enable_negative_lift_is_avoid() -> None:
    assert (
        _map_verdict("ENABLE", lift=-0.20, lift_lo=-0.35, lift_hi=-0.05, dsr=0.97, pbo=0.2, cfg=CFG)
        == "AVOID"
    )


def test_map_disable_is_never_avoid() -> None:
    # Guardrail: the intuitive-but-wrong DISABLE->AVOID map must be impossible.
    assert (
        _map_verdict("DISABLE", lift=0.20, lift_lo=0.05, lift_hi=0.35, dsr=0.97, pbo=0.2, cfg=CFG)
        != "AVOID"
    )


def test_map_family_fail_is_no_edge() -> None:
    # DSR/PBO family gate not cleared -> NO-EDGE even with a clean lift.
    assert (
        _map_verdict("DISABLE", lift=0.20, lift_lo=0.05, lift_hi=0.35, dsr=0.80, pbo=0.2, cfg=CFG)
        == "NO-EDGE"
    )


def test_map_concentrate_is_no_edge() -> None:
    assert (
        _map_verdict("CONCENTRATE", lift=0.20, lift_lo=0.05, lift_hi=0.35, dsr=0.97, pbo=0.2, cfg=CFG)
        == "NO-EDGE"
    )


def test_map_insufficient_passthrough() -> None:
    assert (
        _map_verdict("INSUFFICIENT", lift=0.0, lift_lo=0.0, lift_hi=0.0, dsr=None, pbo=None, cfg=CFG)
        == "INSUFFICIENT"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. poetry run pytest tests/test_indicator_condition.py -v`
Expected: FAIL — `ImportError` / `_map_verdict` not defined.

- [ ] **Step 3: Write minimal implementation**

```python
# analytics/indicator_condition.py
"""H8 M1 indicator-state conditioning audit — pure, read-only.

Tags each backtest trade with the M1 indicator state as-of its entry and
emits a pre-committed BUILD/AVOID/NO-EDGE/INSUFFICIENT verdict per
(axis-state x direction). Mirrors tools/warning_value_audit.py.

audit_guard is SIGN-INVERTED: DISABLE == reliably positive (-> BUILD),
ENABLE == reliably negative (-> AVOID). Do not "fix" this to the intuitive map.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class IndicatorConditionConfig:
    bar: float = 0.05          # R economic bar (~2.5x round-trip cost)
    alpha: float = 0.05        # Holm family alpha
    min_n: int = 30            # per-cell floor
    n_boot: int = 2000         # bootstrap resamples
    block: int = 5             # block length for serial-correlation-aware boot
    seed: int = 12345
    dsr_floor: float = 0.95
    pbo_ceil: float = 0.5


def _map_verdict(
    decision: str,
    *,
    lift: float,
    lift_lo: float,
    lift_hi: float,
    dsr: float | None,
    pbo: float | None,
    cfg: IndicatorConditionConfig,
) -> str:
    """Map an audit_guard cell decision to an H8 verdict (INVERTED)."""
    if decision == "INSUFFICIENT":
        return "INSUFFICIENT"
    family_ok = (
        dsr is not None
        and pbo is not None
        and dsr >= cfg.dsr_floor
        and pbo <= cfg.pbo_ceil
    )
    if decision == "DISABLE" and lift > 0 and lift_lo > 0 and family_ok:
        return "BUILD"
    if decision == "ENABLE" and lift < 0 and lift_hi < 0 and family_ok:
        return "AVOID"
    return "NO-EDGE"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. poetry run pytest tests/test_indicator_condition.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add analytics/indicator_condition.py tests/test_indicator_condition.py
git commit -m "feat(h8): indicator-condition config + inverted-verdict mapping"
```

---

## Task 2: Axis extraction from `IndicatorState`

**Files:**

- Modify: `analytics/indicator_condition.py`
- Test: `tests/test_indicator_condition.py`

Convert an `IndicatorState` (+ regime label + ref price) into the 10 pre-registered axis states. Before writing, **read `analytics/brief/types.py`** for the exact fields of `EmaState`, `BbState`, `VwapState`, `ProfileState` (poc/vah/val), `PaState`, `MondayState` — the code below assumes: `EmaState.stack` / `.slope_200`, `BbState.squeeze` / `.pct_b`, `VwapState.weekly_dist_atr` / `.monthly_dist_atr`, `ProfileState.vah` / `.val`, `PaState.label`, `MondayState.state`. Adjust attribute names if they differ.

- [ ] **Step 1: Write the failing test**

```python
from analytics.brief.types import BbState, EmaState, IndicatorState, MondayState, PaState
from analytics.indicator_condition import _AXES, axis_states


def _state(**kw: object) -> IndicatorState:
    base = dict(ema=None, range_state=None, monday=None, candles=None, pa=None, bb=None, vwap=None, profile=None)
    base.update(kw)
    return IndicatorState(**base)  # type: ignore[arg-type]


def test_axis_states_reads_ema_bb_pa_monday() -> None:
    st = _state(
        ema=EmaState(above_20=True, above_50=True, above_200=False, stack="bullish", slope_200="rising"),
        bb=BbState(pct_b=0.05, bandwidth=0.02, bw_pctile=0.1, squeeze=True),
        pa=PaState(label="grind_up", er=0.5, speed_atr=0.4),
        monday=MondayState(state="inside", pos=0.4),
    )
    ax = axis_states(st, regime_label="trend", ref_close=100.0)
    assert ax["ema_stack"] == "bullish"
    assert ax["ema_slope"] == "rising"
    assert ax["bb_squeeze"] == "squeeze"
    assert ax["bb_pctb"] == "low"           # 0.05 < 0.2
    assert ax["pa_char"] == "grind_up"
    assert ax["monday_range"] == "inside"
    assert ax["regime"] == "trend"


def test_axis_states_missing_subblocks_are_none() -> None:
    ax = axis_states(_state(), regime_label=None, ref_close=100.0)
    assert set(ax) == set(_AXES)
    assert all(v is None for v in ax.values())
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=. poetry run pytest tests/test_indicator_condition.py -k axis_states -v`
Expected: FAIL — `axis_states` not defined.

- [ ] **Step 3: Implement**

```python
# add to analytics/indicator_condition.py
from analytics.brief.types import IndicatorState

_AXES: tuple[str, ...] = (
    "ema_stack", "ema_slope", "regime", "bb_squeeze", "bb_pctb",
    "vwap_weekly", "vwap_monthly", "vp_value_area", "pa_char", "monday_range",
)


def axis_states(
    state: IndicatorState, regime_label: str | None, ref_close: float
) -> dict[str, str | None]:
    """IndicatorState -> {axis: state-enum|None}. A missing sub-block -> None
    for that axis only (the trade is excluded from that axis's split, never
    dropped globally)."""
    out: dict[str, str | None] = {a: None for a in _AXES}
    out["regime"] = regime_label
    if state.ema is not None:
        out["ema_stack"] = state.ema.stack
        out["ema_slope"] = state.ema.slope_200
    if state.bb is not None:
        out["bb_squeeze"] = "squeeze" if state.bb.squeeze else "no_squeeze"
        if state.bb.pct_b is not None:
            out["bb_pctb"] = (
                "low" if state.bb.pct_b < 0.2 else "high" if state.bb.pct_b > 0.8 else "mid"
            )
    if state.vwap is not None:
        if state.vwap.weekly_dist_atr is not None:
            out["vwap_weekly"] = "above" if state.vwap.weekly_dist_atr >= 0 else "below"
        if state.vwap.monthly_dist_atr is not None:
            out["vwap_monthly"] = "above" if state.vwap.monthly_dist_atr >= 0 else "below"
    if state.profile is not None:
        if ref_close > state.profile.vah:
            out["vp_value_area"] = "above"
        elif ref_close < state.profile.val:
            out["vp_value_area"] = "below"
        else:
            out["vp_value_area"] = "inside"
    if state.pa is not None:
        out["pa_char"] = state.pa.label
    if state.monday is not None:
        out["monday_range"] = state.monday.state
    return out
```

- [ ] **Step 4: Run to verify it passes**

Run: `PYTHONPATH=. poetry run pytest tests/test_indicator_condition.py -k axis_states -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add analytics/indicator_condition.py tests/test_indicator_condition.py
git commit -m "feat(h8): axis extraction from IndicatorState"
```

---

## Task 3: As-of-entry tagging + the look-ahead mutation guard

**Files:**

- Modify: `analytics/indicator_condition.py`
- Test: `tests/test_indicator_condition.py`

`tag_trades` re-derives each trade's state from bars whose **close ≤ entry_time** (the entry bar's close == entry_time, so it is included; any bar closing later is look-ahead). The mutation test is load-bearing: it must go RED if the guard is loosened to admit a future bar.

- [ ] **Step 1: Write the failing test** (synthetic OHLCV; a future bar flips a state only if the guard leaks)

```python
import numpy as np
import pandas as pd
from analytics.indicator_condition import tag_trades

_DAY = 86_400_000


def _synth_ohlcv(n: int, start: int, tf_ms: int, closes: list[float]) -> pd.DataFrame:
    ot = [start + i * tf_ms for i in range(n)]
    c = np.array(closes, dtype=float)
    return pd.DataFrame({
        "open_time": ot, "open": c, "high": c * 1.01, "low": c * 0.99,
        "close": c, "volume": np.full(n, 1000.0),
    })


def test_tag_trades_is_causal_and_mutation_proof() -> None:
    # A trade at t = close of bar k. Bars after k must not change its state.
    start = 1_700_000_000_000
    closes_up = [100 + i for i in range(40)]            # steady uptrend through k
    d1 = _synth_ohlcv(40, start, _DAY, closes_up)
    h1 = _synth_ohlcv(40 * 24, start, _DAY // 24, [100 + i / 24 for i in range(40 * 24)])
    k = 30
    t = int(d1.open_time.iloc[k])  # entry at bar k's open==close alignment; see note
    entries = pd.DataFrame([{ "symbol": "TST", "tf": "1d", "strategy": "s", "direction": "long", "entry_time": t, "pnl_r": 1.0 }])
    market = {("TST", "1d"): d1, ("TST", "1h"): h1}

    tagged = tag_trades(entries, market)
    base = tagged.iloc[0]["ema_stack"]

    # Mutate a FUTURE bar (k+5) to a wild value; re-tag; state must be unchanged.
    d1_future = d1.copy()
    d1_future.loc[k + 5, ["close", "high", "low", "open"]] = 1e6
    tagged2 = tag_trades(entries, {("TST", "1d"): d1_future, ("TST", "1h"): h1})
    assert tagged2.iloc[0]["ema_stack"] == base  # causal: future bar is invisible
```

> Note for the implementer: entry_time in `backtest_trades` is the entry bar's timestamp. Define "completed as-of t" as `open_time <= t` (the entry bar itself is the last usable bar). The mutation guard is that bars with `open_time > t` are dropped BEFORE `build_indicator_state`. If you instead keep `open_time <= t + tf_ms`, this test must FAIL — verify that by temporarily loosening the slice and watching it go red (then restore).

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=. poetry run pytest tests/test_indicator_condition.py -k tag_trades -v`
Expected: FAIL — `tag_trades` not defined.

- [ ] **Step 3: Implement**

```python
# add to analytics/indicator_condition.py
import pandas as pd

from analytics.backtest.engine import _compute_atr14
from analytics.brief.indicators import build_indicator_state
from analytics.regime import classify_regime  # confirm the exact fn name in analytics/regime.py


def _tf_ms(tf: str) -> int:
    unit = tf[-1]
    n = int(tf[:-1])
    return n * {"m": 60_000, "h": 3_600_000, "d": 86_400_000}[unit]


def tag_trades(
    entries: pd.DataFrame, market_by_pair: dict[tuple[str, str], pd.DataFrame]
) -> pd.DataFrame:
    """Add one column per axis (state as-of entry). Rows whose (symbol,tf) has
    no 1d/1h OHLCV, or where the pre-entry slice is too short for M1, get all-None
    axes (excluded per-axis downstream)."""
    rows: list[dict[str, object]] = []
    for _, tr in entries.iterrows():
        sym = str(tr["symbol"])
        t = int(tr["entry_time"])
        d1 = market_by_pair.get((sym, "1d"))
        h1 = market_by_pair.get((sym, "1h"))
        axes: dict[str, str | None] = {a: None for a in _AXES}
        if d1 is not None and h1 is not None:
            c1d = d1[d1["open_time"] <= t]
            c1h = h1[h1["open_time"] <= t]
            if len(c1d) >= 60 and len(c1h) >= 60:  # M1 min history
                ref_close = float(c1d["close"].iloc[-1])
                atr14 = _compute_atr14(c1d)  # returns a Series aligned to c1d
                atr_last = float(atr14.iloc[-1]) if hasattr(atr14, "iloc") else float(atr14)
                regime_series = classify_regime(c1d)          # pd.Series of labels
                regime_label = str(regime_series.iloc[-1]) if len(regime_series) else None
                state, _notes = build_indicator_state(
                    c1d, c1h, regime_series, ref_close, atr_last, as_of_ms=t
                )
                if state is not None:
                    axes = axis_states(state, regime_label, ref_close)
        rows.append({**tr.to_dict(), **axes})
    return pd.DataFrame(rows)
```

> Implementer: verify `_compute_atr14`'s return shape and `analytics/regime.py`'s public classifier name/signature (it feeds `build_indicator_state`'s `regime_series_1d`; the brief bundle already calls it — copy that call site). If `build_indicator_state` needs `atr14` as a scalar vs Series, match the brief's usage exactly.

- [ ] **Step 4: Run to verify it passes**

Run: `PYTHONPATH=. poetry run pytest tests/test_indicator_condition.py -k tag_trades -v`
Expected: PASS. Then temporarily change the slice to `d1["open_time"] <= t + _tf_ms("1d")`, re-run, confirm the test FAILS (proves the guard is real), then revert.

- [ ] **Step 5: Commit**

```bash
git add analytics/indicator_condition.py tests/test_indicator_condition.py
git commit -m "feat(h8): causal as-of-entry trade tagging (look-ahead mutation-guarded)"
```

---

## Task 4: Cell building + gate (`build_condition_cells`, `evaluate_conditions`)

**Files:**

- Modify: `analytics/indicator_condition.py`
- Test: `tests/test_indicator_condition.py`

Split tagged trades into per-(axis-state × direction) cells, run the two-leg gate. **Read `analytics/audit_guard.py`** for the exact `Cell` / `evaluate_audit_cells` signature and `analytics/research_guards/` for `block_bootstrap_ci`, `deflated_sharpe`/`cscv_pbo`, `min_track_record_length`. Build each `audit_guard` cell with `supp_r` = the with-state R array and `kept_r` = the without-state R array (§7 of the spec).

- [ ] **Step 1: Write the failing test** (a strongly positive with-state slice -> BUILD)

```python
from analytics.indicator_condition import IndicatorConditionConfig, build_condition_cells, evaluate_conditions


def test_evaluate_builds_on_strong_positive_state() -> None:
    rng = np.random.default_rng(0)
    n = 400
    # 'bullish' EMA trades average +0.5R, others average -0.1R (both low-noise).
    with_r = rng.normal(0.5, 0.3, n)
    without_r = rng.normal(-0.1, 0.3, n)
    df = pd.DataFrame({
        "direction": ["long"] * (2 * n),
        "strategy": ["s"] * (2 * n),
        "ema_stack": (["bullish"] * n) + (["bearish"] * n),
        "pnl_r": list(with_r) + list(without_r),
        **{a: ["x"] * (2 * n) for a in ("ema_slope", "regime", "bb_squeeze", "bb_pctb", "vwap_weekly", "vwap_monthly", "vp_value_area", "pa_char", "monday_range")},
    })
    cells = build_condition_cells(df, axes=("ema_stack",))
    verdicts = evaluate_conditions(cells, IndicatorConditionConfig())
    bull = [v for v in verdicts if v.axis == "ema_stack" and v.state == "bullish" and v.direction == "long"]
    assert bull and bull[0].verdict == "BUILD"
    assert bull[0].lift > 0.4
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=. poetry run pytest tests/test_indicator_condition.py -k evaluate -v`
Expected: FAIL — functions not defined.

- [ ] **Step 3: Implement** — the `ConditionVerdict` dataclass, `build_condition_cells` (group by axis/state/direction; the "without" slice is same-direction trades whose value on that axis differs and is not None), and `evaluate_conditions` (call `evaluate_audit_cells` for the absolute leg; a seeded two-sample bootstrap on `mean(with) - mean(without)` for the lift; `deflated_sharpe`/`cscv_pbo` over the per-axis-state trial family; `min_track_record_length` on the with slice; then `_map_verdict`). Show the dataclass and the lift-bootstrap helper explicitly:

```python
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class ConditionVerdict:
    axis: str
    state: str
    direction: str
    verdict: str            # BUILD | AVOID | NO-EDGE | INSUFFICIENT
    n_with: int
    n_without: int
    avg_r_with: float
    avg_r_without: float
    lift: float
    lift_lo: float
    lift_hi: float
    dsr: float | None
    pbo: float | None


def _lift_ci(
    with_r: np.ndarray, without_r: np.ndarray, cfg: IndicatorConditionConfig
) -> tuple[float, float, float]:
    """Seeded two-sample bootstrap CI on mean(with) - mean(without)."""
    rng = np.random.default_rng(cfg.seed)
    diffs = np.empty(cfg.n_boot)
    for i in range(cfg.n_boot):
        a = rng.choice(with_r, size=len(with_r), replace=True)
        b = rng.choice(without_r, size=len(without_r), replace=True)
        diffs[i] = a.mean() - b.mean()
    lift = float(with_r.mean() - without_r.mean())
    lo, hi = np.quantile(diffs, [cfg.alpha / 2, 1 - cfg.alpha / 2])
    return lift, float(lo), float(hi)
```

Then assemble `evaluate_conditions`: build the `audit_guard` cell family (one per axis-state × direction), call `evaluate_audit_cells(...)` once so Holm spans the whole family, and for each returned `CellVerdict` compute `_lift_ci`, the family DSR/PBO, and `_map_verdict(...)`. Below-`min_n` cells are `INSUFFICIENT` (audit_guard already excludes them from the Holm family — do not add them back).

- [ ] **Step 4: Run to verify it passes**

Run: `PYTHONPATH=. poetry run pytest tests/test_indicator_condition.py -k evaluate -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add analytics/indicator_condition.py tests/test_indicator_condition.py
git commit -m "feat(h8): condition-cell building + two-leg de-biased gate"
```

---

## Task 5: Driver + make target

**Files:**

- Create: `tools/indicator_condition_audit.py`
- Modify: `Makefile`

Read `tools/warning_value_audit.py` end-to-end first; this driver is the same skeleton with `evaluate_conditions` in place of `exploratory_by_tf`/warning logic.

- [ ] **Step 1: Implement the driver** — argparse (`--source both|backtest|live`, `--min-n`, `--timeframes`, `--db`, `--out`, `--seed`); `duckdb.connect(db, read_only=True)`; for each source: `_load_entries` → `normalize_backtest`/`normalize_live` → `_load_market` → `tag_trades` → pooled `build_condition_cells(axes=_AXES)` → `evaluate_conditions` → print a verdict table (axis · state · direction · n_with · avg_r_with · lift[lo,hi] · DSR · PBO · verdict) sorted BUILD/AVOID first, then the per-strategy-TYPE diagnostic (call `build_condition_cells` with a `by_strategy_type=True` grain — add that kwarg in Task 4 if you prefer, or group in the driver). Map each trade's `strategy` to its type via `analytics.strategies._registry` (`STRATEGY_REGISTRY[strategy].type` or the equivalent lookup — confirm the field). Read-only; no writes.

- [ ] **Step 2: Add the make target**

```makefile
buibui-indicator-condition-audit:
 PYTHONPATH=. poetry run python tools/indicator_condition_audit.py $(if $(SOURCE),--source $(SOURCE),) $(if $(MIN_N),--min-n $(MIN_N),)
```

The recipe line must begin with a literal **TAB** (Make requires it — the block above shows spaces only because markdownlint rewrites hard tabs; copy the indentation style from an existing `buibui-*` target). Add `buibui-indicator-condition-audit` to the `.PHONY` line.

- [ ] **Step 3: Smoke-run** (needs a populated `analytics.db` with saved backtest runs)

Run: `make buibui-indicator-condition-audit MIN_N=30`
Expected: a verdict table prints; exit 0; no DB writes (`git status` clean, `analytics.db` mtime unchanged).

- [ ] **Step 4: Commit**

```bash
git add tools/indicator_condition_audit.py Makefile
git commit -m "feat(h8): indicator-condition audit driver + make target"
```

---

## Task 6: Full gate + verdict doc

**Files:**

- Create: `docs/audits/2026-07-XX-h8-m1-indicator-conditioning.md` (XX = run date)

- [ ] **Step 1: Definition of Done**

Run: `make lint-py && make typecheck && make test`
Expected: all green. (No `make test-regression` needed — read-only, no golden touched — but run it to confirm goldens are unmoved.)

- [ ] **Step 2: Run the audit for real and record the verdict**

Run: `make buibui-indicator-condition-audit MIN_N=30` (and `--source live` for corroboration).
Write the verdict doc with the plain-English framing (headline → metric → money → backtest-vs-live), the pre-committed verdict table, and the §9 caveats. State BUILD/AVOID cells (if any) and whether conditional-edge = NO still holds.

- [ ] **Step 3: Update SoT + memory**

Note the verdict in `project_conditional_edge_test` / `project_strategy_overhaul_revisit` and the master to-do. If any cell is BUILD, the follow-up is a live gating lever (not a detector).

- [ ] **Step 4: Commit**

```bash
git add docs/audits/2026-07-*-h8-m1-indicator-conditioning.md
git commit -m "docs(h8): M1 indicator-conditioning audit verdict"
```

---

## Self-review notes (author)

- **Spec coverage:** §3 substrate → Task 5 (`_load_entries`/normalize reuse); §4 axes → Task 2 + `_AXES`; §5 granularity → Task 5 pooled + strategy-type; §6 causal tagging → Task 3 (mutation-guarded); §7 gate + inversion → Tasks 1 & 4; §8 modules → Tasks 1–5; §10 deliverable → Task 6. All covered.
- **Inversion guard** is pinned by `test_map_disable_is_never_avoid` (Task 1) and the causal guard by the future-bar mutation (Task 3) — both fail-for-the-right-reason by construction.
- **Verify-before-trusting flags for the implementer** (do not assume; read the file): `analytics/brief/types.py` sub-block field names (Task 2), `analytics/regime.py` classifier name + `_compute_atr14` return shape + `build_indicator_state` atr arg type (Task 3), `analytics/audit_guard.py` `Cell`/`evaluate_audit_cells` signature + `research_guards` fn names (Task 4), `STRATEGY_REGISTRY` type field (Task 5).
