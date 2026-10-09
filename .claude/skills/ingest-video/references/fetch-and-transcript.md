# Steps 1-2 — fetch, transcript provenance, the `unavailable` shapes

**When:** Read before the first fetch of a session, whenever `transcript_source` is `asr_whisper` or `asr_whisper_captions_missed`, and whenever a shape-2 video appears.

Reference for `.claude/skills/ingest-video/SKILL.md`, which holds the run order, the steps and the condensed rules. This file holds the full text, including the dated incidents behind each rule. The passages below are carried over verbatim from the pre-split SKILL.md (#884), so where one says "this doc", "this document", "above", "below" or names a step, it means SKILL.md.

## Step 1 — fetch the whole batch in ONE call

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
for el in json.loads(Path(".cache/video/_batch.json").read_text(encoding="utf-8")):
    row = {"url": el["url"], "cached": el.get("cached"), "unavailable": el.get("unavailable")}
    meta = el.get("meta")
    if meta:
        out = Path(".cache/video") / meta["video_id"]
        out.mkdir(parents=True, exist_ok=True)
        (out / "transcript.json").write_text(
            json.dumps({"meta": meta, "segments": el.get("segments", [])},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
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
- `meta` — `{source, video_id, author, title, publish_ts_utc, duration_s, lang, url,
  chapters, caption_langs_manual, caption_langs_auto}`, or `null` when the video itself
  was unreachable. `chapters` is `[{start_s, end_s, title}, …]` — author-declared segment
  boundaries, `[]` on roughly half the corpus. The two `caption_langs_*` lists are the
  ONLY provenance signal: `manual` is author-written, `auto` is YouTube ASR, and both
  land on disk under the same `sub.<code>.vtt` name.
- `segments` — `[{ts_s, text, lang}, …]` (empty when there is no transcript)
- `transcript_source` — `manual_captions` | `auto_captions` | `asr_whisper` |
  `asr_whisper_captions_missed` | `captions_unknown` | `""`. **Carry it into the note
  frontmatter (step 9) verbatim.**
  It is not decoration: every item, `raw_quote` and call-time derives from this text, and
  an `asr_whisper` transcript is a materially weaker source than an author-written one —
  worst on the zh channels, where ASR is weakest and `raw_quote` accuracy is load-bearing.
  Treat a `raw_quote` lifted from an ASR transcript as **quoted-with-uncertainty**: if a
  number in it is decision-changing, say so in the digest rather than presenting it as
  the author's exact words. `captions_unknown` means the metadata call described no
  caption mappings (an old cache entry) — that is "we did not ask", NOT "it was ASR".
  ⛔ **`asr_whisper_captions_missed` is the ST121 value and the one to act on: the
  metadata LISTED caption tracks, the download (retried once) still landed none, and this
  text is ASR standing in for a track that should have been used.** Unlike plain
  `asr_whisper` it is **recoverable — re-run the fetch for that video** rather than
  accepting the note, because the cause measured on `xCF8xZQcVfc` was a transient HTTP
  429 and the same call succeeded on a later hand-run. Say so in the digest's health
  notes; a note left at this value is a known-degraded `raw_quote` source.
- `frame_paths` — **always `[]` at this stage.** This CLI fetches metadata + transcript
  only; frames are extracted later (step 5), from a separate Python call, only for the
  moments pass 1 decides are worth a frame. Don't expect frames here — that is not a bug.
- `unavailable` — `null`, or a string reason

A caption-less video needs `GROQ_API_KEY` in the environment (`.env`) to produce a
transcript at all; without it, `unavailable` reports that explicitly (see shape 2 below).

## Step 2 — the three `unavailable` shapes

| Shape | `meta` | `unavailable` | Meaning | Action |
| --- | --- | --- | --- | --- |
| 1 | `null` | `"<reason>"` | The video itself is unreachable (bad URL, deleted, private, yt-dlp failure). Nothing else is known. | Tell the user by URL, drop it from the batch, continue with the rest. |
| 2 | `{...}` | `"<reason>"` | Metadata resolved fine, but no transcript could be produced — one of THREE reasons — "no captions available and no GROQ_API_KEY configured", a Groq failure (HTTP error, audio extraction, chunking), or **media download blocked** (`unable to download video data: HTTP Error 403`), which is neither of the first two and used to be mis-filed as one of them. | Because `meta` is populated, name the video (`meta.author`, `meta.title`) in the health note, and say **which of the three reasons** it was. ⚠ **A media-download block is ENVIRONMENTAL and batch-wide** — it kills captions, the whisper fallback AND frame extraction together, so do not keep batching: check the yt-dlp pin (see step 5) before spending subagent tokens on a round that cannot produce frames. Skip it from pass 1 onward — there is no transcript to feed. |
| 3 | `{...}` | `null` | Fully usable. | Proceed. |

Only shape-3 videos continue through the rest of this flow.
