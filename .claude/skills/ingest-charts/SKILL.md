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
  `coinglass_BTCUSDT_20260715-0930.jpeg` ·
  `coinglass_BTCUSDT_20260715-0931.jpeg` (bump the minute so two panels of
  the same symbol get distinct names — the order carries NO panel meaning,
  and the operator's real captures have run map-then-heatmap, the reverse
  of any order you might read into this pair) ·
  `coinglass-hyperliquid_BTCUSDT_20260716-1040.png` (map scoped
  to the Hyperliquid venue) · `mmt_ETHUSDT.png` (no timestamp = file
  mtime). Panel type never goes in the name — the extraction detects
  heatmap vs map.

### Provenance — a drop is not assumed to be the operator's own screenshot

This skill was written around "the operator screenshotted his own Coinglass
session", and every other source read as second-hand by default. That is wrong
in one common direction:

- **A non-screenshot source is legitimate when it is FIRST-PARTY** — an image
  posted by Coinglass or MMT themselves (e.g. the `@coinglass_com` daily
  heatmap that ST15 automates) is the *same* publisher as the operator's own
  screen, just delivered differently. Treat it as a provenance step **up** from
  a relayed or re-hosted image, not down. Second-hand means *someone else's
  screenshot of* a panel, and that is what deserves suspicion.
- **Record unusual provenance in the snapshot's `notes`.** It survives into the
  Brief's External block, so a later reader can weigh a level whose origin was
  not the usual capture path. Do not put it in `source` — that field is the
  publisher (`coinglass`|`mmt`) and is parsed from the filename.

### Say which panels the batch covers — including OVER-coverage

After the scan, state the pending set against the 6-panel daily protocol
(Heatmap 24h + Map 1d for each of BTC/ETH/SOL): which are covered, which are
missing. **Report over-coverage as readily as under-coverage.** The 2026-08-04L
batch was 15 panels against a protocol of 6, and nothing said so — extra panels
are not free, since each costs a vision dispatch (45K–103K tokens) and a 1w
heatmap band is structural context, not a same-day actionable level.

This matters because the daily check asserts **recency, not coverage**: one
fresh drop greens the line while five panels rot. Naming the gap here is the
only thing standing in for the check ST15 still owes.

## 2. Extract (one sonnet subagent per pending image)

Dispatch each image to the **`chart-extract`** subagent (Agent tool,
`subagent_type: "chart-extract"`, defined at `.claude/agents/chart-extract.md`).
The prompt is self-contained — no repo/SoT reads. Template (fill `<path>`,
`<source>`, `<symbol>`):

**Naming the agent type is half the fix.** This line said only "a **sonnet**
subagent" until 2026-08-07, naming no type at all — so the costliest parameter of
the dispatch was left to whoever happened to run the skill, and the practical
default was a general-purpose agent at a measured **~49K tokens per image**
against a ~1.4–2.1K image. `chart-extract` pins `model: sonnet` and
`tools: Read`, which removes ~20 tool schemas and structurally prevents the
file-writing an extraction agent did on an earlier run.

**If `chart-extract` does not resolve** (fresh clone before the `.gitignore`
re-include propagates, or a harness without project agents), fall back to a
general-purpose sonnet agent **and say so prominently in the review digest** —
name the fallback and the cost. **Never fall back silently:** the output is
byte-identical either way, so an unannounced fallback restores the full cost
while looking exactly like success. Do NOT substitute the stock `Explore` agent,
and do NOT batch several images into one agent.

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

### Verifying the spot hint — that flag has no procedure without this

The flag above names a comparison the skill never told you how to make, which
left it decorative. It is not decorative: on 2026-08-07 it caught a BTC heatmap
whose axis-read spot was **+1.3% wrong** (65,600 against a live 64,781). Two
checks, free one first.

**Check 1 — same-symbol cross-panel agreement. No network.** The daily protocol
gives every symbol BOTH panels, and they read spot by different means: a
`liq_map` usually prints it (`spot_source: "printed"`, high confidence) while a
`liq_heatmap` interpolates off the axis (`spot_source: "axis"`, approximate).
Disagreement is self-diagnosing and the `axis` side is the suspect one. In that
08-07 batch BTC was 1.3% apart while ETH was 0.05% and SOL 0.2% — **the outlier
identified itself before anything external was fetched.**

**Check 2 — confirm against live price**, which settles who was right:

```bash
curl -s "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT"
```

A printed hint should sit within the capture lag (~0.2% over 20 min). Treat >1%
on an `axis` read as wrong until shown otherwise.

**When a hint IS wrong, do NOT reflexively drop the panel — check whether the
CLUSTERS moved with it.** That is the whole decision, and it goes both ways:

- Bands shifted by roughly the same error ⇒ the axis mapping itself is off and
  every price in the panel is contaminated. Drop it.
- Bands still agree with the other panel's ⇒ the hint is an isolated
  axis-label slip. **Keep the clusters, null the hint**, and say so in `notes`.

08-07's BTC heatmap was the second case: its bands sat ~150pts from the map's,
not ~820. Dropping it would have discarded 8 good clusters over one bad number.
Null `spot_price_hint` **and** `spot_source` rather than substituting live
price — the snapshot records what the panel showed, not what was true.

**Do not expect the loader to catch this for you.** `analytics/brief/external.py`
has a `spot_hint_deviation` guard, but `_SPOT_DEVIATION_FRAC = 0.10` — it fires
only past **10%**, which is a wrong-*symbol* detector (ETH prices on a BTC
panel), not a wrong-*axis-read* one. The 08-07 error was 1.3% and would have
passed it silently in either direction. **The cross-panel check above is the only
thing that catches this error class**, which is why it is a step and not a nicety.
Note also that nulling a hint sets `hint is not None` false and therefore
suppresses that guard entirely — harmless at 1.3%, but say what you nulled and
why in `notes`, because after the write the prose is the only surviving record
that the panel was ever suspect.

## 4. Write (approved images only)

For each approved image build the final snapshot dict:

- `schema` "external-levels-v1"; `source`+`symbol` from the filename;
  `venue` from the filename's dash-suffixed source token via
  `PendingDrop.venue` (optional; null/absent = unspecified — most drops
  have no venue); `panel`/`window`/`scope`/`spot_price_hint`/`spot_source`/
  `clusters` from the extraction after operator corrections;
  `captured_at_ms` from the scan output; `ingested_at_ms` = now (ms);
  `verified` true; `notes` = correction summary or "".

  **`spot_source` is optional and carries the extractor's `"printed"` /
  `"axis"` / `null` verbatim — do not invent it and do not drop it.** Step 2
  asked for this field from the beginning, but until 2026-08-07
  `validate_snapshot_dict` rejected unknown top-level keys outright, so it had
  nowhere to land and every write silently discarded it. It is now an
  `_OPTIONAL_KEYS` member alongside `venue`. **Any other field step 2 returns
  that is not named here is still rejected** — `status`, `skip_reason`,
  `symbol_mismatch` and `confidence` are review-gate signals, not snapshot
  content, so they stay out of the dict.

  **Same trap one level down:** cluster dicts are symmetric-difference checked
  against exactly `{price_lo, price_hi, kind, intensity, label}`, so an extra
  key on a cluster fails the whole snapshot rather than being ignored.

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
