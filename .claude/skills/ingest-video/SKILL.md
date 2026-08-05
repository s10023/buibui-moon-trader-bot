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

### 2b. Sibling-repo check — before spending any subagent tokens

This repo and the wifey fork (`~/repo/buibui-wifey-wall-street-bot/`) follow overlapping
channels — Benjamin Cowen sits in both queues today. `tools/route_dedup.py`'s ledger is
**per-repo**, so nothing else catches a cross-repo double-ingest. `/ingest-feed` runs this
at its step 2, but a pasted URL reaches this skill directly and skips that, so run it here
too:

```bash
grep -rl -E 'video_id: *"?(<id1>|<id2>|…)"?' \
  ~/repo/buibui-wifey-wall-street-bot/docs/plans/video-notes/ \
  docs/plans/video-notes/ 2>/dev/null
```

Both directories, deliberately. A hit in **this** repo's dir means you already ingested it
here — skip it outright. A pasted URL bypasses `/ingest-feed`'s re-presented-candidate
guard entirely, and `route_dedup.py`'s `check` runs later in the flow (step 7), after the
subagent spend this step exists to protect; the `.cache/video/<id>/` cache only spares the
re-download, never the re-ingest.

Grep the **frontmatter**, never the filename: wifey names notes
`<date>-<author-slug>-<title-slug>.md` (it has not received #522's `video_id`-slug fix),
and **our own pre-#522 notes still carry title slugs**, so filenames are not comparable
across the two repos — nor even within this one. `video_id:` in frontmatter is present in
every note on both sides.

**A hit is not automatically a skip — apply the subject rule:**

- crypto instrument → **here** (perp data + the only working scorer)
- equities / macro / gold / oil / DXY / bonds → **wifey**
- one video covering both legitimately yields rows in **both** repos. Two different calls,
  not a duplicate.

Route by **subject, never by repo priority.** Our scorer assumes 24/7 perp bars, so a macro
call scored here resolves against the wrong bars, and wifey's ETF proxies mean **oil fails
quietly** (USO sits in roughly the same $70-85 band as WTI without tracking it). Neither
repo is a safe default for the other's subject.

Confirmed live on 2026-08-02 (`/ingest-feed` round 6): 4 of 9 Cowen candidates were already
in wifey, and they were exactly the 4 macro ones — both repos had already split him by
subject before any rule said to.

### 3. Pass 1 — text-only subagent, one per video, pinned to sonnet

For each shape-3 video, dispatch a subagent via the Task tool with **`model: "sonnet"`**
(do not inherit Opus) and **`subagent_type: "Explore"`** — measured **3.6× cheaper** than
`general-purpose` at identical quality on the same extraction shape (24,187 vs 87,975
tokens, 2026-08-03 A/B), the likely mechanism being that it does not inherit full project
context. `Explore` is documented as reading *excerpts*, so state explicitly that it must
**Read the transcript file in full and not sample it**. Give it:

- the video's `transcript_path` from step 1 — **the path, not the transcript.** Instruct
  it to Read that file; `segments` is the `"segments"` key inside it. Pasting the array
  into the prompt puts the whole transcript in your context for no gain, since the
  subagent has its own.
- `meta.publish_ts_utc`, `meta.author`, `meta.lang` — **context only**, for resolving a
  relative stated date ("last Monday") and inferring a speaker's timezone from channel
  locale. It must NOT compute a final call time itself — that happens in code, step 4.
- the channel's `intro_recap_s` and `item_cap` (see below) — numbers, not rules to
  re-derive
- the inline classification rubric (below)

**First, ask the config for this channel's two per-video knobs:**

```bash
PYTHONPATH=. poetry run python tools/yt_feed.py hint --author "<meta.author>"
```

Pure local config read, no API key, no network. Returns
`{"matched": …, "intro_recap_s": N, "item_cap": N, …}`. Run it per video — a batch can
span channels. Both knobs degrade quietly to a default: `matched: false` or
`intro_recap_s: 0` means no recap rule and nothing changes, and `item_cap` falls back to
`video_marks.ITEM_CAP` (5).

**`item_cap` is applied by the pass-1 PROMPT, not by code — so a value fetched here and
not passed on does nothing.** Carry it into rule 5 below as a literal. This shipped
broken: #558 added the key to `config/youtube_channels.toml`, `tools/yt_feed.py`, its
tests and `.claude/context/tools.md`, but left this document saying the cap was always
5 — so on the first run after it merged, `hint` returned `@KoluniteVIP`'s `item_cap: 12`
and the flow discarded it. A cap plumbed everywhere except its one consumer is not
plumbed.

Note this keys on `meta.author` (an @handle), which matches neither the `UC…` id the poll
path uses nor a CJK display `name` — that is why `config/youtube_channels.toml` carries a
`handle` field. **A channel with no `handle` never matches, and then BOTH knobs silently
take their defaults** — measured on `@KoluniteVIP` (2026-08-05): no `handle`, no
`item_cap`, `hint` returned `matched: false`, and the cap sat at 5 on a channel that
relays ~8 traders per upload. Neither degradation raises anything; the run just quietly
keeps less.

It must NOT read any repo, SoT, or memory file — the rubric is self-contained. Instruct
it to return ONLY this JSON:

```json
{
  "summary": "one paragraph, English",
  "stated_ts_utc": "ISO-8601 with an explicit UTC offset, or null",
  "stated_date_only": false,
  "stated_ts_raw": "verbatim quote or empty",
  "candidates": [
    {"ts": 252.0, "content_type": "setup|claim|mechanic", "specificity": 1-5,
     "is_relay": false, "originating_author": "@ThisChannel",
     "is_intro_recap": false, "retrospective": false, "gist": "..."}
  ]
}
```

**`stated_ts_utc` must carry an explicit UTC offset (e.g. `2026-07-14T08:00:00+08:00`),
or be `null` — never a bare local time.** `tools/video_calltime.py` rejects a naive
(offset-less) timestamp and silently falls back to publish time, so a subagent that
emits `2026-07-14T08:00:00` with no offset gets the same downstream result as emitting
nothing, just less honestly. Instruct the subagent: state the offset whenever the
speaker's timezone is inferable from context, otherwise emit `null` — never guess UTC.

**Filter for ATTRIBUTION and SUBJECT BEFORE applying the cap — not after.** The cap is a
budget for items this repo can actually use, so spending a slot on one it will drop at
routing wastes the slot silently. Instruct the subagent, in this order:

1. **Drop `setup` candidates inside the channel's intro-recap window — they are past
   calls.** If `intro_recap_s` came back non-zero, set `is_intro_recap: true` on every
   candidate with `ts < intro_recap_s`. For a `setup`, ALSO set `retrospective: true`,
   which is what makes `route_target` drop it. For a `claim` or `mechanic`, set only
   `is_intro_recap` and keep the candidate — an idea stays portable regardless of when in
   the video it was said — but the digest must show the flag so the human can decline it.

   **Why this is a rule and not a judgement call.** Some channels open every single upload
   by replaying prior positions before saying anything new. A `setup` lifted from that
   window is a *previous* call stamped with *today's* `call_ts_utc`, so
   `tools/pundit_score.py` scores the author on a trade that already resolved. On
   2026-08-03 the 7.24 upload's opening block described an entry that **never filled**
   (「離我入場的位置綠色框框就差幾塊錢」) — scoring that would have credited a trade nobody took.

   **And why `claim`/`mechanic` still need the flag rather than nothing.** `route_target`'s
   `retrospective` drop is **setup-only by design**, so a retrospective *mechanic* has no
   code path that stops it. That is exactly what happened the same day: the 7.20 upload's
   recap block ("上週14號我們是入場了一個比特幣的多單" — a past trade's exit management) was
   typed as a `mechanic` and routed to Stream B with nothing objecting. Only an operator
   reading the digest caught it. The flag is what makes that visible instead of silent.

2. **Relayed `setup` candidates are kept ONLY if the roster resolves the originating
   name.** Set `is_relay: true` whenever the speaker is reading out, reacting to, or
   summarising a call made by **someone else** (「X老师给了一个多单」, a screenshot of
   another analyst's Discord/Telegram post, an on-screen name card introducing a third
   party), and put that person's name **verbatim** in `originating_author` — do not
   normalise, correct or "fix" it, because the raw string is what the roster's alias
   lists are built from and a helpful correction destroys the evidence that grows them.
   Set `is_relay: false` and `originating_author` to the channel's own handle only for
   the speaker's own calls. **`claim` and `mechanic` candidates are exempt** — an idea is
   portable regardless of who first said it, and Streams A/B do not score anyone.
   Resolution happens in step 4; anything that does not resolve to `mapped` drops.
3. **Drop non-crypto `setup` candidates.** A setup on SPX, gold, DXY, oil or a
   single equity routes to wifey (step 2b's subject rule), never to `pundit-calls.jsonl`
   here — our scorer resolves against 24/7 perp bars it has no data for.
4. **`mechanic` candidates stay eligible regardless of symbol.** A risk-management or
   execution technique demonstrated on SPX is just as portable as one on BTC; the
   instrument is incidental to a mechanic in a way it never is to a setup.
5. **Then** rank what remains by `specificity` descending and keep the top `item_cap` —
   **the number `hint` returned for THIS channel**, written into the prompt as a literal.
   It is not a constant: it defaults to `tools/video_marks.py::ITEM_CAP` (5) and is raised
   per channel for aggregators that relay several traders per upload. Never let the
   subagent infer it, and never hard-code 5 here.

Report every dropped candidate with a one-line reason, distinguishing the four causes —
`intro-recap window (ts < intro_recap_s), a prior call not today's` vs
`relayed call by 陈哥, not the speaker's own` vs `non-crypto setup (SPX), routes to wifey`
vs `specificity 2, below the top-12 cutoff (this channel's item_cap)` — for the digest
and the note. **Filtered items
belong in the note too:** they are the record that the video contained something this repo
deliberately declined, not something it failed to see. Relayed calls in particular must be
preserved verbatim in the note with their `originating_author`, so a future attribution
roster can mine them without re-fetching the video.

Round 5 (2026-08-01) established the subject half and round 6 confirmed it paid: a
**specificity-4 S&P 500 setup** was dropped before the cap, freeing a slot for a crypto
item that would otherwise have been cut at rank 6.

**The attribution half is here because it was diagnosed and then NOT shipped, and
regressed.** Round 1 (2026-07-31, Kolunite `6qjuqdlmRVE`) found that `ITEM_CAP` ranks on
`specificity` alone and that this **inverts an aggregator video**: pass 1 returned 25
candidates, the top 5 by specificity were **all second-hand relays**, and the host's own
call ranked 6th — a naive top-5 keep would have routed five unattributable calls and
dropped the only legitimate one. That run fixed it with inline channel context and never
wrote it down here. **Round 7 (2026-08-03) reproduced the identical inversion: 5 of 5 kept
items were relays, 0 host-own.**

Why it is a correctness issue and not an ergonomics one: `tools/pundit_score.py` groups on
`author`, which comes from `meta.author` — the **channel**, not the caller. A routed relay
therefore credits the channel with someone else's call. Round 7 made the consequence
concrete: 峰哥's call was relayed inside a Kolunite roundup **in the same batch as 峰哥's
own video**, under an ASR-mangled name that would not collide with `@Traderfengge` — so
the double-count would also have been invisible to any author-level grouping.
`tools/route_dedup.py`'s Stream C semantic pass is scoped to `same-source` and
**structurally cannot** catch a relay against the original author's own row.

**Do not write the RAW extracted `originating_author` into the Stream C `author` field.**
Relayed names fragment (`输情`/`瞬间` and `舒琴` are one person; ASR mangles CJK names) and
collide (`陈志峰` is three traders run together). Attributing on an unnormalised extracted
name manufactures phantom pundits with fake track records while starving the real ones of
rows.

**The roster-RESOLVED canonical handle is different, and it is what belongs in `author`.**
`tools/pundit_score.py` groups on `author` (`:155`, via `normalize_author`), so writing the
resolved handle there is what makes the scorer credit the right person with **zero**
changes to it. Adding a parallel `originating_author` ledger column instead leaves every
relay scoring under the channel — the exact bug this is meant to fix, wearing the
appearance of a fix. Resolution comes from `tools/pundit_roster.py`; anything it does not
resolve to `MAPPED` sets `unattributable=True` and drops.

A routed relay row carries: `author` = the resolved handle · `relayed_by` = the relaying
channel's handle · `attribution` = `"relay"` · `attribution_confidence` = the roster
entry's `confidence`. A first-hand row sets `attribution: "first-hand"` and omits
`relayed_by`. `source` keeps its current meaning (the medium) and is never overloaded to
carry the relaying channel. `load_ledger` reads per-key with `obj.get(...)`, so these new
keys are backward-compatible and cost the scorer nothing.

**The roster is WIRED — do not rebuild it from scratch.** Gitignored
`config/pundit_roster.toml` (schema + rationale in the committed
`config/pundit_roster.toml.example`) holds the confirmed name→handle mappings, an
`[[unmapped]]` list of names still being chased, and `[[ambiguous]]` entries marked
`never_auto_attribute` for strings like `陈志峰` that are several people.
`tools/pundit_roster.py` reads it at step 4a and returns one of four outcomes; only
`mapped` routes. **A relay whose name does not resolve still drops** — the rule changed
from drop-every-relay to drop-what-cannot-be-attributed.

**Treat `confidence = "operator"` entries as unverified assertions.** A roster entry is
an assertion we make, not a fact the pipeline derives, and a wrong mapping attributes
real calls to the wrong trader with **no downstream check able to see it** — strictly
worse than dropping the relay. When an operator-confidence mapping carries a routed row,
say so in the digest so the approver can weigh it.

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
  "call_ts_source": "stated|publish|publish_relay",
  "publish_ts_utc": "...",
  "stated_ts_raw": "...",
  "backlog": false
}
```

Use these five fields **verbatim** in the digest, the ledger line, and the note. Never
have a subagent or the orchestrator derive `call_ts_utc` by date arithmetic — that is
exactly the look-ahead defect this tool exists to prevent (see Guardrails).

#### 4a. Resolve a relayed call's ORIGINATING AUTHOR

For every `setup` candidate with `is_relay: true`, resolve the verbatim
`originating_author` through the roster:

```bash
PYTHONPATH=. poetry run python tools/pundit_roster.py "<originating_author>"
```

Four outcomes, and each has exactly one action:

- `mapped` — **route it.** `author` = the returned `handle` · `relayed_by` = this
  channel's handle · `attribution` = `"relay"` · `attribution_confidence` = the returned
  `confidence`. Treat `confidence: "operator"` as an unverified assertion and say so in
  the digest.
- `ambiguous` — **surface it in the digest for manual attribution**, listing `members`.
  The operator assigns it from the audio or declines. Default if unactioned: **drop**.
  This is a positive instruction, not missing data: `陈志峰` is three traders, and the
  members being individually identified still does not say which one spoke a given line.
- `unmapped` / `unknown` — set `unattributable=True` so `route_target` drops it, and
  **list the name in the digest's UNRESOLVED NAMES section**. They stay distinct so the
  digest can tell "a name we are already chasing" from "a name we have never seen".

**Never resolve by eye.** `波浪` and `柳玉东` are one person with no shared characters,
`军长`/`君掌` are homophones, and `波浪理论` is Elliott Wave Theory — a phrase, not a
person. Only the roster's explicit alias list gets these right, which is why the resolver
matches on exact equality and nothing else.

#### 4b. Resolve a relayed call's TIME separately from the video's

A relayed call was made **before** the roundup that reports it, so the video-level
`call_ts_utc` is wrong for it — that is the `retrospective` defect applied to 100% of
relays, and it is signed rather than self-cancelling: the scorer replays forward from
`call_ts`, so a relay of a call that already hit target scores as a non-hit, while one
that already stopped out can catch a later recovery.

When pass 1 returned a per-candidate stated time, run `video_calltime.py` again for that
item with `--relay`; when it did not, run it with `--relay` and no `--stated` so the row
is labelled `publish_relay`:

```bash
PYTHONPATH=. poetry run python tools/video_calltime.py \
  --publish <meta.publish_ts_utc> --relay [--stated <item stated_ts_utc>]
```

`--relay` labels the **fallback only** — a stated time that survives every bound is still
the better answer, and still comes back as `stated`.

#### 4c. Check a relay against its ORIGINATING author's own rows

The Stream C dedup pass is `same-source` scoped, so it structurally cannot compare a
relay against the original author's own first-hand row — and ingesting both the
aggregator and the originating channels makes that collision routine rather than
theoretical. Pass the resolved handle so the check widens from one source to one author:

```bash
PYTHONPATH=. poetry run python tools/route_dedup.py check \
  --source-id <video id> --item-ts <t> --sink docs/plans/pundit-calls.jsonl \
  --text "<the call>" --author <resolved handle>
```

`semantic_scope` comes back `same-author` instead of `same-source`. The across-source
exemption is unchanged for **different** authors — two pundits making the same call are
two genuine observations, not a duplicate.

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
`subagent_type: "general-purpose"`. **This one stays `general-purpose` deliberately** —
pass 1 and `/ingest-x` moved to `Explore` on a measured 3.6× saving, but that A/B covered
single-image transcription, and pass 2 is a materially harder task (cross-references the
transcript against ≤15 frames, chart-corrects spoken levels, emits multi-item JSON with
per-item confidence) on a payload of real frame tokens that will not shrink. Pass 2 is
where this pipeline's value lives (round 5: 11 chart corrections, one a level 2.2% off
that would have triggered on a sweep that never happened). **A/B it on ONE video before
switching it** — do not assume the saving transfers. Give it: the `frame_paths` list (it Reads each one —
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
- **The chart wins only on a DRAWN value** — a line, annotation, printed label, or
  measured readout placed on the chart. A value *inferred* from where the live price
  ticker happens to sit is **not** a correction: put that reading in `chart_read`, keep
  the spoken value in `entry`/`stop`/`target`, and leave `corrected_from` empty. A pundit
  is scored on the level they **stated**. (Observed failure: a stated 61,000 pivot was
  proposed as 61,600–61,800 because the ticker sat at a drawn arc's right anchor — ~700
  points onto a level the speaker never said.)
- **`corrected_from` carries chart-vs-transcript corrections ONLY.** A symbol
  normalisation (`BTCUSD` → `BTCUSDT`, so the scorer resolves against perp bars) is not
  one: normalise `symbol` and leave `corrected_from` empty. Same reason `confidence` and
  `vision_confidence` stay separate — a field carrying two semantics can be queried for
  neither.
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
| mechanic | — | `docs/plans/mechanics-backlog.md` (a `-` list bullet) |
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
{"source":"youtube","author":"<handle>","attribution":"first-hand|relay","relayed_by":"<relaying channel handle, relay rows only>","attribution_confidence":"<roster confidence, relay rows only>","url":"<url, with the deep link above for youtube>","ts":252.0,"call_ts_utc":"<resolved call time>","call_ts_source":"stated|publish|publish_relay","publish_ts_utc":"<publish time>","stated_ts_raw":"<verbatim quote or empty>","ingested_ts_utc":"<now>","backlog":false,"symbol":"...","direction":"...","entry":"...","stop":"...","target":"...","horizon":"...","confidence":"","vision_confidence":"high|medium|low","raw_quote":"<original language>","raw_quote_en":"<english>","corrected_from":"<transcript's original value, or empty>"}
```

**`author` is the person who MADE the call, never the channel that reported it.** On a
first-hand row those are the same and `attribution` is `"first-hand"` with `relayed_by`
omitted. On a relay, `author` is the roster-resolved handle from step 4a and `relayed_by`
is this channel — `tools/pundit_score.py` groups on `author` (`:155`), so this is what
makes the scorer credit the right person with **zero** changes to it. `source` keeps its
meaning below (the medium) and is never overloaded to carry the relaying channel.
`load_ledger` reads per-key with `obj.get(...)`, so the three new keys are
backward-compatible and every pre-existing row keeps scoring exactly as before.

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
- A `setup` from the channel's opening recap block is not today's call. `intro_recap_s`
  (step 3, from `yt_feed.py hint`) is the only thing that catches it, and it is **opt-in
  per channel** — an unconfigured channel silently has no rule, which is correct but means
  a newly-followed recap-style channel needs the field set before its first ingest. The
  companion failure is quieter: `route_target`'s `retrospective` drop is **setup-only**, so
  a retrospective `mechanic` or `claim` has NO code path stopping it — that is why those
  carry `is_intro_recap` into the digest instead of being dropped. If a future round finds
  a past trade's exit management sitting in `mechanics-backlog.md`, the bug is that the
  flag never reached the digest, not that the case is unknown.
- A `setup` the speaker **relayed** is not their call either. The attribution filter in
  step 3 drops it before the cap, so it never reaches frames, pass 2, or a sink — which
  is also why pass 2 has no `originating_author` field to fill. This one has regressed
  once already (diagnosed round 1, repeated round 7) because the fix lived in a memory
  file instead of in this document. If a future round finds relays in the kept set again,
  the bug is that step 3's rule was dropped from the pass-1 prompt, not that it is
  unknown.
- Two subagent passes, both pinned to `model: "sonnet"` — never let either inherit Opus.
  Neither may read any repo, SoT, or memory file; the rubric above is the only context
  either needs beyond the video's own transcript/frames.
- `FRAME_CAP` (15) and `ITEM_CAP` (5) are a-priori constants in
  `tools/video_marks.py`. Raising either **globally** is a visible, deliberate change to
  the design spec's constants table — not a silent tuning knob inside a subagent prompt.
  A per-channel `item_cap` in `config/youtube_channels.toml` is the sanctioned exception
  and is not the thing that rule guards against: it is committed config, it is reported by
  `hint`, and step 3 passes it through explicitly. The failure mode it introduces is the
  opposite one — a cap that is configured, fetched, and then never reaches the prompt.
