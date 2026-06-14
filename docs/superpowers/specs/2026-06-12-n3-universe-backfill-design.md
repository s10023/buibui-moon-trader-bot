# N3 — Universe + Deep-History Backfill (design)

Date: 2026-06-12 · SoT item N3 · Branch: `feat/n3-universe-backfill`

## Goal

A 25-symbol liquid USDT-M perp universe with deep OHLCV history
(listing → now, timeframes 1h/4h/1d/**1w**; 15m deepened for the 3 majors)
plus funding-rate history, ingested into `analytics.db` with
survivorship-aware symbol-lifecycle metadata and a committed coverage
report — **zero behaviour change** to the live daemon, backtests, or
regression goldens.

Acceptance (from SoT): rows in DB; coverage report; no survivorship trap
(delisted symbols noted, not dropped).

## Decisions taken (user-approved 2026-06-12)

| Decision | Choice |
| --- | --- |
| Universe selection | Mechanical criterion, committed: **top-25 USDT-M perpetuals by 30-day median daily quote volume; status TRADING; stablecoin bases excluded; listed ≥ 1 year** (snapshot 2026-06-12) |
| Universe members | BTC ETH SOL ZEC HYPE XRP DOGE NEAR BNB WLD SUI 1000PEPE ADA TON ONDO TAO LINK AVAX BCH FIL INJ ENA XLM VVV PAXG (all `USDT`-suffixed) |
| TradFi (SPY/MSTR/TSLA/XAU/XAG…) | **Out.** None exist as Binance perps (verified against exchangeInfo 2026-06-12; only PAXG ≈ gold, already admitted by criterion). US equities + metals are the wifey fork's mandate (yfinance, decades of history). The mechanical criterion auto-admits future Binance tradfi perps once they pass the volume + 1y bars |
| User's 11-name watchlist | Strict subset of the criterion top-25 — no conflict |
| 15m depth | Majors only (BTC/ETH/SOL), deepened to listing |
| Execution | Run the backfill in-session once code + tests are green; commit the coverage report in the same PR |

## Non-goals

- No tradfi instruments, no second data source (wifey covers equities/metals).
- No deep open-interest history (Binance free OI is recent-only; CoinGlass is
  post-G1 per the data-cost policy). `sync_open_interest` stays as-is.
- No changes to detectors, signal daemon behaviour, backtest engine, or
  `coins.json` (which drives the live daemon + backtest defaults — universe
  symbols deliberately do NOT go there).
- No 1m data (H4/VPVR cost-check is a separate decision).
- No universe-aware backtest/recalibrate wiring (that is P2's job).

## Components

### 1. `config/universe.toml` (committed) + `analytics/universe.py` loader

```toml
# Research universe — selected by tools/select_universe.py.
# Refresh = rerun the tool, review the diff, commit. Rotated-out symbols
# keep their data; symbol_lifecycle records the history.
[universe]
selected_at = "2026-06-12"
criterion = "top-25 USDT-M perpetuals by 30d median daily quote volume; TRADING; stablecoin bases excluded; listed >= 1y"
symbols = ["BTCUSDT", "ETHUSDT", ...]
```

`analytics/universe.py`: `DEFAULT_UNIVERSE_PATH`, `load_universe(path) -> list[str]`
(validates non-empty, uppercase, deduped). Tiny module — future P2/P3 research
imports it as the single answer to "what is the universe".

### 2. `tools/select_universe.py` (read-only seeding/refresh tool)

Reproduces the criterion executably: fetch `fapi /ticker/24hr` +
`/exchangeInfo`, filter (USDT-quoted perpetual, TRADING, non-stable base,
listed ≥ 1y), rank top-60 candidates by 30-day median daily quote volume from
1d klines, print the top-N as a ready-to-paste `[universe]` block.
Flags: `--top-n 25`, `--min-age-days 365`. Never writes config itself —
refresh stays a deliberate, reviewed commit. stdlib `urllib` only.

### 3. `symbol_lifecycle` table (survivorship guard)

Additive `CREATE TABLE IF NOT EXISTS` in `analytics/store/schema.py`:

```sql
CREATE TABLE IF NOT EXISTS symbol_lifecycle (
    symbol            TEXT   PRIMARY KEY,
    status            TEXT   NOT NULL,  -- Binance status, or 'DELISTED' (synthesised)
    onboard_ms        BIGINT,           -- exchangeInfo onboardDate
    first_checked_ms  BIGINT NOT NULL,
    last_checked_ms   BIGINT NOT NULL,
    delisted_noted_ms BIGINT            -- set once, when first seen absent/non-TRADING
)
```

Accessors in `analytics/store/market_data.py` via the sealed `_upsert`:
`upsert_symbol_lifecycle(conn, df)`, `get_symbol_lifecycle(conn) -> df`.
Re-exported through the `data_store` shim like every other store function.

### 4. Fetch + refresh logic

- `analytics/data_fetcher.py::fetch_futures_symbol_info(client) -> pd.DataFrame`
  — `(symbol, status, onboard_ms)` from `client.futures_exchange_info()`,
  USDT-M perpetuals only. Binance-only (OKX adapter intentionally not
  extended; backfill is the Binance offline path).
- `analytics/data_sync.py::refresh_symbol_lifecycle(conn, client, symbols) -> int`
  — tracked set = existing `symbol_lifecycle` rows ∪ `symbols` for this run.
  Present in exchangeInfo → update status + `last_checked_ms` (preserve
  `first_checked_ms`, clear nothing). Absent → status `DELISTED`,
  `delisted_noted_ms = now` if not already set. **Never deletes OHLCV/funding
  rows.** Called once per run at the top of `run_backfill` and `run_sync`;
  non-fatal on API error (log + continue — lifecycle must not block ingest).

Documented limitation: already-delisted perps are not enumerable from the
free API, so survivorship handling is forward-looking from this snapshot.

### 5. CLI + Makefile

- `buibui analytics backfill|sync --universe` — symbol source becomes
  `config/universe.toml`; mutually exclusive with `--symbols` (argparse
  group). Default behaviour (coins.json fallback) byte-identical.
- Makefile: `universe-backfill` target =
  `backfill --universe --timeframes 1h 4h 1d 1w --since 2019-01-01`.
- Per-symbol resilience in `run_backfill`/`run_sync`: wrap each symbol's
  work in try/except, log the failure, continue, and exit with a summary of
  failed symbols (a 25-symbol run must not die at symbol 20; re-runs are
  idempotent upserts).

### 6. 1w timeframe

Binance accepts `1w` natively and `data_sync.backfill` pagination is
TF-agnostic — **no ingest code change**. Hardening only:
`utils/okx_client.py` gains the `1w → 1W` (UTC-anchored: `1Wutc`) bar
mapping so the keyless OKX path cannot foot-gun on the new TF.

### 7. `tools/export_live_db.py` scoping (cost guard)

Today it copies the **entire** `ohlcv` table into the committed slim
`live_signal.duckdb` that GH Actions checks out hourly. After this PR the
`ohlcv` copy is scoped to: `symbol IN coins.json keys` AND
`open_time >= now − 400 days`. The 400d floor strictly contains today's
content (≈ 9 months), so the GH Actions daemon sees no regression while the
universe/deep rows never reach the public repo. Other (calibration) tables
unchanged.

### 8. `tools/data_coverage_report.py` (acceptance artifact)

Read-only against `analytics.db`. Emits a markdown report:

- Per (symbol × timeframe): rows, first/last day, expected bar count between
  first and last, gap %.
- Per symbol: funding-rate coverage (rows, first/last) and lifecycle status.
- Summary header: universe size, total rows, DB file size.

Flags: `--db`, `--csv <path>`, `--min-gap-pct` highlight threshold.
After the in-session run, output is committed as
`docs/audits/2026-06-12-universe-backfill-coverage.md`.

## Execution plan (post-implementation, same branch)

1. `make universe-backfill` (≈ 2,000 paginated requests, 30–45 min,
   background) — also deepens majors' funding from 2025-09 back to listing
   via the existing `_sync_ancillary` path.
2. `buibui analytics backfill --symbols BTCUSDT ETHUSDT SOLUSDT
   --timeframes 15m --since 2019-01-01` (majors 15m deepening).
3. Coverage report → `docs/audits/…`; sanity-check gaps.
4. DoD gates: `make lint-py` / `typecheck` / `test` / `test-regression`
   (goldens must be byte-identical — backfill is additive and `db-update`
   is config-scoped to the 3 majors at SINCE=2025-09-12).

## Error handling

- `fetch_*` raise on API errors (existing convention); the runner's new
  per-symbol try/except logs and continues, reporting failures at exit.
- Lifecycle refresh failure is non-fatal (warn + proceed with ingest).
- `load_universe` raises a clear error on missing/empty/malformed TOML.

## Testing (TDD, project conventions)

Mock Binance client (`MagicMock`), in-memory DuckDB throughout; no network.

- `fetch_futures_symbol_info`: mapping, perp-only filter, empty response.
- `refresh_symbol_lifecycle`: new symbol insert; status update preserves
  `first_checked_ms`; absent symbol → DELISTED + `delisted_noted_ms` set
  once (idempotent on second refresh); OHLCV rows untouched.
- `load_universe`: happy path, missing file, empty symbols.
- CLI: `--universe` resolves from TOML; `--universe` + `--symbols` rejected;
  defaults unchanged.
- Runner resilience: one symbol raising doesn't stop the loop; failure
  summary returned.
- `export_live_db`: ohlcv filtered by coins.json symbols + 400d floor;
  calibration tables untouched.
- Coverage report: expected-bar math per TF (incl. 1w), gap % on a seeded
  in-memory DB.
- OKX adapter: `1w` bar mapping.

## Risks / notes

- 24h-volume seeding was rejected as hype-polluted (VELVET/BEAT-class
  listings); the 30d-median criterion is the fix and lives in the committed
  tool.
- Universe selection from today's survivors is still point-in-time biased;
  the lifecycle table + criterion file make the bias explicit and forward-
  correcting. Wifey to-do (logged in its memory): XAU/XAG via yfinance.
- `analytics.db` grows by roughly 2M rows (~hundreds of MB) — local,
  gitignored; the committed slim DB is guarded by component 7.
