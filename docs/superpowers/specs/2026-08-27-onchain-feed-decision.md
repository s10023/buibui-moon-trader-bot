# On-chain feed — decision spec

Decides whether to wire an on-chain data feed, and if so what the minimum viable shape is.
Written 2026-08-27 off the `/ingest-feed` On-Chain Analysis screened tranche, which supplied
the construction of every metric named here.

## 1. Problem

`docs/plans/mechanics-backlog.md` carries an on-chain **risk composite** — a nine-slider
dashboard, seven sliders at non-zero weight, each component min-max normalised to 0-1 and
averaged. Its row has twice been filed as "DATA-BLOCKED at the source", then partially
un-blocked, and the un-blocking rested on a **primitive map that is wrong**.

That row's map: MVRV and MVRV Z-Score need realized cap ("the genuinely hard one"); Puell
needs issuance; MCTC, mCTC and Terminal Price need thermocap; Transaction Fees needs fees.
Conclusion: *"five of the seven need only issuance, fees, price and supply — all free"*, so
the cheap test is "does dropping the two MVRV legs cost the composite anything?"

**The count is right and the membership is inverted on the decisive pair.** That is why it
survived review: the headline number reproduces.

## 2. The question that actually decides it

Every chart in all six videos of the 2026-08-27 tranche names one vendor: **Coin Metrics**.
So this was never seven independent free/paid rulings. It is one:

> **What does the Coin Metrics *community* tier actually expose?**

That is a read, not a build, and until 2026-08-27 nobody had run it.

## 3. What the community tier exposes — MEASURED 2026-08-27, not quoted

`https://community-api.coinmetrics.io/v4/` — **no API key**, 10 requests per 6-second sliding
window per IP. `catalog-v2/asset-metrics?assets=btc` returns **31 metrics**. The ones that
matter:

| exposed | not exposed |
| --- | --- |
| `PriceUSD` · `SplyCur` · `CapMrktCurUSD` | `CapRealUSD` (realized cap) |
| **`CapMVRVCur`** (MVRV, precomputed) | `FeeTotUSD` · `RevUSD` · `RevAllTimeUSD` |
| `IssTotUSD` · `IssTotNtv` | `SplyMiner0HopAllNtv` / `1Hop` (miner balances) |
| **`FeeTotNtv`** (fees in native units) | anything matching `CDD` / `DaysDestroyed` / `VDD` — **zero hits** |
| `HashRate` · `AdrActCnt` · `TxCnt` · `BlkCnt` · exchange flows | |

**One request returned 5,884 daily rows spanning 2010-07-18 → 2026-08-26, no pagination.**
Sixteen years of daily history, keyless, in under a second.

### 3a. Three derivations, each verified against real values

The gaps above are mostly apparent. Verified 2026-08-27:

1. **Realized cap** `= CapMrktCurUSD / CapMVRVCur`. MVRV *is* market cap ÷ realized cap, and
   both sides are free. Sanity-checked: realized cap sat at ~$1.058-1.063T across 2026-08-20
   → 08-25 while market cap swung $1.47-1.58T — a cost-basis series that ratchets slowly
   while market cap oscillates is exactly the expected behaviour.
   **Realized price** `= that / SplyCur` ≈ $52,700-53,000 over the same window.
2. **Transaction fees in USD** `= FeeTotNtv * PriceUSD`. Only the USD-denominated field is
   withheld; the native-unit field is not.
3. **Thermocap** `= cumsum(IssTotUSD + FeeTotNtv * PriceUSD)`, i.e. cumulative miner revenue
   rebuilt from its two components rather than read from the withheld `RevAllTimeUSD`.
   Verified: **$97.4B** across 5,884 daily rows, consistent with published BTC thermocap.
   `MCTC = CapMrktCurUSD / thermocap` then reads **16.2** at 2026-08-25.

## 4. The corrected leg map

| leg | primitive | community tier | filed row said |
| --- | --- | --- | --- |
| MVRV | `CapMVRVCur` | ✅ **free, precomputed** | ❌ gated ("genuinely hard") |
| MVRV Z-Score | derived realized cap + stdev(market cap) | ✅ **free by derivation** | ❌ gated |
| Puell Multiple | `IssTotUSD` / 365d MA | ✅ free | ✅ free |
| Transaction Fees | `FeeTotNtv * PriceUSD` | ✅ free by derivation | ✅ free |
| MCTC | market cap / derived thermocap | ✅ free by derivation | ✅ free |
| **mCTC** | **entity-labelled miner balances** | ⛔ **gated** | ❌ called free |
| **Terminal Price** | **Coin Days Destroyed** (UTXO-age) | ⛔ **gated, absent entirely** | ❌ called free |

⇒ **5 free / 2 gated — the same count the filed row reached, with the membership inverted on
both hard legs.** Realized cap, named as the blocker, is free. Terminal Price and mCTC, named
as free, are the only two that are actually unreachable.

⚠ **Terminal Price is not gated by tier, it is gated by absence.** No CDD-family metric
appears in the community catalog at all, and Terminal Price is built entirely on spend-age
(`CDD → VDD = Price×CDD → Transferred = cumVDD/(MarketAge×Supply) → Terminal = 21×Transferred`,
chart-verbatim). Its companion `Balanced Price = Transferred − Realized Price` is therefore
also out, even though realized price itself is now free.

⚠ **mCTC is gated on labels, not numbers.** ThermoCap is derivable; `MinerCap` = balances of
all *mining entities* × price needs to know which addresses are miners — a proprietary
labelled set. Do not confuse it with MCTC (market cap), which is free.

## 5. Minimum viable shape, if built

Five of seven legs reachable from **one keyless vendor** with 16 years of daily history.

- **Fetcher** — `analytics/onchain/coinmetrics.py`. Keyless; respect 10 req/6s. Full history
  is one request per metric set, so backfill is not a project.
- **Table** — `onchain_metrics(asset, metric, time, value)`, daily. ⚠ Follow the `ohlcv_all`
  precedent: put the **source** in the primary key from day one. A second vendor later is
  then a coexisting row, not an overwrite → `.claude/context/analytics.md`.
- **Derived layer** — realized cap / realized price / fees-USD / thermocap are **computed,
  never stored as if fetched**. Store the primitive, derive on read, or the provenance of a
  derived number is lost the moment someone quotes it.
- **Freshness leg** — per ST61b it must sit on a **SCHEDULED caller**, not a hand-run target,
  and per ST90 staleness is measured in **bar units** with a tolerance set by the refresh
  **cadence**, never a flat wall-clock threshold.
- **Backfill guard** — `AssetEODCompletionTime` is exposed; use it rather than assuming the
  latest daily row is final.

## 6. Pre-registered test — and why it is narrow on purpose

The composite is worth **one** pre-registered trial, not a scan. Trial count dominates n in
this repo's gate, and a per-cell sweep is structurally unreachable.

> **H:** the 5-leg composite (equal-weighted, each leg min-max normalised on a **trailing**
> window) separates forward book-day returns, against the 3-leg gate
> `DSR ≥ 0.95 ∧ PBO ≤ 0.5 ∧ boot_lo > 0` via `analytics.research_guards.passes_gate`.

⚠⚠ **Normalise on a TRAILING window only.** The vendor's own dashboard min-max normalises
over full history, which is **look-ahead by construction** — the min and max include future
data — and its page calls itself a backtest. Same class as the `bos` swing-bar leak and the
ensemble score whose effect lived in an expanding-window fit and flipped sign under a
trailing one. Expect the trailing refit alone to change the result.

⚠ **Day-cluster the verdict, do not also deflate by `effective_independent_series`** — they
are one phenomenon with two estimators. One asset here means singleton clusters anyway.

⚠ **The cycle-timing claims that motivated these metrics are NOT part of this test and must
not be smuggled in.** Fee spikes lagging tops, Terminal Price crossings, mCTC trend-line
touches, Q4-post-halving tops — every one rests on **n_eff ≈ 3 cycles**. That is the
bear-score shape: real-looking, unreachable, hand-read at most. They were deliberately not
filed as hypotheses on 2026-08-27.

## 7. What this does NOT do

- Does not buy anything. Every leg above is the free tier or is derived from it.
- Does not gate, size or suppress any live signal. Like the bear score, if it is ever
  rendered it is a **number, never a gate**.
- Does not rebuild Terminal Price or mCTC. Both are unreachable and stay that way.
- Does not touch `ohlcv_all`, the signal path, or any existing star rating.
- Does not resolve whether the composite has an edge. It resolves whether the question is
  *askable*, which until now it was not.

## 8. Decision log

| decision | why | **what reverses it** |
| --- | --- | --- |
| One vendor ruling, not seven primitive rulings | every chart in the tranche names Coin Metrics | a leg sourced elsewhere in a later video |
| Build on the community tier | 31 metrics, keyless, 16y daily history, one request | the free tier withdrawing a leg, or a rate limit that makes daily refresh impractical |
| Derive rather than buy realized cap / fees / thermocap | all three verified reproducible from free series | a derived value diverging materially from a paid reference, if one is ever seen |
| 5 legs, drop mCTC + Terminal Price | entity labels and CDD are absent, not merely paywalled | Coin Metrics exposing a CDD or miner-balance metric on the community tier |
| Store primitives, derive on read | a derived number stored as fetched loses its provenance | none — this is a hygiene rule |
| One pre-registered trial | trial count dominates n; a scan is unreachable | none — scanning is the failure mode, not the fix |
| Cycle-timing claims excluded | n_eff ≈ 3 across every one of them | a materially larger independent sample, which one more cycle does not supply |
| **The whole thing may still be DON'T BUILD** | reachable ≠ worth building; the composite has never cleared any gate | the pre-registered test in §6 clearing all three legs |

⚠ **"Unreachable, do not build" was a live outcome and remained so until §3 was measured.**
What changed is not that the composite got better — it is that the question stopped being
blocked. Those are different findings and the second does not imply the first.
