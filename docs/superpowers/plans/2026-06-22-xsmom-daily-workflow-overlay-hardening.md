# XS-solo Daily-Workflow Integration + Overlay Hardening — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the XS-solo executor's dry-run a trustworthy daily tool — refresh the universe's 1d bars, refuse degenerate/thin books, let the full book establish on a cold start, and size the overlay to the real book envelope.

**Architecture:** Purely additive changes to the fail-closed overlay (`trade/overlay.py`), the executor orchestrator (`trade/xsmom_executor.py`), the two CLI drivers (`tools/xsmom_execute.py`, `tools/xsmom_targets.py`), and the Makefile. The XS engine (`analytics/`) and all golden fixtures are untouched. Dry-run remains the default; nothing submits.

**Tech Stack:** Python 3.11, frozen dataclasses, argparse, pytest, DuckDB (read-only), Poetry, ruff, mypy (strict).

**Spec:** `docs/superpowers/specs/2026-06-22-xsmom-daily-workflow-overlay-hardening-design.md`

---

## File Structure

- `trade/overlay.py` — `RiskLimits` gains `min_active_positions: int = 0`; `evaluate_overlay` gains a keyword-only `current_gross_notional: float | None = None`, a breadth breach, and a two-branch turnover cap. Stays pure / no-I/O.
- `trade/xsmom_executor.py` — `run_once` computes `current_gross_notional` from already-fetched positions × marks and forwards it.
- `tools/xsmom_execute.py` — extract `build_parser()`; add `--vol-target` (0.20) and `--min-active-positions` (15); recalibrate `--max-gross-leverage` default 3.0 → 4.5; thread vol target into the config; wire breadth into `RiskLimits`.
- `tools/xsmom_targets.py` — add `--vol-target` (0.20) for parity (read-only).
- `Makefile` — `buibui-universe-sync`, `buibui-xsmom-daily` (+`.PHONY`).
- `CLAUDE.md`, `README.md` — document the two `make` targets and the new flags.
- Tests: `tests/trade/test_overlay.py`, `tests/trade/test_xsmom_executor.py`, `tests/trade/test_execute_cli.py`.

**Recommended task order:** 1 → 2 → 3 → 4 → 5 → 6 (overlay primitives first, then the executor that uses them, then the CLI that configures them, then wiring + docs).

---

## Task 1: Min-breadth overlay guard

**Files:**

- Modify: `trade/overlay.py` (`RiskLimits`, `evaluate_overlay`)
- Test: `tests/trade/test_overlay.py`

- [ ] **Step 1: Update the `_limits` test helper to carry the new field**

In `tests/trade/test_overlay.py`, change the helper so the base dict includes `min_active_positions` and tolerates an int value. Add `from typing import Any` to the imports.

```python
def _limits(**kw: float) -> RiskLimits:
    base: dict[str, Any] = {
        "max_gross_leverage": 3.0,
        "max_position_notional_frac": 0.5,
        "max_drawdown_frac": 0.25,
        "max_run_turnover_frac": 1.0,
        "max_data_staleness_hours": 36.0,
        "min_active_positions": 0,
    }
    base.update(kw)
    return RiskLimits(**base)
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/trade/test_overlay.py`:

```python
def test_breadth_guard_aborts_thin_book() -> None:
    # active_count = 1 (one position) < min 15
    book = _book([TargetPosition("AAAUSDT", "long", 0.1, 100.0, 0.0)])
    v = evaluate_overlay(
        _plan([]),
        book,
        AccountState(10_000.0, 10_000.0, False),
        _limits(min_active_positions=15),
        data_age_hours=1.0,
    )
    assert v.allowed is False and any("thin book" in a for a in v.aborts)


def test_breadth_guard_passes_full_book() -> None:
    positions = [
        TargetPosition(f"S{i}USDT", "long", 0.1, 100.0, 0.0) for i in range(15)
    ]
    v = evaluate_overlay(
        _plan([_intent(100.0)]),
        _book(positions),
        AccountState(10_000.0, 10_000.0, False),
        _limits(min_active_positions=15),
        data_age_hours=1.0,
    )
    assert v.allowed is True
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `poetry run pytest tests/trade/test_overlay.py::test_breadth_guard_aborts_thin_book -v`
Expected: FAIL — `TypeError: ... unexpected keyword argument 'min_active_positions'` (field not yet on `RiskLimits`).

- [ ] **Step 4: Add the field and the check**

In `trade/overlay.py`, add the field as the last `RiskLimits` member (a default keeps it additive):

```python
@dataclass(frozen=True)
class RiskLimits:
    max_gross_leverage: float
    max_position_notional_frac: float
    max_drawdown_frac: float
    max_run_turnover_frac: float
    max_data_staleness_hours: float
    min_active_positions: int = 0
```

In `evaluate_overlay`, add the breadth check (place it right after the `aborts: list[str] = []` line, before the kill-switch check):

```python
    if book.active_count < limits.min_active_positions:
        aborts.append(
            f"thin book: active_count {book.active_count} < "
            f"min {limits.min_active_positions}"
        )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `poetry run pytest tests/trade/test_overlay.py -v`
Expected: PASS (all existing + the 2 new tests; `min_active_positions` defaults to 0, so the 8 prior tests do not trip the new guard).

- [ ] **Step 6: Lint, typecheck, commit**

```bash
make lint-py && make typecheck
git add trade/overlay.py tests/trade/test_overlay.py
git commit -m "feat(trade): min-breadth overlay guard (RiskLimits.min_active_positions)"
```

---

## Task 2: Cold-start turnover allowance

**Files:**

- Modify: `trade/overlay.py` (`evaluate_overlay`)
- Test: `tests/trade/test_overlay.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/trade/test_overlay.py`:

```python
def test_cold_start_allows_full_build() -> None:
    # establishing (current gross 0 < half target): turnover capped at the
    # gross cap (4.5x), not the tight 1.0x steady-state cap.
    # target_gross_notional = 2.88 * 10000 = 28800; turnover = 28800 < 45000.
    v = evaluate_overlay(
        _plan([_intent(28_800.0)], gross=2.88),
        _book([], gross=2.88),
        AccountState(10_000.0, 10_000.0, False),
        _limits(max_gross_leverage=4.5),
        data_age_hours=1.0,
        current_gross_notional=0.0,
    )
    assert v.allowed is True


def test_cold_start_still_bounded_by_gross_cap() -> None:
    # even establishing, an over-leveraged target trips the separate gross guard
    v = evaluate_overlay(
        _plan([_intent(50_000.0)], gross=5.0),
        _book([], gross=5.0),
        AccountState(10_000.0, 10_000.0, False),
        _limits(max_gross_leverage=4.5),
        data_age_hours=1.0,
        current_gross_notional=0.0,
    )
    assert v.allowed is False and any("gross" in a.lower() for a in v.aborts)


def test_steady_state_turnover_still_capped() -> None:
    # current gross == target gross -> NOT establishing -> tight 1.0x cap.
    # turnover 28800 > 1.0 * 10000 -> abort.
    v = evaluate_overlay(
        _plan([_intent(28_800.0)], gross=2.88),
        _book([], gross=2.88),
        AccountState(10_000.0, 10_000.0, False),
        _limits(max_gross_leverage=4.5),
        data_age_hours=1.0,
        current_gross_notional=28_800.0,
    )
    assert v.allowed is False and any("turnover" in a.lower() for a in v.aborts)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/trade/test_overlay.py::test_cold_start_allows_full_build -v`
Expected: FAIL — `TypeError: evaluate_overlay() got an unexpected keyword argument 'current_gross_notional'`.

- [ ] **Step 3: Add the parameter and the two-branch turnover cap**

In `trade/overlay.py`, change the signature to add a keyword-only parameter:

```python
def evaluate_overlay(
    plan: OrderPlan,
    book: TargetBook,
    account: AccountState,
    limits: RiskLimits,
    data_age_hours: float,
    *,
    current_gross_notional: float | None = None,
) -> OverlayVerdict:
```

Replace the existing turnover block:

```python
    turnover = sum(abs(o.delta_notional) for o in plan.intents)
    turnover_cap = limits.max_run_turnover_frac * book.capital
    if turnover > turnover_cap:
        aborts.append(f"run turnover {turnover:.2f} > cap {turnover_cap:.2f}")
```

with the two-branch version:

```python
    target_gross_notional = plan.target_gross_leverage * book.capital
    establishing = (
        current_gross_notional is not None
        and current_gross_notional < 0.5 * target_gross_notional
    )
    turnover = sum(abs(o.delta_notional) for o in plan.intents)
    turnover_cap = (
        limits.max_gross_leverage if establishing else limits.max_run_turnover_frac
    ) * book.capital
    if turnover > turnover_cap:
        aborts.append(f"run turnover {turnover:.2f} > cap {turnover_cap:.2f}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/trade/test_overlay.py -v`
Expected: PASS. Note `test_run_turnover_guard_aborts` (existing) still passes: it omits `current_gross_notional` → `None` → not establishing → steady-state cap → 12000 > 10000 aborts.

- [ ] **Step 5: Lint, typecheck, commit**

```bash
make lint-py && make typecheck
git add trade/overlay.py tests/trade/test_overlay.py
git commit -m "feat(trade): auto-detected cold-start turnover allowance in overlay"
```

---

## Task 3: Executor computes & forwards `current_gross_notional`

**Files:**

- Modify: `trade/xsmom_executor.py` (`run_once`)
- Test: `tests/trade/test_xsmom_executor.py`

- [ ] **Step 1: Update the executor-test `_limits` helper for the breadth field**

In `tests/trade/test_xsmom_executor.py`, add `"min_active_positions": 0` to the `_limits` base dict (so the 3-symbol seeded book never trips breadth in these tests) and widen the base annotation. Add `from typing import Any` if absent.

```python
def _limits(**kw: float) -> RiskLimits:
    base: dict[str, Any] = {
        "max_gross_leverage": 10.0,
        "max_position_notional_frac": 1.0,
        "max_drawdown_frac": 0.5,
        "max_run_turnover_frac": 10.0,
        "max_data_staleness_hours": 1e9,
        "min_active_positions": 0,
    }
    base.update(kw)
    return RiskLimits(**base)
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/trade/test_xsmom_executor.py`:

```python
def test_cold_start_build_passes_overlay(tmp_path: Path) -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    syms = _seed(conn)
    adapter = _FakeAdapter(
        equity=10_000.0, positions={}, marks={s: 100.0 for s in syms}
    )
    # Tight steady-state turnover frac, generous gross cap: a cold start (no
    # positions) must STILL pass because the executor forwards current_gross=0,
    # so the establishing branch lifts the turnover cap to the gross cap.
    limits = _limits(max_run_turnover_frac=0.0001)
    res = run_once(
        conn,
        adapter,
        ForecastConfig(),
        syms,
        limits,
        no_trade_band_frac=0.0,
        exchange_leverage=5,
        state_path=tmp_path / "state.json",
    )
    assert res.verdict.allowed is True


def test_steady_state_turnover_blocks(tmp_path: Path) -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    syms = _seed(conn)
    # Large existing positions => current gross >> half target => NOT establishing
    # => tight steady-state turnover cap applies => the rebalance is blocked.
    adapter = _FakeAdapter(
        equity=10_000.0,
        positions={s: 1_000.0 for s in syms},
        marks={s: 100.0 for s in syms},
    )
    limits = _limits(max_run_turnover_frac=0.0001)
    res = run_once(
        conn,
        adapter,
        ForecastConfig(),
        syms,
        limits,
        no_trade_band_frac=0.0,
        exchange_leverage=5,
        state_path=tmp_path / "state.json",
    )
    assert res.verdict.allowed is False and any(
        "turnover" in a.lower() for a in res.verdict.aborts
    )
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `poetry run pytest tests/trade/test_xsmom_executor.py::test_cold_start_build_passes_overlay -v`
Expected: FAIL — the overlay currently receives no `current_gross_notional`, so the cold start is blocked by the tight steady-state turnover cap (`verdict.allowed is False`).

- [ ] **Step 4: Compute and forward `current_gross_notional`**

In `trade/xsmom_executor.py`, after the `marks`/`filters` fetch (the lines that set `marks = adapter.get_marks(...)` and `filters = adapter.get_filters(...)`), compute the current gross from signed `positionAmt` × mark, then pass it into the overlay call:

```python
    current_gross_notional = sum(
        abs(qty) * marks.get(sym, 0.0) for sym, qty in positions.items()
    )
```

Change the `evaluate_overlay` call to forward it:

```python
    verdict = evaluate_overlay(
        plan,
        book,
        account,
        limits,
        data_age,
        current_gross_notional=current_gross_notional,
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `poetry run pytest tests/trade/test_xsmom_executor.py -v`
Expected: PASS (both new tests + all prior executor tests).

- [ ] **Step 6: Lint, typecheck, commit**

```bash
make lint-py && make typecheck
git add trade/xsmom_executor.py tests/trade/test_xsmom_executor.py
git commit -m "feat(trade): executor forwards current gross notional to the overlay"
```

---

## Task 4: CLI — vol-target knob, breadth arg, recalibrated gross default

**Files:**

- Modify: `tools/xsmom_execute.py` (extract `build_parser`, new args, recalibrated default, thread vol target + breadth)
- Modify: `tools/xsmom_targets.py` (`--vol-target` parity)
- Test: `tests/trade/test_execute_cli.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/trade/test_execute_cli.py` (add the import `from tools.xsmom_execute import build_parser`):

```python
def test_parser_defaults_recalibrated() -> None:
    args = build_parser().parse_args([])
    assert args.max_gross_leverage == 4.5
    assert args.vol_target == 0.20
    assert args.min_active_positions == 15


def test_parser_overrides() -> None:
    args = build_parser().parse_args(
        ["--vol-target", "0.10", "--min-active-positions", "20"]
    )
    assert args.vol_target == 0.10
    assert args.min_active_positions == 20
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/trade/test_execute_cli.py::test_parser_defaults_recalibrated -v`
Expected: FAIL — `ImportError: cannot import name 'build_parser'`.

- [ ] **Step 3: Extract `build_parser()` and add the new args**

In `tools/xsmom_execute.py`, add `import dataclasses` near the top imports. Extract the parser construction from `main()` into a module-level function (move every `parser.add_argument(...)` line into it), recalibrate the gross default, and add the two new args:

```python
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--symbols", type=str, default=None)
    parser.add_argument(
        "--mode", choices=("dry_run", "testnet", "live"), default="dry_run"
    )
    parser.add_argument("--no-trade-band", type=float, default=0.005)
    parser.add_argument("--exchange-leverage", type=int, default=5)
    parser.add_argument(
        "--vol-target",
        type=float,
        default=0.20,
        help="Portfolio vol target (validated 0.20; deploy first live cycles at 0.10)",
    )
    parser.add_argument("--max-gross-leverage", type=float, default=4.5)
    parser.add_argument("--max-position-notional-frac", type=float, default=0.5)
    parser.add_argument("--max-drawdown-frac", type=float, default=0.25)
    parser.add_argument("--max-run-turnover-frac", type=float, default=1.0)
    parser.add_argument("--max-data-staleness-hours", type=float, default=36.0)
    parser.add_argument("--min-active-positions", type=int, default=15)
    parser.add_argument("--i-understand-live", action="store_true")
    parser.add_argument("--kill", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--state-dir", type=Path, default=_DEFAULT_STATE_DIR)
    return parser
```

In `main()`, replace the inline parser construction with:

```python
    args = build_parser().parse_args()
```

Thread the vol target into the config and the breadth limit into `RiskLimits`:

```python
    cfg = ForecastConfig.from_toml(args.config) if args.config else ForecastConfig()
    cfg = dataclasses.replace(cfg, vol_target_annual=args.vol_target)
    symbols = args.symbols.split(",") if args.symbols else load_universe()
    limits = RiskLimits(
        max_gross_leverage=args.max_gross_leverage,
        max_position_notional_frac=args.max_position_notional_frac,
        max_drawdown_frac=args.max_drawdown_frac,
        max_run_turnover_frac=args.max_run_turnover_frac,
        max_data_staleness_hours=args.max_data_staleness_hours,
        min_active_positions=args.min_active_positions,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/trade/test_execute_cli.py -v`
Expected: PASS (new + existing CLI tests).

- [ ] **Step 5: Add `--vol-target` parity to the targets driver**

In `tools/xsmom_targets.py`, add `import dataclasses` near the top. Add the arg in `main()` (next to `--config`):

```python
    parser.add_argument("--vol-target", type=float, default=0.20)
```

After the `cfg = ForecastConfig.from_toml(...) ...` line, thread it:

```python
    cfg = dataclasses.replace(cfg, vol_target_annual=args.vol_target)
```

- [ ] **Step 6: Lint, typecheck, full test, commit**

```bash
make lint-py && make typecheck && make test
git add tools/xsmom_execute.py tools/xsmom_targets.py tests/trade/test_execute_cli.py
git commit -m "feat(trade): vol-target knob, breadth arg, recalibrated gross default (CLI)"
```

---

## Task 5: Makefile targets

**Files:**

- Modify: `Makefile`

- [ ] **Step 1: Add the two targets**

Append near the other `buibui-xsmom-*` targets in `Makefile`. **Recipe bodies MUST be
indented with a literal TAB, not spaces (Makefile requirement); shown space-indented
below only so this plan passes the markdown linter.**

```makefile
.PHONY: buibui-universe-sync
buibui-universe-sync:  ## P3: incremental 1d sync of the full research universe (XS book input)
    PYTHONPATH=. poetry run python buibui.py analytics sync --universe --timeframes 1d

.PHONY: buibui-xsmom-daily
buibui-xsmom-daily:  ## P3: daily XS workflow — sync universe 1d, then executor dry-run
    $(MAKE) buibui-universe-sync
    $(MAKE) buibui-xsmom-execute
```

Also add `buibui-universe-sync buibui-xsmom-daily` to the long top-level `.PHONY` line (line ~14) for consistency with the existing convention.

- [ ] **Step 2: Verify the targets resolve (no execution)**

Run: `make -n buibui-universe-sync && make -n buibui-xsmom-daily`
Expected: prints the `poetry run python buibui.py analytics sync --universe --timeframes 1d` command and the two sub-make invocations; no network/DB call.

- [ ] **Step 3: Commit**

```bash
git add Makefile
git commit -m "build: add buibui-universe-sync + buibui-xsmom-daily make targets"
```

---

## Task 6: Docs

**Files:**

- Modify: `CLAUDE.md`, `README.md`

- [ ] **Step 1: Update CLAUDE.md**

In the `tools/` / Makefile description area for the XS executor, note the new daily-sync target and the recalibrated/added overlay knobs. Add a concise sentence to the `trade/` package paragraph and/or the `tools/xsmom_execute.py` line, e.g.:

> Daily workflow: `make buibui-universe-sync` (`analytics sync --universe --timeframes 1d` — the XS book runs on 1d only, which `analytics sync`'s `1h 4h` default never refreshes) then `make buibui-xsmom-daily` (sync + dry-run). Overlay defaults recalibrated to the real book envelope: `--vol-target` (0.20 validated; deploy first at 0.10), `--max-gross-leverage` 4.5, `--min-active-positions` 15 breadth guard, auto-detected cold-start turnover allowance.

- [ ] **Step 2: Update README.md**

Add the two `make` targets to the relevant command list / table so README stays in sync with the Makefile.

- [ ] **Step 3: Lint markdown, commit**

```bash
make lint-md
git add CLAUDE.md README.md
git commit -m "docs: document XS daily-sync targets + recalibrated overlay knobs"
```

---

## Final Verification (Definition of Done)

- [ ] `make lint-py` ✓
- [ ] `make typecheck` ✓
- [ ] `make test` green
- [ ] `make test-regression` goldens **unmoved** (engine untouched — confirm no diff)
- [ ] `make lint-md` ✓
- [ ] Manual acceptance (dry-run only, no writes): after `make buibui-universe-sync`, a cold-start `make buibui-xsmom-execute` yields the full ~25-leg book with `allowed=True`; an intentionally stale DB yields a `thin book` breadth abort.
