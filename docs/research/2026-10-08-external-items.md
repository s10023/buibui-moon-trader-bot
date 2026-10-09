# External items: Myrisklab, openmarket_xyz, TradingView MCP (issue #911)

Follow-on: none recommended; #794 has since closed.

Date: 2026-10-08. Basis labels: **[D]** read on the product's own page or repo this run; **[L]** read from this
repo's own files; **[S]** secondary (directory/search summary); **UNVERIFIED** = not confirmed from a primary source.
Held/obtainable data is judged against `2026-10-07-data-sources.md` (issue #910).

## Answer

None of the three is a data source or validated method this repo needs. Myrisklab is a journal/backtest/charting
product whose order-flow feeds (liquidations, OI, funding, CVD) are data we already hold or can record free, with no
export, and whose journal methods are already filed as mechanics rows. openmarket_xyz is a charting/order-flow
workspace over the same exchange feeds with no history-export path found; of its novel-looking inputs, cross-venue
funding is computable from free per-venue endpoints and economic series are an already-surveyed axis, but
**prediction-market odds are a genuinely non-price series whose free route (Polymarket's own API) was not probed** —
an unframed lead for the data decision, not a finding. The TradingView MCP is plumbing for the #794 drawn-lines idea,
not a data source; the upstream candidate has no confirmed drawing-read tool, so any action there is a short probe of
a fork, not a build. Recommended: keep #794 at p3; no further ingest of either X account is needed for this question.

## 1. Myrisklab (@Myrisklab), by luckychartape

**What it is.** "Risk Lab" is a web platform for charting, backtesting and trade journaling, tagline "Build an edge
you can prove" [D: https://myrisklab.com]. Builder credit goes to "Lucky Chart Ape" (reviews and a luckychartape.com
contact address) [D]; the site does not link the X account, so the @Myrisklab handle itself is UNVERIFIED (x.com
returns HTTP 402 to WebFetch). The repo already ingested a 33-minute walkthrough, "The Risk Lab. How it works." (2026-07-26),
which confirms the product and its author [L: `docs/plans/video-notes/2026-07-31-luckychartape-the-risk-lab-how-it-works.md`].

**Offers.**

- Chart with live tape, auto-drawn fibs/trendlines/FVGs/volume profile, each level with its own historical record;
  order flow (footprint, delta, CVD, OI, liquidations, funding) "collected by the lab itself" on 1,000+ markets, no
  start date or history depth stated [D].
- Lab: plain-language setup, backtested over the chart's tape; win rate, expectancy in R, equity curve, small-sample
  flag [D].
- Journal: ~20 importers (Hyperliquid wallet sync, Binance, Bybit and others), R-normalised, heat/run (MAE/MFE),
  playbooks, sandbox replaying trades under other exit rules, forward simulation [D].
- Trading Floor: optional anonymous pooled playbook results [D].

**Cost and access.** Free account: journal, importers, heat/run, simulator, weekly report. Membership: $119 per 30
days (USDC/USDT or PayPal) for chart, live tape, alerts, Lab, playbooks, Trading Floor; a free week is advertised
until 2026-10-20 [D]. No API mentioned; CSV export only [D]. No keys needed for the free tier.

**New information or re-slice: RE-SLICE.** Order flow it collects is liquidations, OI, funding, CVD: funding and
taker-buy volume are held; OI is free to backfill from Binance vision `metrics` (BTC from 2020-09, most majors from
2021-12); liquidations are recordable free via `!forceOrder@arr` [L: 2026-10-07-data-sources.md rows 20, 24, 26, 27]. Its
history starts whenever the vendor began recording, with no export, so it is strictly weaker than what we can
record ourselves. The backtester is a method over OHLCV plus that tape; no validation of it (DSR/PBO, trial count)
is published on the page [D]; and since AGENTS.md finds trial count dominates n, a lab that makes trials cheap
would by inference raise trial count rather than power. The Trading Floor's pooled playbook R is self-selected, unaudited and cannot be pulled out.

**Pillar.** Intake (a journaling/analytics idea source) and Validation (journal analytics), never Signal or Data.

**Already ingested.** `[L]` 7 video notes under `docs/plans/video-notes/` (Risk Lab walkthrough 2026-07-31;
Educational-videos playlist tranche 1 2026-08-25, 4 videos; 2 more 2026-09-08), 1 X note (TSLA short, 2026-08-26), and
`x-coverage.md` records 4 of his X posts (2 converting). Captured as mechanics-backlog rows:
filed-plan-before-entry and 1% risk; setup-type bucketing; playbook era versioning; give-back/heat-and-run (this
repo's ST17 already measured the same thing at 28.9% finishing at or below zero); discretion score; flipped-level
retest count; channel construction; stop-out-as-system-win; plus one thesis-inbox row (low-volume traversal then full
retracement) and one pundit-priors entry (n=1). The product walkthrough added nothing beyond those rows. Net-new from
this run: only the price and the fact the order-flow history is vendor-held with no export.

**Sources.** <https://myrisklab.com> · <https://x.com/Myrisklab> (not readable) · local ingest notes above.

## 2. openmarket_xyz (@openmarket_xyz)

**What it is.** OpenMarket, a browser charting and market-analysis platform: stocks, crypto, futures, options, FX,
prediction markets and economic data on one chart canvas [D: https://openmarket.xyz]. The repo's follower map lists it
as "Market intelligence platform", 23,632 followers, source `apify_xtdata` [L: `docs/plans/x-following-map.toml`]; no
X posts were readable this run (HTTP 402), so the account's own claims beyond the site are UNVERIFIED.

**Offers (all [D] from the homepage unless noted).**

- Crypto: 4,000+ perps and spot pairs across 29 venues, trade-by-trade order flow, funding aggregated over 10 venues,
  OI, CVD, liquidations; order-book heatmaps, footprints, liquidation maps.
- Options on 7 venues (crypto and CME) with IV and skew; CME index/rates/energy/metals futures; 5,500+ US stocks/ETFs;
  1,200+ FX pairs; prediction-market odds (Polymarket among logos); 5,600+ economic series from 40 publishers.
- Tooling: `wrun` indicator/alert language compiled to WebAssembly, readable feeds "candles, the tape, the book,
  liquidations, options" [D: https://openmarket.xyz/wrun]; alerts to Telegram, Discord, webhook; AI assistant "Kata";
  agent connections for Claude, ChatGPT, Cursor, Codex and Claude Code via a console.

**Cost and access.** "Free to start. Runs in your browser." No tiers or prices on the page; a directory lists a free
charting tier, a Data API with its own free tier and a paid "Plus" with unverified price [S:
https://www.findmymoat.com/tools/openmarket], so treat tiers as UNVERIFIED. The homepage describes no public REST
or WebSocket API; programmatic access is via wrun and the agent console. `/chart/console/agents` rendered only a
loading screen, so the agent tool list, auth and limits are UNVERIFIED. History depth and funding series are not
stated on the wrun page [D].

**New information or re-slice: RE-SLICE, with three untested edges.** Everything in the crypto block is the same
exchange feeds the repo can obtain free (tape, OI, funding, liquidations, `bookDepth` bands) [L: 2026-10-07-data-sources.md], viewed
through a charting UI; no exportable history was found, so it adds no research data. The three things not already
in `2026-10-07-data-sources.md` are (a) cross-venue funding aggregation, which is computable from free per-venue funding endpoints;
(b) prediction-market odds, a genuinely non-price series, but it is an unframed hypothesis with no sample and
Polymarket's own API is the primary route (not probed this run, UNVERIFIED); (c) economic series, already covered as a
conditioning axis (econ calendar, listed in memory as a surveyed data axis). None is a validated method. The AI-chart
agent connection is a convenience, not information.

**Pillar.** Data (aggregator) and Operations (alerts); nothing for Signal.

**Sources.** <https://openmarket.xyz> · <https://openmarket.xyz/wrun> · <https://openmarket.xyz/chart/console/agents> ·
<https://www.findmymoat.com/tools/openmarket> [S] · <https://x.com/openmarket_xyz> (not readable).

## 3. TradingView MCP (issue #794)

**What it is.** Several unrelated open-source servers share the name. #794 names two [L: issue #794]; this run read
the two main ones and a third-party index:

- `atilaahmettaner/tradingview-mcp` (MIT, Python, ~5k stars): 37 tools for screeners, TA indicators, multi-timeframe
  analysis, sentiment, news and 9 backtest strategies; data from public endpoints and Yahoo Finance, no TradingView
  login or session [D: https://github.com/atilaahmettaner/tradingview-mcp]. The page does not describe any drawing read.
- `tradesdontlie/tradingview-mcp` (MIT code only): drives TradingView Desktop over Chrome DevTools Protocol on
  localhost:9222 (`--remote-debugging-port=9222`), requires a TradingView subscription, lists `draw_list`,
  `draw_remove_one`, `draw_clear`; the README does not say whether `draw_list` returns hand-drawn shapes [D:
  https://github.com/tradesdontlie/tradingview-mcp]. It warns the approach uses undocumented internal APIs and may breach
  TradingView's terms on automated collection, and lists automated trading as a prohibited use [D].

**Offers.** For the #794 goal (drawn lines with descriptions as structured input), only the CDP-based family is
relevant, and whether it reads manual drawings is UNVERIFIED (a gateway listing credits a fork with `draw_list` and
`draw_get_properties` [S: https://policylayer.com/policies/ulianbass-tradingview-mcp]; not read in code). The
atilaahmettaner server offers nothing the repo lacks: its TA indicators and backtests re-run on OHLCV, and
`get_technical_analysis` would add a screener view of the same bars.

**Cost and access.** Self-hosting is free for both. atilaahmettaner also sells a hosted tier at $9/mo (2,500
requests) or $29/mo (10,000) [D]. The CDP route needs a paid TradingView plan, Node 18+ and a local Desktop app; it
carries account-ban risk; #794 notes no MCP server is configured on this account today [L].

**New information or re-slice: NEITHER, it is plumbing.** It supplies no data. The CDP variant could move the
operator's own drawn levels into structured form, which is an input to F2 cards and the reference-level hypothesis
([L] memory: reference-level triggers, "untested and supported"). That would carry new information only as the
operator's own labelled levels, which become testable once logged; the MCP itself adds none. #794's own amendment
already notes the as-of walkthrough half needs no MCP [L: #794]. TV-to-Binance routing would duplicate `trade/` and
bypass the overlay; both READMEs also bar automated trading.

**Pillar.** Intake (levels into structured form) and Operations; Execution is explicitly a non-goal.

**Sources.** <https://github.com/atilaahmettaner/tradingview-mcp> · <https://github.com/tradesdontlie/tradingview-mcp> ·
<https://policylayer.com/policies/ulianbass-tradingview-mcp> [S] · issue #794 (read-only `gh issue view`).

## Sources

- <https://myrisklab.com>
- <https://x.com/Myrisklab> (HTTP 402, not readable)
- <https://x.com/luckychartape> (not fetched; handle documented via local ingest notes)
- <https://openmarket.xyz>
- <https://openmarket.xyz/wrun>
- <https://openmarket.xyz/chart/console/agents> (loading screen only)
- <https://www.findmymoat.com/tools/openmarket> (secondary, affiliate directory)
- <https://x.com/openmarket_xyz> (not readable)
- <https://github.com/atilaahmettaner/tradingview-mcp>
- <https://github.com/tradesdontlie/tradingview-mcp>
- <https://policylayer.com/policies/ulianbass-tradingview-mcp> (secondary)
- Local: `docs/research/2026-10-07-data-sources.md`, issue #794, `docs/plans/video-notes/*luckychartape*`,
  `docs/plans/mechanics-backlog.md`, `docs/plans/x-coverage.md`, `docs/plans/x-following-map.toml`
