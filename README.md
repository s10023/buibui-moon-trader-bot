# Buibui Moon Trader Bot

A tactical crypto trading bot designed for fast, risk-managed, and confident entries — with live price monitoring and position tracking. Built for degens who trade smart. LFG.

---

## Features

### Core Tools

- **Live Price Monitor**
  See real-time prices, 15m / 1h / 24h % changes, and intraday % change since Asia open (8AM GMT+8).
  Color-coded for clarity.

- **Live Position Tracker**
  Track open positions with wallet balance, used margin, PnL, %PnL, and risk exposure per trade.
  Table auto-sorted by your config list.

- **15-Min Telegram Updates** *(optional)*
  Get regular position snapshots via Telegram bot.

- **24/7 Signal Detection Daemon**
  Polls closed candles every 5 minutes, runs 19 strategies (FVG, BOS, liquidity sweep, SMT divergence,
  CVD divergence, and more), and sends Telegram alerts with computed SL/TP levels. Two-layer dedup prevents spam.

- **Manual Multi-Trade Entry Script** *(planned)*
  Open multiple trades (BTC, ETH, alts) in one go, using USD-based sizing with automatic SL & leverage.

---

## Risk Rules (Preconfigured)

| Asset Type  | Leverage | Stop Loss |
|-------------|----------|-----------|
| BTC         | 25x      | 2.0%      |
| ETH         | 20x      | 2.5%      |
| Altcoins    | 20x      | 3.5%      |

Includes max USD-per-trade cap and wallet-level risk protection.

---

## Directory Structure

```text
buibui-moon-trader-bot/
├── buibui.py                        # CLI entry point (argparse)
├── monitor/
│   ├── price_monitor.py             # Price monitor thin wrapper (creates client, calls lib)
│   ├── price_lib.py                 # Pure price monitor business logic
│   ├── position_monitor.py          # Position monitor thin wrapper
│   ├── position_lib.py              # Pure position monitor business logic
│   ├── live_price.py                # WebSocket + Rich live mode for price monitor
│   └── live_position.py             # WebSocket + Rich live mode for position monitor
├── analytics/
│   ├── analytics_runner.py          # Analytics thin wrapper (creates client, opens DB, calls libs)
│   ├── backtest_runner.py           # Backtest thin wrapper (opens DB, loads data, calls libs)
│   ├── backtest_lib.py              # Pure backtest engine: Trade, BacktestResult, run_backtest
│   ├── data_fetcher.py              # Pure Binance Futures API → DataFrames (klines, funding, OI)
│   ├── data_store.py                # Pure DuckDB read/write (schema, upsert, query helpers); tables: ohlcv, funding_rates, open_interest, signals, signal_alert_outcomes, backtest_runs, backtest_trades
│   ├── data_sync.py                 # Backfill + incremental sync orchestration
│   ├── indicators_lib.py            # Pure strategy signal detection (21 active strategies + STRATEGY_REGISTRY + DETECTOR_REGISTRY)
│   ├── signal_config.py             # Pure config loader: SignalWatchConfig + load_signal_config()
│   ├── signal_lib.py                # Pure scan lib: scan_symbol(), run_scan_cycle()
│   ├── signal_runner.py             # Signal daemon thin wrapper (creates client, opens DB, polls)
│   └── backtest_config.py           # BacktestSweepConfig + load_backtest_config() for TOML sweep mode
├── signals/
│   ├── registry.py                  # SignalPlugin TypedDict + SIGNAL_REGISTRY (20 active strategies, with confidence)
│   ├── cooldown_store.py            # Two-layer dedup: candle watermark + cooldown timer
│   └── alert_formatter.py           # SignalEvent dataclass + format_signal_alert() → Markdown with SL/TP/stars
├── web/
│   ├── api/
│   │   ├── main.py                  # FastAPI app: lifespan, CORS, health, router mounts, StaticFiles
│   │   ├── deps.py                  # Dependency factories: get_db, get_client, require_token, require_token_sse
│   │   ├── models/                  # Pydantic request/response models
│   │   └── routers/                 # Route handlers: config, ohlcv, signals, backtest, positions, prices, stream
│   └── ui/                          # Svelte 5 + Vite frontend (Phase 5)
│       ├── package.json
│       ├── vite.config.ts           # Vite config — proxies /api to :8000 in dev
│       ├── tsconfig.json
│       ├── index.html
│       └── src/
│           ├── api.ts               # Typed API client + SSE helper
│           ├── stores/              # Svelte stores: config, strategies, prices, positions
│           ├── pages/               # Chart, Backtest, SignalFeed, Positions, Prices
│           └── components/          # Nav, CandleChart, BacktestResult, PriceRow, PositionRow, …
├── trade/
│   └── open_trades.py               # Multi-trade entry (planned)
├── utils/
│   ├── binance_client.py            # Binance client creation, time sync, config loading
│   ├── config_validation.py         # Validates coins.json schema
│   ├── telegram.py                  # Telegram bot messaging
│   ├── live_store.py                # Shared in-memory store for live WebSocket data
│   └── live_loop.py                 # Shared Rich live display loop logic
├── config/
│   ├── coins.json.example           # Coin list, SL%, leverage per symbol
│   └── signal_watch.toml            # Default signal watch config (timeframes, telegram, min_sl_pct)
├── .env.example                     # Environment variable template
├── .github/
│   └── workflows/
│       ├── lint.yaml                # CI: lint, format, typecheck
│       └── docker-build.yaml        # CI: Docker image build
├── Makefile                         # Dev & run commands
├── Dockerfile                       # Container setup
├── pyproject.toml                   # Poetry dependencies
└── README.md
```

---

## Setup

### 1. Clone this repo

```bash
git clone https://github.com/kng-software/buibui-moon-trader-bot.git
cd buibui-moon-trader-bot
```

### 2. Install dependencies

Requires **Python >= 3.11** and [Poetry](https://python-poetry.org/).

```bash
poetry install --no-root
```

To update later:

```bash
poetry update
```

### 3. Add your API keys

Create a `.env` file with the following variables (see `.env.example` for a template):

```bash
BINANCE_API_KEY=your_binance_api_key_here
BINANCE_API_SECRET=your_binance_api_secret_here

TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
TELEGRAM_CHAT_ID=your_telegram_chat_id_here

# Short-term wallet target for progress bar
WALLET_TARGET=1000
```

### 4. Configure your coins

Copy `config/coins.json.example` to `config/coins.json` and edit to define each symbol's leverage and stop-loss percent.

```sh
cp config/coins.json.example config/coins.json
```

```json
{
  "BTCUSDT": { "leverage": 25, "sl_percent": 2.0, "smt_secondary": "ETHUSDT" },
  "ETHUSDT": { "leverage": 20, "sl_percent": 2.5, "smt_secondary": "BTCUSDT" },
  "SOLUSDT": { "leverage": 20, "sl_percent": 3.5, "smt_secondary": "ETHUSDT" }
}
```

`smt_secondary` is optional. When set, the signal daemon uses it as the correlated
symbol for `smt_divergence` detection on that symbol.

---

## Usage

### Monitor Prices

```bash
poetry run python buibui.py monitor price
```

This will run once and exit by default.
To run in live refresh mode:

```bash
poetry run python buibui.py monitor price --live
```

You can also control how the table is sorted using the `--sort` flag:

```bash
poetry run python buibui.py monitor price --sort change_15m:desc   # Sort by highest 15m % change
poetry run python buibui.py monitor price --sort change_1h:asc     # Sort by lowest 1h % change
```

Supported sort keys:

- `default` — Respect order from `config/coins.json`
- `change_15m` — 15-minute % change
- `change_1h` — 1-hour % change
- `change_4h` — 4-hour % change
- `change_asia` — % change since Asia open (8AM GMT+8)
- `change_24h` — 24-hour % change

Append `:asc` or `:desc` to control the sort direction (defaults to `desc`).

It shows:

- Live price
- 15-minute %, 1-hour %, Asia session %, and 24h %

Example Output:

```text
📈 Crypto Price Snapshot — Buibui Moon Bot

╒════════════╤═════════════╤══════════╤══════════╤══════════════════╤══════════╕
│ Symbol     │ Last Price  │ 15m %    │ 1h %     │ Since Asia 8AM   │ 24h %    │
╞════════════╪═════════════╪══════════╪══════════╪══════════════════╪══════════╡
│ BTCUSDT    │ 62,457.10   │ +0.53%   │ +1.42%   │ +0.88%           │ +2.31%   │
├────────────┼─────────────┼──────────┼──────────┼──────────────────┼──────────┤
│ ETHUSDT    │ 3,408.50    │ +0.22%   │ +1.05%   │ +0.71%           │ +1.74%   │
├────────────┼─────────────┼──────────┼──────────┼──────────────────┼──────────┤
│ SOLUSDT    │ 143.22      │ -0.08%   │ +0.34%   │ +0.11%           │ +0.89%   │
╘════════════╧═════════════╧══════════╧══════════╧══════════════════╧══════════╛

🔽 Sorted by: change_15m (descending)
```

When sorting is active, the sort key and direction are displayed below the table.

### Monitor Positions and PnL

```bash
poetry run python buibui.py monitor position [--sort key[:asc|desc]] [--hide-empty] [--compact]
```

Shows:

- Wallet balance
- Total unrealized PnL
- Colorized risk table with per-trade metrics
- Only open positions are shown. Auto-sorted by your `coins.json` order.
- Use `--hide-empty` to hide rows for symbols with no open positions.
- Use `--compact` to only show wallet summary without the position table.

Example Output:

```text
💰 Wallet Balance: $1,123.15
📊 Total Unrealized PnL: +290.29 (+25.85% of wallet)
🧾 Wallet w/ Unrealized: $1,413.44
⚠️ Total SL Risk: -$412.22 (36.71%)

╒══════════════╤════════╤═══════╤═════════╤═════════╤═════════════════════╤═══════════════════════╤════════╤══════════╤═════════╤════════════╤═══════════╤══════════╕
│ Symbol       │ Side   │   Lev │   Entry │    Mark │   Used Margin (USD) │   Position Size (USD) │    PnL │ PnL%     │ Risk%   │   SL Price │ % to SL   │ SL USD   │
╞══════════════╪════════╪═══════╪═════════╪═════════╪═════════════════════╪═══════════════════════╪════════╪══════════╪═════════╪════════════╪═══════════╪══════════╡
│ BTCUSDT      │ SHORT  │    25 │ 110032  │ 108757  │              595.99 │              14,899.7 │ 174.73 │ +29.32%  │ 52.98%  │   109970.0 │ +0.06%    │ $8.45    │
├──────────────┼────────┼───────┼─────────┼─────────┼─────────────────────┼───────────────────────┼────────┼──────────┼─────────┼────────────┼───────────┼──────────┤
│ ETHUSDT      │ SHORT  │    20 │ 2616.17 │ 2550.10 │              591.11 │              11,822.3 │ 306.29 │ +51.82%  │ 52.54%  │    2614.80 │ +0.05%    │ $6.18    │
╘══════════════╧════════╧═══════╧═════════╧═════════╧═════════════════════╧═══════════════════════╧════════╧══════════╧═════════╧════════════╧═══════════╧══════════╛

🔽 Sorted by: pnl_pct (descending)
```

When sorting is active, the sort key and direction are displayed below the table.

Sorting Options:

```bash
poetry run python buibui.py monitor position --sort pnl_pct:desc   # Sort by highest PnL%
poetry run python buibui.py monitor position --sort sl_usd:asc     # Sort by lowest SL risk
poetry run python buibui.py monitor position --sort default        # Sort by coins.json order (default)
```

Supported sort keys:

- `default` — Respect order from `config/coins.json`
- `pnl_pct` — Sort by unrealized profit/loss % (margin-based)
- `sl_usd` — Sort by USD value at risk based on SL

Append `:asc` or `:desc` to control the sort direction (defaults to `desc`).

> **Known limitation — SL columns require a placed Binance order.**
> The `SL Price`, `% to SL`, and `SL USD` columns are populated by reading
> open `STOP_MARKET` / `STOP` orders from the Binance API. If you manage your
> stop loss mentally or through a third-party tool that does not place an actual
> order on Binance, those columns will show `-` and `Total SL Risk` will show
> `$0.00`. To see SL data, place a stop-loss order directly on Binance (via the
> UI order form or API) before starting the monitor.

### Analytics — Backfill Historical Data

The analytics module stores OHLCV candles, funding rates, and open interest in a local
DuckDB database for offline analysis and strategy backtesting.

**First run — backfill historical data:**

```bash
poetry run python buibui.py analytics backfill --since 2023-01-01
```

Options:

- `--since YYYY-MM-DD` — start date for backfill (default: `2023-01-01`)
- `--symbols BTCUSDT ETHUSDT` — symbols to fetch (default: all coins in `config/coins.json`)
- `--timeframes 1h 4h 1d` — timeframes to fetch (default: `1h 4h 1d`)

**Incremental sync — fetch new candles since last stored:**

```bash
poetry run python buibui.py analytics sync
```

Options:

- `--symbols` / `--timeframes` — same as backfill
- Requires backfill to have been run first for each symbol/timeframe

Data is stored in `analytics.db` (auto-created in CWD).

### Backtest Trading Strategies

Backtest runs in two modes: **single-combo** (one symbol + strategy) or **sweep** (all combinations ranked by avg R).

**Single-combo mode:**

```bash
poetry run python buibui.py backtest --symbol BTCUSDT --strategy fvg --interval 4h --days 90
```

**Sweep mode — TOML config:**

```bash
poetry run python buibui.py backtest --config config/backtest_sample.toml
```

**Sweep mode — CLI flags:**

```bash
poetry run python buibui.py backtest --symbols BTCUSDT ETHUSDT --timeframes 1h 4h --strategies fvg bos --days 90
```

**Available strategies:**

| Strategy | Description | Confidence |
| --- | --- | --- |
| `smt_divergence` | Two correlated assets diverge at a swing high/low | ★★★★★ |
| `fvg` | Fair Value Gap — 3-candle imbalance zone fill | ★★★★☆ |
| `liquidity_sweep` | Wick through a swing high/low with close back inside | ★★★★☆ |
| `eqh_eql` | Equal Highs/Lows: liquidity sweep of a double-top or double-bottom level | ★★★★☆ |
| `funding_reversion` | Extreme positive/negative funding rate → contrarian signal | ★★★★☆ |
| `cvd_divergence` | CVD Divergence — price and buying pressure disagree at a swing extreme | ★★★★☆ |
| `order_block` | ICT Order Block — last up/down candle before displacement; entry on retest | ★★★★☆ |
| `orb` | Opening Range Breakout at NY session open (13:00 UTC) | ★★★☆☆ |
| `bos` | Break of Structure / Change of Character (BOS/CHoCH) | ★★★☆☆ |
| `wick_fill` | Price revisits a significant wick zone | ★★☆☆☆ |
| `marubozu` | Retest of a wickless candle's open price (order block) | ★★☆☆☆ |
| `trend_day` | Trend Day: candle opens near one extreme, closes near the other (large body, tiny leading wick) — **4h/1d only** | ★★★☆☆ |
| `engulfing` | Bullish/Bearish Engulfing: current candle body fully engulfs the prior candle body | ★★☆☆☆ |
| `pin_bar` | Pin Bar: small body with a long rejection wick (≥2× body) | ★★☆☆☆ |
| `inside_bar` | Inside Bar breakout: body contained within prior candle, signal on breakout close | ★★☆☆☆ |
| `hammer_hanging_man` | Hammer (bullish reversal) / Hanging Man (bearish): pin-bar shape with trend context | ★★☆☆☆ |
| `doji` | Doji (open ≈ close) followed by a strongly directional confirmation candle | ★★☆☆☆ |
| `morning_evening_star` | Morning Star (3-candle bullish reversal) / Evening Star (3-candle bearish reversal) | ★★★☆☆ |
| `fib_golden_zone` | Fibonacci golden zone (0.5–0.618) entry after confirmed BOS; SL=swing low, TP=1.618 ext | ★★★★☆ |
| `ote_entry` | Optimal Trade Entry (0.618–0.786) after confirmed BOS — deeper, more selective retracement | ★★★★☆ |
| `seasonality` | Average return by day-of-week, hour, and week-of-month | ★★☆☆☆ |

**Single-combo options:**

- `--symbol BTCUSDT` — primary symbol
- `--strategy fvg` — strategy name from table above
- `--interval 4h` — candle timeframe (default: `4h`)
- `--secondary-symbol ETHUSDT` — required for `smt_divergence`

**Sweep options (TOML or CLI):**

- `--config FILE` — TOML preset file (see `config/backtest_sample.toml`)
- `--symbols BTCUSDT ETHUSDT` — symbols to sweep
- `--strategies fvg bos` — strategies to sweep
- `--timeframes 1h 4h` — timeframes to sweep
- `--min-trades 20` — hide combos below this trade count (default: `20`)

**Shared options:**

- `--days 90` — lookback period in days (default: `90`)
- `--sl-pct 0.02` — stop loss as decimal fraction (default: `0.02` = 2%)
- `--tp-r 2.0` — take profit in R multiples (default: `2.0`)
- `--save` — persist results to `backtest_runs` and `backtest_trades` tables in `analytics.db`

**Single-combo example output:**

```text
Backtest: BTCUSDT 4h — fvg
────────────────────────────────────────────────────
Signals:     42 total, 39 closed
Win rate:    61.5%  (24W / 15L)
Avg R:       +0.61R
Total R:     +23.92R
Max DD:      -4.00R
```

**Sweep example output:**

```text
Backtest Sweep — 3 symbol(s) × 2 timeframe(s) × 4 strategy/ies (90d)
══════════════════════════════════════════════════════════════════
Symbol          TF    Strategy            Win%  Trades   Avg R
──────────────────────────────────────────────────────────────────
BTCUSDT       4h    fvg                  62.5%      48  +1.84R
ETHUSDT       1d    liquidity_sweep      58.3%      24  +1.61R
SOLUSDT       1h    bos                  54.1%      85  +1.42R
──────────────────────────────────────────────────────────────────
  Hidden: 3 combo(s) with < 20 trades
```

> **Note:** Requires backfill to be run first for each symbol/timeframe.

### Signal Watch — 24/7 Strategy Alerts

Runs a polling daemon that scans closed candles every N seconds and sends Telegram alerts
when a strategy fires. Requires `analytics backfill` to have been run first.

```bash
poetry run python buibui.py signal watch
```

**Options:**

- `--config config/signal_watch.toml` — load all options from a TOML file; CLI flags override file values
- `--symbols BTCUSDT ETHUSDT` — symbols to scan (default: all from `coins.json`)
- `--timeframes 4h` — candle timeframes (default: `4h`)
- `--strategies fvg bos` — strategies to run (default: all 19 except `seasonality`)
- `--tp-r 2.0` — R multiplier for TP level in alert messages (default: `2.0`)
- `--telegram` — send alerts via Telegram
- `--state-file signal_state.json` — path to cooldown/watermark state file
- `--min-sl-pct 0.003` — minimum SL distance as a fraction of price (e.g. `0.003` = 0.3%); overrides structural SL if too tight (default: disabled)
- `--smt-pairs BTCUSDT:ETHUSDT,ETHUSDT:BTCUSDT` — per-symbol SMT secondary mappings (overrides `smt_secondary` in `coins.json`)
- `--secondary-symbol ETHUSDT` — *(deprecated, use `--smt-pairs`)* applies one secondary to all scanned symbols

**`day_filter`** suppresses signals on Monday and Friday (ICT weekly cycle — manipulation/distribution days). Off by default; enable in TOML:

```toml
day_filter = true
```

Backtest findings (160d, 3 symbols × 4 TFs × 11 strategies, −29% trade volume):

| Strategy          | Avg ΔWin% | Avg ΔR  | Verdict      |
|-------------------|-----------|---------|--------------|
| `orb`             | +1.9pp    | +0.063R | ✅ benefits  |
| `bos`             | +1.3pp    | +0.039R | ✅ benefits  |
| `wick_fill`       | +0.8pp    | +0.027R | ✅ benefits  |
| `fvg`             | +0.1pp    | +0.004R | ➖ neutral   |
| `liquidity_sweep` | −0.1pp    | −0.002R | ➖ neutral   |
| `smt_divergence`  | −0.3pp    | −0.003R | ➖ neutral   |
| `marubozu`        | −1.2pp    | −0.037R | ❌ hurts     |

Notable: ETHUSDT 4h `bos` is the main cost (−5pp/−0.14R) — Mon/Fri 4h ETH BOS signals were genuinely profitable (likely London Monday expansion). All other `bos` and all `orb` combos improve.

**`smt_trend_filter`** gates `smt_divergence` signals against EMA-50: LONG only above EMA, SHORT only below. On by default (`1`). Backtesting shows counter-trend SMT signals are reliably losing — only ETH/1H retains edge. Disable with `smt_trend_filter = 0` in TOML.

**`trend_day`** detects candles where price opens near one extreme and closes near the other — a large body (≥65% of range) with a tiny leading wick (≤15%). Configurable via `body_pct_min` and `wick_max` params in the Backtest UI. Backtest findings (160d, `day_filter = true`):

| Combo | Win% | Trades | Avg R |
| --- | --- | --- | --- |
| BTCUSDT 4h | 41.5% | 106 | +0.20R |
| SOLUSDT 4h | 37.4% | 123 | +0.07R |
| ETHUSDT 4h | 35.5% | 110 | +0.03R |
| ETHUSDT 1h | 35.1% | 439 | +0.01R |
| BTCUSDT/SOLUSDT 1h | ~34% | 478–487 | −0.01 to −0.06R |
| 15m (all) | 33–34% | 2000–2400 | −0.01 to −0.04R |

4h is the best timeframe — BTCUSDT 4h is consistently the strongest combo (+0.20R). 15m signal volume is high but R is flat-to-negative. 1d combos show strong R (+0.15–0.23R) without `day_filter` but sample sizes fall below `min_trades` when Mon/Fri are excluded.

The `[backtest]` table in `config/signal_watch.toml` controls a per-alert win rate filter:

```toml
[backtest]
mode = "hard"           # "soft": append win rate | "hard": suppress low performers | "off"
days = 160              # lookback window
min_trades = 20         # bypass filter if fewer than this many historical trades
filter_threshold = 0.3  # hard mode: suppress alert if win_rate < this

[smt_pairs]
BTCUSDT = "ETHUSDT"     # primary → secondary for smt_divergence strategy
ETHUSDT = "BTCUSDT"
SOLUSDT = "ETHUSDT"
```

**Example alert (Telegram, soft mode):**

```text
SIGNAL — BTCUSDT 4h
Direction: LONG 🟢  Strategy: `fvg`  ★★★★☆
Reason: `fvg_long@43200.00-43350.00`
Price: 43,260.00  |  01-Apr 21:00 SGT
SL: 42,394.80 (2.0%)  TP: 44,985.60 (4.0% | 2.0x R)
📊 Backtest 90d: 62% win (28 trades)
```

Two-layer dedup prevents alert spam:

- **Candle watermark** — won't re-alert the same candle after a restart
- **Cooldown timer** — 1-hour cooldown per `(symbol, strategy, direction)`

State is persisted to `signal_state.json` so dedup survives container restarts.

> **Note:** Run `analytics backfill` + `analytics sync` first. The daemon auto-backfills
> symbols with no data on first boot, but pre-loading data is faster.

### Web API — FastAPI Backend

A JSON REST API and SSE streaming backend for the Phase 5 Svelte frontend (or any HTTP client).

```bash
# Start the API server (default: http://127.0.0.1:8000)
poetry run python buibui.py web

# Custom host/port with auto-reload for development
poetry run python buibui.py web --host 0.0.0.0 --port 8000 --reload

# Or via Makefile
make buibui-web
```

**Authentication:** All endpoints except `/api/health` require a Bearer token. Set `API_TOKEN` in `.env`.
SSE stream endpoints accept `?token=<API_TOKEN>` query param instead (browser `EventSource` cannot send headers).

**Endpoints:**

| Method | Path | Description |
| ------ | ---- | ----------- |
| `GET` | `/api/health` | Health check — no auth required |
| `GET` | `/api/config` | Per-symbol config from `coins.json` |
| `GET` | `/api/strategies` | All strategy specs with params and confidence |
| `GET` | `/api/ohlcv` | OHLCV candles (`?symbol=&timeframe=&start_ms=&end_ms=`) |
| `POST` | `/api/signals` | Detect strategy signals on historical data |
| `POST` | `/api/backtest` | Run a backtest for a symbol/timeframe/strategy |
| `GET` | `/api/positions` | Fetch open futures positions |
| `GET` | `/api/prices` | Latest price changes for all configured symbols |
| `GET` | `/api/stream/prices` | SSE — live prices every 5 s (`?token=`) |
| `GET` | `/api/stream/positions` | SSE — live positions every 10 s (`?token=`) |

**CORS:** Defaults to `http://localhost:5173` (Vite dev server). Override with `CORS_ORIGINS` env var (comma-separated).

**Notes:**

- The web server opens the DB in **read-only** mode. The signal daemon holds the write lock.
- Requires `analytics backfill` to have been run first for OHLCV/signals/backtest endpoints.
- In production, the API server serves the built Svelte UI from `web/ui/dist/` as static files.

### Web Frontend — Svelte 5

A single-page trading terminal UI. Dark theme, no component library, no SSR.
Pages: Chart (candlesticks + signal markers), Backtest (form + equity curve), Signal Feed (poll + filters), Positions (SSE), Prices (SSE).

```bash
# Install frontend dependencies (first time)
make web-install

# Start dev server with API proxy (http://localhost:5173)
# Set VITE_API_TOKEN in web/ui/.env.local
make web-dev

# Build for production (output to web/ui/dist/)
make web-build

# Build + start API server serving the built UI
make web-full
```

**Dev environment:** Set `VITE_API_TOKEN=<your API_TOKEN>` in `web/ui/.env.local`.
**Production:** `make web-build` then `make buibui-web` — FastAPI serves the UI from `/`.

---

## Makefile Usage

The Makefile provides easy commands for all major actions:

**Lint, Format, Typecheck:**

```bash
make lint           # Lint Markdown and Python (excludes venv)
make lint-py        # Lint + format Python with ruff
make typecheck      # Type check with mypy
```

**Install/Update dependencies:**

```bash
make poetry-install
make poetry-update
```

**Run monitors:**

```bash
# Price monitor
make buibui-monitor-price
make buibui-monitor-price-live
make buibui-monitor-price-telegram

# Position monitor (with flexible sorting)
make buibui-monitor-position                       # Default sort
make buibui-monitor-position SORT=pnl_pct:desc     # Sort by PnL%
make buibui-monitor-position SORT=sl_usd:asc       # Sort by SL risk
make buibui-monitor-position-telegram
```

**Analytics:**

```bash
make buibui-analytics-backfill              # Backfill from 2023-01-01 (default)
make buibui-analytics-backfill SINCE=2024-01-01   # Backfill from custom date
make buibui-analytics-sync                  # Incremental sync
```

**Backtest:**

```bash
make buibui-backtest                                          # BTCUSDT fvg 4h 90d (defaults)
make buibui-backtest SYMBOL=ETHUSDT STRATEGY=bos             # Override symbol and strategy
make buibui-backtest SYMBOL=BTCUSDT STRATEGY=smt_divergence SECONDARY=ETHUSDT
make buibui-backtest SYMBOL=BTCUSDT STRATEGY=fvg INTERVAL=1h DAYS=30 SL_PCT=0.015 TP_R=3.0
make buibui-backtest CONFIG=config/signal_watch.toml SAVE=1  # Full sweep + persist to DB
make buibui-backtest SYMBOL=BTCUSDT STRATEGY=bos SAVE=1      # Single-combo + persist to DB
```

Defaults: `SYMBOL=BTCUSDT`, `STRATEGY=fvg`, `INTERVAL=4h`, `DAYS=90`.
Optional overrides: `SL_PCT`, `TP_R`, `SECONDARY` (required for `smt_divergence`), `SAVE=1` (persist to DB).

**Persisting results for confidence score recalibration:**

Add `--save` (or `SAVE=1` via make) to store aggregate results in `analytics.db`:

```text
backtest_runs        — one row per (symbol, tf, strategy, param combo):
                       win_rate, avg_r, total_r, max_drawdown_r, all params used
backtest_trades      — one row per simulated trade, linked to backtest_runs
signal_alert_outcomes — live forward-test outcomes (renamed from signal_outcomes)
```

Re-running with the same params replaces existing rows (deterministic `run_id` hash),
so you can re-run sweeps freely without accumulating duplicates.

**Query win rate per strategy** (foundation for confidence score recalibration):

```python
import duckdb
from analytics.data_store import get_win_rate_by_strategy

conn = duckdb.connect("analytics.db", read_only=True)
print(get_win_rate_by_strategy(conn))
# strategy  total_closed  total_wins  win_rate_pct  mean_avg_r  combos_run
# fvg              312         198          63.5       +0.42         8
# bos              287         168          58.5       +0.31         8
# ...
```

Only includes combos with ≥ 20 closed trades. Use this to compare against the
current editorial star ratings in `SIGNAL_REGISTRY` and adjust `confidence` values.

**TOML opt-in** — add to `config/signal_watch.toml` or `config/backtest_sample.toml`:

```toml
save_results = true
```

**Web frontend:**

```bash
make web-install       # npm install in web/ui/
make web-dev           # Vite dev server (http://localhost:5173, proxies /api to :8000)
make web-build         # Build Svelte app → web/ui/dist/
make web-preview       # Preview production build locally
make web-full          # Build + start FastAPI serving the UI
```

**Signal watch:**

```bash
make buibui-signal-watch                                              # All symbols, 4h, all strategies
make buibui-signal-watch CONFIG=config/signal_watch.toml             # Load from config file
make buibui-signal-watch CONFIG=config/signal_watch.toml TELEGRAM=1  # Config file + override flag
make buibui-signal-watch SYMBOLS="BTCUSDT ETHUSDT"                   # Specific symbols
make buibui-signal-watch STRATEGIES="fvg bos" TELEGRAM=1             # Specific strategies + Telegram
make buibui-signal-watch TIMEFRAMES="15m 1h 4h" MIN_SL_PCT=0.003 TELEGRAM=1  # SL floor
make buibui-signal-watch STRATEGIES="smt_divergence" SECONDARY=ETHUSDT  # deprecated
```

The daemon wakes at clock-aligned candle boundaries (e.g. 04:00:10, 08:00:10 for `4h`),
so alerts arrive within seconds of the candle close. Optional overrides: `SYMBOLS`,
`TIMEFRAMES`, `STRATEGIES`, `MIN_SL_PCT`, `SECONDARY` (deprecated — set `smt_secondary` in `coins.json` instead), `TELEGRAM=1` (flag).

`smt_divergence` secondaries are configured per-symbol in `coins.json` via the optional
`smt_secondary` field. The `--smt-pairs` CLI flag overrides the config-file values.

All commands use your `.env` file for secrets and config.

---

## Docker

You can use Docker to run the bot and analytics tools in a consistent environment.
`config/coins.json` and `.env` are excluded from the image via `.dockerignore` and
bind-mounted at runtime.

### Makefile targets

```bash
make docker-build                  # Build the image

# Monitors — snapshot (colour output via -t)
make docker-monitor-price          # Run price monitor (snapshot)
make docker-monitor-position       # Run position monitor (snapshot)

# Monitors — live mode (interactive TTY via -it)
make docker-monitor-price-live     # Run price monitor in live mode
make docker-monitor-position-live  # Run position monitor in live mode

# Analytics — analytics.db is bind-mounted from the host
make docker-analytics-backfill                       # Backfill from 2023-01-01
make docker-analytics-backfill SINCE=2024-01-01      # Backfill from custom date
make docker-analytics-sync                           # Incremental sync

# Backtest
make docker-backtest                                          # BTCUSDT fvg 4h 90d (defaults)
make docker-backtest SYMBOL=ETHUSDT STRATEGY=bos
make docker-backtest SYMBOL=BTCUSDT STRATEGY=smt_divergence SECONDARY=ETHUSDT

# Signal watch daemon (interactive, Ctrl+C to stop)
make docker-signal-watch                                      # All symbols, 4h, no Telegram
make docker-signal-watch TELEGRAM=1                           # With Telegram alerts
make docker-signal-watch STRATEGIES="fvg bos"
```

> **First run:** Before running analytics, backtest, or signal-watch Docker commands,
> create the bind-mount files on the host so Docker mounts files (not directories):
>
> ```bash
> touch analytics.db signal_state.json
> ```

### Docker Compose

`docker-compose.yml` is provided for long-running services. Analytics services use the
`analytics` profile and are run with `docker-compose run` (one-shot, not `up`).

```bash
# Long-running services (restart: unless-stopped)
docker-compose up price-monitor
docker-compose up position-monitor
docker-compose up signal-watch      # Signal daemon with --telegram enabled

# One-shot analytics (requires touch analytics.db on first use)
touch analytics.db signal_state.json
docker-compose run --rm analytics-backfill
SINCE=2024-01-01 docker-compose run --rm analytics-backfill
docker-compose run --rm analytics-sync
```

Make sure `config/coins.json`, `.env`, `analytics.db`, and `signal_state.json` exist before
running signal-watch or analytics services.

---

## GitHub Actions

Three workflows run automatically on every push and pull request:

### `lint.yaml` — CI (always active)

Runs on every push to `main` and every PR. Uses path filters so only relevant jobs run:

| Job | Triggers on | Steps |
| --- | --- | --- |
| `markdownlint` | `*.md` changes | markdownlint-cli2 across all Markdown files |
| `lint-typecheck-test` | `*.py` / `pyproject.toml` / `poetry.lock` changes | ruff check, ruff format, mypy, pytest (with coverage), uploads test XML + coverage XML as artifacts |

### `docker-build.yaml` — Docker build check (always active)

Builds the Docker image on every push and PR to catch any `Dockerfile` or dependency issues early.

### `monitor.yaml` — Scheduled position monitor (disabled placeholder)

Commented-out template for running the position monitor on a 15-minute cron schedule via a
**self-hosted runner** on an Oracle Cloud VM. GitHub-hosted runners use rotating IPs that
cannot be whitelisted in Binance — this workflow only makes sense with a static-IP self-hosted
runner. Enable it once the Oracle Cloud VM is set up.

---

## Linting and Type Checking

This project uses:

- **ruff** for Python linting and formatting
- **mypy** for static type checking
- **markdownlint-cli2** for Markdown linting
- **pre-commit** for automated checks on every commit

To check formatting and types locally:

```bash
poetry run ruff check .
poetry run ruff format --check .
poetry run mypy .
poetry run pytest tests/ -v
```

---

## Coming Soon / Ideas

- Trade signal engine (support/resistance + volume traps)
- Auto-close on global SL or high-risk warning
- Telegram command handler (`/price`, `/position`)
