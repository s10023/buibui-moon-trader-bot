# Brief-v2 M3 — External Context (chart-drop ingester) — Design

Status: user-approved 2026-07-14 (brainstorm session).
Parent roadmap: Brief-v2 M0–M5 (memory `brief-v2-roadmap`, approved 2026-07-08).

## Goal (one line)

Let the operator drop manually captured Coinglass / Market Monkey Terminal
heatmap and liquidation-map screenshots into a directory and have their
liquidation / resting-order levels appear — vision-extracted, human-verified,
age-stamped — as an additive "External" block in the daily Brief.

## Background

- The liquidity-heatmap data gap (memory `liquidity-heatmap-gap`) is
  API-blocked: the full cross-exchange heatmap is a paid vendor product
  (Coinglass / Hyblock / TradingLite), and free primitives don't reconstruct
  it. The manual-screenshot loop is the only free-first path.
- The operator has free full access to Market Monkey Terminal (mmt.gg) and can
  screenshot Coinglass; capture stays 100% manual per the roadmap decision —
  NO browser automation unless the manual loop proves valuable.
- Vision reading of numbers from images is lossy and misses are **silent
  confabulations** (pxpipe eval, memory `external-repos-eval`). Every design
  choice below assumes extracted numbers are wrong until a human verifies them.
- `/ingest-x` (spec `2026-06-30-x-post-ingest-design.md`) established the
  pattern this reuses: vision in a sonnet subagent with a self-contained
  inline rubric (image bytes never enter main context), one consolidated
  review digest, nothing written to disk before operator approval.

## Decisions (settled in brainstorm, 2026-07-14)

| Decision | Choice | Why |
| --- | --- | --- |
| v1 extraction scope | **Level-shaped panels only: liquidation heatmap, liquidation map, orderbook heatmap** | Tight, verifiable rubrics; directly fills the liquidity-heatmap gap. Other panel types (OI/funding, annotated charts) are detected and skipped with a note |
| Brief surface | **Separate "External" sub-block** per symbol panel | Human-sourced approximate data stays visually distinct from the deterministic level gauge; degrades independently like Indicators/Sessions |
| Staleness | **48h cutoff, age always shown** | Covers a skipped day at daily cadence without showing dead levels; past cutoff the block drops and a health note asks for a re-drop |
| Architecture | **Skill + pure reader** (`/ingest-charts` + `analytics/brief/external.py`) | Mirrors `/ingest-x`; keeps the human-verify gate; the Brief path stays deterministic with no LLM anywhere in it |
| Level representation | **Price bands** (`price_lo`/`price_hi`), not points | Honest about vision's axis-reading precision; the confabulation defense starts in the schema |
| Side (above/below) | **Derived at brief time** from ref price, never stored | Ref price moves between capture and brief |
| Verification | **`verified: true` is the only on-disk state** | A JSON file exists only because the operator approved it in the digest; the Brief cannot consume unverified data |
| Sanity anchor | `spot_price_hint` (printed "Current Price:" / tooltip text when present, else axis read) | Cheap wrong-symbol / misread-axis detector at both digest and brief time; printed text is the high-confidence path |
| Dedup | sha256-of-image-bytes ledger | `tools/x_fetch.py` pattern; re-runs skip ingested images even if renamed; a corrected re-drop (new bytes) processes fresh |
| Liquidation Map in scope | New `liq_map` panel (screenshot review 2026-07-14) | Price is the x-axis with labeled gridlines and spot price is **printed as text** — easier, higher-confidence extraction than the heatmap |
| Snapshot key | Latest fresh per **(source, panel, window)** | The daily Heatmap-24h + Map-1d pair must coexist; window changes interpretation (24h magnets vs 1w shelves) |
| Capture protocol | Model 1 + consistent threshold, always; daily = Heatmap 24h + Map 1d | No ground truth across Coinglass models — day-over-day comparability beats model choice |

## Non-goals (v1)

- NO capture automation — no browser/screenshot tooling; the roadmap's
  recorded revive-triggers stand unchanged.
- NO vendor APIs (Coinglass / CoinAnk / Hyblock) — the data gap stays
  API-blocked; this is the manual loop only.
- NO extraction of annotated price charts, OI/funding panels, or any other
  non-level-shaped screenshot — detected and skipped with a reason.
- NO confluence marker against internal levels/zones (the "block + ★" render
  option) — revisit once the manual loop proves valuable.
- NO screenshot embedding/serving in the web UI (would need static serving of
  a gitignored dir) — the operator has the screenshot open while verifying.
- NO F2 trade-card wiring — M4 integration decides how the card consumes M3.
- X account-timeline ingestion stays DEFERRED (roadmap decision 2026-07-08).

## Capture protocol (Coinglass — reviewed against operator screenshots 2026-07-14)

Operator-facing rules; the mobile app is fine (it is what the operator uses),
and every selector referenced below is visible in its screenshots.

- **Model 1, always.** Coinglass models 1/2/3 have no public ground truth;
  day-over-day comparability (is that shelf still there / did it get eaten)
  beats model choice. Never switch.
- **Liquidity Threshold kept consistent** (operator default 0.90) — the
  slider changes which bands show at all; a moving threshold destroys
  comparability.
- **Daily set per symbol: Liquidation Heatmap 24h + Liquidation Map 1d.**
  The 24h heatmap carries the near-term magnets; the 1d map carries the
  leverage-tier structure AND prints the spot price as text (an exact
  `spot_price_hint` for free). Add a **Heatmap 1w** ad-hoc when
  swing-planning.
- **Pair mode** (e.g. Binance BTCUSDT) is the default — it matches the perp
  actually traded. Aggregated Symbol / Exchange views are allowed and
  recorded as `scope: "agg"`.
- **Optional precision boost:** tap the brightest band before screenshotting
  so its tooltip price is burned into the image — the rubric prefers printed
  text (tooltips, "Current Price:") over axis interpolation. Not required:
  bands absorb axis-reading error by design.

## Architecture

```text
operator screenshots (Coinglass web / MMT)
  │  manual save, prefixed filename
  ▼
docs/plans/chart-drops/                      (gitignored drop dir)
  │
  │  /ingest-charts  (skill)
  ├─(1) SCAN     tools/chart_drops.py: list images → parse filenames
  │              → sha256 dedup vs .cache/chart-drops/processed.json
  ├─(2) EXTRACT  one sonnet subagent per new image; self-contained inline
  │              rubric; returns SMALL JSON only (clusters + spot hint +
  │              panel type, or skip_reason). Image bytes stay in the
  │              subagent — never in main context.
  ├─(3) DIGEST   one consolidated review table for the whole batch
  │              (+ anomaly flags: filename↔vision symbol mismatch,
  │               spot-hint deviation). Operator approves / corrects /
  │               drops per image. NOTHING is written before approval.
  └─(4) WRITE    per approved image → docs/plans/external-context/*.json
                 (verified snapshot) + mark hash processed
                 + move image → docs/plans/chart-drops/done/
  ▼
docs/plans/external-context/                 (gitignored, verified JSON only)
  │
  │  buibui brief  (deterministic, no LLM)
  └─ analytics/brief/external.py: latest fresh snapshot per
     (symbol, source, panel, window)
     → SymbolPanel.external → render/API/UI + health notes
```

Ownership boundaries:

- `analytics/brief/external.py` — the **contract module**: schema dataclasses
  live in `analytics/brief/types.py`, parse/validate + loader live here.
  Pure, read-only, no LLM, no network.
- `tools/chart_drops.py` — the **writer-side helper** the skill shells out
  to: filename parsing, hash ledger, schema-validated JSON writing. Imports
  validation from `analytics.brief.external` so writer and reader share one
  contract. Testable without any vision.
- `.claude/skills/ingest-charts/SKILL.md` — orchestration only: scan →
  dispatch subagents → digest → write-on-approval. All parseable logic lives
  in `tools/chart_drops.py`.

## File contracts

### Drop dir: `docs/plans/chart-drops/`

Filename convention (authoritative for source + symbol; vision only
cross-checks):

```text
<source>_<SYMBOL>[_<YYYYMMDD[-HHMM]>].png|.jpg|.jpeg
coinglass_BTCUSDT_20260714-0930.png
mmt_ETHUSDT.png            ← capture time falls back to file mtime
```

- `source` — lowercase, must be in the configured `allowed_sources`
  (default `["coinglass", "mmt"]`).
- `SYMBOL` — uppercase Binance perp name (`BTCUSDT`).
- Timestamp — interpreted as **MYT (UTC+8)**, the operator's wall clock;
  stored as epoch ms. Absent → file mtime.
- Unparseable filenames are never guessed: the digest lists them and asks the
  operator to rename.
- After ingest (whatever the outcome), the image is moved to
  `docs/plans/chart-drops/done/` — the drop-dir root is always the pending
  inbox. The hash ledger, not the move, is what prevents re-processing.

### Processed ledger: `.cache/chart-drops/processed.json`

`{ "<sha256>": { "filename": ..., "ingested_at_ms": ...,
"outcome": "written" | "skipped" | "dropped" } }` — outcome recorded so a
deliberately dropped image isn't re-surfaced every run.

### Extracted snapshot: `docs/plans/external-context/<source>_<panel>_<SYMBOL>_<ts>.json`

(`panel` is in the output filename — a same-minute heatmap + map pair must
not collide. The **input** filename stays panel-free: vision classifies the
panel trivially.)

```json
{
  "schema": "external-levels-v1",
  "source": "coinglass",
  "symbol": "BTCUSDT",
  "panel": "liq_heatmap",
  "window": "24h",
  "scope": "pair",
  "captured_at_ms": 1789350600000,
  "ingested_at_ms": 1789352400000,
  "verified": true,
  "spot_price_hint": 63250.0,
  "clusters": [
    {
      "price_lo": 65800.0,
      "price_hi": 66200.0,
      "kind": "liq",
      "intensity": "high",
      "label": "long-liq shelf"
    }
  ],
  "notes": "operator corrected 66.2k→66.1k"
}
```

Enums: `panel ∈ {liq_heatmap, book_heatmap, liq_map}` · `kind ∈ {liq, book}`
· `intensity ∈ {high, med, low}` · `scope ∈ {pair, agg} | null`. `window` is
free text read off the visible timeframe selector ("24h", "1d", "1w"), `null`
if cropped/unreadable. `label`/`notes` free text, may be empty.
`spot_price_hint` may be `null` if the axis read failed (digest flags it).
Unknown top-level keys are rejected by the validator (schema version bumps
instead).

## The `/ingest-charts` skill

Subagent rubric rules (inline in the skill, self-contained — no SoT/memory
re-reads, per the subagent-token-efficiency rule; **sonnet** per the model
delegation policy):

- Extract only high-confidence bands; round prices to the chart's visible
  axis granularity; bands not points.
- Cap ~10 clusters per image — signal, not noise.
- Classify `liq_heatmap` / `book_heatmap` / `liq_map`; anything else → skip
  JSON with `skip_reason` (v1 scope).
- `liq_map` reading: tallest bar-cluster bands per side of the printed
  current-price marker; when one leverage tier dominates a cluster, say so in
  `label` ("100x-heavy").
- Prefer **printed text over axis interpolation** wherever both exist:
  "Current Price:" labels and tapped-band tooltips are high-confidence;
  gridline interpolation is band-precision only.
- Read the selected timeframe off the visible selector into `window`
  (`null` if cropped); Pair vs Symbol/Exchange view into `scope`.
- Read the current-price marker into `spot_price_hint`; `null`
  if unreadable.
- Cross-check the chart's symbol against the filename symbol; mismatch →
  flag in the returned JSON (does not block; the digest surfaces it).
- Return ONLY the small JSON — no prose.

Digest per image: symbol · source · capture age · spot hint · cluster table
(band, kind, intensity, label) · anomaly flags. Operator actions per image:
**approve** / **correct** (edits applied before writing, noted in `notes`) /
**drop** (recorded in the ledger, never re-surfaced). Batch-level: unparseable
filenames and skipped panels listed for information.

## Brief-side reader (`analytics/brief/external.py`)

- `load_external_state(dir_path, symbol, as_of_ms, atr14, ref_price, cfg)
  -> ExternalState | None` — scans the JSON dir; keeps the **latest fresh
  snapshot per (source, panel, window)** for the symbol (the daily
  Heatmap-24h + Map-1d pair coexists, as do Coinglass and MMT); ignores
  stale (> `max_age_hours` vs `as_of_ms` — `--as-of` replays stay
  deterministic), unverified (defensive; shouldn't exist on disk), and
  malformed files (→ health note naming the file). One snapshot failing
  never affects another.
- New frozen dataclasses in `analytics/brief/types.py` (additive, JSON-safe
  via the existing `asdict` serialisation):

```python
@dataclass(frozen=True)
class ExternalClusterRow:
    price_lo: float
    price_hi: float
    kind: str            # "liq" | "book"
    intensity: str       # "high" | "med" | "low"
    label: str
    dist_atr: float      # signed distance from ref to band midpoint, in ATR14

@dataclass(frozen=True)
class ExternalSnapshot:
    source: str
    panel: str
    window: str | None
    scope: str | None
    captured_at_ms: int
    age_hours: float
    spot_price_hint: float | None
    spot_hint_deviation: bool   # |hint − ref| / ref > 0.10
    clusters_above: list[ExternalClusterRow]   # nearest-first
    clusters_below: list[ExternalClusterRow]   # nearest-first

@dataclass(frozen=True)
class ExternalState:
    snapshots: list[ExternalSnapshot]
    notes: list[str]
```

- `SymbolPanel.external: ExternalState | None` — additive; `None` keeps every
  existing surface byte-identical.
- Side split: a cluster's side is decided by its band **midpoint** vs ref
  price; a band straddling ref lands on its midpoint's side with a near-zero
  `dist_atr`. `atr14 <= 0` (degenerate panel) → block omitted with a health
  note rather than rendering unnormalised distances.
- Spot-hint sanity check: deviation > 10% of ref price → snapshot still
  renders but carries a warning note ("spot hint deviates — check
  symbol/axis read"). Render-with-warning, not drop: the operator verified
  the clusters; the hint is an anchor, not a gate.

## Config

`[brief.external]` in the brief config TOML (all optional; defaults shown):

```toml
[brief.external]
dir = "docs/plans/external-context"
max_age_hours = 48
max_rows_per_side = 3
allowed_sources = ["coinglass", "mmt"]
```

`allowed_sources` is consumed by `tools/chart_drops.py` (filename gate) and
the loader (unknown-source files → health note, not a crash).

## Rendering

`_external_lines` in `analytics/brief/render.py`, after the Sessions block:

```text
External: coinglass liq (24h) · 14h · above 66.0k–66.2k HIGH (+1.8 ATR), 67.5k med (+3.9) · below 61.2k–61.5k HIGH (−1.6)
          coinglass map (1d) · 14h · above 66.1k HIGH 100x-heavy (+1.8) · below 60.5k–60.9k HIGH (−2.1)
          mmt book · 3h · above 65.0k med (+0.9) · below 62.8k HIGH (−0.7)
```

- One line per snapshot (source · panel · window), nearest-first per side,
  capped at `max_rows_per_side`.
- Age always shown; bands collapse to a single price when `lo == hi` after
  display rounding.
- Degradation contract (mirrors M1/M2): dir missing or no files for the
  symbol → block absent, silent (the feature is opt-in by usage); snapshots
  exist but all stale → health note "external context stale (latest Xd) —
  re-drop screenshots"; malformed file → health note naming it.
- API + UI: flows through `GET /api/brief` via the existing serialisation;
  `Brief.svelte` adds an External section following the Indicators/Sessions
  pattern (load `/frontend-design` at implementation time).

## Error handling

- Per-file isolation everywhere: one bad drop/snapshot never kills the batch
  or the panel; failures become digest flags (ingest side) or health notes
  (brief side).
- The skill writes nothing before operator approval; interrupted runs leave
  the ledger consistent (hash marked only after its outcome is final).
- `tools/chart_drops.py` validates against the schema before writing; an
  invalid extraction cannot reach disk.
- The loader treats disk as untrusted anyway (hand-edited files): validation
  failures → health note, skip file.

## Testing

No vision, no network, no LLM in CI. Fixture JSONs + tmp dirs only.

- Filename parser: round-trips, MYT timestamp → epoch ms, mtime fallback,
  reject unknown source / malformed names.
- Ledger: dedup by content hash, rename-immunity, outcome recording,
  dropped-not-resurfaced.
- Schema validation: accept the contract, reject unknown keys / bad enums /
  missing fields.
- Loader: latest-per-(source, panel, window), staleness vs `as_of_ms` (as-of
  determinism), malformed → note, unknown source → note, per-snapshot
  failure isolation, `window`/`scope` null handling.
- Compute: above/below split, signed ATR distances, nearest-first ordering,
  `max_rows_per_side` cap, spot-hint deviation flag, `atr14 == 0` guard.
- Renderer: block line format, absent/stale/degraded paths, health notes.
- Serialisation: `SymbolPanel.external` through the JSON-safe path.

## Definition of Done

- `make lint-py` · `make typecheck` · `make test` green;
  `make test-regression` goldens unmoved (additive-pure — nothing touches the
  backtest pipeline).
- `make lint-md` green for spec + skill docs.
- CLAUDE.md: `brief/` bullet gains the M3 external layer; skills table gains
  `/ingest-charts`. README brief section updated.
- Smoke: drop a real Coinglass Heatmap-24h + Map-1d pair → `/ingest-charts`
  → verify digest → approve → `make buibui-brief` shows both External lines
  with window + age.
