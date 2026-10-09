# Where does ingested information die between capture and test? (Issue #917)

Follow-on: #977, #978, #979 (the three intake changes decided in #918).

Date 2026-10-08. Read-only research. Streams (Issue #796 and the skills' routing tables): **A** = claims to `thesis-inbox.md`, **B** = mechanics to `mechanics-backlog.md`, **C** = setups to `pundit-calls.jsonl`. Verdicts are joined from `2026-10-07-tested-register.md` (rows A/M/I/S), not re-derived.

## Answer

The funnel narrows between "routed" and "priced", not at capture or routing: roughly 1,160 YouTube candidates and about 200 X posts became 610 routed items (A 74, B 233, C 303), but of the 81 thesis-inbox rows only 8 have been tested and 21 more priced, and of the 65 still-open NEW rows only 7 are open, unpriced and not already judged unreachable by their own text. Explanation 1 (testing capacity) is not supported for Stream A in its narrow sense, test runs: there is no queue of priced, reachable, untested rows. The open rows wait on data (27, of which 15 have a free or record-forward route nobody has built), on power (13), or are dead-class re-slices (14), so the binding capacity is building data collectors and pricing power, not running tests. Stream B has about 185 entries and no per-item status, so capacity cannot be tested there. Explanation 2 (price re-slicing) holds for 32 of 81 rows (40%) and is not the main fate: 41 rows need non-price information, and 31 rows (38%) are cycle- or regime-timing claims whose n is the number of cycles. The better description is a genre mismatch: four authors supply 58% of Stream A, mostly cycle-timing and on-chain narratives that cannot be powered with 7 years of crypto history. The skills drop information (item cap 559 lines, verdict gate about 140 claims, 44 retrospective setups, 19 X rows lost to a failed write), but the lost material is Stream B mechanics and closed-trade outcomes; I found no edge-class idea dropped.

## 1. Funnel

### 1a. Per source, capture to routing

| Source | Captured | Candidates extracted | Routed A / B / C | Dropped with a recorded reason |
| --- | --- | --- | --- | --- |
| YouTube | 154 videos in `yt-feed-state.json` (140 ingested, 14 skipped, all non-crypto); 148 notes | about 1,163 (604 rows in note item tables + 559 below-cap lines; upper bound, 31 notes keep a count only) | 54 / 211 / 177 = 442 from 138 videos (38% of candidates) | cap 559 lines (98 notes); verdict gate 113; retrospective setups 44 |
| X | at least 167 post ids reach a sink (ledger) + 31 notes with `route: dropped` = about 198; 181 Apify-discovered on 08-24, 35 kept | 116 x-notes | 20 / 22 / 126 = 168 from 167 ids | verdict gate 25 claims; 31 dropped notes; 146 of 181 discovered never read |
| Books, repos | 1 book-skill (`carver-futures`) + 1 repo (pysystemtrade), one `/research-distil` run | 12 claims | 0 / 3 / 0 | G1 rejected 5 (the note flags its own G1 count as unreliable), G2 rejected 1, hard cap of 3 held back 3 of 6 survivors |
| Chart drops | 174 images scanned (`processed.json`) | 153 verified snapshots, 1,293 clusters, 23 distinct days | none (feeds `external-context/`, Brief and card only) | 16 dropped, 5 skipped |

Derivation: `routed-ledger.json` has 610 items = 442 YouTube + 168 X ids by sink (`funnel_ledgers.py`); pundit rows 303 non-card (177 YouTube, 126 X) equal the ledger's C count. Video candidates from `funnel_video.py` (32 notes have no parseable item table, so 604 is a floor). X captured is a floor: `.cache/x-posts` is empty on this host and `x-coverage.md` (08-11) counts 167 cached posts that include chain parents and quoted posts.

### 1b. Per stream, after routing

| Stream | Routed (ledger) | Entries on disk | Power-priced | Tested | Verdict standing |
| --- | --- | --- | --- | --- | --- |
| A | 74 (54 YouTube, 20 X) | 81 item rows, 26 authors | 29 of 81: 21 priced or n-stated unreachable, plus 8 tested | 8 rows = 5 tests: H14 (3 rows), H15, cross-venue CVD (2 rows), ST56, 2026-08-20 shower thoughts | none positive: H14 INSUFFICIENT, H15 NO-EDGE/INSUFFICIENT, CVD all 10 trials FAIL, ST56 INDETERMINATE, shower thoughts closed. Priced and closed: RSI<20, LTC halving, Hurst, NFP |
| B | 233 (211 YouTube, 22 X) | about 185 bullets (195 `^-\s` lines by grep, about 10 in the re-audit preamble); 67 `TEST:` lines; no status field | 3 (research-distil G3 family: bar +0.1768R/alert, INSUFFICIENT pending `sr_variance`) | family level only: give-back measured (A48), exit tuning closed (M08), era study (A44); 0 per-item tests | exits closed as a P&L lever; the rest unknown per item |
| C | 303 rows | 303 | not applicable; scoring is mechanical | 165 calls resolved, 192 triggered, 42 open (`pundit-priors.json`, card rows excluded) | no gate run on Stream C; resolved n by author: 1 author at 20 or more, 3 at 10 or more, 12 at 5 or more; hit rate 43% pooled; ST21 killed it only as an author pick |

Stream A rows by tag (heuristic: link type in the row body): YouTube 52, X 16, both 5, operator 2, unlinked 6. Of the 8 tested rows 4 are YouTube-sourced, 2 operator, 2 X or mixed.

## 2. Drop points per skill

### `/ingest-video` (and `/ingest-feed`, which feeds it)

| Step | What is lost | Evidence | Touched |
| --- | --- | --- | --- |
| Pass-1 `item_cap` (default 5, per-channel 12 on 2 channels), ranked by `specificity` | Mechanics score low on specificity, so they are cut first | 559 below-cap lines in 98 notes; 31 more notes keep a count only. Harvest 2026-08-14: 30 mechanics routed, 25 dropped lines recovered (about 11 usable), 29 unrecoverable; a second harvest recovered 6 more (`mechanics-backlog.md` L1049, L2479) | 129 of 148 notes |
| Retrospective gate | A closed trade's entry and outcome are dropped; only the management lesson survives as a mechanic | 44 setups "DROPPED (retrospective: entry predates video)", e.g. `0jctzIc5t_E` ts 67.5 dropped, its ts 153.1 breakeven rule kept | 44 items |
| Verdict gate (ALREADY-TESTED, FROZEN, NOT-FALSIFIABLE) | A claim matched against a verdict snapshot copied into the skill | 113 video + 25 X claims dropped, vs 74 filed to A. The snapshot lagged a retraction once: H14/H15 read "clean NO" until 2026-08-13 | about 138 items |
| Attribution (relays) | Relayed calls cannot be scored against the host | `_xNV7scZVNU`: all 5 kept items relays, 0 host-own; Kolunite 25 candidates to 1 routed row; 12-video backlog paused | 1 channel |
| Vision selectivity | Frames only at the top-cap timestamps; 22 notes have no vision pass; 15% of C rows low confidence | 27 of 177 YouTube C rows `low`, only 11 of the 27 carry all three levels | 22 notes, 27 rows |
| Captions and language | 45 notes zh, 32 with `lang: ""`; provenance recorded on only 59 of 148 notes (49 auto, 5 manual, 5 ASR) | AGENTS.md cites 17 of 89 notes built from ASR; raw_quote repairs below | 59 of 148 known |
| `raw_quote` | Light repair of ASR homophones, not paraphrase | 19 of 28 quotes with a same-note transcript are verbatim; the 9 misses are all zh and fix ASR errors (止盈预期 vs 指引预计, 防守点 vs 房市点). Sample is 6 notes with item detail | 28 quotes |
| Dedup | Restatement, not loss: 56 (author, symbol, direction) groups hold 225 of 303 C rows | `route_dedup` same-source scope by design; two single videos yield 10 rows each | 74% of C rows |

`/ingest-feed`: the poll floor is 2026-07-17..21 for all 10 channels, so earlier uploads exist only via backfill (92 of 148 notes are `backlog: true`). The Beauty of Mathematics playlist has 10 of 72 examined, and its tranche-1 digest recommends closing the other 63 after 38 candidates yielded 5 bullets. That is a decision, not a silent loss.

### `/ingest-x`

| Step | What is lost | Evidence | Touched |
| --- | --- | --- | --- |
| Discovery filter (Aug tranche) | Text-only framework posts and replies | 181 discovered, 35 kept (non-reply with media): 94 replies, 26 media originals and 26 text-only originals not read. Stream B frameworks can be text-only | 146 posts |
| `text_truncated` | Long-form tail unreachable keylessly | 16 of 116 notes flagged; repaired only if the operator pastes | 14% |
| Chart-only levels | Direction and levels live only in the image | 126 X C rows: 47 (37%) have all three levels, 73 (58%) no stop, so no `avg_r`; YouTube is 102 of 177 (58%) and 62 (35%). 122 of 126 X rows carry no `vision_confidence`, so read quality is unrecorded | 79 rows |
| Verdict gate | As above | 25 claims | 22% of notes |
| Write step | Declared routes never written | 19 X Stream C rows recovered 2026-08-26 from note JSON ("original 08-25 append never ran", `extraction_path`) | 19 rows |
| Inferred primitive | Each item is mapped to a detector family at extraction | `gap_note` calls the setup frozen or already-captured in 75 of 106 X notes (71%) and 14 of 28 video items | 71% of X |

### `/ingest-charts`

174 images scanned, 153 written (88%), 16 dropped, 5 skipped. The extractor keeps at most 10 clusters "highest-confidence first, skip faint noise": 63 of 153 snapshots (41%) sit at exactly 10, so the cap binds on the busiest panels. The real loss is downstream: only 23 distinct days exist, bands reproduce to about 16%, and the one consumer (Brief and card, at most one confluence input) has never been measured (#835).

## 3. The 76 NEW thesis-inbox rows, grouped by blocker

Counted from `funnel_inbox_classify.py` (single reader; boundary cases noted). 83 `##` headings = 81 item rows + 1 template + 1 re-audit block. 76 rows read NEW; 11 of those are stale (already tested or priced elsewhere), leaving **65 open**.

| Group | Rows | What blocks them | Line numbers in `thesis-inbox.md` |
| --- | --- | --- | --- |
| Needs data we do not hold | 27 | **D1 9**: free history exists per `2026-10-07-data-sources.md`, no collector or backfill built (MVRV, exchange flows, L/S ratios, Deribit skew, CPI dates). **D2 6**: record-forward or 23-day history only (liquidation heatmaps, spoofing, liquidation decay). **D3 12**: no free route (STH/LTH cohorts, ETF flows, accumulation addresses, social series) | D1 57 317 804 1077 1274 1439 1781 2316 2496; D2 69 81 431 671 1148 2286; D3 188 494 535 616 652 726 825 889 955 1417 1759 2197 |
| Would fail power at a feasible n | 13 | event n of 2 to 10 (cycle analogs, midterm years, dated predictions) or priced in-row | 127 454 684 910 931 1044 1180 1820 1861 2101 2125 2151 2175 |
| Never priced, testable now | 11 | 4 are pre-judged unreachable by their own text (n=2 to 10): 2346 2378 2434 2463. **7 genuinely open**: 111 249 473 507 552 1204 2544 | as listed |
| Re-slice of a dead class | 14 | price or volume only: POC, TPO, value-area, retest counts, analog weeks, low-volume traversal, nested ranges, RSI, vol compression | 94 144 213 233 357 382 408 1312 1392 1732 1899 1945 1994 2035 |
| Stale NEW (tested or priced elsewhere) | 11 | CVD x2, H14 x3, H18, equity/gold x4, ST56 | 160 271 638 701 740 759 783 844 870 1004 1471 |

Whole-file cuts: **cycle-timing** 31 rows (6 U, 12 P, 13 D); **price-derived** 32 rows (14 R, 12 P, 6 U); **non-price information** 41 rows. By author (26 distinct): @benjaminjcowen 21, @GiantCutie 11, @tiabtc 9, @sergio_tesla_ 6, so the top four hold 47 of 81 rows (58%). Six themes hold 43 of 81 rows: on-chain 12, Cowen cycle analogs 8, equity/gold/macro 7, VWAP/POC/value-area 6, liquidation 5, social-interest 5.

The old figure "about 60 of 83 NEW" is out of date: 76 of 81 now read NEW, and 11 of those are stale markers (register I15 reached the same conclusion with 8).

## 4. Testing the two filed explanations

- **Capacity (Stream A): not supported.** Only 7 of 65 open rows (11%) are testable, unpriced and not pre-judged dead. Pricing is one `distil_power` run, so an idle test queue would show as priced-and-reachable rows; the count is zero. The binding work is building collectors (15 D1/D2 rows) and the power calculation, not test runs.
- **Capacity (Stream B): cannot be judged.** No per-item status; 67 `TEST:` lines sit beside about 185 bullets with no run recorded. This is the one stream where capacity is a live candidate.
- **Re-slice: partly.** 40% price-derived, and all 14 R rows are cheap to test and low prior. But H14 and H15 (new data, order-flow, USD/JPY) returned INSUFFICIENT, not NO, so data rows die on n too.
- **Stream C** does not fit either story: 303 rows are routed and 165 resolved, but per-author n is below any gate (one author at 26).

## 5. The operator's two proposals

**(a) Deeper chart read.** It changes levels, not verdicts. The current vision pass already changed a level in 35 of 177 YouTube C rows (20%; 28 at high confidence), 0 of 35 mention direction. Examples: a target spoken as about 68,000 became the ticket's 67999; a Fib zone spoken as about 65,300 became the chart-drawn 64,281 to 64,787; a spoken 1.382 to 1.618 zone of 70,000 to 78,200 became 70,362 on the drawn tool. Shifts of that size (up to about 2% of price) can move a touch or stop outcome in Stream C scoring. The low-confidence cases (27 rows) are attributed in the notes to the wrong frame (a tool caught mid-navigation, ts 506.5 in `JY_wY8XXjYU`), not to read depth. Where the author drew no stop (73 X rows, 62 YouTube rows) a deeper read creates none. For A and B items there is no case where a chart read changed the filed claim's test route: **no evidence either way**.

**(b) Inferring an unexplained setup's rationale.** The extractor already infers a primitive (`gap_note`), and it files 71% of X setups as frozen or already-captured, so an inferred rationale mostly lands the item in the dead class rather than rescuing it. For the one visible case of a claim with "no stated mechanism" (`oFgVfHSbO8w` ts 366.41) the inference was the reason it was dropped. For Stream C, supplying a missing stop or invalidation would put the model's words in the pundit's mouth: the note `upp0EDvipFI` states the empty stop "is a property of the source, not the extraction" and scoring censors it on purpose. No routed item exists whose verdict an inferred rationale would have changed: **no evidence either way**, with the censoring rule as a reason against for C.

## Limits

- X denominators are floors (cache empty). Video item-table parse missed 32 of 148 notes (older formats), so per-note routing counts come from the ledger, and tables give only candidates and drops.
- Section 3 is one reader's classification, not cross-checked; boundary rows (e.g. 1204 range duration U vs R, 1274 CPI D vs P) could move between groups. The strict price-derived cut (32 rows) already counts the six price-only U rows.
- Deduplicated counts in `routed-ledger.json` cover only routed items; items dropped before routing are known only from notes.

## Reproduce

From repo root, `python -I docs/plans/scratch/wayfinder-864/research/<script>`:
`funnel_ledgers.py` (ledger, pundit rows, feed state, chart drops), `funnel_video.py` (item tables, dropped lines), `funnel_skills.py` (raw_quote check, X notes, level completeness), `funnel_inbox_classify.py` (section 3 counts and the line-number dict), `funnel_inbox_digest.py` (per-row digest used to classify). `pundit-priors.json` totals were summed inline from its `authors` map excluding `buibui_card`.
