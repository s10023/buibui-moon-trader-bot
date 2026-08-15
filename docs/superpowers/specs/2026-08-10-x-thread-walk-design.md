# X thread walk — recover a self-thread from its tail

## Goal and success metric

**Goal.** One bookmark of a thread's **last** post yields that author's whole self-thread,
root → leaf, with per-post attribution preserved.

**Success metric.** On the ST21 tranche-1 sample: every `is_thread: true` bookmark
resolves to a chain in which **every hop carries the same `screen_name`**, non-thread
posts cost **zero** extra network requests, and the ingest digest shows the recovered post
count per bookmark. Measured, not asserted — tranche 1 reports `posts_recovered /
bookmark`.

**Anti-drift.** If a step stops serving that metric, stop. In particular this spec does
**not** try to reach replies, and any work that starts reaching for them has drifted.

## Background — what was measured 2026-08-10

Probed live against `cdn.syndication.twimg.com/tweet-result`, the endpoint
`tools/x_fetch.py` already uses (read-only, no auth, no scraping). Two findings:

**1. The response already carries the parent, fully hydrated.** Top-level keys include
`parent`, `in_reply_to_status_id_str`, `in_reply_to_screen_name`, `in_reply_to_user_id_str`
and `conversation_count`. The `parent` object itself carries `id_str`, full `text`, `user`,
`entities`, `favorite_count`, `quoted_tweet`.

**2. The chain can be walked upward, one hop per request.** Proven on a real thread by
following `in_reply_to_status_id_str` until absent:

```text
hop   id                    author      replying_to   conv  txt
  0   2072352754815598663   @BigCheds   @BigCheds        2  219
  1   2072352545025019907   @BigCheds   -                9  132   <- root
```

**The asymmetry is the whole design.** Upward (leaf → root) works. **Downward (root →
replies) is impossible on this endpoint** — there is no `children`/`replies` field. This
is why the operator must bookmark the **tail**: a bookmarked parent yields nothing below
it.

**Sample honesty: 2 threads, max 2 hops.** The mechanism is a stable top-level field
rather than a heuristic, but depth >2 is untested and the hop cap below exists for that
reason.

### `conversation_count` is NOT thread length — do not use it as one

In the trace above the root reads `conversation_count: 9` while the author's own chain is
**2** posts. The other 7 are *other people* replying. It is a *diagnostic* ("a
conversation exists around this") and never a length. Capture it, never branch on it.

## The historical defect this guards against

**`x_fetch.py:112` is `is_thread = data.get("parent") is not None`.** The code fetches a
complete parent object and **discards all of it to keep one boolean**. This is the
"recorded the condition, threw away the evidence" defect: the cache entry reads
`is_thread: true`, which *looks* like the thread was handled, while the content was never
kept.

**Measured exposure: 13 of 167 cached posts (8%)** were classified from a fragment, with
nothing anywhere recording that context was missing. Re-checking what those produced is
tracked separately — this spec stops the bleeding, it does not backfill.

Two rules follow, and both are verified below rather than trusted:

- A flag that summarises data must not be the *only* thing kept about that data.
- The walk must be **visible in the review digest**. A silent multi-hop fetch is the same
  defect wearing better clothes.

## Scope

**In:** upward walk over one author's self-thread; per-hop caching and cooldown reuse;
per-post attribution; digest visibility; graceful degradation.

**Out:** everything in "Out of scope" at the end. No detector, no scoring change, no
backfill of the 13.

## Design

### 1. `XPost` gains four fields — **all with defaults**

```python
    in_reply_to_id: str = ""
    in_reply_to_author: str = ""
    conversation_count: int = 0
    thread_pos: int = 0  # 0 = root, ascending toward the leaf
```

**Defaults are load-bearing, not style.** `_load_cached` builds `XPost(**raw)` from the
cache JSON, so **167 existing entries lack these keys** and would raise `TypeError` on
every read if any field were required. **Update (Task 4b, 2026-08-15):** defaults alone
no longer make an old entry load unchanged — `_CACHE_SCHEMA` now treats a missing schema
stamp as a cache MISS, so a pre-change entry re-fetches instead of loading with these
fields silently defaulted.

The reverse direction is already safe: `_load_cached` catches `TypeError`
(`x_fetch.py:155`) and treats it as a miss, so a *newer* cache read by *older* code
re-fetches rather than crashing. Keep that except clause — it is what makes this change
revertible.

`is_thread` stays exactly as it is. Nothing downstream changes meaning.

### 2. New `walk_thread()` — a separate function, not a change to `fetch_x_post`

`fetch_x_post` keeps its contract: one URL, one request, one post. The walk is a new
function that *calls* it per hop, so single-post behaviour and every existing test stay
untouched.

Returns the chain **root → leaf** (chronological, so the text reads in order) plus a list
of health notes. Termination, in order:

1. `in_reply_to_status_id_str` absent ⇒ **root reached**, normal stop.
2. `in_reply_to_screen_name != author` ⇒ **stop, do not climb.** We have walked into
   someone else's post. Record `stopped: replying to @other`.
3. Hop count reaches `max_hops` (**default 25**) ⇒ stop and **record the truncation**.
4. A hop returns `Unavailable` (deleted/protected middle post) ⇒ stop, return the partial
   chain, record the reason. **Never raise.**

**Rule 2 is the correctness guard, not an optimisation.** Climbing past an author change
harvests a *different pundit's words* and files them under the bookmarked author — a
misattribution straight into `pundit-calls.jsonl`, which is exactly where the ledger is
least able to absorb it.

**Rule 3 must print.** Per the no-silent-caps rule, a truncated chain that reports nothing
reads as a complete one.

### 3. Hops READ the existing cache; they do not write it

Each hop is just a tweet ID, so hops go through `_load_cached` per id. **An ancestor
already ingested on its own costs zero network on the next walk.**

**Amended during implementation 2026-08-10 — the walk does NOT write the cache**, though
this section originally said it would. A cache entry carries `photo_paths`; writing one
here with an empty list would make a later `/ingest-x` of that same post read the cache,
believe its charts were already downloaded, and **silently skip the chart**. On an ingest
pipeline whose whole point is chart images, that is a worse failure than re-fetching an
ancestor. The cost of the amendment is that a repeated walk re-fetches ancestors that were
never separately ingested.

Cooldown follows `fetch_x_batch`'s discipline exactly: sleep `rng.uniform(min_delay,
max_delay)` **between network fetches only**, never after a cache hit, never before the
first. `get` / `sleep` / `rng` stay injected — tests make no network calls.

Cooldown follows `fetch_x_batch`'s discipline exactly: sleep `rng.uniform(min_delay,
max_delay)` **between network fetches only**, never after a cache hit, never before the
first. `get` / `sleep` / `rng` stay injected — tests make no network calls.

### 4. Default ON, with `--no-thread`, and visible in the digest

Walking is **on by default** on the batch/CLI path. Three reasons: it is a **no-op for
non-threads** (no `in_reply_to_status_id_str` ⇒ terminates at hop 0, zero extra requests);
default-off would perpetuate the 8% context loss above; and the operator's bookmarking
rule now depends on it.

It is not silent: `/ingest-x`'s existing human review gate must show, per bookmark, the
**recovered post count and the root→leaf ordering**, plus any health note from §2. A
reviewer who cannot see that a bookmark expanded into 5 posts cannot review it.

### 5. Call-time attribution — the one place a thread changes meaning

A thread's posts carry **different timestamps**, so "the post's time" stops being
well-defined. The chain therefore keeps **per-post `id` + `post_ts_utc`** rather than being
flattened into one blob, and:

- If the extractor attributes an item to a specific post, the call time is **that post's**
  timestamp.
- If it cannot, fall back to the **leaf** (the bookmarked post) timestamp and record
  `calltime_source`.

Leaf, not root, because it is the **conservative** choice — the latest time gives the call
the *shortest* forward window and cannot flatter a score. This mirrors the existing
discipline that call time is resolved in code, never by the model doing date arithmetic.

## Verification

Every leg below is a test that must exist. Repo convention: injected `get`, no network,
`MagicMock`/fakes passed directly.

| # | test | what it proves |
| --- | --- | --- |
| 1 | `test_walk_follows_chain` — **assert the ordered sequence of requested URLs** | It actually walks. An output-only assertion passes for a walker that returns the leaf twice |
| 2 | `test_walk_stops_at_root` | Absent `in_reply_to_status_id_str` terminates |
| 3 | `test_walk_stops_when_author_changes` — **inject a differing `screen_name` mid-chain** | The §2 rule-2 guard fires. Proven by injecting the violation, not by reading the code |
| 4 | `test_walk_hop_cap_is_reported` | Truncation is in the notes, not silent |
| 5 | `test_walk_broken_chain_returns_partial` | A tombstoned middle hop degrades, never raises |
| 6 | `test_walk_reuses_cached_ancestor_without_network` | Per-id cache **read** reuse holds (see §3 amendment) |
| 7 | `test_walk_sleeps_between_network_hops_only` | Cooldown discipline matches `fetch_x_batch` |
| 8 | `test_pre_change_cache_entry_is_now_a_miss` — build `XPost(**raw)` from a dict **missing all four new keys and any `cache_schema` stamp** | **Update (Task 4b):** inverted — asserts `_load_cached(...) is None`. A pre-change entry must now MISS and re-fetch, not load with the new fields defaulted; see `_CACHE_SCHEMA` in `tools/x_fetch.py` |
| 9 | `test_main_thread_flag_emits_chain` | `--thread` returns the chain as JSON |
| 10 | `test_main_thread_human_shows_recovered_count` | The recovered count is printed — §4 review visibility |

**Implemented 2026-08-10, all 10 green** (33 in `tests/test_x_fetch.py`, 23 pre-existing
unchanged). Deviation from this spec: §3 cache-write, amended above with its reason.

**Test 3 and test 8 are the two that would actually catch a regression**; the rest are
shape checks. Do not drop them for being awkward to set up.

Gate, per the project Definition of Done: `make lint-py`, `make typecheck`, `make test`.
**`make test-regression` does not apply** — the diff touches `tools/` and `tests/`, not the
backtest surface. Say which branch was taken.

## Out of scope

- **Downward walking (root → replies).** Not possible on this endpoint; needs the paid
  API. Do not attempt a workaround.
- **Other people's replies** to the author's thread, including agreement or rebuttal.
- **Backfilling the 13 already-ingested fragment posts** — separate task.
- **Quote chains.** `quoted_tweet` is already handled and is a different relation.
- **Threads carrying video.** Unchanged: `video_present` still routes to `/ingest-video`
  (ST16).
- **Any change to detectors, scoring, or `passes_gate`.** This is ingestion only.

## Known-unverified, stated deliberately

- Depth >2 hops is **untested** (sample: 2 threads). The hop cap and the truncation note
  exist because of this, not despite it.
- Rapid sequential hops may hit rate limiting the single-fetch path never saw. The
  cooldown is inherited unchanged; if 429s appear, that is new information, not a bug in
  this design.
- **The operator cannot prove a bookmarked post is the tail.** If the author appends
  later, the bookmark is mid-thread and the tail is silently missed. No code fixes this —
  it is a bookmarking-time judgement, and it belongs in the skill's operator notes.
