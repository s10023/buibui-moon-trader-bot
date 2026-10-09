# Step 5 — frame selection, extraction and the two 403 classes

**When:** Read when `marks` is non-empty but `frame_paths` is empty, and before diagnosing any media `HTTP 403`.

Reference for `.claude/skills/ingest-video/SKILL.md`, which holds the run order, the steps and the condensed rules. This file holds the full text, including the dated incidents behind each rule. The passages below are carried over verbatim from the pre-split SKILL.md (#884), so where one says "this doc", "this document", "above", "below" or names a step, it means SKILL.md.

## Step 5 — select and extract frames

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

raw = json.loads((Path(".cache/video") / VIDEO_ID / "transcript.json").read_text(encoding="utf-8"))
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
if it comes back empty again.
⚠ **A 403 here has TWO classes, and the hand re-run separates them — that is what it is
FOR.** Round 4's were transient. The 2026-08-18 class (SoT ST41) was **persistent and
version-caused**: stable yt-dlp resolved only the `android_vr` player client, whose media
URLs 403 unconditionally, so no retry count and no hand re-run cleared it — 0 frames on
4-for-4 videos. **A 403 that survives the hand re-run is a DEPENDENCY defect, not an
unlucky video.** Stop re-running and run the canary —
`poetry run python -c 'from tools.media_probe import probe_media_leg; print(probe_media_leg().detail)'`
— which fetches a 19-second video through production's own download call and prints the
installed version beside the outcome; it is the same probe `daily_check.py` reds on. Then
check that version against the pin in `pyproject.toml`; diagnose with a plain `yt-dlp -f 251 <url>`, **never
`--download-sections`**, which hands the URL to ffmpeg, carries no client context, and
403s even when a plain download succeeds. Do NOT record
`chart_present: false` for that case; that flag is reserved for step 6, where frames
WERE produced and pass 2 actually looked at them and found no chart.
