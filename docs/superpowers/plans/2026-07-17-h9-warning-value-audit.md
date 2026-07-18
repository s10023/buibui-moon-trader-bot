# H9 Warning-Value Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A read-only audit answering H9 — do the W1/W2/W5/W6/W7/W8 candle warnings
(currently cosmetic text on Telegram alerts) actually predict trade outcomes? —
via regenerated per-trade warning flags and pre-committed de-biased verdicts.

**Architecture:** A pure lib (`analytics/warning_audit.py`) re-derives each
historical trade's six warning flags from OHLCV using the *same private helpers*
the live alert path uses (`signals/alert_formatter.py` — import, never reimplement),
then evaluates (warning × direction) cells through the existing
`analytics/audit_guard.py` gate (block-bootstrap CI clearing ±bar + Holm haircut,
one family per source). A `tools/` driver loads both substrates
(`backtest_trades` = primary, `signal_alert_outcomes` = corroboration — the ST1
two-substrate pattern from `tools/reference_level_proximity_audit.py`) and renders
a markdown report. No engine, live-path, or schema change of any kind.

**Tech Stack:** Python 3.11, pandas, numpy, duckdb (read-only), pytest.
Reuses: `analytics.audit_guard`, `analytics.store.market_data.get_ohlcv`,
`signals.alert_formatter` helpers.

## Context for the implementer (zero-context brief)

The live signal daemon appends candle-anatomy warnings to alerts
(`signals/alert_formatter.py::_build_candle_warnings`): W1 marubozu, W2 equal
highs/lows, W5 wick-rejection-against-direction, W6 three-consecutive-candles,
W7 doji, W8 inside-bar. These are **display-only** — never persisted, gate
nothing, never tested for predictive value. H9 asks whether any of them should be
promoted to a real gate (warned trades reliably lose), flagged as a *reverse*
indicator (warned trades reliably win), or confirmed cosmetic. Because the flags
were never stored, the audit **regenerates** them from OHLCV at each trade's
signal candle — the same regeneration move `analytics/structural_touch.py` made.

Verdict machinery already exists: `analytics/audit_guard.py::evaluate_audit_cells`
takes `AuditCell(label, supp_r, kept_r)` per cell and returns
`CellVerdict(decision, n_supp, n_kept, supp_avg, kept_avg, ci_lo, ci_hi,
adj_pvalue, n_tests, reasons)` with `decision` ∈ ENABLE / DISABLE / CONCENTRATE /
INSUFFICIENT, sharing ONE Holm family across all tested cells. We treat the
**warned slice as the would-be-suppressed slice of a hypothetical warning gate**,
so audit_guard's semantics map directly (see Task 3).

## Global Constraints

- **Read-only:** every DB touch is `duckdb.connect(str(db), read_only=True)`.
  No writes to `analytics.db`, no schema change, no golden-fixture change.
- **No live-path change:** `signals/alert_formatter.py`, `analytics/signal/`,
  `analytics/backtest/` are NOT modified. Importing their private helpers is the
  accepted repo pattern (`tools/gate_audit.py` imports `_filter_signals_by_adr`).
- **Pure lib:** `analytics/warning_audit.py` must not import duckdb (no DB/IO) —
  mirrors `analytics/structural_touch.py` / `analytics/audit_guard.py`.
- **Pre-committed defaults, frozen before real data is touched:** `bar=0.05`,
  `alpha=0.05`, `min_n=30`, `n_boot=10_000`, `seed=12345`, `window_bars=12`.
  W2's lookback/tolerance stay at the live helpers' defaults (10 bars / 0.0015).
- **Pre-committed verdict taxonomy** (Task 3) — fixed before the tool ever runs
  on the real DB. The two-sample lift CI is a *reported corroboration stamp*,
  never gate-deciding.
- **Out of scope:** the volume-spike / low-volume conviction notes (already
  audited via `tools/gate_audit.py` volume-suppress; `volume_spike_boost` was
  removed as inert in PR #396/#397) and the CME-gap warning. No gate wiring in
  this PR regardless of outcome — a SUPPRESS-CANDIDATE finding routes to a
  follow-up soft-mode gate proposal (F8 pattern), live-OOS gated.
- **Quality gate (Definition of Done):** `make lint-py` ✓, `make typecheck` ✓
  (mypy strict — all functions annotated, tests end `-> None`), `make test`
  green, `make test-regression` goldens unmoved (change is additive/read-only).
- **Markdown:** report + verdict doc must pass `make lint-md` (every fence has a
  language, table delimiters spaced `| --- |`).
- Branch: `feat/h9-warning-value-audit` off `main`. Conventional commits.

## File Structure

- Create `analytics/warning_audit.py` — pure lib: `WARNING_KEYS`,
  `compute_warning_flags`, `tag_trades`, `two_sample_lift_ci`,
  `WarningVerdict`, `evaluate_warning_cells`.
- Create `tools/warning_value_audit.py` — driver: substrate loaders
  (`normalize_live` / `normalize_backtest` pure + `_load_entries` /
  `_load_market` DB front door), `exploratory_by_tf`, `format_report`,
  argparse `main`.
- Create `tests/test_warning_audit.py` — lib tests incl. the live-parity test.
- Create `tests/test_warning_value_audit.py` — driver pure-function tests.
- Modify `Makefile` — `buibui-warning-value-audit` target.
- Modify `CLAUDE.md` — one `tools/` bullet (Task 5).
- Create `docs/audits/2026-07-17-h9-warning-value.md` — Task 6, from the real
  run, operator-review gated.

---

### Task 0: Branch

- [ ] **Step 1: Create the branch**

```bash
cd /home/kng/repo/buibui-moon-trader-bot
git checkout main && git pull && git checkout -b feat/h9-warning-value-audit
```

---

### Task 1: `compute_warning_flags` + live-parity test

**Files:**

- Create: `analytics/warning_audit.py`
- Create: `tests/test_warning_audit.py`

**Interfaces:**

- Consumes: `signals.alert_formatter._is_marubozu(o, h, lo, c) -> bool`,
  `_is_doji(o, h, lo, c) -> bool`, `_is_inside_bar(h, lo, prev_h, prev_l) -> bool`,
  `_wick_rejection_against(o, h, lo, c, direction) -> bool`,
  `_has_equal_levels(df, price, direction, lookback=10, tol_pct=0.0015) -> bool`,
  `_has_consecutive_candles(df, direction, n=3) -> bool`.
- Produces: `WARNING_KEYS: tuple[str, ...]` (6 keys) and
  `compute_warning_flags(window: pd.DataFrame, direction: str, price: float | None = None) -> dict[str, bool] | None`
  — used by Task 2's `tag_trades` and every test.

**Precedence rules that MUST mirror `_build_candle_warnings`** (this is the whole
point of the parity test): W7 doji takes precedence over W1 marubozu (`elif`);
W5 wick-rejection is skipped on a doji; W2/W6/W8 are unconditional; a window
with fewer than 2 rows produces **no** candle warnings on the live path →
return `None`. `price` defaults to the signal candle's close — exactly what the
live path passes (`SignalEvent.price` is documented as the signal-candle close).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_warning_audit.py`:

```python
"""Tests for analytics/warning_audit.py (H9 warning-value audit lib)."""

from __future__ import annotations

import pandas as pd

from analytics.signal.types import SignalEvent
from analytics.warning_audit import WARNING_KEYS, compute_warning_flags
from signals.alert_formatter import _build_candle_warnings


def _mk_df(rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    """OHLCV frame from (open, high, low, close) tuples; 1h spacing."""
    return pd.DataFrame(
        {
            "open_time": [3_600_000 * i for i in range(len(rows))],
            "open": [r[0] for r in rows],
            "high": [r[1] for r in rows],
            "low": [r[2] for r in rows],
            "close": [r[3] for r in rows],
            "volume": [100.0] * len(rows),
        }
    )


# (name, rows, {direction: {key: expected}}) — unlisted keys expected False.
_CASES: list[
    tuple[str, list[tuple[float, float, float, float]], dict[str, dict[str, bool]]]
] = [
    (
        "plain",
        [(100.0, 101.0, 99.0, 100.5), (100.5, 103.0, 100.0, 102.0)],
        {"long": {}, "short": {}},
    ),
    (
        "marubozu",
        [(100.0, 101.0, 99.0, 100.5), (100.0, 110.5, 99.8, 110.0)],
        {"long": {"w1_marubozu": True}, "short": {"w1_marubozu": True}},
    ),
    (
        # doji with a huge upper wick: raw wick-rejection is True for long but
        # MUST be suppressed because the candle is a doji (live precedence).
        "doji_suppresses_w5",
        [(100.0, 101.0, 99.0, 100.5), (100.0, 110.0, 99.5, 100.4)],
        {"long": {"w7_doji": True}, "short": {"w7_doji": True}},
    ),
    (
        "inside_bar",
        [(100.0, 110.0, 90.0, 105.0), (104.0, 106.0, 95.0, 96.0)],
        {"long": {"w8_inside_bar": True}, "short": {"w8_inside_bar": True}},
    ),
    (
        "wick_reject_long_only",
        [(100.0, 101.0, 99.0, 100.5), (100.0, 110.0, 99.5, 102.0)],
        {"long": {"w5_wick_rejection": True}, "short": {}},
    ),
    (
        "equal_lows_long_only",
        [
            (100.0, 101.0, 95.0, 100.2),
            (100.2, 100.6, 95.1, 100.4),
            (100.4, 100.8, 99.6, 100.0),
        ],
        {"long": {"w2_equal_levels": True}, "short": {}},
    ),
    (
        "three_greens_long_only",
        [
            (100.0, 101.5, 99.0, 101.0),
            (101.0, 102.5, 100.0, 102.0),
            (102.0, 103.5, 101.2, 103.0),
        ],
        {"long": {"w6_consecutive": True}, "short": {}},
    ),
]

# Note substrings in _build_candle_warnings output → flag key.
_NOTE_MARKERS = {
    "w1_marubozu": "Wickless candle",
    "w2_equal_levels": "liquidity",
    "w5_wick_rejection": "wick rejection",
    "w6_consecutive": "in a row",
    "w7_doji": "Doji signal candle",
    "w8_inside_bar": "inside prior range",
}


class TestComputeWarningFlags:
    def test_returns_none_below_two_rows(self) -> None:
        df = _mk_df([(100.0, 101.0, 99.0, 100.5)])
        assert compute_warning_flags(df, "long") is None

    def test_all_keys_present(self) -> None:
        df = _mk_df(_CASES[0][1])
        flags = compute_warning_flags(df, "long")
        assert flags is not None
        assert set(flags) == set(WARNING_KEYS)

    def test_expected_flags_per_case(self) -> None:
        for name, rows, per_dir in _CASES:
            df = _mk_df(rows)
            for direction, expected in per_dir.items():
                flags = compute_warning_flags(df, direction)
                assert flags is not None, name
                for key in WARNING_KEYS:
                    want = expected.get(key, False)
                    assert flags[key] is want, f"{name}/{direction}/{key}"


class TestLiveParity:
    """compute_warning_flags must agree with _build_candle_warnings exactly."""

    def test_parity_across_case_battery(self) -> None:
        for name, rows, per_dir in _CASES:
            df = _mk_df(rows)
            for direction in per_dir:
                event = SignalEvent(
                    symbol="BTCUSDT",
                    timeframe="1h",
                    strategy="wick_fills",
                    direction=direction,
                    reason="parity-test",
                    open_time=int(df.iloc[-1]["open_time"]),
                    price=float(df.iloc[-1]["close"]),
                )
                notes = _build_candle_warnings([event], df)
                flags = compute_warning_flags(df, direction)
                assert flags is not None, name
                for key, marker in _NOTE_MARKERS.items():
                    fired_live = any(marker in n for n in notes)
                    assert flags[key] is fired_live, f"{name}/{direction}/{key}"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
PYTHONPATH=. poetry run pytest tests/test_warning_audit.py -v
```

Expected: FAIL / collection error — `analytics.warning_audit` does not exist.

- [ ] **Step 3: Write the lib**

Create `analytics/warning_audit.py`:

```python
"""H9 warning-value audit lib (pure — no DB/IO).

The live alert path computes six candle-anatomy warnings at fire time
(``signals.alert_formatter._build_candle_warnings``) but never persists the
flags, so this module re-derives them historically from OHLCV using the SAME
helpers — import, never reimplement — then evaluates whether any warning
predicts avg_r via :mod:`analytics.audit_guard` (block-bootstrap CI clearing
±bar + Holm haircut, one family per audited source).

Scope guard: the volume-spike / low-volume conviction notes are excluded
(already audited via ``tools/gate_audit.py``); CME-gap warnings are out of
scope. See ``docs/superpowers/plans/2026-07-17-h9-warning-value-audit.md``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
import pandas as pd

from analytics import audit_guard
from signals.alert_formatter import (
    _has_consecutive_candles,
    _has_equal_levels,
    _is_doji,
    _is_inside_bar,
    _is_marubozu,
    _wick_rejection_against,
)

WARNING_KEYS: tuple[str, ...] = (
    "w1_marubozu",
    "w2_equal_levels",
    "w5_wick_rejection",
    "w6_consecutive",
    "w7_doji",
    "w8_inside_bar",
)

VERDICT_SUPPRESS = "SUPPRESS-CANDIDATE"
VERDICT_REVERSE = "REVERSE"
VERDICT_COSMETIC = "COSMETIC"
VERDICT_INSUFFICIENT = "INSUFFICIENT"


def compute_warning_flags(
    window: pd.DataFrame,
    direction: str,
    price: float | None = None,
) -> dict[str, bool] | None:
    """Re-derive the six warning flags for the window's LAST candle.

    ``window`` holds OHLCV rows ending AT the signal candle (last row = signal
    candle). Returns ``None`` when the window has < 2 rows — the live path
    emits no candle warnings there either. ``price`` (for W2's equal-levels
    scan) defaults to the signal-candle close, the exact value the live path
    passes (``SignalEvent.price``). Precedence mirrors
    ``_build_candle_warnings``: doji wins over marubozu; wick-rejection is
    skipped on a doji.
    """
    if len(window) < 2:
        return None
    last = window.iloc[-1]
    o = float(last["open"])
    h = float(last["high"])
    lo = float(last["low"])
    c = float(last["close"])
    prev = window.iloc[-2]
    prev_h = float(prev["high"])
    prev_l = float(prev["low"])
    p = c if price is None else price
    doji = _is_doji(o, h, lo, c)
    return {
        "w1_marubozu": (not doji) and _is_marubozu(o, h, lo, c),
        "w2_equal_levels": _has_equal_levels(window, p, direction),
        "w5_wick_rejection": (not doji)
        and _wick_rejection_against(o, h, lo, c, direction),
        "w6_consecutive": _has_consecutive_candles(window, direction),
        "w7_doji": doji,
        "w8_inside_bar": _is_inside_bar(h, lo, prev_h, prev_l),
    }
```

(The remaining lib functions land in Tasks 2–3; this file grows in place.)

- [ ] **Step 4: Run tests to verify they pass**

```bash
PYTHONPATH=. poetry run pytest tests/test_warning_audit.py -v
```

Expected: PASS (4 tests).

- [ ] **Step 5: Lint + typecheck + commit**

```bash
make lint-py && make typecheck
git add analytics/warning_audit.py tests/test_warning_audit.py
git commit -m "feat(h9): warning-flag regeneration with live-parity guarantee"
```

---

### Task 2: `two_sample_lift_ci` + `tag_trades`

**Files:**

- Modify: `analytics/warning_audit.py` (append)
- Modify: `tests/test_warning_audit.py` (append)

**Interfaces:**

- Consumes: `compute_warning_flags` (Task 1).
- Produces:
  `two_sample_lift_ci(warned, clean, *, alpha=0.05, n_boot=10_000, seed=12345) -> tuple[float, float]`
  and
  `tag_trades(entries: pd.DataFrame, ohlcv_by_key: Mapping[tuple[str, str], pd.DataFrame], *, window_bars: int = 12) -> tuple[pd.DataFrame, int]`.
  `entries` columns: `symbol, tf, strategy, direction, ts_ms, r` (`ts_ms` = signal
  candle `open_time`). Returned frame = kept entries + one bool column per
  `WARNING_KEYS` entry; the int is the dropped-row count.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_warning_audit.py`)

```python
import numpy as np

from analytics.warning_audit import tag_trades, two_sample_lift_ci


class TestTwoSampleLiftCi:
    def test_separated_cohorts_ci_excludes_zero(self) -> None:
        rng = np.random.default_rng(7)
        warned = rng.normal(1.0, 0.1, 50)
        clean = rng.normal(0.0, 0.1, 50)
        lo, hi = two_sample_lift_ci(warned, clean, n_boot=500)
        assert lo > 0.5
        assert hi > lo

    def test_tiny_cohort_returns_nan(self) -> None:
        lo, hi = two_sample_lift_ci([1.0], [0.0, 0.1], n_boot=100)
        assert np.isnan(lo) and np.isnan(hi)


class TestTagTrades:
    def _entries(self) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "symbol": ["BTCUSDT"] * 3,
                "tf": ["1h"] * 3,
                "strategy": ["wick_fills"] * 3,
                "direction": ["long"] * 3,
                # candle 3 (plenty of history), candle 0 (< 2-bar window),
                # and a timestamp absent from the OHLCV frame.
                "ts_ms": [3 * 3_600_000, 0, 999_999_999],
                "r": [0.5, -1.0, 1.0],
            }
        )

    def test_tags_matching_and_drops_unmatched(self) -> None:
        ohlcv = _mk_df(
            [
                (100.0, 101.0, 99.0, 100.5),
                (100.5, 102.0, 100.0, 101.5),
                (101.5, 103.0, 101.0, 102.5),
                (102.5, 104.0, 102.0, 103.5),  # 3 greens incl. this one
            ]
        )
        tagged, dropped = tag_trades(self._entries(), {("BTCUSDT", "1h"): ohlcv})
        assert dropped == 2
        assert len(tagged) == 1
        assert bool(tagged.iloc[0]["w6_consecutive"]) is True
        assert set(WARNING_KEYS) <= set(tagged.columns)

    def test_missing_symbol_frame_drops_all(self) -> None:
        tagged, dropped = tag_trades(self._entries(), {})
        assert dropped == 3
        assert tagged.empty
        assert set(WARNING_KEYS) <= set(tagged.columns)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
PYTHONPATH=. poetry run pytest tests/test_warning_audit.py -v
```

Expected: FAIL — `two_sample_lift_ci` / `tag_trades` not defined.

- [ ] **Step 3: Implement** (append to `analytics/warning_audit.py`)

```python
def two_sample_lift_ci(
    warned: Sequence[float] | npt.NDArray[np.float64],
    clean: Sequence[float] | npt.NDArray[np.float64],
    *,
    alpha: float = 0.05,
    n_boot: int = 10_000,
    seed: int | None = 12345,
) -> tuple[float, float]:
    """Seeded two-sample bootstrap CI for ``mean(warned) - mean(clean)``.

    Independent i.i.d. resamples of each cohort; percentile CI of the
    difference of means. ``(nan, nan)`` when either cohort has < 2 rows.
    Report-only corroboration — never gate-deciding (see plan).
    """
    a = np.asarray(warned, dtype=np.float64)
    b = np.asarray(clean, dtype=np.float64)
    if a.shape[0] < 2 or b.shape[0] < 2:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    diffs = np.empty(n_boot, dtype=np.float64)
    for i in range(n_boot):
        ra = rng.integers(0, a.shape[0], a.shape[0])
        rb = rng.integers(0, b.shape[0], b.shape[0])
        diffs[i] = a[ra].mean() - b[rb].mean()
    lo = float(np.quantile(diffs, alpha / 2.0))
    hi = float(np.quantile(diffs, 1.0 - alpha / 2.0))
    return (lo, hi)


def tag_trades(
    entries: pd.DataFrame,
    ohlcv_by_key: Mapping[tuple[str, str], pd.DataFrame],
    *,
    window_bars: int = 12,
) -> tuple[pd.DataFrame, int]:
    """Tag every entry row with its six warning flags.

    ``entries`` columns: symbol, tf, strategy, direction, ts_ms (signal candle
    ``open_time``), r. Rows whose signal candle is absent from the OHLCV frame
    or whose window has < 2 bars are dropped and counted. ``window_bars=12``
    covers every helper's lookback (W2 scans 10 prior bars).
    """
    keep_idx: list[int] = []
    flag_rows: list[dict[str, bool]] = []
    dropped = 0
    pairs = entries[["symbol", "tf"]].drop_duplicates()
    for symbol, tf in zip(pairs["symbol"], pairs["tf"], strict=True):
        sub = entries[(entries["symbol"] == symbol) & (entries["tf"] == tf)]
        df = ohlcv_by_key.get((str(symbol), str(tf)))
        if df is None or df.empty:
            dropped += len(sub)
            continue
        times = df["open_time"].to_numpy(dtype=np.int64)
        for idx, ts, direction in zip(
            sub.index, sub["ts_ms"], sub["direction"], strict=True
        ):
            i = int(np.searchsorted(times, int(ts)))
            if i >= len(times) or int(times[i]) != int(ts):
                dropped += 1
                continue
            window = df.iloc[max(0, i - window_bars + 1) : i + 1]
            flags = compute_warning_flags(window, str(direction))
            if flags is None:
                dropped += 1
                continue
            keep_idx.append(int(idx))
            flag_rows.append(flags)
    if not keep_idx:
        empty = entries.iloc[0:0].copy()
        for key in WARNING_KEYS:
            empty[key] = pd.Series(dtype=bool)
        return empty, dropped
    tagged = entries.loc[keep_idx].reset_index(drop=True)
    flags_df = pd.DataFrame(flag_rows)[list(WARNING_KEYS)]
    return pd.concat([tagged, flags_df], axis=1), dropped
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
PYTHONPATH=. poetry run pytest tests/test_warning_audit.py -v
```

Expected: PASS (8 tests).

- [ ] **Step 5: Lint + typecheck + commit**

```bash
make lint-py && make typecheck
git add analytics/warning_audit.py tests/test_warning_audit.py
git commit -m "feat(h9): trade tagging + two-sample lift CI"
```

---

### Task 3: `WarningVerdict` + `evaluate_warning_cells` (the pre-committed gate)

**Files:**

- Modify: `analytics/warning_audit.py` (append)
- Modify: `tests/test_warning_audit.py` (append)

**Interfaces:**

- Consumes: `audit_guard.AuditCell(label, supp_r, kept_r)`,
  `audit_guard.evaluate_audit_cells(cells, *, bar, alpha, min_n, n_boot, seed)`,
  `audit_guard.DECISION_ENABLE / DECISION_DISABLE / DECISION_CONCENTRATE`,
  `two_sample_lift_ci` (Task 2).
- Produces: frozen dataclass `WarningVerdict` and
  `evaluate_warning_cells(tagged, *, min_n=30, bar=0.05, alpha=0.05, n_boot=10_000, seed=12345) -> list[WarningVerdict]`
  — 12 verdicts (6 warnings × long/short), consumed by the Task 4 driver.

**Pre-committed taxonomy (fixed here, before any real-data run).** The warned
slice is the would-be-suppressed slice of a hypothetical warning gate, so
audit_guard maps directly:

| audit_guard raw decision | condition (from audit_guard) | H9 verdict |
| --- | --- | --- |
| ENABLE | warned mean CI ≤ −bar AND Holm p < alpha | SUPPRESS-CANDIDATE |
| DISABLE | warned mean CI ≥ +bar AND Holm p < alpha | REVERSE |
| CONCENTRATE | warned reliably positive but clean better by ≥ bar | COSMETIC (detail kept in `raw_decision`) |
| INSUFFICIENT, both cohorts ≥ min_n | tested, gates not cleared | COSMETIC |
| INSUFFICIENT otherwise | under-powered | INSUFFICIENT |

- [ ] **Step 1: Write the failing tests** (append to `tests/test_warning_audit.py`)

```python
from analytics.warning_audit import WarningVerdict, evaluate_warning_cells


def _block(
    n: int, r_mean: float, flag: str | None, rng: np.random.Generator
) -> pd.DataFrame:
    df = pd.DataFrame(
        {"direction": ["long"] * n, "r": rng.normal(r_mean, 0.3, n)}
    )
    for key in WARNING_KEYS:
        df[key] = key == flag
    return df


class TestEvaluateWarningCells:
    def test_taxonomy_mapping(self) -> None:
        rng = np.random.default_rng(7)
        tagged = pd.concat(
            [
                _block(80, -0.8, "w7_doji", rng),  # reliable loser
                _block(80, 0.8, "w1_marubozu", rng),  # reliable winner
                _block(80, 0.0, "w6_consecutive", rng),  # powered, no effect
                _block(5, -1.0, "w2_equal_levels", rng),  # under-powered
                _block(300, 0.05, None, rng),  # clean bulk
            ]
        ).reset_index(drop=True)
        verdicts = evaluate_warning_cells(tagged, n_boot=500)
        assert len(verdicts) == 12
        by = {(v.warning, v.direction): v for v in verdicts}
        assert by[("w7_doji", "long")].verdict == "SUPPRESS-CANDIDATE"
        assert by[("w1_marubozu", "long")].verdict == "REVERSE"
        assert by[("w6_consecutive", "long")].verdict == "COSMETIC"
        assert by[("w2_equal_levels", "long")].verdict == "INSUFFICIENT"
        assert by[("w7_doji", "short")].verdict == "INSUFFICIENT"
        # lift stamp populated on a tested cell
        assert by[("w7_doji", "long")].lift_hi < 0.0

    def test_concentrate_maps_to_cosmetic_with_raw_kept(self) -> None:
        rng = np.random.default_rng(11)
        tagged = pd.concat(
            [
                _block(80, 0.3, "w8_inside_bar", rng),
                _block(300, 0.9, None, rng),
            ]
        ).reset_index(drop=True)
        verdicts = evaluate_warning_cells(tagged, n_boot=500)
        by = {(v.warning, v.direction): v for v in verdicts}
        v = by[("w8_inside_bar", "long")]
        assert v.raw_decision == "CONCENTRATE"
        assert v.verdict == "COSMETIC"

    def test_single_holm_family(self) -> None:
        rng = np.random.default_rng(3)
        tagged = pd.concat(
            [
                _block(80, -0.8, "w7_doji", rng),
                _block(80, 0.8, "w1_marubozu", rng),
                _block(300, 0.05, None, rng),
            ]
        ).reset_index(drop=True)
        verdicts = evaluate_warning_cells(tagged, n_boot=500)
        tested = [v for v in verdicts if v.adj_pvalue is not None]
        # every tested cell reports the same family size
        assert len({v.n_tests for v in tested}) == 1
```

Note: `WarningVerdict` must carry `n_tests` for the last assertion — include it
in the dataclass (copied from `CellVerdict.n_tests`).

- [ ] **Step 2: Run tests to verify they fail**

```bash
PYTHONPATH=. poetry run pytest tests/test_warning_audit.py -v
```

Expected: FAIL — `evaluate_warning_cells` not defined.

- [ ] **Step 3: Implement** (append to `analytics/warning_audit.py`)

```python
@dataclass(frozen=True)
class WarningVerdict:
    """Pre-committed H9 verdict for one (warning × direction) cell."""

    warning: str
    direction: str
    n_warned: int
    n_clean: int
    avg_warned: float | None
    avg_clean: float | None
    ci_lo: float | None
    ci_hi: float | None
    adj_pvalue: float | None
    n_tests: int
    lift_lo: float
    lift_hi: float
    raw_decision: str  # audit_guard ENABLE/DISABLE/CONCENTRATE/INSUFFICIENT
    verdict: str  # SUPPRESS-CANDIDATE / REVERSE / COSMETIC / INSUFFICIENT
    reasons: list[str]


def evaluate_warning_cells(
    tagged: pd.DataFrame,
    *,
    min_n: int = 30,
    bar: float = 0.05,
    alpha: float = 0.05,
    n_boot: int = 10_000,
    seed: int | None = 12345,
) -> list[WarningVerdict]:
    """Verdict per (warning × direction) — ONE Holm family per call.

    The warned slice is treated as the would-be-suppressed slice of a
    hypothetical warning gate, so :mod:`analytics.audit_guard` semantics map
    directly: ENABLE (warned reliably ≤ −bar) → SUPPRESS-CANDIDATE; DISABLE
    (warned reliably ≥ +bar) → REVERSE; CONCENTRATE and tested-but-not-clearing
    (both cohorts ≥ min_n) → COSMETIC; otherwise INSUFFICIENT. The two-sample
    lift CI is a reported corroboration stamp, never gate-deciding.
    """
    specs = [(w, d) for w in WARNING_KEYS for d in ("long", "short")]
    warned_arrays: list[npt.NDArray[np.float64]] = []
    clean_arrays: list[npt.NDArray[np.float64]] = []
    for w, d in specs:
        sub = tagged[tagged["direction"] == d]
        warned_arrays.append(sub.loc[sub[w], "r"].to_numpy(dtype=np.float64))
        clean_arrays.append(sub.loc[~sub[w], "r"].to_numpy(dtype=np.float64))
    cells = [
        audit_guard.AuditCell(
            label=f"{w}/{d}",
            supp_r=warned_arrays[i].tolist(),
            kept_r=clean_arrays[i].tolist(),
        )
        for i, (w, d) in enumerate(specs)
    ]
    cell_verdicts = audit_guard.evaluate_audit_cells(
        cells, bar=bar, alpha=alpha, min_n=min_n, n_boot=n_boot, seed=seed
    )
    out: list[WarningVerdict] = []
    for i, ((w, d), cv) in enumerate(zip(specs, cell_verdicts, strict=True)):
        lift_lo, lift_hi = two_sample_lift_ci(
            warned_arrays[i], clean_arrays[i], alpha=alpha, n_boot=n_boot, seed=seed
        )
        if cv.decision == audit_guard.DECISION_ENABLE:
            verdict = VERDICT_SUPPRESS
        elif cv.decision == audit_guard.DECISION_DISABLE:
            verdict = VERDICT_REVERSE
        elif cv.decision == audit_guard.DECISION_CONCENTRATE:
            verdict = VERDICT_COSMETIC
        elif cv.n_supp >= min_n and cv.n_kept >= min_n:
            verdict = VERDICT_COSMETIC
        else:
            verdict = VERDICT_INSUFFICIENT
        out.append(
            WarningVerdict(
                warning=w,
                direction=d,
                n_warned=cv.n_supp,
                n_clean=cv.n_kept,
                avg_warned=cv.supp_avg,
                avg_clean=cv.kept_avg,
                ci_lo=cv.ci_lo,
                ci_hi=cv.ci_hi,
                adj_pvalue=cv.adj_pvalue,
                n_tests=cv.n_tests,
                lift_lo=lift_lo,
                lift_hi=lift_hi,
                raw_decision=cv.decision,
                verdict=verdict,
                reasons=list(cv.reasons),
            )
        )
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
PYTHONPATH=. poetry run pytest tests/test_warning_audit.py -v
```

Expected: PASS (11 tests).

- [ ] **Step 5: Lint + typecheck + commit**

```bash
make lint-py && make typecheck
git add analytics/warning_audit.py tests/test_warning_audit.py
git commit -m "feat(h9): pre-committed warning-cell verdict gate over audit_guard"
```

---

### Task 4: Driver `tools/warning_value_audit.py`

**Files:**

- Create: `tools/warning_value_audit.py`
- Create: `tests/test_warning_value_audit.py`

**Interfaces:**

- Consumes: `analytics.warning_audit` (`WARNING_KEYS`, `WarningVerdict`,
  `evaluate_warning_cells`, `tag_trades`), `analytics.store.DEFAULT_DB_PATH`,
  `analytics.store.market_data.get_ohlcv(conn, symbol, tf, start_ms, end_ms)`.
- Produces (pure, tested): `normalize_live(df) -> pd.DataFrame`,
  `normalize_backtest(df) -> pd.DataFrame` (with cross-run dedup),
  `exploratory_by_tf(tagged) -> pd.DataFrame`, `format_report(results, ...) -> str`.
  CLI: `PYTHONPATH=. poetry run python tools/warning_value_audit.py
  [--db PATH] [--source live|backtest|both] [--since-days N] [--min-n 30]
  [--bar 0.05] [--alpha 0.05] [--n-boot 10000] [--seed 12345]
  [--window-bars 12] [--out PATH]`.

**Substrate notes (both statistics are net of costs — comparable):**
`backtest_trades.pnl_r` deducts fee + slippage + funding (P0b);
`signal_alert_outcomes.outcome_r` is resolved net of the same costs (P0b PR-3).
`backtest_trades` may hold the same signal under multiple saved runs →
`normalize_backtest` dedups on (symbol, tf, strategy, direction, ts_ms) keeping
the lexicographically-latest `run_id` (deterministic; an improvement over the
ST1 loader, note it in the module docstring).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_warning_value_audit.py`:

```python
"""Tests for the pure parts of tools/warning_value_audit.py."""

from __future__ import annotations

import pandas as pd

from analytics.warning_audit import WARNING_KEYS, WarningVerdict
from tools.warning_value_audit import (
    exploratory_by_tf,
    format_report,
    normalize_backtest,
    normalize_live,
)


def _verdict(warning: str, direction: str, verdict: str) -> WarningVerdict:
    return WarningVerdict(
        warning=warning,
        direction=direction,
        n_warned=100,
        n_clean=400,
        avg_warned=-0.2,
        avg_clean=0.1,
        ci_lo=-0.3,
        ci_hi=-0.1,
        adj_pvalue=0.01,
        n_tests=8,
        lift_lo=-0.4,
        lift_hi=-0.2,
        raw_decision="ENABLE",
        verdict=verdict,
        reasons=[],
    )


class TestNormalizers:
    def test_normalize_live_shape(self) -> None:
        raw = pd.DataFrame(
            {
                "symbol": ["BTCUSDT", "ETHUSDT"],
                "tf": ["1h", "4h"],
                "strategy": ["fvg", "bos"],
                "direction": ["long", "short"],
                "candle_ts_ms": [1000, None],
                "outcome_r": [0.5, 1.0],
            }
        )
        out = normalize_live(raw)
        assert list(out.columns) == [
            "symbol", "tf", "strategy", "direction", "ts_ms", "r",
        ]
        assert len(out) == 1  # null candle_ts_ms dropped

    def test_normalize_backtest_dedups_across_runs(self) -> None:
        raw = pd.DataFrame(
            {
                "run_id": ["run_a", "run_b", "run_a"],
                "symbol": ["BTCUSDT"] * 3,
                "timeframe": ["1h"] * 3,
                "strategy": ["fvg"] * 3,
                "direction": ["long"] * 3,
                "signal_time": [1000, 1000, 2000],
                "pnl_r": [0.5, 0.9, -0.2],
            }
        )
        out = normalize_backtest(raw)
        assert len(out) == 2  # duplicate signal collapsed
        kept = out[out["ts_ms"] == 1000].iloc[0]
        assert kept["r"] == 0.9  # latest run_id wins
        assert "run_id" not in out.columns


class TestExploratory:
    def test_by_tf_rows(self) -> None:
        tagged = pd.DataFrame(
            {
                "symbol": ["BTCUSDT"] * 4,
                "tf": ["1h", "1h", "4h", "4h"],
                "strategy": ["fvg"] * 4,
                "direction": ["long"] * 4,
                "ts_ms": [1, 2, 3, 4],
                "r": [1.0, -1.0, 0.5, 0.5],
            }
        )
        for key in WARNING_KEYS:
            tagged[key] = False
        tagged.loc[0, "w7_doji"] = True
        out = exploratory_by_tf(tagged)
        row = out[(out["warning"] == "w7_doji") & (out["tf"] == "1h")].iloc[0]
        assert row["n_warned"] == 1
        assert row["avg_warned"] == 1.0
        assert row["avg_clean"] == -1.0


class TestFormatReport:
    def test_headline_and_tables(self) -> None:
        verdicts = [
            _verdict("w7_doji", "long", "SUPPRESS-CANDIDATE"),
            _verdict("w1_marubozu", "short", "COSMETIC"),
        ]
        expl = pd.DataFrame(
            [
                {
                    "warning": "w7_doji", "direction": "long", "tf": "1h",
                    "n_warned": 10, "avg_warned": -0.2,
                    "n_clean": 40, "avg_clean": 0.1, "lift": -0.3,
                }
            ]
        )
        report = format_report(
            {"backtest": (verdicts, expl, 500, 12)},
            min_n=30, bar=0.05, alpha=0.05, n_boot=100, seed=1,
        )
        assert "SUPPRESS-CANDIDATE: w7_doji/long" in report
        assert "| --- |" in report  # markdownlint-conformant delimiters
        assert "500 tagged" in report
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
PYTHONPATH=. poetry run pytest tests/test_warning_value_audit.py -v
```

Expected: FAIL / collection error — `tools.warning_value_audit` does not exist.

- [ ] **Step 3: Write the driver**

Create `tools/warning_value_audit.py`:

```python
#!/usr/bin/env python
"""H9 warning-value audit — do the W1–W8 candle warnings predict avg_r?

Live alerts append candle-anatomy warnings (W1 marubozu, W2 equal levels,
W5 wick rejection, W6 consecutive candles, W7 doji, W8 inside bar) that are
never persisted and gate nothing. This tool regenerates each historical
trade's flags from OHLCV via :mod:`analytics.warning_audit` (same helpers as
the live path) and emits a pre-committed SUPPRESS-CANDIDATE / REVERSE /
COSMETIC / INSUFFICIENT verdict per (warning × direction) via
:mod:`analytics.audit_guard` (block-bootstrap CI clearing ±bar + Holm
haircut, one family per source).

Substrate roles (pre-committed): ``backtest_trades`` = primary (verdicts
gate); ``signal_alert_outcomes`` = corroboration only. Unlike the ST1 loader
this one dedups backtest trades across saved runs on
(symbol, tf, strategy, direction, signal_time), keeping the
lexicographically-latest run_id. Read-only; no engine/live change.

Run: ``PYTHONPATH=. poetry run python tools/warning_value_audit.py``
(wrapped by ``make buibui-warning-value-audit``).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from analytics.store import DEFAULT_DB_PATH  # noqa: E402
from analytics.store.market_data import get_ohlcv  # noqa: E402
from analytics.warning_audit import (  # noqa: E402
    WARNING_KEYS,
    WarningVerdict,
    evaluate_warning_cells,
    tag_trades,
)

DEFAULT_OUT = REPO_ROOT / "docs" / "audits" / "2026-07-17-h9-warning-value.md"

_TF_MS = {
    "15m": 15 * 60_000,
    "1h": 60 * 60_000,
    "4h": 4 * 60 * 60_000,
    "1d": 24 * 60 * 60_000,
    "1w": 7 * 24 * 60 * 60_000,
}

SourceResult = tuple[list[WarningVerdict], pd.DataFrame, int, int]


def _tf_ms(tf: str) -> int:
    try:
        return _TF_MS[tf]
    except KeyError as exc:
        raise ValueError(f"unknown timeframe: {tf}") from exc


# --------------------------------------------------------------------------- #
# source normalization (pure)                                                  #
# --------------------------------------------------------------------------- #


def normalize_live(df: pd.DataFrame) -> pd.DataFrame:
    """``signal_alert_outcomes`` rows → the common entry frame."""
    out = pd.DataFrame(
        {
            "symbol": df["symbol"],
            "tf": df["tf"],
            "strategy": df["strategy"],
            "direction": df["direction"],
            "ts_ms": df["candle_ts_ms"],
            "r": df["outcome_r"],
        }
    )
    return out.dropna(subset=["ts_ms", "r"]).reset_index(drop=True)


def normalize_backtest(df: pd.DataFrame) -> pd.DataFrame:
    """``backtest_trades`` rows → the common entry frame, deduped across runs.

    The same signal can appear under multiple saved runs (config refreshes,
    sweeps). Dedup on (symbol, tf, strategy, direction, ts_ms) keeping the
    lexicographically-latest ``run_id`` — deterministic, one row per signal.
    """
    out = pd.DataFrame(
        {
            "run_id": df["run_id"],
            "symbol": df["symbol"],
            "tf": df["timeframe"],
            "strategy": df["strategy"],
            "direction": df["direction"],
            "ts_ms": df["signal_time"],
            "r": df["pnl_r"],
        }
    ).dropna(subset=["ts_ms", "r"])
    out = out.sort_values("run_id", kind="stable").drop_duplicates(
        subset=["symbol", "tf", "strategy", "direction", "ts_ms"], keep="last"
    )
    return out.drop(columns=["run_id"]).reset_index(drop=True)


# --------------------------------------------------------------------------- #
# DB front door (read-only)                                                    #
# --------------------------------------------------------------------------- #


def _load_entries(db: Path, src: str, since_ms: int | None) -> pd.DataFrame:
    with duckdb.connect(str(db), read_only=True) as conn:
        if src == "live":
            q = (
                "SELECT symbol, tf, strategy, direction, candle_ts_ms, outcome_r "
                "FROM signal_alert_outcomes "
                "WHERE outcome_r IS NOT NULL AND candle_ts_ms IS NOT NULL"
            )
            if since_ms is not None:
                q += f" AND candle_ts_ms >= {since_ms}"
            return normalize_live(conn.execute(q).df())
        q = (
            "SELECT run_id, symbol, timeframe, strategy, direction, "
            "signal_time, pnl_r "
            "FROM backtest_trades WHERE pnl_r IS NOT NULL"
        )
        if since_ms is not None:
            q += f" AND signal_time >= {since_ms}"
        return normalize_backtest(conn.execute(q).df())


def _load_market(
    db: Path, entries: pd.DataFrame, window_bars: int
) -> dict[tuple[str, str], pd.DataFrame]:
    out: dict[tuple[str, str], pd.DataFrame] = {}
    pairs = entries[["symbol", "tf"]].drop_duplicates()
    with duckdb.connect(str(db), read_only=True) as conn:
        for symbol, tf in zip(pairs["symbol"], pairs["tf"], strict=True):
            sub = entries[(entries["symbol"] == symbol) & (entries["tf"] == tf)]
            margin = (window_bars + 2) * _tf_ms(str(tf))
            tmin, tmax = int(sub["ts_ms"].min()), int(sub["ts_ms"].max())
            out[(str(symbol), str(tf))] = get_ohlcv(
                conn, str(symbol), str(tf), tmin - margin, tmax + _tf_ms(str(tf))
            )
    return out


# --------------------------------------------------------------------------- #
# exploratory (reported, NOT gate-deciding)                                    #
# --------------------------------------------------------------------------- #


def exploratory_by_tf(tagged: pd.DataFrame) -> pd.DataFrame:
    """Per-(warning × direction × tf) breakdown; report-only, no verdicts."""
    rows: list[dict[str, object]] = []
    combos = tagged[["direction", "tf"]].drop_duplicates()
    for warning in WARNING_KEYS:
        for direction, tf in zip(combos["direction"], combos["tf"], strict=True):
            sub = tagged[
                (tagged["direction"] == direction) & (tagged["tf"] == tf)
            ]
            warned = sub.loc[sub[warning], "r"]
            clean = sub.loc[~sub[warning], "r"]
            if warned.empty:
                continue
            avg_clean = float(clean.mean()) if len(clean) else float("nan")
            rows.append(
                {
                    "warning": warning,
                    "direction": direction,
                    "tf": tf,
                    "n_warned": int(len(warned)),
                    "avg_warned": float(warned.mean()),
                    "n_clean": int(len(clean)),
                    "avg_clean": avg_clean,
                    "lift": float(warned.mean()) - avg_clean,
                }
            )
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# report                                                                       #
# --------------------------------------------------------------------------- #


def _fmt(x: float | None) -> str:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "—"
    return f"{x:+.3f}"


def format_report(
    results: dict[str, SourceResult],
    *,
    min_n: int,
    bar: float,
    alpha: float,
    n_boot: int,
    seed: int | None,
) -> str:
    lines: list[str] = []
    lines.append("# H9 warning-value audit — do the W1–W8 alert warnings predict avg_r?")
    lines.append("")
    lines.append(
        f"Generated by `tools/warning_value_audit.py` (read-only). "
        f"Params: min_n={min_n}, bar=±{bar}R, alpha={alpha}, "
        f"n_boot={n_boot}, seed={seed}."
    )
    lines.append("")
    lines.append(
        "Pre-committed semantics: the warned slice is the would-be-suppressed "
        "slice of a hypothetical warning gate. SUPPRESS-CANDIDATE = warned "
        "trades reliably lose ≥ bar (audit_guard ENABLE: bootstrap CI + Holm). "
        "REVERSE = warned trades reliably win ≥ bar (DISABLE). COSMETIC = "
        "well-powered, no gate-grade effect (CONCENTRATE detail kept in Raw). "
        "INSUFFICIENT = under-powered. The two-sample lift CI (warned − clean) "
        "is corroboration only. Backtest = primary substrate; live = "
        "corroboration only."
    )
    lines.append("")
    lines.append("## Headline (backtest primary)")
    lines.append("")
    bt = results.get("backtest")
    if bt is None:
        lines.append("- (backtest source not run — no primary verdict)")
    else:
        sup = [
            f"{v.warning}/{v.direction}"
            for v in bt[0]
            if v.verdict == "SUPPRESS-CANDIDATE"
        ]
        rev = [
            f"{v.warning}/{v.direction}" for v in bt[0] if v.verdict == "REVERSE"
        ]
        if sup:
            lines.append("- SUPPRESS-CANDIDATE: " + ", ".join(sup))
        if rev:
            lines.append("- REVERSE: " + ", ".join(rev))
        if not sup and not rev:
            lines.append(
                "- COSMETIC — no warning carries gate-grade information on "
                "the primary (backtest) substrate"
            )
    for src, (verdicts, expl, n_entries, n_dropped) in results.items():
        lines.append("")
        lines.append(f"## Source: {src} ({n_entries} tagged, {n_dropped} dropped)")
        lines.append("")
        lines.append(
            "| warning | dir | n_warn | n_clean | avg_warn | avg_clean "
            "| CI lo | CI hi | Holm p | lift lo | lift hi | raw | verdict |"
        )
        lines.append(
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- "
            "| --- | --- | --- |"
        )
        for v in verdicts:
            lines.append(
                f"| {v.warning} | {v.direction} | {v.n_warned} | {v.n_clean} "
                f"| {_fmt(v.avg_warned)} | {_fmt(v.avg_clean)} "
                f"| {_fmt(v.ci_lo)} | {_fmt(v.ci_hi)} | {_fmt(v.adj_pvalue)} "
                f"| {_fmt(v.lift_lo)} | {_fmt(v.lift_hi)} "
                f"| {v.raw_decision} | {v.verdict} |"
            )
        if not expl.empty:
            lines.append("")
            lines.append(f"### Exploratory per-tf breakdown ({src}, report-only)")
            lines.append("")
            lines.append(
                "| warning | dir | tf | n_warn | avg_warn | n_clean "
                "| avg_clean | lift |"
            )
            lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
            top = expl.sort_values("n_warned", ascending=False).head(40)
            for row in top.to_dict("records"):
                lines.append(
                    f"| {row['warning']} | {row['direction']} | {row['tf']} "
                    f"| {row['n_warned']} | {_fmt(float(row['avg_warned']))} "
                    f"| {row['n_clean']} | {_fmt(float(row['avg_clean']))} "
                    f"| {_fmt(float(row['lift']))} |"
                )
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="H9: do the W1–W8 alert warnings predict avg_r?"
    )
    p.add_argument("--db", type=Path, default=Path(DEFAULT_DB_PATH))
    p.add_argument(
        "--source", choices=["live", "backtest", "both"], default="both"
    )
    p.add_argument("--since-days", type=int, default=None)
    p.add_argument("--min-n", type=int, default=30)
    p.add_argument("--bar", type=float, default=0.05)
    p.add_argument("--alpha", type=float, default=0.05)
    p.add_argument("--n-boot", type=int, default=10_000)
    p.add_argument("--seed", type=int, default=12345)
    p.add_argument("--window-bars", type=int, default=12)
    p.add_argument("--out", type=Path, default=None)
    return p


def main() -> int:
    args = build_parser().parse_args()
    since_ms: int | None = None
    if args.since_days is not None:
        since_ms = int(
            (pd.Timestamp.utcnow() - pd.Timedelta(days=args.since_days)).timestamp()
            * 1000
        )
    sources = ["backtest", "live"] if args.source == "both" else [args.source]
    results: dict[str, SourceResult] = {}
    for src in sources:
        entries = _load_entries(args.db, src, since_ms)
        if entries.empty:
            print(f"[warn] no entries for source={src}", file=sys.stderr)
            continue
        market = _load_market(args.db, entries, args.window_bars)
        tagged, dropped = tag_trades(entries, market, window_bars=args.window_bars)
        if tagged.empty:
            print(f"[warn] nothing taggable for source={src}", file=sys.stderr)
            continue
        verdicts = evaluate_warning_cells(
            tagged,
            min_n=args.min_n,
            bar=args.bar,
            alpha=args.alpha,
            n_boot=args.n_boot,
            seed=args.seed,
        )
        results[src] = (verdicts, exploratory_by_tf(tagged), len(tagged), dropped)
    if not results:
        print("no data — nothing to evaluate", file=sys.stderr)
        return 1
    report = format_report(
        results,
        min_n=args.min_n,
        bar=args.bar,
        alpha=args.alpha,
        n_boot=args.n_boot,
        seed=args.seed,
    )
    print(report)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report)
        print(f"[saved] {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
PYTHONPATH=. poetry run pytest tests/test_warning_value_audit.py tests/test_warning_audit.py -v
```

Expected: PASS (16 tests).

- [ ] **Step 5: Smoke the CLI parses (no DB run)**

```bash
PYTHONPATH=. poetry run python tools/warning_value_audit.py --help
```

Expected: usage text listing `--source {live,backtest,both}` etc., exit 0.

- [ ] **Step 6: Lint + typecheck + commit**

```bash
make lint-py && make typecheck
git add tools/warning_value_audit.py tests/test_warning_value_audit.py
git commit -m "feat(h9): warning-value audit driver (backtest primary, live corroboration)"
```

---

### Task 5: Makefile target, full gate, docs sync

**Files:**

- Modify: `Makefile` (line-14 `.PHONY` list + new target after
  `buibui-pundit-score`, around line 327)
- Modify: `CLAUDE.md` (one bullet in the `tools/` list)

**Interfaces:**

- Produces: `make buibui-warning-value-audit`.

- [ ] **Step 1: Add the Makefile target**

Append `buibui-warning-value-audit` to the big `.PHONY:` list on line 14, then
add after the `buibui-pundit-score` block (recipe line MUST be a hard tab in
the real Makefile):

<!-- markdownlint-disable MD010 -->

```make
.PHONY: buibui-warning-value-audit
buibui-warning-value-audit:  ## H9: read-only W1-W8 warning-value audit (backtest primary, live corroboration)
	PYTHONPATH=. poetry run python tools/warning_value_audit.py
```

<!-- markdownlint-enable MD010 -->

- [ ] **Step 2: Verify the target resolves**

```bash
make -n buibui-warning-value-audit
```

Expected: prints `PYTHONPATH=. poetry run python tools/warning_value_audit.py`.

- [ ] **Step 3: CLAUDE.md sync**

In the `tools/` section of `CLAUDE.md`, after the
`structural_entry_sim_audit.py` bullet, add:

```markdown
  - `warning_value_audit.py` — H9 read-only warning-value audit; regenerates the W1/W2/W5/W6/W7/W8 candle-warning flags per historical trade from OHLCV (same `signals/alert_formatter.py` helpers as the live path — never persisted, so re-derived) and emits a pre-committed SUPPRESS-CANDIDATE / REVERSE / COSMETIC / INSUFFICIENT verdict per (warning × direction) via `analytics/audit_guard.py` (bootstrap CI + Holm, one family per source; two-sample lift CI reported, never gate-deciding). `backtest_trades` primary (deduped across runs), `signal_alert_outcomes` corroboration. Volume notes excluded (already audited via `gate_audit.py`). Run via `make buibui-warning-value-audit`.
```

Also `README.md`: run `grep -n "reference_level_proximity\|structural_touch" README.md`
— if the tools are listed there, add a matching one-line entry for
`warning_value_audit.py`; if the grep is empty, README doesn't enumerate tools
and needs no change.

- [ ] **Step 4: Full Definition-of-Done gate**

```bash
make lint-py && make typecheck && make test && make test-regression && make lint-md
```

Expected: all green; regression goldens unmoved (this change is additive +
read-only — if goldens move, STOP and investigate, do not regenerate).

- [ ] **Step 5: Commit**

```bash
git add Makefile CLAUDE.md README.md
git commit -m "build(h9): make target + docs sync for warning-value audit"
```

---

### Task 6: Run the real audit + draft the verdict doc (operator-review gated)

**Files:**

- Create: `docs/audits/2026-07-17-h9-warning-value.md`

This task interprets research results — run it in the MAIN session (or report
back before committing), per the repo's model-delegation policy. Do NOT let a
subagent commit a verdict doc unreviewed.

- [ ] **Step 1: Run the audit on the real DB**

```bash
PYTHONPATH=. poetry run python tools/warning_value_audit.py \
  --source both --out docs/audits/2026-07-17-h9-warning-value.md
```

Expected: report printed to stdout + saved. Runtime: minutes (24 cells ×
10k-bootstrap; the lift-CI python loop dominates). Note the dropped-row counts —
live 15m alerts predating OHLCV coverage will drop; report the number, don't
chase it.

- [ ] **Step 2: Prepend the verdict header + caveats to the saved doc**

Edit `docs/audits/2026-07-17-h9-warning-value.md`, inserting ABOVE the generated
content (fill `<...>` from the actual run):

```markdown
# H9 warning-value audit — verdict

- **Date:** 2026-07-17
- **Verdict:** <one line: e.g. "COSMETIC across the board" or "SUPPRESS-CANDIDATE on <cells>, REVERSE on <cells>">
- **Decision:** <what follows: e.g. "no gate change" / "route <cell> to a soft-mode gate proposal (F8 pattern), live-OOS gated">
- **Plan:** `docs/superpowers/plans/2026-07-17-h9-warning-value-audit.md`

## Caveats (pre-committed before the run)

- Warnings co-fire — cells are not independent (W7/W1 are mutually exclusive
  by construction, the rest overlap freely). Holm controls the family error
  rate but co-fired warnings confound single-warning attribution; any
  SUPPRESS-CANDIDATE needs a co-fire cross-tab before a gate proposal.
- Backtest-not-live-confirmed: the primary substrate is engine replay; the
  live ledger is small-n corroboration only.
- Unconditional across strategies and regimes — a follow-up may condition,
  this run does not.
- No gate wiring follows from this PR regardless of outcome (frozen-detector
  policy; soft-mode first, live-OOS gated).
```

- [ ] **Step 3: Lint the doc**

```bash
make lint-md
```

Expected: clean.

- [ ] **Step 4: Commit + PR**

```bash
git add docs/audits/2026-07-17-h9-warning-value.md
git commit -m "docs(h9): warning-value audit verdict"
```

Then follow `/pr-summary` + `/post-branch` per repo convention before reporting
the PR URL.

---

## Self-review checklist (author ran this)

- Spec coverage: flags regeneration (T1), tagging + lift (T2), pre-committed
  gate (T3), two-substrate driver + report (T4), wiring + docs (T5), real run +
  verdict (T6). Volume/CME exclusions and no-gate-wiring stated in Global
  Constraints and the lib docstring.
- Types consistent: `WarningVerdict` fields match between Task 3 code, Task 4
  `_verdict` test helper, and `format_report` usage; `SourceResult` tuple shape
  `(verdicts, exploratory, n_entries, n_dropped)` used consistently.
- No placeholders: every code step contains complete code; every command has
  expected output.
