# TradFi Equity Bot — Fork Design

**Date:** 2026-04-10
**Status:** Draft — **name decided** 2026-05-02 (see §1); pre-fork checklist tracked in `~/.claude-personal/projects/-home-kng-repo-buibui-moon-trader-bot/memory/project_wifey_wall_street_fork.md`
**Author:** brainstorming session

---

## 0. Updates after Phase 2 close (2026-05-02)

This spec predates the Phase 2 architectural split. The §4 / §5 / §6 module references below pre-date the split; the post-split layout is:

| Spec reference | Post-Phase-2 home |
| --- | --- |
| `analytics/indicators_lib.py` | **deleted** in strat-3 (PR #340) — package `analytics/strategies/` (21 detector modules + `_base`, `_shared`, `_seasonality`, `_registry`) |
| `analytics/data_store.py` | thin re-export shim — package `analytics/store/` (8 modules; schema in `analytics/store/schema.py`, OHLCV upsert in `analytics/store/market_data.py`) |
| `analytics/signal_lib.py` | 4-line shim — package `analytics/signal/` (8 modules) |
| `analytics/backtest_lib.py` | thin shim — package `analytics/backtest/` (6 modules) |
| `analytics/stats_lib.py` | thin shim — package `analytics/stats/` (13 modules) |

`funding_reversion` (listed in §5 as "remove at fork") was already removed in Phase 1 H3 (2026-04-27). Fork inherits the absence — no fork-time action needed.

The §4 carry-over verbatim files (`signals/cooldown_store.py`, `signals/alert_formatter.py`, `signals/registry.py`, plus the `web/` and `tests/` trees) are still at their original paths.

The full plan (`docs/superpowers/plans/2026-04-10-tradfi-equity-fork.md`, 1,335 lines) has **not** been patched against Phase 2; review it fresh when execution starts.

---

## 0.1 Updates after 2026-05-14 (Alpaca dropped, data-only-first)

After ~11 days of further work on the parent repo and a fresh re-evaluation, several core assumptions in this spec have changed. **Until §1–§12 below are revised inline, this section overrides them.**

### 0.1.a Data provider: yfinance for Phase A (Alpaca dropped)

Decided 2026-05-14: **yfinance (free)** for Phase A. Alpaca is out. Polygon.io Starter $29/mo is the pre-approved upgrade target if Yahoo breaks the API, history depth becomes a constraint, or symbol count scales beyond ~100.

**Why yfinance won:** the timeframe scope was cut from 15m/1h/4h/1d to **4h/1d/1w only** (2026-05-14). With intraday-below-4h out, yfinance's 1h-=-730-days limit no longer matters — resample 1h→4h client-side yields 2 yr of 4h on the free tier, plenty for the strategy WFO. 1d/1w are unlimited.

**Phase A choices that fall out of yfinance:**

| Axis | Decision |
| --- | --- |
| SDK | `yfinance` PyPI package (unofficial, Yahoo-backed). Wrap in adapter so swap-to-Polygon is one file. |
| Symbol format | Plain ticker (`AAPL`, `MSTR`, `CRCL`) — no suffix |
| Adjustment | `auto_adjust=False` to preserve raw close (S/R needs absolute prices). Split adjustment applied by default; dividend adjustment off. |
| 4h timeframe | Pull 1h, resample client-side anchored to US market open (13:30 UTC / 14:30 UTC EST) |
| VWAP per bar | **Not available from yfinance.** Drop the `vwap` column from the schema entirely (cleaner than NULL placeholder). Re-add only when/if swapping to Polygon. |
| Rate limits | Informal; ~50 symbols × 3 TFs = ~150 req/run is well within tolerance |
| Reliability | Yahoo can break the unofficial API at any time. Phase A accepts this; adapter pattern makes swap cheap. |

See `~/.claude-personal/projects/.../memory/reference_equity_data_providers.md` for the full 9-provider comparison and gotchas.

**Sections invalidated until §6 is rewritten:**

**Sections invalidated until the new provider is chosen:**

| Section | Status |
| --- | --- |
| §2 row "Data source" | **REWRITTEN 2026-05-14** → yfinance |
| §2 row "Real-time delay" | **REWRITTEN 2026-05-14** → ~15-min Yahoo delay accepted |
| §6 (data layer) | **REWRITTEN 2026-05-14** → yfinance client + 4h resample + vwap column dropped |
| §10 row "Order execution" | **REWRITTEN 2026-05-14** → Phase B deferred; broker TBD |
| §11 Q5 "Alpaca account type" | **MARKED OBSOLETE inline 2026-05-14** |
| §12 step 3 "data layer" | **REWRITTEN 2026-05-14** → yfinance data layer |

§4, §5, §7, §8, §9 (carry-overs, strip list, equity adaptations, strategy assessment, run cadence) are **broker-agnostic and survive intact**. The dual-Telegram routing in §7.6 has been further validated by the live `direction_filter` gate shipped in parent PR #367 (2026-05-13) — that gate is the exact mechanism the spec describes; the fork inherits it for free.

### 0.1.b Phased delivery: data-only first, order layer deferred

The spec originally implied a single-shot fork that included paper trading (§10 "Order execution via Alpaca: Paper trading first"). The new phasing is:

1. **Phase A — Signals only.** Data provider + adapted strategies + Telegram alerts (dual channel) + backtest. No order execution, no positions tab, no Alpaca/broker SDK. User executes trades manually based on alerts.
2. **Phase B — Order layer (deferred, TBD).** Broker decision deferred until Phase A is live and the user is comfortable. Could be IBKR, Tradier, Schwab, a CFD broker, or stay manual indefinitely.

**Why:** removing the order layer collapses the broker-decision blocker, lets us ship signals fast, and matches the user's stated preference to "have confidence in the trading system before merging execution work" (memory 2026-05-14).

### 0.1.c Migration workflow — open question, deferred

The §3 "Repo Fork Strategy" claim that "strategy improvements are manually ported between repos" is the weakest part of this design today. Three candidate shapes: (1) a `/migrate-to-wifey` skill, (2) a shared `buibui-core` package both repos import, (3) a `migrations/wifey/` patch queue. **Decision deferred** — pick after the fork exists and the porting pain is concrete. Track as open question §11 Q7 (new).

### 0.1.d Pre-fork checklist status (was in memory file)

| Item | 2026-05-02 status | 2026-05-14 status |
| --- | --- | --- |
| 1. EMA strategy + F8 HTF EMA gate | "next focus" | **F8 shipped** (PR #346, 2026-05-06); EMA WFO closed no-edge (PR #342); F8 EMA infra is the share point |
| 2. Stale `backtest_combos` refreshed | last run 2026-04-13 | **Refreshed 2026-05-11** (PR #356) — `backtest_combos` 4,433 rows fresh |
| 3. `min_avg_r` ordering vs combo confluence | open | **Superseded** — gate-ordering question rolled into broader T6 backtest-live-parity workstream (memory 2026-05-14) |
| 4. `feat/positions-write` audited + merged | open | **Parked** by user 2026-05-14 — want confidence in trading system first |
| 5. Phase 2 micro-cleanups (`cofire.py:141`, stale docstrings) | open | Status TBD — verify before fork-time |

**Net:** items 1, 2 done; 3 superseded; 4 parked; 5 verify-then-go. The pre-fork gate is effectively cleared for Phase A. Forking can start once data provider + migration tooling are decided.

### 0.1.e New open questions

Append to §11:

- ~~**Q7. Data provider**~~ — **RESOLVED 2026-05-14**: yfinance for Phase A; Polygon.io $29 Starter as pre-approved upgrade target. See §0.1.a.
- **Q8. Migration workflow shape** — skill / shared package / patch queue / wait-and-see?
- **Q9. Phase B broker** — IBKR / Tradier / Schwab / CFD / manual-forever?

---

## 1. Overview

Fork `buibui-moon-trader-bot` into a new repo targeting **US equities (single stocks)** via
**yfinance** (Phase A; Polygon.io $29 as pre-approved upgrade). The bot runs on-demand (once per day or once per week), not as a continuous live daemon. It re-uses the entire strategy and backtest engine; only the data layer and equity-specific session logic are replaced. Timeframes: **4h / 1d / 1w** only.

### Name (decided 2026-05-02)

**Buibui Wifey Wall Street Bot.** Repo slug: `buibui-wifey-wall-street-bot`. CLI binary name still open (see §11 Q6: `buibui` → `wifey`, `bwws`, or keep). Placeholder `[BOTNAME]` retained throughout this spec for the repo/package rename mechanics.

---

## 2. What's Decided

| Decision | Choice | Rationale |
| --- | --- | --- |
| Markets | US equities — single stocks (MSTR, AAPL, CRCL, etc.) | User stated |
| Data source | **yfinance** (free; unofficial Yahoo Finance scraper) | No auth, free tier covers the use case (see §0.1.a). Polygon.io $29/mo Starter is the pre-approved upgrade target. |
| Repo strategy | **Fork** of `buibui-moon-trader-bot` | Architecturally too different for a branch |
| Run cadence | Once per day or week (on-demand) | Long-term swing trading, not live daemon |
| Timeframes | **4h / 1d / 1w only** (revised 2026-05-14) | 15m/1h dropped from scope; this is what made yfinance viable |
| Real-time delay | Yahoo data is ~15-min delayed; acceptable | Run is EOD/EOW, not intra-session |
| Order execution | **Phase B only** — deferred entirely; broker TBD (see §0.1.b) | Phase A ships signals + Telegram; user trades manually |

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
| `analytics/strategies/` (was `indicators_lib.py` pre-Phase-2; see §0) | All 20 actionable strategies are pure OHLCV math — no Binance coupling. `funding_reversion` already removed in Phase 1 H3. |
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
| `utils/binance_client.py` | Replaced by `utils/yfinance_client.py` |
| ~~`funding_reversion` strategy~~ | Already removed from parent in Phase 1 H3 (2026-04-27). Fork inherits absence — no fork-time action. |
| Funding rate columns in DB schema | `funding_time`, `funding_rate` — removed from `analytics/store/` |
| OI (open interest) data | Not available for equities at launch. Remove `oi_usd` column and OI fetching. |
| `taker_buy_volume` column | Binance-specific; **dropped entirely** (yfinance has no VWAP per bar). Re-add as `vwap DOUBLE` only on Polygon upgrade. |
| CVD divergence strategy | Requires taker buy/sell split — not available from yfinance (or most retail equity providers). Deferred. |
| `live_price.py` / `live_position.py` | Crypto-specific live WebSocket wrappers. Replace with equity equivalents only when Phase B live work begins. |
| Positions tab (Binance futures) | Deferred to Phase B alongside broker choice. |

---

## 6. Data Layer Replacement

### 6.1 yfinance helper (`utils/yfinance_client.py`)

Replaces `utils/binance_client.py`. SDK: `yfinance` (`pip install yfinance`). **No auth, no client object** — yfinance is an unofficial Yahoo Finance scraper with module-level functions.

```python
import yfinance as yf
import pandas as pd

YF_INTERVALS: dict[str, str] = {"1h": "60m", "1d": "1d", "1wk": "1wk"}

def fetch_history(symbol: str, *, interval: str, period: str = "max") -> pd.DataFrame:
    """Return canonical OHLCV (lowercase columns, UTC-naive DatetimeIndex).

    auto_adjust=False — preserves raw close for S/R level detection.
    actions=False     — strips Dividends + Stock Splits columns.
    """
    ...
```

**Reliability caveat:** Yahoo can break or rate-limit the underlying endpoints at any time. Callers must handle failure. The adapter pattern in `analytics/data_fetcher.py` makes a swap to Polygon ($29/mo) a single-file change if/when needed.

### 6.2 OHLCV fetcher (`analytics/data_fetcher.py`)

yfinance equivalent of `fetch_klines()`:

```python
def fetch_bars(
    symbol: str,           # e.g. "AAPL" — plain ticker, no suffix; no client argument
    interval: str,         # "1h", "4h", "1d", "1wk"
    start_ms: int,         # Unix ms; results filtered to start_ms onward
    limit: int = 5000,
) -> pd.DataFrame:         # canonical OHLCV schema (NO vwap column)
```

**Key adaptation: 4h is synthesised.** yfinance does not serve 4h natively; for `interval="4h"` the fetcher pulls 1h and resamples client-side anchored to 13:30 UTC (US regular-session open):

```python
hourly.resample("4h", origin="start_day", offset="13h30min").agg(
    {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
).dropna()
```

**Column mapping:**

| buibui column | yfinance source | Notes |
| --- | --- | --- |
| `open_time` | DataFrame index (UTC ns → ms) | Yahoo returns America/New_York tz — convert to UTC then drop tz |
| `open` | `Open` | |
| `high` | `High` | |
| `low` | `Low` | |
| `close` | `Close` (raw, `auto_adjust=False`) | Preserves absolute price for S/R |
| `volume` | `Volume` | Shares traded |
| ~~`taker_buy_volume` / `vwap`~~ | **NOT AVAILABLE** | Column **dropped entirely** from schema. Re-add as `vwap DOUBLE` only if upgrading to Polygon. |

### 6.3 Timeframe mapping

| buibui interval string | yfinance interval | Notes |
| --- | --- | --- |
| ~~`"15m"`~~ | — | **OUT of scope** (2026-05-14) |
| ~~`"1h"`~~ | — | **OUT of scope** (2026-05-14) for live signals; still used internally as the source for 4h resample |
| `"4h"` | `"1h"` + client-side resample | Anchored to 13:30 UTC; yfinance has no native 4h |
| `"1d"` | `"1d"` | Primary TF |
| `"1wk"` | `"1wk"` | Weekly setups |

**History caps (yfinance free tier):**

| Interval | Max period |
| --- | --- |
| `1h` (so 4h via resample) | 730 days (~2 yr) |
| `1d` | unlimited |
| `1wk` | unlimited |

### 6.4 Data sync (`analytics/data_sync.py`)

- yfinance returns the full requested `period` in one call → the parent repo's "page until short batch" loop collapses to a single fetch per (symbol × interval). Adjust accordingly.
- **No rate limit officially documented** by Yahoo — informal headroom is plenty for ~50 symbols × 3 TFs per EOD run. If 429s appear, add jitter + retry.
- Split adjustment is applied by yfinance by default; dividend adjustment is off (we pass `auto_adjust=False`).
- **No funding sync** — remove entirely
- **No OI sync** — remove entirely
- **No `client` parameter** — `backfill()` / `incremental_sync()` signatures drop the client argument entirely.

### 6.5 DB schema (`analytics/store/` post-Phase-2; was `data_store.py` pre-split — see §0)

Modified (not copied verbatim):

| Change | Detail |
| --- | --- |
| **Drop `taker_buy_volume` column entirely** | yfinance has no VWAP. No replacement column. Re-add as `vwap DOUBLE` (nullable) only if upgrading to Polygon. |
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
For future live scanning, add an `is_market_open()` guard. yfinance does not expose a market-clock endpoint — use a static US-market-hours calculation (9:30–16:00 ET, Mon–Fri, excluding holidays from a hardcoded list or the `pandas_market_calendars` package).

### 7.2 Session anchor for ORB

`detect_orb_breakout()` currently anchors to `00:00 UTC` (crypto daily open).
For US equities, the ORB anchor is **9:30am ET = 13:30 UTC** (14:30 UTC during EST/winter).

Change: set `session_hour_utc=13` as default for US market open.
Long-term: derive dynamically from `pandas_market_calendars` (XNYS calendar) to handle DST automatically.

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

yfinance applies split adjustments by default and exposes dividend adjustment via `auto_adjust=True`. Phase A passes `auto_adjust=False` — preserves absolute price levels needed for S/R detection. If full dividend adjustment is later wanted (e.g. for long-term performance attribution, not signal detection), swap to `auto_adjust=True` selectively.

### 7.5 Earnings calendar (deferred)

Earnings cause vol spikes and gaps that invalidate pattern setups. Future milestone: suppress signals within ±2 days of earnings. yfinance exposes `Ticker.earnings_dates` as a stop-gap data source; for production-grade data swap to a dedicated earnings API. Not in initial scope.

### 7.6 Dual Telegram channel routing

Two Telegram channels, one signal-generation pass:

| Channel | Audience | Direction filter | Label rewrite |
| --- | --- | --- | --- |
| **Personal** | Self | Long + Short (full output) | None — keeps "LONG" / "SHORT" |
| **Wife (`Buy`)** | Self + wife | Long only (Short suppressed entirely, not just hidden) | "LONG" → "BUY" everywhere user-visible (alert subject, body, summary) |

**Implementation outline:**

- Routing is a **dispatcher** concern, not a detector concern. Detectors emit a single `SignalEvent`; the dispatcher fans out to N publishers each with `(direction_filter, label_rewrite)` config.
- Two BotFather bots (separate tokens). Two env-var groups: `TELEGRAM_PERSONAL_TOKEN` / `_CHAT_ID` and `TELEGRAM_WIFE_TOKEN` / `_CHAT_ID`. Manual setup.
- Cooldown store keyed by `(symbol, tf, direction)` is already the right shape — wife channel dedup is independent of personal channel because `direction="short"` rows never reach it.
- Label rewrite happens in the publisher right before `bot.send_message`; the underlying `SignalEvent` and DB row keep the canonical `"long"` direction. Backtest, recalibrate, and stats still see one truth.
- Future extension: per-channel quality threshold (e.g. wife channel only ≥ 4-star ratings, or only certain strategy types) plugs into the same dispatcher config.

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
| `cvd_divergence` | No taker buy/sell split available from retail equity providers (yfinance, Polygon retail tiers, Tiingo). Deferred. |

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
| Order execution | **Phase B only** — broker TBD (IBKR / Tradier / Schwab / CFD / manual-forever); see §0.1.b |
| Positions tab | Deferred to Phase B alongside broker choice |
| Earnings calendar suppression | Needs separate earnings API (yfinance has limited support); defer |
| Real-time live daemon | yfinance is ~15-min delayed; Polygon $29 Starter also 15-min delayed; live needs Polygon $199 Advanced or different provider |
| Pre/after-hours OHLCV | yfinance includes extended hours by default in 1h bars — filter client-side if needed; not actively used in Phase A |
| Short interest / borrow rate | Equity equivalent of funding — defer |
| Options data | Out of scope (would need Polygon paid or another provider) |
| Multi-market (HK, SG) | Possible future path with a different provider |
| Intraday TFs (15m, 1h native) | Cut from scope 2026-05-14 — Phase A is 4h / 1d / 1w only |

---

## 11. Open Questions

1. ~~**Name**~~ — Resolved 2026-05-02: **Buibui Wifey Wall Street Bot**.
2. **GitHub org/account** — same account as buibui or separate?
3. **Initial watchlist** — MSTR, AAPL, CRCL confirmed; how many others?
4. ~~**Primary timeframe**~~ — Resolved 2026-05-14: **4h / 1d / 1w only**; 15m/1h cut from scope.
5. ~~**Alpaca account type**~~ — **OBSOLETE 2026-05-14**: Alpaca dropped (see §0.1.a). Phase B broker is now a separate open question (see §0.1.e Q9).
6. **CLI binary name** — `buibui` rename to `wifey`, `bwws`, or keep `buibui`?
7. ~~**Data provider**~~ — Resolved 2026-05-14: **yfinance** for Phase A (see §0.1.a); Polygon.io $29 Starter is the pre-approved upgrade target.
8. **Migration workflow shape** — skill / shared package / patch queue / wait-and-see? Deferred (see §0.1.c).
9. **Phase B broker** — IBKR / Tradier / Schwab / CFD / manual-forever?

---

## 12. Implementation Sequence

Once name is chosen and spec approved:

1. **Fork repo** → rename → global find/replace `buibui` → `[BOTNAME]`
2. **Strip crypto layer** — remove Binance client, funding/OI, `cme_gap_lib`, positions tab
3. **yfinance data layer** — new `utils/yfinance_client.py`, rewrite `analytics/data_fetcher.py` (with 1h→4h resample), update `analytics/data_sync.py` (drop client argument; remove funding/OI sync)
4. **Schema updates** — `analytics/store/`: drop `taker_buy_volume` column entirely, drop funding/OI tables
5. **Config** — `stocks.json` watchlist, equity `signal_watch_*.toml` with `strategy_timeframes` restricted to `["4h", "1d", "1wk"]`
6. **ORB session fix** — `session_hour_utc=13` default for US market open
7. **Overnight gap lib** — `overnight_gap_lib.py` replacing `cme_gap_lib.py`
8. **Remove `funding_reversion` + `cvd_divergence`** from all registries
9. **Tests** — update test fixtures; add yfinance fetcher mock (patch `analytics.data_fetcher.fetch_history`)
10. **Backtest + scan** — validate pipeline end-to-end on AAPL 1d
11. **Web dashboard** — verify it boots correctly with new config
