# 🚀 Buibui Moon Trader Bot

A tactical crypto trading bot for Binance Futures, designed for fast, risk-managed, and confident entries — with live price monitoring and position tracking. Built for degens who trade smart. LFG. 🌕

---

## 🧠 Features

- **Manual Multi-Trade Entry Script**  
  *(Planned: `trade/open_trades.py` is currently a placeholder. Usage: Open multiple trades (BTC, ETH, alts) in one go,
  using USD-based sizing with automatic SL &
  leverage.)*
- **Live Price Monitor**  
  Real-time prices, `15m` / `1h` / `24h` `%` changes, and intraday `%` change since Asia open (8AM GMT+8). Color-coded, sortable, and can send Telegram updates.
- **Live Position Tracker**  
  Track open positions, wallet balance, margin, PnL, %PnL, and risk per trade. Colorized, auto-sorted, and Telegram-enabled.
- **Risk Management**  
  Per-symbol leverage and stop loss, wallet-level risk, config validation.
- **Telegram Integration**  
  Optional price/position updates to Telegram.
- **Docker & Makefile**  
  Easy local or containerized runs, with developer commands for linting, type checking, and more.
- **CI/CD**  
  GitHub Actions for linting, formatting, and type checking.

---

## 📦 Directory Structure

```bash
buibui-moon-trader-bot/
├── buibui.py                # Main CLI entry point
├── trade/
│   └── open_trades.py       # (Planned) Multi-trade entry script
├── monitor/
│   ├── price_monitor.py     # Live price monitor
│   └── position_monitor.py  # Position & PnL monitor
├── utils/
│   ├── telegram.py          # Telegram integration
│   └── config_validation.py # Config validation
├── config/
│   └── coins.json.example   # Example coin config
├── tests/                   # (Currently empty)
├── .github/                 # GitHub Actions workflows
├── requirements.txt         # Python dependencies
├── pyproject.toml           # Poetry/Build config
├── Makefile                 # Developer commands
├── Dockerfile               # Docker support
└── README.md
```

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

### 3. Install dependencies

```bash
poetry install --no-root
```

### 4. Add Your API Keys

Create a `.env` file based on `.env.example`:

```env
BINANCE_API_KEY=your_key
BINANCE_API_SECRET=your_secret
TELEGRAM_BOT_TOKEN=bot_token
TELEGRAM_CHAT_ID=your_chat_id
WALLET_TARGET=2000
```

### 5. Configure your coins

Edit `config/coins.json` to define each symbol's leverage and stop-loss percent:

```json
{
  "BTCUSDT": { "leverage": 25, "sl_percent": 2.0 },
  "ETHUSDT": { "leverage": 20, "sl_percent": 2.5 },
  "SOLUSDT": { "leverage": 20, "sl_percent": 3.5 }
}
```

---

## 🛠️ Makefile Usage

**Lint, Format, Typecheck:**

```bash
make lint           # Lint Markdown and Python
make typecheck      # Type check with mypy
```

**Install/Update dependencies:**

```bash
make poetry-install
make poetry-update
```

**Run monitors:**

```bash
make buibui-monitor-price
make buibui-monitor-price-live
make buibui-monitor-price-telegram
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

## 🐳 Docker Usage

You can use Docker to run your bot in a consistent environment:

```bash
make docker-build
make docker-monitor-price
make docker-monitor-position
```

---

## 🛠️ Usage

### 🧾 Open Multiple Trades (manual, planned)

```bash
poetry run python trade/open_trades.py
```

*Currently a placeholder. Planned: open multiple trades with USD sizing, SL, and leverage.*

### 📈 Monitor Prices

```bash
poetry run python buibui.py monitor price
poetry run python buibui.py monitor price --live
poetry run python buibui.py monitor price --sort change_15m:desc
```

### 📊 Monitor Positions & PnL

```bash
poetry run python buibui.py monitor position [--sort key[:asc|desc]] [--hide-empty] [--compact]
```

---

## 🧪 Testing

*Currently, there are no automated tests. To add tests, create files in the `tests/` directory and use your preferred Python test framework (e.g., pytest, unittest).*

**TODO:** Add test coverage for config validation, monitor logic, and CLI entry points.

---

## 📚 Dependencies

Major dependencies:

- **python-binance**: Binance API client (futures trading)
- **python-dotenv**: Loads environment variables from `.env`
- **tabulate**: Pretty-print tables in terminal
- **colorama**: Terminal color support
- **requests**: HTTP requests (Telegram integration)
- **pytz**: Timezone handling
- **black, mypy**: Formatting and type checking
- **docker**: (Optional) Containerized runs

See `requirements.txt` and `pyproject.toml` for the full list.

---

## 🛡️ Security & Disclaimer

- **Trading is risky!** This bot is for educational and experimental purposes only.
- **API keys**: Never share your API keys. Use read-only or restricted keys where possible.
- **No warranty**: Use at your own risk. The authors are not responsible for financial loss or account issues.
- **Review the code** before using with real funds.

---

## 🛠️ Troubleshooting

- **API Key Errors**: Double-check your `.env` file and Binance API permissions.
- **Config Errors**: Ensure `config/coins.json` is valid and matches the example format.
- **Binance API Limits**: Too many requests may result in rate limiting. Use with care.
- **Telegram Issues**: Ensure your bot token and chat ID are correct.
- **Docker Issues**: Make sure Docker is installed and running.

---

## 🤝 Contributing

Contributions are welcome! To contribute:

- Fork the repo and create a feature branch.
- Follow PEP8 and use `black` for formatting.
- Add docstrings and type hints.
- Open a pull request with a clear description.

**TODO:** Add more detailed contributing guidelines if you have a specific process.

---

## 📝 Changelog

*Maintain a list of changes here. For now, see commit history.*

---

## 📸 Screenshots / Visuals

**TODO:** Add screenshots or GIFs of the terminal UI here for visual reference.

---

## 📄 License

**TODO:** Add your license here (MIT, GPL, proprietary, etc.).

---

## 📬 Contact / Support

**TODO:** Add your contact info or support channel here if you want users to reach out.

---

## 💡 Roadmap / Ideas

- Implement multi-trade entry script
- Add more advanced risk controls (max daily loss, auto-close, etc.)
- Add more notification options (email, Discord)
- Add a web dashboard for richer visualization
- Add unit and integration tests

---

*This README was auto-generated and improved for clarity, completeness, and professionalism. Please fill in the TODOs as appropriate for your project.*
