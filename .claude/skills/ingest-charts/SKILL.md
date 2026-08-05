---
name: ingest-charts
description: >
  Ingest Coinglass / MMT heatmap + liquidation-map screenshots (dropped by
  hand, or captured by tools/coinglass_capture.sh) from
  docs/plans/chart-drops/ into verified external-context
  JSON for the daily Brief (M3). Scans via tools/chart_drops.py (sha256
  dedup ledger), vision-extracts each image in a per-image sonnet subagent
  (image bytes never enter main context), presents ONE consolidated review
  digest for the whole batch, and writes docs/plans/external-context/*.json
  ONLY after the operator approves — verified:true is the only on-disk
  state. Invoke when the user says "/ingest-charts", "ingest my chart
  drops", or has dropped new heatmap screenshots. Spec:
  docs/superpowers/specs/2026-07-14-m3-external-context-design.md.
---

# /ingest-charts — chart drops → verified external-context JSON

Advisory data path only. Never write a stream file before the operator
approves. Never `git add` anything here — `docs/plans/` and `.cache/` are
gitignored.

**Drops arrive two ways, and the split matters.** `tools/coinglass_capture.sh`
captures the **Liquidation Heatmap 24h** row (BTC/ETH/SOL) unattended; the
**Liquidation Map 1d** row is still hand-captured (ST15 is half done — the map
route 404s on `?coin=` and walls even on BTC logged-out). So a scan that shows
only three heatmaps means the set is **incomplete, not ready**.

**Do not ingest a partial daily set.** The daily check asserts external-context
*recency*, so a single fresh drop turns that line green while every uncovered
panel silently goes stale — the coverage hazard filed against ST15. A complete
set is 6 panels: Heatmap 24h + Map 1d per symbol.

## 1. Scan

```bash
PYTHONPATH=. poetry run python tools/chart_drops.py scan
```

- `pending` empty and `unparseable` empty → report "no new drops" and stop.
- `unparseable` non-empty → list the names and ask the operator to rename to
  `<source>[-<venue>]_<SYMBOL>[_<YYYYMMDD[-HHMM]>].png|.jpg|.jpeg` (source ∈
  coinglass|mmt, MYT timestamp). `venue` is an optional dash-suffixed token
  on the source segment naming the specific exchange a panel's data comes
  from (e.g. Coinglass's per-exchange liq-map view); omit it when the panel
  is exchange-aggregated or the exchange is unknown. Do NOT guess. Continue
  with `pending`. Echo these copy-paste examples with the rename request:
  `coinglass_BTCUSDT_20260715-0930.jpeg` (heatmap) ·
  `coinglass_BTCUSDT_20260715-0931.jpeg` (map — bump the minute so names
  differ) · `coinglass-hyperliquid_BTCUSDT_20260716-1040.png` (map scoped
  to the Hyperliquid venue) · `mmt_ETHUSDT.png` (no timestamp = file
  mtime). Panel type never goes in the name — the extraction detects
  heatmap vs map.

## 2. Extract (one sonnet subagent per pending image)

Dispatch each image to a **sonnet** subagent (Agent tool). The prompt is
self-contained — no repo/SoT reads. Template (fill `<path>`, `<source>`,
`<symbol>`):

```text
Read the image file at <path> (one Coinglass/MMT chart screenshot) and
return ONLY a JSON object — no prose, no markdown fence.

Extractable panel types (anything else => status "skip"):
- liq_heatmap: time×price heatmap; bright horizontal bands = liquidation
  clusters. Read band price ranges against the y-axis gridlines.
- book_heatmap: same shape but resting limit-order density.
- liq_map: bar chart with PRICE on the x-axis; bars = liquidation leverage
  at that price; usually prints "Current Price:<n>" as text.

Rules:
- Bands, not points: give price_lo/price_hi rounded to the visible axis
  granularity. Max 10 clusters, highest-confidence first. Skip faint noise.
- Prefer PRINTED TEXT over axis interpolation: "Current Price:" labels and
  tapped-band tooltips are high-confidence; gridline reads are approximate.
- intensity: high = brightest/tallest, med = clear, low = faint but real.
- liq_map: cluster the tallest bar groups per side of current price; when
  one leverage tier dominates a cluster, note it in label (e.g.
  "100x-heavy").
- window: the selected timeframe button if visible ("12h","24h","48h",
  "3d","1w","1d",...), else null.
- scope: "pair" for a single-exchange pair view (e.g. Binance BTCUSDT),
  "agg" for aggregated Symbol/Exchange views, else null.
- Filename metadata is authoritative: source=<source>, symbol=<symbol>.
  If the chart clearly shows a DIFFERENT asset, set symbol_mismatch=true.

Return exactly:
{"status":"ok"|"skip","skip_reason":null|"<why>",
 "panel":"liq_heatmap"|"book_heatmap"|"liq_map"|null,
 "window":"24h"|null,"scope":"pair"|"agg"|null,
 "symbol_mismatch":false,"spot_price_hint":62452.0|null,
 "spot_source":"printed"|"axis"|null,
 "clusters":[{"price_lo":63200,"price_hi":63400,"kind":"liq"|"book",
              "intensity":"high"|"med"|"low","label":""}],
 "confidence":"high"|"med"|"low"}
```

## 3. Review digest (ONE for the whole batch — the human-verify gate)

Present a table per image: file · source · symbol · panel · window · scope ·
capture age · spot hint (+source) · clusters (band, kind, intensity, label)
· flags (symbol_mismatch, skip_reason, low confidence, spot hint far from
recent price). Ask the operator per image: **approve / correct / drop**.
Corrections are applied to the cluster list / fields before writing and
summarized in the snapshot's `notes` field. NOTHING is written before this
gate.

## 4. Write (approved images only)

For each approved image build the final snapshot dict:

- `schema` "external-levels-v1"; `source`+`symbol` from the filename;
  `venue` from the filename's dash-suffixed source token via
  `PendingDrop.venue` (optional; null/absent = unspecified — most drops
  have no venue); `panel`/`window`/`scope`/`spot_price_hint`/`clusters`
  from the extraction after operator corrections; `captured_at_ms` from
  the scan output; `ingested_at_ms` = now (ms); `verified` true; `notes` =
  correction summary or "".

Save it to a scratchpad temp file, then:

```bash
PYTHONPATH=. poetry run python tools/chart_drops.py write \
  --json-file <tmp>.json --image <pending path>
```

For skipped panels (subagent status "skip") and operator-dropped images:

```bash
PYTHONPATH=. poetry run python tools/chart_drops.py mark \
  --image <pending path> --outcome skipped|dropped
```

Every outcome moves the image to `docs/plans/chart-drops/done/` and records
the sha256 in `.cache/chart-drops/processed.json` so re-runs are no-ops.

## 5. Report

Summarize: written snapshots (paths), skipped (reasons), dropped,
unparseable-awaiting-rename. Remind: the Brief picks these up on its next
run (48h freshness window); daily capture protocol = Heatmap 24h + Map 1d
per symbol, Model 1, consistent threshold (spec "Capture protocol"
section).
