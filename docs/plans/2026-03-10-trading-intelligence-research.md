# Trading Intelligence Research & Implementation Plan

**Date:** 2026-03-10
**Status:** Draft — for review

---

## 1. BrighterData

### What It Is

BrighterData is a statistical analytics platform for crypto traders built around the concept that
**price moves with higher probability at specific times and levels**. Rather than watching charts
24/7, BrighterData provides pre-computed statistical insights so a trader can know in advance which
hours and sessions are most actionable.

### Core Concepts

#### Session Breakdown

BrighterData segments the trading day into named sessions aligned with traditional market hours:

- **Asia session** (~00:00–08:00 UTC)
- **London/Europe session** (~07:00–16:00 UTC)
- **New York session** (~13:00–22:00 UTC)

For each session it tracks: typical range size, directional bias (% of days that session closed
up vs down), and mean/median extension from open.

#### P1 / P2 Pivots

Proprietary pivot system (not classical floor pivots):

- **P1**: Primary pivot — derived from prior session's range midpoint; acts as the first area of
  interest where price is likely to stall, reverse, or accelerate
- **P2**: Secondary pivot — extension of P1; targets beyond the first reaction zone
- These are statistical levels, not mechanical buy/sell triggers

#### Weak High / Weak Low vs Strong High / Strong Low

A core structural concept:

- **Weak high**: High formed with low volume or momentum, likely to be taken out (stop-run target)
- **Strong high**: High defended by real buying; acts as resistance with mean-reversion potential
- Same logic applies to lows
- BrighterData likely automates identifying these by comparing volume profiles and wick rejection
  at session extremes

#### Monthly / Weekly / Daily Breakdown

Statistical tables showing:

- Day-of-week bias (e.g., "Monday is bullish 63% of the time on BTC in trending regimes")
- Day-of-month patterns (e.g., "Days 1–3 of month show elevated volatility")
- Hourly distribution of range extension (which hours produce the most price movement)

### Practical Use for Your Bot

Instead of monitoring 24/7, BrighterData tells you: "The next high-probability window is
London open (08:00 UTC) on a Tuesday after a Monday that closed red." Your bot could:

1. Read BrighterData session tables (if API exists) or replicate the stats internally
2. Send Telegram alerts only during statistically significant windows
3. Suppress noise alerts during low-probability hours (e.g., late Asia dead zone)

### Pricing (estimated, based on known market positioning)

| Tier | Price | What You Get |
| ---- | ----- | ------------ |
| Free | $0 | Limited historical data, basic session breakdown |
| Pro | ~$29–49/mo | Full historical stats, P1/P2 levels, daily bias tables |
| Team | ~$99+/mo | API access, multiple assets, export |

> Note: BrighterData is a relatively niche tool; pricing may have changed. Verify at
> brighterdata.com. Free tier is worth testing first.

### Actionable Next Steps — BrighterData

1. Sign up for free tier, test session breakdown on BTCUSDT
2. Manually note which hours your current bot fires the most alerts vs actual moves
3. Compare against BrighterData session windows — use this to build an "active hours" filter
4. If API access exists in paid tier, plan a Python wrapper to pull daily bias data

---

## 2. Time-Based Analysis Framework (KillaXBT Style)

### What KillaXBT Does

KillaXBT (@KillaXBT on X) is a quantitative crypto analyst known for publishing
**time-based statistical backtests** on BTC and ETH. Their approach:

- Takes years of OHLCV data
- Segments it by: day of week, day of month, hour of day, week of month
- Produces tables like: "BTC on the 14th day of the month: +1.2% average, 68% bullish"
- Overlays macro context (e.g., post-halving regimes, Fed meeting weeks)
- Publishes "seasonal" patterns: BTC tends to top in Q4, bottom in Q1, etc.

The key insight: **most retail traders treat every hour as equally valid**. Time-based analysis
shows that certain windows have a dramatically higher hit rate, allowing traders to be
more selective.

### Framework You Could Build

#### Data Required

- **Source**: Binance Futures REST API (`/fapi/v1/klines`)
- **Granularity**: 1h candles (good balance of precision vs noise)
- **History**: Minimum 2 years; 3–4 years preferred for statistical significance
- **Assets**: BTCUSDT, ETHUSDT as anchors; add others as needed
- **Fields per candle**: open, high, low, close, volume, timestamp

Binance provides up to 1,000 candles per request; paginate with `startTime`/`endTime` to
pull full history.

#### Statistical Methods

#### Day-of-Week Analysis

```text
For each weekday (Mon–Sun):
  - % of days that closed bullish (close > open)
  - Mean return (%)
  - Median return (%)
  - Std deviation
  - Max gain / max loss
```

#### Day-of-Month Analysis

```text
For each day 1–31:
  - Same metrics as above
  - Special attention to: day 1 (first of month flows), day 15 (mid-month),
    last 3 days (options expiry, derivatives settlement)
```

#### Hour-of-Day Analysis

```text
For each hour 0–23 UTC:
  - % of hours that closed bullish
  - Mean hourly range size (high - low)
  - Mean return from open of that hour
  - Useful for: entry timing, setting alerts only during high-activity hours
```

#### Session-Level Analysis

```text
Define sessions:
  Asia:    00:00–08:00 UTC
  London:  07:00–16:00 UTC (overlap 07–08 = high volatility)
  New York: 13:00–22:00 UTC (overlap 13–16 = highest volatility)
  Dead zone: 22:00–00:00 UTC
For each session:
  - Average range as % of daily ATR
  - % sessions that set the day's high or low
  - Directional bias
```

#### Week-of-Month Analysis

```text
Week 1 (days 1–7), Week 2 (8–14), Week 3 (15–21), Week 4 (22–28), Week 5 (28+)
  - Tracks monthly rhythms (e.g., options expiry in last week)
```

#### Regime Filtering

More advanced: segment stats by market regime:

- Bull trend: price > 200-day SMA
- Bear trend: price < 200-day SMA
- High volatility: ATR > X%
- This prevents averaging together bull and bear data which dilutes signals

#### Output Format

#### Summary Table (console/Telegram)

```text
BTC Day-of-Week Stats (2022–2026, 1h candles)
Day       | Bullish% | Mean Ret | Median | Max Win | Max Loss
----------|----------|----------|--------|---------|----------
Monday    |   54.3%  |  +0.31%  | +0.18% | +8.2%  | -6.1%
Tuesday   |   51.7%  |  +0.12%  | +0.08% | +7.4%  | -5.9%
Wednesday |   48.2%  |  -0.08%  | -0.03% | +6.8%  | -7.2%
Thursday  |   52.1%  |  +0.19%  | +0.11% | +9.1%  | -5.5%
Friday    |   49.8%  |  -0.15%  | -0.10% | +5.9%  | -8.4%
Saturday  |   50.1%  |  +0.05%  | +0.02% | +5.2%  | -4.8%
Sunday    |   53.2%  |  +0.22%  | +0.14% | +6.3%  | -4.2%
```

**Heatmap**: Hour vs Day matrix showing average return — easy to spot high-probability windows.

#### Implementation Approach

**Stack**: Python + pandas + Binance REST client (already in your project)

**Architecture**:

```text
scripts/
  fetch_historical.py     — pull and cache OHLCV data to CSV/parquet
  time_analysis.py        — compute all stats, output tables
  regime_filter.py        — classify bars as bull/bear/volatile
Output: JSON + CSV files in data/ directory
```

**Libraries**:

- `pandas` — aggregation, groupby, pivot tables
- `numpy` — stats
- `matplotlib` / `seaborn` — heatmaps (optional, offline use)
- No new runtime dependencies needed for the bot itself

**Workflow**:

1. Run `fetch_historical.py` once, then weekly to update
2. Run `time_analysis.py` to regenerate stats tables
3. Bot reads a pre-computed JSON file at startup: `data/time_bias.json`
4. Price monitor uses bias to tag alerts: "This alert is in a HIGH PROBABILITY window (Mon
   London open, historically 67% bullish)"

**Estimated build time**: 2–3 days for a solid v1.

---

## 3. MMT (mmt.gg)

### What MMT Is

MMT (Market Making Tools) is a professional-grade crypto market data platform focused on
**microstructure data** — the kind of information that reveals what institutional participants
and market makers are doing. It targets serious futures traders who need more than price charts.

### Core Tools and Features

#### Liquidation Maps / Heatmaps

- Shows where clusters of leveraged positions exist and will be force-liquidated as price moves
- Displayed as a price/time heatmap overlaid on chart
- Extremely useful: market makers and large traders often hunt these liquidation clusters
  ("stop hunts")
- Allows you to anticipate: "If BTC pushes to $X, a cascade of shorts/longs get liquidated,
  creating fuel for an extended move"

#### Orderbook Depth (Aggregated)

- Real-time view of bid/ask walls across major exchanges
- Spot large limit orders (whales) sitting at key levels
- Detects spoofing patterns (large orders that disappear before being hit)

#### Open Interest (OI) Analysis

- Total OI across Binance, Bybit, OKX, Deribit
- OI divergence from price: price up + OI up = trending; price up + OI down = short covering
  (weak rally)
- OI heatmap: where was OI added/removed

#### Funding Rate Dashboard

- Aggregated funding across exchanges
- Historical funding chart — extreme funding = mean-reversion signal
- Funding arbitrage opportunities visible when exchanges diverge

#### CVD (Cumulative Volume Delta)

- Tracks whether buyers or sellers are more aggressive (market orders vs limit orders)
- Rising price + falling CVD = warning sign (price going up on weak buying)
- Falling price + rising CVD = potential reversal (buyers absorbing sells)

#### Spot vs Futures Premium (Basis)

- When futures trade at a large premium to spot, it signals leveraged speculation
- Useful for identifying overheated markets

### Free vs Paid

| Tier | Price | Access |
| ---- | ----- | ------ |
| Free | $0 | Basic liquidation heatmap (delayed), limited history, single asset |
| Pro | ~$49–79/mo | Real-time data, full history, multi-asset, OI, CVD, orderbook |
| Institutional | Custom | API access, bulk data, white-label |

> Free tier gives enough to understand the tool and identify the highest-value features.
> Pro tier is justified if you trade size or make 5+ trades/month where liquidation zones
> change your entry/exit decision.

### How It Helps Your Bot

Your bot currently monitors prices and positions. MMT adds a **context layer**:

- Before entering a position, check if a major liquidation cluster sits just beyond your
  target — it might accelerate the move (or trigger a reversal)
- Funding rate alerts: if funding is extreme (>0.1% per 8h), flag it in Telegram messages
- OI spike detection: sudden OI increase can precede large moves

You do not need to replicate MMT. Instead, use Binance's own public APIs for the free
equivalent of some of these:

- `/fapi/v1/openInterest` — open interest per symbol
- `/fapi/v1/fundingRate` — current and historical funding
- `/fapi/v1/ticker/bookTicker` — best bid/ask
- Liquidation heatmaps are harder — require aggregating trade data over time (MMT's paid
  differentiator)

### Actionable Next Steps — MMT

1. Create a free MMT account, explore the liquidation heatmap on BTCUSDT during an active
   session
2. Note the $-levels where large liquidation clusters sit — treat these as key levels for
   the week
3. Add funding rate to your bot's Telegram summary (free, from Binance API)
4. Add OI change (%) to your position monitor output

---

## 4. aggr.trade

### Overview

**aggr.trade** is an open-source, real-time **order flow aggregator** for crypto. It is not a charting
platform — it shows you the raw live stream of market orders (the tape) across multiple exchanges
simultaneously, filtered and grouped to remove noise. Built by Tucsky; runs fully in the browser
with no API key required. Free and open-source (MIT).

The core idea: standard charts tell you *where* price is. aggr.trade tells you *what is actually
happening* — who is buying, who is selling, how large the trades are, and when forced liquidations
are occurring.

### Data Sources It Aggregates

Connects directly via WebSocket to public exchange feeds — no account needed:

- Binance (spot + futures)
- Bybit, OKX, Bitget, Coinbase

Also captures **liquidation events** from futures exchanges when available.

### Core Features

#### Trade Bubble Map

Each bubble is a block of aggregated trades from the same timestamp + side. Green = market buys,
red = market sells. Bubble size = trade value in USD. You set a filter threshold (e.g. only show
trades >$50k) to watch only large orders and filter retail noise.

#### Cumulative Volume Delta (CVD)

Running net of buyer-initiated vs seller-initiated volume. Divergence between CVD and price is one
of the most reliable reversal signals:

- Price new high + CVD declining → buyers exhausted, possible reversal
- Price falling + CVD rising → sellers absorbed by passive buyers, possible bounce

#### Liquidation Feed

Visual and audio alerts when large leveraged positions are force-closed. Liquidation clusters are
watched as potential cascade triggers (if large = more stop-outs incoming) or absorption signals
(if price doesn't move despite a cluster = strong passive buyer present).

#### Multi-Exchange Unified Tape

All exchanges shown simultaneously. Lets you see which venue is driving price and where large
players are active.

### What Makes It Uniquely Useful

**Iceberg detection**: Large algorithmic orders are broken into many small fills. On a standard
chart this is invisible. On aggr.trade you see the sequential fills hitting and can recognize the
pattern.

**Absorption signals**: A cluster of large sell liquidations hits but price barely moves — a large
passive buyer is absorbing the flow. This is frequently a precursor to a local reversal upward.

**Liquidation cascade anticipation**: If price approaches a zone with many known long positions
(from MMT's liquidation heatmap) and you see the first liquidations starting on aggr.trade, you
can anticipate a cascade and act before it fully plays out.

### Technical Details

- Vue.js SPA, runs in browser, each exchange handled by a dedicated Web Worker
- For **historical data**: self-host `aggr-server` (separate repo) — connects same feeds, stores
  to InfluxDB v1.8, serves data back to the client via HTTP
- No public REST/WebSocket API exposed by aggr.trade itself
- Full open-source fork/extend available

### Integration with Your Bot

aggr.trade data is read visually — it is not a data source you poll programmatically (unlike
Binance REST endpoints). However, since aggr.trade connects to the same Binance WebSocket feeds
you already use, you can replicate the core insight:

- **From Binance `aggTrades` WebSocket**: stream of aggregated trades in real time
- **From Binance `forceOrder` WebSocket**: liquidation events in real time
- Build a lightweight CVD tracker using `aggTrades` (sum buy volume − sell volume per minute)
- Log liquidation events per symbol and alert when a spike is detected

This is the "free DIY" path if you want order flow signals without running a separate UI.

### Actionable Next Steps — aggr.trade

1. Open aggr.trade in browser, load BTCUSDT from Binance Futures
2. Set bubble threshold to $100k — observe how large orders cluster around key levels
3. Watch a session open (London or New York) — note how CVD and price interact in the first 30
   minutes
4. Compare what you see on aggr.trade to what your bot is currently monitoring — identify gaps
5. Optionally: subscribe to `aggTrades` WebSocket in your bot and track a 5-minute CVD; add it
   to 1h Telegram summary

### Cost

| Access | Cost |
| ------ | ---- |
| aggr.trade web app | Free |
| Self-hosted aggr-server (historical data) | Free (infra cost only) |

---

## 5. Best Indicators for Crypto Futures Trading

### Priority Ranking (Impact vs Effort for Binance Futures)

Ranked by practical edge for a Binance Futures trader:

#### Tier 1 — Highest Impact (Use These First)

#### 1. Liquidation Heatmap

- What: Shows where stop-loss clusters and forced liquidations will trigger as price moves
- Why it matters: Crypto is heavily leveraged; market makers target these zones
- TradingView: Not natively available; available via MMT, Coinglass, Hyblock
- Pine Script: Cannot build from public data alone (requires private liquidation data)
- Free option: Coinglass.com has a free liquidation chart

#### 2. Funding Rate

- What: The cost of holding a futures position (paid every 8h); when funding is very positive,
  longs are paying shorts, indicating crowded long positioning
- Why it matters: Extreme funding (>0.1%/8h) often precedes sharp corrections; near-zero or
  negative funding often precedes rallies
- TradingView: Available as a free indicator (search "Funding Rate"); also on Coinglass
- Pine Script: Can pull via `request.security()` if a feed exists; easier to use a pre-built
  public indicator
- Availability: Directly from Binance API, free

#### 3. Open Interest (OI) + OI Change

- What: Total notional value of all open futures contracts; rising OI = new money entering,
  falling OI = positions being closed
- Why it matters: Price + OI divergence is one of the most reliable trend-continuation or
  reversal signals
- TradingView: Free indicators available (search "Open Interest"); Binance OI visible in the
  "Binance" data feed
- Pine Script: Can be plotted if you use a source that provides OI as a security feed
- Availability: Binance `/fapi/v1/openInterest` — free

#### 4. CVD — Cumulative Volume Delta

- What: Running total of (buy volume − sell volume); measures aggressor pressure
- Why it matters: Reveals whether moves are driven by real buying/selling pressure or passive
  order flow; divergence between price and CVD is a leading reversal signal
- TradingView: Available (some free, some Pro-only scripts exist); search "CVD" or "Volume Delta"
- Pine Script: Buildable if you have access to tick data; approximated with candle
  open/close direction heuristic on lower timeframes
- Note: True CVD requires tick-level data; approximations work but have limitations

#### Tier 2 — High Value, Worth Adding

#### 5. VWAP (Volume Weighted Average Price)

- What: Average price weighted by volume; represents the fair value for the session
- Why it matters: Institutions often execute around VWAP; price returning to VWAP after
  extension is a common mean-reversion setup; strong trending days stay above/below it
- TradingView: Built-in free indicator (native)
- Pine Script: Easy to build custom (anchored VWAP, VWAP bands, session VWAP)
- Key variations:
  - Session VWAP (resets daily)
  - Anchored VWAP (anchored to a specific swing point — powerful)
  - Weekly/Monthly VWAP

#### 6. Market Profile / TPO (Time Price Opportunity)

- What: Displays how much time price spent at each level during a session, identifying
  the "Point of Control" (POC — most-traded price), Value Area High (VAH), Value Area Low (VAL)
- Why it matters: Price tends to rotate between value areas; breakouts from value area are
  high-probability continuation moves; returns to POC are common in range markets
- TradingView: Available on Pro+ and above (built-in); free alternatives exist as community
  scripts
- Pine Script: Buildable (complex but doable); several good open-source implementations exist
- Best use: Daily and weekly Market Profile for identifying key levels before the session

#### 7. Spot Orderbook Depth / Bid-Ask Walls

- What: Cumulative limit orders sitting at each price level on spot exchanges
- Why it matters: Large bid walls attract price (support); large ask walls repel price
  (resistance); their removal signals a coming move
- TradingView: Not available natively; use exchange's own orderbook or dedicated tools
- Pine Script: Not buildable (requires live L2 data, not available in Pine)
- Free option: Binance spot orderbook visible in the trading interface; Bookmap (paid) for
  full visualization

#### Tier 3 — Useful Context Indicators

#### 8. Spot vs Futures Basis / Premium

- What: Difference between spot price and futures contract price
- Why it matters: High premium = bullish sentiment (futures traders willing to pay up);
  discount = bearish sentiment or high supply pressure on futures
- TradingView: Can be computed manually with two securities; some indicators exist
- Pine Script: Buildable (compute `close_futures - close_spot` as a separate pane)

#### 9. Relative Strength vs BTC (RS)

- What: How an alt is performing relative to BTC on the same timeframe
- Why it matters: Alts showing strength vs BTC during a BTC pullback are candidates for
  leading higher; alts weaker than BTC during rallies are candidates for shorting
- TradingView: Easy to set up (set chart to "ALTUSDT/BTCUSDT" ratio)
- Pine Script: Simple to build

#### 10. Bollinger Bands + ATR

- What: Volatility envelope around a moving average; ATR measures average daily range
- Why it matters: Crypto mean-reverts to bands in range environments; band expansions signal
  trend starts; ATR sizing helps set rational stop-losses
- TradingView: Built-in free
- Pine Script: Built-in or easy custom

### Pine Script Custom Indicator Priority List

If building custom Pine Script indicators, prioritize in this order:

1. **Anchored VWAP** — high value, buildable, used by professionals
2. **Session VWAP with bands** — identify daily value area
3. **Funding Rate overlay** — plot from Binance feed or proxy
4. **OI Change % bar chart** — visual OI momentum
5. **Spot/Futures basis** — quick sentiment gauge
6. **TPO/Market Profile** — complex but powerful; good open-source Pine scripts exist to
   adapt

### TradingView Plan Requirements

| Indicator | Free | Pro ($14.95/mo) | Pro+ ($29.95/mo) |
| --------- | ---- | --------------- | ---------------- |
| VWAP | Yes | Yes | Yes |
| Bollinger, ATR | Yes | Yes | Yes |
| Funding Rate (community) | Yes | Yes | Yes |
| OI (community) | Yes | Yes | Yes |
| Market Profile | No | No | Yes |
| More than 5 indicators/chart | No (5 max) | Yes (25 max) | Yes (25 max) |
| Multiple charts same tab | No | No | Yes (up to 4) |
| Alerts (limit) | 20 | 20 | 100 |

> Bottom line: **Pro ($14.95/mo) unlocks the most important limit** — more indicators per
> chart. Pro+ adds Market Profile and multi-pane layout, which is genuinely useful.

---

## 6. Periodic Market Summary Feature (Bot Enhancement)

### Feature Overview

Add a periodic Telegram broadcast — every 15 minutes, 1 hour, or 4 hours — that gives a
concise market overview. This replaces the need to open a terminal or chart just to get
context.

### Data to Include

**15-Minute Summary** (tactical — what's moving right now)

- Top 3 gainers and top 3 losers from your watchlist (% change, 15m)
- Any symbol with a candle body > 1.5% in last 15m (momentum alert)
- BTC and ETH current price + 15m change
- Any position currently at > 80% of take-profit or stop-loss

**1-Hour Summary** (operational — session context)

- All watchlist symbols: price, 1h change %, 24h change %
- Current funding rate for BTC and ETH (fetch from Binance)
- Open Interest change in last 1h for BTC (rising/falling/flat)
- Which session is currently active (Asia / London / New York / Dead Zone)
- Any symbols near a key level (within 0.5% of weekly high/low)
- Current bot positions: entry, current PnL, distance to stop

**4-Hour Summary** (strategic — daily planning)

- 4h candle overview: trending up/down/ranging for each watchlist symbol
- BTC dominance direction (narrative context)
- Funding rate extremes: any symbol with funding > 0.05% or < -0.05%
- OI trend: 4h OI change for BTC
- Daily bias note (from pre-computed time_bias.json): "Today is Tuesday — historically
  52% bullish on BTC"
- Weekly high/low for each symbol (key levels for the day)

### Architecture

```text
scheduler/
  market_summary.py       — orchestrates data fetching and message formatting
  formatters/
    price_formatter.py    — formats price changes with emoji direction arrows
    funding_formatter.py  — formats funding rate data
    oi_formatter.py       — formats OI data
```

**Data Sources (all Binance REST, no new dependencies)**:

| Data | Endpoint |
| ---- | -------- |
| Price + 24h change | `/fapi/v1/ticker/24hr` |
| Funding rate | `/fapi/v1/fundingRate` |
| Open Interest | `/fapi/v1/openInterest` |
| OI history | `/futures/data/openInterestHist` |
| Recent candles | `/fapi/v1/klines` |

**Scheduling options**:

- Option A: `schedule` library — simple cron-like scheduling in Python, no new infra
- Option B: `asyncio` event loop with `asyncio.sleep()` — fits well if bot becomes async
- Option C: System cron (`crontab`) calling `buibui.py summary --interval 1h` — cleanest
  separation, easiest to manage independently

Recommendation: **Option C (cron)** — keeps the summary feature independent, easy to
enable/disable, and avoids complexity in the main bot loop.

**Telegram Message Format** (1-hour example):

```text
📊 1H Market Summary — 14:00 UTC | London Session
BTC  $84,200 | 1H: +0.8% | 24H: +2.1%
ETH  $3,820  | 1H: -0.2% | 24H: +1.4%
SOL  $178    | 1H: +1.4% | 24H: +3.2%  <- TOP MOVER
Funding (8H):
BTC: +0.012%  ETH: +0.008%  SOL: +0.031%
OI (1H): BTC +2.3% (new longs opening)
Session: London Open (historically higher probability)
Day bias: Tuesday | BTC 52% bullish (2022-2026)
Positions:
BTCUSDT Long | Entry: $83,100 | PnL: +$340 (+1.3%)
```

**Implementation Steps**:

1. Add `monitor/market_summary_lib.py` — pure logic (follows existing lib pattern)
2. Add `monitor/market_summary.py` — thin wrapper
3. Add `buibui.py summary` subcommand with `--interval` flag
4. Add cron entry to systemd service or crontab on Oracle VM
5. Write tests for formatter functions (mock Binance client responses)

**Estimated build time**: 3–4 days for solid v1 with 1h summaries; 15m and 4h variants
are minor additions once the base is built.

---

## 7. Minimum Cost Setup for Trading Excellence

### Fixed Costs You Already Have

- Oracle Cloud Free Tier VM — $0 (covers VPS/server)
- Python bot (this repo) — $0
- Binance API — $0 (data is free for account holders)

### Additional Tool Costs

#### TradingView

| Plan | Monthly | Annual (÷12) | Key Unlock |
| ---- | ------- | ------------ | ---------- |
| Free | $0 | — | 5 indicators, 1 chart, 20 alerts, no Market Profile |
| Essential | $9.95/mo | ~$8.25 | 10 indicators, 2 charts |
| Pro | $14.95/mo | ~$12.42 | 25 indicators, multiple charts, 400 alerts |
| Pro+ | $29.95/mo | ~$24.92 | Market Profile, 4 charts, 400 alerts |
| Premium | $59.95/mo | ~$49.92 | More data, priority support |

Recommendation: **Pro ($14.95/mo)** is the sweet spot. Pro+ only if you actively use
Market Profile (which you should if you're serious about institutional levels).

#### Market Data / Analytics

| Tool | Free Tier | Paid |
| ---- | --------- | ---- |
| Coinglass | Yes (liquidation maps, OI, funding — delayed) | ~$29–49/mo for real-time |
| MMT (mmt.gg) | Yes (limited) | ~$49–79/mo |
| BrighterData | Yes (limited) | ~$29–49/mo |
| aggr.trade | Yes (fully free, open-source) | $0 (self-host aggr-server for history) |
| Hyblock Capital | Yes (limited liquidation heatmap) | ~$39–79/mo |
| Velo Data | No | ~$99/mo (institutional grade) |

#### Indicators / Scripts

| Tool | Cost |
| ---- | ---- |
| TradingView community Pine scripts | Free |
| Premium Pine script authors (e.g., Luxalgo, Market Cipher) | $50–150 one-time or /mo |
| Bookmap (orderbook visualization) | ~$49/mo |

#### News / Sentiment

| Tool | Cost |
| ---- | ---- |
| CryptoPanic (news aggregator) | Free (basic), $15/mo (Pro with API) |
| Santiment | Free (limited), $45–150/mo |
| The Tie | Institutional, custom pricing |
| Unusual Whales (crypto macro) | ~$50/mo |

### Tiered Recommendations

#### Minimum Tier — $0/month

**Goal**: Smarter trading with zero additional spend

- TradingView Free (5 indicators — use them wisely: VWAP, Bollinger, Volume, Funding, OI)
- Coinglass Free (check liquidation heatmap manually before each trade)
- MMT Free (explore liquidation map, note key levels weekly)
- BrighterData Free (session breakdown, check once per day)
- aggr.trade (fully free — open in browser, watch order flow during sessions)
- Your bot (this repo) — add funding rate and OI to your existing Telegram messages
- Time-based analysis script — build it once, run weekly, costs $0

**Limitation**: No real-time data, manual workflow, limited chart layouts

---

#### Recommended Tier — ~$45–60/month

**Goal**: Professional workflow without breaking the bank

| Tool | Cost |
| ---- | ---- |
| TradingView Pro | $14.95/mo |
| Coinglass Pro | ~$29/mo |
| Oracle VM | $0 |
| Bot (this repo) + market summary feature | $0 |
| **Total** | **~$44/mo** |

**What this buys you**:

- TradingView Pro: 25 indicators, 400 alerts, proper multi-chart setup
- Coinglass Pro: Real-time liquidation heatmaps, OI, funding across all exchanges
- Bot: Automated market summaries, funding alerts, no need to watch charts constantly

**Add optionally**: BrighterData free tier for session stats (no cost add-on)

---

#### Power Trader Tier — ~$130–180/month

**Goal**: Near-institutional data access, maximum edge

| Tool | Cost |
| ---- | ---- |
| TradingView Pro+ | $29.95/mo |
| MMT Pro | ~$49–79/mo |
| BrighterData Pro | ~$29–49/mo |
| CryptoPanic Pro | ~$15/mo |
| Oracle VM | $0 |
| **Total** | **~$125–175/mo** |

**What this buys you**:

- Market Profile on TradingView (identify institutional value areas)
- MMT: Real-time liquidation heatmap, CVD, OI, multi-exchange orderbook
- BrighterData: Full time-based statistics, P1/P2 pivots, session bias
- News feed with API access for sentiment scanning
- Full automation pipeline: bot fetches data, applies time-bias filter, sends targeted alerts

**ROI threshold**: This tier is justified if you're trading with $10,000+ and your
monthly trading fees/losses are already exceeding $175/mo. If you're profitable at
$50/mo tooling, don't rush to Power tier.

---

### Priority Order for Incremental Investment

1. **First**: Build the time-based analysis script yourself (free, high value)
2. **Second**: Add funding rate + OI to your bot's Telegram output (free, 1 day of work)
3. **Third**: Upgrade to TradingView Pro ($15/mo) — removes the biggest friction point
4. **Fourth**: Add market summary feature to bot (free, ~3 days of work)
5. **Fifth**: Coinglass Pro ($29/mo) when you're trading with enough size that real-time
   liquidation data changes your decisions
6. **Sixth**: MMT or BrighterData Pro when you've exhausted the free tier value

---

## Summary: Recommended Implementation Roadmap

| Priority | Task | Cost | Effort | Impact |
| -------- | ---- | ---- | ------ | ------ |
| 1 | Add funding rate + OI to bot Telegram output | $0 | 0.5 day | High |
| 2 | Build time-based analysis script (KillaXBT style) | $0 | 2–3 days | High |
| 3 | Upgrade TradingView to Pro | $15/mo | 0 | High |
| 4 | Use Coinglass free for liquidation heatmaps | $0 | 0 | High |
| 5 | Use aggr.trade during active sessions (London/NY open) | $0 | 0 | High |
| 6 | Add periodic market summary (1h Telegram) | $0 | 3–4 days | Medium-High |
| 7 | Add CVD tracker to bot via Binance aggTrades WebSocket | $0 | 1–2 days | Medium-High |
| 8 | Explore BrighterData free tier | $0 | 0.5 day | Medium |
| 9 | Implement active-hours filter in bot | $0 | 1 day | Medium |
| 10 | Upgrade Coinglass or MMT to Pro | ~$29–79/mo | 0 | Medium |

**Total minimum spend to get to Recommended Tier**: ~$15/mo (TradingView Pro only)
**Total bot development work**: ~7–10 days across all features
