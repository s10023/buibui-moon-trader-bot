# Steps 9-10 — the per-video note and the reconcile gate

**When:** Read before writing a note's frontmatter, and when `route_reconcile.py` reports a finding.

Reference for `.claude/skills/ingest-video/SKILL.md`, which holds the run order, the steps and the condensed rules. This file holds the full text, including the dated incidents behind each rule. The passages below are carried over verbatim from the pre-split SKILL.md (#884), so where one says "this doc", "this document", "above", "below" or names a step, it means SKILL.md.

## Step 9 — write the per-video note

One file per video (not per item) at
`docs/plans/video-notes/<date>-<author-slug>-<video_id>.md` — already gitignored via
`docs/plans/`. `<date>` is the ingest date (UTC, `YYYY-MM-DD`).

```bash
# Inlined rather than wrapped in a slug() helper on purpose: a dollar-sign
# positional parameter in a SKILL.md body is substituted by the harness with the
# matching invocation arg when the skill is called WITH args, which silently
# rewrites the snippet. Named variables are safe; positionals are not. This
# comment deliberately spells that out in words rather than showing the literal.
author_slug=$(printf '%s' "$AUTHOR" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9' '-' | sed -E 's/^-+|-+$//g' | cut -c1-40)
note_path="docs/plans/video-notes/$(date -u +%F)-$author_slug-$VIDEO_ID.md"
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
  `publish_ts_utc`, `call_ts_utc`, `call_ts_source`, `stated_ts_utc`, `stated_ts_raw`,
  `ingested_ts_utc`,
  `backlog`, `chart_present`, `transcript_source`, `caption_langs_manual`,
  `caption_langs_auto`, `route`
  ⚠ **The two `caption_langs_*` lists are written for AUDIT, not for the reader.**
  ST121 asked for a one-query triage — "how many notes are ASR while captions existed?" —
  and it could not run: the lists reached step 1's JSON but were never persisted, so the
  corpus recorded the ANSWER (`transcript_source`) with none of the EVIDENCE, and 7 notes
  carried them only as model-narrated prose. Copy them from step 1's `meta` verbatim; an
  empty list is `[]` and is itself the finding that the video had no tracks.
  ⚠ **`route` is the sink path, or `dropped`** (comma-separated when one video routes to
  several sinks). Step 10 reconciles this key, and it is absent from all 135 notes written
  before 2026-08-26 — those are unreconcilable and read as `undeclared`, which is a
  finding rather than a pass. Do not retrofit them; declare it from here on.
  ⚠ **`transcript_source` is written from step 1's JSON, never narrated from memory.**
  Before this it was neither: provenance appeared on 2 of 89 notes, as a model-authored
  `lang: "zh (whisper)"` string, so the corpus could not be filtered by source at all.
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

raw = json.loads((Path(".cache/video") / VIDEO_ID / "transcript.json").read_text(encoding="utf-8"))
lines = "\n".join(f"- `{s['ts_s']:.1f}` {s['text']}" for s in raw["segments"])
with NOTE.open("a", encoding="utf-8") as fh:
    fh.write(f"\n## Transcript (as fetched, not proofread)\n\n{lines}\n")
PY
```

## Step 10 — reconcile the round

```bash
PYTHONPATH=. poetry run python tools/route_reconcile.py docs/plans/video-notes/<date>-*.md
```

Exit 1 means at least one declared route did not happen. Fix it and re-run before telling
the operator the round landed.

**Same gate, same tool, same verdicts as `/ingest-x` step 6 — read the verdict table
there, and the tool's own docstring for the why.** It is one checker rather than three
descriptions because a rule spelled three ways drifts, which is the failure this whole
item comes from: step 8 records each route as it goes, per-item and unverified in
aggregate, and on the 2026-08-25 X round 19 declared Stream C routes were never written
and went unnoticed for two days. **A note's `route:` is a declaration and a ledger mark is
a second declaration; only the sink is evidence.**

⚠ **One video legitimately yields several items under one `video_id`**, so a note routing
to several sinks lists them all in `route:` (comma-separated) — a partial declaration
reconciles clean while hiding the missing half.

⚠ **It checks SINKS, not counts, and this bites hardest here.** The reconciler asks
whether the `video_id` reached each declared sink, never how many items did. A video
routing three Stream C setups where only one landed still reads `ok`. It catches a stream
skipped ENTIRELY — the 2026-08-25 shape — so on a multi-item video the per-item count in
the step-9 items table is still yours to check by eye.
