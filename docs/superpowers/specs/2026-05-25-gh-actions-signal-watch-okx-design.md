# Design: Scheduled signal-watch on GitHub Actions (OKX data source)

**Date:** 2026-05-25
**Status:** Approved design — ready for implementation plan
**Related memory:** `project_github_actions_alerts_data_source.md`

## Goal

Run the signal daemon on an **hourly cron on free GitHub-hosted runners** and fire
Telegram alerts, without paying for or maintaining persistent infra. The blocker has
always been that Binance returns **HTTP 451** from US GitHub runner IPs. This design
swaps the *live alert* data source to **OKX** (verified reachable from US runners) while
keeping Binance as the offline calibration ground truth.

## Verified facts (gathered during design, 2026-05-25)

- **Binance** is geo-blocked from US GH runners (known; HTTP 451).
- **Bybit** is geo-blocked too — **HTTP 403 on all 5** sampled US runner IPs (run 26407655152 + matrix run 26407927420). Rejected.
- **OKX** `/api/v5/market/candles` **and** `/api/v5/market/history-candles` returned **HTTP 200 on all 5** distinct US runner IPs (Iowa, Chicago×3, Phoenix). Keyless. **Chosen.** No geo-fallback needed.
- The live daemon's **DB reads are exactly**: `confidence_ratings`, `backtest_combos`, `backtest_cross_tf_combos`, `ohlcv`, `funding_rates`, `open_interest`, `backtest_cache` (r/w). It **never reads** `backtest_trades` or `backtest_runs` (proven by grep — the only `backtest_trades` mention in the live path is a comment).
- **Funding + OI are not needed live:** no detector consumes `open_interest`; no funding strategy (`funding_extreme`/`funding_reversion`) is enabled in any of the 3 configs; there is no `requires_funding=True` in the registry. (`smt_divergence` needs a *secondary symbol's OHLCV*, which we already have — not funding/OI.)
- The live `min_avg_r` **hard gate recomputes a per-signal backtest at alert time** over a `[backtest].since`-anchored window (`since = 2025-09-12`, ~256 days and growing), keyed by last-closed-candle ts (`scanner.py:597`). It runs against **history**, so committing that history lets the recompute happen locally.
- Real DB today: 139 MB on disk; `backtest_trades` is the single largest table at **57 MB**. Live-needed tables: OHLCV (~4 MB) + 3 calibration tables (~6.75 MB). A freshly-written slim live DB ≈ **~12 MB**.
- Symbols/TFs in scope: **BTCUSDT / ETHUSDT / SOLUSDT × 15m / 1h / 4h / 1d**.
- `buibui signal watch` is a **sleep-loop daemon** (`while not shutdown_requested`); there is **no single-shot flag** today.

## Locked decisions

| Decision | Choice | Rationale |
| --- | --- | --- |
| Live data source | **OKX** (keyless V5 public market data) | Only non-geo-blocked option verified 5/5 from US runners |
| OHLCV strategy | **Approach A** — commit OHLCV in the live DB, incremental-sync per run | OKX paginates ~100 candles/req; full-window fetch every hour (Approach B) would be ~hundreds of requests and grow unbounded |
| Calibration source | **Binance** (existing local DB), exported to a slim DB | Keeps the trading/tuning ground truth; only stars + combo tags cross the exchange boundary |
| Funding / OI | **Dropped** from the live path | No live detector uses them |
| Cadence | **Hourly** (`cron: '0 * * * *'`) + `workflow_dispatch` | User decision |
| Committed-artifact store | **Git LFS** | ~12 MB binary refreshed periodically; keeps git history clean |
| Cross-run state | `signal_state.json` via `actions/cache` | Only cooldown/dedup must persist |

## Architecture — three storage classes

| Class | Contents | Where | Refresh |
| --- | --- | --- | --- |
| **Committed (LFS)** | `live_signal.duckdb`: `confidence_ratings` + `backtest_combos` + `backtest_cross_tf_combos` + `ohlcv` (no `backtest_trades`/`backtest_runs`/`backtest_cache`), ~12 MB | Git LFS | Offline, after local recalibrate |
| **Synced per run** | New OKX candles since the committed DB's last candle, appended to a working copy | Ephemeral runner | Every hourly run |
| **Persisted across runs** | `signal_state.json` (cooldown/dedup only) | `actions/cache` | Restore → run → save |

## Parity analysis

- **EV gate is self-consistent.** `min_avg_r` recomputes its per-signal backtest on the OKX candles in the working DB → OKX-on-OKX, no Binance dependency in the gate.
- **Only stars + combo tags cross exchanges.** `confidence_ratings` and the combo lookups are Binance-derived but applied to OKX candles. Bounded, low-stakes drift (decoration + side tiebreak + DOW suppress, not the primary gate). Can be measured later by a one-off OKX recalibration diff; not blocking.
- **Price basis.** Alerts (entry/SL/TP levels) are computed on OKX prices; the user executes on Binance. For BTC/ETH/SOL the cross-exchange basis is tiny (<~0.05%) — acceptable for alert heads-ups. Documented, not mitigated.

## Components

### 1. OKX data adapter

New adapter behind the existing `analytics/data_fetcher.py` / `data_sync.py` interface, mapping OKX V5 responses to canonical `OHLCV_COLUMNS`. Selected by env var `DATA_SOURCE=okx|binance` (default `binance` so local is unchanged).

- Endpoints: `/api/v5/market/candles` (recent) + `/api/v5/market/history-candles` (deep history, ~100/req, paginate via `after`/`before` ms cursors).
- Symbol map: `BTCUSDT → BTC-USDT-SWAP` (linear perp), `ETHUSDT → ETH-USDT-SWAP`, `SOLUSDT → SOL-USDT-SWAP`.
- Bar map: `15m → 15m`, `1h → 1H`, `4h → 4H`, `1d → 1D` (OKX uses uppercase H/D and UTC bars; verify `1D` vs `1Dutc`).
- OKX returns rows newest-first as string arrays `[ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm]`; adapter reverses to ascending, casts to float, drops the unconfirmed-candle row (`confirm == "0"`).
- Funding/OI fetchers are **not** implemented for OKX (out of scope).

### 2. Single-shot scan mode

Add `--once` to `buibui signal watch`, which sets a new `max_cycles: int | None = None` param on `run_signal_watch` (`None` = loop forever as today; `1` = single cycle). After `max_cycles` cycles, persist cooldown/dedup to `signal_state.json` and exit 0. The single-shot path must skip the interruptible-sleep block entirely. Required because the daemon otherwise sleeps forever.

### 3. Live-DB export tool

`make export-live-db` (or a small script under `tools/`): from the local `analytics.db`, `COPY` the 4 live tables into a fresh `live_signal.duckdb` (excludes `backtest_trades`/`backtest_runs`/`backtest_cache`). Committed via Git LFS. Run after `make db-update` / recalibrate. Document the cadence (e.g. weekly, or whenever ratings change).

### 4. `signal_state.json` persistence

`actions/cache` restore at job start + save at job end, keyed so cooldown/dedup survives between hourly runs (prevents duplicate alerts). Missing cache → start fresh (worst case: one duplicate alert).

### 5. GitHub workflow `.github/workflows/signal-watch.yaml`

- `on: schedule: cron '0 * * * *'` + `workflow_dispatch`.
- Steps: checkout (with LFS) → setup Python 3.11 + Poetry → `poetry install --no-root` → restore `signal_state.json` cache → copy `live_signal.duckdb` to the working DB path → run `DATA_SOURCE=okx poetry run python buibui.py signal watch --once --telegram` (config auto-picks by UTC weekday — already built in) → save `signal_state.json` cache.
- Secrets: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (repo secrets).
- The run incremental-syncs OKX candles from the committed DB's last candle to now before scanning.

### 6. Cleanup

Delete the throwaway `.github/workflows/verify-data-source.yaml` (its job is done — OKX is locked).

## Data flow

**Offline (local Binance machine, periodic):** existing pipeline → recalibrate → `make export-live-db` → commit `live_signal.duckdb` via LFS → push.

**Hourly (GH runner):** restore state cache → copy committed DB to working path → `signal watch --once` with `DATA_SOURCE=okx`:
incremental OKX-sync new candles → detect on latest candle → per-signal backtest on OKX history → `min_avg_r` gate → decorate with committed stars/combos → cooldown dedup → Telegram → save state cache.

## Error handling

- OKX fetch failure → retry with backoff; if still failing, log + exit non-zero so the run shows red (no silent gaps).
- Missing/cold state cache → start fresh (at most one duplicate alert).
- Missing LFS DB → fail fast with a clear message.
- (Geo-fallback intentionally **not** built — OKX verified 5/5 reliable.)

## Testing

- Unit: OKX response → `OHLCV_COLUMNS` mapping (mocked HTTP, no live calls per repo rules); symbol/bar mapping; newest-first reversal + unconfirmed-candle drop; pagination cursor logic.
- Unit: `--once` runs exactly one cycle and exits; cooldown persisted.
- Parity: OKX adapter output frame shape == Binance fetcher frame shape.
- `make lint-py` + `make typecheck` + `make test` green.

## Out of scope (follow-ups)

- OKX funding/OI adapters (only if a funding/OI strategy is ever enabled live).
- Recalibrating on OKX data (only if stars/combo drift proves material).
- Position monitor on GH Actions (separate `monitor.yaml` concern).
- Migrating the live trade-execution path (alerts only).

## Open risks

- OKX `history-candles` max history depth per `instId` — confirm it reaches back to 2025-09-12 for all TFs (verify during adapter build; if not, the committed DB already holds the deep history and we only need recent candles per run, so this is low-risk under Approach A).
- OKX `1D` bar UTC alignment vs Binance daily `open_time` — verify candle boundaries match so detectors and `day_filter` agree.
- LFS bandwidth quota (small; ~12 MB refreshed weekly is well within free tier).
