# 🚀 Buibui Moon Trader Bot

A tactical crypto trading bot designed for fast, risk-managed, and confident entries — with live price monitoring and position tracking. Built for degens who trade smart. LFG. 🌕

---

## 🧠 Features

### ✅ Core Tools

- **Manual Multi-Trade Entry Script**  
  Open multiple trades (BTC, ETH, alts) in one go, using USD-based sizing with automatic SL & leverage.

- **Live Price Monitor**  
  See real-time prices, 15m / 1h / 24h % changes, and intraday % change since Asia open (8AM GMT+8).  
  Color-coded for clarity.

- **Live Position Tracker**  
  Track open positions with wallet balance, used margin, PnL, %PnL, and risk exposure per trade.  
  Table auto-sorted by your config list.

- **15-Min Telegram Updates** *(optional)*  
  Get regular position snapshots via Telegram bot.

---

## 🔒 Risk Rules (Preconfigured)

| Asset Type  | Leverage | Stop Loss |
|-------------|----------|-----------|
| BTC         | 25x      | 2.0%      |
| ETH         | 20x      | 2.5%      |
| Altcoins    | 20x      | 3.5%      |

Includes max USD-per-trade cap and wallet-level risk protection.

---

## 📦 Directory Structure

```bash
buibui-moon-trader-bot/
├── trade/
│ └── open_trades.py # Open multiple trades via Binance
├── monitor/
│ ├── price_monitor.py # Live price, PnL, risk tracker
│ └── position_monitor.py # Telegram PnL updates every 15min
├── config/
│ └── coins.json # Coin list, SL%, leverage per symbol
├── .github/
│ └── workflows/
│ └── monitor.yml # GitHub Actions for automated Telegram updates
├── .env.example # Sample config (Telegram + Binance keys)
├── requirements.txt # Python dependencies
└── README.md
```

## 🛠️ Makefile Usage

The Makefile provides easy commands for all major actions:

**Lint, Format, Typecheck:**

```bash
make lint           # Lint Markdown and Python (excludes venv)
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
make buibui-monitor-position           # Default sort
make buibui-monitor-position SORT=pnl_pct:desc   # Sort by PnL%
make buibui-monitor-position SORT=sl_usd:asc     # Sort by SL risk
make buibui-monitor-position-telegram
```

**Open trades:**

```bash
make buibui-open-trades
```

**Docker:**

```bash
make docker-build
make docker-monitor-price
make docker-monitor-position
```

All commands use your `.env` file for secrets and config.

---

## ⚙️ Setup

### 1. Clone this repo

```bash
git clone https://github.com/yourname/buibui-moon-trader-bot.git
cd buibui-moon-trader-bot

```

### 2. Create and Activate a Virtual Environment

```bash
# With Poetry (recommended)
poetry install --no-root
poetry shell
```

(If you want to use a system venv, you can, but Poetry manages its own by default.)

### 3. Install dependencies

```bash
poetry install --no-root
```

🔁 To update later:
poetry update

### 4. Add Your API Keys

Create a `.env` file based on .env.example:

```bash
# .env
BINANCE_API_KEY=your_key
BINANCE_API_SECRET=your_secret

TELEGRAM_BOT_TOKEN=bot_token
TELEGRAM_CHAT_ID=your_chat_id

# Short-term wallet target for progress bar
WALLET_TARGET=2000
```

### 4. Configure your coins

Edit `config/coins.json` to define each symbol's leverage and stop-loss percent.

```json
{
  "BTCUSDT": { "leverage": 25, "sl_percent": 2.0 },
  "ETHUSDT": { "leverage": 20, "sl_percent": 2.5 },
  "SOLUSDT": { "leverage": 20, "sl_percent": 3.5 }
}
```

## 🐳 Docker & Makefile Usage

You can use Docker to run your bot in a consistent environment, and the Makefile provides easy commands for building and running your container.

### Build the Docker image

```bash
make docker-build
```

### Run the price monitor in Docker

```bash
make docker-run-price
```

### Run the position monitor in Docker

```bash
make docker-run-position
```

All commands use your `.env` file for secrets and config.

## 🛠️ Usage

### 🧾 Open Multiple Trades (manually)

```bash
poetry run python trade/open_trades.py
```

You'll be prompted to enter:

- Direction (LONG/SHORT)

- USD per trade (with default)

- Confirmation before executing

### 📈 Monitor Prices

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

- default — Respect order from `config/coins.json`
- `change_15m` — 15-minute % change
- `change_1h` — 1-hour % change
- `change_asia` — % change since Asia open (8AM GMT+8)
- `change_24h` — 24-hour % change

Append `:asc` or `:desc` to control the sort direction (defaults to `desc`).

It shows:

- Live price
- 15-minute %, 1-hour %, Asia session %, and 24h %

Example Output:

```bash
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

```

### 📊 Monitor Positions & PnL

```bash
poetry run python buibui.py monitor position [--sort key[:asc|desc]] [--hide-empty]
```

Shows:

- Wallet balance

- Total unrealized PnL

- Colorized risk table with per-trade metrics

- Only open positions are shown. Auto-sorted by your `coins.json` order.

- Use `--hide-empty` to hide rows for symbols with no open positions.

- Use `--compact` to not list out the list of open positions, and only show necessary information.

Example Output:

```yaml
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


```

Sorting Options:

- You can now control how the table is sorted using the `--sort` flag.

```bash
poetry run python buibui.py monitor position --sort pnl_pct:desc   # Sort by highest PnL%
poetry run python buibui.py monitor position --sort sl_usd:asc     # Sort by lowest SL risk
poetry run python buibui.py monitor position --sort default        # Sort by coins.json order (default)
```

Supported sort keys:

- default — Respect order from `config/coins.json`

- `pnl_pct` — Sort by unrealized profit/loss % (margin-based)

- `sl_usd` — Sort by USD value at risk based on SL

Append `:asc` or `:desc` to control the sort direction (defaults to `desc`).

### ☁️ GitHub Actions (Optional)

The `.github/workflows/monitor.yaml` file can be configured to:

- Run position_monitor.py every 15 minutes

- Send live updates to Telegram

### 📌 Coming Soon / Ideas

- Trade signal engine (support/resistance + volume traps)

- Auto-close on global SL or high-risk warning

- Visual dashboard (web UI or terminal rich)

- Funding rate monitor + reversal detector

## 🧹 Linting & Type Checking

This project uses:

- **black** for code formatting
- **mypy** for static type checking

To check formatting and types locally:

```bash
poetry run black --check .
poetry run mypy .
```

## 🛡️ Continuous Integration

Every push and pull request runs automated checks (linting, formatting, and type checking) via GitHub Actions.  
You can find the workflow in `.github/workflows/lint.yaml`.
