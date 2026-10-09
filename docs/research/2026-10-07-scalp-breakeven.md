# #912: break-even win rate for a 15m scalp, by stop width, RR and fill mode

Follow-on: #981 (exit manager v1, maker exits).

Measured 2026-10-07. R and percentages only. Scripts and raw output are local-only (gitignored) in `docs/plans/scratch/wayfinder-864/research/`
(`scalp_breakeven.py`, `scalp_proxy.py`, `scalp_render.py`, `scalp_breakeven.json`).

## Verdict

**A 15m scalp does not need maker fills to have a plausible break-even at RR 1.5, as long as the stop is at least 0.5%.**
At RR 1.5 the all-taker bar is 51.2% at a 0.5% stop and 43.7% at 1.5%, so under 55% everywhere the production stop floor allows.
Maker fills move it by about 4 points (51.2% to 47.2% with a maker entry, 43.2% all-maker at 0.5%).
The bar only crosses 55% in the region the production floor forbids (0.25% stop, all-taker: 62.4%).
At RR 1 the answer flips: all-taker needs 64.0% at 0.5%, and only all-maker gets under 55%.

Two things make that less comfortable than it reads.
First, the bar is a necessary condition, not a plausibility argument for an edge.
A zero-drift price path wins 1/(RR+1) = 40% of RR 1.5 trades before costs, so at 0.5% all-taker the strategy needs +11.2 win-rate points of real edge over a coin flip, versus +3.2 points all-maker.
Second, the floor is what sets the stop, not the ATR.
A 1.0x ATR stop is 0.24% / 0.32% / 0.38% on BTC / ETH / SOL (median), so the 0.5% floor overrides it 73% to 93% of the time and the real stop is about 1.3x to 2.1x ATR.
The 0.75% RR 1.5 target is then 2 to 3 ATR away on a 15m bar series.

## 1. Cost parameters

| Parameter | Value | Source |
| --- | --- | --- |
| Taker fee per leg | 0.0005 (5 bps) | `config/strategy_params.toml:35` (`fee_pct`); `portfolio/sizing.py:137` agrees |
| Slippage per leg | 2.0 bps = 0.0002 | `config/strategy_params.toml:157` (`[backtest] slippage_bps`, header at :146); `portfolio/sizing.py:138` agrees |
| Stop floor | 0.5% | `config/strategy_params.toml:14` (`min_sl_pct = 0.005`); applied at `analytics/backtest/engine.py:999-1004` |
| Drag formula | `2 x (fee + slip) x entry / risk` | `portfolio/sizing.py:141-168` (`round_trip_drag_r`; the return is line 168) |
| Maker fee, Binance USD-M VIP0 regular user | 0.02% (2 bps) | Binance support FAQ, "Regular User's maker fee is 0.02% and taker fee is 0.05%": <https://www.binance.com/en/support/faq/detail/360033544231> |

The all-taker production drag is 2 x (0.0005 + 0.0002) = 14 bps round trip, so 0.28R at a 0.5% stop, matching the brief.

Fee verification caveat: Binance's own live fee table (<https://www.binance.com/en/fee/futureFee>) returned "No records found" to an unauthenticated fetch, so the maker figure rests on the first-party FAQ page, which also states the 0.05% taker rate that matches `fee_pct`. The BNB-pay discount is not applied.
Fee tiers can change, so re-read the table logged in before pre-registering on 2 bps.

**Per-leg assumptions** (mine, stated because the repo has no maker model).
The drag in R is the sum of the entry-leg and exit-leg costs divided by stop width.
Taker legs pay fee + 2 bps slippage; maker legs pay the 2 bps fee and zero slippage.

| Mode | Entry leg | Exit leg | Round-trip cost | Drag at 0.5% stop |
| --- | --- | --- | --- | --- |
| taker | 5 + 2 = 7 bps | 7 bps | 14 bps | 0.280R |
| maker-entry / taker-exit | 2 bps | 7 bps | 9 bps | 0.180R |
| maker | 2 bps | 2 bps | 4 bps | 0.080R |

The table is computed by calling `portfolio.sizing.round_trip_drag_r` with the per-leg average fee and slippage, not by restating the formula.
Break-even: p = (1 + drag) / (RR + 1), from `p*RR - (1-p) - drag = 0`.

What this does not model, all of which favour the taker column being the honest one for a *scalp*:

- A resting post-only entry fills only when price trades through it. Fills are adverse-selected (the ones that fill are disproportionately the ones that keep going against you) and some signals never fill. Maker rows are a cost floor, not an expected outcome.
- A stop-loss exit is a stop-market order, so it is a taker leg in practice. "All-maker" therefore assumes the stop is also a resting limit, which a protective stop cannot be. A realistic maker-scalp is a maker entry, a maker TP on wins and a taker stop on losses, which lands between the middle and the first column and depends on the win rate.
- Funding is excluded, as in `round_trip_drag_r` (`portfolio/sizing.py` docstring); on a 15m hold it is small.
- The production backtest charges 5 bps + 2 bps on every leg (`fee_pct` is a single value), so a backtest cannot show a maker benefit without a code change.

## 2. Break-even win rate table

Stop width is entry-to-stop as a percentage of entry. The last four rows are the measured widths from section 3.

| Stop % | Mode | Drag R | RR 1 | RR 1.5 | RR 2 | RR 3 |
| --- | --- | --- | --- | --- | --- | --- |
| 0.25 | taker | 0.560 | 78.0% | 62.4% | 52.0% | 39.0% |
| 0.25 | maker-entry / taker-exit | 0.360 | 68.0% | 54.4% | 45.3% | 34.0% |
| 0.25 | maker | 0.160 | 58.0% | 46.4% | 38.7% | 29.0% |
| 0.50 | taker | 0.280 | 64.0% | 51.2% | 42.7% | 32.0% |
| 0.50 | maker-entry / taker-exit | 0.180 | 59.0% | 47.2% | 39.3% | 29.5% |
| 0.50 | maker | 0.080 | 54.0% | 43.2% | 36.0% | 27.0% |
| 0.75 | taker | 0.187 | 59.3% | 47.5% | 39.6% | 29.7% |
| 0.75 | maker-entry / taker-exit | 0.120 | 56.0% | 44.8% | 37.3% | 28.0% |
| 0.75 | maker | 0.053 | 52.7% | 42.1% | 35.1% | 26.3% |
| 1.00 | taker | 0.140 | 57.0% | 45.6% | 38.0% | 28.5% |
| 1.00 | maker-entry / taker-exit | 0.090 | 54.5% | 43.6% | 36.3% | 27.2% |
| 1.00 | maker | 0.040 | 52.0% | 41.6% | 34.7% | 26.0% |
| 1.50 | taker | 0.093 | 54.7% | 43.7% | 36.4% | 27.3% |
| 1.50 | maker-entry / taker-exit | 0.060 | 53.0% | 42.4% | 35.3% | 26.5% |
| 1.50 | maker | 0.027 | 51.3% | 41.1% | 34.2% | 25.7% |
| 2.00 | taker | 0.070 | 53.5% | 42.8% | 35.7% | 26.7% |
| 2.00 | maker-entry / taker-exit | 0.045 | 52.2% | 41.8% | 34.8% | 26.1% |
| 2.00 | maker | 0.020 | 51.0% | 40.8% | 34.0% | 25.5% |
| 0.316 (BTC/ETH/SOL median of 15m ATR, 1.0x, ETH) | taker | 0.444 | 72.2% | 57.7% | 48.1% | 36.1% |
| 0.316 | maker-entry / taker-exit | 0.285 | 64.3% | 51.4% | 42.8% | 32.1% |
| 0.316 | maker | 0.127 | 56.3% | 45.1% | 37.6% | 28.2% |
| 0.473 (same, 1.5x) | taker | 0.296 | 64.8% | 51.8% | 43.2% | 32.4% |
| 0.473 | maker-entry / taker-exit | 0.190 | 59.5% | 47.6% | 39.7% | 29.8% |
| 0.473 | maker | 0.085 | 54.2% | 43.4% | 36.2% | 27.1% |
| 0.492 (25-symbol median of per-symbol p50, 1.0x) | taker | 0.285 | 64.2% | 51.4% | 42.8% | 32.1% |
| 0.492 | maker-entry / taker-exit | 0.183 | 59.2% | 47.3% | 39.4% | 29.6% |
| 0.492 | maker | 0.081 | 54.1% | 43.3% | 36.0% | 27.0% |
| 0.738 (same, 1.5x) | taker | 0.190 | 59.5% | 47.6% | 39.7% | 29.7% |
| 0.738 | maker-entry / taker-exit | 0.122 | 56.1% | 44.9% | 37.4% | 28.1% |
| 0.738 | maker | 0.054 | 52.7% | 42.2% | 35.1% | 26.4% |

Rows 0.316 and 0.473 are below the 0.5% production floor, so on those the live stop is 0.5% and the 0.50 rows are what applies.
The 0.492 row is the unfloored width; floored it is also the 0.50 row.
The 0.492/0.738 rows use a proxy ATR for 22 of 25 symbols (section 3), so treat them as approximate.

## 3. 15m ATR% percentiles, last 180 days

Window: 2026-04-10 to 2026-10-07 (the 180 days ending at the newest 15m bar, from the `ohlcv` view).
ATR is `analytics/backtest/engine.py:51-75` (`_compute_atr14`): the simple mean of the last 14 true ranges, not Wilder smoothing.
Percentiles are over 15m bars, ATR as % of that bar's close.
Majors in `config/coins.json`: BTCUSDT, ETHUSDT, SOLUSDT (all three are also in the 25-symbol universe, `config/universe.toml`).

**Only those three symbols have any 15m rows in `analytics.db`.**
The other 22 universe symbols hold 1h and above only (`ohlcv` coverage query: 1h present for all 25, 15m for BTC, ETH, SOL).
So the 22 rows below are a proxy: 1h ATR% (same 14-bar simple mean, same 180 days) x 0.452.
0.452 is the mean 15m/1h median-ATR ratio measured on the three symbols that have both (BTC 0.446, ETH 0.450, SOL 0.460, a spread of 0.014, so tight and close to the 0.5 a pure square-root-of-time scaling predicts).
The ratio is calibrated on large caps only, so small-cap proxies are weaker evidence than the direct rows (a higher-kurtosis tape could scale differently).
TONUSDT has forward-filled flat bars from late June 2026 (zero range, the contract is gone), so its window is the 180 days ending at its last bar with a real range.
Getting real 15m data for the 22 means `buibui analytics backfill --universe` on 15m, which was not run (read-only brief).

"1.0x / 1.5x stop" is the median ATR% times 1.0 or 1.5. "Floor binds" is the share of bars where that multiple is below the 0.5% floor, so production would widen the stop (direct rows only).

| Symbol | Basis | p25 | p50 | p75 | 1.0x stop (p50) | 1.5x stop (p50) | Floor binds 1.0x | Floor binds 1.5x |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| BTCUSDT | direct 15m | 0.169 | 0.236 | 0.329 | 0.236 | 0.354 | 93% | 76% |
| ETHUSDT | direct 15m | 0.232 | 0.316 | 0.438 | 0.316 | 0.473 | 83% | 55% |
| SOLUSDT | direct 15m | 0.280 | 0.378 | 0.517 | 0.378 | 0.567 | 73% | 39% |
| ZECUSDT | 1h proxy | 0.579 | 0.741 | 0.944 | 0.741 | 1.112 | | |
| HYPEUSDT | 1h proxy | 0.407 | 0.514 | 0.715 | 0.514 | 0.772 | | |
| XRPUSDT | 1h proxy | 0.246 | 0.334 | 0.455 | 0.334 | 0.501 | | |
| DOGEUSDT | 1h proxy | 0.297 | 0.386 | 0.496 | 0.386 | 0.579 | | |
| NEARUSDT | 1h proxy | 0.475 | 0.676 | 1.022 | 0.676 | 1.014 | | |
| BNBUSDT | 1h proxy | 0.184 | 0.241 | 0.306 | 0.241 | 0.361 | | |
| WLDUSDT | 1h proxy | 0.600 | 0.777 | 1.077 | 0.777 | 1.165 | | |
| SUIUSDT | 1h proxy | 0.360 | 0.507 | 0.688 | 0.507 | 0.760 | | |
| 1000PEPEUSDT | 1h proxy | 0.409 | 0.524 | 0.657 | 0.524 | 0.786 | | |
| ADAUSDT | 1h proxy | 0.369 | 0.492 | 0.642 | 0.492 | 0.738 | | |
| TONUSDT | 1h proxy | 0.361 | 0.475 | 0.678 | 0.475 | 0.713 | | |
| ONDOUSDT | 1h proxy | 0.468 | 0.625 | 0.874 | 0.625 | 0.937 | | |
| TAOUSDT | 1h proxy | 0.441 | 0.567 | 0.746 | 0.567 | 0.850 | | |
| LINKUSDT | 1h proxy | 0.320 | 0.399 | 0.508 | 0.399 | 0.599 | | |
| AVAXUSDT | 1h proxy | 0.334 | 0.425 | 0.543 | 0.425 | 0.638 | | |
| BCHUSDT | 1h proxy | 0.272 | 0.399 | 0.588 | 0.399 | 0.598 | | |
| FILUSDT | 1h proxy | 0.421 | 0.556 | 0.755 | 0.556 | 0.834 | | |
| INJUSDT | 1h proxy | 0.498 | 0.661 | 0.911 | 0.661 | 0.992 | | |
| ENAUSDT | 1h proxy | 0.558 | 0.728 | 0.983 | 0.728 | 1.092 | | |
| XLMUSDT | 1h proxy | 0.342 | 0.450 | 0.655 | 0.450 | 0.676 | | |
| VVVUSDT | 1h proxy | 0.708 | 0.958 | 1.229 | 0.958 | 1.438 | | |
| PAXGUSDT | 1h proxy | 0.076 | 0.144 | 0.180 | 0.144 | 0.216 | | |

Across the 25: median p50 is 0.49%, range 0.14% (PAXG) to 0.96% (VVV).
13 of 25 have a 1.0x ATR stop below the 0.5% floor, 4 of 25 a 1.5x stop below it, and 5 of 25 reach 1.0% or more at 1.5x.
The drag at the symbol's own natural 1.0x stop therefore ranges from 0.15R/0.09R/0.04R (VVV, stop 0.96%, taker / maker-entry / maker) to the floor-capped 0.28R/0.18R/0.08R for the 13 symbols under 0.5%.

## 4. Reading it

- The cost bar is set by the floor on the majors, and by ATR on the volatile alts. For BTC, ETH and SOL the floor makes the effective stop 0.5%, so the relevant rows are the 0.50 rows: taker 64.0 / 51.2 / 42.7 / 32.0% for RR 1 / 1.5 / 2 / 3.
- Raising RR is a much bigger lever than going maker. At 0.5% all-taker, moving from RR 1 to RR 1.5 cuts the bar by 12.8 points; going from all-taker to all-maker at RR 1.5 cuts it by 8.0, and to maker-entry only by 4.0.
- The costs that rise as RR rises are in the unmodelled column: a 0.5% stop with RR 3 puts the target at 1.5%, about 4 to 6 ATR of a 15m bar series, so the break-even win rate of 32% is a statement about a far target, not an easy one.
- This does not test whether any 15m detector wins at those rates. The repo's standing result is a 15m book that is unprofitable on a median-R basis in every regime (`AGENTS.md`, multi-regime verdict, median -0.0474R), and the flat-2% stop family sits at the other end of this table (drag 0.07R). Any 15m scalp candidate inherits the full three-leg gate under its own pre-registration; this note only sets the net-of-cost bar it must clear.
