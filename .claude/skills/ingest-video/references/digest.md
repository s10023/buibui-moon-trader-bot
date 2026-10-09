# Step 7 — the consolidated digest and the dedup checks

**When:** Read before printing the first digest of a session, and when a dedup check returns candidates or an empty list on Stream B.

Reference for `.claude/skills/ingest-video/SKILL.md`, which holds the run order, the steps and the condensed rules. This file holds the full text, including the dated incidents behind each rule. The passages below are carried over verbatim from the pre-split SKILL.md (#884), so where one says "this doc", "this document", "above", "below" or names a step, it means SKILL.md.

## Step 7 — one consolidated digest for the whole batch

The digest has **three parts, in this order**. The order is load-bearing, not cosmetic —
see "Why summaries come first" below.

**7a — What each pundit said. The pass-1 `summary` per video, ABOVE the table.** One
short paragraph each, in the operator's reading order (freshest first is fine). Do not
bury these under the table and do not compress them to a clause: this is the part of the
digest a human reads to *learn what was said*, and it is the only place the unroutable
substance of a video survives at all.

**7b — The board view. Cross-video synthesis, in prose, 3-6 sentences.** Streams A/B/C
are per-item sinks, so agreement and disagreement *between* pundits exist only in the
relationship between rows and are recorded nowhere. Surface at minimum:

- who is directionally long / short / neutral, and who is already **positioned** (a
  pundit managing an open trade is not a neutral observer of it);
- **levels two or more of them name independently** — the strongest signal the batch
  carries, and invisible per-item;
- where they read the *same* structure and draw opposite conclusions;
- a short "dropped but worth knowing" list: items cut for being unscoreable or below the
  `ITEM_CAP` specificity cut are frequently the most informative in the batch, and the
  drop reason says nothing about how interesting they were.

Build this yourself from data already in hand — **no extra subagent, no extra tokens.**

**The board view is READ-ONLY and strictly outside the routing path.** It must not write
to a sink, must not influence `content_type` / `verdict` / dedup, and must not feed the
daily Brief. A narrative digest of pundit opinion is exactly the thing that can become a
trading input without ever earning a track record, which is what `tools/pundit_score.py`
exists to prevent. It is **information, not evidence** — say so if it is ever quoted back
as a reason to take a trade.

**7c — The routing table.** One row per kept item across every video: video (title) ·
author · `call_ts_utc` (`call_ts_source`) · `ts` · `content_type` · `retrospective` ·
`is_intro_recap` · `rejected` · `verdict` · proposed routing · `vision_confidence`.

**`is_intro_recap: true` on a `claim`/`mechanic` must be visible in this table**, since no
code will drop it — the human deciding is the entire mechanism there (step 3, rule 1).
Below the table, per video: the dropped candidates with their reasons, the `chart_present`
flag, and `backlog` when `true`. List any shape-1 / shape-2 videos separately with their
skip reason. **Write nothing yet.**

**A relayed row shows `author` = the RESOLVED handle, and `relayed_by` beside it**, plus
`attribution_confidence`. A row reading `author: <the channel>` with `attribution: relay`
is the exact bug this pipeline was paused for — flag it rather than routing it.

**Both fields are VALIDATED ON READ since 2026-08-08** (`analytics/pundit_attribution.py`,
consumed by `tools/pundit_score.py`), so they are no longer free text the scorer ignores:

- `attribution` ∈ {`first-hand`, `relay`} · `attribution_confidence` ∈ {`high`,
  `operator`}, matching `config/pundit_roster.toml`'s own vocabulary. **A present but
  out-of-enum value makes the scorer skip the row with a warning** — the call is lost
  permanently, same cost as a bad `direction` or `horizon`. Write the roster's value
  verbatim; do not invent `verified`, `medium` or `low`.
- **Omitting `attribution` on a relay row no longer buys full trust**: `relayed_by` is
  cross-checked and forces `relay`. But omitting `relayed_by` *too* silently books the
  row as first-hand, so keep writing both.
- A missing `attribution_confidence` on a relay row does not fail — it ranks *below*
  `operator` and is caught by `--min-attribution-confidence`. It still costs the row its
  standing, so write it.

**7d — UNRESOLVED NAMES (mandatory — never omit, never abbreviate).**

List every `originating_author` that did not resolve to `mapped`, with the video and
timestamp it came from and the **verbatim** string, grouped as `ambiguous` (awaiting a
manual call, listing `members`) vs `unmapped`/`unknown`.

**This is the only mechanism by which the roster grows.** Round 7 measured 2 of 5
originating names ASR-mangled — `输情`/`瞬间` → `舒琴`, recovered only from a Discord
channel title visible in a frame. New mangles arrive every round, miss the exact-alias
lookup by construction, and drop. If they are not listed here they are lost silently, and
the roster stops improving while continuing to look like it works.

Print the section **even when it is empty**, as `UNRESOLVED NAMES: none`. An omitted
section and a clean round are indistinguishable to the reader, and only one of them is
good news.

**Why summaries come first (2026-08-04, operator).** Part of the reason this pipeline
exists is to **receive what is in a video without watching it**. The routing streams do
not serve that goal by themselves — they capture only what can be *scored or tested*, so
everything else is dropped, and an operator reading a routing table alone "will never
learn anything that cannot be scored". Round 9 is the worked example: four pundits, one
shared 61-62k pivot, two of them reading the same head-and-shoulders bottom and
disagreeing on whether it completes — and not one stream recorded that shape.

**For any video where `call_ts_source == "stated"`, also print `stated_ts_raw`,
`publish_ts_utc`, and the delta between the stated and publish times (e.g. "stated
2026-07-14T08:00:00+08:00 vs publish 2026-07-14T22:10:00+00:00, Δ14h").** A stated time
can move `call_ts_utc` up to `STATED_TS_MAX_LEAD_H` (168h) earlier than publish, and this
digest — specifically the human looking at it — is the only runtime control on that
input; `call_ts_utc (call_ts_source)` alone does not show the approver the quote that
justified the shift, so they cannot judge it. Showing the raw quote and the gap is what
lets the approver reject a fabricated or implausible timestamp before it reaches the
ledger.

**Run the dedup check before printing the digest**, once per non-dropped item, so its
result appears *in* the digest rather than after approval. `--item-ts` is the item's own
`ts` — never omit it, and never key on the video id alone: one video legitimately yields
several items (`umX9m7y7jsU` produced calls at `t=162s` AND `t=886s`), and collapsing them
would delete real rows.

```bash
PYTHONPATH=. poetry run python tools/route_dedup.py check \
  --source-id <meta.video_id> --item-ts <item ts> --sink <route_target output> \
  --text "<the gist being routed>"
```

- `already_routed: true` → **do not append.** Show the item as "already routed" and route
  nothing for it in step 8. Exact match, no judgement needed.
- `candidates` non-empty → **not a block.** Print each candidate's `excerpt` and
  `shared_levels` under that item and let the user decide: new row, corroboration line on
  the existing entry, or drop. Bulk video ingest makes this the common case — a pundit
  routinely repeats one thesis across a week of uploads.
- `semantic_scope` says what the near-duplicate pass actually compared against:
  `all-entries` (Streams A and B), `same-source` (Stream C — only this video's own
  earlier rows, never another author's), or `none`. Report it; never let an empty
  `candidates` list read as "checked against everything and clean".

**⚠ On Stream B, read an empty `candidates` list as "nothing scored above threshold",
never as "not a duplicate" — and no flag fixes this.** Measured 2026-08-12g:
`zaWgINlnNcQ` ts 575.23 (@KoluniteVIP) is a near-verbatim restatement of
`mechanics-backlog.md:632` — same channel, same author, same mechanic, 4 days apart — and
**its own check returned `candidates: []`**. It surfaced only because an unrelated item's
check ranked that entry at 3.088.

**Scope was never the problem, which is why no fix is offered here.** Stream A/B are
`SEMANTIC_SINKS`, so `semantic_scope` already returns `all-entries` — the entire file. The
restatement was *already in scope* and still ranked below an unrelated item, making this a
**lexical ranking** limit. **Do not reach for `--author`: it is inert on Stream A/B** —
both `semantic_scope` (`tools/route_dedup.py:385`) and `_comparable_entries` (`:404`)
return before `author` is read. It is a Stream C affordance only (step 4c). The digest's
human gate is doing the real work here.

**Then run the intra-video pass, once per video that has two or more Stream-C-bound
items.** `check` cannot catch these: every check runs *before* the approval that writes
anything, so when a video's items are checked none of them are on disk yet — two legs of
one position are only findable item-vs-item. Write the video's pending Stream C items
(the pass-2 item dicts are enough — it reads `symbol`/`direction`/`entry`/`stop`/
`target`/`raw_quote*`/`ts`) to a scratch file, then:

```bash
PYTHONPATH=. poetry run python tools/route_dedup.py pairs \
  --items .cache/video/<video_id>/pending_calls.json
```

Print every returned pair under that video: both `ts` values, `score`, `shared_levels`,
and both excerpts. **Advisory, never a block** — two legs of one position and two
genuinely distinct calls on one symbol look alike by construction, and only the operator
knows which they are watching. Calibration on the 109-row ledger: 1 of 22 same-source
pairs flagged, and the known-legitimate `umX9m7y7jsU` pair (one video, two different
BTCUSDT longs) is correctly left alone.
