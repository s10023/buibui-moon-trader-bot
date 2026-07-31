# ST10 — YouTube channel auto-feed (`/ingest-feed`) design

Date: 2026-07-31. Status: approved design, pre-implementation.

ST10 (SoT item): stop hand-pasting YouTube URLs into `/ingest-video` — poll a pre-configured
channel list for new uploads daily, present the candidates, and hand the picked ones to the
existing skill's batch path. This automates the *fetch/discovery* step of an existing daily
habit; the human review gate inside `/ingest-video` is unchanged and remains the only route
into the research sinks.

Related: `docs/superpowers/specs/2026-07-28-ingest-video-design.md` (the pipeline this feeds),
`.claude/skills/ingest-video/SKILL.md`, `tools/x_route.py` (routing taxonomy, untouched).

## 1. Decisions inherited (pre-made, not revisited here)

- **YouTube Data API v3, API key only.** Uploads-playlist polling costs 1 unit/channel/poll
  against a free 10,000 units/day quota. `search.list` (100 units) is never called. No OAuth
  in phase 1 — public channel uploads only. Key lives in `.env` as `YOUTUBE_API_KEY`
  (restrict to the YT Data API in the GCP console; rotating it is recommended since it was
  once pasted into a chat).
- **Channel config is gitignored** (a follow list is personal), with a committed `.example` —
  the `coins.json` pattern.
- **Consumption is stamped only after the review gate routes the batch** — never at fetch.
  See §3, the load-bearing invariant.
- **The retrospective-call drop rule** from the 2026-07-29 e2e run is codified in
  `/ingest-video` as part of this work (§7).
- Benjamin Cowen's channel ships in the `.example` (cycle ideas feed hypothesis H2, the 50W
  EMA cycle-bias test).

Operator choices made at design time (2026-07-31): fold in a duration-based cost-estimate
column (cheap version only); present-and-pick selection UX; a new small `/ingest-feed` skill
rather than extending `/ingest-video`.

## 2. Architecture overview

```text
config/youtube_channels.toml        docs/plans/yt-feed-state.json
        (gitignored)                        (gitignored)
              │                                  │
              ▼                                  ▼
tools/yt_feed.py poll  ──── read-only ────  candidates (table + --json)
              │
              ▼
.claude/skills/ingest-feed/SKILL.md
   1. poll → present candidate table (est. tokens per video)
   2. operator picks
   3. run the existing /ingest-video flow (steps 1–9) over picked URLs
   4. after routing completes: tools/yt_feed.py mark   ← the ONLY state writer
```

New surfaces: `tools/yt_feed.py`, `config/youtube_channels.toml(.example)`,
`docs/plans/yt-feed-state.json`, `.claude/skills/ingest-feed/SKILL.md`. Modified surfaces:
`.claude/skills/ingest-video/SKILL.md` (retrospective rule), `.env.example`, README +
CLAUDE.md doc rows. No schema, DB, or daemon change; nothing here touches the trading path.

## 3. Load-bearing invariant — consumption semantics

**The historical defect this design must not repeat** (wifey PR #68, watermark-on-send; the
same class N6 fixed in the signal scanner): stamping a "seen" watermark at *fetch* time lets
an aborted or non-completing run permanently consume items — the queue silently loses videos
that were never reviewed.

Design answer, three properties:

1. **`poll` is strictly read-only.** It writes neither state nor cache. Aborting at any point
   before routing leaves every candidate unconsumed; it reappears on the next poll.
2. **The ledger records explicit outcomes only.** A video enters state solely via
   `yt_feed.py mark`, invoked by the skill after the `/ingest-video` review gate has routed
   the batch. Statuses: `ingested` (went through the pipeline) or `skipped` (operator
   explicitly declined it, permanently). There is no fetch-time or in-progress status.
3. **No moving watermark.** Each channel gets a static `floor_ts_utc`, set once when the
   channel is added (`now − cold_start_days`) and never advanced afterwards. "New" =
   on the uploads playlist's first page ∧ `publishedAt ≥ floor_ts_utc` ∧ not in the ledger ∧
   passes filters. A moving watermark was considered and rejected: it makes
   skipped-but-below-watermark videos silently unreachable and couples consumption to fetch
   order — the exact semantics the ledger model exists to avoid.

The floor bounds the **daily `poll` only** — it keeps a fresh channel add from flooding the
feed. It is not a wall: the deep back-catalogue is reachable any time via the explicit
`backfill` subcommand (§5), which ignores the floor but still respects the ledger, so
ingested/skipped videos never re-present through either path.

Failure window: if the session dies between routing and `mark`, the routed videos re-present
on the next poll. This is deliberate fail-open toward *visible* duplication rather than
invisible loss; the skill instructs a per-video-note existence check
(`docs/plans/video-notes/`) before re-routing a re-presented candidate. Deferral is the
default for unpicked candidates: they get **no** mark and simply reappear; `skipped` is only
written when the operator explicitly says so at pick time.

## 4. `config/youtube_channels.toml`

Gitignored; committed sibling `config/youtube_channels.toml.example` documents the schema and
ships Cowen as the first entry.

```toml
[feed]
cold_start_days = 14      # floor_ts = add-time − this; bounds the daily poll only —
                          # older uploads stay reachable via `backfill` (§5)

[[channel]]
id = "UC..."                       # UC… channel id — fill via `yt_feed.py resolve <handle>`
                                   # at implementation time; never hand-guess an id
name = "Benjamin Cowen"
title_include = []                 # keep only titles matching ANY keyword; empty = keep all
title_exclude = ["#shorts"]        # drop titles matching ANY keyword
min_duration_s = 180               # Shorts / clip filter
lang = "en"                        # hint only: displayed, stored on candidates; consumed by
                                   # nothing yet (future per-language _ASR_VOCAB follow-up)
```

Filter semantics: `title_include`/`title_exclude` are case-insensitive substring matches;
exclude wins over include. `min_duration_s` needs real durations, which is why `poll` calls
`videos.list` (§5). Defaults when a key is omitted: empty lists, `min_duration_s = 180`,
`lang = ""`.

## 5. `tools/yt_feed.py`

One-shot analysis-tool conventions: `PYTHONPATH=. poetry run python tools/yt_feed.py …`,
typed, pure logic separated from transport. The HTTP `get` is injected (as in
`tools/x_fetch.py` / `tools/video_fetch.py`) so the suite is network-free.

### Subcommands

**`poll [--config PATH] [--state PATH] [--json]`** — read-only. Per channel:

1. Derive the uploads playlist id: `UC…` → `UU…` prefix swap. If `playlistItems.list` on the
   derived id errors (404), fall back to `channels.list?part=contentDetails` and read
   `relatedPlaylists.uploads`.
2. Fetch page 1 of `playlistItems.list` (`maxResults=50`, newest-first). Bounded recovery:
   anything that falls off page 1 without a ledger entry drops out of the daily feed — same
   philosophy as the scanner's 200-candle `_SCAN_WINDOW`. It stays recoverable via
   `backfill` (below) or a manual URL paste.
3. Drop: below `floor_ts_utc`; already in the ledger; title-filtered.
4. Batch `videos.list?part=contentDetails,snippet` for the survivors (50 ids/call) →
   `contentDetails.duration` (ISO-8601 parse) and `snippet.liveBroadcastContent` (a field of
   `snippet`, not its own part).
5. Drop: `duration < min_duration_s`; `liveBroadcastContent ∈ {live, upcoming}` (premieres
   and live streams become candidates once they are plain VODs — they were never ledgered,
   so they reappear naturally).
6. Emit candidates.

Output: a human table plus `--json`. Per candidate:

```json
{"channel_id": "UC…", "channel_name": "…", "video_id": "…",
 "url": "https://www.youtube.com/watch?v=<id>", "title": "…",
 "publish_ts_utc": "2026-07-31T02:00:00+00:00", "duration_s": 1260,
 "age_h": 9.5, "est_tokens": 34040, "lang_hint": "en"}
```

**No silent caps:** the output also reports, per channel, how many page-1 items were excluded
and why (`below_floor` / `ledgered` / `title_filtered` / `too_short` / `live_or_upcoming`),
plus a per-channel `errors` list. One failing channel never kills the poll (the
`run_backfill` per-symbol resilience pattern); exit 1 if any channel errored, 0 otherwise.

**`mark [--state PATH] --ingested ID… --skipped ID…`** — the only state writer. Validates id
shape (11-char YouTube id) but cannot validate against a candidate list (poll left no trace —
by design). Idempotent; re-marking overwrites (last wins). Accepts bare video ids and stores
`channel_id: null` when unknown — the ledger's only job is diffing, and diffing is by
video id.

**`backfill CHANNEL_ID [--since ISO_DATE] [--max-videos N] [--config PATH] [--state PATH]
[--json]`** — the deep-backlog path, read-only like `poll`. Pages through the channel's full
uploads playlist (50/page, 1 unit/page, newest-first) until `--since` or `--max-videos`
(default 200) is reached. Differences from `poll`: it **ignores `floor_ts_utc`** (that is its
purpose) and takes exactly one channel per invocation; everything else is identical — same
ledger diff, same title/duration/live filters, same candidate JSON shape, same exclusion
summary. Deferral semantics: an unpicked backfill candidate is below the poll floor, so it
reappears only on the next `backfill` run of that channel, never in the daily feed. For a
"tons of old videos" channel the intended workflow is tranches: run `backfill`, pick a
session-quota-sized batch by `est_tokens`, ingest, `mark`, repeat another day — the ledger
carries the progress, no extra bookkeeping.

**`resolve HANDLE`** — `channels.list?forHandle=<handle>&part=id,snippet` → prints a
ready-to-paste `[[channel]]` TOML block (id + name). Never writes config — a follow-list
change stays a deliberate operator edit, the `select_universe.py` convention.

### Quota table (a-priori; daily cost for N channels ≈ 2–3 N units vs 10,000/day)

| Endpoint | Units | Called |
| --- | --- | --- |
| `playlistItems.list` | 1 | once per channel per poll; 1 per 50-video page in `backfill` (a 2,000-video channel ≈ 40 units) |
| `videos.list` | 1 per 50 ids | once per ≤50 surviving candidates |
| `channels.list` | 1 | `resolve`, and the rare `UU`-derivation fallback |
| `search.list` | 100 | **never** |

### Error handling

- Missing `YOUTUBE_API_KEY` → immediate, actionable abort (name the env var and `.env`).
- HTTP 403 `quotaExceeded` vs 400 bad-key are distinguished in the per-channel error text.
- Malformed state JSON → loud abort, never silent reset (a reset would re-queue everything
  ever ingested and erase every skip decision).

## 6. State — `docs/plans/yt-feed-state.json`

Gitignored via `docs/plans/`. Deliberately **not** under `.cache/`: clearing caches is a
normal operation (the video-fetch cache documents exactly that) and must never re-queue or
lose ledger history. Writes are atomic (temp file + `os.replace`).

```json
{
  "version": 1,
  "channels": {
    "UC…": {"added_ts_utc": "2026-07-31T09:00:00+00:00",
             "floor_ts_utc": "2026-07-17T09:00:00+00:00"}
  },
  "videos": {
    "dQw4w9WgXcQ": {"status": "ingested", "channel_id": "UC…",
                     "title": "…", "decided_ts_utc": "2026-07-31T10:12:00+00:00"}
  }
}
```

A channel first seen in config during `poll` gets its `channels` entry (floor computed) — this
is the one thing `poll` would need to persist, so instead: **`poll` computes the floor for an
unknown channel in memory and reports it; the entry is persisted by the next `mark`** (which
receives `--channel-seen UC…=<floor_ts>` pairs from the skill; `=` separator, since the
timestamp itself contains colons), keeping `poll` write-free. Until the first `mark` persists
the entry, the floor drifts forward with each poll date — harmless, since it only affects a
channel the operator has never ingested from.

`title` is stored for human auditability of the ledger; nothing reads it programmatically.

## 7. `/ingest-video` edits — retrospective-call rule

Found by the 2026-07-29 e2e run: pundits review trades **entered on prior days**; those items
were stamped with the video's `call_ts_utc` while carrying an entry set earlier, so the scorer
resolves forward from a point where the outcome is already partly known — flattering the
measured hit rate. Operator decision: drop them from Stream C. Codification:

- **Rubric** (the shared inline classification rubric): `setup` items gain a required boolean
  `retrospective` — `true` when the speaker is reviewing or managing a position entered
  *before* this video (past-tense entry, "we entered yesterday", "已经进场", a Telegram
  screenshot of a prior day's fill), rather than issuing a new actionable call. When in doubt
  (entry timing unclear), `true` — the conservative direction for ledger integrity.
- **Routing** (step 8): `setup` + `retrospective: true` → **drop from Stream C**, shown in
  the digest and the per-video note's items table with reason "retrospective — call predates
  video". It is not a Stream C write under any override.
- The per-video note items table gains the `retrospective` column.

This edit rides in the ST10 implementation PR (SoT: "fold in the retrospective-call drop
rule").

## 8. `.claude/skills/ingest-feed/SKILL.md`

Orchestration only, mirroring the sibling skills' shape. Flow:

1. `PYTHONPATH=. poetry run python tools/yt_feed.py poll --json` — or, when the operator
   asks for a channel's back-catalogue ("backfill <channel>", "ingest the old videos"),
   `tools/yt_feed.py backfill <channel_id> --json` instead; every later step is identical.
2. Render the candidate table: channel · title · duration · age · `est_tokens` — plus the
   per-channel exclusion summary and any errors. Zero candidates → report and stop. For a
   large backfill, recommend a tranche sized to remaining session quota (rank by
   `est_tokens`) rather than ingesting the whole list in one go.
3. Operator picks. Syntax: ingest some, optionally explicitly skip others; **anything not
   named is deferred** (no mark, reappears next poll).
4. Run the existing `/ingest-video` flow, steps 1–9, over the picked URLs **by reference**
   (`.claude/skills/ingest-video/SKILL.md`) — this skill must not restate or fork that flow.
5. Immediately after routing + notes complete, one call:
   `tools/yt_feed.py mark --ingested <picked…> --skipped <explicit skips…> --channel-seen …`
6. Re-presented candidate guard: if a candidate's per-video note already exists in
   `docs/plans/video-notes/`, flag it in the table ("note exists — was this already routed?")
   before the operator picks it again.

The skill never writes to research sinks itself — that stays inside the `/ingest-video` flow
and its single approval gate.

## 9. Cost-estimate column

Duration-only heuristic at poll time (no transcript exists yet):

```text
est_tokens = duration_s × EST_TOKENS_PER_S
           + FRAME_CAP × EST_TOKENS_PER_FRAME
           + EST_FIXED_OVERHEAD
```

A-priori constants in `tools/yt_feed.py`: `EST_TOKENS_PER_S = 4` (≈15 speech chars/s ÷ 4
chars/token), `EST_TOKENS_PER_FRAME = 1400` (vision-input midpoint), `EST_FIXED_OVERHEAD =
8000` (two rubric-bearing subagent prompts), `FRAME_CAP = 15` imported from
`tools/video_marks.py` (upper bound — frames actually extracted may be fewer). Labeled in the
table as an estimate for **ranking** videos within a batch (±30% class), not for budgeting.
The full per-run perf ledger + calibrated estimator stays a separate project
(ingest-perf-tracking) and is out of scope here.

## 10. Testing

- Pure functions, directly tested: config load/validation, TOML defaults, ISO-8601 duration
  parse, title filters, candidate diff (floor/ledger/filter interplay), estimate arithmetic,
  state load/save/atomicity, `UU` derivation.
- Injected-`get` tests assert **request shape** — endpoint path, `key` present, `part`/
  `playlistId`/`maxResults` params, id batching at 50 — against realistic messy fixtures:
  playlist entries for deleted ("Deleted video") and private videos, a `liveBroadcastContent:
  "upcoming"` premiere, a Short (45 s), missing `contentDetails`, a 404 on the derived `UU`
  id forcing the `channels.list` fallback, HTTP 403 quota body.
- The faked-transport blind spot is named: a network-free suite cannot catch a wrong
  real-world parameter, so **one manual e2e poll against the operator's real channel config
  is a load-bearing acceptance gate** (see §12), not a nice-to-have.
- Standard gates: `make lint-py`, `make typecheck`, `make test`, `make lint-md`;
  `make test-regression` must be untouched (nothing here goes near the backtest pipeline).

## 11. Out of scope (phase 2+, recorded so they are decisions, not omissions)

- Playlist-category content-type prior (per-channel named playlists as routing hints).
- The per-item "also wifey?" handoff flag (undesigned; belongs to the digest gate).
- ST11's X feed joining `/ingest-feed` (spike on the syndication timeline endpoint pending).
- WebSub/PubSubHubbub push, OAuth, subscriptions auto-derivation, private "Ingest" playlist.
- Cron/timer automation: the review gate is human, so a timer could at most *remind*; that
  belongs to the ST3 VPS work, never to this tool.
- Full ingest perf/cost ledger (ingest-perf-tracking).

## 12. Acceptance criteria

1. Unit suite green with zero network; request-shape assertions in place.
2. Manual e2e (load-bearing): `poll` over the real config (≥2 channels incl. Cowen) lists
   sane candidates with correct durations and exclusion summaries; an aborted run (stop after
   step 2 of the skill) changes nothing on disk; a full run through `/ingest-video` +
   `mark` consumes exactly the picked/skipped ids; the next `poll` re-presents deferred
   candidates and nothing that was marked. `backfill` on one real channel pages past 50
   videos, lists below-floor candidates, and never re-presents marked ids.
3. Retrospective rule visible end-to-end on a real video that reviews a prior-day entry:
   flagged in the digest, absent from `pundit-calls.jsonl`, present in the note.
4. Docs updated: README, CLAUDE.md (tools + skills tables), `.env.example`
   (`YOUTUBE_API_KEY`), `config/youtube_channels.toml.example`.

## 13. Rejected alternatives

- **RSS feeds** (`/feeds/videos.xml?channel_id=…`): zero quota and no key, but no duration
  field — the Shorts filter and cost column both need `videos.list` anyway, at which point
  the API path is strictly more capable for the same effective cost. Kept in mind as a
  fallback if the Data API ever becomes unavailable.
- **Moving per-channel watermark**: §3.
- **Extending `/ingest-video` with a no-URL feed mode**: muddies its URL-in contract and
  grows an already 456-line skill; a separate thin skill keeps the boundary clean and gives
  ST11 a place to land.
- **`search.list` discovery**: 100 units/call; uploads playlists cover the requirement at
  1 unit.
