---
name: ingest-video
description: >
  Ingest one or more YouTube or X video URLs, Chinese-language video included,
  into the research pipeline: transcript and chart frames are read, each item is
  classified, and after one human review gate for the whole batch it is routed to
  the thesis inbox, the mechanics backlog or the pundit-calls ledger, plus a
  per-video note. Invoke when the user says "/ingest-video", pastes YouTube or X
  video URLs, or asks to ingest a video.
allowed-tools: Bash, Read, Write, Edit, Task
---

# Ingest video(s)

Spec: `docs/superpowers/specs/2026-07-28-ingest-video-design.md`. Sibling of `/ingest-x` —
same batch → sonnet subagent per item → ONE digest → ONE approval → route shape, reusing
`tools/x_route.py::route_target` unchanged. What differs: transcript-driven frames, two
passes (text, then vision), and call time resolved in code.

**Goal:** each video's routable items reach the right sink, credited to whoever made the
call, at a call time no model computed, after ONE approval for the batch. Collect every
pasted URL (YouTube or X video) and run the flow once. **Run order:** steps 1 → 10; nothing
touches a sink before step 8, and step 10 gates the report.

This file holds the steps, the condensed rules and the **Gotchas**; the full text of every
rule and the incidents behind it are in `references/`, verbatim from the pre-split file.
**Read the Gotchas before step 1.**

## References — open each one when its trigger fires

| File | Open it |
| --- | --- |
| `references/subagent-prompts.md` | **every run**: the pass-1 and pass-2 return schemas and the classification rubric, pasted verbatim |
| `references/fetch-and-transcript.md` | at step 1 (the batch-split snippet); when `transcript_source` is `asr_whisper*`; on a shape-2 video |
| `references/sibling-repo-check.md` | when the step-2b grep hits the wifey fork and you must pick a repo |
| `references/pass-1-extraction.md` | before writing a pass-1 prompt; when `hint` says `matched: false`; when a kept set looks wrong |
| `references/call-time.md` | when pass 1 returns any stated time; when a quote and its timestamp disagree |
| `references/relay-attribution.md` | whenever a kept `setup` is a relay; when the roster returns anything but `mapped` |
| `references/frames.md` | at step 5 (the extraction snippet); before diagnosing any media `HTTP 403` |
| `references/pass-2-vision.md` | before skipping pass 2, changing its agent type, or when it returns extra items |
| `references/pass-2-field-rules.md` | before writing the pass-2 prompt; when `entry`/`stop`/`direction`/`horizon`/`frame_path` looks off |
| `references/digest.md` | before the first digest of a session; when a dedup check returns candidates, or nothing on Stream B |
| `references/routing.md` | before the first Stream C write of a session; when `target`/`entry` holds a range or parenthetical |
| `references/note-and-reconcile.md` | at step 9 (the transcript-append snippet); when `route_reconcile.py` reports a finding |
| `references/guardrails.md` | when a guardrail seems to conflict with a step; before changing a cap, the model or the recap rule |

## Flow

### 1. Fetch the whole batch in ONE call

```bash
PYTHONPATH=. poetry run python tools/video_fetch.py <url1> <url2> … --json \
  > .cache/video/_batch.json
```

**Redirect to a file — never let the batch JSON print into your context.** Then run the
split snippet in `references/fetch-and-transcript.md`, which writes each transcript to
`.cache/video/<video_id>/transcript.json` and prints a compact index to work from. No
`--batch` flag; trust each element's `url`, not array position; `frame_paths` is **always
`[]` here**. A caption-less video needs `GROQ_API_KEY`.

**Carry `transcript_source` into the note frontmatter verbatim (step 9).** Treat a
`raw_quote` from an `asr_whisper` transcript as **quoted-with-uncertainty**, and say so in
the digest when a number in it is decision-changing. ⛔ **`asr_whisper_captions_missed` is
recoverable — re-run the fetch for that video** rather than accepting the note, and say so in
the digest's health notes.

### 2. Distinguish the three `unavailable` shapes — they are NOT the same

| Shape | `meta` | `unavailable` | Action |
| --- | --- | --- | --- |
| 1 | `null` | `"<reason>"` | Video unreachable. Tell the user by URL, drop it, continue. |
| 2 | `{...}` | `"<reason>"` | No transcript: no captions + no `GROQ_API_KEY`, a Groq failure, or **media download blocked** (HTTP 403). Name the video and **which of the three** in the health note; skip it. A media block → check the yt-dlp pin (step 5) before spending subagent tokens. |
| 3 | `{...}` | `null` | Fully usable — proceed. |

Only shape-3 videos continue.

### 2b. Sibling-repo check — before spending any subagent tokens

```bash
grep -rl -E 'video_id: *"?(<id1>|<id2>|…)"?' \
  ~/repo/buibui-wifey-wall-street-bot/docs/plans/video-notes/ \
  docs/plans/video-notes/ 2>/dev/null
```

Grep **both** directories, on **frontmatter**, never filenames. A hit in **this** repo → skip
outright. A wifey hit is not automatically a skip — **route by subject, never by repo
priority**: crypto instrument → here; equities / macro / gold / oil / DXY / bonds → wifey; one
video covering both legitimately yields rows in both repos.

### 3. Pass 1 — text-only subagent, one per video, pinned to sonnet

Per video, read the channel's two knobs (local config, no API key):

```bash
PYTHONPATH=. poetry run python tools/yt_feed.py hint --author "<meta.author>"
```

`matched: false` or `intro_recap_s: 0` = no recap rule; `item_cap` defaults to
`video_marks.ITEM_CAP` (5). Then compute the per-video recap window from `meta.chapters`:

```bash
PYTHONPATH=. poetry run python -c "
import json,sys
from tools.video_fetch import Chapter, recap_window_s
ch = tuple(Chapter(**c) for c in json.load(sys.stdin))
print(recap_window_s(ch))
" <<< '<meta.chapters as JSON>'
```

A **positive** result REPLACES `intro_recap_s` for that video, in both directions; **`0.0`
means fall back to `intro_recap_s`**.

Dispatch via Task with **`model: "sonnet"`** (never inherit Opus) and
**`subagent_type: "Explore"`**, told to **Read the transcript file in full, not sample it**.
Give it: the `transcript_path` (**the path, never the segments**); `meta.publish_ts_utc`,
`meta.author`, `meta.lang` as **context only** — it must NOT compute a final call time; the
recap window and `item_cap` as **literal numbers**; the rubric and the pass-1 return schema
from `references/subagent-prompts.md`. It must NOT read any repo, SoT or memory file.

- **`stated_ts_utc` carries an explicit UTC offset or is `null`** — never a bare local time,
  never a guessed UTC.
- **Ask explicitly for a per-candidate `item_stated_ts_utc`** (relays: 「昨天下午」) under the
  same offset rule, plus **`item_stated_ts_raw`, mandatory whenever that field is non-null** —
  verbatim, speaker's language, never translated. **Do not fix a missing one downstream.**
- **When a quote and its resolved timestamp disagree, emit the quote and let the tool
  decide; never reconcile them in the prompt.**

**Filter for ATTRIBUTION and SUBJECT BEFORE applying the cap.** Instruct, in this order:

1. **Drop `setup` candidates inside the intro-recap window — they are past calls.** If the
   window is non-zero, set `is_intro_recap: true` on every candidate with `ts < window`; a
   `setup` also gets `retrospective: true`. A `claim`/`mechanic` gets only the flag and is
   kept — the digest must show it.
2. **Relayed `setup` candidates are kept ONLY if the roster resolves the originating name.**
   `is_relay: true` when the speaker relays **someone else's** call, with that name
   **verbatim** in `originating_author` — never normalised or corrected. Own calls:
   `is_relay: false`, the channel's handle. **`claim` and `mechanic` are exempt.**
3. **Drop non-crypto `setup` candidates** — they belong to wifey (step 2b).
4. **`mechanic` candidates stay eligible regardless of symbol.**
5. **Then** rank by `specificity` descending and keep the top `item_cap` — **the number
   `hint` returned for THIS channel, written as a literal.** Never let the subagent infer
   it; never hard-code 5.

Report every dropped candidate with a one-line reason naming its cause (recap window, relay by
<name>, non-crypto, below this channel's `item_cap`). **Filtered items belong in the note
too**, relays verbatim with their `originating_author`.

**`author` on a relay row is the roster-RESOLVED handle, never the raw extracted name.** The
roster is WIRED (`config/pundit_roster.toml`, schema in the committed `.example`) — **do not
rebuild it.** **Treat `confidence = "operator"` entries as unverified** and say so in the digest.

### 4. Resolve the call time deterministically — never in the prompt

```bash
PYTHONPATH=. poetry run python tools/video_calltime.py \
  --publish <meta.publish_ts_utc> \
  [--stated <stated_ts_utc>] [--date-only] \
  --stated-raw "<stated_ts_raw>" \
  --ingested <now, UTC ISO-8601>
```

- Omit `--stated` when pass 1 returned `null` — never pass the string `"null"`.
  `--date-only` only when `stated_date_only` was `true`. `--stated-raw` always (empty is fine).
- `--ingested` = `` $(date -u +%Y-%m-%dT%H:%M:%SZ) ``, **captured ONCE per batch and reused
  verbatim** for every `ingested_ts_utc` (steps 8 and 9) — never call `date -u` again.
- An empty `meta.publish_ts_utc` raises `ValueError`: skip that video with a health note.

It prints `call_ts_utc`, `call_ts_source` (`stated|publish|publish_relay`), `publish_ts_utc`,
`stated_ts_utc`, `stated_ts_raw`, `backlog` — **use these six verbatim** in the digest, the
ledger line and the note. `stated_ts_utc` is the tool's INPUT echoed back — keep it.

#### 4a. Resolve a relayed call's ORIGINATING AUTHOR

```bash
PYTHONPATH=. poetry run python tools/pundit_roster.py "<originating_author>"
```

- `mapped` — **route it**: `author` = returned `handle`, `relayed_by` = this channel's handle
  **verbatim as in `config/youtube_channels.toml`, leading `@` included**,
  `attribution: "relay"`, `attribution_confidence` = returned `confidence`.
- `ambiguous` — **surface it in the digest for manual attribution**, listing `members`.
  Default if unactioned: **drop**.
- `unmapped` / `unknown` — set `unattributable=True` and **list it under UNRESOLVED NAMES**,
  keeping the two outcomes distinct.

**Never resolve by eye** — only the roster's exact-alias match counts.

#### 4b. Resolve a relayed call's TIME separately from the video's

```bash
PYTHONPATH=. poetry run python tools/video_calltime.py \
  --publish <meta.publish_ts_utc> --relay [--stated <item stated_ts_utc>]
```

Add `--stated` only when pass 1 returned a per-candidate time, and with it the **per-item**
`item_stated_ts_raw` as `--stated-raw`, never the video-level quote. `--relay` labels the
fallback only (`publish_relay`).

#### 4c. Check a relay against its ORIGINATING author's own rows

```bash
PYTHONPATH=. poetry run python tools/route_dedup.py check \
  --source-id <video id> --item-ts <t> --sink docs/plans/pundit-calls.jsonl \
  --text "<the call>" --author <resolved handle>
```

`semantic_scope` reads `same-author`. Different authors stay exempt.

### 5. Select and extract frames

Run the extraction snippet in `references/frames.md`, substituting exactly `VIDEO_ID` and
`ITEM_TS` (the kept items' `ts`). **Do NOT write a `marks_input.json` with the Write tool.**
Frames land at `.cache/video/<video_id>/frames/f_NNNN.jpg`; `select()` caps at `FRAME_CAP`
= 15 and always yields safety-sample marks (`SAFETY_SAMPLE_S`, 300s) when `duration_s > 0`.

**`marks` non-empty but `frame_paths` empty is a download failure, not "no chart."** **Re-run
the step once by hand**, and only if it is empty again write the health note "frame
extraction failed (media download error)". **A 403 that survives the hand re-run is a
DEPENDENCY defect** — stop re-running and run the canary,
`poetry run python -c 'from tools.media_probe import probe_media_leg; print(probe_media_leg().detail)'`,
then compare the version against the pin in `pyproject.toml`. Diagnose with a plain
`yt-dlp -f 251 <url>`, **never `--download-sections`**. Do NOT record `chart_present: false`
for a download failure — that flag is reserved for step 6.

### 6. Pass 2 — vision subagent, one per video, pinned to sonnet

**Non-empty `frame_paths` is the PRECONDITION, not the trigger — decide by CONTENT, per video.**
**Run** pass 2 when kept items turn on numbers read off a chart. **Skip** it for narrative /
talking-head content: every item `vision_confidence: "low"`, `frame_path` = the nearest path
**from `frame_paths`**, health note "vision skipped (narrative content)", and
`chart_present: null   # vision skipped - nobody looked; NOT a claim that no chart exists`.
⚠ **Only skip when the kept set has NO `claim` items.** **When in doubt, run it.**

Empty `frame_paths`, no dispatch: `marks` also empty → every item `low`, `frame_path: null`,
`chart_present: false`; `marks` non-empty → the same item fields with step 5's health note
instead of `chart_present: false`.

Otherwise dispatch **`model: "sonnet"`**, **`subagent_type: "general-purpose"`** —
**deliberately not `Explore`; A/B it on ONE video before switching.** Give it `frame_paths`
(it Reads each), the `transcript_path` (**the path**), the kept items (`ts`, `content_type`,
`gist`), and the rubric and pass-2 return schema from `references/subagent-prompts.md`. It
must not read any repo, SoT or memory file. **The item set is CLOSED** — instruct: *"Return
EXACTLY the kept items you were given. Do not add, split, merge or discover new ones — the cap
was already applied in pass 1."* Then **assert `len(items) <= item_cap` yourself.**

**Paste the pass-2 field rules from `references/subagent-prompts.md` into the prompt.** The
ones that cost rows when broken: **a level named as the entry CONDITION IS the `entry`**; **a
contingent stop-management instruction is NOT a `stop`**; **`direction` ∈ `long`/`short`/
`neutral`** and **`horizon` ∈ `intraday`/`swing`/`unspecified` or omitted**; **the chart wins
only on a DRAWN value**; **`frame_path` is COPIED from the supplied list, never built from `ts`**.

**`confidence` and `vision_confidence` are separate columns — never merge them.**

### 7. ONE consolidated digest for the whole batch

Four parts, **in this order** — the order is load-bearing:

**7a — What each pundit said.** The pass-1 `summary` per video, ABOVE the table, one short
paragraph each — never buried, never compressed to a clause.

**7b — The board view.** 3-6 sentences across videos: who is long / short / neutral and who
is already **positioned**; levels two or more name independently; one structure read two
ways; a "dropped but worth knowing" list. **No extra subagent. READ-ONLY and outside the
routing path** — no sink, no effect on `content_type` / `verdict` / dedup, never the daily
Brief: **information, not evidence.**

**7c — The routing table.** One row per kept item: video (title) · author · `call_ts_utc`
(`call_ts_source`) · `ts` · `content_type` · `retrospective` · `is_intro_recap` · `rejected` ·
`verdict` · proposed routing · `vision_confidence`; below it, per video, dropped candidates,
`chart_present`, `backlog`, and shape-1/2 skips. **Write nothing yet.** A relay row with
`author: <the channel>` is the bug — flag it. Write `attribution` (`first-hand`/`relay`) and
`attribution_confidence` (`high`/`operator`) **verbatim from the roster**, plus `relayed_by`.

**7d — UNRESOLVED NAMES (mandatory — never omit, never abbreviate).** Every unresolved
`originating_author`, verbatim, with video and timestamp, grouped `ambiguous` (with
`members`) vs `unmapped`/`unknown`. **Print it even when empty**, as `UNRESOLVED NAMES: none`.

**For every `call_ts_source == "stated"` video, also print `stated_ts_raw`, `publish_ts_utc`
and the delta between them.**

**Run the dedup check before printing**, once per non-dropped item, keyed on its own `ts`:

```bash
PYTHONPATH=. poetry run python tools/route_dedup.py check \
  --source-id <meta.video_id> --item-ts <item ts> --sink <route_target output> \
  --text "<the gist being routed>"
```

`already_routed: true` → **do not append**. Non-empty `candidates` → **not a block**: print
each `excerpt` and `shared_levels`; the user picks new row, corroboration line or drop.
**Report `semantic_scope`.** ⚠ **On Stream B an empty `candidates` list is "nothing scored
above threshold", never "not a duplicate"** — and `--author` is inert there.

Then, per video with two or more Stream-C-bound items, write the pending pass-2 item dicts to
a scratch file and run the **advisory** intra-video pass; print every pair it returns:

```bash
PYTHONPATH=. poetry run python tools/route_dedup.py pairs \
  --items .cache/video/<video_id>/pending_calls.json
```

### 8. Route on a single approval

After approval, compute each destination with
`tools/x_route.py::route_target(content_type, verdict, retrospective=…, rejected=…)` — do
not fork it:

| content_type | verdict | Append to |
| --- | --- | --- |
| setup (`retrospective: false`, `rejected: false`) | — | `docs/plans/pundit-calls.jsonl` (one JSON line, schema below) |
| setup (`retrospective: true`) | — | **drop** — "retrospective — call predates video"; digest + note only |
| setup (`rejected: true`) | — | **drop** — "rejected — speaker argued against taking it"; digest + note only |
| mechanic | — | `docs/plans/mechanics-backlog.md` (a `-` list bullet) |
| claim | NOVEL | `docs/plans/thesis-inbox.md` (a draft `H` row) |
| claim | ALREADY-TESTED / FROZEN-CATEGORY / NOT-FALSIFIABLE | **drop** — state "seen, verdict X", write nothing |

**Pass both flags to `route_target` — do not hand-apply the two setup rows.** Create a
missing sink with a one-line header; report one line per item. **After each successful
append** — never before, never for a dropped item:

```bash
PYTHONPATH=. poetry run python tools/route_dedup.py mark \
  --source-id <meta.video_id> --item-ts <item ts> \
  --sink <the FULL path route_target returned, e.g. docs/plans/pundit-calls.jsonl>
```

- **Stream C requires a real `symbol`**; `null`/`""` → dropped ("no symbol resolved").
- **Deep links are separator-aware:** URL contains `?` → append `&t=<ts>s`; otherwise
  (`youtu.be`) `?t=<ts>s`. X video gets **no** deep link — plain URL, offset in `ts`.

**Stream C line** (one JSON line, extends the `/ingest-x` schema):

```json
{"source":"youtube","author":"<handle>","attribution":"first-hand|relay","relayed_by":"<relaying channel handle, relay rows only>","attribution_confidence":"<roster confidence, relay rows only>","url":"<url, with the deep link above for youtube>","ts":252.0,"call_ts_utc":"<resolved call time>","call_ts_source":"stated|publish|publish_relay","publish_ts_utc":"<publish time>","stated_ts_utc":"<the stated time step 4 was GIVEN, or null>","stated_ts_raw":"<verbatim quote or empty>","ingested_ts_utc":"<now>","backlog":false,"symbol":"...","direction":"...","entry":"...","stop":"...","target":"...","horizon":"...","confidence":"","vision_confidence":"high|medium|low","raw_quote":"<original language>","raw_quote_en":"<english>","corrected_from":"<transcript's original value, or empty>"}
```

- **⚠ `target` and `entry` are MACHINE-PARSED.** `target` is a bare `/`-separated ladder
  (`67,000 / 70,362.23 / 82,000`) — ranges go in `raw_quote`, and **no parenthetical**.
- **`author` is the person who MADE the call, never the reporting channel.** `source` =
  `meta.source` verbatim. **`confidence` is always `""`** on a video row.
- **Run `make buibui-pundit-score` as the last action of the round** to see the parse.

### 9. Write the per-video note

One file per video at `docs/plans/video-notes/<date>-<author-slug>-<video_id>.md`, `<date>` =
the ingest date (UTC):

```bash
# Named variables only, never a positional: the harness substitutes positionals in a
# SKILL.md body when the skill is called with args (why: references/note-and-reconcile.md).
author_slug=$(printf '%s' "$AUTHOR" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9' '-' | sed -E 's/^-+|-+$//g' | cut -c1-40)
note_path="docs/plans/video-notes/$(date -u +%F)-$author_slug-$VIDEO_ID.md"
```

**`video_id`, not a title slug — the rule, not a collision fallback.** An empty author
segment from a CJK handle is correct; do not "fix" it.

- **Frontmatter:** `source`, `video_id`, `url`, `author`, `title`, `duration_s`, `lang`,
  `publish_ts_utc`, `call_ts_utc`, `call_ts_source`, `stated_ts_utc`, `stated_ts_raw`,
  `ingested_ts_utc`, `backlog`, `chart_present`, `transcript_source`,
  `caption_langs_manual`, `caption_langs_auto`, `route`. Copy `caption_langs_*` from step 1
  verbatim (none = `[]`); write `transcript_source` from step 1's JSON, never from memory;
  `route` = the sink path(s), comma-separated, or `dropped` — do not retrofit old notes.
- **Body:** the pass-1 `summary`; an items table (`ts` · `content_type` · `retrospective` ·
  `rejected` · `verdict` · routing outcome · `vision_confidence` · `frame_path`); dropped
  candidates with reasons; every extracted frame (path + `ts`); the transcript, **not
  proofread** — only routed items carry a reviewed `raw_quote`/`raw_quote_en`.

Write everything above the transcript with Write, then **append the transcript with code**
using the snippet in `references/note-and-reconcile.md` — never retype it.

### 10. Reconcile the round before you report it — a GATE, not a summary

```bash
PYTHONPATH=. poetry run python tools/route_reconcile.py docs/plans/video-notes/<date>-*.md
```

Exit 1 = a declared route did not happen: fix it and re-run before telling the operator the
round landed. Same tool and verdicts as `/ingest-x` step 6; **only the sink is evidence.** A
multi-sink note lists every sink in `route:`. It checks SINKS, not counts — check the per-item
count in the step-9 items table by eye.

## Guardrails

Full text: `references/guardrails.md`. Never write a stream before the user approves the
digest. `call_ts_utc` never comes from model arithmetic. Output is something to TEST, never
"add a detector" (the 22-strategy list is frozen). `route_dedup` never auto-drops a
near-duplicate. Declined, recap and unresolvable-relay setups are not calls — drop them via
`route_target` flags, never by eye; `intro_recap_s` is opt-in, so set it on a new recap-style
channel before its first ingest. Both passes stay on sonnet. `FRAME_CAP` (15) and `ITEM_CAP`
(5) change **globally** only as a deliberate spec change; a per-channel `item_cap` is the
sanctioned exception and must reach the pass-1 prompt.

## Gotchas

Each is a rule a past run broke; the incident is in the reference named beside it.

**Steps 1–2b** (`fetch-and-transcript.md`, `sibling-repo-check.md`)

- ⚠ A shape-2 **media-download 403 is ENVIRONMENTAL and batch-wide** — it kills captions,
  the whisper fallback AND frames together. `captions_unknown` = "we did not ask", not ASR.
- Note filenames are not comparable across the two repos. Neither repo is a safe default for
  the other's subject — wifey's ETF proxies make **oil fail quietly**.

**Steps 3–4c** (`pass-1-extraction.md`, `call-time.md`, `relay-attribution.md`)

- ⚠ **`item_cap` is applied by the PROMPT, not by code** — a value fetched and not passed on
  does nothing. A channel with no `handle` silently gets no recap rule and a cap of 5.
- ⚠ `specificity` alone **inverts an aggregator video** — its top-N were all relays, twice.
- ⚠ Pass 1 silently "fixes" a contradictory stated date and attaches the wrong phrase to a
  relay about a third of the time; only the raw quote on the row lets a human see it.
- ⚠ **Never write the RAW `originating_author` into `author`**, and never add a parallel
  `originating_author` column instead — both leave relays mis-credited with no check able to see it.
- ⚠ `route_target`'s `retrospective` drop is setup-only, so a recap `mechanic`/`claim` has
  no code path stopping it — the digest flag is the whole control.
- ⚠ `relayed_by` without the `@` splits the channel on any `GROUP BY relayed_by`.

**Step 5** (`frames.md`)

- ⚠ A media 403 has TWO classes — transient, and persistent / version-caused (the yt-dlp
  build); the hand re-run separates them. `--download-sections` 403s even when a plain
  download succeeds.

**Step 6** (`pass-2-vision.md`, `pass-2-field-rules.md`)

- Unconditional vision is what makes a five-video batch unaffordable (~71% of subagent tokens).
- ⚠ Skipping pass 2 on a video with a `claim` leaves it unroutable — `verdict` comes only
  from pass 2. ⚠ `chart_present: false` on a skipped video claims a look that never happened.
- ⚠ "No repo files" bounds what pass 2 READS, not what it knows — settle the vector with
  throwaway probes before building anything.
- ⚠ entry == stop scores an instant 0.00R loss; no numeric guard replaces the judgement.
- ⚠ An out-of-enum `direction` or `horizon` makes the scorer skip the row; prose in `entry`
  is safe on `neutral` rows ONLY. Never route an already-dropped item retroactively.

**Step 7** (`digest.md`)

- ⚠ An out-of-enum `attribution`/`attribution_confidence` skips the row; omitting
  `relayed_by` too books a relay as first-hand.
- UNRESOLVED NAMES is the only way the roster grows; omitted, it looks like a clean round.
- The stated-vs-publish delta is the only runtime control on a stated time, which may move
  `call_ts_utc` up to `STATED_TS_MAX_LEAD_H` (168h) before publish.

**Step 8** (`routing.md`)

- ⚠ **A bare filename `--sink` is REJECTED** — pass one of the three full paths.
- ⚠ A hyphenated range anywhere in `target` beats the ladder and parses to its LOW end.
- `mark` at check time lets an abandoned review consume the id (watermark-on-send).

**Steps 9–10** (`note-and-reconcile.md`)

- Pre-2026-08-26 notes have no `route:` and reconcile as `undeclared` — a finding. A partial
  `route:` declaration reconciles clean while hiding the missing half.
