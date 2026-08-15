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
  images + quoted-tweet; a post in a thread is recovered upward to its root
  (bookmark the LAST post), and video is handed off to /ingest-video rather than
  skipped. Invoke when the user says
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

1. **Resolve the whole evidence graph in ONE call, per URL.** The tool walks the
   reply chain upward, resolves every quoted post to `--max-quote-depth`
   (default 2), and downloads images for **every** post it reaches — bookmarked,
   chain parents, and quoted — with a randomized cooldown *between network
   fetches* and a per-id dedup cache (a cached id costs zero network on re-run):

   ```bash
   PYTHONPATH=. poetry run python tools/x_fetch.py <url1> <url2> … --resolve --json
   ```

   Pass every pasted URL on one command line. Output is one JSON object per URL
   on stdout, `{"posts": [...], "notes": [...]}`. Each post in `posts` carries
   the flat post fields (`author`, `author_name`, `post_ts_utc`, `text`,
   `photo_urls`, `video_present`, `is_quote`, `quoted_text`, `quoted_author`,
   `quoted_id`, `quoted_photo_urls`, `text_truncated`, `edited`, …) plus:

   - `role` — `"bookmarked"` (the URL you passed) / `"chain_parent"`
     (recovered upward from it) / `"quoted"` (pulled in because something in
     the bundle quoted it).
   - `depth` — `0` for the reply chain, `+1` per quote hop.
   - `referred_by` — the status id that pulled this post in; `""` for the
     bookmarked post.
   - `photo_paths` — **local** downloaded chart files, already on disk for
     **every** post regardless of role — do NOT re-fetch. There is no second
     pass to run: `--resolve` already downloaded everything in this one call,
     and re-running `--batch` over the recovered parents would just
     double-download them.
   - `quoted_photo_paths` — local files for that post's own embedded
     quoted-tweet image, if it has one.

   For any URL that comes back `UNAVAILABLE: <url>` on stderr
   (protected/deleted/age-gated), tell the user and ask them to paste that
   post's text + drop a screenshot; continue that one from step 2 with the
   pasted text + image.

   **⚠ OPERATOR RULE — bookmark the LAST post of a thread, never the parent.**
   The endpoint exposes the reply-to chain but has no replies/children field,
   so a thread can only be recovered **upward**. A bookmarked parent yields
   nothing below it — unchanged by this call collapsing to one step.

   Traps — always check all three:

   - **`conversation_count` is NOT thread length** (it counts everyone's
     replies to the conversation — a 2-post thread routinely reads 9).
   - **Always read `notes`** — every stop, cap, cycle and skip the walk hit is
     recorded there verbatim (author change, hop cap, `max_quote_depth`
     reached, a deleted/unavailable post, an already-visited id). A bundle
     that stopped early otherwise reads as a complete one.
   - **New: `text_truncated: true` means that post is missing a long-form
     tail no keyless path can fetch.** The endpoint's `note_tweet` proves a
     longer body exists but hands back only an opaque ID stub, never the
     text. Say so to the step-2 extractor for that post — name it in
     `chart_read` or `gap_note` — rather than letting it extrapolate the
     missing tail from what little text it has.

   **`video_present: true`** on the bookmarked post or a `chain_parent` ⇒ hand
   off that post's own URL to `/ingest-video`, don't make the operator
   re-paste. `tools/video_fetch.py` already matches X status URLs
   (`_X_RE`, `parse_video_url` → `("x", <status_id>)`) and the Groq whisper
   fallback covers caption-less X video, so the same URL runs there unchanged.
   Say plainly that you are handing it off, and carry over the rest of the
   resolved bundle. Do **not** attempt the vision pass here — this skill has
   no frame extraction.

2. **Extract via a subagent — one per post, pinned to sonnet.** For each post,
   dispatch a subagent (Task tool) **with `model: "sonnet"`** (do not inherit Opus)
   and **`subagent_type: "Explore"`** — measured **3.6× cheaper** than
   `general-purpose` at identical quality on exactly this task (24,187 vs 87,975
   tokens, 2026-08-03 A/B on a real 2-post batch), the likely mechanism being that
   it does not inherit full project context. Cost here is **fixed per-subagent
   overhead, not payload** (6 varied posts landed inside a ±2% band), so the
   dispatch type is the lever and image size is not. Give it: the post
   `text` (and `quoted_text` prefixed `"[quoting @<quoted_author>]"` when present),
   the `photo_paths`, whether this post's `text_truncated` is `true`, the schema
   below, and the **inline rubric** in the next section. Instruct it to Read each
   image (vision) and return ONLY this JSON — it must NOT read any repo/SoT/memory
   file (the rubric below is self-contained; that is the whole point — one image
   Read, no 7K-token SoT re-read):

   ```json
   {
     "symbol": "BTCUSDT | null",
     "direction": "EXACTLY ONE OF: long | short | neutral | null — no other value",
     "entry": "BARE NUMBER — no commentary, no parentheticals, no hyphenated ranges — or \"\" if not stated",
     "stop": "BARE NUMBER — no commentary, no parentheticals, no hyphenated ranges — or \"\" if not stated",
     "target": "BARE NUMBER — no commentary, no parentheticals, no hyphenated ranges — or \"\" if not stated",
     "horizon": "EXACTLY ONE OF: intraday | swing | unspecified — no other value",
     "setup_type": "free text",
     "raw_quote": "the sentence(s) the call/claim came from",
     "chart_read": "what the chart shows (levels, structure, annotations); name it here if this post is text_truncated",
     "content_type": "claim | setup | mechanic",
     "verdict": "NOVEL | ALREADY-TESTED | FROZEN-CATEGORY | NOT-FALSIFIABLE",
     "is_retrospective": "true | false",
     "gap_note": "one line: implied primitive + does the system already have/test/freeze it? name it here instead if chart_read doesn't apply"
   }
   ```

   **⚠ `entry` / `stop` / `target` are MACHINE-PARSED — write each as a BARE
   NUMBER, no commentary, no parentheticals, no hyphenated ranges.** Exactly one
   number is taken from each field downstream (`tools/pundit_score.py`); a
   hyphenated range anywhere in the string beats a `/`-separated ladder and
   resolves to the range's LOW end. If the post does not state a value, use
   `""`. This is the same contract step 4 states where the row is *written* —
   stating it here, where the values are *produced*, is the fix: on 2026-08-15
   the extractor returned prose with parentheticals for all three fields, and
   it was harmless only because `direction: neutral` short-circuited before the
   parse ever ran.

   **Name any `text_truncated: true` post explicitly, in that post's
   `chart_read` or `gap_note`** — e.g. "text cuts off mid-sentence, no
   long-form body recoverable" — so a partial reading is visible in the digest
   rather than inferred after the fact.

   `verdict` applies only when `content_type = claim`; for `setup`/`mechanic` set it
   to `NOVEL` as a non-blocking default (routing uses `content_type` for those).

   **`direction` and `horizon` are closed enums, and a value outside them costs you
   the whole row.** Both are now enforced in code at both ledger read boundaries —
   `analytics/pundit_direction.py` and `analytics/pundit_horizon.py` — so the scorer
   and the Brief's pundit board each skip the line with a warning rather than
   mis-booking it. That is an improvement on what came before, but it is still a
   **loss**: the call is never scored, and nothing tells you at write time.

   - **`direction`** — anything unrecognised used to fall through to
     `dirsign = 1.0 if direction == "long" else -1.0` and book as a **SHORT**.
     Measured on `/ingest-video` 2026-08-05: a range-trade plan was emitted as
     `direction: "range"`, which would have scored a deliberately non-directional
     call as bearish. A range / chop / two-sided plan is **`neutral`** — map it there
     and say why in `setup_type`. A *missing* direction is rejected on the same
     grounds; it arrived as `""`, which is also not `"long"`, so absence scored as a
     short too.
   - **`horizon`** — an unrecognised value used to take the 14-day `unspecified`
     window instead of intraday's 48h or swing's 30d, changing the
     WIN / LOSS / NOT_TRIGGERED verdict for the same call. **Absence is fine here and
     is NOT the same as direction**: `unspecified` is a real member of the enum, so
     omit the field or write `unspecified` when no timeframe was stated. What is
     rejected is a plausible-looking near-miss — `daily`, `1h`, `short-term`,
     `position`, `scalp`.

   Casing and surrounding whitespace are folded on both (`SHORT` → `short`), so only
   genuinely new values are rejected. **The schema's `null` for `direction` is not a
   contradiction:** a `claim` or `mechanic` legitimately has no direction and those
   never route to Stream C. `null` is invalid only on a row that reaches the ledger.
   Check both values before writing any Stream C row.

   This rule exists because `/ingest-video` carried it and this skill did not, though
   both write the same Stream C rows from the same item schema — a gap found by
   hand-grepping the skill tree, not by any gate.

   **`is_retrospective` = the post describes a call whose outcome was already known
   when it was posted** — an archive repost, a past trade recapped, a chart annotated
   after the fact. Judge it from the post text plus the chart; the tell is almost
   always written down: "found this in the archives", "back in April", "this was the
   plan", past-tense narration of an entry already taken, or a price axis visibly
   stale against the post's own date. Default `false`; set `true` only on positive
   evidence, since on the `setup` path it deletes the row. Say which words or which
   axis reading drove a `true` in `raw_quote` / `chart_read` so the digest can show
   the reasoning rather than a bare boolean.

   This exists because on 2026-08-03 a five-step BTC rotation walkthrough captioned
   "found this in the archives, check out the price axis" reached the digest as a
   routable `setup`, and was stopped only by a human hand-writing a warning into that
   one subagent's prompt. Luck plus a person, not a rule — the identical post with
   entry/stop/target and no hand-written warning scores its author on a call whose
   outcome was already known.

3. **ONE consolidated review digest** for the whole batch. Print a single table —
   one row per post: author · `post_ts_utc` · symbol/direction · `content_type` ·
   `verdict` · proposed routing · **truncated?** · **edited?** · `gap_note`; note
   `quoted_text` / `video_present` / `is_thread` / `role` where set. Same
   principle as `is_retrospective` below: the operator decides, but only about
   what they can see, and `truncated?` / `edited?` are exactly that on every
   row, not just the ones the extractor happened to flag in prose. **A `quoted`
   post appears as its own row**, showing its `referred_by` (the status id
   that pulled it in), so attribution is visible before approval rather than
   buried inside its referrer's row. Show each `chart_read` and the full
   extraction JSON below the table. Write NOTHING yet.

   **Surface `is_retrospective: true` in its own column, on EVERY row — including
   `claim` and `mechanic` rows that still route.** The drop in step 4 is
   **`setup`-only by design**, so a past trade's management notes typed as `mechanic`
   route to Stream B with nothing objecting. That is not a bug to fix in the router
   (a retrospective mechanic is often still a perfectly good mechanic), but it is
   yours to see and decide on, and it is precisely the hole `/ingest-feed` shipped
   #535 for. A `true` on a routing row is a prompt to the reviewer, not a block.

   **Run the dedup check before printing the digest**, once per non-dropped post, so
   its result appears *in* the digest rather than after approval:

   ```bash
   PYTHONPATH=. poetry run python tools/route_dedup.py check \
     --source-id <status id> --item-ts 0 --sink <route_target output> \
     --text "<the gist being routed>"
   ```

   - `already_routed: true` → **do not append.** Show the row as "already routed",
     and route nothing for it in step 4. This is exact and needs no judgement.
   - `candidates` non-empty → **not a block.** Print each candidate's `excerpt` and
     `shared_levels` under that post's row and let the user decide: new row,
     corroboration line on the existing entry, or drop.
   - `semantic_scope` says what the near-duplicate pass compared against:
     `all-entries` (Streams A and B) or `same-source` (Stream C — only rows from this
     same status id, never another author's). Report it; never let an empty
     `candidates` list read as "checked against everything and clean". On Stream C the
     same-source scope is near-inert here, since one X post routes one item — the
     identity layer is what protects this sink.

4. **Route on a single approval.** After the user approves the batch, for each post
   compute the destination with
   `tools/x_route.py::route_target(content_type, verdict, retrospective=<is_retrospective>)`
   (returns the sink path or `None` for a drop) and append per this table. Pass the
   extracted `is_retrospective` through — never re-judge it here. Report a
   one-line result per post (routed → which file, or dropped → verdict).

   | content_type | verdict | Append to |
   | --- | --- | --- |
   | setup | — | `docs/plans/pundit-calls.jsonl` (one JSON line, schema below) |
   | mechanic | — | `docs/plans/mechanics-backlog.md` (a `-` list bullet) |
   | claim | NOVEL | `docs/plans/thesis-inbox.md` (a draft `H` row) |
   | claim | ALREADY-TESTED / FROZEN-CATEGORY / NOT-FALSIFIABLE | **drop** — state "seen, verdict X", write nothing |

   Create the sink file with a one-line header if it does not exist.

   **After each successful append, record it:**

   ```bash
   PYTHONPATH=. poetry run python tools/route_dedup.py mark \
     --source-id <status id> --item-ts 0 --sink <sink path>
   ```

   `mark` runs **after** the write, never before. Marking at check time would let an
   abandoned review consume the id and dedup away the real append later — the
   wifey-#68 watermark-on-send defect class. Never mark a dropped post.

   Stream C's near-duplicate exemption is **across sources only**: two pundits making
   the same call are two real observations and `tools/pundit_score.py` scores both
   authors, so collapsing those would delete signal. Within one `source_id` the pass
   does run — that matters for `/ingest-video`, where one video yields several items;
   here one post yields one. Stream C still gets the exact `already_routed` block.

   `route_target`'s other keyword flag, `rejected=`, drops a `setup` the author walked
   through and then argued **against** taking. This pipeline does not extract it, so it
   stays `False`; see `/ingest-video`'s step 6. Left open deliberately — the
   retrospective tell is written in the post text and was measured firing, while a
   talked-out-of-it setup is rarer on X and a mis-set flag silently deletes a real call.

   **Stream C line** (`pundit-calls.jsonl`, one line, matches the parent spec's
   pundit-call schema). **Re-check `direction` and `horizon` against their enums
   here** — this is the last point before the row becomes ledger evidence, and both
   are silently unrecoverable once written:

   ```json
   {"source":"twitter","author":"<handle>","url":"<url>","call_ts_utc":"<post_ts_utc>","symbol":"<symbol>","direction":"<direction>","entry":"<entry>","stop":"<stop>","target":"<target>","horizon":"<horizon>","confidence":"<verbatim hedging or empty>","raw_quote":"<raw_quote>"}
   ```

   **⚠ `target` and `entry` are MACHINE-PARSED — the format is a contract, not prose.**
   `tools/pundit_score.py` takes ONE number from the string, and **a hyphenated range
   anywhere in it beats a `/`-separated ladder and resolves to the range's LOW end.**
   Write `target` as a bare `/`-separated ladder — `67,000 / 70,362.23 / 82,000` — with
   ranges in `raw_quote` instead. **A clarifying parenthetical re-breaks it**: the
   constraint is on the whole field, not its leading number. `entry` degrades gently (a
   `60.0K-61.2K` box parses to the 60,600 midpoint — fine for a zone, wrong for a
   target). The error only ever pushes the target further away, **understating that
   author's hit rate**, so it reads as "these pundits are bad" rather than as a bug.
   Same rule as `/ingest-video` step 8 — both skills write this file, one parser scores
   both.

   **Run `make buibui-pundit-score` as the last action of the round** — reading the row
   back does not show you the parse, and at round-end every row is still OPEN, so a bad
   parse is free to fix then and invisible later.

   **`author` is whoever MADE the call, not whoever posted the tweet you fetched.**
   This is the same rule `/ingest-video` states for relays, and it bites here through
   quote-tweets: when `is_quote` is true and the call lives in `quoted_text` rather
   than in the poster's own `text`, credit **`quoted_author`**. Crediting the quoter
   assigns a real call to the wrong trader, and `pundit_score.py` groups on `author`,
   so the wrong person's track record moves with **nothing downstream able to notice**.

   **Latent, not hypothetical-and-harmless:** an audit on 2026-08-03f found 28/28
   routed quote-tweets correctly attributed — but all 28 were self-quotes (27) or a
   reply-quote (1), i.e. cases where quoter and quoted are the same person and the
   rule cannot be observed to fire. **The first genuine third-party quote-tweet is the
   first test this has ever had**, so do not read that 28/28 as coverage.

   When quoter and quoted differ, say so in the digest line so the approver sees which
   name the row will carry before it is written.

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
