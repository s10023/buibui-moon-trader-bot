---
name: ingest-x
description: >
  Ingest one OR MORE X/Twitter post URLs into the research pipeline in a single
  call. Fetches each post's text + chart image with NO login/scraping via the
  public syndication endpoint (tools/x_fetch.py) — batched with a randomized
  cooldown + a dedup cache so re-runs hit zero network — reads each chart with
  vision in a per-post subagent, classifies it (content-type gate -> the parent
  pipeline's 4-bucket verdict taxonomy), and routes it (after ONE human review
  gate for the whole batch) into one of three streams: A hypotheses ->
  docs/plans/thesis-inbox.md, B mechanics -> docs/plans/mechanics-backlog.md,
  C daily setups -> docs/plans/pundit-calls.jsonl. Iteration 2 = text + still
  images + quoted-tweet; video is detected and skipped. Invoke when the user says
  "/ingest-x", pastes one or more x.com / twitter.com status URLs, or says
  "ingest this/these X post(s)".
allowed-tools: Bash, Read, Write, Edit, Task
---

# Ingest X post(s)

Spec: `docs/superpowers/specs/2026-06-30-x-post-ingest-design.md`
(iteration-2 batch/cooldown/cache/sonnet: `docs/superpowers/plans/2026-07-01-x-ingest-iter2.md`).

Handles **one or many** URLs in a single invocation. Collect every URL the user
pasted, then run the flow once over the whole set.

## Flow

1. **Fetch the whole batch in ONE call.** The tool fetches each post once (text +
   chart paths), with a randomized cooldown *between network fetches* and a
   per-id dedup cache (a re-fetched URL returns `"cached": true` with no network,
   no sleep). Always use `--batch` so the output shape, cache, and downloaded
   chart paths are uniform even for a single URL:

   ```bash
   PYTHONPATH=. poetry run python tools/x_fetch.py <url1> <url2> … --batch --json
   ```

   Output is a JSON **array**; per element: `url`, `cached`, `photo_paths`
   (local chart files, already downloaded — do NOT re-fetch), and either `post`
   (`author`, `author_name`, `post_ts_utc`, `text`, `photo_urls`, `video_present`,
   `is_thread`, `is_quote`, `quoted_text`, `quoted_author`) or `unavailable`
   (reason). For any `unavailable` element (protected/deleted/age-gated), tell the
   user and ask them to paste that post's text + drop a screenshot; continue that
   one from step 2 with the pasted text + image. Do NOT run the old `python -c`
   download one-liner — `photo_paths` already holds the local files.

2. **Extract via a subagent — one per post, pinned to sonnet.** For each post,
   dispatch a `general-purpose` subagent (Task tool) **with `model: "sonnet"`**
   (do not inherit Opus) and `subagent_type: "general-purpose"`. Give it: the post
   `text` (and `quoted_text` prefixed `"[quoting @<quoted_author>]"` when present),
   the `photo_paths`, the schema below, and the **inline rubric** in the next
   section. Instruct it to Read each image (vision) and return ONLY this JSON — it
   must NOT read any repo/SoT/memory file (the rubric below is self-contained; that
   is the whole point — one image Read, no 7K-token SoT re-read):

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

   `verdict` applies only when `content_type = claim`; for `setup`/`mechanic` set it
   to `NOVEL` as a non-blocking default (routing uses `content_type` for those).

3. **ONE consolidated review digest** for the whole batch. Print a single table —
   one row per post: author · `post_ts_utc` · symbol/direction · `content_type` ·
   `verdict` · proposed routing · `gap_note`; note `quoted_text` / `video_present` /
   `is_thread` / `cached` where set. Show each `chart_read` and the full extraction
   JSON below the table. Write NOTHING yet.

4. **Route on a single approval.** After the user approves the batch, for each post
   compute the destination with `tools/x_route.py::route_target(content_type, verdict)`
   (returns the sink path or `None` for a drop) and append per this table. Report a
   one-line result per post (routed → which file, or dropped → verdict).

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

## Inline classification rubric (self-contained — paste into the subagent prompt)

> A distilled snapshot of the SoT's Frozen / Closed / Parked state so the subagent
> classifies from the prompt alone. **Refresh from `project_todo_master.md`
> periodically** — treat as a de-biasing prior, not gospel; NOVEL still passes the
> human gate. `content_type`: **setup** = a specific symbol+direction+levels trade
> call → Stream C; **mechanic** = an exit/risk/data/microstructure execution rule →
> Stream B; **claim** = a generalizable market-behaviour assertion → verdict below.

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
  ([[usdt-dominance-hypothesis]]) — if a post reasserts it, `ALREADY-TESTED`-style
  "already in thesis-inbox", don't duplicate the H-row.

**`NOT-FALSIFIABLE`:** vibes / no testable prediction / unfalsifiable hindsight.
**`NOVEL`:** a genuinely new, testable, uncovered market-behaviour claim.

## Guardrails

- Output is a hypothesis/setup/mechanic to TEST — never an "add a detector" task. The
  detector list is frozen.
- Never auto-write a stream file before the user approves the digest — one approval
  covers the whole batch.
- Iteration 2: text + still images + quoted-tweet surfacing. No video, no
  thread-walking, no reply bodies, no scraping. Syndication + manual-paste are the
  only two fetch paths.
