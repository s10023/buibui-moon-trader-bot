# P3 XS-momentum — beta-neutral + forward-persistence re-test

Date: 2026-06-17
Tool: `PYTHONPATH=. poetry run python tools/xsmom_audit.py` (read-only over `analytics.db`)
Spec: `docs/superpowers/specs/2026-06-17-p3-xsmom-beta-neutral-persistence-design.md`
Predecessor: `docs/audits/2026-06-16-p3-xsmom-sleeve.md` (G3 CLEARED, +1.375)

## Verdict

**GRADUATE the XS sleeve to the IDM / trend×XS portfolio-combine layer.** The
+1.375 headline is **genuine cross-sectional alpha, not bull-beta**: the book is
already intrinsically market-neutral (β ≈ 0, R² ≈ 0–0.05), the alpha is
statistically significant (t ≈ 2.6–4.1), and *beta-hedging raises the Sharpe
rather than lowering it*. The edge is **persistent** — it is explicitly **not** a
2021 alt-mania artifact (2021 was a losing year), and recent windows are strong
(trailing-1y +1.59). The survivorship caveat is unchanged and remains demoted to
a pre-capital rigor audit.

## The four questions

### 1. Does the gate survive dollar-neutralization?

| book                    | sharpe | dsr   | pbo   | boot_lo | max_dd | ann_vol |
| ----------------------- | ------ | ----- | ----- | ------- | ------ | ------- |
| universe original @2bps | +1.375 | 0.997 | 0.295 | +0.596  | −0.397 | 0.321   |
| universe neutral @2bps  | +0.917 | 0.942 | 0.144 | +0.191  | −0.587 | 0.436   |

The crude dollar-neutral re-center (Σ leverage = 0 via cross-sectional
mean-subtraction) costs ~0.46 Sharpe and **narrowly misses the gate on DSR
(0.942 < 0.95)** while still passing PBO (0.144) and the bootstrap floor
(boot_lo +0.191 > 0). It also nearly halves risk-adjusted quality and roughly
doubles max-DD (−39.7% → −58.7%).

**But this is the wrong question to hang the verdict on.** Mean-subtraction is a
*blunt* neutralizer — it shifts every leg by a common amount and so discards part
of the relative-strength signal. The beta-attribution below shows the book did
not need neutralizing in the first place: it is already market-neutral. The
gate-clearing original book (DSR 0.997) stands.

### 2. How much of +1.375 was market beta?

| book           | proxy   | alpha_ann | beta   | alpha_t | hedged_sharpe | r²    |
| -------------- | ------- | --------- | ------ | ------- | ------------- | ----- |
| original       | alt-mkt | +0.500    | −0.074 | +4.12   | +1.585        | 0.035 |
| original       | BTC     | +0.464    | −0.047 | +3.77   | +1.449        | 0.008 |
| dollar-neutral | alt-mkt | +0.494    | −0.120 | +3.03   | +1.164        | 0.051 |
| dollar-neutral | BTC     | +0.433    | −0.072 | +2.60   | +0.998        | 0.010 |

**Almost none.** The book's beta to both the equal-weight alt-market and to BTC
is small and **negative** (−0.05 to −0.12) — a mild market-*short*, not the
bull-tilt the caveat hypothesized. R² is 0.01–0.05, so the market factor explains
1–5% of the return at most. The intercept (alpha) is large (≈ +0.46–0.50
annualized) and significant (t ≈ 2.6–4.1). Decisively: the **beta-hedged Sharpe
of the original book (+1.585 alt-mkt / +1.449 BTC) is *higher* than the raw
+1.375** — the small net-short exposure was a slight *drag*, and removing it
*improves* the result. The edge is alpha.

> Correction to the spec/handoff framing: caveat #2 assumed a "~9% residual
> net-*long* / bull-beta tilt." The data refutes the direction — the residual
> tilt is a mild market-*short* (consistent with the G3 note's "−0.20 corr to the
> alt market"), and it is immaterial (R² ≈ 0). There is no bull-beta to strip.

### 3. Is the edge persistent (not a 2021 artifact)?

Per-calendar-year annualized Sharpe of the dollar-neutral book (the conservative
variant):

```text
2019  -1.782   (low-n: 1d history starts 2019-09; thin cross-section)
2020  +2.688
2021  -0.751   <-- alt-mania year was a LOSER, not the source of the edge
2022  -0.244
2023  +2.417
2024  +0.397
2025  +0.808
2026  +2.248
trailing_2y  +0.969
trailing_1y  +1.590
```

Year-to-year is uneven (2021/2022 negative), but the key falsification holds:
**the edge is not a 2021 alt-mania artifact — 2021 was a losing year.** It is
also **not decayed**: the two most recent windows are strong (trailing-1y +1.59,
trailing-2y +0.97; 2025 +0.81, 2026 +2.25). The 2019 −1.78 is low-n noise (only
~4 months of data and a thin cross-section that early).

### 4. Graduate to the combine layer?

**Yes.** Both addressable caveats clear in the sleeve's favour: the edge is
market-neutral alpha (knob #2 resolved), and it persists into recent regimes
(forward-persistence resolved). The XS sleeve remains the strongest core-sleeve
candidate in the system.

## Implications for the combine layer

- **Treat XS as an already-market-neutral alpha sleeve.** Do not apply the crude
  dollar-neutral re-center for deployment — it is unnecessary (β ≈ 0 already) and
  counterproductive (−0.46 Sharpe, discards signal). If exact market-neutrality
  is ever required, use a **regression beta-hedge**, which *improves* the Sharpe
  (+1.585 vs +1.375) rather than degrading it. The `xs_dollar_neutral` flag stays
  default-off; it is a research diagnostic, not a deployment setting.
- `corr_to_trend` +0.37 (original) confirms XS diversifies the trend sleeve — the
  IDM weighting should lean on XS (+1.375) with trend (+0.36) as the diversifier.

## Standing caveat (unchanged, demoted)

- **Survivorship.** The universe is a fixed today's-survivor set, so +1.375 is an
  upper bound. The bias direction for XS momentum is ambiguous (catastrophic
  delistings were shorts → absence *understates*; slow-bleed delistings are noise
  → slight *overstate*), and the live book always trades currently-listed names.
  A spike confirmed dead-name 1d klines are freely obtainable from Binance fapi
  and the book is already point-in-time-correct given the data, so a
  delisting-inclusive re-test is feasible as a future **pre-capital rigor audit**.
  Interim mitigation: haircut the Sharpe when sizing.

## Method notes

- Read-only replay over `analytics.db`; no schema/golden change. Diagnostics are
  pure (`analytics/xsmom/diagnostics.py`): full-sample OLS attribution reporting
  the beta-hedged-return Sharpe (not the zero-mean residual), and per-year /
  trailing-window persistence. Market proxy = active-set equal-weight universe
  return; BTC proxy reindexed onto the same union daily index for positional
  alignment.
- Sanity: `trend_sharpe` +0.375 is consistent with the G2 trend headline
  (~+0.36 — minor drift from OHLCV added since G2, the DB now extends to
  2026-06-17); the original `corr_to_trend` +0.370 matches G3.
