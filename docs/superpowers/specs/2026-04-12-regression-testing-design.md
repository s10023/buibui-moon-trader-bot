# Regression Testing Framework — Design Spec

**Date:** 2026-04-12
**Status:** Approved, pending implementation

## Problem

After merging any branch — param tuning, WFO sweep, detector refactor — there is no automated way to detect whether the change accidentally hurt backtest performance. Regressions (signal suppression, avg_r drops, directional skew) are only discovered manually, if at all.

## Goal

A regression test layer that:

- Catches unintended metric changes on every PR via CI
- Makes intentional changes explicit and reviewable as a `git diff` on golden JSON files
- Runs in seconds, requires no live DB

## Non-Goals

- Forward-testing / live performance tracking
- Cross-commit DB comparison (DB schema evolves)
- SMT divergence coverage (Phase 1 — BTC only; ETH added later)

---

## Design

### Layer 1 — Frozen fixture data

OHLCV extracted once from `analytics.db` and committed as parquet files. Never updated except as an explicit decision (which also resets all golden files).

```text
tests/fixtures/
  btc_1h_200d.parquet    # ~4800 rows, BTCUSDT 1h
  btc_4h_200d.parquet    # ~1200 rows, BTCUSDT 4h
  btc_1d_200d.parquet    # ~200 rows,  BTCUSDT 1d
```

Extracted via a one-time script:

```bash
poetry run python scripts/extract_regression_fixture.py
```

When the fixture window is refreshed (e.g. every 6 months), all golden files are regenerated at the same time — this is a deliberate, visible commit, not silent drift.

**ETH fixtures** added in a later phase to cover `smt_divergence`.

### Layer 2 — Golden JSON files (one per TOML config)

```text
tests/fixtures/
  golden_signal_watch.json
  golden_weekdays.json
  golden_all.json
```

Each file is keyed `strategy → tf → metric dict`. Only strategy × TF combinations present in that config's `strategy_timeframes` get entries.

**Structure:**

```json
{
  "generated_at": "2026-04-12",
  "config": "config/signal_watch.toml",
  "strategies": {
    "engulfing": {
      "1h": {
        "trade_count": 47,
        "long_trade_count": 23,
        "short_trade_count": 24,
        "win_rate": 0.51,
        "avg_r": 0.31,
        "total_r": 14.57,
        "long_avg_r": 0.44,
        "short_avg_r": 0.18,
        "long_total_r": 10.12,
        "short_total_r": 4.32,
        "long_win_rate": 0.57,
        "short_win_rate": 0.46,
        "max_drawdown_r": 8.20,
        "recovery_factor": 1.77
      }
    }
  }
}
```

**Metrics captured per strategy × TF:**

| Field | What regression it catches |
| --- | --- |
| `trade_count` | Gate too aggressive, detector misfiring |
| `long_trade_count` / `short_trade_count` | ADR / day_filter suppressing one direction silently |
| `win_rate` | Win/loss ratio shift |
| `avg_r` | Overall edge drift |
| `total_r` | Cumulative impact |
| `long_avg_r` / `short_avg_r` | tp_r_long / tp_r_short tuning regressions |
| `long_total_r` / `short_total_r` | Directional cumulative edge |
| `long_win_rate` / `short_win_rate` | Directional win ratio |
| `max_drawdown_r` | Risk profile change |
| `recovery_factor` | Edge quality (total_r / max_drawdown_r) |

**Excluded:** volume split metrics (low/normal/spike avg_r) — trade_count shift already catches volume gate regressions.

**Tolerance:** exact match. Intentional changes require a conscious `make regression-update` + golden file commit.

`generated_at` is metadata only — excluded from comparison.

### Layer 3 — Pytest regression test

**`tests/test_regression.py`** — one parametrized test, three configs:

```python
@pytest.mark.parametrize("config_name,config_path", [
    ("signal_watch", "config/signal_watch.toml"),
    ("weekdays",     "config/weekdays.toml"),
    ("all",          "config/all.toml"),
])
def test_golden_metrics(config_name, config_path, request):
    update = request.config.getoption("--update-golden")
    cfg = load_backtest_config(config_path)

    ohlcv_by_tf = {
        "1h": pd.read_parquet("tests/fixtures/btc_1h_200d.parquet"),
        "4h": pd.read_parquet("tests/fixtures/btc_4h_200d.parquet"),
        "1d": pd.read_parquet("tests/fixtures/btc_1d_200d.parquet"),
    }

    results = {}
    for strategy, detector_fn in DETECTOR_REGISTRY.items():
        for tf in cfg.strategy_timeframes.get(strategy, []):
            signals = detector_fn(ohlcv_by_tf[tf], **cfg.get_detector_params(strategy))
            result  = run_backtest(
                ohlcv_by_tf[tf], signals,
                tp_r=cfg.effective_tp_r(strategy, "BTCUSDT", tf),
                tp_r_long=cfg.effective_tp_r(strategy, "BTCUSDT", tf, "long"),
                tp_r_short=cfg.effective_tp_r(strategy, "BTCUSDT", tf, "short"),
                fee_pct=cfg.fee_pct,
                min_sl_pct=cfg.min_sl_pct,
            )
            results.setdefault(strategy, {})[tf] = _extract_metrics(result)

    golden_path = Path(f"tests/fixtures/golden_{config_name}.json")

    if update:
        golden_path.write_text(json.dumps({
            "generated_at": date.today().isoformat(),
            "config": config_path,
            "strategies": results,
        }, indent=2))
        return

    golden = json.loads(golden_path.read_text())["strategies"]
    assert results == golden, _diff_report(results, golden)
```

`_diff_report()` produces a focused failure message — only changed cells:

```text
REGRESSION DETECTED:
  engulfing / 1h:
    avg_r:             0.31 → 0.27  ← dropped
    long_trade_count:  23   → 19    ← dropped
  pin_bar / 4h:
    short_avg_r:       0.18 → 0.44  ← improved (intentional?)

Run: make regression-update  (if changes are intentional)
```

`--update-golden` registered in `conftest.py`:

```python
def pytest_addoption(parser):
    parser.addoption("--update-golden", action="store_true", default=False)
```

### Layer 4 — Makefile targets

```makefile
test-regression:
    @poetry run pytest tests/test_regression.py -v

regression-update:
    @poetry run pytest tests/test_regression.py --update-golden -v
    @echo "Review: git diff tests/fixtures/golden_*.json"
    @echo "Commit golden updates alongside your TOML/code changes."
```

### Layer 5 — CI GitHub Actions job

Added to the existing workflow, runs after the unit test job:

```yaml
regression:
  name: Regression tests
  runs-on: ubuntu-latest
  needs: test
  steps:
    - uses: actions/checkout@v4
    - name: Set up Python
      uses: actions/setup-python@v4
      with:
        python-version: '3.11'
    - name: Install dependencies
      run: pip install poetry && poetry install --no-root
    - name: Run regression tests
      run: make test-regression
```

CI behaviour:

- Golden file missing → error: "run `make regression-update`"
- Metrics changed → fail with diff report, PR blocked
- Golden matches → green, PR unblocked

**Note:** Parquet fixture files must not be blocked by `.gitignore`. Check for `*.parquet` exclusion rules before implementation.

---

## Workflow Integration

After every WFO sweep, param change, or detector refactor:

```text
PR opened
  → CI: make test              (988 unit tests)
  → CI: make test-regression   (3 configs × strategies × TFs)
  → fail with diff report if metrics moved
  → dev runs: make regression-update
  → git diff tests/fixtures/golden_*.json   ← review intent
  → commit golden update alongside TOML/code change
  → PR green
```

`make regression-update` added as a step in `/wfo-sweep` and `/recalibrate` skills (one-line addition to each skill file).

---

## Phase 2 (later)

- Add `tests/fixtures/eth_1h_200d.parquet` + `eth_4h_200d.parquet` to cover `smt_divergence`
- `golden_untuned.json` — one-time baseline generated with registry defaults (no TOML overrides) as a zero-point reference; never updated by CI

---

## Out of Scope

- A17 (backtest sliding window drift): `--since YYYY-MM-DD` flag to anchor `start_ms` — tracked separately as highest-priority infrastructure fix
