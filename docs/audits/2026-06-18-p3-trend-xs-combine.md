# P3 trend×XS combine — clears the gate, but Sharpe-dominated by XS-solo

**Date:** 2026-06-18
**Tool:** `make buibui-combine-audit` (`tools/combine_audit.py`, read-only over `analytics.db`)
**Package:** `analytics/combine/` (book-return-space combine, causal-rolling Carver IDM, equal-risk default)
**Spec / plan:** `docs/superpowers/specs/2026-06-18-p3-trend-xs-combine-design.md` ·
`docs/superpowers/plans/2026-06-18-p3-trend-xs-combine.md`

## Verdict

The combined book **passes the de-biased gate** (DSR 0.966 ∧ PBO 0.206 ∧ boot_lo +0.357
over {trend, XS, combined}) and the diversification is **mechanically real** (div_mult
1.195 → ~16% pre-scaling vol reduction; realized IDM 1.25). **But it does NOT beat the
best single sleeve: combined Sharpe +1.145 < XS-solo +1.375.** Trend's standalone Sharpe
(+0.38) is too weak to lift a blend with XS — the Sharpe-dilution from spending risk on
the weaker sleeve outruns the 0.37-correlation diversification benefit.

**→ Decision: deploy XS-solo as the core. Do NOT fold trend in via this combine.** Trend
is demoted from "diversifier worth combining" to a **shelf candidate** — it is still a real,
cost-robust positive (and on majors it is the *stronger* sleeve), so it is kept, not
killed, and re-evaluated only when (a) a third, comparably-strong sleeve (carry / basis)
exists to combine with, or (b) drawdown-reduction, not Sharpe, becomes the deploy objective.
The combine layer itself is correct and validated — it is the reusable portfolio-construction
socket for that future sleeve.

## Headline (universe @2bps, equal-risk 0.5/0.5, causal IDM)

| metric | combined | XS-solo | trend-solo |
| --- | --- | --- | --- |
| Sharpe (annual) | **+1.145** | +1.375 | +0.382 |
| max drawdown | −24.0% | — | — |
| annual vol | 21.5% | ~20% (governed) | ~20% (governed) |
| DSR | 0.966 | — | — |
| PBO | 0.206 | — | — |
| boot CI (95%) | [+0.357, +1.923] | — | — |
| gate | **PASS** | — | — |
| corr(XS, trend) | +0.370 | | |
| realized IDM | +1.253 | | |
| diversification mult | +1.195 | | |
| n (days) | 2476 | | |

The gate (the handoff's bar: DSR ≥ 0.95 ∧ PBO ≤ 0.5 ∧ boot_lo > 0) is **cleared** — the
combined stream is a legitimate, non-overfit positive-EV book. The *separate* question the
combine was built to answer — "does combining beat either sleeve alone?" — is **no**.

## Why diversification does not help here

Diversification is a free lunch only when the sleeves have *comparable* Sharpes. For an
equal-risk blend of two ~equal-vol streams with Sharpes `S₁`, `S₂` and correlation `ρ`, the
IDM rescales vol to target but is **Sharpe-neutral** (it scales mean and vol equally), so:

```text
S_combined ≈ (S_xs + S_trend) / √(2·(1 + ρ))
           = (1.375 + 0.382) / √(2·1.37)
           = 1.757 / 1.655  ≈  1.06   (observed +1.145, governor/weights differ slightly)
```

To beat `S_xs = 1.375` you need `S_trend ≳ S_xs·(√(2(1+ρ)) − 1) = 1.375·0.655 ≈ 0.90`.
Trend delivers **0.38**. So no amount of IDM rescaling makes the equal-weight combine beat
XS-solo — the weak sleeve dilutes the strong one's ratio. The going-in thesis (combine →
~1.4–1.5) was wrong precisely because it assumed correlation, not relative Sharpe, was the
binding term.

## Sensitivity (all reported, not gate-selected)

**Weights** — tilting toward XS barely moves the headline; the only weighting that beats
XS-solo is the degenerate "≈100% XS" (i.e. drop trend):

| weights (XS/trend) | Sharpe | DSR | PBO | boot_lo | gate |
| --- | --- | --- | --- | --- | --- |
| 0.5 / 0.5 | +1.145 | 0.966 | 0.206 | +0.357 | PASS |
| 0.7 / 0.3 | +1.144 | 0.966 | 0.225 | +0.336 | PASS |
| 0.79 / 0.21 | +1.159 | 0.968 | 0.271 | +0.333 | PASS |

**IDM mode** — the verdict is robust to the IDM-estimation choice (the causal, no-look-ahead
headline is essentially the same as the full-sample static reference):

| IDM mode | Sharpe | DSR | PBO | boot_lo | gate |
| --- | --- | --- | --- | --- | --- |
| causal (headline) | +1.145 | 0.966 | 0.206 | +0.357 | PASS |
| static (sensitivity) | +1.163 | 0.969 | 0.272 | +0.383 | PASS |

**Cost** — the combined Sharpe decays at the *same rate* as XS-solo (≈ −0.20 Sharpe over
0→16 bps), so double-counted turnover is **not** binding — book-return-space was the right
call and forecast-space netting is **not** triggered. The combine stays a PASS through 8 bps
and marginally fails the DSR bar (0.934 < 0.95) only at an extreme 16 bps/leg:

| cost (bps/leg) | combined Sharpe | XS-solo Sharpe | DSR | PBO | boot_lo | gate |
| --- | --- | --- | --- | --- | --- | --- |
| 0 | +1.170 | +1.401 | 0.969 | 0.203 | +0.382 | PASS |
| 2 | +1.145 | +1.375 | 0.966 | 0.206 | +0.357 | PASS |
| 8 | +1.068 | +1.297 | 0.954 | 0.221 | +0.278 | PASS |
| 16 | +0.965 | +1.193 | 0.934 | 0.175 | +0.175 | FAIL (DSR) |

## Breadth (majors-only @2bps)

| sleeve | Sharpe |
| --- | --- |
| combined | +0.619 |
| XS-solo | +0.293 |
| trend-solo | +0.658 |

On majors the roles **reverse** — trend is the stronger sleeve (+0.658) and XS is weak
(+0.293), and the combine (+0.619) again lands *between* them, below the best single sleeve,
and fails the gate (DSR 0.878, boot_lo −0.237). This is the same breadth-direction reversal
the XS verdict found (XS pays cross-sectionally on alt breadth; trend likes majors). The
combine never beats best-single in either universe — confirming the issue is relative Sharpe,
not the combine mechanics.

## What this delivers toward the end goal

- **A definitive deployment decision, de-biased:** XS-solo is the core; trend does not earn
  its risk allocation. That is the monetization-relevant answer, not a metric.
- **A correct, validated portfolio-construction layer** (causal IDM + governor, gate-clearing,
  diversification provably real) — the reusable socket the next sleeve plugs into.
- **A falsified optimism:** "two positive sleeves + low correlation ⇒ combine wins" is false
  when the diversifier is sub-bar. The binding constraint for a Sharpe-improving combine is a
  *second comparably-strong edge*, not a better weight vector or IDM estimator.

## Caveats carried forward

- **Survivorship** (pre-capital): unchanged from the XS verdict — the book is point-in-time
  correct via the NaN active-set demean; the magnitude check is a pre-real-money rigor audit,
  not a blocker.
- **Replay-only:** this is read-only replay over `analytics.db`, not live capital; execution
  realism at size is still ahead.
- **Trend is demoted, not killed:** it remains a real cost-robust positive and the stronger
  sleeve on majors; revisit when a comparably-strong third sleeve exists or if the objective
  shifts from Sharpe to drawdown.
