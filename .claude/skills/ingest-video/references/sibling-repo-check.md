# Step 2b — the sibling-repo check

**When:** Read when a grep hit lands in the wifey fork's notes and you must decide which repo the item belongs in.

Reference for `.claude/skills/ingest-video/SKILL.md`, which holds the run order, the steps and the condensed rules. This file holds the full text, including the dated incidents behind each rule. The passages below are carried over verbatim from the pre-split SKILL.md (#884), so where one says "this doc", "this document", "above", "below" or names a step, it means SKILL.md.

## Step 2b — sibling-repo check

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
quietly** (USO sits in roughly the same USD 70-85 band as WTI without tracking it). Neither
repo is a safe default for the other's subject.

Confirmed live on 2026-08-02 (`/ingest-feed` round 6): 4 of 9 Cowen candidates were already
in wifey, and they were exactly the 4 macro ones — both repos had already split him by
subject before any rule said to.
