---
name: ingest-feed
description: Poll the configured YouTube channel follow list for new uploads (tools/yt_feed.py — read-only Data API polling + explicit-outcome ledger) and feed the picked videos into the existing /ingest-video batch flow, marking consumption ONLY after the review gate routes the batch. Also drives deep back-catalogue ingestion per channel via the backfill subcommand. Invoke when the user says "/ingest-feed", "what's new on youtube", "poll the channels", "backfill <channel>", or "ingest the old videos from <channel>".
---

# Ingest feed (YouTube channel auto-feed)

Spec: `docs/superpowers/specs/2026-07-31-st10-youtube-feed-design.md`. Companion of
`/ingest-video` — this skill only automates *discovery*; every research-sink write still
happens inside the `/ingest-video` flow behind its single approval gate.

## Flow

### 1. Poll (or backfill)

```bash
PYTHONPATH=. poetry run python tools/yt_feed.py poll --json
```

For a back-catalogue request ("backfill Cowen", "ingest the old videos"), find the
channel's `UC…` id in `config/youtube_channels.toml` and run instead:

```bash
PYTHONPATH=. poetry run python tools/yt_feed.py backfill <UC…> --json \
  [--since 2026-01-01] [--max-videos 200]
```

Save the JSON output to a scratchpad file — step 4 needs it for `--candidates-json`.
Exit 1 means at least one channel errored; report the errors and continue with the
candidates that did resolve. Exit 2 = `YOUTUBE_API_KEY` missing from `.env`.

### 2. Present the candidate table

One row per candidate: channel · title · duration · age · `est_tokens` (a ±30%
ranking-grade estimate — rank by it, don't budget by it). Below the table: each
channel's exclusion summary (`below_floor` / `ledgered` / `title_filtered` /
`too_short` / `live_or_upcoming` / `unavailable`) and any errors — never hide drops.

Zero candidates → report that and stop.

**Re-presented-candidate guard:** if a candidate's per-video note already exists under
`docs/plans/video-notes/` (match on the video id in frontmatter), flag the row —
"note exists — was this already routed?" — a prior run may have died between routing
and `mark`. Confirm with the operator before re-ingesting it.

**Sibling-repo guard (cross-repo dedup).** Also check the wifey fork's notes — the two
repos follow overlapping channels and `route_dedup.py`'s ledger is per-repo, so nothing
else catches this. Grep **both** dirs in one pass:

```bash
grep -rl -E 'video_id: *"?(<id1>|<id2>|…)"?' \
  ~/repo/buibui-wifey-wall-street-bot/docs/plans/video-notes/ \
  docs/plans/video-notes/ 2>/dev/null
```

Both directories, deliberately — this subsumes the re-presented-candidate guard above and
covers the case that guard misses, since a hit in **our** dir is a same-repo re-ingest and
`.cache/video/<id>/` only spares the re-download, never the re-ingest.

**Grep the frontmatter, not the filename — in both repos.** Wifey's `/ingest-video` still
names notes `<date>-<author-slug>-<title-slug>.md` (it has not received the #522
`video_id`-slug fix), and **our own pre-#522 notes still carry title slugs too**, so
filenames are not comparable across the two repos *nor even within this one*. `video_id:`
in frontmatter is the one key present in every note on both sides. (Back-porting #522's
slug rule to wifey is a `/sync-parent` item; until then, never match on its filenames.)

A hit is **not** automatically a skip — apply the subject rule: **crypto → here,
equities/macro/gold/oil/DXY/bonds → wifey**, and a video covering both legitimately
yields rows in both repos (two different calls, not a duplicate). Route by subject, never
by repo priority: our scorer assumes 24/7 perp bars, so a macro call scored here resolves
against the wrong bars, and USO-vs-WTI fails *quietly*. Videos that are wholly the
sibling's subject should be `mark --skipped` here, not deferred, so they stop
re-presenting forever. Round 6 (2026-08-02) hit exactly this: 4 of 9 Cowen candidates were
already in wifey, and they were precisely the 4 macro ones.

For a large backfill, recommend a tranche sized to the remaining session quota rather
than ingesting the whole list — the ledger carries the progress across days.

### 3. Operator picks

Ask which candidates to ingest. Three outcomes per candidate, and only the first two
are ever marked:

- **ingest** — goes into the batch (→ `mark --ingested` in step 5)
- **skip** — operator explicitly declines, permanently (→ `mark --skipped`)
- **defer** (the default for anything not named) — NO mark; it simply reappears next
  poll/backfill

### 4. Run the ingest batch

Execute the `/ingest-video` flow (`.claude/skills/ingest-video/SKILL.md`), steps 1–9,
over the picked URLs. Follow that skill by reference — do not restate or fork it here.
Its digest + single approval remain the only gate before any research-sink write.

### 5. Mark — immediately after routing completes

```bash
PYTHONPATH=. poetry run python tools/yt_feed.py mark \
  --ingested <picked ids…> --skipped <explicitly skipped ids…> \
  --candidates-json <scratchpad file from step 1>
```

`--ingested` covers only candidates whose step-4 routing actually completed — if
`/ingest-video` dropped or failed on a picked video, omit its id here; it gets NO mark
and simply re-presents next poll/backfill, same as a deferred candidate. Do this in the
same turn as routing: the gap between routing and mark is the one failure window (see
Guardrails).

**You no longer pass `--channel-seen` by hand.** The poll payload's `channels` array
already carries both fields it wanted, so `--candidates-json` now derives the pairs —
previously this flag took ONE pair per use and had to be repeated once per followed
channel (nine times today), with the values copied out of the very file already being
passed on the line above. The flag still exists and still wins over a derived pair, for
the rare case of persisting a channel the poll did not report. Either way the write is a
`setdefault`, so a recorded floor is static and **can never move** — the derived pairs go
through the same path, which is what keeps the watermark-advances-on-its-own defect class
out of this command.

### 6. Report

Per candidate: ingested (→ which streams) / skipped / deferred. Include the mark
summary line ("marked N video(s)") as evidence the ledger write happened.

## Guardrails

- **The historical defect this design exists to avoid (wifey PR #68, watermark-on-send;
  same class the scanner's N6 fixed):** consumption stamped at fetch lets an aborted
  run permanently eat videos. Therefore `poll`/`backfill` write NOTHING; only `mark`
  writes, and only after the review gate routed the batch. Never "optimize" by marking
  early, and never edit `docs/plans/yt-feed-state.json` by hand.
- If a run dies between routing and `mark`, the routed videos re-present next poll —
  that is deliberate (visible duplication beats invisible loss). The step-2 note-exists
  guard is how the duplicate gets caught.
- This skill never writes to `thesis-inbox.md` / `mechanics-backlog.md` /
  `pundit-calls.jsonl` itself — those writes live inside `/ingest-video`'s flow only.
- A follow-list change (`config/youtube_channels.toml`) is a deliberate operator edit;
  `resolve` prints a block to paste, it never writes config.
- Quota: poll ≈ 2–3 units/channel/day against 10,000/day — never call `search.list`
  (100 units); the tool doesn't, don't add it.
