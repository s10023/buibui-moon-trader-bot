# Audit index

**Generated — do not edit by hand.** Regenerate with `make docs-index`;
`make docs-index-check` (and `tests/test_docs_index.py`) fails when this file
drifts from the corpus.

**40 audits.** A verdict line is shown only where one could be read
out of a Verdict heading as prose — that is **23 of 40**.
An em dash means the doc states its verdict in a table, a blockquote or the body,
**not** that it lacks one; open the file. Nothing here is keyword-guessed.

| Date | Audit | Verdict (as written) | File |
| --- | --- | --- | --- |
| 2026-08-12 | Multi-regime detector validation — verdict: NO detectable regime dependence | Three of the four cells are powered nulls with tight bounds. The fourth is a nominal hit that does not survive multiplicity. | [2026-08-12-multi-regime-validation.md](2026-08-12-multi-regime-validation.md) |
| 2026-08-11 | Ensemble / confluence score — walk-forward | `passes_gate(DSR, PBO, boot_lo) -> False`. | [2026-08-11-ensemble-walkforward.md](2026-08-11-ensemble-walkforward.md) |
| 2026-08-11 | D1 — Spot-perp CVD divergence sleeve | Venue-split order flow, as constructed here, is not a second edge. It is also not the *wrong-signed* edge — see "Direction"… | [2026-08-11-d1-spot-perp-cvd.md](2026-08-11-d1-spot-perp-cvd.md) |
| 2026-08-06 | Spec reconcile — P3 cross-sectional momentum sleeve (the deploy core) | This is the third spec reconciled and the highest-stakes one: xsmom is the only sleeve carrying real capital (+1.375 Sharpe, DSR… | [2026-08-06-spec-reconcile-p3-xsmom.md](2026-08-06-spec-reconcile-p3-xsmom.md) |
| 2026-08-06 | Spec-vs-code reconcile — P2 EWMAC trend + P3 trend×XS combine | — | [2026-08-06-spec-reconcile-p2-ewmac-p3-combine.md](2026-08-06-spec-reconcile-p2-ewmac-p3-combine.md) |
| 2026-08-06 | P2 §6 per-regime attribution — EWMAC trend sleeve | The point estimates lean the way theory predicts, and every one of them dissolves once the cross-section's correlation is… | [2026-08-06-p2-ewmac-regime-attribution.md](2026-08-06-p2-ewmac-regime-attribution.md) |
| 2026-08-04 | H15 — USD/JPY Carry-Unwind State Tag | Every cell in every one of the three panels — the primary forward panel and the two secondary ledger panels — lands NO-EDGE or… | [2026-08-04-h15-usdjpy-carry-unwind.md](2026-08-04-h15-usdjpy-carry-unwind.md) |
| 2026-08-04 | H14 — Coinbase-Premium Market-State Tag | Per spec §8 this is branch 2, applied verbatim: | [2026-08-04-h14-coinbase-premium-state-tag.md](2026-08-04-h14-coinbase-premium-state-tag.md) |
| 2026-07-25 | P3 Cross-Sectional Reversal Sleeve | REDUNDANT / NO-EDGE. Short-horizon (2-7 day) cross-sectional reversal | [2026-07-25-p3-xs-reversal-sleeve.md](2026-07-25-p3-xs-reversal-sleeve.md) |
| 2026-07-24 | H8 — M1 Indicator-State Conditioning Audit | `bar = ±0.05R`, `alpha = 0.05`, `min_n = 30`, `n_boot = 2000`, seed 12345. Run one tier at a time (`--timeframes`): pooling tiers… | [2026-07-24-h8-m1-indicator-conditioning.md](2026-07-24-h8-m1-indicator-conditioning.md) |
| 2026-07-23 | H10 — Partial-path predictiveness | NO-EDGE across all 15 gated cells → ST6 and ST7 close (spec §8). | [2026-07-23-h10-partial-path-predictiveness.md](2026-07-23-h10-partial-path-predictiveness.md) |
| 2026-07-21 | ST9 / H11 — SL-horizon audit | — | [2026-07-21-st9-sl-horizon.md](2026-07-21-st9-sl-horizon.md) |
| 2026-07-17 | H9 warning-value audit | — | [2026-07-17-h9-warning-value.md](2026-07-17-h9-warning-value.md) |
| 2026-06-26 | Structural level-hold touch-decay kill-test | First-touch beats repeat-touch (time-split-robust, Holm, CI>bar) on: bos/short, eqh_eql/short, fvg/long → escalate to the… | [2026-06-26-structural-level-hold-touch-decay.md](2026-06-26-structural-level-hold-touch-decay.md) |
| 2026-06-26 | Faithful per-strategy structural entry-sim harness | BUILD holds independently on every requested timeframe (1d) — see the per-tf breakdown below for each tf's own cells. | [2026-06-26-structural-entry-sim-harness.md](2026-06-26-structural-entry-sim-harness.md) |
| 2026-06-24 | Reference-level proximity audit | Live near-level cohort does not clear the de-biased gate — do not build. | [2026-06-24-reference-level-proximity.md](2026-06-24-reference-level-proximity.md) |
| 2026-06-24 | P3 XS-momentum — survivorship magnitude check | Including the genuine top-volume dead crypto perps that would actually have belonged in a point-in-time universe — LUNA (the… | [2026-06-24-p3-xsmom-survivorship.md](2026-06-24-p3-xsmom-survivorship.md) |
| 2026-06-20 | P3 — XS-momentum execution-realism capacity stress test | — | [2026-06-20-p3-xsmom-capacity.md](2026-06-20-p3-xsmom-capacity.md) |
| 2026-06-19 | P3 Carry sleeve — funding-carry G-gate | FAIL the de-biased gate. No standalone edge — carry is essentially flat. | [2026-06-19-p3-carry-sleeve.md](2026-06-19-p3-carry-sleeve.md) |
| 2026-06-18 | P3 trend×XS combine — clears the gate, but Sharpe-dominated by XS-solo | The combined book passes the de-biased gate (DSR 0.966 ∧ PBO 0.206 ∧ boot_lo +0.357 over {trend, XS, combined}) and the… | [2026-06-18-p3-trend-xs-combine.md](2026-06-18-p3-trend-xs-combine.md) |
| 2026-06-17 | P3 XS-momentum — beta-neutral + forward-persistence re-test | — | [2026-06-17-p3-xsmom-beta-neutral-persistence.md](2026-06-17-p3-xsmom-beta-neutral-persistence.md) |
| 2026-06-16 | P3 — Cross-Sectional Momentum Sleeve vs Gate G3 | The commit gate is DSR ≥ 0.95 ∧ PBO ≤ 0.5 ∧ boot_lo > 0 (the same bar the forecast-weight study failed). The universe XS sleeve… | [2026-06-16-p3-xsmom-sleeve.md](2026-06-16-p3-xsmom-sleeve.md) |
| 2026-06-16 | P2 EWMAC trend sleeve — forecast-weight study | Three things matter more than the binary FAIL: | [2026-06-16-p2-forecast-weight-study.md](2026-06-16-p2-forecast-weight-study.md) |
| 2026-06-15 | P2 — EWMAC Trend Sleeve A/B vs Gate G2 | The gate is trend-sleeve OOS Sharpe ≥ ~1 on the universe, costs in, DSR/PBO-gated. The universe book scores +0.36 — clearly… | [2026-06-15-p2-ewmac-trend-g2.md](2026-06-15-p2-ewmac-trend-g2.md) |
| 2026-06-15 | MFE timing within the hold window — exit sub-project B, step 1 | Next: build the pluggable exit-replay (#1 time-stop + #2 BE + #6 partial-at-1R vs policy #0), feed `(new_R, new_exit_ts)` into… | [2026-06-15-mfe-timing.md](2026-06-15-mfe-timing.md) |
| 2026-06-15 | Exit-policy A/B v1 — fixed (#0) vs composite — sub-project B | — | [2026-06-15-exit-policy-ab-v1.md](2026-06-15-exit-policy-ab-v1.md) |
| 2026-06-14 | P1 Paper-Portfolio Baseline — first risk-adjusted numbers | — | [2026-06-14-p1-portfolio-baseline.md](2026-06-14-p1-portfolio-baseline.md) |
| 2026-06-12 | N3 universe + deep-history backfill — coverage audit | — | [2026-06-12-universe-backfill-coverage.md](2026-06-12-universe-backfill-coverage.md) |
| 2026-06-11 | N2 — MFE/MAE diagnostic: exit-fixable vs entry-broken (exit spec §2) | This is not a tp_r re-sweep of the TA book (stop-doing list holds): no detector or TOML changes; the next step is the §4… | [2026-06-11-mfe-mae-diagnostic.md](2026-06-11-mfe-mae-diagnostic.md) |
| 2026-06-04 | Prune-to-positive-core — review draft + the combo-rescue question | — | [2026-06-04-prune-to-core-draft.md](2026-06-04-prune-to-core-draft.md) |
| 2026-06-04 | Conditional-Edge Test — does "location/context" rescue the losing strategies? | The tool's built-in verdict returned KEEP for 18/19 strategies (only `wick_fill` DEMOTE). This is misleading by construction: the… | [2026-06-04-conditional-edge-test.md](2026-06-04-conditional-edge-test.md) |
| 2026-06-03 | Direction-Axis Hard-Flip Decision Doc — F8 `suppress_directions` + bos long-suppress | — | [2026-06-03-direction-axis-hard-flip.md](2026-06-03-direction-axis-hard-flip.md) |
| 2026-05-21 | T6 Phase A — `volume_suppress` mon_fri + weekend Audit Findings | — | [2026-05-21-volume-suppress-mon-fri-weekend.md](2026-05-21-volume-suppress-mon-fri-weekend.md) |
| 2026-05-21 | T6 Phase A — `adr_exempt` mon_fri + weekend Audit Findings | — | [2026-05-21-adr-exempt-mon-fri-weekend.md](2026-05-21-adr-exempt-mon-fri-weekend.md) |
| 2026-05-20 | `volume_spike_boost` — Structural Inertness Finding | PR #381 reported `engulfing 15m long DISABLE` at n_supp=38, supp_avg_r=+0.27R, and `15m short DISABLE` at n_supp=32,… | [2026-05-20-volume-spike-boost-structural-inertness.md](2026-05-20-volume-spike-boost-structural-inertness.md) |
| 2026-05-18 | Bucket C TOML Decisions — Per-tf-direction Encoding | — | [2026-05-18-bucket-c-toml.md](2026-05-18-bucket-c-toml.md) |
| 2026-05-18 | Bucket C Dying-Cell Directional Cuts | — | [2026-05-18-bucket-c-dying-cells.md](2026-05-18-bucket-c-dying-cells.md) |
| 2026-05-17 | T6 Phase A — `volume_spike_boost` Audit Findings | — | [2026-05-17-volume-spike-boost.md](2026-05-17-volume-spike-boost.md) |
| 2026-05-17 | T6 Phase A — `adr_suppress_threshold` Audit Findings | — | [2026-05-17-adr-suppress-threshold.md](2026-05-17-adr-suppress-threshold.md) |
| 2026-05-17 | T6 Phase A — `adr_exempt` Audit Findings | — | [2026-05-17-adr-exempt.md](2026-05-17-adr-exempt.md) |
