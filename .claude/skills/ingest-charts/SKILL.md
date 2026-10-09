---
name: ingest-charts
description: >
  Ingest Coinglass / MMT heatmap and liquidation-map screenshots from
  docs/plans/chart-drops/ into verified external-context JSON for the daily
  Brief (M3), behind ONE review digest for the whole batch; nothing is written
  until the operator approves. Invoke when the user says "/ingest-charts",
  "ingest my chart drops", or has dropped new heatmap screenshots.
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
  `<source>[-<venue>]_<SYMBOL>[_<YYYYMMDD[-HHMM]>[_<label>]].png|.jpg|.jpeg`
  (source ∈ coinglass|mmt, MYT timestamp). `venue` is an optional dash-suffixed
  token on the source segment naming the specific exchange a panel's data comes
  from (e.g. Coinglass's per-exchange liq-map view); omit it when the panel
  is exchange-aggregated or the exchange is unknown. Do NOT guess. Continue
  with `pending`. Echo these copy-paste examples with the rename request:
  `coinglass_BTCUSDT_20260715-0930.jpeg` ·
  `coinglass_BTCUSDT_20260715-0931.jpeg` (bump the minute so two panels of
  the same symbol get distinct names — the order carries NO panel meaning,
  and the operator's real captures have run map-then-heatmap, the reverse
  of any order you might read into this pair) ·
  `coinglass_BTCUSDT_20260824_Map_7d.png` ·
  `coinglass_BTCUSDT_20260824_Map_1y.png` (a `<label>` after the timestamp —
  free text, must start with a letter — so a BURST captured in one minute can
  still be named; bumping the minute is otherwise the only disambiguator, which
  is what left five same-minute liq-maps unnameable on 2026-08-24) ·
  `coinglass-hyperliquid_BTCUSDT_20260716-1040.png` (map scoped
  to the Hyperliquid venue) · `mmt_ETHUSDT.png` (no timestamp = file
  mtime). Panel type never goes in the name — the extraction detects
  heatmap vs map.

  ⚠ **The time attaches to the date with a DASH, and the underscore form
  BACKDATES the capture silently.** `..._20260827-1800.png` parses as 18:00
  MYT; `..._20260827_HeatMap_1d_1800.png` parses as date-ONLY, so the capture
  is stamped **midnight** and the rest is swallowed as free-text label and
  discarded. Measured 2026-08-27 (ST112): six hand-named drops lost 18 of
  their 48h freshness window, and **every gate read GREEN throughout** — the
  daily check asserts RECENCY, and a backdated stamp IS recency, just wrong.
  `tools/coinglass_capture.sh` builds the dash form from a real clock read;
  only hand-naming reaches this.
  ⚡ **Since #827 the parser REJECTS that shape** — a date-only stamp whose
  label ends in a valid HHMM lands in the UNPARSEABLE list rather than at
  midnight. Ask the operator to rename it to the dash form; never re-stamp it
  from the label. A date-only stamp with any other label still parses as
  midnight, so the dash rule still matters.

  ⚠ **The label is parsed and DISCARDED, so tell the operator it is for their
  eyes only.** Nothing downstream reads it, and it is deliberately NOT a source
  of truth for `window`: that field is read off the chart by the extraction,
  corrected at the review gate, and is part of `load_external_state`'s dedup key
  (`analytics/brief/external.py`), so a name asserting `1y` over an image reading
  `180d` would silently split or merge snapshots. Naming a file `_1y` does not
  make its window 1y. **Never suggest a rename that encodes the window as though
  it were data** — and when two drops differ only by label, they are separate
  images with separate hashes, so each still gets its own extraction.

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
are not free, since each costs a vision dispatch — cost it at the LATEST entry in
the series below, never at this sentence — and a 1w heatmap band is structural
context, not a same-day actionable level.
**The per-image figure is MEASURED, and it ROSE then PLATEAUED — fourteen RECORDED 6-panel
batches ran mean 32.7K (2026-08-11, ~196K total), 37.1K (2026-08-13, ~223K total,
range 36.7–37.5K), 36.4K (2026-08-18, ~218K),
43.6K (2026-08-19, range 41.9–50.0K), 50.8K (2026-08-20, range 48.6–55.7K),
58.3K (2026-08-25, ~350K total, range 56.7–64.5K), 57.9K (2026-08-27, ~348K
total, range 57.6–58.3K), 58.6K (2026-08-28, ~351K
total, range 57.5–63.8K), 60.6K (2026-08-31, ~363K total, range 58.9–66.1K),
59.9K (2026-09-01, ~359K total, range 59.0–63.0K), 63.1K (2026-09-02, ~379K
total, range 61.9–64.5K), 64.7K (2026-09-03, ~388K total, range 63.2–65.4K),
65.4K (2026-09-07, ~392K total, range 64.1–67.0K)
and 69.7K (2026-09-09, ~418K total, range 67.9–73.3K).
⚠ **The series is INCOMPLETE and that count is of ENTRIES, not of batches** — 08-12,
08-14, 08-17 and 08-24 each hold six panels in `docs/plans/external-context/` with no
entry here, so read every derived figure below as computed on what was RECORDED rather
than on what ran (SoT ST131). A candidate 38.3K mean for 08-14 sits in the skill-fix
queue. ⛔ **Do not fabricate the missing means** — an invented entry corrupts the only
signal separating `chart-extract` from a general-purpose fallback.
That is 78% over the fourteen days to 08-25, and then a PLATEAU — nine readings
across the fifteen days since sit inside 1.20× of each other.** ⚠ **A step down is the
NORM, not the exception: three of the thirteen are down-steps — 08-13 → 08-18 at 0.981×,
08-25 → 08-27 at 0.994× and 08-31 → 09-01 at 0.988×.** ⚠ **BOTH of the first two were
measured on time and went missing from this series anyway, by DIFFERENT routes — which is
why "append on every run" is stated as a rule and not a habit.** 08-18 was filed in the
skill-fix queue and never appended here, so the series read as monotone for nineteen days
while the data was not. 08-27 was appended under the WRONG DATE: one 2026-08-28 session
handled TWO 6-panel batches — the re-stamped `20260827-1800` set whose capture time it was
busy correcting (ST112) and the day's own `20260828-174x` set — and only the second reached
this file. The first was recorded in the SoT instead, so for nine days the two surfaces
looked like a **0.7K disagreement about one batch** and were left unreconciled on exactly
that reading. ⇒ **They were both right.** ⛔ **Before appending, key the entry to the
snapshot's CAPTURE date, never to the date you are running** — a corrective re-ingest is a
different batch from today's, and this series is keyed by capture. Say the plateau out
loud rather than the 78%: quoting the rise alone reads as a trend still running,
which is the framing that makes a correct dispatch look like a fallback. Read the
trend as "unpredictable" rather than "always rising", and do not extrapolate a step
that has stopped. A stale baseline still turns a correct dispatch into a
false fallback alarm — which is exactly how the ABSOLUTE band failed below. Cost a batch at the LATEST entry in that
series, never at a figure quoted elsewhere in this file: every estimate written here
has been low within a fortnight, five times running. **Append the new mean on every
run** — the series is not history, it is the calibration the fallback check below
reads.

This matters because the daily check asserts **recency, not coverage**: one
fresh drop greens the line while five panels rot. Naming the gap here is the
only thing standing in for the check ST15 still owes.

## 2. Extract (one chart-extract subagent per pending image)

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
while looking exactly like success. **Catch it by comparing this batch's mean `subagent_tokens` against the LAST RECORDED
MEAN in the series above — a STEP, not a band.** Drift between batches is smooth
(32.7K → 37.1K → 36.4K → 43.6K → 50.8K → 58.3K → 57.9K → 58.6K → 60.6K → 59.9K
→ 63.1K → 64.7K → 65.4K → 69.7K over twenty-nine days, the last three steps 1.025×,
1.011× and 1.066× — a
step can go DOWN, three times now, so read the band as two-sided rather than a ceiling
on a rising series);
a silent fallback is a jump, measured at ~1.5× its same-era
`chart-extract` cost (49K against 32.7K, 2026-08-07). So: a batch mean
within roughly 1.2× of the previous entry is drift, and one at 1.4× or above is the
fallback. Say which reading you got in the digest, and append the new mean either way.

⚠ **Do NOT reinstate an ABSOLUTE band — it was tried and it FAILED (2026-08-19).** The
rule read `chart-extract` ~33–36K against a ~49K fallback; a legitimate batch then
measured 41.9–50.0K, **overlapping the fallback band outright**, so a correct dispatch
read as a silent fallback. A fixed number cannot survive a cost that drifts, and the
last four estimates in this file all went stale inside two weeks.

⚠ **And do NOT compare panels WITHIN one batch.** The fallback fires when
`chart-extract` fails to RESOLVE, which is a per-session property: every panel falls
back together, so a within-batch outlier check can never fire. It looks like a tighter
test and is a vacuous one. **The one measured fallback pair is 2026-08-07 and the
general-purpose figure has NOT been re-measured since**, so treat the 1.5× as a
single-pair estimate rather than a calibrated threshold; re-measure it the next time a
fallback is genuinely taken.

Do NOT substitute the stock `Explore` agent, and do NOT batch several images into one
agent.

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
  "100x-heavy"). Bars ABOVE current price are SHORT liquidations, bars BELOW
  are LONG ones — a short is liquidated when price RISES. Any label naming a
  side must get this round the right way.
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

### ⚠ A cluster is a BAND, never a LEVEL — measured, not asserted

**Same-input A/B, 2026-08-12** (3 × 1d liq_map panels re-extracted from the
byte-identical images; report
`docs/plans/scratch/7w-map-cluster-same-input-2026-08-12.md`):

| what | reproduces on identical input? |
| --- | --- |
| `panel` · `window` · `scope` · `spot_price_hint` · `spot_source` | **3/3 identical** |
| intensity of a matched band | **19/19 — perfect** |
| existence of a `high` band near spot | never lost |
| **exact band edges** | **3/19 = 16%**; mean drift **20–43% of band width** |
| **which faint tail makes the cut** | near-arbitrary |
| **how adjacent bands segment** | merge/split freely |

**So a cluster supports "heavy liquidation interest around X ± the band width"
and nothing finer. Anything that thresholds or ranks on an exact edge is
reading noise at the third digit.** This is why M4 caps mapped liquidity at ONE
confluence input — the cap is a precision statement, not a modesty one.

**Two traps that follow.** Do NOT use cluster COUNT as a quality metric: two of
the three panels returned an *unchanged* count while 20% and 12.5% of their
clusters had no counterpart at all, so count is nearly blind to the churn that
matters. And when comparing two extractions, match bands by **overlap**, not by
greedy best-IoU — a merge of two adjacent bands reports as one vanished band
under IoU, which reads alarmingly like a `high` cluster disappearing near spot.

A future extraction-quality A/B scores **band agreement and intensity**, never
cluster count.

## 3. Review digest (ONE for the whole batch — the human-verify gate)

Present a table per image: file · source · symbol · panel · window · scope ·
capture age · spot hint (+source) · clusters (band, kind, intensity, label)
· flags (symbol_mismatch, skip_reason, low confidence, spot hint far from
recent price, non-canonical `window`). Ask the operator per image:
**approve / correct / drop**. Corrections are applied to the cluster list /
fields before writing and summarized in the snapshot's `notes` field. NOTHING
is written before this gate.

**Flag a `window` outside the canonical set — `12h` `24h` `48h` `1d` `3d` `1w` —
and normalise it HERE.** `window` is a DIMENSION of `load_external_state`'s dedup
key rather than a filter on it, so `"1 day"` and `"1d"` are two surviving
snapshots, not one superseding the other (ST74). A free-text variant therefore
STACKS silently — nothing downstream rejects it, and the panel count quietly
doubles while every panel still reads fresh. This gate is the only place it is
cheap to fix: after the write the variant is part of the key.

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

⚠ **That 1% is a FLOOR, not the test — a panel whose cross-panel gap is several
times its batch siblings' is suspect at any absolute figure.** Measured
2026-09-03: SOL's heatmap read 0.82% from live and 0.79% from its own map twin,
passing the 1% bar, while BTC and ETH sat at 0.12% and 0.15% in the same batch —
so the absolute rule cleared it and the RELATIVE read convicted it. This is the
same defect the token-cost check above already learned and fixed: a fixed number
cannot survive a quantity that drifts, and this one drifts with volatility, so
0.82% is unremarkable for SOL on a wild day and damning on a quiet one. ⚠ The
within-batch comparison that is vacuous for the cost check is VALID here, and the
reason is worth keeping straight: a fallback is a per-SESSION property so every
panel falls back together, whereas spot accuracy is per-PANEL, so a batch sibling
is a genuine control rather than a copy of the same draw.

⚠ **Below 10% this review is the ONLY check, so the relative read is load-bearing
rather than a refinement.** The loader's runtime guard is
`_SPOT_DEVIATION_FRAC = 0.10` (`analytics/brief/external.py:27`) — **ten times the
bar above** — so SOL's 0.82% would not have warned whether the hint was written or
nulled. ⇒ Read "nulling the hint mutes `spot_hint_deviation`" as TRUE ABOUT THE
MECHANISM AND EMPTY IN THIS RANGE: what nulling costs is the hint itself, never a
warning that was never going to fire. The two thresholds are not in conflict and
must not be "reconciled" — 1% screens a HUMAN read of an axis at ingest, 10%
catches a wrong-symbol or wrong-axis panel at runtime, and a 1% runtime guard
would fire on ordinary capture lag.

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
- **`spot_price_hint` is the anchor consumers judge the clusters against** —
  which is why the null-don't-substitute rule above is a rule. A consumer
  comparing clusters to the live mark instead reads ordinary drift as an
  extraction sign-flip; `/card` digest step 2 carries the mirror, after a
  false alarm on 2026-08-07l.

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
