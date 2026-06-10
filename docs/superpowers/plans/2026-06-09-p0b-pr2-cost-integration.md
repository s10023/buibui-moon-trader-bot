# P0b PR-2 — Cost Integration (funding + slippage) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make backtest avg_r reflect real trading costs by folding directional funding and per-leg slippage into `Trade.pnl_r`, so `net_R = raw_R − fee_R − slippage_R − funding_R`.

**Architecture:** Two new always-`0.0` `Trade` fields (`slippage_pct`, `funding_r`) keep every existing construction site byte-stable. `pnl_r` subtracts the two new terms. `run_backtest` gains kw-only `slippage_pct` and `funding_series` (mirroring the existing `regime_series` pattern); funding_r is precomputed at trade close from the funding series over `(entry_ts, exit_ts]`. A `_build_funding_series_by_symbol` helper feeds the series into the 4 in-scope sweep call sites in `backtest_runner.py`. Slippage is a flat `[backtest].slippage_bps` (default 2.0 → 4 bps round-trip), resolved to a fraction in both config loaders. The regression suite gains a funding fixture so its goldens mirror the full net_R. Combo/cross-TF/live-gate/WFO callers stay byte-stable (deferred).

**Tech Stack:** Python 3.11, pandas, numpy, DuckDB (in-memory for tests), pytest, ruff, mypy strict, TOML.

**Spec:** `docs/redesign/2026-06-09-p0b-honest-costs-design.md` (§3 = cost math, §3.3 = funding sign convention, §3.5 = slippage choice, §3.7 = DB refresh + delta).

## Scope

**In scope (PR-2):**

- `analytics/backtest/engine.py` — `Trade` fields + `pnl_r` + funding_r at close + `slippage_pct`/`funding_series` params.
- `analytics/backtest_runner.py` — `_build_funding_series_by_symbol` + wiring into the 4 in-scope `run_backtest` call sites.
- `analytics/backtest_config.py` + `analytics/signal_config.py` — `slippage_bps` → `slippage_pct` resolution.
- `config/strategy_params.toml` — `[backtest].slippage_bps = 2.0` (inherited by the 3 `signal_watch*.toml` via `extends`).
- `tests/test_regression.py` + `scripts/extract_regression_fixture.py` — funding fixture + pass new args so goldens mirror net_R.
- In-session `make db-update` + before/after delta table.

**Out of scope (byte-stable via kw-defaults; deferred — do NOT touch):**

- `analytics/backtest/combo.py:126`, `analytics/backtest/cross_tf.py:119` — combo/cross-TF engines (deferred follow-up, mirrors Bucket-C deferral).
- `analytics/signal/bt_cache.py:100` — live signal-watch gate; live-ledger costs are **PR-3**.
- `analytics/param_sweep.py` (4 calls) — WFO IS/OOS; honest-WFO costs are a separate follow-up.

These keep zero behavioural drift because the new `run_backtest` params default to `0.0` / `None`.

---

## Task 1: `Trade` slippage + funding fields and `pnl_r` cost terms

**Files:**

- Modify: `analytics/backtest/engine.py:77-114` (`Trade` dataclass + `pnl_r`)
- Test: `tests/test_backtest_lib.py` (class `TestTradePnlR`, after line 102)

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_backtest_lib.py` inside `class TestTradePnlR` (after `test_long_win_returns_positive`, before `test_long_loss_returns_negative`):

```python
    def test_slippage_subtracts_like_fee(self) -> None:
        # risk = 2, entry = 100 → slippage_drag_r = 2 * 0.0002 * 100 / 2 = 0.02
        t = Trade(
            signal_time=0,
            entry_time=1,
            entry_price=100.0,
            direction="long",
            sl_price=98.0,  # risk = 2
            tp_price=104.0,
            exit_price=104.0,
            exit_time=2,
            outcome="win",
            slippage_pct=0.0002,
        )
        assert t.pnl_r == pytest.approx(2.0 - 0.02)

    def test_slippage_hurts_tight_sl_more(self) -> None:
        # Same slippage_pct, tighter SL → larger R drag (R-normalisation property).
        tight = Trade(
            signal_time=0, entry_time=1, entry_price=100.0, direction="long",
            sl_price=99.0, tp_price=102.0, exit_price=102.0, exit_time=2,
            outcome="win", slippage_pct=0.0002,
        )  # risk = 1 → drag = 2 * 0.0002 * 100 / 1 = 0.04
        wide = Trade(
            signal_time=0, entry_time=1, entry_price=100.0, direction="long",
            sl_price=96.0, tp_price=108.0, exit_price=108.0, exit_time=2,
            outcome="win", slippage_pct=0.0002,
        )  # risk = 4 → drag = 2 * 0.0002 * 100 / 4 = 0.01
        tight_drag = 2.0 - tight.pnl_r  # type: ignore[operator]
        wide_drag = 2.0 - wide.pnl_r  # type: ignore[operator]
        assert tight_drag > wide_drag

    def test_funding_r_subtracts(self) -> None:
        # funding_r is precomputed; pnl_r subtracts it verbatim.
        t = Trade(
            signal_time=0, entry_time=1, entry_price=100.0, direction="long",
            sl_price=98.0, tp_price=104.0, exit_price=104.0, exit_time=2,
            outcome="win", funding_r=0.05,
        )
        assert t.pnl_r == pytest.approx(2.0 - 0.05)

    def test_all_cost_terms_compose(self) -> None:
        t = Trade(
            signal_time=0, entry_time=1, entry_price=100.0, direction="long",
            sl_price=98.0, tp_price=104.0, exit_price=104.0, exit_time=2,
            outcome="win", fee_pct=0.0005, slippage_pct=0.0002, funding_r=0.05,
        )
        fee = 2.0 * 0.0005 * 100.0 / 2.0   # 0.05
        slip = 2.0 * 0.0002 * 100.0 / 2.0  # 0.02
        assert t.pnl_r == pytest.approx(2.0 - fee - slip - 0.05)

    def test_defaults_are_byte_stable(self) -> None:
        # New fields default to 0.0 → unchanged from the fee-only formula.
        t = Trade(
            signal_time=0, entry_time=1, entry_price=100.0, direction="long",
            sl_price=98.0, tp_price=104.0, exit_price=104.0, exit_time=2,
            outcome="win",
        )
        assert t.pnl_r == pytest.approx(2.0)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `poetry run pytest tests/test_backtest_lib.py::TestTradePnlR -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'slippage_pct'` (and `funding_r`).

- [ ] **Step 3: Add the fields and update `pnl_r`**

In `analytics/backtest/engine.py`, add the two fields to `Trade` after `fee_pct: float = 0.0` (line 89):

```python
    fee_pct: float = 0.0
    slippage_pct: float = 0.0  # per-leg slippage as a price fraction (fee-shaped)
    funding_r: float = 0.0  # funding cost in R units; precomputed at close (see run_backtest)
    low_volume: bool = False  # True when signal candle volume < 1.5× rolling mean
    volume_spike: bool = False  # True when signal candle volume > 3× rolling mean
```

Then replace the `pnl_r` body (lines 104-114) — update the docstring and the return:

```python
        if self.exit_price is None:
            return None
        risk = abs(self.entry_price - self.sl_price)
        if risk == 0.0:
            return None
        if self.direction == "long":
            raw_r = (self.exit_price - self.entry_price) / risk
        else:
            raw_r = (self.entry_price - self.exit_price) / risk
        fee_drag_r = 2.0 * self.fee_pct * self.entry_price / risk
        slippage_drag_r = 2.0 * self.slippage_pct * self.entry_price / risk
        return raw_r - fee_drag_r - slippage_drag_r - self.funding_r
```

Also update the `pnl_r` docstring (lines 95-103) to mention slippage (same shape as fee) and funding (precomputed). Suggested:

```python
        """P&L in R multiples (1R = amount risked), after fees, slippage, funding.

        Fee drag: each leg (entry + exit) costs fee_pct of notional →
          fee_drag_r = 2 * fee_pct * entry_price / risk
        Slippage drag has the identical shape with slippage_pct, so both
        auto-concentrate their pain on tight-SL cells where costs eat the
        actual risk taken. funding_r is precomputed at close (run_backtest
        needs the funding series + exit_time, which this property cannot see)
        and subtracted directly.
        """
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `poetry run pytest tests/test_backtest_lib.py::TestTradePnlR -v`
Expected: PASS (all, including the existing fee-only tests — they are byte-stable).

- [ ] **Step 5: Commit**

```bash
git add analytics/backtest/engine.py tests/test_backtest_lib.py
git commit -m "feat(backtest): Trade gains slippage_pct + funding_r cost terms"
```

---

## Task 2: funding_r computation at close + `slippage_pct`/`funding_series` params on `run_backtest`

**Files:**

- Modify: `analytics/backtest/engine.py:764-1055` (`run_backtest` signature + funding pre-extract + Trade construction + funding_r at close)
- Test: `tests/test_backtest_lib.py` (new class `TestRunBacktestCosts`, append near the other `run_backtest` test classes)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_backtest_lib.py`:

```python
# ---------------------------------------------------------------------------
# run_backtest — funding + slippage costs
# ---------------------------------------------------------------------------


def _one_long_win_setup() -> tuple[pd.DataFrame, pd.DataFrame]:
    """OHLCV where a long entered at candle 1 wins by candle 3.

    Times are ms: 0,1,2,3,4. Signal at t=0 → entry at t=1 open=100.
    SL fallback = 2% (sl_pct), risk = 2.0. TP at tp_r=2 → 104. High hits 104 at t=3.
    """
    rows = [
        {"open_time": 0, "open": 100, "high": 101, "low": 99, "close": 100,
         "volume": 1000, "taker_buy_volume": 500},
        {"open_time": 1, "open": 100, "high": 101, "low": 99, "close": 100,
         "volume": 1000, "taker_buy_volume": 500},
        {"open_time": 2, "open": 100, "high": 102, "low": 99, "close": 101,
         "volume": 1000, "taker_buy_volume": 500},
        {"open_time": 3, "open": 101, "high": 105, "low": 100, "close": 104,
         "volume": 1000, "taker_buy_volume": 500},
        {"open_time": 4, "open": 104, "high": 106, "low": 103, "close": 105,
         "volume": 1000, "taker_buy_volume": 500},
    ]
    ohlcv = pd.DataFrame(rows)
    signals = pd.DataFrame([{"open_time": 0, "direction": "long"}])
    return ohlcv, signals


class TestRunBacktestCosts:
    def test_funding_none_is_byte_stable(self) -> None:
        ohlcv, signals = _one_long_win_setup()
        base = run_backtest(ohlcv, signals, "BTCUSDT", "1h", "fvg", sl_pct=0.02)
        none = run_backtest(
            ohlcv, signals, "BTCUSDT", "1h", "fvg", sl_pct=0.02, funding_series=None
        )
        assert base.closed_trades[0].funding_r == 0.0
        assert none.closed_trades[0].pnl_r == pytest.approx(base.closed_trades[0].pnl_r)

    def test_long_pays_funding_when_rate_positive(self) -> None:
        ohlcv, signals = _one_long_win_setup()
        # Funding stamp at t=2 (inside (entry=1, exit=3]) with positive rate.
        funding = pd.Series([0.01], index=[2])
        res = run_backtest(
            ohlcv, signals, "BTCUSDT", "1h", "fvg", sl_pct=0.02, funding_series=funding
        )
        trade = res.closed_trades[0]
        # entry=100, risk=2, long → funding_r = +1 * 0.01 * 100 / 2 = 0.5
        assert trade.funding_r == pytest.approx(0.5)
        # net_R = raw 2.0 − funding 0.5 (no fee/slippage)
        assert trade.pnl_r == pytest.approx(2.0 - 0.5)

    def test_short_receives_funding_when_rate_positive(self) -> None:
        # Mirror short: entry 100, sl 102 (risk 2), tp 96; low hits 96.
        rows = [
            {"open_time": 0, "open": 100, "high": 101, "low": 99, "close": 100,
             "volume": 1000, "taker_buy_volume": 500},
            {"open_time": 1, "open": 100, "high": 101, "low": 99, "close": 100,
             "volume": 1000, "taker_buy_volume": 500},
            {"open_time": 2, "open": 100, "high": 101, "low": 98, "close": 99,
             "volume": 1000, "taker_buy_volume": 500},
            {"open_time": 3, "open": 99, "high": 100, "low": 95, "close": 96,
             "volume": 1000, "taker_buy_volume": 500},
            {"open_time": 4, "open": 96, "high": 97, "low": 94, "close": 95,
             "volume": 1000, "taker_buy_volume": 500},
        ]
        ohlcv = pd.DataFrame(rows)
        signals = pd.DataFrame([{"open_time": 0, "direction": "short"}])
        funding = pd.Series([0.01], index=[2])
        res = run_backtest(
            ohlcv, signals, "BTCUSDT", "1h", "fvg", sl_pct=0.02, funding_series=funding
        )
        trade = res.closed_trades[0]
        # short → funding_r = -1 * 0.01 * 100 / 2 = -0.5; pnl_r subtracts → +0.5 added
        assert trade.funding_r == pytest.approx(-0.5)
        assert trade.pnl_r == pytest.approx(2.0 - (-0.5))

    def test_funding_window_is_exclusive_of_entry_inclusive_of_exit(self) -> None:
        ohlcv, signals = _one_long_win_setup()
        # Stamps at entry (t=1, excluded), inside (t=2, included), exit (t=3, included),
        # after (t=4, excluded). Only t=2 and t=3 count.
        funding = pd.Series([0.01, 0.02, 0.03, 0.04], index=[1, 2, 3, 4])
        res = run_backtest(
            ohlcv, signals, "BTCUSDT", "1h", "fvg", sl_pct=0.02, funding_series=funding
        )
        # sum = 0.02 + 0.03 = 0.05 → funding_r = 0.05 * 100 / 2 = 2.5
        assert res.closed_trades[0].funding_r == pytest.approx(2.5)

    def test_empty_funding_series_is_zero(self) -> None:
        ohlcv, signals = _one_long_win_setup()
        funding = pd.Series([], dtype=float)
        res = run_backtest(
            ohlcv, signals, "BTCUSDT", "1h", "fvg", sl_pct=0.02, funding_series=funding
        )
        assert res.closed_trades[0].funding_r == 0.0

    def test_slippage_param_flows_to_trade(self) -> None:
        ohlcv, signals = _one_long_win_setup()
        res = run_backtest(
            ohlcv, signals, "BTCUSDT", "1h", "fvg", sl_pct=0.02, slippage_pct=0.0002
        )
        trade = res.closed_trades[0]
        assert trade.slippage_pct == 0.0002
        # net_R = 2.0 − slippage_drag (2*0.0002*100/2 = 0.02)
        assert trade.pnl_r == pytest.approx(2.0 - 0.02)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `poetry run pytest tests/test_backtest_lib.py::TestRunBacktestCosts -v`
Expected: FAIL — `TypeError: run_backtest() got an unexpected keyword argument 'funding_series'`.

- [ ] **Step 3: Add the params, pre-extract funding, set fields, compute funding_r**

In `analytics/backtest/engine.py`, add two kw-only params to `run_backtest` (after `htf_slope_series_by_anchor` at line 786):

```python
    htf_slope_series_by_anchor: Mapping[tuple[str, int, int], pd.Series] | None = None,
    slippage_pct: float = 0.0,
    funding_series: pd.Series | None = None,
```

After the OHLCV array pre-extraction block (after line 910, `n_candles = len(ohlcv_times_np)`), add the funding pre-extract:

```python
    # Pre-extract the funding series once for funding-cost computation at close.
    # Index is funding_time (ms), ascending (get_funding_rates ORDER BY funding_time).
    if funding_series is not None and not funding_series.empty:
        funding_times_np = funding_series.index.to_numpy(dtype=np.int64)
        funding_rates_np = funding_series.to_numpy(dtype=float)
    else:
        funding_times_np = None
        funding_rates_np = None
```

Add `slippage_pct=slippage_pct,` to the `Trade(...)` construction (after `fee_pct=fee_pct,` at line 1021):

```python
            fee_pct=fee_pct,
            slippage_pct=slippage_pct,
            low_volume=is_low_vol,
            volume_spike=is_spike,
```

After the exit-determination block (after line 1051's `# else: neither hit → trade remains open`, before `result.trades.append(trade)` at line 1053), add the funding_r computation:

```python
        # Funding cost in R units (P0b PR-2). Sum funding stamps held in
        # (entry_time, exit_time]; long pays (+), short receives (−). The
        # subtraction happens in Trade.pnl_r. Graceful 0.0 with no series/data.
        if funding_times_np is not None and trade.exit_time is not None:
            risk = abs(entry_price - sl_price)
            if risk > 0.0:
                lo_i = int(
                    np.searchsorted(funding_times_np, entry_time, side="right")
                )
                hi_i = int(
                    np.searchsorted(funding_times_np, trade.exit_time, side="right")
                )
                if hi_i > lo_i:
                    funding_sum = float(funding_rates_np[lo_i:hi_i].sum())
                    side_sign = 1.0 if direction == "long" else -1.0
                    trade.funding_r = side_sign * funding_sum * entry_price / risk
```

Update the `run_backtest` docstring (the gate list around lines 808-830) with a short note that `slippage_pct` mirrors `fee_pct` and `funding_series` (indexed by funding_time ms) drives the at-close `funding_r`; both default to byte-stable no-ops.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `poetry run pytest tests/test_backtest_lib.py::TestRunBacktestCosts tests/test_backtest_lib.py::TestRunBacktest -v`
Expected: PASS (new cost tests + existing `run_backtest` tests unchanged).

- [ ] **Step 5: Commit**

```bash
git add analytics/backtest/engine.py tests/test_backtest_lib.py
git commit -m "feat(backtest): run_backtest computes funding_r at close + slippage_pct"
```

---

## Task 3: slippage config resolution (`slippage_bps` → `slippage_pct`)

**Files:**

- Modify: `analytics/backtest_config.py:130-140` (field) + `:552-572` (parse, in `load_backtest_config`)
- Modify: `analytics/signal_config.py:253` (field) + `:678-683` (parse)
- Modify: `config/strategy_params.toml:146` (`[backtest]` section)
- Test: `tests/test_backtest_config.py` + `tests/test_backtest_filter.py` (config parse tests)

- [ ] **Step 1: Write the failing tests**

Find how each test module builds a TOML + loads config (search for `load_backtest_config` in `tests/test_backtest_config.py` and `load_signal_config` in `tests/test_backtest_filter.py`), then add tests mirroring that style.

For `tests/test_backtest_config.py` (sweep config):

```python
    def test_slippage_bps_resolves_to_fraction(self, tmp_path: Path) -> None:
        toml = tmp_path / "c.toml"
        toml.write_text('[backtest]\nslippage_bps = 2.0\n')
        cfg = load_backtest_config(str(toml))
        assert cfg.slippage_pct == pytest.approx(0.0002)

    def test_slippage_defaults_to_2bps_when_omitted(self, tmp_path: Path) -> None:
        toml = tmp_path / "c.toml"
        toml.write_text('[backtest]\nmode = "hard"\n')
        cfg = load_backtest_config(str(toml))
        assert cfg.slippage_pct == pytest.approx(0.0002)

    def test_slippage_field_default_is_zero(self) -> None:
        # Direct construction stays byte-stable (engine no-op).
        assert BacktestSweepConfig().slippage_pct == 0.0
```

For `tests/test_backtest_filter.py` (live `BacktestFilterConfig`):

```python
    def test_slippage_bps_resolves_to_fraction(self, tmp_path: Path) -> None:
        toml = tmp_path / "c.toml"
        toml.write_text('[backtest]\nslippage_bps = 2.0\n')
        cfg = load_signal_config(str(toml))
        assert cfg.backtest.slippage_pct == pytest.approx(0.0002)

    def test_slippage_field_default_is_zero(self) -> None:
        assert BacktestFilterConfig().slippage_pct == 0.0
```

Adjust imports (`Path`, `pytest`, `BacktestSweepConfig`/`load_backtest_config`, `BacktestFilterConfig`/`load_signal_config`) to match each file's existing imports.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `poetry run pytest tests/test_backtest_config.py -k slippage tests/test_backtest_filter.py -k slippage -v`
Expected: FAIL — `AttributeError: 'BacktestSweepConfig' object has no attribute 'slippage_pct'`.

- [ ] **Step 3: Add the field + parse in `backtest_config.py`**

Add the field to `BacktestSweepConfig` after `fee_pct: float = 0.0` (line 140):

```python
    fee_pct: float = 0.0
    # Per-leg slippage as a price fraction (resolved from [backtest].slippage_bps).
    slippage_pct: float = 0.0
```

Add the parse line in the `BacktestSweepConfig(...)` return (after `fee_pct=float(data.get("fee_pct", 0.0)),` at line 560). `_bt_section` is already defined at line 512:

```python
        fee_pct=float(data.get("fee_pct", 0.0)),
        slippage_pct=float(
            _bt_section.get("slippage_bps", data.get("slippage_bps", 2.0))
        )
        / 10000.0,
```

- [ ] **Step 4: Add the field + parse in `signal_config.py`**

Add the field to `BacktestFilterConfig` after `fee_pct: float = 0.0` (line 253):

```python
    # Taker fee per leg (e.g. 0.0005 = 0.05%); applied to each backtest trade
    fee_pct: float = 0.0
    # Per-leg slippage as a price fraction (resolved from [backtest].slippage_bps).
    slippage_pct: float = 0.0
```

Add the parse line after `fee_pct=float(raw_bt.get("fee_pct", data.get("fee_pct", 0.0))),` (line 680). `raw_bt` is the `[backtest]` table:

```python
        fee_pct=float(raw_bt.get("fee_pct", data.get("fee_pct", 0.0))),
        # [backtest].slippage_bps (per leg) → fraction; default 2.0 bps (honest cost)
        slippage_pct=float(raw_bt.get("slippage_bps", data.get("slippage_bps", 2.0)))
        / 10000.0,
```

- [ ] **Step 5: Add `slippage_bps` to the base TOML**

In `config/strategy_params.toml`, add to the `[backtest]` section (after line 156 `min_avg_r = 0.0`):

```toml
min_avg_r = 0.0
slippage_bps = 2.0      # per-leg slippage (4 bps round-trip); honest-cost model (P0b)
```

The 3 `signal_watch*.toml` configs inherit `[backtest]` via `extends = "strategy_params.toml"` (their `[backtest]` is commented out), so no per-config edit is needed — verified in Step 7.

- [ ] **Step 6: Run the config tests to verify they pass**

Run: `poetry run pytest tests/test_backtest_config.py -k slippage tests/test_backtest_filter.py -k slippage -v`
Expected: PASS.

- [ ] **Step 7: Verify inheritance into all 3 production configs**

Run:

```bash
poetry run python -c "
from analytics.signal_config import load_signal_config
from analytics.backtest_config import load_backtest_config
for c in ['config/signal_watch.toml','config/signal_watch_all.toml','config/signal_watch_weekdays.toml']:
    s = load_signal_config(c).backtest.slippage_pct
    b = load_backtest_config(c).slippage_pct
    print(c, 'live=', s, 'sweep=', b)
    assert s == 0.0002 and b == 0.0002, c
print('OK')
"
```

Expected: each line prints `live= 0.0002 sweep= 0.0002`, then `OK`.

- [ ] **Step 8: Commit**

```bash
git add analytics/backtest_config.py analytics/signal_config.py config/strategy_params.toml \
        tests/test_backtest_config.py tests/test_backtest_filter.py
git commit -m "feat(config): [backtest].slippage_bps resolves to slippage_pct (default 2bps)"
```

---

## Task 4: funding-series plumbing in `backtest_runner.py`

**Files:**

- Modify: `analytics/backtest_runner.py:43-51` (import) + new `_build_funding_series_by_symbol` (after `_build_regime_series_by_symbol`, ~line 119) + 4 `run_backtest` call sites (662, 795, 847, 1033) + `_collect_sweep_results` signature (523) + `run_backtest_sweep` try-block (777) + `run_backtest_cmd` (921, 1033)
- Test: `tests/test_backtest_runner.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_backtest_runner.py` (match the file's existing in-memory DuckDB + `init_schema` + `upsert_*` helper style; search for an existing test that seeds OHLCV to copy the setup):

```python
def test_build_funding_series_by_symbol(in_memory_db) -> None:
    from analytics.data_store import upsert_funding_rates
    from analytics.backtest_runner import _build_funding_series_by_symbol

    upsert_funding_rates(
        in_memory_db,
        pd.DataFrame(
            {
                "symbol": ["BTCUSDT", "BTCUSDT"],
                "funding_time": [1000, 2000],
                "funding_rate": [0.01, 0.02],
            }
        ),
    )
    out = _build_funding_series_by_symbol(
        in_memory_db, ["BTCUSDT", "ETHUSDT"], 0, 9999
    )
    assert "BTCUSDT" in out
    assert "ETHUSDT" not in out  # no data → omitted (engine falls to funding_r=0)
    s = out["BTCUSDT"]
    assert list(s.index) == [1000, 2000]
    assert list(s.to_numpy()) == [pytest.approx(0.01), pytest.approx(0.02)]
```

(If `tests/test_backtest_runner.py` has no `in_memory_db` fixture, add one: `duckdb.connect(":memory:")` + `init_schema`, mirroring other analytics tests — or reuse an existing fixture in that file / `tests/conftest.py`.)

- [ ] **Step 2: Run the test to verify it fails**

Run: `poetry run pytest tests/test_backtest_runner.py -k build_funding_series -v`
Expected: FAIL — `ImportError: cannot import name '_build_funding_series_by_symbol'`.

- [ ] **Step 3: Add the import + builder**

In `analytics/backtest_runner.py`, add `get_funding_rates` to the `from analytics.data_store import (...)` block (after `get_ohlcv,` at line 45):

```python
    get_funding_rates,
    get_ohlcv,
```

Add the builder after `_build_regime_series_by_symbol` (after line 118):

```python
def _build_funding_series_by_symbol(
    conn: duckdb.DuckDBPyConnection,
    symbols: list[str],
    start_ms: int,
    end_ms: int,
) -> dict[str, pd.Series]:
    """Load the funding-rate series per symbol once per sweep.

    Funding costs are always-on (no gate), so this is built unconditionally.
    Returns ``{symbol: Series}`` indexed by funding_time (ms, ascending) so the
    engine's at-close ``searchsorted`` window works. Symbols with no funding
    rows are omitted — the engine then sees ``funding_series=None`` and falls
    to ``funding_r = 0.0`` (graceful, matches a pre-backfill data gap).
    """
    out: dict[str, pd.Series] = {}
    for sym in symbols:
        df = get_funding_rates(conn, sym, start_ms, end_ms)
        if df.empty:
            continue
        out[sym] = pd.Series(
            df["funding_rate"].astype(float).to_numpy(),
            index=df["funding_time"].astype("int64").to_numpy(),
        )
    return out
```

- [ ] **Step 4: Run the builder test to verify it passes**

Run: `poetry run pytest tests/test_backtest_runner.py -k build_funding_series -v`
Expected: PASS.

- [ ] **Step 5: Thread the series into `_collect_sweep_results`**

Add a kw-only param to `_collect_sweep_results` (after `htf_slope_by_symbol=...` at line 525):

```python
    htf_slope_by_symbol: dict[str, dict[tuple[str, int, int], pd.Series]] | None = None,
    funding_by_symbol: dict[str, pd.Series] | None = None,
```

After the `htf_slope_by_symbol is None` build block (after line 573), add:

```python
    if funding_by_symbol is None:
        funding_by_symbol = _build_funding_series_by_symbol(
            conn, symbols, start_ms, end_ms
        )
```

In the Phase-3 `run_backtest(...)` call (line 662), add two args after `cfg.fee_pct,` and inside the kw block (after `htf_slope_series_by_anchor=...`):

```python
            cfg.fee_pct,
            slippage_pct=cfg.slippage_pct,
            ...
            htf_slope_series_by_anchor=(
                htf_slope_by_symbol.get(symbol)
                if htf_slope_by_symbol is not None
                else None
            ),
            funding_series=funding_by_symbol.get(symbol),
```

Note: `slippage_pct` is kw-only on `run_backtest`, so it must go in the keyword section, not positionally after `cfg.fee_pct`. Place both `slippage_pct=cfg.slippage_pct` and `funding_series=...` in the keyword block (alongside `min_sl_pct=...`).

- [ ] **Step 6: Thread the series into `run_backtest_sweep` (build + 3 call sites)**

In `run_backtest_sweep`'s try-block, after `htf_slope_by_symbol = _build_htf_slope_series_by_symbol(...)` (line 782), add:

```python
        funding_by_symbol = _build_funding_series_by_symbol(
            conn, symbols, start_ms, end_ms
        )
```

In the **tp-sweep** `run_backtest(...)` (line 795) and **atr-sweep** `run_backtest(...)` (line 847), add to each keyword block:

```python
                        live_parity=cfg.live_parity,
                        slippage_pct=cfg.slippage_pct,
                        ...
                        funding_series=funding_by_symbol.get(sym),
```

In the single-run branch's `_collect_sweep_results(...)` call (line 888), pass the pre-built map:

```python
                    htf_slope_by_symbol=htf_slope_by_symbol,
                    funding_by_symbol=funding_by_symbol,
```

- [ ] **Step 7: Thread into `run_backtest_cmd`**

Add a param to `run_backtest_cmd` (after `fee_pct: float = 0.0,` at line 928):

```python
    fee_pct: float = 0.0,
    slippage_pct: float = 0.0,
```

Build the funding series before the `run_backtest(...)` call (after the `htf_slope_by_anchor` block ends, ~line 1031):

```python
        funding_series = _build_funding_series_by_symbol(
            conn, [symbol], start_ms, end_ms
        ).get(symbol)
```

Add to the `run_backtest(...)` keyword block (line 1033):

```python
            sl_pct,
            tp_r,
            fee_pct,
            slippage_pct=slippage_pct,
            ...
            htf_slope_series_by_anchor=htf_slope_by_anchor,
            funding_series=funding_series,
```

- [ ] **Step 8: Add a sweep-level integration test**

Add to `tests/test_backtest_runner.py` a test that seeds OHLCV (enough for ≥1 closed `fvg` trade) + funding, runs `run_backtest_sweep` with `save_results=True` against an in-memory DB (use a tmp file db path or in-memory with the runner's connect — match how other runner tests invoke the sweep), and asserts the saved `backtest_runs.avg_r` is **lower** than a baseline sweep with `slippage_bps = 0` and no funding rows. If invoking the full sweep is heavy in this file, instead assert at the `_collect_sweep_results` level:

```python
def test_collect_sweep_results_applies_costs(in_memory_db) -> None:
    # Seed OHLCV + funding so at least one fvg trade closes; build a cfg with
    # slippage_pct=0.0002. Run _collect_sweep_results twice — once with a funding
    # map, once with an empty {} — and assert the funding run's mean avg_r is lower.
    ...
```

Keep this test pragmatic: the rigorous numeric delta is produced in Task 6 against the real DB. The unit assertion only needs to prove costs flow through and reduce avg_r (sign + direction), plus that the default path (`funding_by_symbol={}` + `slippage_pct=0`) is byte-stable vs today.

- [ ] **Step 9: Run the runner tests**

Run: `poetry run pytest tests/test_backtest_runner.py -v`
Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add analytics/backtest_runner.py tests/test_backtest_runner.py
git commit -m "feat(backtest): plumb funding_series + slippage_pct through sweep runner"
```

---

## Task 5: regression suite mirrors net_R (funding fixture + new args)

**Files:**

- Modify: `scripts/extract_regression_fixture.py:29-67` (extract a funding fixture)
- Modify: `tests/test_regression.py:159-189` (load funding fixture, build series, pass `slippage_pct` + `funding_series`)
- New (generated, committed): `tests/fixtures/btc_funding_200d.parquet`
- Regenerated (committed): `tests/fixtures/golden_*.json`

- [ ] **Step 1: Add funding extraction to the fixture script**

In `scripts/extract_regression_fixture.py`, add `get_funding_rates` to the import (line 29):

```python
from analytics.data_store import DEFAULT_DB_PATH, get_funding_rates, get_ohlcv  # noqa: E402
```

After the timeframe loop writes the OHLCV parquets (after line 65), add (still inside the `try`):

```python
        fdf = get_funding_rates(conn, SYMBOL, since_ms, now_ms)
        if fdf.empty:
            print(
                f"WARNING: no funding for {SYMBOL} — funding fixture skipped",
                file=sys.stderr,
            )
        else:
            fdf["symbol"] = fdf["symbol"].astype(object)
            fout = OUTPUT_DIR / "btc_funding_200d.parquet"
            fdf.to_parquet(fout, index=False)
            print(f"  wrote {fout.name}  ({len(fdf)} rows)")
```

- [ ] **Step 2: Generate the funding fixture**

Run (requires the local `analytics.db` with PR-1's funding backfill present):

```bash
poetry run python scripts/extract_regression_fixture.py
```

Expected: prints `wrote btc_funding_200d.parquet  (N rows)` with N > 0 (≈ 1 stamp / 8h over the window, ~810+). If it warns "no funding", STOP — PR-1's backfill must be run first (`buibui analytics backfill --symbols BTCUSDT,ETHUSDT,SOLUSDT --since 2025-09-12`, `DATA_SOURCE=binance`).

- [ ] **Step 3: Wire funding + slippage into the regression test**

In `tests/test_regression.py`, load the funding fixture once (near the OHLCV `fixtures` load at line 159):

```python
    fixtures = _load_fixtures(["15m", "1h", "4h", "1d"])
    _funding_path = FIXTURE_DIR / "btc_funding_200d.parquet"
    funding_series: pd.Series | None = None
    if _funding_path.exists():
        _fdf = pd.read_parquet(_funding_path)
        funding_series = pd.Series(
            _fdf["funding_rate"].astype(float).to_numpy(),
            index=_fdf["funding_time"].astype("int64").to_numpy(),
        )
```

Add the two new args to the `run_backtest(...)` call (after `fee_pct=cfg.backtest.fee_pct,` at line 184, in the keyword block):

```python
                fee_pct=cfg.backtest.fee_pct,
                slippage_pct=cfg.backtest.slippage_pct,
                ...
                tp_r_short=cfg.effective_tp_r(strategy, "BTCUSDT", tf, "short"),
                funding_series=funding_series,
```

Ensure `import pandas as pd` exists at the top of `tests/test_regression.py` (add if missing).

- [ ] **Step 4: Confirm the goldens move (and only by costs)**

First run against the OLD goldens to see the drift report (this SHOULD fail — that is the intended behavioural move):

Run: `poetry run pytest tests/test_regression.py -v --timeout=300`
Expected: FAIL with a diff report showing avg_r values dropping (more negative) — sanity-check a couple of cells by hand: the drop should ≈ `slippage_drag + funding_r`, never an improvement. If any cell *improves*, STOP and inspect the funding sign (spec §3.3) before regenerating.

Then regenerate:

Run: `poetry run pytest tests/test_regression.py --update-golden -v --timeout=300`
Then: `git diff --stat tests/fixtures/golden_*.json` (confirm all 3 goldens changed).

- [ ] **Step 5: Commit**

```bash
git add scripts/extract_regression_fixture.py tests/test_regression.py \
        tests/fixtures/btc_funding_200d.parquet tests/fixtures/golden_*.json
git commit -m "test(regression): goldens mirror net_R (funding fixture + slippage)"
```

---

## Task 6: in-session DB refresh + before/after avg_r delta

**Files:** none committed by this task except the (already-moved) goldens from `make db-update`'s `regression-update` leg. Produces the delta table for the PR body / handoff.

- [ ] **Step 1: Capture the controlled before/after delta (read-only, real DB)**

Run a controlled A/B over the **same** loaded data (costs off vs on) so the delta is purely cost-driven, not data-window drift. Use an in-session script (do NOT commit it; it is analysis only):

```bash
poetry run python - <<'PY'
import duckdb, datetime, statistics
from analytics.data_store import DEFAULT_DB_PATH, get_ohlcv
from analytics.backtest_config import load_backtest_config
from analytics.backtest_runner import (
    detect_signals_for_strategy, _build_funding_series_by_symbol, _SWEEP_STRATEGIES,
)
from analytics.backtest_lib import run_backtest

cfg = load_backtest_config("config/signal_watch.toml")
conn = duckdb.connect(str(DEFAULT_DB_PATH), read_only=True)
end = int(datetime.datetime.now(datetime.UTC).timestamp()*1000)
start = int(datetime.datetime.strptime(cfg.since,"%Y-%m-%d").replace(tzinfo=datetime.UTC).timestamp()*1000)
syms = cfg.symbols or ["BTCUSDT","ETHUSDT","SOLUSDT"]
fund = _build_funding_series_by_symbol(conn, syms, start, end)

def run(slip, use_funding):
    longs, shorts, allr = [], [], []
    for sym in syms:
        for tf in cfg.timeframes:
            ohlcv = get_ohlcv(conn, sym, tf, start, end)
            if ohlcv.empty: continue
            for strat in _SWEEP_STRATEGIES:
                sigs = detect_signals_for_strategy(conn, ohlcv, sym, tf, strat, start, end, cfg.smt_pairs.get(sym))
                if sigs is None or sigs.empty: continue
                bt = run_backtest(ohlcv, sigs, sym, tf, strat,
                    cfg.effective_sl_pct(strat,sym,tf), cfg.effective_tp_r(strat,sym,tf),
                    cfg.fee_pct, min_sl_pct=cfg.min_sl_pct,
                    slippage_pct=slip,
                    funding_series=(fund.get(sym) if use_funding else None))
                for t in bt.closed_trades:
                    r = t.pnl_r
                    if r is None: continue
                    allr.append(r)
                    (longs if t.direction=="long" else shorts).append(r)
    m = lambda xs: round(statistics.mean(xs),4) if xs else None
    return m(allr), m(longs), m(shorts), len(allr)

before = run(0.0, False)
after  = run(cfg.slippage_pct, True)
print("signal_watch.toml (BTC/ETH/SOL, since", cfg.since, ")")
print(f"  before  all={before[0]} long={before[1]} short={before[2]} n={before[3]}")
print(f"  after   all={after[0]} long={after[1]} short={after[2]} n={after[3]}")
print(f"  delta   all={round(after[0]-before[0],4)} long={round(after[1]-before[1],4)} short={round(after[2]-before[2],4)}")
conn.close()
PY
```

Expected: `after` ≤ `before` on every column (costs only subtract); the long/short split shows funding is directional (longs pay more under positive funding; shorts may move less or favourably). Record this table for the PR body. Repeat for `signal_watch_all.toml` / `signal_watch_weekdays.toml` if you want the per-day_filter rows the spec asks for.

- [ ] **Step 2: Sanity gate before db-update**

Confirm: no column *improves* unexpectedly; the `all` delta magnitude is plausible (a few bps to a fraction of an R, dominated by tight-SL/15m cells). If a cell improves, STOP — re-check the funding sign (spec §3.3) and the `(entry, exit]` window.

- [ ] **Step 3: Run the routine DB refresh**

Run: `make db-update`
This chains: backtest (3 configs, SAVE) → recalibrate → regression-update. The regression-update leg re-asserts the goldens from Task 5 (should be clean now). Expect star ratings to shift downward where costs bite (cost-driven, not data drift).

- [ ] **Step 4: Confirm recalibrate shifts are cost-driven**

Spot-check a few `confidence_ratings` before/after (e.g. a tight-SL 15m cell should lose more stars than a 1d cell). Note any star demotions in the handoff.

- [ ] **Step 5: Commit the DB-refresh golden movement (if `make db-update` re-touched goldens)**

```bash
git status --short tests/fixtures/golden_*.json
# If changed beyond Task 5's commit:
git add tests/fixtures/golden_*.json
git commit -m "chore(db-update): refresh goldens after cost integration"
```

---

## Task 7: Definition-of-Done gate

- [ ] **Step 1: lint + typecheck + full suite + regression**

```bash
make lint-py        # ruff format + lint
make typecheck      # mypy strict
make test           # full pytest suite
make test-regression  # goldens (now reflect net_R) unmoved vs Task 5/6 commit
make lint-md        # plan doc + any doc edits
```

State each result plainly. `make test-regression` must be GREEN against the regenerated goldens (the move was intentional and already committed). If any fails, fix before proceeding — do not claim green without running it.

- [ ] **Step 2: Branch + PR**

The work is on a fresh branch off `main` (e.g. `feat/cost-integration`). After DoD is green:

```bash
gh pr create --title "feat(backtest): P0b PR-2 — honest costs (funding + slippage)" --body-file /tmp/pr-cost-integration.md
```

(Use `/pr-summary` to draft the body; include the before/after delta table from Task 6.) Then run `/post-branch` (behaviour gate WALKS — this is behavioural): sync `CLAUDE.md` (`engine.py` Trade/pnl_r note + `backtest_runner.py` funding builder), `.claude/context/analytics.md` (Trade cost fields, `funding_series` param, `slippage_bps`), `README.md` if costs are user-facing, and append "Documentation updates" to the PR body.

`gh` runs on `s10023` (`gh auth switch --user s10023` if a call hits a collaborator error).

---

## Self-Review (completed against the spec)

- **§3.1 Trade fields** → Task 1 (defaults 0.0, byte-stable). ✓
- **§3.2 pnl_r slippage term** → Task 1. ✓
- **§3.3 funding_r + sign convention** → Task 2 (long +1 pays, short −1 receives; `(entry, exit]` window; entry_price notional). ✓
- **§3.4 funding_series plumbing** → Task 2 (engine param) + Task 4 (`_build_funding_series_by_symbol` + 4 call sites). ✓
- **§3.5 slippage config** → Task 3 (`[backtest].slippage_bps` default 2.0 → `/10000`; both loaders; base TOML inherited by 3 configs). ✓
- **§3.6 tests** → Tasks 1–4 (pnl_r, funding sign matrix, window, empty→0, byte-stable, config parse, runner builder). ✓
- **§3.7 db-update + delta** → Task 6 (controlled A/B + `make db-update` + recalibrate spot-check). ✓
- **§5 coverage gate** → Task 5 Step 2 gate (STOP if funding fixture empty) + Task 6 Step 2 sanity gate (STOP if any cell improves). ✓
- **Deferred (PR-3 / out of scope)**: `bt_cache.py` live gate, `combo.py`/`cross_tf.py`, `param_sweep.py` WFO — all byte-stable via kw-defaults, documented in Scope. ✓

**Type consistency:** `slippage_pct` (float fraction) and `funding_r` (float R) names match across `Trade`, `run_backtest`, both config dataclasses, and the runner. `funding_series` (pd.Series | None) matches engine param ↔ `_build_funding_series_by_symbol` return values ↔ regression-test construction. `funding_by_symbol` (dict[str, pd.Series]) is the runner-internal map name.

**Placeholder scan:** no TBD/"handle edge cases"/uncited symbols — every code step shows the code. The two pragmatic Task-4 Step-8 / Task-6 scripts give concrete code with explicit assertions.
