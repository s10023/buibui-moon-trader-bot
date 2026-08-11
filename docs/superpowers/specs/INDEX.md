# Spec index

**Generated — do not edit by hand.** Regenerate with `make docs-index`;
`make docs-index-check` (and `tests/test_docs_index.py`) fails when this file
drifts from the corpus.

**50 specs on disk.** Reconcile status is **derived** — a spec counts as
reconciled when an audit that names its filename also discusses reconciling, so
the count is recomputed from disk rather than carried in prose. That is the
counter CLAUDE.md has had wrong every time it was checked.

- **Reconciled (derived): 3 of 50.**
- Referenced by no audit at all: 36.

**Two caveats before quoting these numbers.** A *partial* reconcile is
indistinguishable from a whole-spec one here, and this table cannot see one that
was done but never written down in an audit. Treat it as the list of what exists,
and the reconcile column as a floor.

| Date | Spec | Reconciled by | Also referenced by | File |
| --- | --- | --- | --- | --- |
| 2026-08-11 | D1 — Spot-perp CVD divergence sleeve (design + pre-committed gate) | — | 2026-08-11-d1-spot-perp-cvd.md | [2026-08-11-d1-spot-perp-cvd-design.md](2026-08-11-d1-spot-perp-cvd-design.md) |
| 2026-08-10 | X thread walk — recover a self-thread from its tail | — | — | [2026-08-10-x-thread-walk-design.md](2026-08-10-x-thread-walk-design.md) |
| 2026-08-07 | 7j — a dedicated `chart-extract` subagent for `/ingest-charts` | — | — | [2026-08-07-7j-chart-extract-agent-design.md](2026-08-07-7j-chart-extract-agent-design.md) |
| 2026-08-06 | Design: `/card` capital resolution (+ the SL-vs-invalidation check, dropped) | — | — | [2026-08-06-card-capital-and-invalidation-design.md](2026-08-06-card-capital-and-invalidation-design.md) |
| 2026-08-05 | Relay attribution — routing second-hand calls to their originating author (design) | — | — | [2026-08-05-relay-attribution-design.md](2026-08-05-relay-attribution-design.md) |
| 2026-08-04 | H15 — USD/JPY carry-unwind state tag (design + pre-committed gate) | — | 2026-08-04-h15-usdjpy-carry-unwind.md | [2026-08-04-h15-usdjpy-carry-unwind-design.md](2026-08-04-h15-usdjpy-carry-unwind-design.md) |
| 2026-08-04 | H14 — Coinbase-premium market-state tag (design + pre-committed gate) | — | 2026-08-04-h14-coinbase-premium-state-tag.md | [2026-08-04-h14-coinbase-premium-state-tag-design.md](2026-08-04-h14-coinbase-premium-state-tag-design.md) |
| 2026-07-31 | ST10 — YouTube channel auto-feed (`/ingest-feed`) design | — | — | [2026-07-31-st10-youtube-feed-design.md](2026-07-31-st10-youtube-feed-design.md) |
| 2026-07-31 | B2 — routing dedup for the ingest sinks (design) | — | — | [2026-07-31-b2-routing-dedup-design.md](2026-07-31-b2-routing-dedup-design.md) |
| 2026-07-28 | `/ingest-video` — video → research pipeline (design) | — | — | [2026-07-28-ingest-video-design.md](2026-07-28-ingest-video-design.md) |
| 2026-07-24 | P3 Cross-Sectional Short-Horizon Reversal Sleeve — Design | — | 2026-07-25-p3-xs-reversal-sleeve.md | [2026-07-24-p3-xs-reversal-sleeve-design.md](2026-07-24-p3-xs-reversal-sleeve-design.md) |
| 2026-07-24 | H8 — M1 indicator-state conditioning audit (design) | — | — | [2026-07-24-h8-m1-indicator-conditioning-design.md](2026-07-24-h8-m1-indicator-conditioning-design.md) |
| 2026-07-23 | H10 — Partial-path predictiveness (design) | — | 2026-07-23-h10-partial-path-predictiveness.md | [2026-07-23-h10-partial-path-predictiveness-design.md](2026-07-23-h10-partial-path-predictiveness-design.md) |
| 2026-07-21 | Weekly Path Cone + Monthly Context — design | — | — | [2026-07-21-weekly-path-cone-design.md](2026-07-21-weekly-path-cone-design.md) |
| 2026-07-21 | ST9 / H11 — SL-horizon audit (design) | — | — | [2026-07-21-st9-sl-horizon-audit-design.md](2026-07-21-st9-sl-horizon-audit-design.md) |
| 2026-07-20 | Stats UX polish — live-outcomes honesty + path-cone readout | — | — | [2026-07-20-stats-ux-polish-design.md](2026-07-20-stats-ux-polish-design.md) |
| 2026-07-19 | M5 Stats Rework — Live Alert Outcomes UX (design) | — | — | [2026-07-19-m5-live-outcomes-ux-design.md](2026-07-19-m5-live-outcomes-ux-design.md) |
| 2026-07-18 | M5 Stats Rework — Daily Price-Distribution Cone (design) | — | — | [2026-07-18-m5-price-distribution-cone-design.md](2026-07-18-m5-price-distribution-cone-design.md) |
| 2026-07-15 | M4 Integration — card-v2 rubric + /humanizer copy sweep (design) | — | — | [2026-07-15-m4-integration-design.md](2026-07-15-m4-integration-design.md) |
| 2026-07-14 | Brief-v2 M3 — External Context (chart-drop ingester) — Design | — | — | [2026-07-14-m3-external-context-design.md](2026-07-14-m3-external-context-design.md) |
| 2026-07-13 | F2 fix — pundit board must not cite the AI card as an external pundit | — | — | [2026-07-13-f2-card-pundit-self-citation-fix-design.md](2026-07-13-f2-card-pundit-self-citation-fix-design.md) |
| 2026-07-10 | M2 Session Layer — Design | — | — | [2026-07-10-m2-session-layer-design.md](2026-07-10-m2-session-layer-design.md) |
| 2026-07-09 | M1 Indicator-State Layer — Design | — | — | [2026-07-09-m1-indicator-state-design.md](2026-07-09-m1-indicator-state-design.md) |
| 2026-07-08 | M0 Brief Fixes — sweep-flag correctness, fresh as-of price, legend | — | — | [2026-07-08-m0-brief-fixes-design.md](2026-07-08-m0-brief-fixes-design.md) |
| 2026-07-08 | F2 AI Trade Card v1 — Design | — | — | [2026-07-08-f2-trade-card-design.md](2026-07-08-f2-trade-card-design.md) |
| 2026-07-04 | Pundit-Ledger Scorer — Design | — | — | [2026-07-04-pundit-ledger-scorer-design.md](2026-07-04-pundit-ledger-scorer-design.md) |
| 2026-07-04 | Daily Market Brief — Design | — | — | [2026-07-04-daily-market-brief-design.md](2026-07-04-daily-market-brief-design.md) |
| 2026-06-30 | X-post ingest — iteration 1 (design) | — | — | [2026-06-30-x-post-ingest-design.md](2026-06-30-x-post-ingest-design.md) |
| 2026-06-26 | Faithful per-strategy structural entry-sim harness — design | — | — | [2026-06-26-structural-entry-sim-harness-design.md](2026-06-26-structural-entry-sim-harness-design.md) |
| 2026-06-25 | 24/7 VPS deployment — signal-watch + XS executor (design) | — | — | [2026-06-25-vps-deployment-design.md](2026-06-25-vps-deployment-design.md) |
| 2026-06-23 | XS executor output polish — design | — | — | [2026-06-23-xsmom-executor-output-polish-design.md](2026-06-23-xsmom-executor-output-polish-design.md) |
| 2026-06-22 | XS-solo daily-workflow integration + overlay hardening — design | — | — | [2026-06-22-xsmom-daily-workflow-overlay-hardening-design.md](2026-06-22-xsmom-daily-workflow-overlay-hardening-design.md) |
| 2026-06-21 | XS-solo Order Routing + Risk Overlay — Design Spec | — | — | [2026-06-21-p3-xsmom-order-routing-overlay-design.md](2026-06-21-p3-xsmom-order-routing-overlay-design.md) |
| 2026-06-21 | Spec — API-assisted `/journal-trade` v2 | — | — | [2026-06-21-journal-trade-api-assist-design.md](2026-06-21-journal-trade-api-assist-design.md) |
| 2026-06-20 | Research-ingestion knowledge pipeline (design) | — | — | [2026-06-20-research-ingestion-pipeline-design.md](2026-06-20-research-ingestion-pipeline-design.md) |
| 2026-06-20 | P3 XS-solo — read-only daily target-position generator (design) | — | — | [2026-06-20-p3-xsmom-live-target-generator-design.md](2026-06-20-p3-xsmom-live-target-generator-design.md) |
| 2026-06-20 | P3 — XS-momentum execution-realism capacity stress test | — | 2026-06-20-p3-xsmom-capacity.md | [2026-06-20-p3-xsmom-execution-capacity-design.md](2026-06-20-p3-xsmom-execution-capacity-design.md) |
| 2026-06-19 | P3 Carry sleeve — funding-carry as a vol-scaled forecast (design) | — | 2026-06-19-p3-carry-sleeve.md | [2026-06-19-p3-carry-sleeve-design.md](2026-06-19-p3-carry-sleeve-design.md) |
| 2026-06-18 | P3 trend×XS combine layer — IDM portfolio-construction design | 2026-08-06-spec-reconcile-p2-ewmac-p3-combine.md | 2026-06-18-p3-trend-xs-combine.md | [2026-06-18-p3-trend-xs-combine-design.md](2026-06-18-p3-trend-xs-combine-design.md) |
| 2026-06-17 | P3 XS-momentum — beta-neutralization + forward-persistence re-test | — | 2026-06-17-p3-xsmom-beta-neutral-persistence.md | [2026-06-17-p3-xsmom-beta-neutral-persistence-design.md](2026-06-17-p3-xsmom-beta-neutral-persistence-design.md) |
| 2026-06-16 | P3 — Cross-Sectional Momentum Sleeve (design) | 2026-08-06-spec-reconcile-p3-xsmom.md | 2026-06-16-p3-xsmom-sleeve.md | [2026-06-16-p3-cross-sectional-momentum-sleeve-design.md](2026-06-16-p3-cross-sectional-momentum-sleeve-design.md) |
| 2026-06-16 | P2 EWMAC trend sleeve — forecast-weight study (design) | — | 2026-06-16-p2-forecast-weight-study.md | [2026-06-16-p2-forecast-weight-study-design.md](2026-06-16-p2-forecast-weight-study-design.md) |
| 2026-06-15 | P2 — EWMAC Trend Sleeve → Gate G2 (design) | 2026-08-06-spec-reconcile-p2-ewmac-p3-combine.md | 2026-06-15-p2-ewmac-trend-g2.md | [2026-06-15-p2-ewmac-trend-sleeve-design.md](2026-06-15-p2-ewmac-trend-sleeve-design.md) |
| 2026-06-12 | N3 — Universe + Deep-History Backfill (design) | — | — | [2026-06-12-n3-universe-backfill-design.md](2026-06-12-n3-universe-backfill-design.md) |
| 2026-06-01 | Outcome-Ledger SL/TP Fallback — Design | — | — | [2026-06-01-outcome-ledger-sl-tp-fallback-design.md](2026-06-01-outcome-ledger-sl-tp-fallback-design.md) |
| 2026-06-01 | Asymmetric F8 HTF EMA gate — design spec | — | 2026-06-03-direction-axis-hard-flip.md | [2026-06-01-asymmetric-f8-htf-ema-gate-design.md](2026-06-01-asymmetric-f8-htf-ema-gate-design.md) |
| 2026-05-25 | Design: Scheduled signal-watch on GitHub Actions (OKX data source) | — | — | [2026-05-25-gh-actions-signal-watch-okx-design.md](2026-05-25-gh-actions-signal-watch-okx-design.md) |
| 2026-05-02 | EMA Strategy — Design Spec | — | 2026-05-20-volume-spike-boost-structural-inertness.md | [2026-05-02-ema-strategy-design.md](2026-05-02-ema-strategy-design.md) |
| 2026-04-10 | TradFi Equity Bot — Fork Design | — | — | [2026-04-10-tradfi-equity-fork-design.md](2026-04-10-tradfi-equity-fork-design.md) |
| 2026-04-10 | Pre-D10 Confluence Readiness — Design Spec | — | — | [2026-04-10-pre-d10-confluence-readiness-design.md](2026-04-10-pre-d10-confluence-readiness-design.md) |
