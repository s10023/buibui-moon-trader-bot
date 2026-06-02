# Outcome-Ledger SL/TP Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the live outcome-ledger writer (`analytics/signal/scanner.py`) persist a non-NULL `sl_price`/`tp_price`/`rr_ratio` for **every** fired event by falling back to the same pct-based SL the alert formatter already uses, so `signal_alert_outcomes` can score 100% of alerts instead of 11%. Then (separate task) retroactively reconstruct the 2,141 already-NULL rows.

**Architecture:** The writer at `scanner.py:918-940` currently sets `ev_sl`/`ev_tp` only when an event carries a valid structural `sl_price`, else leaves them `None` (→ NULL → unscoreable). Replace that with "structural-if-valid, else `entry*(1±eff_sl_pct)` floored by `min_sl_pct`", mirroring `signals/alert_formatter.py::_widest_sl` + `_apply_min_sl_floor`. The identical fallback pattern already exists locally at `scanner.py:973-986` (CME rough-TP block) — reuse its shape. No schema change, no detector change, no alert change.

**Tech Stack:** Python 3.13, Poetry, pytest + unittest.mock, DuckDB (`:memory:` for tests), pandas, ruff, mypy strict, TOML config.

---

## Background the engineer needs

- **Why** — full reasoning + table evidence in `docs/superpowers/specs/2026-06-01-outcome-ledger-sl-tp-fallback-design.md`. TL;DR: 89% of `signal_alert_outcomes` rows have `tp_price = NULL` and can never resolve; `bos` (87% of volume) has 0/256 resolved; the alert shows a target but the ledger drops it.
- **The asymmetry** — alert formatter (`_widest_sl`, alert_formatter.py:306-309) falls back to `price*(1±sl_pct)` when no structural SL exists; the ledger writer (scanner.py:918-940) does not. `SignalEvent.sl_price` defaults to `0.0` (types.py:66 — "use sl_pct fallback").
- **Parity targets** — match the alert exactly:
  - `eff_sl_pct = _resolve_sl_pct(strategy_params, e.strategy, symbol, tf, sl_pct)` (per-strategy; already imported at scanner.py:60, used for the alert at scanner.py:534). **Do not** use bare `sl_pct`.
  - Floor with the function-param `min_sl_pct` (the writer runs *outside* the `if backtest_cfg:` guard, so `backtest_cfg.min_sl_pct` is not reliably available — the existing structural path at line 926 already uses param `min_sl_pct`). Verify param vs `backtest_cfg.min_sl_pct` are the same in the shipped configs; note any divergence.
  - Keep the existing `e.tp_price` structural-TP preference (lines 928-932 / 936-940) ahead of the `sl_dist * eff_alert_tp_r` fallback.
- **Per-event grain is intentional** — the ledger scores each event on its own SL (right grain for per-(strategy,tf,direction) stats), not the alert's group-widest SL. Tests assert per-event values.
- **Safety** — **never smoke-run the live daemon** to test (prior incident fired real Telegram + wrote real `analytics.db`; see memory `feedback-live-daemon-smoke-test-danger`). All tests use mocked events + in-memory DuckDB.
- **No `make db-update`** — `signal_alert_outcomes` is not in the backtest golden pipeline; regression goldens must stay unmoved (verify in the final task).
- **Run from repo root** `/home/kng/repo/buibui-moon-trader-bot` on branch `fix/outcome-ledger-sl-tp-fallback`. After any Python change: `make lint-py`, `make typecheck`, `make test`.

## File structure

- **Modify** `analytics/signal/scanner.py` — the outcome-row SL/TP fallback (Task 2). Optionally extract a small `_resolve_event_sl_tp(...)` helper for testability (Task 1).
- **Modify/Create** `tests/test_outcome_ledger_fallback.py` — fallback unit + parity + non-NULL invariant tests (Tasks 1-3).
- **Create** `tools/backfill_null_tp_outcomes.py` + `tests/test_backfill_null_tp_outcomes.py` — retroactive migration (Task 4).
- **Modify** `CLAUDE.md`, `.claude/context/analytics.md` (or `signals.md`) — note the writer now always persists a scoreable SL/TP (Task 5).

---

## Task 1: Extract a testable per-event SL/TP resolver

**Files:** Modify `analytics/signal/scanner.py`; Test `tests/test_outcome_ledger_fallback.py`

Lift the per-event SL/TP math out of the inline loop into a pure helper so it can be unit-tested without standing up a scan. Signature (no I/O):

```python
def _resolve_outcome_sl_tp(
    *, direction: str, entry: float, struct_sl: float, struct_tp: float,
    eff_sl_pct: float, min_sl_pct: float, tp_r: float,
) -> tuple[float, float]:
    """Return (sl_price, tp_price) for an outcome-ledger row, mirroring the alert
    formatter's fallback when struct_sl is invalid for `direction`."""
```

- [ ] **Step 1: failing test** — `tests/test_outcome_ledger_fallback.py`:
  - `test_long_uses_structural_sl_when_valid` — `struct_sl=95, entry=100` → `sl≈95` side.
  - `test_long_falls_back_to_pct_when_no_structural` — `struct_sl=0, entry=100, eff_sl_pct=0.02` → `sl_dist≈2.0`, `sl≈98`, `tp≈100+2*tp_r`.
  - `test_short_mirror` (both branches).
  - `test_min_sl_floor_widens` — tiny structural SL floored to `entry*min_sl_pct`.
  - `test_structural_tp_preferred` — valid `struct_tp` wins over `sl_dist*tp_r`.
- [ ] **Step 2: run, verify ImportError/fail.**
- [ ] **Step 3: implement** the helper (pattern from spec §Forward fix). Return floats only.
- [ ] **Step 4: green** `poetry run pytest tests/test_outcome_ledger_fallback.py -v`.
- [ ] **Step 5:** `make lint-py && make typecheck`. Commit: `refactor(signal): extract _resolve_outcome_sl_tp helper`.

## Task 2: Wire the helper into the writer (close the hole)

**Files:** Modify `analytics/signal/scanner.py:918-959`

- [ ] **Step 1: failing test** — add `test_outcome_row_never_null_tp` to the new test file: build a `SignalEvent` with `sl_price=0.0` (no structural), run the persist loop (or a thin wrapper that calls the writer block), assert the written row has non-NULL `sl_price`, `tp_price`, `rr_ratio`. Use in-memory DuckDB + `init_schema` + `get_signals_history`-style read, or inspect the dict passed to a mocked `upsert_signal_outcome`.
- [ ] **Step 2: run, verify fail** (today's code writes NULL).
- [ ] **Step 3: implement** — replace lines 923-940 to call `_resolve_outcome_sl_tp(...)` with `eff_sl_pct = _resolve_sl_pct(strategy_params, e.strategy, symbol, tf, sl_pct)`. `ev_sl, ev_tp` always set; set `"rr_ratio": eff_alert_tp_r` unconditionally (drop the `if ev_tp is not None` guard at line 955). Delete the now-stale "stay unbackfilled by design" comment (916-917).
- [ ] **Step 4: green** + run the full outcome/scanner suites: `poetry run pytest tests/test_outcome_backfill.py tests/test_outcome_ledger_fallback.py -v`.
- [ ] **Step 5:** `make lint-py && make typecheck && make test`. Commit: `fix(signal): outcome ledger falls back to pct SL so every alert is scoreable`.

## Task 3: Parity + regression guards

**Files:** Modify `tests/test_outcome_ledger_fallback.py`

- [ ] **Step 1:** add `test_ledger_sl_tp_matches_formatter_for_no_structural_event` — for a single-event group with `sl_price=0`, assert the ledger `(sl,tp)` equals the formatter's `_widest_sl`/`_apply_min_sl_floor` + tp math for the same `eff_sl_pct`/`min_sl_pct`/`tp_r`. (Per-event ⇒ single-event group keeps it exact.)
- [ ] **Step 2:** add `test_structural_event_row_unchanged` — an event with a valid structural SL writes the *same* `(sl,tp)` as before this change (lock the no-regression on the 11% that already worked).
- [ ] **Step 3: green.** Commit: `test(signal): outcome-ledger ↔ alert SL/TP parity guards`.

## Task 4: Retroactive migration script (separate, opt-in)

**Files:** Create `tools/backfill_null_tp_outcomes.py`, `tests/test_backfill_null_tp_outcomes.py`

- [ ] **Step 1: failing test** — seed an in-memory DB with a `tp_price IS NULL, outcome IS NULL` row (known `entry_price`, strategy, symbol, tf, direction) + enough `ohlcv` to resolve it; assert that after `--apply` the row has non-NULL `sl_price`/`tp_price`/`rr_ratio` and a resolved `outcome`/`outcome_r`.
- [ ] **Step 2: implement** per spec §Retroactive migration: select NULL rows, recompute `eff_sl_pct`/`eff_tp_r` from config keyed on stored `(strategy, symbol, tf, direction)`, derive pct-fallback `sl`/`tp`, `UPDATE`, then call `backfill_outcomes()`. Read-only by default; `--apply` writes; print dry-run counts + sample. Idempotent.
- [ ] **Step 3: green** + `make lint-py && make typecheck`.
- [ ] **Step 4: dry-run against the real DB read-only** (no `--apply`): `PYTHONPATH=. poetry run python tools/backfill_null_tp_outcomes.py` — sanity-check the reported reconstructable count (~2,141). Do **not** `--apply` until reviewed. Commit: `feat(tools): retroactive SL/TP backfill for NULL-tp outcome rows`.

## Task 5: Docs + verification

**Files:** Modify `CLAUDE.md`, `.claude/context/analytics.md` (outcome_backfill / scanner entries)

- [ ] **Step 1:** document that the writer now always persists a scoreable SL/TP (pct fallback) and reference the migration tool.
- [ ] **Step 2: verify no golden movement** — `make test-regression` (or `make db-update` dry check) → goldens UNMOVED (this change is outside the backtest pipeline).
- [ ] **Step 3:** final whole-branch review; `make lint-py && make typecheck && make test` all green. Commit: `docs: outcome ledger SL/TP fallback`.
- [ ] **Step 4:** `/pr-summary` then `/post-branch`.

---

## Done criteria

- Every fired event writes non-NULL `sl_price`/`tp_price`/`rr_ratio`.
- Ledger `(sl,tp)` matches the alert for no-structural-SL events (single-event parity test).
- Structural-SL events unchanged (regression test).
- Retro tool reconstructs the NULL backlog read-only; `--apply` gated on review.
- Lint/typecheck/test green; regression goldens unmoved; no `make db-update`.
- **Post-merge watch:** `tools/live_outcomes_report.py` `no_tp` share → 0; `bos` resolves.
