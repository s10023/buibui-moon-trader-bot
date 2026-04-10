# TradFi Equity Bot — Fork Design

**Date:** 2026-04-10
**Status:** Draft — name TBD
**Author:** brainstorming session

---

## 1. Overview

Fork `buibui-moon-trader-bot` into a new repo targeting **US equities (single stocks)** via the
**Alpaca Markets API**. The bot runs on-demand (once per day or once per week), not as a
continuous live daemon. It re-uses the entire strategy and backtest engine; only the data layer
and equity-specific session logic are replaced.

### Name candidates (TBD — pick one)

| Name | Theme |
| --- | --- |
| `lunafi` | luna (moon lineage) + fi (finance) |
| `moonshot` | keeps celestial theme, equities connotation |
| `equiluna` | equities + luna lineage |
| `starshot` | celestial + upside bias |

Decision: **pending user choice.** Placeholder used throughout: `[BOTNAME]`.

---

## 2. What's Decided

| Decision | Choice | Rationale |
| --- | --- | --- |
| Markets | US equities — single stocks (MSTR, AAPL, CRCL, etc.) | User stated |
| Data source | Alpaca Markets (`alpaca-py` SDK) | REST/WS, no daemon, unrestricted historical |
| Repo strategy | **Fork** of `buibui-moon-trader-bot` | Architecturally too different for a branch |
| Run cadence | Once per day or week (on-demand) | Long-term swing trading, not live daemon |
| Timeframes | 1d primary; 4h/1h for entry refinement | Implied by daily/weekly cadence |
| Real-time delay | 15-min delay on free tier is acceptable | Run is EOD/EOW, not intra-session |

---

## 3. Repo Fork Strategy

### What the fork is NOT

- Not a git branch — branches are for features, not separate products
- Not a git subtree/shared library (over-engineered for now)

### Fork process

1. `gh repo fork` or manual fork on GitHub
2. Rename repo to `[BOTNAME]`
3. Global rename: `buibui` → `[BOTNAME]` in CLI entry point, Makefile, README, config keys
4. Strip crypto-specific files immediately (see §5)
5. Replace data layer (see §6)

### What stays in both repos (no sync needed)

The strategy math is pure Python — no shared package needed. If a strategy is improved in one
repo, it is manually ported to the other. This is intentional: the two bots may diverge as
equity-specific patterns emerge.

---

## 4. What Carries Over Unchanged

These files are copied from buibui verbatim and need **zero modification** at fork time:

| File / Module | Reason |
| --- | --- |
| `analytics/indicators_lib.py` | All 21 strategies are pure OHLCV math — no Binance coupling |
| `analytics/backtest_lib.py` | Engine is exchange-agnostic |
| `analytics/param_sweep.py` | WFO logic is exchange-agnostic |
| `analytics/digest_lib.py` | SQL aggregation over `backtest_runs` — unchanged |
| `analytics/recalibrate_lib.py` | Star rating logic — unchanged |
| `analytics/recalibrate_runner.py` | Thin wrapper — unchanged |
| `signals/cooldown_store.py` | Dedup logic — unchanged |
| `signals/alert_formatter.py` | Formatting unchanged (stats context adapted separately) |
| `signals/registry.py` | Strategy registry unchanged |
| `web/` | Entire Svelte + FastAPI web stack carries over |
| `tests/` | Strategy unit tests carry over (they use mock DataFrames) |
| `.claude/skills/` | All skills carry over |

---

## 5. What Gets Removed at Fork

These are crypto-specific and have no equity equivalent at launch:

| File / Concept | Why removed |
| --- | --- |
| `analytics/cme_gap_lib.py` | Crypto CME gap (Fri 21:00–Sun 22:00 UTC). Replaced by overnight gap (§7.3). |
| `utils/binance_client.py` | Replaced by `utils/alpaca_client.py` |
| `funding_reversion` strategy | No funding rates in equities. Remove from `STRATEGY_REGISTRY` and `SIGNAL_REGISTRY`. |
| Funding rate columns in DB schema | `funding_time`, `funding_rate` — removed from `data_store.py` |
| OI (open interest) data | Not available for equities at launch. Remove `oi_usd` column and OI fetching. |
| `taker_buy_volume` column | Binance-specific. Alpaca provides `vwap` instead (see §6.2). |
| CVD divergence strategy | Requires taker buy/sell split — not available from Alpaca. Deferred. |
| `live_price.py` / `live_position.py` | Crypto-specific live WebSocket wrappers. Replace with equity equivalents later. |
| Positions tab (Binance futures) | Equity positions API is different — deferred to later milestone. |

---

## 6. Data Layer Replacement

### 6.1 Alpaca client (`utils/alpaca_client.py`)

Replaces `utils/binance_client.py`. SDK: `alpaca-py` (`pip install alpaca-py`).
Auth: `ALPACA_API_KEY` + `ALPACA_SECRET_KEY` env vars.

```python
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.trading.client import TradingClient

def create_data_client() -> StockHistoricalDataClient: ...
def create_trading_client(paper: bool = True) -> TradingClient: ...
```

Base URL: paper → `https://paper-api.alpaca.markets`, live → `https://api.alpaca.markets`

### 6.2 OHLCV fetcher (`analytics/data_fetcher.py`)

Alpaca equivalent of `fetch_klines()`:

```python
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

def fetch_bars(
    client: StockHistoricalDataClient,
    symbol: str,           # e.g. "AAPL"
    timeframe: TimeFrame,  # TimeFrame.Day, TimeFrame.Hour, etc.
    start: datetime,
    end: datetime,
    adjustment: str = "split",  # handles stock splits automatically
) -> pd.DataFrame:          # same OHLCV_COLUMNS schema as buibui
```

**Column mapping:**

| buibui column | Alpaca field | Notes |
| --- | --- | --- |
| `open_time` | `timestamp` (Unix ms) | Convert from Alpaca datetime |
| `open` | `open` | |
| `high` | `high` | |
| `low` | `low` | |
| `close` | `close` | |
| `volume` | `volume` | Shares traded (not USD) |
| `taker_buy_volume` | `vwap` | Repurposed column — rename to `vwap` in schema |

### 6.3 Timeframe mapping

| buibui interval string | Alpaca TimeFrame | Notes |
| --- | --- | --- |
| `"15m"` | `TimeFrame(15, TimeFrameUnit.Minute)` | |
| `"1h"` | `TimeFrame.Hour` | |
| `"4h"` | `TimeFrame(4, TimeFrameUnit.Hour)` | |
| `"1d"` | `TimeFrame.Day` | Primary TF for this bot |

### 6.4 Data sync (`analytics/data_sync.py`)

- Same paginated backfill pattern as buibui
- Alpaca free tier: no rate-limit issues for EOD batch (200 req/min)
- `adjustment="split"` on all fetches — handles AAPL/NVDA-style stock splits automatically
- **No funding sync** — remove entirely
- **No OI sync** — remove entirely

### 6.5 DB schema (`analytics/data_store.py`)

Modified (not copied verbatim):

| Change | Detail |
| --- | --- |
| Rename `taker_buy_volume` → `vwap` | Alpaca provides VWAP per bar; same column position |
| Drop `funding_rates` table | No funding in equities |
| Drop `open_interest` table | No OI data at launch |
| Symbol format | Plain ticker (`AAPL`) — no `USDT` suffix. DB key stays `(symbol, timeframe, open_time)` |

### 6.6 Watchlist config (`config/stocks.json`)

Replaces `config/coins.json`. Simplified schema (no leverage, no smt_secondary for now):

```json
{
  "AAPL": { "sl_pct": 0.05 },
  "MSTR": { "sl_pct": 0.08 },
  "CRCL": { "sl_pct": 0.07 }
}
```

---

## 7. Equity-Specific Adaptations

### 7.1 Market hours awareness

US equities trade **9:30am–4:00pm ET, Mon–Fri** (regular session).
The bot runs **EOD (after 4pm ET) or EOW (Friday evening)** so market hours don't block execution.
For future live scanning, add a `is_market_open()` guard using Alpaca's `GET /v2/clock` endpoint.

### 7.2 Session anchor for ORB

`detect_orb_breakout()` currently anchors to `00:00 UTC` (crypto daily open).
For US equities, the ORB anchor is **9:30am ET = 13:30 UTC** (14:30 UTC during EST/winter).

Change: set `session_hour_utc=13` as default for US market open.
Long-term: derive dynamically from Alpaca market calendar API to handle DST automatically.

**Impact:** ORB is more powerful for equities — the first 15–30 min range is the most-watched
level in US markets (classic floor trader strategy).

### 7.3 Overnight gap (replaces CME gap)

US equities close at 4pm and re-open at 9:30am the next trading day. Every day has an
**overnight gap** between prior close and today's open. New module: `analytics/overnight_gap_lib.py`

```python
@dataclass
class OvernightGap:
    gap_pct: float     # (open - prev_close) / prev_close
    gap_up: bool
    filled: bool       # did price return to prev_close intraday?
    prev_close: float
    today_open: float

def get_overnight_gap(ohlcv_df: pd.DataFrame) -> OvernightGap | None: ...
def gap_fill_warning(gap: OvernightGap, direction: str, entry: float) -> str | None: ...
```

### 7.4 Dividend adjustment

Alpaca's `adjustment="split"` handles split adjustments automatically. For full dividend
adjustment use `adjustment="all"`. Default: `"split"` only — preserves absolute price levels
needed for S/R detection.

### 7.5 Earnings calendar (deferred)

Earnings cause vol spikes and gaps that invalidate pattern setups. Future milestone: suppress
signals within ±2 days of earnings using Alpaca's earnings calendar endpoint. Not in initial scope.

---

## 8. Strategies Assessment

### Fully transfer as-is (no changes needed)

`wick_fill`, `marubozu`, `liquidity_sweep`, `fvg`, `bos`, `eqh_eql`, `order_block`,
`trend_day`, `engulfing`, `pin_bar`, `inside_bar`, `hammer_hanging_man`, `doji`,
`morning_evening_star`, `fib_golden_zone`, `ote_entry`, `seasonality`

### Transfer with minor adaptation

| Strategy | Change needed |
| --- | --- |
| `orb` | `session_hour_utc` → 13/14 (market open ET) |
| `smt_divergence` | Secondary map changes: e.g. MSTR→BTC price correlation, or QQQ→AAPL. Define in `stocks.json` as `smt_secondary`. |

### Removed at launch

| Strategy | Reason |
| --- | --- |
| `funding_reversion` | No funding rates in equities |
| `cvd_divergence` | No taker buy/sell split from Alpaca free tier |

---

## 9. Run Cadence and CLI

Unlike buibui's continuous daemon, this bot runs on-demand:

```bash
# Fetch latest candles + run signal scan, print/send alerts
[botname] scan --config config/watchlist.toml

# Backtest all stocks in watchlist
[botname] backtest --config config/watchlist.toml --save

# Web dashboard (same as buibui)
[botname] web --config config/watchlist.toml
```

Typical workflow:

- **Daily (EOD):** run `scan` after 4pm ET — catches daily closes
- **Weekly (EOW):** run `scan` on Friday evening — weekly candle setups
- **Backtest refresh:** run `backtest` monthly or after adding new stocks

---

## 10. Out of Scope (Initial Fork)

| Feature | Notes |
| --- | --- |
| Order execution via Alpaca | Paper trading first; execution after bot is validated |
| Positions tab | Alpaca positions API differs from Binance futures |
| Earnings calendar suppression | Needs Alpaca paid data or free earnings API |
| Real-time live daemon | Alpaca free tier has 15-min delay; add paid plan when needed |
| Pre/after-hours OHLCV | Available via Alpaca `extended_hours=True` — defer |
| Short interest / borrow rate | Equity equivalent of funding — defer |
| Options data | Available from Alpaca paid — out of scope |
| Multi-market (HK, SG) | Moomoo supports these — possible future path |

---

## 11. Open Questions

1. **Name** — pick from candidates in §1 (`lunafi`, `moonshot`, `equiluna`, `starshot`) or propose another
2. **GitHub org/account** — same account as buibui or separate?
3. **Initial watchlist** — MSTR, AAPL, CRCL confirmed; how many others?
4. **Primary timeframe** — 1d only to start, or include 4h from day one?
5. **Alpaca account type** — paper only initially, or set up live account from the start?

---

## 12. Implementation Sequence

Once name is chosen and spec approved:

1. **Fork repo** → rename → global find/replace `buibui` → `[BOTNAME]`
2. **Strip crypto layer** — remove Binance client, funding/OI, `cme_gap_lib`, positions tab
3. **Alpaca data layer** — `alpaca_client.py`, new `data_fetcher.py`, updated `data_sync.py`
4. **Schema updates** — `data_store.py`: rename `taker_buy_volume`→`vwap`, drop funding/OI tables
5. **Config** — `stocks.json` watchlist, TOML template for Alpaca
6. **ORB session fix** — `session_hour_utc=13` default for US market open
7. **Overnight gap lib** — `overnight_gap_lib.py` replacing `cme_gap_lib.py`
8. **Remove `funding_reversion` + `cvd_divergence`** from all registries
9. **Tests** — update test fixtures; add Alpaca fetcher mock
10. **Backtest + scan** — validate pipeline end-to-end on AAPL 1d
11. **Web dashboard** — verify it boots correctly with new config
