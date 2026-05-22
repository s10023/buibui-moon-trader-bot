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
  Polls closed candles every 5 minutes, runs 20 strategies (FVG, BOS, liquidity sweep, SMT divergence,
  CVD divergence, and more — 19 actionable, plus `seasonality` stats), and sends Telegram alerts with computed SL/TP levels. Two-layer dedup prevents spam.
  Alerts include a 2-line statistical context: direction-aware P1/P2 day bias, ADR consumed %, per-DOW empirical peak hour, and weekly P2 timing probability.

- **Statistical Context Engine** *(new)*
  BrighterData-style probability dashboard computed from historical OHLCV. Per-symbol stats:
  P1/P2 daily (was low made before high? by day-of-week), hourly extreme distribution (empirical kill zones),
  average daily range + today's consumed %, day-of-week patterns, session (Asia/London/NY) breakdown, and
  weekly P1/P2, avg return by day-of-week, and weekly P2 timing with P1 flip risk. Cached in DB, served via `GET /api/stats/{symbol}`, shown on the Stats web page.

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
│   ├── data_store.py                # Pure DuckDB read/write (schema, upsert, query helpers); tables: ohlcv, funding_rates, open_interest, signals, signal_alert_outcomes, backtest_runs, backtest_trades, backtest_cache, stats_cache
│   ├── data_sync.py                 # Backfill + incremental sync orchestration
│   ├── strategies/                  # Per-detector strategy package (22 active strategies + STRATEGY_REGISTRY + DETECTOR_REGISTRY)
│   ├── signal_config.py             # Pure config loader: SignalWatchConfig, BacktestFilterConfig, BiasConfig, ComboConfig; TOML extends support
│   ├── signal_lib.py                # Pure scan lib: scan_symbol(), run_scan_cycle(); injects StatsContext into alerts
│   ├── signal_runner.py             # Signal daemon thin wrapper (creates client, opens DB, polls)
│   ├── signal_test_runner.py        # Historical replay: no DB writes, no cooldown; --at / --lookback
│   ├── stats_lib.py                 # Pure stats lib: compute_p1p2_daily, compute_hourly_extremes, compute_adr, compute_dow_patterns, compute_session_breakdown, compute_weekly_p1p2, compute_all → StatsBundle
│   ├── backtest_config.py           # BacktestSweepConfig + load_backtest_config() for TOML sweep mode
│   ├── param_sweep.py               # WFO sweep lib: run_param_sweep / run_strategy_audit; parallelized via ProcessPoolExecutor
│   ├── digest_lib.py                # 12 pre-canned SQL queries; run_digest; DigestScope; powers buibui digest
│   ├── cme_gap_lib.py               # CME gap detection + alert warning helper
│   ├── zones_lib.py                 # Structural zone extraction (geometry only): FVG, OB, EQH/EQL, BOS, Fib, OTE, swing points
│   ├── recalibrate_lib.py           # Compute + write star ratings to DB or source
│   ├── recalibrate_runner.py        # Recalibrate thin wrapper
│   ├── perf_timer.py                # timed(label) context manager
│   └── regime.py                    # Regime classifier (trend/range/high_vol/unknown); §6 of v2 redesign; Phase 2 live gate (soft mode)
├── signals/
│   ├── registry.py                  # SignalPlugin TypedDict + SIGNAL_REGISTRY (20 actionable strategies; seasonality/funding_reversion/fibonacci_retracement excluded)
│   ├── cooldown_store.py            # Two-layer dedup: candle watermark + cooldown timer
│   └── alert_formatter.py           # SignalEvent, StatsContext, ConfluenceData; 6-section alert layout; W1–W8 candle warnings
├── web/
│   ├── api/
│   │   ├── main.py                  # FastAPI app: lifespan, CORS, health, router mounts, StaticFiles
│   │   ├── deps.py                  # Dependency factories: get_db, get_client, require_token, require_token_sse
│   │   ├── models/                  # Pydantic request/response models
│   │   └── routers/                 # Route handlers: config, ohlcv, fib, signals, backtest, positions, prices, stream, stats, zones
│   └── ui/                          # Svelte 5 + Vite frontend (Phase 5)
│       ├── package.json
│       ├── vite.config.ts           # Vite config — proxies /api to :8000 in dev
│       ├── tsconfig.json
│       ├── index.html
│       └── src/
│           ├── api.ts               # Typed API client + SSE helper
│           ├── stores/              # Svelte stores: config, strategies, prices, positions
│           ├── pages/               # Chart, Backtest, SignalFeed, Positions, Prices, Stats
│           └── components/          # Nav, CandleChart, BacktestResult, PriceRow, PositionRow, …
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

## Stats Dashboard

The Stats page (`#/stats`) shows BrighterData-style probability tables computed from historical 1h OHLCV data. Each card has a **?** button that explains what it shows and how to use it in trading decisions.

| Card | What it answers | Interactions |
| ---- | --------------- | ------------ |
| **P1/P2 Daily** | Was the daily low or high made first? Per-day-of-week breakdown. Also shows "P1 strong %" — fraction of P1 candles where the P1-direction wick was < 20% of range (closed near the extreme). | Toggle **Low First / High First** for bullish/bearish context. Today's DOW highlighted. |
| **Average Daily Range (ADR)** | ADR(14) = 2-week average (short-term vol). ADR(30) = monthly baseline. Today's range consumed as a progress bar; turns red + warning if ≥80%. | — |
| **Hourly Extreme Distribution** | Which MYT hour (0–23) most often produces the daily high (green) vs low (red). Empirically-derived kill zones. | Current MYT hour highlighted with accent border. |
| **Day-of-Week Patterns** | Average range (relative bar), bull/bear split bar + %, avg return, and **Str H / Str L** columns — fraction of days each day-of-week formed a strong high (upper wick < 20% of range) or strong low (lower wick < 20% of range). | Today's DOW row highlighted. |
| **Session Breakdown** | Which session (Asia 00–07 / London 14–21 / NY 20–03 MYT) most often makes the daily high vs low. Columns don't sum to 100% — London/NY overlap (20–21 MYT) is counted in both. | Active sessions shown with a pulsing ● indicator. |
| **Weekly P1/P2** | Which day of the week most commonly forms the weekly high vs low, shown as a per-DOW bar chart. | Toggle **Bear** (when does weekly HIGH form?) or **Bull** (when does weekly LOW form?). Defaults to Bear. Today's DOW highlighted. |
| **Avg Return by Day** | Average `(close−open)/open` per weekday — shows which days are historically bullish or bearish. Bars grow from bottom; green = positive, red = negative. | Today's DOW highlighted. |
| **Weekly P2 Timing** | 5-column per-DOW table: how often the weekly low/high is still ahead after each DOW (still-ahead %) and how often the running P1 gets undercut later in the week (flip risk %). Conditioned view shows P(P2 still ahead \| P1 direction, DOW). | Today's DOW highlighted; flip risk ≥ 30% shown in amber. Toggle **All / Bullish P1 / Bearish P1** to condition on which extreme was set first. |
| **Daily Distance** | Given today's current high-low range (as × ADR14), P(historical daily move > today's). Gap to 80th-percentile daily move. High exceedance = today is already an extreme day, don't chase. Live — recomputed on every page load. | — |
| **P1 Wick Rank** | Current week's P1 wick (normalised by open × ADR14) ranked against all historical P1 wicks. Shows exceedance %, direction (Bullish/Bearish P1), and a rank bar. "P1 not yet set" shown if both weekly extremes haven't formed yet. Live — recomputed on every page load. | — |

A 2-line summary of the most actionable stats is injected into every Telegram signal alert:

```text
📐 Mon closes bullish 67% · Daily low set first 69% of Mondays · ADR 4.3% (82% used)
⏰ Daily high typically peaks ~23:00 MYT on Mondays · Weekly low: 78% of weeks still ahead
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

> **Known limitation — SL/TP columns require standalone Binance orders.**
> `SL Price`, `% to SL`, `SL USD`, and `TP Price` are populated by reading open
> `STOP_MARKET` / `STOP` and `TAKE_PROFIT_MARKET` / `TAKE_PROFIT` orders from the
> Binance API. Binance's **Position TP/SL** feature (set at order opening or via the
> TP/SL tab on a position) is stored internally by Binance and is **not exposed
> through any public REST API** — no endpoint returns this data. Those columns will
> show `–` and `Total SL Risk` will show `$0.00` when Position TP/SL is used.
> To see SL/TP data in the monitor, place them as standalone orders from the Binance
> order form instead of using the TP/SL tab.

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
poetry run python buibui.py backtest --config config/signal_watch.toml
```

**Sweep mode — CLI flags:**

```bash
poetry run python buibui.py backtest --symbols BTCUSDT ETHUSDT --timeframes 1h 4h --strategies fvg bos --days 90
```

**Available strategies:**

| Strategy | Description | Confidence |
| --- | --- | --- |
| `smt_divergence` | Two correlated assets diverge at a confirmed pivot swing high/low (centred 11-candle window) | ★★★★☆ |
| `fvg` | Fair Value Gap — 3-candle imbalance zone fill with EMA-50 trend filter | ★☆☆☆☆ |
| `liquidity_sweep` | Fakeout above/below a pivot swing high/low that extends to the 1.13 or 1.27 fib extension of the prior range; entry on close rejection at that level | ★☆☆☆☆ |
| `eqh_eql` | Equal Highs/Lows: liquidity sweep of a double-top or double-bottom; both pivots must be intact (price must not have breached the level between their formations) | ★☆☆☆☆ |
| `funding_reversion` | Extreme positive/negative funding rate → contrarian signal | ★☆☆☆☆ |
| `cvd_divergence` | CVD Divergence — price and buying pressure disagree at a swing extreme | ★☆☆☆☆ |
| `order_block` | ICT Order Block — last up/down candle before displacement; entry on retest | ★☆☆☆☆ |
| `orb` | Opening Range Breakout — first 2 candles of UTC day form the range; breakout enters | ★☆☆☆☆ |
| `bos` | Break of Structure / Change of Character (BOS/CHoCH) | ★☆☆☆☆ |
| `wick_fill` | Price revisits a significant wick zone | ★☆☆☆☆ |
| `marubozu` | Retest of a wickless candle's open price (order block) | ★☆☆☆☆ |
| `trend_day` | Trend Day: candle opens near one extreme, closes near the other (large body, tiny leading wick) — **4h/1d only** | ★☆☆☆☆ |
| `engulfing` | Bullish/Bearish Engulfing: current candle body fully engulfs the prior candle body | ★★☆☆☆ |
| `pin_bar` | Pin Bar: small body with a long rejection wick (≥2× body) | ★★☆☆☆ |
| `inside_bar` | Inside Bar breakout: body contained within prior candle, signal on breakout close | ★★☆☆☆ |
| `hammer_hanging_man` | Hammer (bullish reversal) / Hanging Man (bearish): pin-bar shape with trend context | ★☆☆☆☆ |
| `doji` | Doji (open ≈ close) followed by a strongly directional confirmation candle | ★★☆☆☆ |
| `morning_evening_star` | Morning Star (3-candle bullish reversal) / Evening Star (3-candle bearish reversal) | ★★☆☆☆ |
| `fib_golden_zone` | Fibonacci golden zone (0.5–0.618) entry after confirmed BOS; SL=swing low, TP=1.618 ext | ★☆☆☆☆ |
| `ote_entry` | Optimal Trade Entry (0.618–0.786) after confirmed BOS — deeper, more selective retracement | ★☆☆☆☆ |
| `seasonality` | Average return by day-of-week, hour, and week-of-month | ★★☆☆☆ |
| `ema` | EMA pullback continuation (Variant A): trend (slow EMA + slope) + regime gate, pullback wick into fast EMA, body-fraction trigger | ★★★☆☆ |

**Single-combo options:**

- `--symbol BTCUSDT` — primary symbol
- `--strategy fvg` — strategy name from table above
- `--interval 4h` — candle timeframe (default: `4h`)
- `--secondary-symbol ETHUSDT` — required for `smt_divergence`

**Sweep options (TOML or CLI):**

- `--config FILE` — TOML preset file (see `config/signal_watch.toml`)
- `--symbols BTCUSDT ETHUSDT` — symbols to sweep
- `--strategies fvg bos` — strategies to sweep
- `--timeframes 1h 4h` — timeframes to sweep
- `--min-trades 20` — hide combos below this trade count (default: `20`)

**Shared options:**

- `--days 90` — lookback period in days (default: `90`)
- `--since YYYY-MM-DD` — anchor start date for stable, comparable runs (e.g. `--since 2025-09-12`). Overrides `--days` when set — use this for saved runs so results don't drift day-to-day.
- `--sl-pct 0.02` — stop loss as decimal fraction (default: `0.02` = 2%)
- `--tp-r 2.0` — take profit in R multiples (default: `2.0`)
- `--fee-pct 0.0005` — taker fee per leg (default: `0.0`; use `0.0005` for 0.05% Binance taker)
- `--day-filter` — suppress Monday and Friday signals before backtesting (ICT weekly cycle)
- `--save` — persist results to `backtest_runs` and `backtest_trades` tables in `analytics.db`
- `--combo` — run co-firing confluence backtests across all strategy pairs; detects pairs within `--window` candles
- `--window N` — co-firing window: ±N candles for strategy pair detection (default: `5`)
- `--cross-tf` — run cross-TF co-firing backtests (HTF sets context, LTF is entry); sweeps all symbol × HTF/LTF-pair × strategy pairs
- `--htf-ltf 4h:15m 4h:1h` — HTF:LTF pairs to sweep (default: all 5 canonical pairs)
- `--window-hours N` — cross-TF lookback in hours: HTF signal must have fired within N hours of the LTF signal (default: `4.0`)
- `--workers N` — parallel workers for combo backtest, one per symbol×TF chunk (default: `min(4, cpu_count-1)`); pass `1` for serial mode

**Live-parity options (T6, PR-1 plumbing + PR-2 regime + PR-3 direction_filter + F8 HTF EMA + PR-4 ADR bias + PR-4b conflict resolver + PR-5 cooldown):**

- `--live-parity` — master switch; expands to enabling every per-gate flag below
- `--with-regime` / `--without-regime` — **wired in PR-2.** Ports the live `_apply_regime_gate` into the backtest engine via per-signal HTF regime lookup (each historical signal is evaluated against the regime active at its own `open_time` — true replay parity). Reads `[bias.regime]` from the same TOML the signal daemon uses (`enabled`, `mode` soft/hard, `htf_tf`, `enabled_regimes`, `per_strategy`). When a sweep `--config` is supplied, the runner pre-classifies `bias.regime_htf_tf` candles per symbol once and threads the series through every `run_backtest()` call.
- `--with-direction-filter` / `--without-direction-filter` — **wired in PR-3.** Ports the live `_apply_direction_filter_gate` — pure per-event flag check on `[strategy_params.<name>].suppress_long` / `.suppress_short`. Reads `[bias.direction_filter]` (`enabled`, `mode` soft/hard) + the live `[strategy_params]` block from the same TOML.
- `--with-f8-htf-ema` / `--without-f8-htf-ema` — **wired in PR-3.** Ports the live `_apply_htf_ema_gate` via per-signal HTF slope lookup. The runner pre-computes an EMA slope series for every distinct `(anchor_tf, period, slope_lookback)` anchor needed by `[bias.htf_ema]` + `[bias.htf_ema.per_strategy]`, indexed by HTF open_time; the engine uses the same "last fully closed HTF candle at signal time" semantics as the regime gate.
- `--with-adr-bias` / `--without-adr-bias` — **wired in PR-4.** Ports the live `_filter_signals_by_adr` with per-direction exemption. Honours both strategy-wide `adr_exempt` and the per-direction `adr_exempt_long` / `adr_exempt_short` (PR #380) from `[strategy_params.<name>]` — propagating Bucket C's directional exemption findings into backtest replay. The engine splits signals into (exempt, non-exempt) per direction, applies the live ADR filter on the non-exempt slice only, and concats back ordered by `open_time`. The legacy runner-side ADR pre-filter is skipped when the gate is on to avoid double-filtering.
- `--with-conflict-resolver` / `--without-conflict-resolver` — **wired in PR-4b.** Ports the live conflict resolver via runner-level cross-strategy pooling: the runner pools detected signals across all swept strategies for each (symbol, tf) candle, calls `_apply_conflict_resolver` (lifted into `analytics/signal/gates.py` in PR-4), then redistributes survivors back into per-strategy signal frames before `run_backtest()`. The confidence tiebreaker is the per-(strategy, tf, direction) `avg_r` from the `confidence_ratings` table (config keyed on the TOML stem, e.g. `signal_watch`) — missing keys default to 0.0, so unrated strategies rank below any rated competitor. Run `make db-update` (recalibrate) before relying on the gate so ratings reflect current data.
- `--with-cooldown` / `--without-cooldown` — **wired in PR-5.** Engine-side N-bar cooldown keyed by `(symbol, timeframe, strategy, direction)`. State is instantiated inside `run_backtest()` so each call gets a fresh ledger (per T6 plan Q1) — directly replays the live candle-watermark / per-strategy suppression behaviour against historical signals. Baked-in defaults: 15m=4, 1h=3, 4h=2, 1d=1 bars; override via `[backtest.live_parity.cooldown_bars]` TOML sub-table. After each fire, subsequent signals on the same key within `cooldown_bars × tf` are dropped; opposing-direction signals are not suppressed.
- TOML equivalent: `[backtest.live_parity]` block with `enabled` / `regime` / `direction_filter` / `f8_htf_ema` / `adr_bias` / `conflict_resolver` / `cooldown` keys + optional `[backtest.live_parity.cooldown_bars]` per-tf sub-table. CLI `--without-<gate>` wins over TOML; `--live-parity --without-cooldown` cleanly disables a single gate. **All defaults `False` remain a true no-op — regression goldens unchanged.**

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

### Recalibrate — Update Confidence Star Ratings

Reads `backtest_runs` from `analytics.db` and maps real avg R per strategy to 1–5 star
confidence ratings. Each signal-watch TOML config gets its own set of ratings stored in the
`confidence_ratings` DB table — stars are no longer shared globals baked into source code.

```bash
# Per-config workflow (preferred — no source patching)
poetry run python buibui.py recalibrate --config config/signal_watch.toml            # dry-run
poetry run python buibui.py recalibrate --config config/signal_watch.toml --apply    # write to DB
poetry run python buibui.py recalibrate --config config/signal_watch_weekdays.toml --apply

# Legacy: write global ratings directly to analytics/strategies/_registry.py (still works, no --config needed)
poetry run python buibui.py recalibrate --apply
poetry run python buibui.py recalibrate --min-trades 20 --apply
```

`--config` derives `day_filter` and `config_name` from the TOML file, then filters
`backtest_runs` to only runs matching that `day_filter` before computing stars.
`--apply` with `--config` writes to the `confidence_ratings` table keyed by config name —
signal watch loads these at startup so each TOML config uses its own calibrated stars.
When the active config's `day_filter` changes between runs, recalibrate's stale-row
pruner removes ratings written under the previous scope so the daemon never reads zombies.

**Day-filter scopes.** The three production configs partition the calendar:

| Config | `day_filter` | Days |
| --- | --- | --- |
| `signal_watch.toml` | `tue_thu` | Tue, Wed, Thu |
| `signal_watch_weekdays.toml` | `mon_fri` | Mon, Fri |
| `signal_watch_all.toml` | `weekend` | Sat, Sun |

`buibui signal watch` with **no `--config`** auto-picks the matching config based on
today's **UTC weekday**. Explicit `--config X` always wins. The pick is made once
at daemon startup — restart after a UTC midnight to refresh.

UTC (not local time) so the picker agrees with `day_filter` by construction —
each config's `day_filter` evaluates every candle's UTC `open_time`, so picking
by UTC weekday guarantees the picked config will accept the candles the daemon
will actually receive.

**Star rating thresholds (avg R):**

| avg R | Stars |
| --- | --- |
| < 0 | ★☆☆☆☆ |
| 0 – 0.2 | ★★☆☆☆ |
| 0.2 – 0.5 | ★★★☆☆ |
| 0.5 – 0.9 | ★★★★☆ |
| ≥ 0.9 | ★★★★★ |

Strategies with fewer than `--min-trades` (default: 10) closed trades are excluded and shown as `(no data)`.

**Full workflow:**

```bash
# After any backtest sweep with SAVE=1 — recalibrate each config independently
make buibui-backtest CONFIG=config/signal_watch.toml SAVE=1
make buibui-recalibrate CONFIG=config/signal_watch.toml             # preview
make buibui-recalibrate CONFIG=config/signal_watch.toml APPLY=1    # write to DB
make buibui-signal-watch CONFIG=config/signal_watch.toml            # restart; loads DB stars
```

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
- `--strategies fvg bos` — strategies to run (default: all 20 actionable from `SIGNAL_REGISTRY`)
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

**`smt_trend_filter`** gates `smt_divergence` signals against EMA-50: LONG only above EMA, SHORT only below. On by default (`1`). Backtesting shows counter-trend SMT signals underperform. Post-A18 pivot fix, all TF combos are positive except BTCUSDT 4h (suppressed by hard-mode backtest filter at runtime). Disable with `smt_trend_filter = 0` in TOML.

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

The `[backtest]` table in `config/signal_watch.toml` controls a per-alert expected-value filter:

```toml
[backtest]
mode = "hard"           # "soft": append win rate | "hard": suppress low performers | "off"
days = 200              # lookback window
min_trades = 12         # global fallback — applied to directional trade count (longs for LONG alerts, shorts for SHORT)
min_trades_15m = 20     # per-TF overrides; calibrated from DB p25 directional counts
min_trades_1h  = 12
min_trades_4h  = 5
min_trades_1d  = 2
min_avg_r = 0.0         # hard mode: suppress alert if directional avg_r < this (positive EV gate)
fee_pct = 0.0005        # taker fee applied to inline backtest (falls back to top-level fee_pct)

[smt_pairs]
BTCUSDT = "ETHUSDT"     # primary → secondary for smt_divergence strategy
ETHUSDT = "BTCUSDT"
SOLUSDT = "ETHUSDT"
```

**`[strategy_params]`** overrides `tp_r`, `sl_pct`, and volume/ADR gates per strategy, per TF, and per symbol.
Resolution order: **symbol+TF → symbol → TF → strategy → global**.

```toml
[strategy_params.engulfing]
tp_r = 3.0          # all symbols, all TFs

[strategy_params.engulfing.SOLUSDT]
tp_r_4h = 4.0       # SOL 4h only; other SOL TFs fall back to strategy-wide 3.0

[strategy_params.doji]
tp_r = 3.0          # all symbols fallback

[strategy_params.doji.BTCUSDT]
tp_r_15m = 3.5      # BTC 15m only

[strategy_params.doji.ETHUSDT]
tp_r_15m = 4.5      # ETH 15m only — diverges from BTC
```

Per-symbol blocks use `[strategy_params.STRATEGY.SYMBOL]` sub-table syntax, placed after their
parent `[strategy_params.STRATEGY]` block. Any symbol not listed falls through to TF-level or
strategy-wide.

Two boolean flags are also supported per strategy block:

- **`adr_exempt = true`** — skip the ADR bias gate for this strategy (use for breakout/continuation strategies that need range momentum)
- **`adr_exempt_long = true/false`** / **`adr_exempt_short = true/false`** — per-direction override (Bucket C); when set, wins over the strategy-wide `adr_exempt`. Mirrors the live `signal_config` schema so the same TOML applies to live signal selection and backtest replay.
- **`[strategy_params.<name>.adr_exempt_long_per_tf]`** / **`adr_exempt_short_per_tf`** — per-tf-direction override (Bucket C follow-up); a sub-table keyed by timeframe string (`"15m"`, `"1h"`, `"4h"`, `"1d"`) mapping to bool. Precedence is per-tf-direction > per-direction > strategy-wide. Lets a single (tf, direction) cell flip without dragging the same direction on other tfs (e.g. `bos 15m short mon_fri` exempt, `bos 4h short mon_fri` kept).
- **`volume_suppress = true/false`** — override the global `[backtest].volume_suppress` for this strategy. `true` drops signals on candles with volume < 1.5× the 20-candle rolling mean; `false` explicitly keeps them even when the global flag is on. Omit to inherit the global default (off). Decision is data-driven: run `make buibui-backtest` and check the "Volume Impact" table for each strategy — suppress when normal-vol avg R clearly exceeds low-vol avg R (Δ > 0.05R).

The inline backtest (computed each scan cycle per firing signal) respects all config values:
`fee_pct`, `day_filter`, `sl_pct`, and `cooldown_seconds` are now all read from TOML and
applied correctly — results stored in `backtest_runs` match what the live filter uses.

**`[bias]`** — bias chain applied between detector fan-out and Telegram dispatch.
Order: `regime` (Step −1) → `htf_ema` / F8 (Step 0) → `adr_suppress_threshold` → `dow_soft_suppress`.

```toml
[bias]
# ADR directional gate: when today's range has consumed >= this fraction of ADR-14,
# suppresses only the chasing direction (LONGs when move was up, SHORTs when move was
# down). Reversal signals at the extreme still fire. Falls back to blanket suppress when
# move direction is unknown.
adr_suppress_threshold = 0.80   # e.g. 0.80 = suppress chasing direction when 80%+ consumed

# DOW soft suppress: reduce confidence by 1 star when signal direction opposes today's
# historical DOW avg return (from stats_lib). Signal still fires but shows lower conviction.
dow_soft_suppress = false
dow_suppress_min_abs_return = 0.005  # dead-band: ±0.5% to avoid noise from near-zero days

# F8 HTF EMA directional gate — suppresses signals fighting the HTF trend.
# See `config/strategy_params.toml` for the live anchor mix and per-strategy overrides.
[bias.htf_ema]
enabled = true
mode = "hard"                   # "soft" = log only; "hard" = drop opposing signals
default_tf = "4h"               # default anchor TF; per_strategy entries can override
default_period = 50
default_slope_lookback = 10
deadband_pct = 0.003            # |slope| < 0.3% over slope_lookback bars → allow

# v2 Phase 2 regime gate (per redesign §6) — Step −1, runs before F8.
# Drops signals whose strategy type is not enabled in the current 4h regime.
# `unknown` regime and cache misses always fall open.
[bias.regime]
enabled = true
mode = "soft"                   # ship soft first; flip to "hard" after ≥2 weeks observation
htf_tf = "4h"                   # regime classified off 4h candles

[bias.regime.enabled_regimes]
trend         = ["trend"]                       # continuation only in trend
fib           = ["trend"]                       # BOS-anchored continuation
flow          = ["trend", "range", "high_vol"]
structural    = ["trend", "range", "high_vol"]
price_action  = ["trend", "range", "high_vol"]
candlestick   = ["trend", "range", "high_vol"]
session       = ["trend", "range", "high_vol"]

[bias.regime.per_strategy]
bos = ["high_vol", "range"]     # routing-audit-corrected (PR #366); trend regime was bos's worst
fib_golden_zone = ["range", "high_vol"]   # inverted off §6 default (PR #354)

# T2c per-strategy directional suppress — Step −0.5 of the bias chain.
# Drops signals matching [strategy_params.<name>].suppress_long / .suppress_short.
# Cheapest filter — pure per-event flag, no HTF / regime data.
[bias.direction_filter]
enabled = true
mode = "soft"                   # flip to "hard" after ≥2 weeks of soft-mode logs

[strategy_params.bos]
suppress_long = true            # T2c: long-side avg_r=−0.268R on n=34,767 (routing audit 2026-05-13)
```

ADR + DOW gates read from the per-symbol `StatsContext` computed each cycle (same data shown
in the Telegram stats footer). F8 reads from a slope cache pre-computed once per cycle from
HTF candles. Regime reads from a `dict[symbol, Regime]` classified once per cycle off the
`htf_tf` candles. If any data is unavailable for a symbol, the corresponding gate is silently
skipped (fall-open).

**Example alert (Telegram, soft mode):**

```text
SIGNAL — BTCUSDT 4h
Direction: LONG 🟢  Strategy: `fvg`  ★★★★☆
Reason: `fvg_long@43200.00-43350.00`
Price: 43,260.00  |  01-Apr 21:00 SGT
SL: 42,394.80 (2.0%)  TP: 44,985.60 (4.0% | 2.0x R)
📊 Backtest 90d [↑]: 62% win · avg +1.4R (18 longs)
```

Two-layer dedup prevents alert spam:

- **Candle watermark** — won't re-alert the same candle after a restart
- **Cooldown timer** — 1-hour cooldown per `(symbol, strategy, direction)`

State is persisted to `signal_state.json` so dedup survives container restarts.

> **Note:** Run `analytics backfill` + `analytics sync` first. The daemon auto-backfills
> symbols with no data on first boot, but pre-loading data is faster.

### Signal Test — Fire a Test Alert From Historical Data

Runs a detector against real historical OHLCV data and prints (or sends) the formatted alert.
Useful for testing alert formatting changes without waiting for a live signal.
No DB writes, no cooldown state, no latest-candle-only restriction.

```bash
# Most recent BOS signal for BTCUSDT 1h — print only
poetry run python buibui.py signal test --strategy bos --symbol BTCUSDT --timeframe 1h

# Pin to a specific candle (UTC)
poetry run python buibui.py signal test --strategy bos --symbol BTCUSDT --timeframe 1h \
  --at 2026-04-07T02:00:00

# Use MYT offset (+08:00)
poetry run python buibui.py signal test --strategy bos --symbol BTCUSDT --timeframe 1h \
  --at 2026-04-07T10:00:00+08:00

# Inherit symbol/TF/tp_r from TOML and send to Telegram
poetry run python buibui.py signal test --config config/signal_watch.toml \
  --strategy marubozu --timeframe 15m --telegram

# Filter to shorts only, wider lookback
poetry run python buibui.py signal test --strategy fvg --symbol ETHUSDT --timeframe 4h \
  --direction short --lookback 500
```

Or via Makefile:

```bash
make buibui-signal-test STRATEGY=bos SYMBOL=BTCUSDT TIMEFRAME=1h
make buibui-signal-test STRATEGY=bos SYMBOL=BTCUSDT TIMEFRAME=1h AT=2026-04-07T02:00:00
make buibui-signal-test CONFIG=config/signal_watch.toml STRATEGY=marubozu TIMEFRAME=15m TELEGRAM=1
```

**Options:**

- `--strategy` *(required)* — strategy to test (e.g. `bos`, `fvg`, `marubozu`)
- `--symbol` — trading pair (required unless `--config` provides one)
- `--timeframe` — candle timeframe (required unless `--config` provides one)
- `--at` — pin to a specific candle; ISO datetime (naive = UTC, or with `+08:00` for MYT) or Unix ms integer; defaults to latest available candle
- `--lookback` — number of candles to load ending at `--at` (default: `200`)
- `--direction` — filter to `long` or `short` signals only
- `--tp-r` — TP risk:reward for formatting (default: `2.0` or from `--config`)
- `--min-sl-pct` — minimum SL distance as fraction of price (default: `0` or from `--config`)
- `--config` — TOML file to inherit symbol/TF/tp_r/sl_pct defaults
- `--telegram` — send the alert via Telegram (in addition to printing)

> **Note:** `smt_divergence` is supported — the secondary symbol is resolved automatically from `coins.json` (`smt_secondary` field). No extra flag needed.

### Web API — FastAPI Backend

A JSON REST API and SSE streaming backend for the Phase 5 Svelte frontend (or any HTTP client).

```bash
# Start the API server (default: http://127.0.0.1:8000)
poetry run python buibui.py web

# Pass a signal-watch TOML so the UI auto-populates defaults from it
poetry run python buibui.py web --config config/signal_watch.toml

# Custom host/port with auto-reload for development
poetry run python buibui.py web --host 0.0.0.0 --port 8000 --reload

# Or via Makefile (override PORT and/or CONFIG)
make buibui-web
make buibui-web PORT=8080
make buibui-web CONFIG=config/signal_watch.toml
make web-full CONFIG=config/signal_watch.toml   # build UI then start server
```

**Authentication:** All endpoints except `/api/health` require a Bearer token. Set `API_TOKEN` in `.env`.
SSE stream endpoints accept `?token=<API_TOKEN>` query param instead (browser `EventSource` cannot send headers).

**Endpoints:**

| Method | Path | Description |
| ------ | ---- | ----------- |
| `GET` | `/api/health` | Health check — no auth required |
| `GET` | `/api/config` | Per-symbol config from `coins.json` |
| `GET` | `/api/active-config` | Active TOML config the server was started with (empty defaults when no `--config` passed) |
| `GET` | `/api/strategies` | All strategy specs with params and confidence (auto-uses active config's star ratings) |
| `GET` | `/api/ohlcv` | OHLCV candles (`?symbol=&timeframe=&start_ms=&end_ms=`) |
| `POST` | `/api/signals` | Detect strategy signals on historical data |
| `GET` | `/api/backtest/runs` | All saved backtest runs from DB, newest first |
| `POST` | `/api/backtest` | Run a backtest (auto-saved to DB) for a symbol/timeframe/strategy |
| `GET` | `/api/positions` | Fetch open futures positions |
| `GET` | `/api/prices` | Latest price changes for all configured symbols |
| `GET` | `/api/stream/prices` | SSE — live prices every 5 s (`?token=`) |
| `GET` | `/api/stream/positions` | SSE — live positions every 10 s (`?token=`) |
| `GET` | `/api/stats/{symbol}` | Computed stats bundle (P1/P2, ADR, DOW, session, weekly) for a symbol |
| `GET` | `/api/zones` | Structural zones for a symbol+timeframe (FVG, OB, EQH/EQL, BOS, Fib, OTE, swings) |

**CORS:** Defaults to `http://localhost:5173` (Vite dev server). Override with `CORS_ORIGINS` env var (comma-separated). If you change `DEV_PORT`, update `CORS_ORIGINS` accordingly (e.g. `CORS_ORIGINS=http://localhost:3000`).

**Notes:**

- The web server opens the DB in **read-only** mode. The signal daemon holds the write lock.
- Requires `analytics backfill` to have been run first for OHLCV/signals/backtest endpoints.
- In production, the API server serves the built Svelte UI from `web/ui/dist/` as static files.

### Web Frontend — Svelte 5

A single-page trading terminal UI. Dark theme, no component library, no SSR.
Pages: Chart (candlesticks + signal markers + structural zone overlays), Backtest (DB-backed sortable/filterable results table + collapsible run form), Signal Feed (poll + filters), Positions (SSE), Prices (SSE).

Chart overlays include EMA 20/50/200, RSI sub-panel, Range Levels (MO/DO/WO + PDH/PDL/PWH/PWL/Mon H·L), CME Gap (15m/1h only), Fibonacci retracement, and **Structural Zones** (7 toggles: FVG boxes, Order Block boxes, EQH·EQL lines, BOS levels, Fib Golden Zone box, OTE box, swing pivot dots — powered by `GET /api/zones`).

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

# Co-firing confluence backtest (D10)
make buibui-combo-backtest CONFIG=config/signal_watch.toml SINCE=2025-09-12 SAVE=1
make buibui-combo-backtest CONFIG=config/signal_watch.toml WINDOW=3 MIN_TRADES=5
make buibui-combo-backtest CONFIG=config/signal_watch.toml WORKERS=2  # light mode when other processes running

# Recalibrate confidence star ratings (per-config)
make buibui-recalibrate CONFIG=config/signal_watch.toml          # dry-run
make buibui-recalibrate CONFIG=config/signal_watch.toml APPLY=1  # write to DB
make buibui-recalibrate MIN_TRADES=20 CONFIG=config/signal_watch.toml APPLY=1

# Digest: aggregated analysis over saved backtest runs
make buibui-digest QUERY=strategy           # strategy leaderboard (default)
make buibui-digest QUERY=symbol             # symbol leaderboard
make buibui-digest QUERY=direction_bias     # long vs short avg R per strategy
make buibui-digest QUERY=adr_ab             # ADR gate A/B delta
make buibui-digest QUERY=volume_ab          # volume suppress A/B delta
make buibui-digest QUERY=day_filter_ab      # day filter A/B delta
make buibui-digest QUERY=consistency        # edge breadth across symbol×TF combos
make buibui-digest QUERY=recovery_factor    # risk-adjusted ranking
make buibui-digest QUERY=tf                 # timeframe ranking
make buibui-digest QUERY=combos TOP_N=20    # best combos top-N
make buibui-digest QUERY=co_firing          # co-firing confluence pair leaderboard
make buibui-digest QUERY=cross_tf_combos   # cross-TF co-firing pair leaderboard (HTF→LTF)
make buibui-digest MIN_TRADES=10            # raise min-trades threshold
```

Defaults: `SYMBOL=BTCUSDT`, `STRATEGY=fvg`, `INTERVAL=4h`, `DAYS=90`.
Optional overrides: `SL_PCT`, `TP_R`, `FEE_PCT`, `SECONDARY` (required for `smt_divergence`), `SAVE=1` (persist to DB).

To populate both `day_filter` variants for complete coverage:

```bash
# day_filter = false
poetry run python buibui.py backtest --config config/signal_watch.toml --save

# day_filter = true
poetry run python buibui.py backtest --config config/signal_watch.toml --day-filter --save
```

**Persisting results for confidence score recalibration:**

Add `--save` (or `SAVE=1` via make) to store aggregate results in `analytics.db`:

```text
backtest_runs        — one row per (symbol, tf, strategy, param combo):
                       win_rate, avg_r, total_r, max_drawdown_r, all params used;
                       long_win_rate, long_avg_r, short_win_rate, short_avg_r (direction split)
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

**TOML opt-in** — add to `config/signal_watch.toml`:

```toml
save_results = true
```

**Web frontend:**

```bash
make web-install                    # npm install in web/ui/
make web-dev                        # Vite dev server (http://localhost:5173, proxies /api to :8000)
make web-dev DEV_PORT=3000          # Override Vite port
make web-build                      # Build Svelte app → web/ui/dist/
make web-preview                    # Preview production build locally
make web-full                       # Build + start FastAPI serving the UI
make buibui-web PORT=8080           # FastAPI on a custom port
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
make buibui-signal-test STRATEGY=bos SYMBOL=BTCUSDT TIMEFRAME=1h       # test alert, print only
make buibui-signal-test STRATEGY=bos SYMBOL=BTCUSDT TIMEFRAME=1h AT=2026-04-07T02:00:00  # pin candle
make buibui-signal-test CONFIG=config/signal_watch.toml STRATEGY=marubozu TIMEFRAME=15m TELEGRAM=1
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
| `regression` | `*.py` / TOML / fixture / golden JSON changes | runs `make test-regression` against committed golden files; fails with a diff report if metrics drift |

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

- Auto-close on global SL or high-risk warning
- Telegram command handler (`/price`, `/position`)
