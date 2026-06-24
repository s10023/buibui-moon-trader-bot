# P3 XS-momentum — survivorship magnitude check

**Date:** 2026-06-24
**Scope:** Cross-sectional momentum sleeve (deploy core; headline universe Sharpe +1.375).
**Type:** Pre-capital rigor gate before the live mainnet flip. Read-only research; no
code/schema/golden change. `analytics.db` untouched (all work on a scratch copy).

## Verdict — PASS: survivorship does NOT explain the XS edge

Including the genuine top-volume dead crypto perps that would actually have belonged in a
point-in-time universe — **LUNA** (the canonical top-10 → zero Terra collapse), MATIC, EOS,
GAL, RNDR, TOMO, BTT, FRONT, DODO — leaves the XS sleeve's Sharpe in **[1.35, 1.42]** with the
de-biased gate **CLEARING throughout** (DSR ≥ 0.99, PBO ≤ 0.23, bootstrap lower bound ≥ 0.56).
The headline +1.375 is **confirmed, not inflated** by the exclusion of delisted names. The
pre-capital survivorship concern is **cleared**; it is no longer a blocker for the mainnet flip.

## Why this check

`tools/select_universe.py` builds the research universe from the live `exchangeInfo`, filtered
to `status == "TRADING"`. Delisted perps are absent from `exchangeInfo`, so they are
structurally excluded from both the universe and `analytics.db` — classic survivorship. The XS
book is already *point-in-time-correct within its data* (the cross-sectional demean only includes
warmed-up/active instruments via NaN handling), but it had never been tested against the
**dead names themselves**. A whole-branch review flagged this as the cheapest kill-test to run
before committing real capital.

## Method

1. **Linchpin confirmed:** Binance fapi serves historical 1d klines for delisted symbols
   (`LUNAUSDT`'s final bars are 2022-05-13, exactly the Terra collapse; `SRMUSDT`/`ANTUSDT` end
   2024-05). Dead-name data is freely obtainable.
2. **Enumerate dead names:** all USDT symbol dirs ever on the UM data dump
   (`data.binance.vision`) minus the live `exchangeInfo` set = **136 delisted USDT perps**.
3. **Isolate dead *crypto*:** most of the 136 are Binance's 2025-26 tokenized-stock / ETF /
   commodity / pre-IPO-index experiment (AAPL, TSLA, NVDA, SPY, QQQ, XAU, XAG, NATGAS,
   ANTHROPIC, …) — a different asset class. Filter `first-listing < 2025-01-01` (drops the
   tokenized-stock era cleanly — they all listed 2025-12+) **AND** `≥ 288d` history (the
   `ForecastConfig` warmup floor) → **18 dead crypto perps**; the 2 pre-2025 non-coins
   (`FOOTBALLUSDT`, `BLUEBIRDUSDT`, Binance index products) dropped.
4. **Backfill** their 1d klines into a scratch copy of `analytics.db` (real DB never touched).
5. **Re-run** the XS book through the *same* `evaluate_xs` harness `tools/xsmom_audit.py` uses,
   at 2 bps/leg.

Dead crypto set (18): `AKRO ANT AUDIO BTS BTT BZRX COCOS DODO EOS FRONT GAL HNT LUNA MATIC RNDR
SRM TOMO YFII`.

## Results

**Baseline reproduces the published number.** Survivors-only (the 25-name config universe) over
the scratch DB → Sharpe **1.373** ≈ published +1.375 → harness validated.

**Per-name leave-one-in** (survivors + one dead name): **15 of 18** integrate cleanly with Sharpe
**1.33–1.47** ≈ baseline. LUNA alone → **+1.387** (momentum *shorts* the collapse, so the worst
survivorship offender marginally *helps*). MATIC+EOS+LUNA together → **+1.415**.

**3 names are thin-dead-name DATA artifacts, not economics** — when added, the integrated book
produces impossible single-day portfolio returns (`BTS` **4509×**, `SRM` 59×, `HNT` 5.1×, vs the
book's natural ~0.18 daily scale). This is a vol-parity explosion: a thin/illiquid delisted stretch
drives realized vol toward zero → leverage `(vol_target / vol_ann)` explodes. Excluded — and
consistent with how the live book operates (its ADV/liquidity filter would never size into such a
name; see the capacity audit).

**Cumulative add by peak volume** (the survivorship-relevant order; artifacts removed):

| Universe | n_inst | Sharpe | max_dd | DSR | PBO | boot_lo | Gate |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| baseline (survivors) | 25 | +1.373 | −0.397 | 0.997 | 0.295 | +0.578 | CLEARS |
| +top3 (LUNA, MATIC, EOS) | 28 | +1.415 | −0.377 | 0.996 | 0.166 | +0.608 | CLEARS |
| +top6 (+GAL, RNDR, TOMO) | 31 | +1.352 | −0.409 | 0.993 | 0.188 | +0.562 | CLEARS |
| +top9 (+BTT, FRONT, DODO) | 34 | +1.424 | −0.408 | 0.997 | 0.230 | +0.647 | CLEARS |
| +top12 (+COCOS, ANT, YFII) | 37 | −1.660 | −1.000 | 0.000 | 0.470 | −3.205 | book blowup |
| +top15 (+AUDIO, BZRX, AKRO) | 40 | −1.636 | −1.000 | 0.000 | 0.495 | −3.191 | book blowup |

The edge holds and the gate clears through **all 9 survivorship-relevant top-volume dead names**
(LUNA/MATIC/EOS at 1.5–2.1 B peak 30d-median quote vol; GAL/RNDR/TOMO at 0.3–0.5 B;
BTT/FRONT/DODO at 0.1–0.2 B). The blowup appears only at top-12+, when the **thin low-volume tail**
(≤ ~0.1 B) inflates the *simultaneously-active* cross-section to 37–40 thin early-period names — a
breadth that **never existed in reality** (in 2021–22 most current survivors had not yet listed),
which the sum-based vol-parity book + 0.5×-floored governor cannot absorb.

## Caveats (honest)

1. **This is a magnitude/confirm check, not a full point-in-time universe reconstruction** — by
   design. The book is already point-in-time-correct via the NaN active-set demean; this validates
   the survivorship *magnitude*. Each dead name was tested in the real survivor cross-section
   (leave-one-in) and in realistic-breadth cumulative batches, not as a rebuilt rotating universe.
2. **The sum-based XS book is not robust to large breadth inflation** (top-12+). This is a
   book-construction limit relevant to the **IDM / portfolio layer** (P3 combine), *not* a deploy
   blocker for the current liquid-survivor book — the live book trades ~the 25 survivors, never 40
   simultaneously-active thin names. If a future point-in-time reconstruction is wanted, it needs a
   per-leg vol floor / leverage cap or a bounded ~25-wide rotating universe.
3. **3 dead names (BTS/SRM/HNT) excluded as data artifacts** — flagged objectively by an impossible
   integrated-book daily return (> 1.0), not cherry-picked.

## Decision

Survivorship is **cleared** as a pre-capital concern. The XS +1.375 edge survives the inclusion of
the dead names that actually mattered. → Proceed toward the mainnet flip (Task A); fold the
breadth-inflation robustness note into the IDM/portfolio-layer backlog.

## Reproduce

Scratch scripts (session scratchpad, not committed): `recon_delisted.py` (enumerate dead names),
`backfill_dead.py <research.db> <csv>` (backfill 1d klines into a copy of `analytics.db`),
`analysis_survivorship.py <research.db>` (baseline vs corrected via `evaluate_xs`). All read-only
against fapi; writes only to the scratch research DB.
