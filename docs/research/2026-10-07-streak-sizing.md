# What risk per trade survives the losing-streak rule over a book's expected trade count?

Ticket: <https://github.com/s10023/buibui-moon-trader-bot/issues/914> (map <https://github.com/s10023/buibui-moon-trader-bot/issues/864>) · Date 2026-10-07 · research agent. R, percentages and counts only; no account figure appears in this file.

Follow-on: #980 (sizing rule, landed in #987).

## Answer

Size each independent bet at f = 1/k, where k is the smallest losing streak with P(run ≥ k in N trades) ≤ α: on the grid p = 0.35–0.60, N = 100–2,000, α = 1–10% that is 3.8% to 14.3% (at α = 5%, N = 1,000: 4.8% at p = 0.35 up to 9.1% at p = 0.60), and every figure on file reproduces to the precision quoted (10.09% / 43.32% / 68.17% at p = 0.45, k = 10).
When each resolved alert counts as a trade, the bot's ledger clusters far beyond that model: 9,225 alerts (fills 2026-03-25 to 2026-10-07) have a longest loss run of 162 against an independent median of 18 (p ≤ 5e-5 over 20,000 shuffles), 4.5 times the independent number of runs of 10 or more, and a first-order fit that doubles k at α = 5% (20 to 41, so f falls from 5.0% to 2.4% at N = 1,000).
The excess is simultaneity inside one market move, not persistence (78% of alerts close on a fill timestamp shared with another, and one observation per UTC day shows no detectable excess: longest losing-day run 11 against a shuffled median of 10, p = 0.34, 191 days, low power), so the rule is f = 1/k from the α = 5% table at the expected N and a conservative p, applied per cluster or per day rather than per alert, with any trade skipped when f × capital is below minimum notional × stop width.
The manual journal cannot test clustering (37 closed trades on 14 entry days, 3 losses, longest run 1).
The table is not a safety floor in general: ignoring wins is conservative about ruin from the starting capital only for a book with solidly positive expectancy at RR ≥ 2, it is optimistic about a k-R drawdown from the equity peak, and the ledger's own expectancy is −0.16R per alert, where constant-risk ruin is certain at any f.
At the 2026-10-07 exchange filters the capital that lets f = 1% clear the minimum lot at a 1% stop is 84 USDT on BTC, 20 on ETH and 5 on SOL, so BTC is where the skip rule bites first (f = 0.5–1% at a 2% stop needs 168–337 USDT; ETH and SOL clear f = 1% at a 2% stop from 40 and 10 USDT).

## Reproduction of the filed figures

The filed figures are the map comments of 2026-10-01 [1]. Each is the exact value rounded to the precision quoted, so there is no discrepancy (0.283% was filed as 0.3%).

| p (win) | k | N | filed | exact | Monte Carlo, 200,000 books | Poisson 1 − exp(−N·p·q^k) |
| --- | --- | --- | --- | --- | --- | --- |
| 0.45 | 10 | 100 | about 10% | 10.087% | 10.03% | 10.77% |
| 0.45 | 10 | 500 | 43% | 43.324% | 43.22% | 43.44% |
| 0.45 | 10 | 1,000 | 68% | 68.167% | 67.98% | 68.01% |
| 0.55 | 10 | 100 | 1.7% | 1.708% | 1.70% | 1.86% |
| 0.55 | 10 | 500 | 8.8% | 8.816% | 8.76% | 8.94% |
| 0.55 | 10 | 1,000 | 17% | 16.982% | 16.94% | 17.08% |
| 0.45 | 15 | 1,000 | 5.5% | 5.511% | 5.52% | 5.58% |
| 0.45 | 20 | 1,000 | 0.3% | 0.283% | 0.30% | 0.29% |

The exact column came out identical to 1e-10 from four routes: the Markov chain in floats, the same chain in exact rationals, the classical recurrence a(m) = a(m−1) − p·q^k·a(m−k−1) for "no run of k", and a transition-matrix power. A brute-force enumeration of all 2^14 sequences agrees with the chain for the run, ruin and drawdown variants used below. The filed cross-check (expected fresh streaks n·p·q^k = 1.1398, so 1 − e^−1.1398 = 68.01%) is right at large N (exact 68.17%) and overstates at small N (10.77% against 10.09% at N = 100) because it ignores the finite window.

## f table (alpha x N x p -> k and f)

Rule: constant currency risk R per trade on a fixed capital basis C, ruin after k = C/R = 1/f straight losses, any win resets the count (the strict rule; wins' profit ignored). Cell = k / f where k is the smallest streak with P(run ≥ k in N) ≤ α and f = 1/k. N is the number of trades over which survival is required; P(run ≥ k) tends to 1 as N grows, so k has to be re-derived for each horizon.

alpha = 1%

| N \ p | 0.35 | 0.40 | 0.45 | 0.50 | 0.55 | 0.60 |
| --- | --- | --- | --- | --- | --- | --- |
| 100 | 19 / 5.3% | 16 / 6.3% | 14 / 7.1% | 13 / 7.7% | 11 / 9.1% | 10 / 10.0% |
| 250 | 21 / 4.8% | 18 / 5.6% | 16 / 6.3% | 14 / 7.1% | 12 / 8.3% | 11 / 9.1% |
| 500 | 23 / 4.3% | 20 / 5.0% | 17 / 5.9% | 15 / 6.7% | 13 / 7.7% | 12 / 8.3% |
| 1000 | 25 / 4.0% | 21 / 4.8% | 18 / 5.6% | 16 / 6.3% | 14 / 7.1% | 12 / 8.3% |
| 2000 | 26 / 3.8% | 23 / 4.3% | 20 / 5.0% | 17 / 5.9% | 15 / 6.7% | 13 / 7.7% |

alpha = 5%

| N \ p | 0.35 | 0.40 | 0.45 | 0.50 | 0.55 | 0.60 |
| --- | --- | --- | --- | --- | --- | --- |
| 100 | 15 / 6.7% | 13 / 7.7% | 12 / 8.3% | 10 / 10.0% | 9 / 11.1% | 8 / 12.5% |
| 250 | 18 / 5.6% | 15 / 6.7% | 13 / 7.7% | 12 / 8.3% | 10 / 10.0% | 9 / 11.1% |
| 500 | 19 / 5.3% | 17 / 5.9% | 14 / 7.1% | 13 / 7.7% | 11 / 9.1% | 10 / 10.0% |
| 1000 | 21 / 4.8% | 18 / 5.6% | 16 / 6.3% | 14 / 7.1% | 12 / 8.3% | 11 / 9.1% |
| 2000 | 23 / 4.3% | 19 / 5.3% | 17 / 5.9% | 15 / 6.7% | 13 / 7.7% | 11 / 9.1% |

alpha = 10%

| N \ p | 0.35 | 0.40 | 0.45 | 0.50 | 0.55 | 0.60 |
| --- | --- | --- | --- | --- | --- | --- |
| 100 | 14 / 7.1% | 12 / 8.3% | 11 / 9.1% | 9 / 11.1% | 8 / 12.5% | 7 / 14.3% |
| 250 | 16 / 6.3% | 14 / 7.1% | 12 / 8.3% | 11 / 9.1% | 9 / 11.1% | 8 / 12.5% |
| 500 | 18 / 5.6% | 15 / 6.7% | 13 / 7.7% | 12 / 8.3% | 10 / 10.0% | 9 / 11.1% |
| 1000 | 19 / 5.3% | 17 / 5.9% | 14 / 7.1% | 13 / 7.7% | 11 / 9.1% | 10 / 10.0% |
| 2000 | 21 / 4.8% | 18 / 5.6% | 16 / 6.3% | 14 / 7.1% | 12 / 8.3% | 11 / 9.1% |

Reading it:

- N enters logarithmically. Doubling N adds zero to two to k (f falls by up to 10%); a ten times tighter α (10% to 1%) adds two to six. p matters more: each 0.05 of win rate moves f by 0.7–1.2 points at α = 5%, N = 1,000.
- Rule of thumb: k ≈ ceil( ln(N·p / −ln(1−α)) / ln(1/q) ). It equals the exact k in 82 of the 90 cells, exceeds it by one in the other 8 and is never below it, so 1/k_hat never overshoots f.
- Reverse view at fixed f, N = 1,000: f = 10% (k = 10) ruins the strict rule 99.3 / 91.5 / 68.2 / 38.5 / 17.0 / 6.1% of the time at p = 0.35 … 0.60; f = 5% (k = 20) gives 6.05 / 1.43 / 0.28 / 0.05 / 0.006 / 0.001%; f ≤ 2% (k ≥ 50) stays at or below 3.0e-7 everywhere on the grid. On this grid the independent-trade rule only binds above about 3.8%.

### Wins at RR > 1: the table is not a floor on safety in general

The strict rule credits a win only as a reset of the count. A win at RR > 1 adds RR·R of cushion above the capital basis, which lengthens the runway, but a win smaller than the drawdown behind it only partly recovers that drawdown, where the strict model resets the count to zero. Side check, N = 1,000, loss −1R, win +RR, gross and zero cost, exact chain. Each cell is "k-R drawdown from the running equity peak / ruin from the starting capital (cumulative R reaches −k)".

| p | k | strict run model | RR 1 | RR 2 | RR 3 |
| --- | --- | --- | --- | --- | --- |
| 0.35 | 20 | 6.05% | 100% / 100% | 97.2% / 33.6% (E = +0.05R) | 40.1% / 0.67% (E = +0.40R) |
| 0.45 | 15 | 5.51% | 100% / 99.9% | 43.9% / 0.63% (E = +0.35R) | 13.7% / 0.066% (E = +0.80R) |
| 0.45 | 20 | 0.28% | 100% / 99.8% | 9.65% / 0.12% | 1.26% / 0.006% |
| 0.55 | 10 | 16.98% | 98.5% / 13.4% (E = +0.10R) | 37.1% / 0.19% (E = +0.65R) | 22.1% / 0.064% (E = +1.20R) |
| 0.55 | 20 | 0.006% | 28.8% / 1.80% | 0.084% / 0.000% | 0.016% / 0.000% |

- Drawdown from the peak is never below the strict figure, because the strict model is the RR → infinity limit in which one win erases the whole drawdown. It is 2.2 to 34 times higher at RR 2 and 1.3 to 6.6 times higher at RR 3 in these rows. For "never lose k R from the peak" the table is optimistic.
- Ruin from the starting capital is far below the strict figure for a solidly positive book (E ≥ +0.35R at RR ≥ 2: 0.63% against 5.51% at k = 15) and above it when expectancy is near zero or negative (E = +0.05R at RR 2: 33.6% against 6.05% at k = 20; p = 0.45 at RR 1: about 100%). The "wins extend the runway, so this is a floor" reading needs a real edge first.
- The bot ledger's mean net outcome is −0.162R per resolved alert (below), so a constant-risk walk on it ruins at any f; the survival rule has content only for a candidate that already clears the gate.

## Empirical clustering (bot book; manual book)

### Bot book

Source and window. `analytics.db`, table `signal_alert_outcomes` [2], opened read-only on 2026-10-07 at 07:08 UTC. 9,303 rows; 78 unresolved (NULL outcome) dropped; 9,225 resolved rows (outcome win, loss or expired with `outcome_r`, `candle_ts_ms` and `outcome_filled_at_ms` set, the same predicate as `portfolio/replay.py:110-117` [4]). Fill times run 2026-03-25 12:00 to 2026-10-07 06:45 UTC (197 calendar days, 191 with a fill), 3 symbols (ETHUSDT 3,255, BTCUSDT 3,221, SOLUSDT 2,749), 19 strategies, timeframes 15m 6,397 / 1h 2,037 / 4h 634 / 1d 157.

What a "loss" is. By outcome: loss (stop hit first) 4,216, mean −1.063R; expired (time-stop at the mark) 4,597, mean +0.416R, 65% positive; win (target hit) 412, mean +3.051R [3]. By the sign of `outcome_r`: 3,386 wins, 5,838 losses, 1 exact zero (counted as not a loss), win rate 36.7%. Only 412 of the 3,386 sign-wins are target hits; 1,622 of the 5,838 sign-losses are time-stops averaging −0.318R, so a sign-loss run costs less than one R per member. The stop-out-only variant (V4) is the one-R-per-loss unit. Mean net R per resolved alert: −0.162R restated, −0.142R as stored (AGENTS.md carries −0.1665R at an earlier n = 4,907).

Basis change. Rows resolved before `_COST_PARITY_MS` = 1,781,149,644,000 ms (2026-06-11T03:47:24Z, split on `outcome_filled_at_ms`) are gross, rows after are net [4]. 2,492 rows are pre-parity and 6,733 post-parity. Restating the pre-parity half with the repo's `restate_on_resolution_clock` moves 29 rows from win to loss (3,386 to 3,357 sign-wins) and changes nothing below (V2); the post-parity-only subset (V3) gives the same picture.

Method. Order by (`outcome_filled_at_ms`, `candle_ts_ms`, `signal_id`). A loss is `outcome_r < 0`. Permutation test: 20,000 shuffles of the whole sequence (loss rate fixed, order destroyed), one-sided p = (1 + shuffles at least as extreme) / 20,001, so 5.0e-05 is the floor. The independent comparator is the exact chain at the same loss rate; its quantiles match the shuffled ones (longest run 18 / 24 / 27 at the median / 95th / 99th in both), which validates both. A "clustering factor" below is the observed number of loss runs of length ≥ k over the independent expectation.

All units, same test. Every unit uses the 2026-03-25 to 2026-10-07 fill window except V3, which starts at the 2026-06-11 cost-parity boundary. "k at 5%" is the independent k against the k from a first-order chain fitted to the same sequence, at a horizon of 1,000 units (250 for days):

| unit | n | win rate | longest run | shuffled longest: median / 95th / 99th | permutation p | runs ≥ 10: observed / independent (factor) | k at 5%: independent to fitted |
| --- | --- | --- | --- | --- | --- | --- | --- |
| V1 resolved alert, sign as stored (ticket definition) | 9,225 | 0.367 | 162 | 18 / 24 / 27 | 5e-05 | 157 / 34.7 (4.5×) | 20 to 41 |
| V2 same, pre-parity rows restated to net | 9,225 | 0.364 | 162 | 18 / 24 / 27 | 5e-05 | 158 / 36.2 (4.4×) | 20 to 41 |
| V3 post-parity rows only (net as stored) | 6,733 | 0.368 | 72 | 17 / 23 / 27 | 5e-05 | 115 / 25.0 (4.6×) | 20 to 38 |
| V4 stop-outs only (outcome = loss; win rate = share not stopped out) | 9,225 | 0.543 | 67 | 11 / 14 / 16 | 5e-05 | 116 / 2.0 (58.7×) | 12 to 37 |
| V5 15m only | 6,397 | 0.395 | 96 | 16 / 21 / 24 | 5e-05 | 109 / 16.6 (6.6×) | 18 to 42 |
| V9 one row per symbol, timeframe, candle, direction | 7,791 | 0.368 | 113 | 18 / 23 / 27 | 5e-05 | 139 / 29.2 (4.8×) | 20 to 35 |
| V7 one event per fill timestamp (sign of summed R) | 3,922 | 0.482 | 35 | 12 / 15 / 18 | 5e-05 | 28 / 2.6 (10.8×) | 14 to 21 |
| V8 one observation per UTC fill day (sign of summed R) | 191 | 0.366 | 11 | 10 / 15 / 18 | 0.34 | at k = 5: 7 / 7.0 (1.0×) | 17 to 18 |

V1 in detail (the ticket's definition). Observed loss runs: 1,037, against 2,144 expected (z = −50); P(loss | previous loss) = 0.822 against P(loss | previous win) = 0.306, where independence gives 0.633 for both. The longest run, 162, exceeds the longest run in every one of 20,000 shuffles (their maximum is 37–39), so its percentile is above 99.995; the exact independent P(run ≥ 162 in 9,225) is 2.2e-29. Its cumulative R is −155.9R (148 stop-outs and 14 time-stops).

| k | observed runs ≥ k | independent expected runs ≥ k | factor | independent P(longest ≥ k) |
| --- | --- | --- | --- | --- |
| 5 | 342 | 343.6 | 1.0 | 1.000 |
| 10 | 157 | 34.8 | 4.5 | 1.000 |
| 15 | 89 | 3.55 | 25 | 0.972 |
| 20 | 55 | 0.37 | 149 | 0.302 |
| 25 | 34 | 0.036 | about 950 | 0.036 |
| 30 | 26 | 0.0037 | about 7,000 | 0.0037 |
| 40 | 12 | 0.00004 | about 300,000 | 0.00004 |

Run-length distribution, observed against the independent expectation (the ledger has too few short runs and far too many long ones):

| length | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 | 13 | 14 | 15 | 16+ |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| observed | 330 | 178 | 111 | 76 | 55 | 40 | 34 | 28 | 28 | 15 | 15 | 9 | 18 | 11 | 5 | 84 |
| independent | 787 | 498 | 315 | 200 | 126 | 80 | 51 | 32 | 20 | 12.8 | 8.1 | 5.1 | 3.2 | 2.1 | 1.3 | 2.2 |

The same reading as exceedance over a horizon. Share of rolling windows of N consecutive resolved alerts that contain a loss run of at least k, empirical / independent at the same loss rate (windows overlap, so the number of independent windows is only n/N: 36.9, 18.4, 9.2 and 4.6):

| N | k = 10 | k = 15 | k = 20 | k = 30 | k = 40 |
| --- | --- | --- | --- | --- | --- |
| 250 | 100% / 61.6% | 95.2% / 8.8% | 84.4% / 0.9% | 47.9% / 0.0% | 25.5% / 0.0% |
| 500 | 100% / 85.7% | 100% / 17.2% | 99.4% / 1.9% | 70.8% / 0.0% | 46.9% / 0.0% |
| 1000 | 100% / 98.0% | 100% / 31.7% | 100% / 3.8% | 88.2% / 0.0% | 67.7% / 0.0% |
| 2000 | 100% / 100% | 100% / 53.6% | 100% / 7.4% | 100% / 0.1% | 92.7% / 0.0% |

At the fill-timestamp unit (V7) the same comparison reads 68.4% against 4.2% for k = 12 at N = 250 events, and 32.6% against 0.6% for k = 15.

Where the runs are. The eight longest runs are separate stretches on seven distinct date ranges, not one event: 162 (2026-03-29 22:45 to 03-31 01:00 UTC, 26 h, 29 fill timestamps, −155.9R), 72 (2026-08-05, 5 h), 70 (2026-09-30, 1 h over 5 timestamps), 68 (2026-04-13, 7 h), 54 (2026-08-26, 3 h), 50 (2026-08-19, within 15 minutes across 2 timestamps), 48 (2026-08-20, 12 h), 47 (2026-07-09 to 07-10, 22 h). Deleting the single longest run leaves a longest of 72 against an independent 95th percentile of 24. The monthly sign-loss rate drifts between 56% (June) and 67% (August).

What drives it. 78% of the 9,225 closures (7,194) share their fill timestamp with at least one other (3,922 distinct timestamps, up to 50 at once; median 36 closures per fill day). 29.6% of rows (2,732, in 1,298 groups) share symbol, timeframe, signal candle and direction with another row: all 1,298 groups share one entry price, 1,136 share one stop, and 1,268 agree on win or loss. Collapsing to one event per fill timestamp (V7) cuts the longest run from 162 to 35, though runs of 10 or more are still 10.8 times the independent count (28 against 2.6); collapsing to a day (V8) removes the excess (factor 1.0, p = 0.34, but 191 days can only detect large persistence). So the filed statement that "losses cluster by regime" is right for the closure sequence and its mechanism is mostly the same market move closing many alerts together; a day-to-day persistence of losing days is not detectable here.

Ordering sensitivity. Closures at one timestamp have no order. The baseline gives 162; 300 random tie orders gave 154–166 (median 157) and a second set of 300 reached at most 170; losses-first within a timestamp gives 157, wins-first 181; the exact maximum over every tie ordering is 184 (a closed-form scan over the timestamp groups, consistent with every ordering tried).

Translating into the f table (α = 5%, horizon 1,000 units, 250 for days):

| unit | independent k and f | fitted first-order k and f | note |
| --- | --- | --- | --- |
| V1 resolved alert (p = 0.37) | 20, 5.0% | 41, 2.4% | the fit still understates the tail: it implies about 55 runs of 16 or more (observed 84) and 2e-11 expected runs of 162 or more (observed 1) |
| V9 deduplicated (p = 0.37) | 20, 5.0% | 35, 2.9% | |
| V7 fill event (p = 0.48) | 14, 7.1% | 21, 4.8% | |
| V8 UTC day (p = 0.37, N = 250) | 17, 5.9% | 18, 5.6% | not significant at n = 191 |

Against the repo's own sizing: `SizingConfig` carries `r_base` = 0.25% (k = 400), `r_open_max` = 2% and `r_cluster_max` = 1% for the BTC/ETH/SOL cluster [5]. At k = 400 the worst observed run (162, −155.9R) costs 39% of the capital basis under constant currency risk; surviving it needs f ≤ 1/163 = 0.61% if every closure were sized separately. The concurrency caps are the repo's existing correction for simultaneous alerts being one bet.

### Manual book

Source and window. `docs/plans/journal/*.md` frontmatter [8]: 37 entries (TEMPLATE.md excluded), entries 2026-05-24 to last exit 2026-09-08. Only id, symbol, direction, intent, entry and exit times, outcome, `r_realized` and `planned_rr` were read, after reading the frontmatter of two files (2026-05-24-btc-short, 2026-07-28-eth-long) to learn the fields; free text and any currency field were not read or used.

n is small, so treat everything here as a description, not a test. 37 closed trades on 14 entry days (9 days with a BTC/ETH/SOL basket entered together, 1 day with two baskets, 4 single trades), 33 scalps and 4 swings, 20 short and 17 long. Ordered by exit time (one entry has none; its entry time was used), ties by file name.

| quantity | value |
| --- | --- |
| wins / losses by sign of `r_realized` | 34 / 3 (win rate 91.9%; the outcome label agrees on all 37) |
| losses | 2026-06-09 (−0.80R), 2026-06-18 (−0.12R, ETH inside a three-symbol basket), 2026-07-26 (−1.00R) |
| mean R per trade, mean win, mean loss | +0.488R, +0.587R (median +0.356R; 13 of 34 wins under +0.30R), −0.640R |
| cumulative R, max drawdown from peak | +18.0R, 1.00R |
| longest losing run | 1 (exact maximum over tie orders of simultaneous exits: 2) |
| independent model at q = 3/37 = 8.1% | P(run ≥ 2 in 37) = 19.8%; P(run ≥ 3) = 1.7% |
| permutation, 20,000 shuffles | P(longest ≥ 2) = 15.9%; observed longest 1 is at the 84th percentile; 3 loss runs against 2.84 shuffled mean (factor 1.06) |
| episode level (same entry date) | 14 episodes, 2 losing, longest losing run 1 |

Verdict for the manual book: no clustering is visible and none could be. With 3 losses the test can only separate "no two adjacent" from "some adjacent"; basket trades are not independent draws (14 episodes, not 37); and it is unverified whether the journal holds every closed trade, so a 91.9% win rate from a journal that may be selected is not a p to size from. About 96 independent closed trades are needed to pin a win rate to ±10 points at 95%, which at this episode rate (14 in 107 days) is about two years away. Until then, size the manual book from the table at a conservative p (0.35–0.45), not from the journal.

## Minimum-lot capital thresholds (prices of 2026-10-07)

Public Binance USD-M endpoints, no key [7]. Filters from `exchangeInfo` (body stamped 2026-10-07T03:11:36Z; two fetches 9 minutes apart returned identical filters); prices from the public ticker at 2026-10-07 07:15 UTC. MARKET_LOT_SIZE carries the same minQty as LOT_SIZE on all three.

| symbol | LOT_SIZE minQty / stepSize | MIN_NOTIONAL | price (USDT) | minQty × price | minimum order notional = max(minQty × price, MIN_NOTIONAL) | smallest orderable size, step-rounded |
| --- | --- | --- | --- | --- | --- | --- |
| BTCUSDT | 0.001 / 0.001 | 50 | 84,227.2 | 84.23 | 84.23 (minQty binds) | 0.001 BTC = 84.23 |
| ETHUSDT | 0.001 / 0.001 | 20 | 2,617.3 | 2.62 | 20.00 (MIN_NOTIONAL binds) | 0.008 ETH = 20.94 |
| SOLUSDT | 0.01 / 0.01 | 5 | 118.95 | 1.19 | 5.00 (MIN_NOTIONAL binds) | 0.05 SOL = 5.95 |

Minimum risk per trade at stop width s = minimum notional × s; the capital at which a per-trade risk f clears the minimum lot = minimum risk / f. Cells are capital in USDT.

| symbol | stop width s | min risk (USDT) | f = 0.5% | f = 1% | f = 2% | f = 5% |
| --- | --- | --- | --- | --- | --- | --- |
| BTCUSDT | 0.5% | 0.421 | 84 | 42 | 21 | 8.4 |
| BTCUSDT | 1.0% | 0.842 | 168 | 84 | 42 | 17 |
| BTCUSDT | 2.0% | 1.685 | 337 | 168 | 84 | 34 |
| ETHUSDT | 0.5% | 0.100 | 20 | 10 | 5.0 | 2.0 |
| ETHUSDT | 1.0% | 0.200 | 40 | 20 | 10 | 4.0 |
| ETHUSDT | 2.0% | 0.400 | 80 | 40 | 20 | 8.0 |
| SOLUSDT | 0.5% | 0.025 | 5.0 | 2.5 | 1.3 | 0.5 |
| SOLUSDT | 1.0% | 0.050 | 10 | 5.0 | 2.5 | 1.0 |
| SOLUSDT | 2.0% | 0.100 | 20 | 10 | 5.0 | 2.0 |

- The step-rounded order is larger than the formula for ETH and SOL because MIN_NOTIONAL / price is not a step multiple: multiply the ETH rows by 1.047 and the SOL rows by 1.19 for the smallest order that is actually placeable (BTC unchanged).
- BTC thresholds scale with price (minQty binds) and ETH/SOL thresholds do not until price passes 20,000 and 500 USDT respectively. The figures exclude fees and funding.
- Position notional = capital × f / s, so f / s above 1 needs leverage: f = 5% at a 0.5% stop is 10×; f = 2% at a 2% stop is 1×.
- How the repo handles it: `round_down_to_step` floors the quantity to the LOT_SIZE step (`portfolio/sizing.py:213-234`); the card vetoes when the size floors to zero ("risk budget is below one lot") and restates risk from the rounded size (`card/card.py:411-425`); order placement vetoes a quantity below minQty ("sub-lot after rounding") or a notional below MIN_NOTIONAL (`card/orders.py:322-335`, tests `tests/test_card_orders.py:264-306`); the XS router skips `min_qty` and `min_notional` (`trade/routing.py:129-147`). Every path vetoes or skips; none sizes up to the minimum [5], [6].

## Method

Markov chain for the exact streak probability (state = current losing-run length; a win resets it, a loss extends it, reaching k is absorbing):

```python
import numpy as np

def p_run_dp(n: int, k: int, q: float) -> float:
    """P(at least one run of >= k consecutive losses in n independent trades), loss prob q."""
    p = 1.0 - q
    v = np.zeros(k)
    v[0] = 1.0                      # v[j] = P(current losing run is j), j = 0..k-1
    absorbed = 0.0
    for _ in range(n):
        absorbed += q * v[k - 1]    # a loss from run length k-1 reaches k and is absorbed
        new = np.empty(k)
        new[0] = p * v.sum()        # a win resets every state to 0
        new[1:] = q * v[:-1]        # a loss extends the run by one
        v = new
    return absorbed

def smallest_k(n: int, q: float, alpha: float) -> int:
    k = 1
    while p_run_dp(n, k, q) > alpha:   # P(run >= k) is non-increasing in k
        k += 1
    return k                           # f = 1 / k
```

- f table: `smallest_k(N, 1 - p, alpha)` for each cell; each boundary was asserted (k meets the tolerance, k − 1 does not).
- Side check: a cumulative-R walk (loss −1, win +RR) with absorption at −k for ruin from the start, and a drawdown-from-peak chain (loss +1, win −RR floored at 0, absorbed at k); both verified against brute force over all 2^14 sequences.
- Permutation test and clustering factors: shuffle the 0/1 loss vector 20,000 times (numpy `default_rng`, seeds 5, 7 and 11), recompute run lengths from the boundaries of maximal runs, compare counts of runs ≥ k, the longest run and the transition probabilities; p-values are one-sided with the +1 correction.
- First-order fit: P(loss | previous loss) = a and P(loss | previous win) = b from the sequence, then the same absorbing chain with the loss probability a from any state of run length ≥ 1 and b from run length 0; checked to equal the independent chain when a = b = q.
- Rolling-window exceedance: for each maximal run of length ≥ k, the windows overlapping it by at least k are counted by an interval difference array (checked against brute force on a random sequence).
- Ledger: one read-only pass (`signal_alert_outcomes`, predicate in Sources [2]); the pre-parity restatement uses `portfolio.replay.restate_on_resolution_clock` unchanged. Journal: YAML frontmatter parsed with PyYAML, whitelisted fields only.
- Minimum lot: minimum order notional = max(minQty × price, MIN_NOTIONAL); step-rounded quantity = ceil(max(minQty, MIN_NOTIONAL / price) / step) × step.
- Scripts and raw outputs (scratch, not tracked): `C:\Users\User\AppData\Local\Temp\claude\C--Users-User-repo-buibui-moon-trader-bot\fc362fbb-3190-4103-b160-deb7a151c1f5\scratchpad\research\streak-work\` (`streaklib.py`, `repro.py`, `ftable.py`, `selftest.py`, `clusterlib.py`, `ledger_extract.py`, `ledger_summary.py`, `ledger_cluster.py`, `ledger_cluster2.py`, `ledger_windows.py`, `ledger_topruns.py`, `journal_extract.py`, `journal_cluster.py`, `minlot.py`, `make_tables.py`).

## Caveats

- The table assumes independent trades at one constant win rate. The ledger violates both (shared fill timestamps, duplicate alerts on one candle, a monthly loss rate that moves between 56% and 67%), and p itself is an estimate: use a lower bound, since each 0.05 of win rate costs 0.7–1.2 points of f.
- The strict rule is exact for "k consecutive losses" and says nothing about drawdown made of losses separated by small wins; see the RR side check for the direction of the error in each framing.
- A sign-loss in the ledger includes small time-stop exits (mean −0.318R), so a sign-based run of k costs less than k R; V4 (stop-outs only) is the one-R unit and clusters harder (longest 67, factor 58.7 at k = 10).
- The ledger is an alert stream, not the bot's traded book: no concurrency cap, no minimum-lot skip and no position limit is applied, so its closure-level clustering probably overstates what a capped book would see, and the day-level result is the closer analogue.
- Costs are modelled, not realised, and a gap through a stop books as a clean touch (AGENTS.md); every R figure is an optimistic bound.
- The permutation p-values are floored at 1/20,001; the exceedance windows overlap and carry only 4.6 to 36.9 independent windows.
- The day-level test has low power at 191 days (the fitted first-order k is 18 against 17 independent): "no detectable excess" is not "none".
- Journal: n = 37 on 14 episodes, `r_realized` is hand-recorded (some values carry "~" or a confirm note), whether it is gross or net of fees is unverified, and whether every closed trade is journaled is unverified. Two near-zero wins (+0.015R, +0.07R) could flip sign net of fees.
- Fixed-fractional sizing (the repo's layer A uses `r_eff × equity`) leaves (1 − f)^k of the capital after k losses rather than zero; the zero-capital ruin in the operator's example holds only for constant currency risk. Under constant currency risk each top-up raises k = C/R, under fixed-fractional risk it does not.
- Exchange filters change and the BTC row moves with price; refetch `exchangeInfo` before relying on a threshold.

## Sources

1. Ticket #914 and map #864, comments of 2026-10-01T07:02:30Z and 07:09:17Z (the question, the filed streak figures and their method note).
2. `analytics.db` table `signal_alert_outcomes`, read-only, 2026-10-07 07:08 UTC. Query: `SELECT signal_id, symbol, tf, strategy, direction, fired_at_ms, candle_ts_ms, entry_price, sl_price, outcome, outcome_r, outcome_filled_at_ms FROM signal_alert_outcomes WHERE outcome IN ('win','loss','expired') AND outcome_r IS NOT NULL AND candle_ts_ms IS NOT NULL AND outcome_filled_at_ms IS NOT NULL` (9,225 rows).
3. `analytics/signal/outcome_backfill.py:15-23` (win = target first, loss = stop first at −1R minus costs, expired = mark-to-market at max hold).
4. `portfolio/replay.py:26-54` (gross to net basis change at e5d92bb, `_COST_PARITY_MS`), `:86-107` (`restate_on_resolution_clock`), `:110-117` (resolved-row SQL).
5. `portfolio/sizing.py:36-50` (`SizingConfig`: `r_base`, `r_open_max`, `r_cluster_max`) and `:213-234` (`round_down_to_step`).
6. `card/card.py:411-425`, `card/orders.py:322-335`, `trade/routing.py:129-147`, `tests/test_card_orders.py:264-306`.
7. Binance USD-M Futures public REST: `GET https://fapi.binance.com/fapi/v1/exchangeInfo` and `GET https://fapi.binance.com/fapi/v1/ticker/price?symbol=…`, fetched 2026-10-07 (BTCUSDT 84,227.2, ETHUSDT 2,617.3, SOLUSDT 118.95 at 07:15 UTC).
8. `docs/plans/journal/*.md`, YAML frontmatter fields `id`, `symbol`, `direction`, `intent`, `entry_ts_utc`, `exit_ts_utc`, `outcome`, `r_realized`, `planned_rr`.
9. `AGENTS.md`: the `outcome_r` basis-change paragraph (pooled ledger −0.1665R at n = 4,907) and the card sizing paragraph on the sub-lot veto.
10. Feller, An Introduction to Probability Theory and Its Applications, vol. 1 (runs in Bernoulli trials): the recurrence used as an independent numeric cross-check, verified here against the chain and brute force.
