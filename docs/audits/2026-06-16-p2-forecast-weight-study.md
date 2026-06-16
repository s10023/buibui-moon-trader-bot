# P2 EWMAC trend sleeve — forecast-weight study (verdict)

**Date:** 2026-06-16
**Tool:** `make buibui-forecast-weight-study` (`tools/forecast_audit.py --weight-study`)
**Read-only** over `analytics.db` (N3 universe, 1d). Spec:
`docs/superpowers/specs/2026-06-16-p2-forecast-weight-study-design.md`.
Predecessor: `docs/audits/2026-06-15-p2-ewmac-trend-g2.md`.

## Question

G2 found the equal-weight combine sub-bar (universe Sharpe +0.36) while the fast
single speed s8/32 alone was +0.83. Does re-weighting the four EWMAC speeds lift
the *universe-combined* Sharpe toward the fast level **and survive DSR/PBO** over
an enlarged, honestly-labelled trial family?

Decision rule (mirrors `analytics/sweep_guard.py`): a scheme clears iff
`DSR ≥ 0.95 ∧ PBO ≤ 0.5 ∧ boot_lo > 0`. DSR deflates over the wider family
`{6 schemes} ∪ {4 per-speed singles}`; PBO/CSCV competes among the 6 schemes only
(putting the dead singles in the PBO matrix would inflate apparent robustness).

## Result

| scheme | a_priori | sharpe | dsr | pbo | boot_lo | min_trl | clears |
| --- | --- | --- | --- | --- | --- | --- | --- |
| fast_only | no | +0.822 | 0.834 | 0.000 | +0.125 | ∞ | no |
| drop_two_slowest | no | +0.641 | 0.692 | 0.000 | −0.047 | ∞ | no |
| fast_tilt_geom | no | +0.582 | 0.636 | 0.000 | −0.112 | ∞ | no |
| fast_tilt_linear | no | +0.503 | 0.557 | 0.000 | −0.205 | ∞ | no |
| equal | yes | +0.359 | 0.409 | 0.000 | −0.370 | ∞ | no |
| inverse_cost | yes | +0.146 | 0.216 | 0.000 | −0.622 | ∞ | no |

Sanity: `equal` = +0.359 reproduces the G2 universe headline (+0.36) — the
weighted-combine path is byte-faithful when weights are uniform; `fast_only`
= +0.822 reproduces the G2 single-speed s8/32 (+0.83).

## Verdict — FAIL on the gate; equal-weight stays

**No scheme clears `DSR ≥ 0.95 ∧ PBO ≤ 0.5 ∧ boot_lo > 0`.** The forecast-weight
lever does **not**, on its own, carry the universe-combined book across the G2
bar. Equal-weight remains the shippable default; any fast tilt is, on this data,
in-sample. This is consistent with G2's "promising, sub-bar" — re-weighting is
not the missing piece.

Three things matter more than the binary FAIL:

1. **The fast tilt is a real, monotone, structurally stable signal — not a
   fluke.** Sharpe rises strictly with the fast tilt (fast_only > drop_two_slowest
   > fast_tilt_geom > fast_tilt_linear > equal > inverse_cost), and **PBO = 0.000
   for every scheme** — the in-sample ranking is perfectly preserved across all
   CSCV train/test splits. "Fast trend beats slow trend, slow legs are drag" is
   robust across sub-periods. This *strengthens* the G2 finding rather than
   contradicting it.

2. **`fast_only` is close but does not clear — and it fails on DSR, not PBO or
   the CI.** It is the **only** scheme whose 95% bootstrap CI excludes zero
   (boot_lo = +0.125), and its PBO is 0. It fails solely because **DSR = 0.834 <
   0.95**: deflating a +0.82 Sharpe against a 10-trial search family is just shy
   of the bar. Close enough to say the edge is real; short enough to say we have
   not *proven* it out-of-sample at the 0.95 confidence we hold ourselves to.

3. **No look-ahead-free scheme helps.** Both a-priori schemes are weak — `equal`
   (0.41) and `inverse_cost` (0.22, it tilts toward the dead slow legs). By the
   spec's design there is no a-priori, non-snooped way to justify a fast tilt on
   this data without correlation estimation (which would reintroduce look-ahead).
   So the lift exists only in the data-snooped schemes — which is exactly why the
   DSR haircut is the honest arbiter, and why we do **not** ship a fast-tilt now.

## Recommendation

- **Ship nothing from this study.** Keep equal-weight as the default combine
  (the `weights` knob is now available but stays `None`).
- **Do not ship a data-snooped `fast_only`/fast-tilt sleeve** on the strength of
  these numbers — the lift is real but unconfirmed at our DSR bar. The correct way
  to promote it is an **out-of-sample / forward** confirmation of fast-over-slow,
  not an in-sample re-weight.
- **The binding constraint is still breadth, expressed cross-sectionally (P3),
  not the time-series weight vector.** G2 already showed majors (0.65) > universe
  (0.36) on Sharpe while breadth halved max-DD; this study shows the universe
  time-series combine cannot be re-weighted across the bar. Both point to **P3
  cross-sectional momentum + a correlation/IDM portfolio layer** as the next
  sub-project.

## Next lever

P3 cross-sectional (relative-strength) momentum + IDM/correlation weighting —
the P2 portfolio vol governor only approximates the diversification a real
correlation layer would capture. The trend sleeve stays a **positive,
cost-robust core candidate**, demoted "promising, sub-bar", not killed; fast
trend is where its edge lives.
