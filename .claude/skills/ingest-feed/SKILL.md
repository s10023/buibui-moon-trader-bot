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
  --candidates-json <scratchpad file from step 1> \
  --channel-seen <UC…>=<floor_ts_utc from the poll output, one per channel polled>
```

`--channel-seen` persists first-seen channels' static floors (`setdefault` — it can
never move an existing floor). Do this in the same turn as routing: the gap between
routing and mark is the one failure window (see Guardrails).

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
