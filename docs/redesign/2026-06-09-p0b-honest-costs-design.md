# P0b — Honest Costs (funding + slippage) + funding-history backfill — design

**Date:** 2026-06-09 · **Status:** design / approved (no code yet) · **Parent:** `docs/redesign/2026-06-05-p0-research-guardrails-spec.md` §4 (P0b) · **Predecessor:** P0a (CLOSED — PRs #421/#422/#424/#425/#426).

## 0. Goal

Make backtest avg_r reflect real trading costs. Today `engine.py::Trade.pnl_r`
deducts only a round-trip taker fee. **Funding accrual and slippage are missing** —
both bias avg_r upward, disproportionately on the high-turnover / tight-SL TA book,
which is exactly where the false positives live. Extend the P&L to:

```text
net_R = raw_R − fee_R − slippage_R − funding_R     (all in R units, ÷ risk)
```

**Success metric:** every cell's avg_r drops by an honest, cost-driven amount; the
prune-to-core direction sharpens; the change ships with a before/after avg_r delta
table and moved regression goldens. Many cells get *more* negative — that is the
feature, not a regression.

**Prerequisite discovered during design:** the ingested `funding_rates` table covers
only ~2026-01-14 → present (~420 rows/symbol) versus the backtest window
~2025-09-12 → present. Root cause: `run_backfill` calls `_sync_ancillary` →
`sync_funding_rates(days=90)` **inside** the OHLCV loop, so funding is *always*
recent-only even on a deep backfill. The Binance REST endpoint *does* serve full
history; our wrapper never paginated it. Applying funding costs over a window where
half the trades have `funding_R = 0` would be a time-inhomogeneous bias, not honest
costs. So the work splits into a backfill prerequisite, then the cost integration.

## 1. Phasing (3 PRs, hard sequence)

| PR | Scope | Behavioral? | Blast radius |
| --- | --- | --- | --- |
| **PR-1** Funding-history backfill | Paginate funding fetch → wire into `run_backfill` → ingest full BTC/ETH/SOL history; verify coverage | No — data-layer only; R unchanged | Low — additive |
| **PR-2** Cost integration | funding_R + slippage_R into `Trade.pnl_r`; funding-series plumbing; `[backtest].slippage_bps`; `make db-update` + recalibrate + regression-update in-session; before/after delta | **Yes** — goldens move | High |
| **PR-3** (deferred) Live-ledger parity | Mirror costs in `signal/outcome_backfill.py::_scan_forward` so live R == backtest R | Yes — live reporting | Medium |

Sequence is hard: PR-1 ships → I run the backfill to populate the DB → verify
funding now covers the OHLCV window → only then PR-2 (it needs the data present to
move goldens and produce the delta honestly). PR-3 is explicitly deferred to a
follow-up (decision in §8).

## 2. PR-1 — Funding-history backfill (data layer)

### 2.1 `analytics/data_fetcher.py::fetch_funding_rates`

Add optional `start_time: int | None = None`, `end_time: int | None = None` (ms).
When `start_time` is set, pass `startTime` / `endTime` / `limit=1000` through to
`client.futures_funding_rate(...)`. Backward compatible: callers passing only
`limit` keep the recent-only behaviour. Binance-only — the OKX adapter does not
implement funding (per CLAUDE.md); the funding path is reached only on the Binance
client, so no OKX branch is added.

### 2.2 `analytics/data_sync.py::backfill_funding_rates`

New paginated function mirroring the OHLCV `backfill()` loop:

```text
backfill_funding_rates(conn, client, symbol, since_ms, until_ms=None, sleep=0.1)
  current_start = since_ms
  loop:
    df = fetch_funding_rates(client, symbol, start_time=current_start,
                             end_time=until_ms, limit=1000)
    if df empty: break
    upsert_funding_rates(conn, df)
    if len(df) < 1000: break          # last (short) page
    current_start = int(df.funding_time.iloc[-1]) + 1
    sleep(sleep)
```

Returns the row count ingested. ~810 records cover 9 months at 8h cadence (one
page), but the loop generalises to multi-year history.

### 2.3 `analytics/analytics_runner.py::run_backfill` wiring

Per symbol, call `backfill_funding_rates(conn, client, symbol, since_ms)` instead of
the recent-only funding sync. **Open-interest stays recent-only** — no detector
needs OI history, so OI is out of scope here. The funding backfill rides the
existing `buibui analytics backfill --since` flag; no new CLI surface.

### 2.4 Ingest + verify (run in-session, read/write upsert)

Run `buibui analytics backfill --symbols BTCUSDT,ETHUSDT,SOLUSDT --since 2025-09-01`
(or the OHLCV floor) locally with `DATA_SOURCE=binance`. Pure upsert — idempotent,
never deletes older data. Then verify: `MIN(funding_time)` per symbol ≤ the OHLCV
floor, and the funding window now spans the backtest window.

### 2.5 Tests

- Mock client returning two pages (1000 + a short page) → assert the loop upserts
  both, advances `current_start` correctly, and stops on the short page.
- `fetch_funding_rates` with `start_time` passes `startTime`/`endTime`/`limit` to the
  client mock; without it, preserves the recent-only call shape (no regression).
- In-memory DuckDB coverage assertion after a two-page backfill.

## 3. PR-2 — Cost integration (behavioral)

### 3.1 `Trade` dataclass

Two new fields, both defaulting to `0.0` so existing construction sites and tests
are byte-stable until populated:

- `slippage_pct: float = 0.0` — per-leg slippage as a price fraction (scalar,
  fee-shaped).
- `funding_r: float = 0.0` — funding cost in R units, **precomputed at close**
  (funding needs the funding series + exit_time, which `pnl_r` cannot see).

### 3.2 `Trade.pnl_r`

```text
raw_r          = directional (exit−entry)/risk
fee_drag_r     = 2 · fee_pct      · entry_price / risk     (unchanged)
slippage_drag_r= 2 · slippage_pct · entry_price / risk     (new; identical shape)
return raw_r − fee_drag_r − slippage_drag_r − funding_r
```

Slippage is the same math as fee drag, so R-normalisation auto-concentrates its
pain on tight-SL / 15m cells — the false-positive zone.

### 3.3 funding_r computation (in `run_backtest`, at close)

When the trade's exit is determined (exit_time known), and a `funding_series` is
provided:

```text
events     = funding_series[(index > entry_ts) & (index <= exit_ts)]
funding_sum= events.sum()                      # 0.0 when no data → graceful
side_sign  = +1 if long else −1
funding_r  = side_sign · funding_sum · entry_price / risk
```

**Sign convention.** `pnl_r` subtracts `funding_r`. For a long when rates are
positive: `funding_r > 0` → net_R decreases (longs pay). For a short when rates are
positive: `side_sign = −1` → `funding_r < 0` → subtracting adds R (shorts receive).
This makes funding directional, materially relevant to the short-edge finding
([[direction-axis-hard-flip]]), and currently invisible.

**Window convention.** Funding events counted in `(entry_ts, exit_ts]` — a position
held across a funding stamp pays/receives it. Edge cases at the exact stamp are ~0
impact; documented as an approximation.

**Notional approximation.** Uses `entry_price` as the per-unit notional proxy (real
funding uses mark price × current qty at each stamp). `entry_price / risk` is
unit-free since qty cancels. Stated as an approximation; acceptable for backtest
honesty and consistent with the existing fee-drag treatment.

### 3.4 Plumbing

New keyword-only `funding_series: pd.Series | None = None` on `run_backtest`,
mirroring the existing `regime_series` parameter. Computed once per symbol in
`backtest_runner.py` from `get_funding_rates(conn, symbol, start, end)` (indexed by
`funding_time`), passed down. `None` → all `funding_r = 0.0` (byte-stable when
unwired).

### 3.5 Config — slippage

- New `[backtest].slippage_bps` (float). **Default = 2.0** (per leg → 4 bps
  round-trip). Conservative middle of spec §8's 1–3 bps for majors; spec §5 says err
  toward over-estimating. Flat bps chosen over ×ATR: same math shape as fee, one
  fewer knob, no evidence ×ATR is better at this stage (YAGNI).
- Resolve `slippage_pct = slippage_bps / 10000.0` in `backtest_config.py` (sweep) +
  `signal_config.py::BacktestFilterConfig` (parity), mirroring how `fee_pct` is
  sourced from `[backtest].fee_pct`.
- Add `slippage_bps = 2.0` to `strategy_params.toml` base + the 3 `signal_watch*.toml`
  configs (inherited via `extends`).
- Funding has **no toggle** — always-on, read from the ingested table.

### 3.6 Tests

- `pnl_r`: slippage term subtracts correctly; tight-SL cell loses more R than
  wide-SL (R-normalisation property).
- funding_r: long pays / short receives; multi-stamp sum; window boundary; empty
  series → 0.0; sign matrix (long/short × positive/negative rate).
- `backtest_runner` builds the funding series from in-memory DuckDB and passes it
  through; default-None path is byte-identical to today.
- Config: `slippage_bps` parsed and converted; default applied when omitted.

### 3.7 In-session DB refresh + delta

After code lands: `make db-update` (backtest 3 configs → recalibrate →
regression-update). Regression goldens **intentionally move** — regenerate and note
it. Publish a before/after `mean(avg_r)` delta table (overall + per day_filter
scope, and a funding-direction split long vs short), and confirm recalibrate star
shifts are cost-driven.

## 4. PR-3 — Live-ledger parity (deferred)

`signal/outcome_backfill.py::_scan_forward` currently applies **no costs at all**
(latent fee-parity gap predating P0b). The same `net_R` deduction must eventually
mirror there so live-ledger R and backtest R agree (engine ↔ `_scan_forward`
must not diverge). **Deferred to a follow-up PR** (user decision) to keep PR-2's
blast radius bounded to the backtest pipeline. Tracked here so it is not lost.

## 5. Methodology / honesty notes

- **Coverage gate:** PR-2 must not run its delta until PR-1's backfill verifies
  funding spans the OHLCV window — otherwise `funding_r = 0` on half the trades
  reintroduces the bias P0b removes.
- **Conservatism:** slippage over- rather than under-estimated (§3.5). False
  skepticism is cheap; false confidence is not.
- **Approximations stated, not hidden:** entry-price notional proxy and the
  `(entry_ts, exit_ts]` window are documented approximations, both ~0 impact and
  consistent with the existing fee model.
- **Directional funding is a feature:** it interacts with the short-edge finding and
  must not be averaged away into a symmetric cost.

## 6. Rollout order

1. **PR-1** fetch pagination + `backfill_funding_rates` + `run_backfill` wiring +
   tests. Then run the backfill, verify coverage.
2. **PR-2** `Trade` fields + `pnl_r` + funding_r close logic + funding-series
   plumbing + `slippage_bps` config + tests. Then `make db-update` +
   regression-update in-session; publish the delta.
3. **PR-3** (deferred) live-ledger parity in `_scan_forward`.

## 7. Risks / rollback

- **PR-1:** Binance rate limits during backfill — mitigated by the 0.1s sleep and a
  single-page-per-symbol working set. Geo-block (HTTP 451) is a GH-runner problem
  only; this runs locally with `DATA_SOURCE=binance`. Rollback: backfill is pure
  upsert; no destructive step.
- **PR-2:** Goldens move — intentional and gated by regression-update + the delta
  table. If the delta looks wrong (e.g. a cell improving), stop and inspect the
  funding sign before committing. Rollback: revert the cost terms (default `0.0`
  fields make the engine byte-stable when reverted).

## 8. Open decisions (resolved)

1. **Slippage model:** flat `slippage_bps = 2.0` per leg. ✅ (delegated to
   implementer; §3.5)
2. **PR split:** funding backfill first, then full P0b. ✅
3. **`_scan_forward` parity:** deferred to PR-3. ✅
4. **DB refresh:** run in-session in PR-2. ✅
