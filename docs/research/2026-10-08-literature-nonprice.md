# Non-price predictors of crypto perp returns at 15m-1d: literature (issue #909)

Follow-on: #937 (quarter-hour opening imbalance), #920 (round-1 long list).

Date: 2026-10-08. Research ticket under wayfinder #864. Availability column cites `2026-10-07-data-sources.md` (this folder), not re-researched.

Basis labels: **[P]** peer-reviewed or working paper read at abstract or full-text level this run; **[PR]** practitioner/firm research (not peer-reviewed); **[S]** secondary (search summary or snippet; full text not read, treat as unverified); **[M]** cited from memory, not fetched.

## Answer

No variable has clean, net-of-cost, out-of-sample evidence at 15m-1d horizons; the literature is thin and mostly gross-only. The best-supported lead is taker order-flow imbalance sampled at 15-minute boundaries, which in a July 2026 preprint on six Binance perps predicts 4-12h returns by roughly 5-17 bps per interquartile move (in-sample, gross, no cost analysis at those horizons) [1]. Funding and basis carry real information about crash risk and about leverage demand [10] [11], but the directional-return test found is null for BTC [14], the delta-neutral arbitrage is decaying [10], and the repo's own carry sleeve is shelved. Liquidation-driven moves reverse more often than matched shocks over 2h (61% vs 43%, p about 0.10, gross, 43 sampled days) [16], an idea only worth testing on record-forward data. Dead or crowded: sub-second order-book and flow signals (decay in seconds, taker fee dominates) [2] [7], options IV and skew (predict volatility, not returns) [20], exchange netflows and stablecoin supply (no credible test found; the Tether-issuance tests are null or contested) [21] [22], ETF flows (levels regressions with reverse causality, under 3 years of data) [23], and cross-venue lead-lag (resolved in minutes, below bar frequency) [26]. Funding and basis are trend-chasing proxies [10] [11], and open interest plausibly is too (no source found either way), so all three likely correlate with XS momentum and are weak candidates for the "new information" the project needs. Note for the data decision: the lead's construct is the 10-second quarter-hour opening, which needs the tick tape (aggTrades, free), not the held whole-bar `taker_buy_volume`.

Reading rule: "no credible evidence found" and "No" in the net-of-cost column mean the literature offers no positive, cost-surviving result. Neither is a powered null; no row here is evidence that an effect was ruled out.

## Table

Cost wall: about 14 bps round trip modelled in this repo. For scale, Binance standard-tier perp fees in the Kim-Hansen sample were 5.0 bps taker and 2.0 bps maker per side [1].

| Variable | Effect & horizon | Net of cost? | Sample & venue | Post-publication decay | Data required (availability per 2026-10-07-data-sources.md) | Overlap with filed verdict | Cites |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **Taker order-flow imbalance** | (a) First-10s quarter-hour return predictable OOS: mean R2_OOS 3.37%, hit rate 56.6%. (b) Order imbalance at quarter-hour openings predicts 4-12h returns, peaking 8-12h; IQR effect about 5-6 bps (lagged-flow part) and 9.8 / 16.9 bps at 8h / 12h (public-signal part). Contemporaneous: signed volume explains about 80% of BTC return variation. Daily BTC (Bitstamp) imbalance: R2 1.05% at 1d, 0.45% at 2d, about 0 by 36d | **No** for (a): gross 0.5 bps per trade vs 5 bps taker fee. **Partial/unknown** for (b): gross, in-sample OLS with HAC errors, no cost analysis. Daily: in-sample, no costs | (a)(b) 6 Binance perps (BTC, ETH, XRP, SOL, DOGE, ADA), Jan 2021-Oct 2024. Daily: Bitstamp 2013-2023. Contemporaneous: 34 exchanges, 2017-18 | Not measured. One preprint (Jul 2026); no replication found | Taker-buy volume per bar HELD (15m: BTC/ETH/SOL; 1h/4h/1d: 25). Quarter-hour 10s imbalance needs the tick tape (aggTrades, free from 2019-12 for BTC; no tape held) | **Spot-perp CVD (a)**: daily bars, 10 trials failed; this evidence is intraday, which is the untested re-entry. Likely partly momentum-linked: the 12h "public" component is built from technical indicators | [1] [3] [4] |
| **Order-book depth and imbalance** | Sub-second: linear models explain 10-37% of 500 ms returns. L2 liquidity-state transitions are predictable, but order-flow gain is inconsistent for BTC. VPIN/Roll predict the sign of the change in realized volatility, not return direction | **No** at 15m+. Taker strategies earn little where taker fees are large; maker results only from a live test | Coinbase/Binance/others sub-second (2021 paper); Binance BTC/ETH L2 2023-26; 5 major coins | Not measured | Band depth (+-0.2% to 5%, 30s) free from 2023-01; top-of-book only to 2024-03; full L2 PAID (Tardis). Nothing held | None filed | [2] [5] [8] |
| **Spot-perp basis and premium** | Perp deviations from no-arbitrage are large (BTC mean 0.69 pre-2022, 0.17 after) and driven by past returns (past returns predict the basis, not the reverse). Futures basis carries some, biased, information on future spot changes (2018-19 CME/CBOE) | Arbitrage: Sharpe 3.35 (retail fee tier) and 3.27 after spreads for BTC; 11.65 at zero cost; mean hold about 5h; most return is convergence, not funding. **Directional use: no test found** | Binance hourly, 2020-01 to 2024-03 (arbitrage); CME/CBOE 2018-03/2019 (basis) | **Yes**: deviations fall about 22 pp per year [10] | Needs spot and perp bars at the same frequency. Perp 1h held; spot held at DAILY only (`spot_ohlcv`, `venue_spot_daily` closes); spot 1h/1m klines free on data.binance.vision | **Coinbase premium (H14) NO EDGE**; carry sleeve SHELVED. Basis is near-collinear with funding. Correlates with XS (trend-chasing) | [10] [11] [28] |
| **Funding rates** | Funding forecastable itself (persistent). Carry predicts crypto crashes (horizon not stated on the page); carry averages above 10% p.a., up to 60%. BTC 7-day funding change: R2 12.5% contemporaneous, R2 about 0 for next period | **No** as a directional signal: next-period R2 zero, gross, no costs [14]. Carry-capture economics are the shelved sleeve | BIS: BTC/ETH futures (dates not on page). [14]: BTC, 2021-early 2024; alpha on top-50 Binance perps, 5m data, no costs | Delta-neutral version decaying [10]; BIS paper revised Oct 2025 | Funding HELD for all 25 symbols since listing (BTC from 2019-09) | **Carry sleeve** (+0.03 Sharpe, not cost-robust past 8 bps): the null directional result agrees. Highly correlated with XS momentum via trend-chasing leverage | [10] [11] [12] [13] [14] |
| **Open-interest changes** | **No credible evidence found** for return predictability. Only volatility links (mixed signs by horizon) and descriptive work. Exchange-reported perp OI is systematically misquoted by some exchanges | No test exists to net | n/a | n/a | Free backfill from vision `metrics` (5-min; BTC 2020-09, majors 2021-12). Hourly OI held only from 2026-02 / 2026-05 | None. Likely momentum-correlated (OI rises with trend) | [15] |
| **Liquidations** | Liquidation-originated moves reverse: at 2h, 61.2% reverse vs 43.3% of matched non-liquidation shocks; median continuation difference -19.8 bps, CI [-49.7, -0.0], Mann-Whitney p = 0.099, n = 67 pairs. Warning signs before cascades are event-specific (no invariant) | **No**: gross, "suggestive rather than conclusive" per the authors | Binance + Bybit, 43 sampled days (first of each month), 2023-01 to 2026-07; 7 BTC cascades 2022-25 | Not measured | **Record-forward only** (WS `!forceOrder@arr`, about 2.8 MB/day); no free history. Heatmap snapshots held but approximate | None filed. Reversal is the opposite of momentum, so possibly diversifying to XS | [16] [17] |
| **Options IV and skew** | Variance risk premium is large and regime-dependent (higher in low-vol states). IV slopes do not predict returns but forecast weekly realized volatility. A 25-delta risk-reversal direction test: 58.9% accuracy, p about 0.18 (hobby backtest) | **No** for returns | Deribit 2019-20 (VIX construction); Bitcoin options, years unverified [20] | Not measured | Deribit option trades with IV free from 2018-06; DVOL from 2021-03-24; skew must be rebuilt from trades or snapshotted going forward; BTC/ETH only | None filed. BTC/ETH only, cannot feed a 25-perp cross-section | [18] [19] [20] |
| **Exchange netflows (on-chain)** | **No credible evidence found.** The nearest are descriptive netflow-price studies and an exchange-to-whale netflow paper on market efficiency (variance ratio), not returns | No test exists | n/a | n/a | Coin Metrics community API, daily only: exchange flow in/out and supply (BTC from 2011). Not SOL | None filed | [27] [29b] |
| **Stablecoin supply and issuance** | Tether issuance: reported null for subsequent BTC returns (affects volume); issuance responds endogenously to peg deviations. Contrary view: Tether-backed price support in 2017-18. Lagged stablecoin supply change adds no robust signal (p = 0.758) | No (null or contested) | 2017-18 (Tether); 2023-2026 (practitioner note) | n/a | DeFiLlama daily, free, from 2017-11; aggregate, not per-asset | None filed | [16] [21] [22] |
| **ETF flows (spot BTC/ETH)** | Net flows "predict" price LEVELS (R2 about 95%) and price rises precede abnormal ETF volume; cointegration with ETF AUM. Practitioner Granger: prior-day inflows positive for today's price change (F = 8.48, p = 0.004; units unclear) | **No** for returns: levels regressions on trending series, reverse causality, gross | US spot ETFs from 2024-01-11: under 3 years, one market-wide series | n/a (event too young) | **Not scriptable from this host** (Farside behind Cloudflare). Daily, US hours only; third-party scrapes [S] | None filed. Single time series, no cross-section, so it cannot feed XS | [23] [24] [25] |
| **Cross-venue lead-lag** | Perps/futures on unregulated venues lead spot at the minute level; CME minor; regulated/US spot respond "slowly". Later note: no durable winner, leads rotate and are measured at 1-5 min. Sub-second leaders depend on fee regime. Cross-exchange gaps large and recurrent in 2017-18 | **No** at 15m+: the lead is shorter than a bar. Gap arbitrage faces capital controls | 2017-2020 (BitMEX era); 2023-2026 (Binance/CME/ETF note); sub-second Bitcoin venues | Leadership rotates by period (BitMEX, then Binance) | OKX/Bybit candles and trades free (OKX 1H about 2y, Bybit trade CSV from 2020-03); nothing below 15m held | **Coinbase premium (H14) NO EDGE** is the same family | [2] [4] [16] [26] |

## Notes

**Taker order flow (the only row with a live lead).**

- Kim-Hansen [1] is an arXiv preprint (v2, 2026-07-16), not peer-reviewed, one author funded by a UBRI grant (disclosed).
- The OOS test covers only the first-10s opening return. The 4-12h result is full-sample OLS with Bartlett HAC errors, 95% significant for four of six contracts at every horizon, SOL insignificant at 8h. It is gross and has no fee analysis.
- The effect shape (negative for about 30 min, then positive, peaking at 8-12h) means it is a slow drift, not a 15m signal. Excluding funding-settlement quarter-hours leaves it unchanged.
- The 4h effect is mostly lagged flow; by 12h about 65% is a "public signal" built from 28 technical indicators. That part is momentum-like and probably overlaps XS.
- A whole-bar taker imbalance from the held 15m `taker_buy_volume` is a coarser construct than the 10-second quarter-hour opening; the paper finds fine-frequency and baseline imbalance carry little. Do not assume the held column reproduces it.
- Scale: even at the 16.9 bps (12h) IQR figure the gross move is near the 14 bps cost wall for one round trip, before any capture-rate haircut.
- [9] (Anastasopoulos and Gradojevic, world order flow, +0.2% daily and +0.9% weekly per 1 SD) I could not retrieve; those figures are a search-summary [S] only. Daily-horizon crypto flow studies are in-sample [3].
- The Makarov-Schoar 80% [4] is a contemporaneous variance share, not predictability. Their abstract does not give a forecasting R2.
- Easley et al. [5] predict the sign of the change in realized volatility with ML, not return direction. Useful only as a volatility-timing input.

**Funding and basis.**

- [10] is the strongest paper in the funding/basis family, but it is about the deviation from no-arbitrage price, not directional return. I found no peer-reviewed test of funding or basis as a cross-sectional or time-series return predictor on perps.
- The [10] Sharpe 11.65 is zero-cost and high-turnover (about 5h holds, hourly data); the net figures (3.27-3.35) still assume spot and perp legs, margin and borrow handled. Not reconciled with the repo's +0.03 carry-sleeve Sharpe: the sleeve is a different, slower capture design, so do not treat the two as contradictory without a direct replication.
- [11] shows carry rises with trend-chasing demand, so any carry or basis sort is partly a momentum sort. Test orthogonality to XS before spending trials.
- [14] is firm research with no cost treatment; its alpha is shown only as images I could not read.

**Open interest.** Zero return-predictability papers found despite an extended search. Practitioner "price up, OI up means trend" heuristics are untested. [15] is a data-quality warning for exchange-reported OI; use Binance `metrics` for consistency.

**Liquidations.** [16] is a firm research note on sampled days (liquidation data only for the first day of each month); its own tests disagree at 5% (bootstrap CI touches zero, MW p = 0.099). The persistence metric is defined in the note, not in this file; the 61.2% vs 43.3% reversal rates and the -19.8 bps difference are the cleanest numbers. Treat as a hypothesis for forward collection, not an effect size.

**Options.** [20] is a snippet-level source (journal and authors not verified); the corroborating Deribit risk-reversal backtest is a personal project, not evidence. No paper found regresses forward returns on DVOL or skew with overlap-robust errors and an OOS split.

**Stablecoins.** [22] is cited from memory (Griffin and Shams, Journal of Finance, 2020): supply-based Tether story on 2017-18 data, contested by [21] and the Wei 2018 VAR null reported there. Neither speaks to 15m-1d post-2020 behaviour.

**ETF flows.** The levels R2 of about 95% [23] should be read as a trending-series artifact: both flows and price rise together, and [23] itself reports that price increases lead abnormal ETF volume. The sample is about 2.7 years of daily bars.

## Sources

Local:

- [29] `docs/research/2026-10-07-data-sources.md` (availability column).

Peer-reviewed or working papers [P]:

- [1] Kim, C. and Hansen, P. R. (2026). The Quarter-Hour Effect: Periodic Algorithmic Trading and Return Predictability in Cryptocurrency Futures. arXiv:2607.09426. <https://arxiv.org/abs/2607.09426> (full text read)
- [2] Albers, Cucuringu, Howison, Shestopaloff (2021). Fragmentation, Price Formation, and Cross-Impact in Bitcoin Markets. arXiv:2108.09750. <https://arxiv.org/abs/2108.09750> (abstract and structure read)
- [3] Koutmos, D. and Wei, W. C. (2023). Nowcasting bitcoin's crash risk with order imbalance. Review of Quantitative Finance and Accounting. <https://doi.org/10.1007/s11156-023-01148-1> ; <https://pmc.ncbi.nlm.nih.gov/articles/PMC10040314/>
- [4] Makarov, I. and Schoar, A. (2020). Trading and arbitrage in cryptocurrency markets. Journal of Financial Economics 135(2), 293-319. <https://doi.org/10.1016/j.jfineco.2019.07.001> (abstract level)
- [5] Easley, O'Hara, Yang, Zhang (2024). Microstructure and Market Dynamics in Crypto Markets. SSRN 4814346. <https://ssrn.com/abstract=4814346>
- [6] Silantyev, E. (2019). Order flow analysis of cryptocurrency markets. Digital Finance 1, 191-218. <https://doi.org/10.1007/s42521-019-00007-w> (abstract only; contemporaneous)
- [7] Cont, R., Cucuringu, M., Zhang, C. (2023). Cross-impact of order flow imbalance in equity markets. Quantitative Finance 23(10). <https://doi.org/10.1080/14697688.2023.2236159> (equities benchmark: lagged cross-asset OFI forecasts, decays quickly)
- [8] Jeon, J. (2026). When Does Order Flow Matter? State-Dependent L2 Liquidity-State Transitions in Crypto Futures. arXiv:2607.09230. <https://arxiv.org/abs/2607.09230>
- [10] He, Manela, Ross, von Wachter. Fundamentals of Perpetual Futures. arXiv:2212.06888 (v7, 2026-09). <https://arxiv.org/abs/2212.06888> (first 100k chars of the HTML read)
- [11] Schmeling, Schrimpf, Todorov (2023, rev. 2025). Crypto carry. BIS Working Paper 1087. <https://www.bis.org/publ/work1087.htm>
- [12] Ackerer, Hugonnier, Jermann (2024). Perpetual Futures Pricing. NBER w32936 / arXiv:2310.11771. <https://www.nber.org/papers/w32936> (theory)
- [13] Inan, E. Predictability of Funding Rates. SSRN 5576424. <https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5576424> (forecasts funding, not returns)
- [15] Reconciling Open Interest with Traded Volume in Perpetual Swaps. arXiv:2310.14973. <https://arxiv.org/abs/2310.14973> (authors not captured)
- [17] Early-warning signals across seven crypto-perpetual liquidation cascades. arXiv:2607.27070. <https://arxiv.org/abs/2607.27070> (preprint, authors not captured)
- [18] Almeida, Grith, Miftachov, Wang (2024-25). Risk Premia in the Bitcoin Market. arXiv:2410.15195. <https://arxiv.org/abs/2410.15195>
- [19] Alexander, C. and Imeraj, A. The Bitcoin VIX and its variance risk premium. <https://hdl.handle.net/10779/uos.23306975> (abstract not read; cited via search)
- [23] Mazur, M. and Polyzos, E. (2025). Spot Bitcoin ETFs: The Effect of Fund Flows on Bitcoin Price Formation. Journal of Alternative Investments 27(4), 110-123. <https://doi.org/10.3905/jai.2025.1.239>
- [24] Guliyev and Ahmadova (2025). From Flows to Value: Cointegration Between Bitcoin Spot ETF Assets and Bitcoin Price. Ledger. <https://ledger.pitt.edu/ojs/ledger/article/view/393>
- [26] Alexander, C. and Heck, D. F. (2020). Price discovery in Bitcoin: The impact of unregulated markets. Journal of Financial Stability 50, 100776. <https://ideas.repec.org/a/eee/finsta/v50y2020ics1572308920300759.html>
- [27] Borri, Liu, Tsyvinski, Wu (2025-26). Cryptocurrency as an Investable Asset Class: Coming of Age. arXiv:2510.14435. <https://arxiv.org/abs/2510.14435> (abstract: "blockchain information helps drive prices"; no horizon or effect size read)

Practitioner [PR]:

- [14] Presto Research (J. H. Jung). Can Funding Rate Predict Price Change? <https://www.prestolabs.io/research/can-funding-rate-predict-price-change>
- [16] SharpeEdge Research. Bitcoin Price Discovery: an empirical attribution across spot, offshore perpetuals, CME futures and U.S. ETFs. <https://sharpeedgecapital.com/research/bitcoin-price-discovery-an-empirical/>
- [25] FalconX. What Can Spot ETF Flows Tell Us About the Trajectory of Bitcoin Prices? <https://www.falconx.io/newsroom/what-can-spot-etf-flows-tell-us-about-the-trajectory-of-bitcoin-prices-a-preliminary-statistical-investigation>

Secondary or unverified [S]/[M]:

- [9] Anastasopoulos, A. and Gradojevic, N. Order Flow and Cryptocurrency Returns (EFMA 2025 version; journal page <https://www.sciencedirect.com/science/article/pii/S1386418126000029>). Not retrieved (certificate error, then HTTP 403); figures from a search summary only.
- [20] Implied volatility slopes and jumps in bitcoin options market. <https://www.sciencedirect.com/science/article/abs/pii/S0167637724000713> (HTTP 403; search snippet only)
- [21] Lyons, R. and Viswanath-Natraj, G. VoxEU column, Stable coins don't inflate crypto markets. <https://cepr.org/voxeu/columns/stable-coins-dont-inflate-crypto-markets> (summary of the paper; also reports the Wei 2018 VAR null)
- [22] Griffin, J. and Shams, A. (2020). Is Bitcoin Really Untethered? Journal of Finance. <https://doi.org/10.1111/jofi.12903> (from memory, not fetched)
- [28] Pricing efficiency and arbitrage in the Bitcoin spot and futures markets (CME/CBOE basis, 2018-19). <https://www.sciencedirect.com/science/article/abs/pii/S0275531919309808> (snippet only; authors not verified)
- [29b] Exchange-flow descriptive study: The Block Research, Bitcoin exchange flows, volumes and price (2017-09 to 2021-09). <https://www.theblock.co/research/institutional/to-what-extent-are-bitcoin-exchange-flows-trading-volumes-and-price-related-a-historical-view-118545> (descriptive; results not read). Exchange-to-whale netflow and efficiency: <https://www.sciencedirect.com/science/article/pii/S0890838925000915> (snippet only).
