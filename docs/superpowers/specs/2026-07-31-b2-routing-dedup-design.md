# B2 — routing dedup for the ingest sinks (design)

Date: 2026-07-31 · Status: design approved, unimplemented
Backlog origin: `/ingest-x` iteration-2 item #7 ("routing dedup", found 2026-07-02)

## 1. Problem

The ingest pipeline (`/ingest-x`, `/ingest-video`) appends to three gitignored sinks:

| Sink | Stream | Consumed by |
| --- | --- | --- |
| `docs/plans/thesis-inbox.md` | A — hypotheses | the operator, when picking the next research question |
| `docs/plans/mechanics-backlog.md` | B — trade mechanics | the exit-policy / T5 test families |
| `docs/plans/pundit-calls.jsonl` | C — dated setups | `tools/pundit_score.py` → author hit rates → brief pundit board → F2 card |

The **fetch** layer dedups correctly: `.cache/x-posts/<id>.json` and
`.cache/video/<id>/asset.json` make a re-run cost zero network. Nothing dedups the
**routing**. There is no routed-ids ledger and no grep-before-append in code. The only
guard is a prose instruction in both skills — "before appending a claim, grep the sink
yourself" — which is discipline-dependent, and discipline-dependent steps fail under bulk.

Two distinct duplicate classes hide behind the one backlog line:

1. **Identity duplicate** — the same post/video routed twice across sessions. Observed as
   a near-miss on 2026-07-02 (a cached JordiCharts status that would have been
   double-appended to `pundit-calls.jsonl`, caught only because the sink happened to be
   read first). Mechanically detectable.
2. **Semantic duplicate** — the same *thesis* from a different source. Observed twice:
   most recently 大漂亮 restating the ~69k short-term-holder cost-basis claim 18 days
   after @Max1milianPrice. Correct handling was a corroboration line on the existing row,
   not a second row. Not detectable by id at all.

### Why it matters, per sink

- **Stream C is a scoring bug.** `pundit_score.py` computes per-author hit rate and R
  proxies. A duplicated call double-weights one outcome, inflating that author's hit rate
  if it won and deflating it if it lost. The ledger exists to produce honest priors, and
  those priors feed the daily brief and the F2 trade card.
- **Stream A is worse, epistemically.** Two rows asserting the same thesis read as two
  independent sightings. They are not — that is one idea propagating through a pundit
  network, which is arguably the opposite signal (crowding). This project runs Holm
  corrections and PBO precisely because it does not trust independence claims;
  miscounting at the *input* stage is a bias no downstream gate can repair, because every
  one of them assumes distinct observations.
- **Stream B is clutter** — a wasted research slot; nothing statistical depends on the count.

### Current state (audited 2026-07-31, before any change)

The sinks are **clean**: 94 rows in `pundit-calls.jsonl`, zero duplicate URLs. Eight
groups share `(author, symbol, direction, date)`, and inspection confirms every one is a
genuinely distinct call — different levels, different source URLs. The manual grep guard
has held so far.

So this work is **preventive, not remedial**. It is justified by scale (40 videos queued,
tranches of ~5) and by the base rate: a pundit routinely repeats one thesis across a week
of uploads, so back-catalogue ingestion has a far higher duplicate rate than one-off X posts.

## 2. Structural constraint

**Streams A and B carry no source id.** `thesis-inbox.md` is freeform markdown
(`## <date> — @author on <topic>`) whose stated purpose is "paste anything here, no
structure required"; `mechanics-backlog.md` is a bullet list
(`- <date> (@author, ...): ...`). Only Stream C carries a `url`. A grep-the-sink-for-the-id
check is therefore possible only on Stream C today.

Resolution: a **side ledger**, not ids embedded in the sinks. This also happens to be the
semantically correct choice — the ledger records that an item **was routed**, which is a
fact about history. The sinks are working files whose rows get consumed and deleted (the
thesis inbox explicitly says entries get "marked processed"), so a deleted row must not
re-open the item for re-routing.

## 3. Ledger key

**`(source_id, item_ts, sink)`** — never `source_id` alone.

One video legitimately yields multiple items: `umX9m7y7jsU` produced calls at `t=162s` and
`t=886s`, `0jctzIc5t_E` at `t=251s` and `t=537s`. A key of `source_id` alone would silently
eat real rows — the dedup would destroy exactly the data it exists to protect. Including
`sink` lets one moment yield both a claim and a setup.

X posts use `item_ts = 0.0`. `item_ts` is rounded to 1 decimal place so float noise cannot
split one item into two ledger entries.

Accepted limitation: two items of the same content type at the same timestamp collide.
Frame marks are deduped within a 45s window so this is rare, and the review digest shows
the collision.

## 4. Module — `tools/route_dedup.py`

Pure, stdlib-only matching; I/O confined to the ledger read/write. Sits beside
`tools/x_route.py`, which both skills already share for the routing decision.

Ledger file: `docs/plans/routed-ledger.json` (gitignored, durable). Atomic write
(tmp + rename), loud abort on malformed — the `tools/yt_feed.py` state-file pattern.
Deliberately **not** under `.cache/`: the fetch caches are disposable by design, and
pruning one must not silently re-open the duplicate hole.

### 4.1 Identity layer

```text
load_ledger(path) -> list[RoutedItem]                  # I/O; missing file -> []
is_routed(ledger, source_id, item_ts, sink) -> bool    # pure
append_routed(path, items) -> None                     # I/O, atomic, idempotent
```

`RoutedItem` is a frozen dataclass: `source_id`, `item_ts`, `sink`, `routed_ts_utc`.

An exact hit **blocks** the append — unambiguous, no judgement required. Escape hatch:
`--force`, for deliberately re-routing after deleting a bad row.

### 4.2 Semantic layer (pure)

```text
normalize_levels(text) -> frozenset[float]
split_entries(sink, text) -> list[str]
find_similar(claim, sink_text, *, top_n, min_score) -> list[DedupCandidate]
```

`normalize_levels` handles `69k` → 69000.0, `1,982.10` → 1982.1, `~57000` → 57000.0,
`$60,946.8` → 60946.8. Two negatives are load-bearing: an ISO date (`2026-07-13`) must not
parse as a price level, and fib ratios (`0.618`, `0.5`) collide across unrelated entries,
so numeric evidence alone cannot carry a match.

`split_entries` is sink-shaped: a level-2 heading (`^##`) starts a thesis-inbox entry, a
top-level bullet (`^-`) starts a mechanics entry, and the JSONL is one entry per line.

`find_similar` weights shared price levels above shared content words and normalises for
entry length so a long entry does not dominate. `DedupCandidate` carries the sink, a
trimmed excerpt of the matched entry, the score, and the shared levels/terms that produced
it — the excerpt is what the operator actually reads in the digest.

**The threshold is set for recall, not precision, deliberately.** There is exactly one
confirmed positive pair to calibrate against, so any threshold claimed as "tuned" would be
fit on n=1. Because the output is advisory, a false positive costs a glance at the digest
and a false negative costs a corrupted sink; the asymmetry says err loose. This is stated
so a later reader does not mistake a loose threshold for sloppiness.

## 5. Wiring

Both skills, at the routing step (`/ingest-x` step 4, `/ingest-video` step 8):

- **`check`** before appending. `already_routed: true` blocks the append and is reported
  in the digest as already-routed. `candidates` never block — each is surfaced **in the one
  consolidated review digest**, under its item, with the matched excerpt, so the operator
  decides: new row, corroboration line, or drop.
- **`mark`** after the sink write succeeds. `mark` is the only writer.

The ordering is the load-bearing rule, not a detail: marking at check time would let a dry
run or an abandoned review consume an id and dedup away the real write later. This is the
wifey-#68 watermark-on-send defect class, and ST10's ledger already encodes the same rule.

CLI shape (mirrors `tools/chart_drops.py` / `tools/yt_feed.py` subcommand style):

```bash
PYTHONPATH=. poetry run python tools/route_dedup.py check \
  --source-id <id> --item-ts <ts> --sink <path> --text "<gist>" --json
PYTHONPATH=. poetry run python tools/route_dedup.py mark \
  --source-id <id> --item-ts <ts> --sink <path> [--force]
```

## 6. Out of scope

- **Never auto-drops a semantic match.** Advisory only; the operator's review gate stays
  the decision point — same posture as `verified:true` in `/ingest-charts` and `mark` in ST10.
- **No retro-dedupe of the existing sinks.** They are clean (§1).
- **No cross-language matching** beyond what the already-normalised English rows provide.
- No schema change, no DB touch, no golden-fixture impact. All writes land in gitignored
  `docs/plans/`.

## 7. Test plan

- Ledger: missing file → empty; round-trip; atomic write; malformed → loud abort;
  idempotent `append_routed`.
- Key collisions: same `source_id` + different `item_ts` are distinct; same
  `(source_id, item_ts)` + different `sink` are distinct; identical triple is a hit.
- `is_routed` is pure — no filesystem access.
- `normalize_levels`: the four positive forms, plus the ISO-date and fib-ratio negatives.
- `split_entries`: one case per sink, against fixtures shaped like the real files.
- `find_similar`: the observed ~69k restatement scores above threshold; two unrelated BTC
  entries score below; empty sink returns `[]`.
- CLI: `check` exits 0 and emits parseable JSON; `mark` appends exactly one row and is
  idempotent; `--force` overrides a block.

No network in any test — the module has no network surface at all.
