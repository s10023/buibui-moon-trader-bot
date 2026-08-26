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

- `buibui brief` — daily market brief (levels/zones/regime/seasonality/pundit board; `make buibui-brief`). The brief also carries an indicator-state block per symbol: EMA 20/50/200 stack, regime run-length with range bounds, Monday-range position, yesterday's candle patterns, PA character (impulse/grind/chop), Bollinger + weekly/monthly anchored-VWAP reads, and a 60-day volume-profile POC/VAH/VAL — all computed from our own OHLCV. An M2 session layer adds a bundle-level session clock (Asia/London/NY in MYT) plus a per-symbol recap of the last 3 completed sessions (net move and range in ATR units, set-extreme markers, partial-coverage flags) and the 180-day session high/low tendency percentages. The Brief can also surface operator-verified external levels (Coinglass/MMT liquidation heatmaps and maps, ingested from manual screenshots via `/ingest-charts`) as a per-symbol External block with snapshot age and ATR distances; snapshots older than 48h drop out with a health note. An M5 big-picture layer adds a Month line (month-to-date return, its percentile against completed prior months, range position, share of month elapsed) and a Week line placing the forming week inside the weekly path cone (direction of the path so far, hours elapsed since the Monday 00:00 UTC open, move in AWR14 units, percentile against both same-direction and all past weeks, and where the week's low/high closes have landed). The weekly cone is conditional on **outcome, not a forecast** — weeks are grouped by how they closed, so the bull band sits above the unconditional band by construction and mid-week you do not know which group applies. The brief also opens with a bundle-level **bear score** read from BTC rather than from the panel symbol: how many of the 50W SMA, 50W EMA, 200D EMA, 200D SMA, 20W SMA and 21W EMA BTC's completed daily close sits below (0-6), the nearest average below price as the level whose break adds a point, its distance in percent, and how many days the current score has held. The four weekly averages are resampled from daily bars and exclude the in-progress week. It is reported as a number for context only and never gates, sizes or suppresses anything — the effect behind it was measured over dates spanning only about three distinct bear markets. All blocks render in the `buibui brief` CLI, `GET /api/brief`, and the Brief tab.

  Screenshot drop-filename examples (dir `docs/plans/chart-drops/`; pattern `<source>[-<venue>]_<SYMBOL>[_<YYYYMMDD[-HHMM]>[_<label>]].png|.jpg|.jpeg` — source lowercase `coinglass`|`mmt`, SYMBOL uppercase perp name, timestamp = your MYT wall clock. The panel type is NOT in the name — vision detects heatmap vs map, and reads the window off the chart. `<label>` is free text after the timestamp, must start with a letter, and is **parsed then discarded**: it exists so a burst of panels captured in the same minute can still be given distinct, readable names, since bumping the minute is otherwise the only way to tell two drops apart. It is not a source of truth — naming a file `_1y` does not make its `window` 1y):

  ```text
  coinglass_BTCUSDT_20260715-0930.jpeg   # Liq Heatmap 24h  (daily pair, 1 of 2)
  coinglass_BTCUSDT_20260715-0931.jpeg   # Liq Map 1d       (daily pair, 2 of 2 — bump the minute)
  coinglass_BTCUSDT_20260715-0935.jpeg   # optional Heatmap 1w when swing-planning
  coinglass_BTCUSDT_20260824_Map_7d.png  # label: a same-minute burst, one file per window
  coinglass_BTCUSDT_20260824_Map_1y.png  # ...the label is for YOUR eyes; vision reads the window
  mmt_ETHUSDT_20260715.png               # date-only is fine (time-of-day from file mtime)
  mmt_SOLUSDT.png                        # no timestamp at all — capture time = file mtime
  ```

- `buibui card SYMBOL` — AI trade card (F2): feeds the brief panel, pundit board, XS target, recent fires, and live account into an LLM (`claude -p`, subscription auth) with a fixed rubric, then deterministically sizes and rule-checks the result in code (VETOED on violation). Sizing uses **live account equity** when it is available, falling back to the configured `[portfolio] capital`; each card records which it used. Because real equity is far below the old $10k default, the sub-lot veto is common — a BTCUSDT stop wider than roughly 2.7% at ~$1,200 equity leaves a risk budget under one LOT_SIZE step and is rejected. Advisory only, no order routing. Every call logs to `docs/plans/ai-cards.jsonl`. `--dry-run` prints the state + prompt with no LLM call. `--telegram` additionally pushes the rendered card to Telegram (opt-in per run; every verdict, VETOED included). `--horizon intraday|swing` sets the horizon the card reasons at and the scoring window its ledger row is resolved against (48h vs 30d). `make buibui-card SYMBOL=BTCUSDT [DIRECTION=] [HORIZON=] [AS_OF=] [DRY=1] [TG=1] [CONFIG=]`.

- `buibui param-audit | param-sweep` — walk-forward optimization (WFO) parameter tools.
  `param-audit` reports how each strategy × timeframe's current parameters hold up
  out-of-sample; `param-sweep` searches the grid and prints the per-cell winners. Wrapped by
  `make buibui-param-audit` / `make buibui-param-sweep`. `/wfo-sweep` is the trusted
  production path for committing the resulting `tp_r` values to TOML.

- `/ingest-video` *(Claude Code skill)* — turn a pasted YouTube or X video URL, including
  Chinese-language video, into routed research items. Fetches metadata + transcript
  (`tools/video_fetch.py`: yt-dlp captions, Groq `whisper-large-v3` fallback for
  caption-less video, per-video dedup cache), then two sonnet subagent passes — text-only
  segmenting/ranking, then vision over a small set of transcript-selected frames
  (`tools/video_marks.py`; frames follow deictic phrases and spoken price levels, never
  scene-change, so a 38-minute video costs 8–15 images instead of ~100). The in-video call
  time is resolved deterministically in code (`tools/video_calltime.py`, never by model
  date arithmetic) — a stated time is preferred but bounded below the video's publish
  timestamp, so a backlog video can't be scored against price action the speaker had
  already seen. One consolidated review digest, one approval, then routes into the same
  three research streams as `/ingest-x` plus a durable per-video note under
  `docs/plans/video-notes/`.

- `/ingest-feed` *(Claude Code skill)* — auto-discovers new uploads instead of waiting for
  a pasted URL. `tools/yt_feed.py poll` reads each followed channel's uploads playlist
  (read-only YouTube Data API v3, `YOUTUBE_API_KEY`, ~2–3 quota units/channel/day, never
  `search.list`); `backfill <UC…>` deep-pages a single channel's back catalogue. Follow
  list lives in gitignored `config/youtube_channels.toml`. The skill presents candidates
  (title/duration/age/est-token rank) for the operator to pick, then hands the picked URLs
  to the existing `/ingest-video` flow unchanged — every research-sink write still happens
  behind that skill's single approval gate. Consumption is stamped only by an explicit
  `mark` call after routing completes (never at fetch time), so an aborted run re-presents
  the same videos next poll instead of silently losing them. Curated `PL…` playlists need a
  second mechanism for the same guarantee, because they are paged in tranches against a
  cursor rather than tracked per video: `mark` holds the whole tranche whenever a video it
  offered went undecided, so deferring one is a real outcome there too rather than a silent
  drop.

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

Top-level orientation only. **The per-module reference lives in `.claude/context/` —
this section deliberately does not duplicate it.** It used to, and the duplicate rotted:
before 2026-08-04 this tree was missing ten of eleven packages and still described
`data_store.py`, `signal_lib.py`, `stats_lib.py` and `backtest_lib.py` as the real
engines, when all four have been thin re-export shims since the Phase-2 package split.

```text
buibui-moon-trader-bot/
├── buibui.py            # CLI entry shim → cli.main:main
├── cli/                 # argparse subcommands (monitor, signal, analytics, backtest, …)
├── analytics/           # DuckDB data layer: store, strategies, backtest, signal, stats,
│                        #   brief, exits, research guards — plus the research sleeves
│                        #   (forecast, xsmom, combine, carry, xsrev, cvd)
├── signals/             # Alerting + dedup daemon (detection itself lives in analytics/)
├── card/                # F2 AI trade card
├── portfolio/           # P1 paper-portfolio sizing + replay
├── trade/               # Execution layer: XS live wiring, routing, risk overlay
├── monitor/             # Live price / position monitors
├── web/                 # FastAPI backend (api/) + Svelte 5 UI (ui/)
├── tools/               # One-shot analysis + audit scripts
├── utils/               # Shared clients, Telegram, config validation
├── deploy/              # 24/7 VPS deploy kit (systemd timers)
├── migrations/          # One-off DB schema migrations
├── config/              # coins.json + pundit_roster.toml (gitignored), universe.toml, strategy_params.toml, eras.toml
└── tests/               # pytest suite
```

| Area | Deep reference |
| --- | --- |
| `analytics/` core | `.claude/context/analytics.md` |
| Research sleeves + their verdicts | `.claude/context/research-sleeves.md` |
| `signals/` · `card/` · `portfolio/` | `.claude/context/signals.md` |
| `web/` | `.claude/context/web.md` |
| `trade/` · `deploy/` · `monitor/` · `utils/` · `cli/` | `.claude/context/execution.md` |
| `tools/` | `.claude/context/tools.md` |

`AGENTS.md` carries the package index and the sleeve verdicts.

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
| **Daily Path Cone** | Today's intraday price path (in ADR units) overlaid on historical percentile bands for the same direction × weekday combo — shows whether today is tracking a typical, wide, or narrow day, plus % of matching days where the low/high is already in and typical pivot magnitude/timing. Historical bands cached; today overlay live — recomputed on every page load. | Toggle **All / Bull / Bear** direction and day-of-week filter. Hover the chart for a per-hour p90/p75/p50/p25/p10 readout (prices when live, else ADR×) plus today's value at that hour — hover-only, no keyboard equivalent. |
| **Weekly Path Cone** | The daily cone one horizon up: the forming week's path (in AWR units) over 168 hourly bars, Monday 00:00 UTC → Sunday 23:00 UTC, overlaid on historical percentile bands for weeks that closed the same way — with the **unconditional** band always drawn underneath as the reference. Conditioned on direction only, so weekday becomes the x-axis. Historical bands cached; current-week overlay live. **Read it as conditional on outcome, not a forecast:** a "bull week" is *defined* by its close, so the bull cone sits above the unconditional band by construction and that gap carries no predictive information — at mid-week you don't know which cone you're in. | Toggle **Bull / Bear** for the conditional cone; the gray unconditional band stays. Hover for a per-hour p90/p75/p50/p25/p10 readout as prices, labelled in **UTC and MYT** (the axis is UTC because the week is UTC-anchored). |
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

Requires **Python >= 3.13** and [Poetry](https://python-poetry.org/).

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

## Scheduled alerts (GitHub Actions)

`.github/workflows/signal-watch.yaml` runs the signal daemon on an **hourly cron**
and fires Telegram alerts — no always-on host required.

> **Status + reality check (measured 2026-07-23).** This workflow is currently
> **disabled**. GitHub queues and drops scheduled runs under fleet load: across the
> last 200 runs the hourly cron delivered **35%** (only 12.6% on time, median gap
> 2.6h, max 6.7h), so **65% of 1h candles were never scanned**. Worse, the drops are
> not random — run-hour distribution is non-uniform at p<0.01 (Asia 0.70x,
> Off-hours 1.62x), which makes the resulting ledger *session-skewed*, not merely
> thin. Do not treat a scheduled-cron ledger as a clean out-of-sample sample.
> Mitigation is `--catch-up` below: it decouples ledger completeness from run
> frequency, so a single run per day yields the same rows as a perfect hourly cron.

- **`--catch-up` (off by default).** Replays every un-alerted **closed** candle
  since the last run instead of only the newest, so a skipped cycle no longer
  loses those ledger rows. Backfilled candles are recorded but **never sent to
  Telegram** — only the newest closed candle can alert, since a signal that old is
  not tradeable. A cold-start guard keeps a fresh `signal_state.json` from bursting
  the whole window. Depth is bounded by the 200-candle scan window (~8 days on 1h,
  ~33 on 4h, ~200 on 1d). Caveat: gating context (regime / HTF-EMA / ADR / DOW /
  star ratings) is evaluated as-of-now, so a backfill reaching past a ratings
  refresh is look-ahead in the gating and should be read as backtest, not OOS.
  Enable with `make buibui-signal-watch CATCH_UP=1` or `--catch-up`.

- **Data source: OKX.** GitHub-hosted (US) runners are geo-blocked from Binance
  (HTTP 451) and Bybit (403), but OKX V5 public market data is reachable. Set
  `DATA_SOURCE=okx` to select the keyless `utils/okx_client.py` adapter; the daemon
  entry point is `buibui signal watch --once` (single scan cycle, then exit).
- **Calibration is committed, not recomputed.** `make export-live-db` writes a slim
  `live_signal.duckdb` (~7 MB: `ohlcv_all` + `confidence_ratings` + combo tables, **no**
  `backtest_trades`) by reading your local Binance `analytics.db` **read-only**. It is
  committed in **plain git** (public-repo checkout bandwidth is free; Git LFS bandwidth
  is metered even on public repos). Re-run `make export-live-db` and commit it whenever
  calibration changes (e.g. after `make db-update`) — not every code change, to keep
  history lean. Star ratings / combos stay Binance-derived; only the `min_avg_r` gate
  recomputes on OKX candles, so it stays self-consistent.
- **The runner never touches your data.** It copies `live_signal.duckdb` to an
  ephemeral `analytics.db` inside the runner, incremental-syncs new OKX candles, scans,
  and persists only `signal_state.json` (cooldown/dedup) via `actions/cache`. No DB is
  ever written back or committed. Locally, `DATA_SOURCE` defaults to `binance`, so
  `make db-update` is unaffected.
- **Required repo secrets:** `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
- **Caveat — `cvd_divergence`:** OKX candles lack taker-buy volume, so OKX-synced rows
  set `taker_buy_volume = NULL` — never a fabricated `volume / 2`, which is what the
  adapter wrote until 2026-08-21. Committed Binance history keeps real taker volume;
  `cvd_divergence` drops NULL rows and returns no signals, rather than crashing or firing
  on a fabricated flat series.
  ✅ **The overwrite is now PREVENTED, not merely visible (2026-08-21).** `ohlcv` is a
  view over `ohlcv_all`, whose primary key is `(venue, symbol, timeframe, open_time)`, so
  an OKX row lands **beside** the Binance row for the same slot instead of replacing it.
  Reads resolve one row per slot from `db_meta.read_venue_order` — `binance` on your local
  DB, `okx,binance` on the committed slim DB so the runner keeps its intended mixed series
  (Binance history underneath, the OKX tail on top). Pointing `DATA_SOURCE=okx` at your
  real `analytics.db` is no longer destructive: those rows are simply invisible to the
  default view, and re-backfilling from Binance restores the intended reading.
  ⚠ **A database created before that change must be migrated once** —
  `tools/migrate_ohlcv_venue.py`, after a backup; `init_schema` refuses to run until then
  rather than half-applying anything. See `deploy/README.md` § *Schema migrations*.

### Maintenance — re-export cadence

Each run starts from the **committed** `live_signal.duckdb` (the runner's working DB is
ephemeral and discarded), so every hourly run re-syncs the **entire gap** from the
snapshot's newest candle up to now — not just the last hour. Two things drift as the
committed snapshot ages:

1. **OHLCV gap → eventual holes.** OKX's `/market/candles` only serves a bounded window
   of recent candles, and the shortest timeframe (`15m`) exhausts it first. If the
   snapshot goes stale beyond OKX's reach, the incremental sync can no longer bridge the
   gap and you get missing candles.
2. **Frozen calibration.** `confidence_ratings` (stars), `backtest_combos`, and
   `backtest_cross_tf_combos` never update on the runner — they are whatever the last
   export captured. Same-candle confluence grouping (`Confluence: N strategies`) is
   computed live and is unaffected, but the **combo / cross-TF historical-edge tagging**
   only recognises pairs present at export time: a pair discovered by a later backtest is
   silently skipped (the signal still fires, just without its combo stats) until you
   re-export. Star-gated alert quality drifts the same way.

**Fix — re-export and commit periodically:**

```sh
make export-live-db && git add live_signal.duckdb && git commit -m "build: refresh live DB" && git push
```

Best run **right after `make db-update`** (refreshes calibration *and* advances the OHLCV
snapshot in one step), and at minimum **weekly** so the `15m` gap never outruns OKX's
recent-candle window. Everything else (OKX sync, dedup state, alerts) is automatic.

### Pausing while running the daemon locally

The cron job and a local `signal watch` daemon do **not** share dedup state (the runner
uses `actions/cache`, your laptop uses its own `signal_state.json`), so running both
fires **duplicate** alerts. Pause the cron before a local session and re-enable after:

```sh
gh workflow disable signal-watch.yaml      # stops the hourly cron + blocks manual dispatch
# ... run the local daemon ...
gh workflow enable signal-watch.yaml       # resume
```

It's a persistent state toggle (survives across runs until flipped back); an in-flight
run still finishes. The same toggle lives in the **Actions** tab → **Signal Watch (OKX)**
→ **⋯** → **Disable workflow**. Note scheduled workflows only fire from the **default
branch**, so the cron does nothing until this workflow is merged to `main`.

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

> **Note — how SL/TP columns are sourced.** `SL Price`, `% to SL`, `SL USD`, and
> `TP Price` are populated by reading open `STOP_MARKET` / `STOP` and
> `TAKE_PROFIT_MARKET` / `TAKE_PROFIT` orders from **both** Binance order books:
> the classic one (`/fapi/v1/openOrders`) and the conditional/algo one
> (`/fapi/v1/openAlgoOrders`). Binance migrated all conditional order types —
> including UI-placed Position TP/SL and standalone stop orders — to the algo
> subsystem on 2025-12-09, so the monitor queries both and merges the results.
> The old limitation ("Position TP/SL is not exposed through any public REST
> API") no longer applies: TP/SL set via the position card or at order opening
> now shows in the monitor.
>
> **Naked tails.** The monitor checks stop *coverage*, not only price.
> When the working stops on a position side sum to less than the position,
> `SL Price` renders `⚠ <price> (N% naked)`. This catches a trailing stop
> re-armed for only part of a position — the failure that left three positions
> partly unstopped for four hours on 2026-08-04. A `closePosition` stop counts
> as full cover whatever its quantity says, take-profit orders never count as
> protection, and the flag stays silent wherever coverage cannot be determined
> (a warning that fires on every row is one you learn to ignore).

### Analytics — Backfill Historical Data

The analytics module stores OHLCV candles, funding rates, and open interest in a local
DuckDB database for offline analysis and strategy backtesting.

**First run — backfill historical data:**

```bash
poetry run python buibui.py analytics backfill --since 2023-01-01
```

Options:

- `--since YYYY-MM-DD` — start date for backfill (default: `2023-01-01`); also bounds funding-rate history depth (a deep backfill now pulls full funding history, not just the recent ~90 days)
- `--symbols BTCUSDT ETHUSDT` — symbols to fetch (default: all coins in `config/coins.json`)
- `--universe` — fetch the committed 25-perp research universe from `config/universe.toml` instead (mutually exclusive with `--symbols`; criterion + refresh tool: `tools/select_universe.py`)
- `--timeframes 1h 4h 1d 1w` — timeframes to fetch (default: `1h 4h`)

**Deep universe backfill (research breadth):**

```bash
make universe-backfill            # --universe, 1h/4h/1d/1w, since 2019-01-01
```

Every backfill/sync run also refreshes the `symbol_lifecycle` table from
futures exchangeInfo — symbols that disappear from the exchange are marked
`DELISTED` (noted, never dropped) so the research breadth set stays
survivorship-aware. Coverage audit: `tools/data_coverage_report.py`.

**Incremental sync — fetch new candles since last stored:**

```bash
poetry run python buibui.py analytics sync
```

Options:

- `--symbols` / `--universe` / `--timeframes` — same as backfill
- Requires backfill to have been run first for each symbol/timeframe

Data is stored in `analytics.db` (auto-created in CWD).

**`analytics.db` is gitignored and single-copy — back it up.** It holds the live
outcome ledger, which is *not* reconstructible: exchanges do not re-serve historical
signal fires, and restarting collection yields a differently-biased sample rather than
an equivalent one. The committed `live_signal.duckdb` is **not** a substitute — it ships
the schema with an empty `signal_alert_outcomes` table.

```bash
./deploy/backup-analytics.sh --dry-run   # see what would be captured
./deploy/backup-analytics.sh --weekly    # verified snapshot + portable parquet export
```

Snapshots land in `~/backups/buibui` (`BUIBUI_BACKUP_ROOT`), staged and renamed only
after verification so a crash cannot leave a plausible-looking bad backup. A systemd
user timer runs it twice daily — see `deploy/README.md` for install, retention, restore,
and log commands.

That is the **local** leg only: every copy it writes shares the laptop's disk, so it
survives a fat-finger delete and not a dead drive. The off-machine leg is separate:

```bash
./deploy/backup-offsite.sh --dry-run     # rclone sync of BUIBUI_BACKUP_ROOT to a remote
```

It needs `rclone config` (interactive) and `BUIBUI_BACKUP_REMOTE` in `.env` first, and
**exits 1 until that is set** so an enabled timer complains rather than looking green
while nothing is being copied. It uses `sync`, so remote retention follows local
retention — and therefore mirrors deletions, which is why it refuses to run when the
backup root contains no `MANIFEST.json` instead of syncing an empty tree over your only
remote copy. It mirrors deletions into the **destination** just as readily, so it also
refuses a remote with no path component (a bare `remote:` is the entire drive) and a
destination holding entries your local root does not have — and the remote itself is
pinned to the backup folder via rclone's `root_folder_id`. ⚠ That second check compares
**top-level entries only**, so it catches an unrelated folder but *not* a sibling repo
whose tree is the same shape; two repos sharing one drive each need their own remote and
their own `root_folder_id`. The step-by-step setup —
measured space requirement, provider choice, the confinement step, and the one provider
flag that is load-bearing rather than tidiness — is in
`deploy/README.md` under "Off-machine backup".

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

Six candle-pattern strategies above (`engulfing`, `pin_bar`, `inside_bar`,
`hammer_hanging_man`, `doji`, `morning_evening_star`) hard-code a flat 2% stop
(`--sl-pct` default) at every timeframe. `make buibui-sl-horizon-audit`
(`tools/sl_horizon_audit.py`) is a read-only audit that re-resolves those same
signals under an ATR-scaled counterfactual stop grid — verdict:
`docs/audits/2026-07-21-st9-sl-horizon.md`, **corrected 2026-08-14 by
`docs/audits/2026-08-14-st27-sl-horizon-powered-null.md`** (its negative verdicts were
emitted from failure to clear the bar rather than from a CI; 22 of 30 do not survive the
corrected one-sided criterion, and the surviving `CONFIRMED-BAD` is 15m only).

`make buibui-weekly-path-audit` (`tools/weekly_path_audit.py`) is a read-only
audit that tests whether a week's AWR-normalized partial path at hour `h`
predicts the return from `h` to the week's close, beyond drift — see spec
`docs/superpowers/specs/2026-07-23-h10-partial-path-predictiveness-design.md`.

`make buibui-indicator-condition-audit` (`tools/indicator_condition_audit.py`)
is a read-only audit that tags every historical trade with the M1 brief-indicator
state (EMA stack/slope, regime, Bollinger, anchored-VWAP distance, volume-profile
value-area, price-action character, Monday-range) that held at its entry, then
emits a BUILD / AVOID / NO-EDGE / INSUFFICIENT verdict per (indicator-state ×
direction) — verdict `docs/audits/2026-07-24-h8-m1-indicator-conditioning.md`.

`make buibui-occurrence-dump` (`tools/occurrence_dump.py`) is a read-only
diagnostic that dumps one row per strategy fire together with the M1 indicator
state that held at its entry, so the conditions separating good fires from bad
are visible per strategy rather than pooled. It emits **no verdict**:
conditioning axes are 6-for-6-plus-one-amended NO in this repo and the 1d
timeframe alone yields 757 cells, so a gated search over them is unreachable by
trial count — the tool prints that cell count rather than asserting the point.
Scope with `TF=` for runtime (15m is ~4h); an unscoped run is still
statistically sound, unlike the H8 audit below, because its summary groups by
timeframe and shares no multiplicity family.

`make buibui-premium-state-audit` (`tools/premium_state_audit.py`) is a read-only
audit that tags every historical trade with the **Coinbase-premium** market state
(US-spot demand measured against Binance, plus the USDT peg deviation it is
routinely confounded with) as of the last completed daily close before entry,
collapses to one observation per UTC day, and emits the same BUILD / AVOID /
NO-EDGE / INSUFFICIENT verdict per (state × direction). The first conditioning
axis built from a **different venue's** order flow rather than a re-slice of the
same OHLCV — verdict `docs/audits/2026-08-04-h14-coinbase-premium-state-tag.md`
(NO-EDGE on all 10 pre-registered cells; **amended 2026-08-13 — all ten now read
INSUFFICIENT**, because the old verdict map treated `n >= min_n` as power and none
of the ten CIs actually excluded an effect at the bar. The direction of the finding
stands; "the axis is closed" does not).

`make buibui-carry-unwind-audit` (`tools/carry_unwind_audit.py`) is a read-only
audit that tags every UTC day with a **USD/JPY yen-strength** state — a run
counter of consecutive down weeks and a causal 4-week magnitude z-score, derived
from keyless Yahoo daily bars — and runs two panels through the same pre-committed
gate: BTCUSDT's daily return normalised by its causal trailing 30-day volatility
(primary), and the trade ledger (secondary). The second conditioning axis built
from **genuinely new data** rather than a re-slice of held OHLCV, after H14 —
verdict `docs/audits/2026-08-04-h15-usdjpy-carry-unwind.md` (NO-EDGE on every cell
in every panel; ⚠ **the "well powered" claim is withdrawn 2026-08-13** — it rested on
n=200–2,055 clearing `MIN_N`, which is a sample-size floor rather than power, and at
n=2,055 the MDE is ~2.4× the bar. On the corrected map every primary cell reads
INSUFFICIENT. The NO-EDGE conclusion still stands on the sign disagreement between
the two axes).

Bar units are not portable between those two panels: the gate's effect-size floor
is expressed in the units of the observation, so the forward panel uses
`BAR_VOL = 0.02` sigma-units while the ledger panel keeps `0.05` R. Reusing one
numeral across both would make a verdict structurally unreachable.

`make buibui-dead-surface-check` (`tools/dead_surface_check.py`) is a read-only
check for cells where declaration and output disagree, in both directions.
*Dead cells* are declared by a `signal_watch*.toml` but the detector has never
fired across the whole history — a declaration the system cannot honour, costing
detector work every scan cycle and returning nothing. *Orphaned ratings* are the
inverse: `confidence_ratings` rows for cells no config declares any more, which
`recalibrate` keeps refreshing because it rebuilds from historical
`backtest_runs` and has no notion of what the configs currently declare. Orphans
are inert at runtime (both read sites are keyed lookups, so a cell nothing scans
is never queried) but they inflate any population counted off that table. The
check is direction-aware, tiers orphans by whether *any* config still declares
them, and is report-only unless given `STRICT=1`. It never prunes — `recalibrate
--apply --config` does that (below). Note that orphans do **not** go stale:
recalibrate keeps refreshing their timestamps, so an orphan is a stale value
wearing a fresh one and nothing about the row looks wrong.

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

A second pruner removes rows for cells the config **no longer declares**. Without it
nothing ever did: the upsert only inserts-or-replaces, so a dropped cell kept its stars
forever (measured 2026-08-13 — a full `/db-update` left the orphan set at 206 → 206). It
is guarded by a share ceiling: if more than half of one config's rating rows would be
deleted, it **refuses and deletes nothing**, because a declaration resolver that
under-reports presents as mass deletion. That guard is a tripwire on the resolver, not a
policy knob — raise it only after reading `make buibui-dead-surface-check` output.

A third pruner removes rows **the pass that just ran produced no rating for**. Omission is
not deletion: `compute_recalibrated_ratings` skips a strategy under `--min-trades` and the
directional pass skips a direction under its own floor, so a cell that stayed *declared*
while falling below the floor kept its last stars forever — measured 2026-08-20 at 24 of 288
rows, the oldest stamped 2026-04-02, and **all 24 still declared**, which is why neither of
the other two pruners could reach them. This is not cosmetic the way an orphan is: a declared
cell is still scanned, and the live conflict resolver drops the lower-confidence side when
both directions fire on one candle, so a frozen star can silence the correct direction. It
carries its own share ceiling and refuses the same way — a mis-scoped pool (a missing
`adr_suppress_threshold` returns zero rows for two of the three configs) rates nothing, and
an unguarded delete would then wipe a whole config in one silent pass.

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

### Portfolio Replay — Paper-Portfolio Risk-Adjusted Numbers

Replays the resolved live outcome ledger (`signal_alert_outcomes`) through the Carver
two-layer position-sizing model (`docs/redesign/2026-06-05-p1-sizing-spec.md`) into an
overlapping-position paper book, then prints the system's risk-adjusted numbers —
Sharpe / Sortino / Calmar / max-drawdown / annualized return + vol / exposure / turnover,
plus a per-(strategy × tf × direction) P&L attribution. **Read-only** over `analytics.db`
(no Telegram, no DB writes, no schema changes).

```bash
# Defaults: $10k paper capital, 0.25% per-trade risk on stop, 20% annual vol-target,
# 2% concurrent-risk cap, 1% majors-cluster cap
poetry run python buibui.py portfolio replay
make buibui-portfolio-replay                       # equivalent

# Overrides
make buibui-portfolio-replay CAPITAL=25000 VOL_TARGET=0.30
make buibui-portfolio-replay CONFIG=config/strategy_params.toml   # optional [portfolio] block
```

`SizingConfig` validates every numeric field at construction, so a degenerate
`CAPITAL=` / `VOL_TARGET=` override or `[portfolio]` key raises immediately instead of
producing a plausible-looking report. Capital, per-trade risk, vol-target and the
annualization factor must be finite and positive; the caps and vol-governor bounds must
be finite and non-negative, with `g_vol_min <= g_vol_max`. Smallness is not degeneracy —
a genuinely tiny account is honoured, the same rule the card's live-equity path follows.

Two equity-curve bases are reported in parallel: **fixed-notional / constant-R** (the
headline Sharpe) and **compounding** (the vol-governor's feedback basis). The vol governor
is causal (reads only trailing realized vol strictly before each entry). Baseline verdict:
`docs/audits/2026-06-14-p1-portfolio-baseline.md`. The exit-policy replay (time-stop /
breakeven / partial-at-1R) is a follow-up that reuses this same paper book.

### XS Target Positions — Daily Read-Only Target Generator

Generates today's governor-scaled XS target positions (side, leverage, $notional at ~$10k)
from the latest causal EWMAC forecast stored in `analytics.db`. Saves a gitignored snapshot
to `docs/plans/xsmom_targets/<date>.json`. Read-only — no order routing.

```bash
make buibui-universe-sync               # sync universe OHLCV, all timeframes (XS reads 1d)
make buibui-xsmom-targets               # print today's target table + save snapshot
```

- `make buibui-universe-sync` — `analytics sync --universe --timeframes 1h 4h 1d 1w`
  (override with `TIMEFRAMES=`). **Must run before targets/executor**: the XS book runs on 1d
  bars only, and `analytics sync`'s default timeframes (`1h 4h`) never refresh 1d, which
  silently degenerates the book to majors-only. ⚠ **It covers all four timeframes, not just
  1d, since 2026-08-23 (ST61a)** — nothing else refreshes the universe's 1h/4h/1w, and when
  this target named 1d alone they sat frozen for up to 76 days with every check green.
- `make buibui-xsmom-targets` — read-only daily XS target-position generator. Accepts `--vol-target` (default 0.20) for parity with the executor.
  (`tools/xsmom_targets.py`): today's governor-scaled target positions
  (side · leverage · $notional at ~$10k) + a gitignored snapshot. No order routing.

### XS Order Routing — Overlay-Gated Executor

Turns the XS target book into Binance Futures orders, gated by a fail-closed risk overlay.
The pure routing/overlay logic and the injectable Binance adapter live under `trade/`;
`analytics/xsmom/` stays pure/read-only.

```bash
make buibui-universe-sync               # sync universe OHLCV first (see above)
make buibui-xsmom-daily                 # universe sync + executor dry-run (one command)
make buibui-xsmom-execute               # DRY-RUN: print the order plan, submit nothing
make buibui-xsmom-execute MODE=testnet  # submit on Binance Futures testnet (validation)
```

- `make buibui-xsmom-daily` — convenience wrapper: `buibui-universe-sync` then
  `buibui-xsmom-execute` (dry-run). The recommended daily command.
- **Dry-run is the default** — it sizes the book off live account equity, reconciles
  against current exchange positions into an order plan (LOT_SIZE rounding, no-trade
  band, min-notional skips; risk-increasing legs price as a post-only LIMIT at the
  book touch, risk-reducing legs stay MARKET), runs the overlay, and prints the plan
  **without submitting anything**.
- The **risk overlay** (fail-closed, blocks the whole plan on any breach): kill-switch,
  drawdown halt, gross-leverage cap, per-instrument notional cap, per-run turnover guard,
  data-staleness guard. Toggle the kill-switch with `--kill` / `--resume`. Correct a drawdown
  high-water mark that was never real with `--set-peak <value> --peak-reason "<why>"` — one-way
  (it refuses to RAISE the mark, which is the ratchet's job from live equity, and refuses a
  non-positive peak, which would put the floor at 0 and read as corrected while the breaker is
  OFF), and it never lifts a halt the corrected floor still justifies. Overlay defaults
  are calibrated to the real book envelope: `--vol-target 0.20` (validated; deploy first
  live cycles at `0.10`), `--max-gross-leverage 4.5`, `--min-active-positions 15` (breadth
  guard — aborts on a thin/degenerate cross-section), auto cold-start turnover allowance
  (lets the full book establish on day 1 without tripping the steady-state churn guard).
- `--mode testnet` validates the full order-submission path at zero capital risk (needs
  `BINANCE_TESTNET_API_KEY` / `BINANCE_TESTNET_API_SECRET`). `--mode live` is double-gated
  by `--i-understand-live` **and** `BINANCE_ALLOW_LIVE=1` (the mainnet flip is a later
  supervised step). Requires one-way position mode.

### XS executor dry-run output

`make buibui-xsmom-execute` (dry-run default) prints the target book as a Rich
table — one row per active leg (not just the legs that trade this cycle), sorted
by |notional| descending:

| Column | Meaning |
| --- | --- |
| SYM | Instrument |
| SIDE | LONG (green) / SHORT (red) |
| CUR→TGT | Current leverage → target leverage (signed, governor-scaled) |
| $NOTIONAL | Target dollar exposure (leverage × equity) |
| Δ$ | Dollar move this cycle (the order, if any) |
| MARK | Latest mark price |
| FCAST | Demeaned cross-sectional forecast (relative-strength signal) |
| ACTION | open / rebalance / close / hold (band) / skip:&lt;why&gt; |

The header summarises the book: `GOV` (vol governor), `GROSS` / `NET` leverage,
leg count, total gross notional. Leverage is vol-targeted and vol-parity — **not
1× per leg**; `--exchange-leverage` is only the Binance margin setting, separate
from the book's gross.

Three output versions:

- **dry-run plan** (default): the advisory plan; nothing is submitted.
- **⛔ BLOCKED by overlay**: a risk guardrail tripped — the book table still
  renders (so you see what was blocked) beneath the abort reasons; nothing
  submits.
- **testnet submit** (`--mode testnet`): same layout; the footer's `submitted` /
  `failed` counts reflect real orders placed on testnet.

### Signal Watch — 24/7 Strategy Alerts

Runs a polling daemon that scans closed candles every N seconds and sends Telegram alerts
when a strategy fires. Requires `analytics backfill` to have been run first.

> **Scheduled deployments should run `signal watch --once` on a timer, not this looping
> form.** Running both at the same time gives two writers racing `signal_state.json` and
> duplicate Telegram alerts. Unit files live in `deploy/systemd/` (VPS, system-scope,
> `/opt/buibui`) and `deploy/systemd/user/` (laptop, user-scope — the deployment
> actually live today); see [`docs/MIGRATION.md`](docs/MIGRATION.md) for re-establishing
> a timer on a new machine (`loginctl enable-linger` is required, or user timers stop at
> logout).

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

**`[strategy_timeframes]`** restricts a strategy to a subset of timeframes. Strategies not
listed run on all TFs. The optional **`[strategy_timeframes_long]`** / **`[strategy_timeframes_short]`**
sub-blocks narrow per direction (Bucket C — Q-BC-2 additive narrowing): the directional list, when set,
intersects with the base list to determine the allowed (tf, direction) cells.

```toml
[strategy_timeframes]
inside_bar = ["15m", "1h", "4h", "1d"]

[strategy_timeframes_long]
inside_bar = ["15m", "1h", "1d"]   # 4h long excluded; 4h short still fires

[strategy_timeframes_short]
hammer_hanging_man = ["15m", "1d"] # 1h/4h short excluded; long fires on all base TFs
```

Both the live signal daemon and `make buibui-backtest` (sweep mode) consume the same blocks — backtest
parity was wired in PR #403. The base list hard-skips the (symbol, tf, strategy) cell entirely; the
directional sub-blocks mask signal rows post-detection.

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
mode = "soft"                   # "soft" = log only; "hard" = drop opposing signals
default_tf = "4h"               # default anchor TF; per_strategy entries can override
default_period = 50
default_slope_lookback = 10
deadband_pct = 0.003            # |slope| < 0.3% over slope_lookback bars → allow
# Directions F8 may suppress when a signal opposes the HTF slope. Precedence:
# per-strategy override → this global → built-in ("long","short")=symmetric.
# [] = full exempt; omitting the key = symmetric (back-compat). 2026-06-01
# ablation found counter-trend shorts win, so the global gates longs only;
# flow family (cvd/smt) is exempt, fib family (fib_golden_zone/ote_entry) stays
# symmetric. Shipped soft for observation; hard flip is OOS-gated
# (`tools/htf_ema_gate_replay.py --oos-frac 0.3`).
suppress_directions = ["long"]

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

# Pass a signal-watch TOML so the UI auto-populates defaults from it.
# Omitted, it auto-picks today's config by UTC weekday exactly like
# `buibui signal watch` — so the UI reports the config the daemon is running,
# instead of serving /api/active-config empty and reading as "no config".
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
| `GET` | `/api/live-outcomes` | Cross-symbol roll-up of fired-alert outcomes from `signal_alert_outcomes` (win/loss/avg-R per strategy×tf×direction, both the per-strategy and per-cell groupings also carrying win/loss/expired counts; optional `symbol` query param scopes the roll-up to one symbol) |
| `GET` | `/api/live-outcomes/open` | Unresolved alerts marked to the live price (`?symbol=`); degrades to `marks_ok=false` with null price columns when the price feed is unavailable |
| `GET` | `/api/zones` | Structural zones for a symbol+timeframe (FVG, OB, EQH/EQL, BOS, Fib, OTE, swings) |

**CORS:** Defaults to `http://localhost:5173` (Vite dev server). Override with `CORS_ORIGINS` env var (comma-separated). If you change `DEV_PORT`, update `CORS_ORIGINS` accordingly (e.g. `CORS_ORIGINS=http://localhost:3000`).

**Notes:**

- The web server serves requests from a **read-only** connection (`web/api/deps.py`),
  which returns 503 rather than crashing when the DB is busy. It does still take a
  **write** lock in two places — app startup and the stats cache refresh — so "the web
  server is read-only" is not a safe assumption when diagnosing a lock conflict. Writers
  (`analytics_runner`, the signal daemon) contend for a single DuckDB write lock;
  `analytics.db_retry.connect_with_retry` waits one out instead of failing the job.
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
make docs-index     # Regenerate the audit + spec indexes
```

`docs/audits/INDEX.md` and `docs/superpowers/specs/INDEX.md` are **generated** —
edit the docs, then run `make docs-index`. `tests/test_docs_index.py` fails when
a new audit or spec lands unindexed, so the indexes cannot drift silently.

It also fails when **a new audit states no verdict a machine can read**. Put the
verdict in prose under a `## Headline verdict:` heading — a table, blockquote or
`**Date:**` line under that heading is rejected on purpose, because each renders
as a plausible-but-wrong verdict. This is not style: an unreadable verdict is
invisible to the daily check that asks whether anyone has picked the finding up.

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
make universe-backfill                      # Deep 25-perp universe (1h/4h/1d/1w since 2019)
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

**TOML opt-OUT, and currently DISABLED.** This was documented as an "opt-in" that you
add to `config/signal_watch.toml`, which was wrong in a way that mattered: the loader
defaults it to **`True`** (`analytics/signal_config.py:251`, `:680`), so the live daemon
persisted a `backtest_runs` row every 15-minute cycle whether or not any config named it.

It is now explicitly **`save_results = false`** in the `[backtest]` section of the shared
base `config/strategy_params.toml`, as containment for a writer collision: the live gate
and the sweep compute the *same* `run_id` for the chosen cell (`_backtest_run_id` hashes
only params, no writer identity) and `upsert_backtest_run` is `INSERT OR REPLACE`, so the
daemon was silently overwriting swept rows. Read
`docs/plans/scratch/backtest-runs-writer-collision-2026-08-12.md` before re-enabling.

**Sweeps are unaffected** — `make buibui-backtest SAVE=1` takes the flag from the `--save`
CLI argument (`cli/backtest.py`), not from this TOML key, and still persists normally.

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

Four workflows live in `.github/workflows/` — three run on push and pull request, one on a
cron. Every job carries a `timeout-minutes`, and `concurrency` cancels superseded **PR** runs
but never `main` runs: cancelling on `main` would destroy the record of whether `main` is green.

### `lint.yaml` — CI (always active)

Runs on every push to `main` and every PR. Uses path filters so only relevant jobs run:

| Job | Triggers on | Steps |
| --- | --- | --- |
| `markdownlint` | `*.md` changes | markdownlint-cli2 across all Markdown files; also validates `SKILL.md` frontmatter when `.claude/skills/**` changes |
| `lint-typecheck-test` | `*.py` / `pyproject.toml` / `poetry.lock` changes | ruff check, ruff format, mypy, pytest (no coverage — see below), uploads test XML as an artifact |
| `regression` | `analytics/**` / config TOML / fixtures / goldens / `pyproject.toml` / `poetry.lock` changes | runs `make test-regression` against committed golden files; fails with a diff report if metrics drift |
| `frontend-check` | `web/ui/**` changes | npm ci, vite build, `svelte-check` |

Both Python jobs cache `~/.cache/pypoetry` — which holds the downloaded wheels **and** the
virtualenv — on a key shared with `signal-watch.yaml`.

CI runs pytest **without** `pytest-cov`. Nothing in the repo consumed the coverage report —
there is no codecov/coveralls step and no `fail_under` gate, so `coverage.xml` was uploaded
on every run and never downloaded. Measured over the full suite the tracer cost 50s
(−19.4%), and this job is ~85% of the Python pipeline, so it was the most expensive unread
file in CI. Coverage is still available locally on demand via `make test-cov`. Both `make
test` and CI pass `--durations`, which keeps the slowest tests visible.

The `regression` filter is deliberately narrower than "every Python file": `tests/test_regression.py`
imports from `analytics.*` only, so a `tools/` or `web/` change cannot move a golden. But
`pyproject.toml` and `poetry.lock` stay in scope, because a pandas or numpy bump **does** move
goldens — which is the drift the suite exists to catch.

### `docker-build.yaml` — Docker build check

Builds the Docker image when the `Dockerfile`, `docker-compose.yml`, or the dependency manifests
change, catching build breakage early. The filter sits on the **trigger**, not on the steps, so an
unaffected PR never starts the workflow. An earlier step-level filter still spun up a runner to
check out the repo and evaluate the filter before skipping — "docs-only PRs skip it" read as "costs
nothing" while it was quietly costing a runner on every docs PR.

### `security-scan.yaml` — Trivy filesystem scan

Two Trivy passes over the tree on push and PR to `main`. They are split because the scanners answer
different questions and so earn different exit codes:

| Pass | Exit code | Why |
| --- | --- | --- |
| `secret` | `'1'` — **fails the build** | A committed key is a property of *this diff*, always the author's to fix, and fixable in the same PR |
| `vuln` (CRITICAL/HIGH, `ignore-unfixed`) | `'0'` — reports only | A CVE appears because the outside world changed, not because the repo did; gating would redden whichever unrelated PR happened to be open |

The advisory pass carries `if: always()`, so its report still appears when the secret gate has
failed. Before the split, one `scan-type: fs` step covered both scanners under a single
`exit-code: '0'`, which forced the whole scan to be decorative to avoid the CVE failure mode.

### `signal-watch.yaml` — hourly signal daemon (OKX)

Hourly cron running one scan cycle against OKX market data; see the signal-watch section above
for the ephemeral-DB mechanics. Its `concurrency` is deliberately set to **not** cancel in
progress — cancelling a live scan drops alerts.

---

## Linting and Type Checking

This project uses:

- **ruff** for Python linting and formatting
- **mypy** for static type checking
- **markdownlint-cli2** for Markdown linting
- **pre-commit** for automated checks on every commit

To check formatting and types locally:

```bash
make lint-py          # ruff format + lint
make typecheck        # mypy strict
make test             # full suite (excludes the golden regression tests)
make test-regression  # goldens — passes the --timeout=300 they need
```

Run the goldens via `make test-regression`, never a bare `pytest tests/`:
`pyproject.toml` sets a global 30s timeout and those three backtests take
~97s, so a bare run reports timeouts that look exactly like real golden
drift.

---

## Coming Soon / Ideas

- Auto-close on global SL or high-risk warning
- Telegram command handler (`/price`, `/position`)
