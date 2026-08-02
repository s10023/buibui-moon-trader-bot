---
name: ingest-video
description: >
  Ingest one OR MORE YouTube or X video URLs into the research pipeline in a single
  call — including Chinese-language video. Fetches metadata + transcript (yt-dlp
  captions, Groq whisper-large-v3 fallback) via tools/video_fetch.py, batched with a
  randomized cooldown + a per-video dedup cache so re-runs hit zero network, then
  runs TWO sonnet subagent passes per video: pass 1 (text-only) segments the
  transcript and ranks candidate items; pass 2 (vision) reads the transcript-selected
  frames (never scene-change — tools/video_marks.py) and produces chart-corrected
  item JSON. Call time is resolved deterministically in code (tools/video_calltime.py)
  — never by the model doing date arithmetic — preferring a stated in-video time but
  bounded below the publish timestamp. Classifies via the shared content-type gate +
  4-bucket verdict taxonomy and routes (after ONE human review gate for the whole
  batch) into the same three streams as /ingest-x: A hypotheses ->
  docs/plans/thesis-inbox.md, B mechanics -> docs/plans/mechanics-backlog.md, C daily
  setups -> docs/plans/pundit-calls.jsonl, plus a durable per-video note. Invoke when
  the user says "/ingest-video", pastes one or more YouTube or X video URLs, or says
  "ingest this video" / "ingest these videos".
allowed-tools: Bash, Read, Write, Edit, Task
---

# Ingest video(s)

Spec: `docs/superpowers/specs/2026-07-28-ingest-video-design.md`.

Sibling of `/ingest-x` (`.claude/skills/ingest-x/SKILL.md`) — same batch → per-item
sonnet subagent → ONE consolidated review digest → ONE approval → route shape, reusing
`tools/x_route.py::route_target` unchanged. This doc assumes the reader knows that
shape; it spells out in full only what differs: frame extraction driven by the
transcript (not scene-change), a two-pass split (text then vision), and deterministic
call-time resolution.

Handles **one or many** URLs (YouTube, or X video) in a single invocation. Collect
every URL the user pasted, then run the flow once over the whole set.

## Flow

### 1. Fetch the whole batch in ONE call

```bash
PYTHONPATH=. poetry run python tools/video_fetch.py <url1> <url2> … --json \
  > .cache/video/_batch.json
```

**Redirect to a file — never let the batch JSON print into your context.** A batch of 8
carries eight full transcripts; splitting them to disk and passing subagents a *path*
measured ~24k tokens saved on an 8-video batch and changed no output. The split below
prints a compact index only:

```bash
PYTHONPATH=. poetry run python - <<'PY'
import json
from pathlib import Path

index = []
for el in json.loads(Path(".cache/video/_batch.json").read_text()):
    row = {"url": el["url"], "cached": el.get("cached"), "unavailable": el.get("unavailable")}
    meta = el.get("meta")
    if meta:
        out = Path(".cache/video") / meta["video_id"]
        out.mkdir(parents=True, exist_ok=True)
        (out / "transcript.json").write_text(
            json.dumps({"meta": meta, "segments": el.get("segments", [])},
                       ensure_ascii=False, indent=2)
        )
        row |= {k: meta[k] for k in
                ("video_id", "author", "title", "duration_s", "lang", "publish_ts_utc")}
        row["n_segments"] = len(el.get("segments", []))
        row["transcript_path"] = str(out / "transcript.json")
    index.append(row)
print(json.dumps(index, ensure_ascii=False, indent=2))
PY
```

Every later step reads `.cache/video/<video_id>/transcript.json` (already gitignored,
alongside the fetch cache and the frames). You work from the index; the transcripts stay
on disk.

`tools/video_fetch.py` always batches (unlike `tools/x_fetch.py`, it has no separate
single-URL path, so there is no `--batch` flag to pass). Output is a JSON **array**,
one element per URL **in the position it was requested** (`url` on each element is
that position's URL, correct even on a cache hit — do not assume array order
otherwise). Per element:

- `url` — the URL actually requested at this position
- `cached` — bool; `true` = zero network, served from `.cache/video/<id>/`
- `meta` — `{source, video_id, author, title, publish_ts_utc, duration_s, lang, url}`,
  or `null` when the video itself was unreachable
- `segments` — `[{ts_s, text, lang}, …]` (empty when there is no transcript)
- `frame_paths` — **always `[]` at this stage.** This CLI fetches metadata + transcript
  only; frames are extracted later (step 5), from a separate Python call, only for the
  moments pass 1 decides are worth a frame. Don't expect frames here — that is not a bug.
- `unavailable` — `null`, or a string reason

A caption-less video needs `GROQ_API_KEY` in the environment (`.env`) to produce a
transcript at all; without it, `unavailable` reports that explicitly (see shape 2 below).

### 2. Distinguish the three `unavailable` shapes — they are NOT the same

| Shape | `meta` | `unavailable` | Meaning | Action |
| --- | --- | --- | --- | --- |
| 1 | `null` | `"<reason>"` | The video itself is unreachable (bad URL, deleted, private, yt-dlp failure). Nothing else is known. | Tell the user by URL, drop it from the batch, continue with the rest. |
| 2 | `{...}` | `"<reason>"` | Metadata resolved fine, but no transcript could be produced — either "no captions available and no GROQ_API_KEY configured", or a Groq failure (HTTP error, audio extraction, chunking). | Because `meta` is populated, name the video (`meta.author`, `meta.title`) in the health note, and say **which of the two reasons** it was. Skip it from pass 1 onward — there is no transcript to feed. |
| 3 | `{...}` | `null` | Fully usable. | Proceed. |

Only shape-3 videos continue through the rest of this flow.

### 3. Pass 1 — text-only subagent, one per video, pinned to sonnet

For each shape-3 video, dispatch a `general-purpose` subagent via the Task tool with
**`model: "sonnet"`** (do not inherit Opus) and `subagent_type: "general-purpose"`. Give
it:

- the video's `transcript_path` from step 1 — **the path, not the transcript.** Instruct
  it to Read that file; `segments` is the `"segments"` key inside it. Pasting the array
  into the prompt puts the whole transcript in your context for no gain, since the
  subagent has its own.
- `meta.publish_ts_utc`, `meta.author`, `meta.lang` — **context only**, for resolving a
  relative stated date ("last Monday") and inferring a speaker's timezone from channel
  locale. It must NOT compute a final call time itself — that happens in code, step 4.
- the inline classification rubric (below)

It must NOT read any repo, SoT, or memory file — the rubric is self-contained. Instruct
it to return ONLY this JSON:

```json
{
  "summary": "one paragraph, English",
  "stated_ts_utc": "ISO-8601 with an explicit UTC offset, or null",
  "stated_date_only": false,
  "stated_ts_raw": "verbatim quote or empty",
  "candidates": [
    {"ts": 252.0, "content_type": "setup|claim|mechanic", "specificity": 1-5, "gist": "..."}
  ]
}
```

**`stated_ts_utc` must carry an explicit UTC offset (e.g. `2026-07-14T08:00:00+08:00`),
or be `null` — never a bare local time.** `tools/video_calltime.py` rejects a naive
(offset-less) timestamp and silently falls back to publish time, so a subagent that
emits `2026-07-14T08:00:00` with no offset gets the same downstream result as emitting
nothing, just less honestly. Instruct the subagent: state the offset whenever the
speaker's timezone is inferable from context, otherwise emit `null` — never guess UTC.

Rank `candidates` by `specificity` descending. Keep the top `ITEM_CAP` (5 —
`tools/video_marks.py::ITEM_CAP`) as this video's kept items; report the rest as dropped,
with a one-line reason each (e.g. `specificity 2, below the top-5 cutoff`), for the
digest and the note.

### 4. Resolve the call time deterministically — never in the prompt

```bash
PYTHONPATH=. poetry run python tools/video_calltime.py \
  --publish <meta.publish_ts_utc> \
  [--stated <stated_ts_utc>] [--date-only] \
  --stated-raw "<stated_ts_raw>" \
  --ingested <now, UTC ISO-8601>
```

- Omit `--stated` entirely when pass 1 returned `null` — do not pass the literal string
  `"null"`.
- Pass `--date-only` only when `stated_date_only` was `true`.
- `--stated-raw` is always passed (an empty string is fine).
- `--ingested` is the current UTC time, e.g. `` $(date -u +%Y-%m-%dT%H:%M:%SZ) `` —
  needed so the tool can also compute `backlog`. **Capture this one value per batch and
  reuse it verbatim** everywhere `ingested_ts_utc` is written later (the Stream C line
  in step 8, the per-video note frontmatter in step 9) — do not call `date -u` again at
  those points; two separate calls could disagree by however long the batch took to
  process, and the field exists to say when THIS pipeline saw the video, not to be
  re-timestamped per write site.
- **If `meta.publish_ts_utc` is an empty string** (yt-dlp returned no timestamp field —
  rare, but possible), `video_calltime.py` raises `ValueError` rather than guessing.
  Treat that video as call-time-unresolvable and skip it with a health note; do not pass
  an empty string through.

Output (JSON to stdout):

```json
{
  "call_ts_utc": "...",
  "call_ts_source": "stated|publish",
  "publish_ts_utc": "...",
  "stated_ts_raw": "...",
  "backlog": false
}
```

Use these five fields **verbatim** in the digest, the ledger line, and the note. Never
have a subagent or the orchestrator derive `call_ts_utc` by date arithmetic — that is
exactly the look-ahead defect this tool exists to prevent (see Guardrails).

### 5. Select and extract frames

There is no CLI for this — `video_marks.select` and `video_fetch.extract_frames` are
library calls. The transcript is already on disk from step 1, so there are exactly **two**
placeholders to substitute per shape-3 video: `VIDEO_ID` and `ITEM_TS` (the kept items'
`ts` values from step 3's top-`ITEM_CAP` candidates — a short list of floats). Do NOT
write a `marks_input.json` with the Write tool; that pulled the whole transcript back
through your context to hand it to a script that can read it itself.

```bash
PYTHONPATH=. poetry run python - <<'PY'
import json
from dataclasses import asdict
from pathlib import Path

from tools.video_fetch import VideoMeta, extract_frames
from tools.video_marks import TranscriptSegment, select

VIDEO_ID = "<video_id>"          # e.g. "dQw4w9WgXcQ"
ITEM_TS = [<ts of each kept item from step 3>]   # e.g. [252.0, 886.0]

raw = json.loads((Path(".cache/video") / VIDEO_ID / "transcript.json").read_text())
meta = VideoMeta(**raw["meta"])
segments = [TranscriptSegment(**s) for s in raw["segments"]]

marks = select(segments, ITEM_TS, meta.duration_s)  # cap defaults to FRAME_CAP (15)
dest_dir = Path(".cache/video") / meta.video_id / "frames"
frame_paths = extract_frames(meta, marks, dest_dir)

print(json.dumps({"marks": [asdict(m) for m in marks], "frame_paths": frame_paths}, indent=2))
PY
```

Frames land at
`.cache/video/<meta.video_id>/frames/f_NNNN.jpg` (already gitignored, alongside the
fetch cache). `extract_frames` downloads the video once into that same frames
directory (ffmpeg cannot seek the web-page URL directly), seeks the local copy per
mark, then deletes the downloaded video — only the frame JPEGs persist. `select()` is
deterministic and caps at `FRAME_CAP` = 15; frames can come from deixis phrases and
spoken price levels even when `item_ts` is short or empty — a video with zero routable
candidates can still produce frames via those triggers or the `SAFETY_SAMPLE_S` (300s)
floor.

**`marks` non-empty but `frame_paths` empty is a download failure, not "no chart" —
keep the two apart.** `select()` returns at least the safety-sample marks for any
`duration_s > 0`, so an empty `marks` list only happens for a (rare) zero-duration
video. If `marks` came back non-empty here but `frame_paths` is still `[]`, the
video's media download failed (network error, age-gate, region block) — record that
video's health note as "frame extraction failed (media download error)". `extract_frames`
already retried the download-and-seek internally (3 attempts, short backoff) — but the
`HTTP 403` behind this is **intermittent and server-side**, so a built-in retry does not
exhaust it: round 4 hit two that survived the retry and **both cleared on a single manual
re-run**, one of them on the video carrying that batch's only complete
entry+stop+target row. So **re-run the step once by hand**, and take the health note only
if it comes back empty again. Do NOT record
`chart_present: false` for that case; that flag is reserved for step 6, where frames
WERE produced and pass 2 actually looked at them and found no chart.

### 6. Pass 2 — vision subagent, one per video, pinned to sonnet

Dispatch whenever `frame_paths` from step 5 is non-empty — independent of whether step 3
found any candidates (see note above). When `frame_paths` is empty, which health note
you write depends on step 5's `marks` distinction:

- `marks` was also empty (a zero-duration video — rare): skip pass 2, treat every kept
  item from pass 1 as `vision_confidence: "low"`, `frame_path: null`, and record
  `chart_present: false` for that video in the digest and note.
- `marks` was non-empty (the ordinary empty-`frame_paths` case): this is the step-5
  download failure, not "no chart" — and only after step 5's hand re-run also came back
  empty. Skip pass 2, still mark every kept item
  `vision_confidence: "low"` / `frame_path: null`, but write the step-5 health note
  ("frame extraction failed (media download error)") instead of `chart_present: false`
  — you never actually looked, so don't claim you did.

No subagent dispatch in either case.

Otherwise, dispatch a `general-purpose` subagent, **`model: "sonnet"`**,
`subagent_type: "general-purpose"`. Give it: the `frame_paths` list (it Reads each one —
vision), the `transcript_path` from step 1 (**the path** — it Reads that file for context
on what was said; do not paste `segments`), the kept items from step 3
(`ts`, `content_type`, `gist`), and the item schema below. It must NOT read any repo,
SoT, or memory file. Instruct it to return ONLY this JSON:

```json
{
  "chart_present": true,
  "items": [
    {
      "ts": 252.0,
      "frame_path": ".cache/video/<id>/frames/f_0252.jpg",
      "symbol": "BTCUSDT | null",
      "direction": "long | short | neutral | null",
      "entry": "...", "stop": "...", "target": "...",
      "horizon": "intraday | swing | unspecified",
      "setup_type": "free text",
      "raw_quote": "the sentence(s) the call/claim came from, ORIGINAL language",
      "raw_quote_en": "English translation of raw_quote",
      "chart_read": "what the frame shows (levels, structure, annotations)",
      "content_type": "claim | setup | mechanic",
      "retrospective": false,
      "rejected": false,
      "verdict": "NOVEL | ALREADY-TESTED | FROZEN-CATEGORY | NOT-FALSIFIABLE",
      "gap_note": "one line: implied primitive + does the system already have/test/freeze it?",
      "vision_confidence": "high | medium | low",
      "corrected_from": "the transcript's original value, or empty"
    }
  ]
}
```

Rules for the subagent:

- Where a frame shows a number/level/structure that **contradicts** the transcript, the
  chart wins: use the chart's value in `entry`/`stop`/`target`/`chart_read`, and record
  the transcript's original claim in `corrected_from`. When nothing was corrected, leave
  `corrected_from` empty.
- Anything the frames do **not** visually corroborate (no frame near that `ts`, or the
  nearest frame doesn't show what was said) gets `vision_confidence: "low"`. Reserve
  `"high"` for a frame that directly confirms the claim; `"medium"` for
  partial/ambiguous support.
- `raw_quote` stays in the transcript's original language (Chinese stays Chinese);
  `raw_quote_en` is always English (identical to `raw_quote` when the source is already
  English).
- `verdict` applies only when `content_type = claim`; for `setup`/`mechanic` default it
  to `NOVEL` (non-blocking — routing uses `content_type` for those, same as `/ingest-x`).
- `chart_present: false` when no frame in this video shows a chart at all (pure
  talking-head) — still emit `items` from the transcript alone, all
  `vision_confidence: "low"`, `frame_path: null`.
- `retrospective` — `true` when the speaker is reviewing or managing a position entered
  **before** this video (past-tense entry, "we entered yesterday", "已经进场", a
  screenshot of a prior day's fill), rather than issuing a new actionable call. When the
  entry timing is unclear, `true` — the conservative direction for ledger integrity
  (a retrospective stamped with this video's `call_ts_utc` scores forward from a point
  where the outcome is partly known, flattering the author's hit rate). For
  `claim`/`mechanic` items always `false`.
- `rejected` — `true` when the speaker walks through a trade and then argues **against
  taking it** ("but I wouldn't touch this", "我不会进"). The setup is real and fully
  specified, which is exactly why it is dangerous: it looks identical to a live call,
  so `route_target` would send it to Stream C and `tools/pundit_score.py` would score
  the author on a trade they declined. This shipped once (2026-07-31 round 3) and only
  the human reading the digest caught it. Distinct from `retrospective` — that one is
  about *when* the position was entered, this one about *whether it was taken at all*;
  both can be false, either can be true. For `claim`/`mechanic` items always `false`.

**`confidence` vs `vision_confidence` — never merge these, they mean different things.**
`confidence` means the same thing across **every** source already in
`docs/plans/pundit-calls.jsonl`: the pundit's verbatim hedging phrase, or empty. This
pipeline does not extract that from a video (pass 2's contract is a visual-corroboration
read, not a hedging-language read — see the next step's Stream C schema for how the two
fields coexist without colliding). `vision_confidence` is **video-only** and records
whether pass 2 could visually corroborate the item against a frame — a property no other
source in the ledger has or needs. Keeping them as separate columns means a future
`GROUP BY confidence` (or any other query over the hedging-language column) stays honest
across every source, instead of silently mixing two incompatible populations.

### 7. ONE consolidated digest for the whole batch

Print a single table — one row per kept item across every video: video (title) · author ·
`call_ts_utc` (`call_ts_source`) · `ts` · `content_type` · `retrospective` · `rejected` ·
`verdict` ·
proposed routing · `vision_confidence`. Below the table, per video: the pass-1 `summary`, the dropped
candidates with their reasons, the `chart_present` flag, and `backlog` when `true`. List
any shape-1 / shape-2 videos separately with their skip reason. **Write nothing yet.**

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

### 8. Route on a single approval

After the user approves the batch, for each item compute the destination with
`tools/x_route.py::route_target(content_type, verdict, retrospective=…, rejected=…)`
(same taxonomy, unchanged import — do not fork it) and append per this table:

| content_type | verdict | Append to |
| --- | --- | --- |
| setup (`retrospective: false`, `rejected: false`) | — | `docs/plans/pundit-calls.jsonl` (one JSON line, schema below) |
| setup (`retrospective: true`) | — | **drop** — reason "retrospective — call predates video"; shown in the digest and the per-video note, never a Stream C write |
| setup (`rejected: true`) | — | **drop** — reason "rejected — speaker argued against taking it"; shown in the digest and the per-video note, never a Stream C write |
| mechanic | — | `docs/plans/mechanics-backlog.md` (a `- ` bullet) |
| claim | NOVEL | `docs/plans/thesis-inbox.md` (a draft `H` row) |
| claim | ALREADY-TESTED / FROZEN-CATEGORY / NOT-FALSIFIABLE | **drop** — state "seen, verdict X", write nothing |

**Pass both flags to `route_target` — do not hand-apply the two setup rows.** The
function returns `None` for either, so the drop is a code path with a test behind it
rather than a table you have to remember to consult at the end of a six-video batch.
That is precisely how the `rejected` case shipped. `/ingest-x` does not extract either
flag, so both default to `False` there and its behaviour is unchanged.

Create the sink file with a one-line header if it does not exist. Report a one-line
result per item (routed → which file, or dropped → verdict).

**After each successful append, record it:**

```bash
PYTHONPATH=. poetry run python tools/route_dedup.py mark \
  --source-id <meta.video_id> --item-ts <item ts> --sink <sink path>
```

`mark` runs **after** the write, never before. Marking at check time would let an
abandoned review consume the id and dedup away the real append later — the wifey-#68
watermark-on-send defect class, the same rule ST10's feed ledger follows. Never mark a
dropped item.

Stream C's near-duplicate exemption is **across sources only**: two pundits making the
same call are two real observations and `tools/pundit_score.py` scores both authors, so
collapsing those would delete signal. It never justified one video restating its own
call, which is how an entry leg and a target leg of a single position became two rows —
so within one `source_id` the pass does run (step 7's `same-source` scope plus the
`pairs` call). Stream C also still gets the exact `already_routed` block.

**Stream C requires a real `symbol` — never route a `setup` item with `symbol: null` or
`symbol: ""` to `pundit-calls.jsonl`.** `tools/pundit_score.py` has no null check of its
own; it would read the literal string `"None"` as a symbol and pollute the scored
ledger. If pass 2 could not resolve a symbol for a `setup` item, treat it as a dropped
candidate instead (reason: "no symbol resolved") in the digest and the per-video note,
not a Stream C write.

The dedup check from step 7 covers this — a `claim` whose check returned candidates must
have shown them in the digest, and an `already_routed: true` claim is not appended at all.

**Deep-link rule — separator-aware, do not reintroduce the bug.** A YouTube timestamp
deep link must respect whatever the URL already has:

- if the URL contains `?` (e.g. `https://www.youtube.com/watch?v=<id>`), append
  `&t=<ts>s`
- if it does **not** (e.g. `https://youtu.be/<id>`), append `?t=<ts>s` instead

Blindly appending `&t=<ts>s` to a `youtu.be` URL produces
`https://youtu.be/<id>&t=90s`, which is **broken** — with no prior `?`, the `&` never
starts a query string and the timestamp is silently dropped by the player. Always branch
on whether `"?"` is already in the URL before appending.

X video URLs get **no** timestamp deep link at all (the platform doesn't support one) —
persist the plain URL, and carry the offset separately in the `ts` field instead (added
below for every source, not just X, so it's never only recoverable by re-parsing the
URL).

**Stream C line** (`pundit-calls.jsonl`, one JSON line, extends the `/ingest-x` schema):

```json
{"source":"youtube","author":"<handle>","url":"<url, with the deep link above for youtube>","ts":252.0,"call_ts_utc":"<resolved call time>","call_ts_source":"stated|publish","publish_ts_utc":"<publish time>","stated_ts_raw":"<verbatim quote or empty>","ingested_ts_utc":"<now>","backlog":false,"symbol":"...","direction":"...","entry":"...","stop":"...","target":"...","horizon":"...","confidence":"","vision_confidence":"high|medium|low","raw_quote":"<original language>","raw_quote_en":"<english>","corrected_from":"<transcript's original value, or empty>"}
```

`source` is `youtube` or `x-video` (from `meta.source`, verbatim — `tools/video_fetch.py`
already resolves this). **`confidence` is always written as an empty string for a video
row.** It keeps its `/ingest-x` meaning (the pundit's verbatim hedging phrase) — this
pipeline does not currently extract that from a video, so the honest value is "not
collected," not a repurposed visual-corroboration score. `vision_confidence` carries pass
2's high/medium/low rating instead; see the "never merge these" note in step 6.

### 9. Write the per-video note

One file per video (not per item) at
`docs/plans/video-notes/<date>-<author-slug>-<video_id>.md` — already gitignored via
`docs/plans/`. `<date>` is the ingest date (UTC, `YYYY-MM-DD`).

```bash
slug() { printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9' '-' | sed -E 's/^-+|-+$//g' | cut -c1-40; }
note_path="docs/plans/video-notes/$(date -u +%F)-$(slug "$AUTHOR")-$VIDEO_ID.md"
```

**`video_id`, not a title slug — this is the rule, not a collision fallback.** `slug()`
keeps only `[a-z0-9]`, so a Chinese-language title slugifies to the **empty string** and
every note from that channel collapses onto one path. On these channels the collision is
guaranteed, not an edge case: round 4 produced an 8-way collision on a single
`<date>-tiabtc-btc.md`, which silently overwrites 7 of the 8 notes. `video_id` is unique
by construction and is also the key everything else in this flow is filed under
(`.cache/video/<video_id>/`, the routing ledger, the YouTube deep link).

A CJK **handle** collapses the same way — `meta.author` is usually the Latin `@handle`
(`@Traderfengge`), but not always (`@大漂亮`), and then the name reads
`<date>--<video_id>.md` with an empty author segment. Ugly, still unique, still correct:
do not "fix" it by putting the title back.

Contents:

- YAML frontmatter: `source`, `video_id`, `url`, `author`, `title`, `duration_s`, `lang`,
  `publish_ts_utc`, `call_ts_utc`, `call_ts_source`, `stated_ts_raw`, `ingested_ts_utc`,
  `backlog`, `chart_present`
- the pass-1 `summary`
- an items table: `ts` · `content_type` · `retrospective` · `rejected` · `verdict` ·
  routing outcome · `vision_confidence` · `frame_path`
- the dropped candidates, with reasons
- frame references (path + `ts` for every extracted frame, including ones that produced
  no routed item — that's how you learn the sampling triggers are working)
- the transcript, original language, as fetched — **not proofread; it is scratch, not
  the artifact** (see spec §Transcript quality). Only routed items carry a reviewed
  `raw_quote`/`raw_quote_en` pair; the rest of the transcript is left as-is.

Write everything above the transcript with the Write tool, then **append the transcript
with code** — it is already on disk from step 1, and retyping it is exactly the round-trip
step 1 exists to avoid:

```bash
PYTHONPATH=. poetry run python - <<'PY'
import json
from pathlib import Path

VIDEO_ID = "<video_id>"
NOTE = Path("<note_path>")

raw = json.loads((Path(".cache/video") / VIDEO_ID / "transcript.json").read_text())
lines = "\n".join(f"- `{s['ts_s']:.1f}` {s['text']}" for s in raw["segments"])
with NOTE.open("a") as fh:
    fh.write(f"\n## Transcript (as fetched, not proofread)\n\n{lines}\n")
PY
```

## Inline classification rubric (self-contained — paste into BOTH the pass-1 and pass-2 subagent prompts)

> A distilled snapshot of the SoT's Frozen / Closed / Parked state so each subagent
> classifies from the prompt alone. **Refresh from `project_todo_master.md`
> periodically** — treat as a de-biasing prior, not gospel; NOVEL still passes the
> human gate. This block is shared verbatim with `/ingest-x`'s rubric — keep the two in
> sync when either is refreshed. `content_type`: **setup** = a specific
> symbol+direction+levels trade call → Stream C; **mechanic** = an exit/risk/data/
> microstructure execution rule → Stream B; **claim** = a generalizable
> market-behaviour assertion → verdict below.

**Frozen — never propose a new TA detector.** The 22-strategy detector family
(wicks, marubozu, ORB, liquidity sweep, FVG, BOS/market-structure, funding extreme,
SMT, EQH/EQL, order block, CVD divergence, trend day, engulfing, pin bar, inside
bar, hammer, doji, morning/evening star, fib retracement / golden zone, OTE, EMA)
is frozen. A claim that just restates one of these candlestick/structure patterns →
`FROZEN-CATEGORY`.

**Already-tested (verdict known → `ALREADY-TESTED`, drop unless materially new evidence):**

- DOW / day-of-week seasonality (e.g. "Monday is the weekly high → short"): base
  rate real but the tradeable edge decays OOS; the gorgeous version is look-ahead.
- Reference-level proximity (PDH/PDL, weekly/monthly H/L, DO/WO/MO opens): audited
  NO-EDGE / underpowered-positive; revisit only when the live long-near-level cell
  ~doubles (n≥100).
- Structural first-touch entries (FVG / OB / EQH-EQL / BOS): audited BUILD on 1d but
  **live-OOS-gated** — a `structural_touch` detector is justified, not yet built.
- Funding **carry** sleeve: audited, FAILS the gate → shelved.
- Absolute **trend** (EWMAC): real but sub-gate (+0.36) → shelved as a diversifier.
- Cross-sectional **XS momentum**: the gate-clearing **deploy core** (+1.375) — not novel.

**Parked / data-blocked:**

- Price-distribution "candle outcome cone": parked (operator tool, not an edge).
- Liquidity/liquidation heatmap (magnet levels): `NOVEL` in principle but **data-blocked**
  (paid Coinglass/Hyblock; no free clean feed) — say so in `gap_note`.
- USDT.D dominance top → crypto bottom: **already captured** in `thesis-inbox.md`
  ([[usdt-dominance-hypothesis]]) — if a video reasserts it, `ALREADY-TESTED`-style
  "already in thesis-inbox", don't duplicate the H-row.

**`NOT-FALSIFIABLE`:** vibes / no testable prediction / unfalsifiable hindsight.
**`NOVEL`:** a genuinely new, testable, uncovered market-behaviour claim.

## Guardrails

- Never write to a stream before the user approves the digest — one approval covers the
  whole batch, exactly as in `/ingest-x`.
- `call_ts_utc` never comes from the model's own date arithmetic. Pass 1 only extracts a
  candidate `stated_ts_utc`/`stated_ts_raw`; the actual resolution — stated-vs-publish,
  bounding, backlog — always runs through `tools/video_calltime.py` (step 4). A subagent
  or the orchestrator computing this by hand reintroduces the exact look-ahead defect the
  tool exists to prevent (see the spec's "ledger-integrity constraint").
- Output is a hypothesis/setup/mechanic to TEST — never an "add a detector" task. The
  22-strategy detector list is frozen, same as `/ingest-x`.
- Routing dedup is `tools/route_dedup.py` (checked in step 7, marked in step 8) — the
  manual "grep the sink yourself" guardrail is retired. One thing it does NOT do, and
  you must not assume otherwise: it never auto-drops a near-duplicate — the digest and
  the human decide, on Stream C and everywhere else. On Stream C its near-duplicate pass
  is scoped to a single `source_id`, so it will never flag two authors making the same
  call, which are two real observations rather than a duplicate.
- A `setup` the speaker declined is not a call. `rejected: true` is the only thing
  standing between "he talked through this short" and "he is scored on this short";
  pass it to `route_target` rather than applying it by eye.
- Two subagent passes, both pinned to `model: "sonnet"` — never let either inherit Opus.
  Neither may read any repo, SoT, or memory file; the rubric above is the only context
  either needs beyond the video's own transcript/frames.
- `FRAME_CAP` (15) and `ITEM_CAP` (5) are a-priori constants in
  `tools/video_marks.py`. Raising either is a visible, deliberate change to the design
  spec's constants table — not a silent tuning knob inside a subagent prompt.
