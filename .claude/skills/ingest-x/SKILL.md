---
name: ingest-x
description: >
  Ingest a single X/Twitter post (URL) into the research pipeline. Fetches the
  post text + chart image with NO login/scraping via the public syndication
  endpoint (tools/x_fetch.py), reads the chart with vision in a subagent, classifies
  it (content-type gate -> the parent pipeline's 4-bucket verdict taxonomy), and
  routes it (after a human review gate) into one of the three streams: A hypotheses
  -> docs/plans/thesis-inbox.md, B mechanics -> docs/plans/mechanics-backlog.md,
  C daily setups -> docs/plans/pundit-calls.jsonl. Iteration 1 = text + still
  images; video is detected and skipped. Invoke when the user says "/ingest-x",
  pastes an x.com / twitter.com status URL, or says "ingest this X post".
allowed-tools: Bash, Read, Write, Edit, Task
---

# Ingest X post

Spec: `docs/superpowers/specs/2026-06-30-x-post-ingest-design.md`.

## Flow

1. **Fetch.** Run the read-only fetcher:

   ```bash
   PYTHONPATH=. poetry run python tools/x_fetch.py "<url>" --json
   ```

   - If it prints `UNAVAILABLE: …` (protected/deleted/age-gated), tell the user and
     ask them to paste the post text and drop a screenshot; then continue from step 3
     using the pasted text + image.
   - Otherwise parse the JSON: `author`, `author_name`, `post_ts_utc`, `text`,
     `photo_urls`, `video_present`, `is_thread`, `is_quote`.

2. **Download charts** (only if `photo_urls` is non-empty). In Python via the tool:

   ```bash
   PYTHONPATH=. poetry run python -c "from pathlib import Path; from tools.x_fetch import fetch_x_post, download_photos; p=fetch_x_post('<url>'); print([str(x) for x in download_photos(p, Path('.cache/x-media/<id>'))])"
   ```

   (`<id>` = the numeric tweet id.) If `video_present` is true, note in the digest
   "video detected — skipped (iteration 2)" and proceed with text + still images.

3. **Extract via a subagent** (keeps image bytes out of the main context). Dispatch a
   `general-purpose` subagent (Task tool) with: the post text, the downloaded chart
   image file path(s), the schema below, and this classification context — the
   FROZEN-detector list and the "Parked / NOT now" + "Closed" sections of
   `~/.claude-personal/.../memory/project_todo_master.md` (the living SoT) plus
   `docs/plans/thesis-inbox.md`. Instruct it to Read each image (vision), then return
   ONLY this JSON (no prose):

   ```json
   {
     "symbol": "BTCUSDT | null",
     "direction": "long | short | neutral | null",
     "entry": "...", "stop": "...", "target": "...",
     "horizon": "intraday | swing | unspecified",
     "setup_type": "free text",
     "raw_quote": "the sentence(s) the call/claim came from",
     "chart_read": "what the chart shows (levels, structure, annotations)",
     "content_type": "claim | setup | mechanic",
     "verdict": "NOVEL | ALREADY-TESTED | FROZEN-CATEGORY | NOT-FALSIFIABLE",
     "gap_note": "one line: implied primitive + does the system already have/test/freeze it?"
   }
   ```

   `verdict` applies only when `content_type = claim`; for `setup`/`mechanic` set it to
   `NOVEL` as a non-blocking default (routing uses `content_type` for those).

4. **Review digest.** Show the user: author + timestamp, the text, the `chart_read`,
   the full extraction JSON, the `gap_note`, and the proposed routing (table below).
   Write NOTHING yet.

5. **Route on approval only.** After the user approves, compute the destination with
   `tools/x_route.py::route_target(content_type, verdict)` (returns the sink path or
   `None` for a drop), then append per this table:

   | content_type | verdict | Append to |
   | --- | --- | --- |
   | setup | — | `docs/plans/pundit-calls.jsonl` (one JSON line, schema below) |
   | mechanic | — | `docs/plans/mechanics-backlog.md` (a `- ` bullet) |
   | claim | NOVEL | `docs/plans/thesis-inbox.md` (a draft `H` row) |
   | claim | ALREADY-TESTED / FROZEN-CATEGORY / NOT-FALSIFIABLE | **drop** — state "seen, verdict X", write nothing |

   Create the sink file with a one-line header if it does not exist.

   **Stream C line** (`pundit-calls.jsonl`, one line, matches the parent spec's
   pundit-call schema):

   ```json
   {"source":"twitter","author":"<handle>","url":"<url>","call_ts_utc":"<post_ts_utc>","symbol":"<symbol>","direction":"<direction>","entry":"<entry>","stop":"<stop>","target":"<target>","horizon":"<horizon>","confidence":"<verbatim hedging or empty>","raw_quote":"<raw_quote>"}
   ```

## Guardrails

- Output is a hypothesis/setup/mechanic to TEST — never an "add a detector" task. The
  detector list is frozen.
- Never auto-write a stream file before the user approves the digest.
- Iteration 1: no video, no thread-walking, no scraping. The syndication + manual-paste
  paths are the only two.
