# E850 — pre-registration for the short after a breakout on rising open interest

**Date:** 2026-10-09 · **Status:** pre-registered, unrun · **Issue:** H18 (#850), slot 1 of
round 2 per #922 · **Round:** 2, first candidate (slots 2–3 are #983)

This file fixes the construction, the gate and the kill-switches **before** any E850 return
exists. Choosing them read open-interest and OHLCV coverage only (§2), never a return
conditioned on an event. A change to any of them after the first look is a new trial, not a
correction.

## 1. Hypothesis

A daily close through 20-day resistance, made while open interest in contracts has risen
unusually fast, predicts a downward move ("flush") over the next five days, so a short opened
on it earns positive net R.

The source is the 2026-08-04 thesis-inbox row (@Traderfengge): a resistance break draws in
leveraged longs, the crowded long book builds, and a flush liquidates it. The primitive is the
**quantity** of leverage, which this system has never tested. The shelved carry sleeve tested
its **price** (funding).

The prior is adverse. `xsrev` (short recent winners, 2–7d) read −2.9 Sharpe even at zero
cost, so shorting breakouts loses on average. E850 pays only if rising OI flips a breakout
from continuation into reversal. The control column (§3) measures that base rate.

## 2. Power — priced first

`tools/distil_power.py --units per_book_day --sr-footing per_obs --periods-per-year 365
--n-trials 3 --sd 1.0 --corpus-best 0.07197` (XS's +1.375 annualised, per book-day), run
2026-10-09 on a snapshot of `analytics.db`:

| book-days | bar at variance 0.005 (the round floor) | bar at 0.0005 |
| --- | --- | --- |
| 1,771 | 1.90 annualised | 1.11 |
| 1,200 | 2.06 | 1.27 |
| 900 | 2.20 | 1.41 |
| ~20,000 | 1.375 (first meets XS) | — |

- **1,771 is the ceiling.** It is the number of UTC days on which at least 14 universe symbols
  carry hourly OI (2021-12-02 → 2026-10-08). An event construction holds positions on fewer
  days, so the true bar sits above 1.90.
- **The variance term sets the bar, not n.** At the floor it stays near 1.15 with unlimited
  data, so more history cannot bring E850 under XS.
- **E850 is built anyway (operator, 2026-10-09).** A PASS needs a Sharpe beyond anything this
  system has found, and the realistic outcome is a licensed NO on the OI axis. At n = 1,000–1,771
  the best-case CI half-width is about 0.9–1.2 annualised, under the ~1.9–2.2 bar, so
  `analytics.audit_guard.powered_null` can license "no gate-grade effect".
- The 2026-08-13 parking priced n = 84 book-days at a 3.48 bar. That panel was REST-only. The
  #936 archive replaced it.

## 3. Construction — frozen

| Item | Value |
| --- | --- |
| Panel | `config/universe.toml` minus PAXGUSDT: 24 USDT-M perps. Each symbol starts on its own data. BTC's OI starts 2020-09, most majors 2021-12, the newest symbols 2023–2025 |
| Clock | every timestamp in UTC. The DuckDB connection runs `SET TimeZone='UTC'` before any date cast; on this host it otherwise casts to UTC+8 |
| Bars | Binance 1d OHLCV through the `ohlcv` view. A decision on day D uses only bars closed by 00:00 UTC of D+1. `sync` stores the forming bar, so the newest row is never read as closed |
| OI | `open_interest_archive.oi_contracts`, source `vision_um_metrics`. OI_D is the hourly row stamped 23:00 UTC of day D: the last row strictly before D's close. No interpolation. If OI_D or OI_{D−5} is missing, nothing is decided on D. `oi_usd` is not used, because it rises with price at a breakout even when no positions are added |
| Break | close_D > max(high_{D−20} … high_{D−1}) |
| Rising OI | g_D = ln(OI_D / OI_{D−5}). It qualifies when g_D is at or above the 67th percentile of that symbol's g over the 180 days D−180 … D−1, with at least 90 non-missing values in the window |
| Treatment (E850) | short every break where the OI condition qualifies |
| Control | short every break, whether or not the OI condition qualifies. It is never gated. It serves as the base rate (§6) and as a PBO column (§4) |
| Entry | short at the open of D+1 |
| Exit | cover at the close of D+5. No stop and no target |
| Re-trigger | a symbol that triggers while its position in that column is open is skipped, never stacked. Each column applies the rule separately |
| Size | notional N = 1 / σ20, where σ20 is the sample sd (ddof 1) of the 20 daily close-to-close returns ending at D. 1R is one σ20 move |
| Daily P&L | −N × each held bar's return, in R. Bar D+1 is open-to-close and later bars are close-to-close |
| Costs | `fee_pct` and `[backtest] slippage_bps` read from `config/strategy_params.toml` at run time (today 5 bps and 2 bps per leg), never restated in the driver. A position pays 2(fee + slip) × N per round trip: half on its entry day, half on its exit day |
| Funding | excluded from the gated series. The mean funding received per held position-day (from `funding_rates`) is printed beside the result, signed, with which way the omission cut |
| Gates | none: no regime, bear-score, HTF or day filter |
| Sample | every position whose exit bar closes on or before 2026-10-08. Later data is a forward record and never runs through this gate |

## 4. Observation unit and the gate

- **Book-day series.** One row per UTC day with at least one open treatment position: the mean
  net R across positions open that day. Averaging within a day absorbs the cross-symbol
  correlation once, so per AGENTS.md the rows are **not deflated again**. Rows are daily
  mark-to-market, so the five-day overlap needs no deflator; the bootstrap covers what serial
  correlation remains.
- **Edge** = `analytics.research_guards.passes_gate(dsr, pbo, boot_lo)`.
  - DSR ≥ 0.95 at N = 3, on E850's own series, with `sr_variance` as in K1.
  - boot_lo > 0, from `block_bootstrap_ci` with `block` = 5 (the hold), on E850's own series.
  - PBO ≤ 0.5, from `cscv_pbo` across every round-2 column that exists when E850 runs plus the
    control. Per #921: the union of UTC calendar days, 0R on a day a column does not trade. A
    column joins only if it produces that book-day series. The result names the columns
    present. A column added later never re-scores a filed verdict.

## 5. Kill-switches — checked before the gate is read

The driver prints K1's inputs and K2's counts before it computes any mean of either column.

- **K1 Power.** Measure the treatment's book-day n and sd, then run `tools/distil_power.py
  --units per_book_day --sr-footing per_obs --periods-per-year 365 --n-obs <n> --n-trials 3
  --sd <sd> --sr-variance <max(per-book-day Sharpe variance across round-2 columns and the
  control, 0.005)>`, with **no `--n-eff`**. **Do not run E850** if the required Sharpe exceeds
  **2.5 annualised**. The n_eff that `analytics.forecast.effective_independent_series` gives over
  the 24 symbols' daily returns is printed beside K1 as a disclosure.
- **K2 Fidelity.**
  1. A truncation test in the style of `tests/test_lookahead.py`: the treatment and control
     events at D are identical when bars and OI are cut at D's close.
  2. Event counts per symbol and per year, for treatment and control. Treatment ⊆ control holds
     event by event, before re-trigger skips.
  3. Every hourly |Δ ln `oi_contracts`| > ln 3 in the panel is listed and explained before
     anything runs, as a guard against contract redenomination or a bad archive day. An
     unexplained jump within a symbol's g window removes that symbol's affected decisions,
     named in the result.
- Same-bar ties cannot arise: there is no stop and no target.

## 6. Reading the verdict

Checked in this order once the gate is read:

1. **FAIL.** Read as "no effect found". A filed NO only where `analytics.audit_guard.powered_null`
   licenses it at the DSR bar. A failure to clear the gate is not a statement about power.
2. **Beta guard.** Regress the treatment's book-day returns on the equal-weight 24-symbol
   universe's same-day close-to-close return. A PASS whose intercept is ≤ 0 is filed as
   **beta, not an edge**, and goes no further.
3. **XS overlap.** Compute the Pearson ρ of the treatment's book-day returns against the XS
   sleeve's net book return, on the treatment's open days and on the full UTC axis (0R on idle
   days). |ρ| ≥ 0.5 on either disqualifies a PASS. A strong negative ρ would make E850 `xsrev`
   under another name.
4. **Attribution.** Compute the mean per-event net R of treatment minus control, pooled across
   symbols, with its t-stat deflated by sqrt(k / n_eff) from `effective_independent_series`
   (the raw t printed beside it). A PASS whose deflated |t| < 1.96 is filed as **"breakout
   reversal; OI not shown to add"**, not as an OI edge.
5. **PASS** survives 2–4 and is an OI edge.

**Killed** (K1, K2) is a failure to clear, never a null, and is never quoted as "no edge".

## 7. Tradeable test — only after a PASS

1. **Minimum lot.** At 1% risk per R on the equity held when the test runs, drop every
   position below the exchange minimum lot (skipped, never sized up) and re-run the gate. The
   equity figure stays local.
2. **Survival.** #915's f = min(1/k at 5% over 12 months, half-Kelly net) must be at least the
   1% measurement size.
3. **Execution.** Shorts on universe symbols overlap the XS sleeve's book, and
   `cancel_open_orders` is symbol-wide, so trading both needs separate sub-accounts (AGENTS.md,
   XS execution).

## 8. Disclosures

- **Looks taken before this file.** OI and OHLCV coverage per symbol and per day, the OI schema
  (REST stores only `oi_usd`; the archive also stores `oi_contracts`, complete through
  2026-10-09), and `funding_rates` coverage. No event was counted and no return was read.
- **Thin early breadth.** From 2020-09 to 2021-11, BTC is the only symbol with OI, so those
  book-days are single-asset and fall in the 2021 bull run, which is adverse to a breakout
  short. They were kept because dropping them would be a choice made with their shape already
  known.
- **Costs are optimistic.** Modelled fee and slippage bound the real cost from one side only.
- **Funding.** If mean funding on held days is positive, as is typical after crowded breaks,
  omitting it understates the short and the gate is conservative. If it is negative, the gate
  was optimistic by that amount. §3 says which.
- **Survivorship.** The universe is today's 25-symbol research set, read with hindsight about
  which names lasted.
- **No prior effect.** Nothing has measured E850's effect. The #920 long list priced it at
  calendar-day n with sd 1.0 only.

## 9. What the build owes (the hand-off)

- A tracked driver under `tools/` (never scratch) implementing §3–§6, with tests for: the
  break and OI-percentile triggers on a fixture, the truncation test, the 23:00 OI read and
  missing-row skip, the re-trigger skip per column, treatment ⊆ control, the cost charge, and
  the UTC pin.
- The run on a snapshot of `analytics.db`, with K1 and K2 reported before the gate, and the
  verdict filed as an audit under `docs/audits/` with its verdict as prose.

## 10. Decision log

Each line names the observable that would reverse it.

- **Build despite a bar above XS** (§2): reversed if K1 at the measured n and variance exceeds
  2.5 annualised.
- **Short, not long or a spread:** this is the thesis as filed. Reversed only by a new
  pre-registration. The opposite reading (rising OI as conviction to add, mechanics-backlog
  2026-08-23) is a separate trial.
- **Control = every break, ungated:** it is the base rate that "above the unconditional base
  rate" refers to. Reversed if treatment ⊄ control in K2, which would mean a defect, not a
  design change.
- **1d bars, OI at 23:00 UTC:** this matches #921's UTC book-day axis and leaves one hour of
  causal margin. Reversed if the archive's hourly stamp proves to mark the end of its interval
  rather than the start. Then 22:00 restores the margin, recorded as a correction before any
  run.
- **Donchian 20 on highs:** one causal parameter, no pivot confirmation (the leak class behind
  `bos` and `liquidity_sweep`). Reversed only by a new trial.
- **Contracts, 5-day window, own-history 67th percentile over 180 days (min 90):** USD OI
  rises with price, a sign test would make treatment ≈ control, and own history normalises
  across symbols. Reversed if K2 finds `oi_contracts` broken by redenominations that cannot be
  explained.
- **Five-day fixed hold, no stop:** no same-bar ties, and it meets `xsrev`'s 2–7d window head
  on. Reversed only by a new trial; a stop belongs to an exit manager after a PASS.
- **Unhedged with a beta guard:** keeps the market-wide flush the thesis describes and refuses a
  pass that is short-market drift. Reversed if the beta regression cannot be estimated (fewer
  than 30 book-days), which K1 would already have killed.
- **PAXG excluded; BTC kept from 2020-09:** PAXG's OI is gold positioning; BTC's early span is
  kept because it was not chosen. Reversed only by a new pre-registration.
- **K1 cap 2.5 annualised:** nearly double XS; above it the run only spends a trial. Reversed
  only by a new pre-registration.
- **PBO = round-2 columns present + control:** follows #919's fallback. Reversed if #983 closes
  before E850 runs with a ruling that slot 1 waits for the round.
- **Funding excluded, reported beside:** the hypothesis is about price, not carry. Reversed if
  printed mean funding per held day is negative and large enough to flip a PASS, in which
  case the verdict states the funding-inclusive figure beside the gate result.
- **|ρ| ≥ 0.5 disqualifier; deflator only on attribution:** the same rule as E841, and the
  deflator goes on the one comparison that pools symbol-events. Reversed if the gate series is
  replaced by pooled events, where n_eff applies to the gate too.
