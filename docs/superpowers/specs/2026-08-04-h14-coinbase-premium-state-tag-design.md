# H14 — Coinbase-premium market-state tag (design + pre-committed gate)

**Date:** 2026-08-04 · **Status:** spec, pre-committed before any avg_r was looked at
· **Board slot:** hypothesis inbox H14; the "Task 2" lead carried since round 8 of
`/ingest-video`.

## 1. The question

> Does the **Coinbase premium** — US-spot demand pressure, measured as Coinbase
> `BTC-USD` against Binance `BTC-USDT` — carry information about the forward
> performance of this system's existing signals, beyond what the stored OHLCV
> already encodes?

Deliverable is a **verdict**, not a feature. Explicit non-goals in §8.

## 2. Why a sixth conditioning test is justified after five NOs

Conditioning axes are 0-for-5 here: regime / session / combo / direction
(2026-06-04), and H8's ten M1 indicator axes (2026-07-24). The prior is against
this one and the spec says so up front.

The argument for running it anyway is **one specific property, not optimism**:
every previously-tested axis was *derived from the same OHLCV the signal itself is
derived from*. Regime, EMA stack, BB %B, VWAP distance, PA character — all are
re-slices of one price series, so none of them could add information the detector
had not already seen. The premium is the first candidate state computed from a
**different venue's order flow**. The SoT names "the next edge needs NEW DATA" as
the binding constraint five times over; this is the cheapest available test of
exactly that claim.

**So the result is interpretable either way.** If it fails, it fails as a
*new-data* test — which is a materially stronger negative than a sixth OHLCV
re-slice, and it moves the diagnosis toward the signal book itself.

## 3. Data — measured 2026-08-04, not assumed

All endpoints free and keyless. Probe results, verbatim:

| Series | Endpoint | Depth |
| --- | --- | --- |
| Coinbase `BTC-USD` daily | `api.exchange.coinbase.com/products/{id}/candles` | ≥ 2019-01 ✓ |
| Binance spot `BTCUSDT` daily | `api.binance.com/api/v3/klines` | **2017-08-17** ✓ |
| Coinbase `USDT-USD` daily | same as Coinbase above | **starts 2021-05-04** |

**Universe coverage was probed and rejected the per-symbol design.** Coinbase lists
24 of the 25 universe bases as `-USD` (only `1000PEPE`/`PEPE` absent), but *depth*
is the constraint: only **5 have 2019 history** (BTC, ETH, BCH, XLM, XRP), 12 reach
2022, 18 reach 2025, and 6 are unclear or very recent. A per-symbol premium panel
would therefore be breadth-starved exactly where breadth has mattered in every
prior sleeve result, and alt Coinbase-USD books are thin enough that a per-alt
premium is mostly listing/liquidity artifact. **H14 is a BTC-level, market-wide
state tag applied to all trades**, which is also what the canonical public index
is, and what "size governor / regime tag" implies.

Coinbase candles cap at 300 bars per request — the fetcher pages.

## 4. The peg confound — the central design finding

The public Coinbase Premium Index (CryptoQuant, Coinglass, and therefore any pundit
claim referencing it) is the **raw** difference. It silently mixes two unrelated
things: US-spot demand imbalance, and **USDT peg deviation**. If USDT trades at
0.9989, every Coinbase-USD price shows a −0.11% "premium" carrying zero demand
information.

Measured at 2026-08-04 01:3x UTC:

```text
CB BTC-USD 63,519.9 | BN BTCUSDT 63,586.3 | USDT-USD 0.9989
raw premium      -0.105%
peg-adj premium  +0.004%     <- the peg explains essentially ALL of it
```

Peg deviation is first-order, not a rounding error: over a 2023 sample (n=244) the
`USDT-USD` close had **stdev 10.6 bps** and ranged 0.9985–1.0078 (−15 to +78 bps) —
the same order of magnitude as the premium being measured.

**Design consequence: carry three series, not one.** The disagreement between them
is a result, not noise.

| Series | Definition | Interpretation | Window |
| --- | --- | --- | --- |
| `prem_adj` | `CB(BTC-USD) / (BN(BTCUSDT) × CB(USDT-USD)) − 1` | **peg-neutral US-spot demand** | 2021-05-04 → |
| `prem_raw` | `CB(BTC-USD) / BN(BTCUSDT) − 1` | the public index, confound included | full |
| `peg_dev` | `CB(USDT-USD) − 1` | stablecoin stress, a state in its own right | 2021-05-04 → |

`prem_adj` is **primary** (clean interpretation; 2021-05 → 2026-08 still spans a
full bear and a full bull). `prem_raw` and `peg_dev` are pre-registered replication
arms, each with its **own** Holm family — never pooled with the primary.

## 5. States — pre-registered, a-priori, causal

Fixed before any avg_r is computed.

**Level axis (primary).** At each daily close compute a **causal** z-score against a
trailing 90-day window using strictly prior data (`.shift(1)` before use, matching
the causality discipline in `forecast/`, `xsmom/`, and `carry/`; a perturbation test
enforces it):

- `elevated` — z ≥ +1.0
- `neutral` — −1.0 < z < +1.0
- `depressed` — z ≤ −1.0

**Change axis (pre-registered secondary, NOT crossed with level).** Sign of the
5-day change in the smoothed premium: `rising` / `falling`. Deliberately not crossed
with the level axis — crossing takes the family from 10 cells to 30 and this repo's
own warning about "a multiple-testing machine" applies directly.

Primary Holm family = **level (3) × direction (2) + change (2) × direction (2) = 10
cells**, on `prem_adj` only.

Each trade is tagged by the state as of the **last completed daily close strictly
before its entry** — never the day of entry, which would be look-ahead.

## 6. The unit of observation — one day, not one trade

**This is the H10 lesson applied in advance, and it is load-bearing.** The state is
market-wide and daily. Tagging ~161k trades with ~1,900 daily states does not give
161k independent observations: on any given day, 25 symbols × 4 timeframes share one
state and one market. H10 hit exactly this and fixed it by collapsing 6,000
symbol-weeks to one observation per calendar week.

**Primary statistic: per-UTC-day mean R** — one observation per day, averaging every
trade entering that day. That yields n ≈ 300 per level tail rather than a
spuriously precise n ≈ 25,000. The per-trade version is computed and printed, but
labelled explicitly as the optimistic comparison that decides nothing.

The circular block bootstrap then does meaningful work, because consecutive daily
observations are genuinely serially correlated and the blocks respect that.

## 7. The gate — pre-committed

Reuses `analytics/audit_guard.py::evaluate_audit_cells` unchanged:
`AuditCell(label, supp_r, kept_r)` → `CellVerdict`, with `bar=0.05` (R),
`alpha=0.05`, `min_n=30`, `haircut_method="holm"`, `boot_method="circular"`,
`seed=12345`. `supp_r` = the state slice's per-day mean R; `kept_r` = the
complement (same direction, all other states) so the `CONCENTRATE` branch stays
meaningful. Family-level DSR / PBO / MinTRL via `analytics/research_guards`.

**⚠ Sign inversion — pinned here and enforced by a test.** `audit_guard` was written
for *suppression* questions, so its decisions run opposite to "is this state good".
Verified in source (`analytics/audit_guard.py:1-20`): `ci.hi <= -bar` → the
suppressed slice is reliably **bad** → `ENABLE`; `ci.lo >= +bar` → reliably **good**
→ `DISABLE`. This inversion bit ST9 and again in H8. The mapping is therefore:

| audit_guard | H14 verdict | Meaning |
| --- | --- | --- |
| `DISABLE` | **BUILD** | trades in this state are reliably better |
| `ENABLE` | **AVOID** | reliably worse — a suppression candidate |
| `CONCENTRATE` | **NO-EDGE** | the complement is even better; nothing to act on |
| `INSUFFICIENT` | **INSUFFICIENT** | n < 30 days |

Anything powered that clears neither side is **NO-EDGE**. A `BUILD`/`AVOID` must
*additionally* satisfy: n ≥ MinTRL(0.95), DSR ≥ 0.95 and PBO ≤ 0.5 over the cell
family, and **early/late sign agreement** (split the window at its midpoint; both
halves must share the sign of the effect).

**Substrate.** `backtest_trades` is primary and gate-deciding (de-biased, deep).
`signal_alert_outcomes` is corroboration only — it is pooled-negative and, before
N6 catch-up, a 35%-complete session-skewed sample, so it can support a verdict but
never carry one.

## 8. Pre-committed decision rule

Written down before the numbers exist, so the result cannot be re-interpreted after
the fact.

1. **BUILD survives on `prem_adj`, and `prem_raw` agrees in sign** (significance not
   required) → file as a **live-gate / size-governor hypothesis**, explicitly NOT a
   detector. Next step is a sizing overlay evaluated in `portfolio/` against its own
   gate. The frozen-detector list is untouched.
2. **All cells NO-EDGE** → record the **sixth** conditioning NO, and state the
   stronger conclusion it licenses: new *venue-price* data does not rescue the
   existing signal book either, which moves the diagnosis from the conditioning to
   the signal book itself.
3. **BUILD on `prem_raw` but NOT on `prem_adj`** → the effect is **peg stress, not
   US demand**. Re-file as a stablecoin-stress hypothesis — a different mechanism,
   and arguably a more interesting one. This branch exists only because §4's probe
   was run; without it the result would have been mislabelled.

**Non-goals / drift guards.** No new detector (the freeze holds). No new sleeve. No
entry trigger — the honest ceiling for a market-wide daily state is a size governor
or a gate. No per-symbol premium panel (§3 killed it on depth). No paid data.

## 9. Deliverables

| File | Responsibility |
| --- | --- |
| `analytics/venue_premium.py` | **pure** — series construction, causal z-score, state labelling, per-day collapse, cell building, `evaluate_premium_states` verdict. No DB, no network. |
| `analytics/store/venue_prices.py` | additive `venue_spot_daily` upsert/getter (venue, symbol, open_time, close). Additive table only — cannot move backtest goldens. |
| `analytics/venue_fetch.py` | Coinbase + Binance-spot daily fetchers, injected `get` so tests are network-free (the `x_fetch.py` / `video_fetch.py` pattern). |
| `tools/premium_state_audit.py` | DB front door + report; `make buibui-premium-state-audit`. Read-only over `analytics.db`. |
| `tests/…` | pure-lib unit tests incl. the **causality perturbation test** and the **sign-inversion assertion**. |

Plan: `docs/superpowers/plans/2026-08-04-h14-coinbase-premium-state-tag.md`.
