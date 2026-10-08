# E841 — pre-registration for hedged violent-up continuation, out of universe

**Date:** 2026-10-08 · **Status:** pre-registered, unrun · **Issue:** H1 adjacent result
(#841), slot decided in #921, construction decided in #945 under the #864 map · **Round:** 1,
third of three candidates

This file fixes the holdout, the construction, the gate and the kill-switches **before** any
holdout number exists. The symbol list (Appendix A) was frozen from Binance's public
`exchangeInfo` before the holdout was backfilled; no holdout bar had been read when any choice
below was made. A change to any of them after the first look is a new trial, not a correction.

## 1. Hypothesis

A violent up day on a USDT perpetual predicts continuation over the next five days **in excess
of the market**, net of modelled cost, on perps the nominating look never saw.

The nominating look (25-perp universe, 2019-09 → 2026-10) read +5.745% mean five-day return
per event date against a +1.146% all-days baseline: +4.60pp excess, 506 dates. The raw book is
beta-heavy (+0.51 to the equal-weight universe), so the unit tested here is hedged; a raw book
would let market beta pass the gate.

## 2. Trial accounting

- **Round 1 is fixed at N = 3** (#833, applied in #864). E841 spends one; its DSR is computed at
  N = 3 whatever H13 and C836 turn out to be.
- **Prior looks, disclosed and not counted.** The 2026-08-20 measurement (412 dates, +3.63pp),
  the #920 reproduction (506 dates, +4.60pp), the bear-score regime splits on the short side
  (#841), and the #921 XS correlation (daily ρ −0.118, under the |ρ| ≥ 0.5 disqualifier). All
  of them read the 25 universe perps only, and none of them chose a holdout parameter.
- **K1's yardstick re-reads the nominating data** (§5). It decides only whether the holdout
  can run, never what the holdout is, so it adds no trial.

## 3. Construction — frozen

| Item | Value |
| --- | --- |
| Holdout | the 402 symbols in Appendix A: USDT-M `PERPETUAL`, status `TRADING`, `underlyingType` `COIN`, base not in `tools/select_universe.STABLE_BASES`, `onboardDate` at least 365 days before the snapshot (2026-10-08 11:42 UTC), and not in `config/universe.toml`. The two `INDEX` perps (ALLUSDT, BTCDOMUSDT) are excluded as baskets. No symbol shares an underlying with the universe through a `1000`/`1000000`/`1M` prefix |
| Data | Binance 1d bars, `buibui analytics backfill --symbols <Appendix A> --timeframes 1d --since 2019-01-01`, venue `binance`. A symbol the backfill returns no bars for is dropped and named in the result |
| Return | r_D = close_D / close_{D−1} − 1 |
| Trigger | r_D ≥ 0.05 **and** z_D = r_D / sd(r_{D−20} … r_{D−1}) ≥ 2.5, sample sd (ddof 1) over the 20 returns before D, exactly as the #920 reproduction computed it |
| Eligibility on D | at least 60 days after the symbol's first 1d bar (listing pumps are a different phenomenon); 20 prior returns exist; liquidity: the median of volume × close over the 30 bars ending at D is at least **$5M**. `volume` is base volume, so volume × close approximates quote volume |
| Entry | long at the open of D+1 |
| Hold | five daily bars: exit at the close of D+5 |
| Re-trigger | a symbol that triggers while already held is skipped, never stacked |
| Size | notional N = 1 / σ20, σ20 the same prior-20 sd the trigger uses, so 1R is a one-σ20 move |
| Hedge | at entry, short notional N of the 25-perp universe, split equally across the universe symbols with a bar at D+1, held without rebalancing and closed at the close of D+5 |
| Daily P&L | each position's long-leg return minus its hedge-basket return over each held bar, times N, in R. Bar D+1 is open-to-close; later bars are close-to-close |
| Costs | production values read from `config/strategy_params.toml` at run time (today `fee_pct` 5 bps, `slippage_bps` 2 per taker leg), never restated in the driver. Each leg pays 2(fee + slip) × N per round trip, so a position pays 4(fee + slip) × N, half charged on its entry day and half on its exit day |
| Gates | none: no regime, bear-score, HTF or day filter |
| Sample | every trigger whose exit bar (D+5) closes on or before 2026-10-08, the commit date of this file. Later data is a forward record and is never run through this gate |

## 4. Observation unit and the gate

- **Book-day series**: one row per UTC calendar day with at least one open position: the mean
  net R across positions open that day. Averaging inside a day absorbs the cross-symbol
  correlation once, so per AGENTS.md the rows are not deflated again. Each row is one day's
  mark-to-market P&L, so the five-day overlap needs no deflator either; the bootstrap covers
  what serial correlation remains.
- **Edge** = `analytics.research_guards.passes_gate(dsr, pbo, boot_lo)`: DSR ≥ 0.95 at N = 3
  and boot_lo > 0, both on E841's own series; boot_lo from `block_bootstrap_ci` with
  `block` = 5, the hold length.
- **PBO** runs across round 1's columns on the union of UTC calendar days, 0R on a day a
  column does not trade (#921).

## 5. Kill-switches — checked before the gate is read

The driver prints K1's inputs (n and sd) and K2's counts before it computes any mean of the
holdout series.

- **K1 Power.** On the holdout series, measure n (book-days) and sd, then run
  `tools/distil_power.py --units per_book_day --sr-footing per_obs --periods-per-year 365
  --n-obs <n> --n-trials 3 --sd <sd> --sr-variance <max(measured across round 1's columns,
  0.005)>`, with **no `--n-eff`**. The yardstick is the realised annualised Sharpe of the
  §3 construction run on the 25 universe perps (the nominating data, read only for this).
  **Do not run E841** if the required annualised Sharpe exceeds that yardstick, or if the
  yardstick is not positive. The holdout's `analytics.forecast.effective_independent_series`
  n_eff over its per-symbol daily returns is printed beside K1 as a disclosure.
- **K2 Fidelity.** The driver's trigger, run raw on the 25 universe perps (no eligibility
  filter, no hedge, equal notional, close-to-close five-day return from D's close) with the
  panel cut at the 2026-10-08 bar, must reproduce #920: **1,079 symbol-days, 506 dates,
  +5.745% per-date mean against a +1.146% baseline**. Any difference is explained before
  anything runs, never tolerated. The trigger must also pass a truncation test in the style of
  `tests/test_lookahead.py`: the triggers at D are identical when the series is cut at D.
- Same-bar ties do not arise: the construction has no stop and no target.

## 6. Reading the verdict

- **PASS**: the gate clears. E841 is an edge out of universe.
- **FAIL**: read as "no effect found". A filed NO only where
  `analytics.audit_guard.powered_null` licenses one; a failure to clear the gate is not power.
- **Killed** (K1, K2): a failure to clear, never a null, and never quoted as "no edge". The
  slot stays empty (#921).

## 7. Tradeable test — only after a PASS

1. **Minimum lot.** At 1% risk per R on the equity held when the test runs, drop every leg
   below the exchange minimum lot (skipped, never sized up) and re-run the gate. The hedge
   splits N across 25 names, so it hits the floor first. The equity figure stays local.
2. **Survival.** #915's f = min(1/k at 5% over 12 months, half-Kelly net) must be at least the
   1% measurement size.
3. **XS.** Report the correlation of E841's book-day returns to the XS sleeve's. The hedge
   shorts the XS sleeve's own symbols, so trading both needs separate sub-accounts:
   `cancel_open_orders` is symbol-wide (AGENTS.md, XS execution).

## 8. Disclosures

- **Survivorship, in continuation's favour.** Only currently listed perps can be fetched, so a
  name that pumped and was later delisted is missing; the 365-day listing rule, read today,
  keeps only names that lasted a year.
- **Shared dates.** Violent up days are often market-wide, so the holdout trades many of the
  universe's event dates. It is out of sample in symbols, not in time.
- **Costs are optimistic.** Small alts trade wider than the modelled 7 bps per leg; the $5M
  floor narrows the gap without closing it. Modelled costs are an optimistic bound whose error
  runs one way.
- **The prior is in different units.** It is equal-notional %, unhedged; this test is
  vol-scaled R, hedged. K1's yardstick re-expresses it in this test's units.

## 9. What the build owes (the hand-off)

- A tracked driver under `tools/` (never scratch) implementing §3–§5, with tests for the
  trigger (K2 counts on a fixture), the truncation test, the re-trigger skip, the
  no-rebalance hedge and the cost charge.
- The holdout backfill, run in batches between the signal-watch timer's DuckDB writes.
- The run itself, K1 and K2 reported before the gate.

## 10. Decision log

Each line names the observable that would reverse it.

- 402 symbols, 365-day listing, causal $5M gate rather than today's volume: today's volume is
  partly an outcome of past pumps. Reversed only by a new pre-registration.
- 60-day listing skip: reversed by evidence that post-listing days behave like the rest of the
  sample, which would need its own look.
- Hedge = 25-perp universe, equal notional, no rebalance: it is liquid, has history since 2019,
  and is the basket the +0.51 beta was measured against. A holdout basket would contain the
  co-pumping names on market-wide days and cancel the signal by construction.
- Vol-scaled positions, mean of open: constant gross risk, and alt-coin tails do not decide the
  result. Reversed if K1 cannot be priced in these units.
- Re-trigger skipped: one position per symbol keeps N's meaning. Reversed only by a new trial.
- K1 without `--n-eff` amends #921, which named the holdout's n_eff: book-day rows already
  average the cross-section, and AGENTS.md forbids deflating them twice. Reversed if the
  book-day series is replaced by pooled symbol-days, where the n_eff deflator applies.
- K1 yardstick = the in-universe hedged Sharpe: a holdout unable to detect the effect that
  nominated it cannot confirm it. Reversed if the yardstick changes construction.

## Appendix A — the holdout (402 symbols, frozen 2026-10-08 11:42 UTC)

```text
0GUSDT 1000000MOGUSDT 1000BONKUSDT 1000CATUSDT 1000CHEEMSUSDT 1000FLOKIUSDT 1000LUNCUSDT 1000RATSUSDT
1000SATSUSDT 1000SHIBUSDT 1000XECUSDT 1INCHUSDT 1MBABYDOGEUSDT 2ZUSDT 4USDT AAVEUSDT
ACEUSDT ACHUSDT ACTUSDT AEROUSDT AEVOUSDT AGLDUSDT AGTUSDT AINUSDT
AIOTUSDT AIOUSDT AIXBTUSDT AKEUSDT AKTUSDT ALCHUSDT ALGOUSDT ALICEUSDT
ALPINEUSDT ALTUSDT ANIMEUSDT ANKRUSDT APEUSDT API3USDT APTUSDT ARBUSDT
ARCUSDT ARIAUSDT ARKMUSDT ARKUSDT ARPAUSDT ARUSDT ASRUSDT ASTERUSDT
ASTRUSDT ATHUSDT ATOMUSDT AUCTIONUSDT AUSDT AVAAIUSDT AVAUSDT AVNTUSDT
AWEUSDT AXLUSDT AXSUSDT B2USDT BABYUSDT BANANAS31USDT BANANAUSDT BANDUSDT
BANKUSDT BANUSDT BARDUSDT BASUSDT BATUSDT BBUSDT BEAMXUSDT BELUSDT
BERAUSDT BICOUSDT BIGTIMEUSDT BIOUSDT BLESSUSDT BLURUSDT BMTUSDT BNTUSDT
BOMEUSDT BRETTUSDT BROCCOLI714USDT BROCCOLIF3BUSDT BRUSDT BSVUSDT BTRUSDT BULLAUSDT
BUSDT C98USDT CAKEUSDT CARVUSDT CATIUSDT CELOUSDT CELRUSDT CETUSUSDT
CFXUSDT CGPTUSDT CHILLGUYUSDT CHRUSDT CHZUSDT CKBUSDT COAIUSDT COMPUSDT
COOKIEUSDT COTIUSDT COWUSDT CROSSUSDT CRVUSDT CTKUSDT CTSIUSDT CUSDT
CVCUSDT CVXUSDT CYBERUSDT DASHUSDT DEEPUSDT DEXEUSDT DIAUSDT DODOXUSDT
DOGSUSDT DOLOUSDT DOODUSDT DOTUSDT DRIFTUSDT DUSKUSDT DYDXUSDT DYMUSDT
EDENUSDT EDUUSDT EGLDUSDT EIGENUSDT ENJUSDT ENSUSDT EPICUSDT ERAUSDT
ESPORTSUSDT ETCUSDT ETHFIUSDT ETHWUSDT EVAAUSDT FARTCOINUSDT FETUSDT FFUSDT
FHEUSDT FIDAUSDT FLOCKUSDT FLOWUSDT FLUIDUSDT FLUXUSDT FORMUSDT FUSDT
GALAUSDT GASUSDT GLMUSDT GMTUSDT GMXUSDT GOATUSDT GPSUSDT GRASSUSDT
GRIFFAINUSDT GRTUSDT GTCUSDT GUNUSDT GUSDT HAEDALUSDT HANAUSDT HBARUSDT
HEIUSDT HEMIUSDT HIVEUSDT HMSTRUSDT HOLOUSDT HOMEUSDT HOTUSDT HUMAUSDT
HUSDT HYPERUSDT ICNTUSDT ICPUSDT IDOLUSDT IDUSDT ILVUSDT IMXUSDT
INITUSDT INUSDT IOSTUSDT IOTAUSDT IOTXUSDT IOUSDT JASMYUSDT JELLYJELLYUSDT
JOEUSDT JSTUSDT JTOUSDT JUPUSDT KAIAUSDT KAITOUSDT KASUSDT KAVAUSDT
KERNELUSDT KGENUSDT KMNOUSDT KNCUSDT KOMAUSDT KSMUSDT LAUSDT LAYERUSDT
LDOUSDT LIGHTUSDT LINEAUSDT LISTAUSDT LPTUSDT LQTYUSDT LSKUSDT LTCUSDT
LUMIAUSDT LUNA2USDT LYNUSDT MAGICUSDT MANAUSDT MANTAUSDT MASKUSDT MAVIAUSDT
MAVUSDT MELANIAUSDT MEMEUSDT MERLUSDT METISUSDT MEUSDT MEWUSDT MINAUSDT
MIRAUSDT MITOUSDT MOCAUSDT MOODENGUSDT MORPHOUSDT MOVEUSDT MOVRUSDT MTLUSDT
MUBARAKUSDT MUSDT MYXUSDT NAORISUSDT NEIROUSDT NEOUSDT NEWTUSDT NILUSDT
NMRUSDT NOMUSDT NOTUSDT NXPCUSDT OGNUSDT OGUSDT ONEUSDT ONGUSDT
ONTUSDT OPENUSDT OPUSDT ORCAUSDT ORDERUSDT ORDIUSDT PARTIUSDT PENDLEUSDT
PENGUUSDT PEOPLEUSDT PHAUSDT PIPPINUSDT PIXELUSDT PLAYUSDT PLUMEUSDT PNUTUSDT
POLUSDT POLYXUSDT POPCATUSDT PORTALUSDT POWRUSDT PROMUSDT PROVEUSDT PTBUSDT
PUMPUSDT PUNDIXUSDT PYTHUSDT QNTUSDT QTUMUSDT QUSDT RAREUSDT RAYSOLUSDT
REDUSDT RENDERUSDT RESOLVUSDT REZUSDT RIFUSDT RLCUSDT RONINUSDT ROSEUSDT
RPLUSDT RSRUSDT RUNEUSDT RVNUSDT SAFEUSDT SAGAUSDT SAHARAUSDT SANDUSDT
SANTOSUSDT SAPIENUSDT SCRUSDT SEIUSDT SFPUSDT SHELLUSDT SIGNUSDT SIRENUSDT
SKLUSDT SKYAIUSDT SKYUSDT SLPUSDT SNXUSDT SOLVUSDT SOMIUSDT SONICUSDT
SOONUSDT SOPHUSDT SPELLUSDT SPKUSDT SPXUSDT SQDUSDT SSVUSDT STBLUSDT
STEEMUSDT STOUSDT STRKUSDT STXUSDT SUNUSDT SUPERUSDT SUSDT SUSHIUSDT
SWARMSUSDT SXTUSDT SYNUSDT SYRUPUSDT TACUSDT TAGUSDT TAIKOUSDT TAKEUSDT
TAUSDT THETAUSDT THEUSDT TIAUSDT TLMUSDT TNSRUSDT TOSHIUSDT TOWNSUSDT
TRADOORUSDT TRBUSDT TREEUSDT TRUMPUSDT TRUTHUSDT TRXUSDT TSTUSDT TURBOUSDT
TUSDT TUTUSDT TWTUSDT UBUSDT UMAUSDT UNIUSDT USELESSUSDT USTCUSDT
USUALUSDT VANAUSDT VELODROMEUSDT VELVETUSDT VETUSDT VIRTUALUSDT VTHOUSDT WALUSDT
WAXPUSDT WCTUSDT WIFUSDT WLFIUSDT WOOUSDT WUSDT XAIUSDT XANUSDT
XMRUSDT XNYUSDT XPINUSDT XPLUSDT XTZUSDT XVGUSDT XVSUSDT YFIUSDT
YGGUSDT ZENUSDT ZEREBROUSDT ZETAUSDT ZILUSDT ZKCUSDT ZKUSDT ZORAUSDT
ZROUSDT ZRXUSDT
```
