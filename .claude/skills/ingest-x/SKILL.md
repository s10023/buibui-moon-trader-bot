---
name: ingest-x
description: >
  Ingest one or more X/Twitter post URLs into the research pipeline: each post's
  reply chain, quoted posts and images are resolved without login, charts are read
  with vision, and after one human review gate for the whole batch each item is
  routed to the thesis inbox, the mechanics backlog or the pundit-calls ledger.
  Video is handed to /ingest-video. Invoke when the user says "/ingest-x", pastes
  x.com or twitter.com status URLs, or asks to ingest an X post.
allowed-tools: Bash, Read, Write, Edit, Task
---

# Ingest X post(s)

Spec: `docs/superpowers/specs/2026-06-30-x-post-ingest-design.md`
(iteration-2 batch/cooldown/cache/sonnet: `docs/superpowers/plans/2026-07-01-x-ingest-iter2.md`;
upward thread walk: `docs/superpowers/specs/2026-08-10-x-thread-walk-design.md`;
graph resolution — quoted-post promotion, `--resolve`, truncation/edit flags:
`docs/superpowers/specs/2026-08-15-x-ingest-graph-resolution.md`).

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

   Pass every pasted URL on one command line. Output is **one JSON array** on
   stdout — one element per pasted URL, in the order you passed them:
   `{"url": <the URL you passed>, "posts": [...], "notes": [...]}`. An
   unavailable URL still gets an element, with `posts: []` and the reason in its
   `notes`, so **N inputs always give N outputs** and every bundle is tied back
   to its own input. (Emitting one top-level object per URL is what this used to
   do, and `json.loads` rejects two of those concatenated.) Each post in `posts` carries
   the flat post fields (`author`, `author_name`, `post_ts_utc`, `text`,
   `photo_urls`, `video_present`, `is_quote`, `quoted_text`, `quoted_author`,
   `quoted_id`, `quoted_photo_urls`, `text_truncated`, `edited`, and the numeric
   account ids `author_id` / `quoted_author_id` / `in_reply_to_author_id` — empty
   when the payload carried none, and the only field that survives a handle
   rename, so quote it rather than the handle when noting an author's identity …)
   plus:

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
   pasted text + image. The `! stopped at <id>: …` line printed beneath it is
   the **why** — `HTTP 404` reads differently from `post unavailable
   (protected/deleted/age-gated)`, and that is the distinction the ask to the
   user turns on. It is also in that element's `notes`.

   **`--force` ignores the dedup cache** on every hop of the graph — the chain
   walk and each quoted post. Use it when a post is flagged `edited: true` (the
   cached text is then not the text that was posted), and when `notes` reports an
   image that failed to download: a cache entry is authoritative for every field,
   so nothing else re-fetches it. Everything else should run cached — that is
   what keeps a re-run at zero network.

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
     that stopped early otherwise reads as a complete one. Three entries are
     easy to skim past and change what the evidence IS: **a quoted post that is
     itself a reply** (its own chain is deliberately not walked — bounded, but
     the argument above it is missing), **a quote whose payload carries no id**
     (nothing to resolve; only the referrer's truncated `quoted_text` survives),
     and **`N of M images failed to download`** — that chart is not on disk, the
     entry cached anyway, and only `--force` retries it. Say so in the digest;
     never let the extractor read a missing chart as a post without one.
   - **New: `text_truncated: true` means that post is missing a long-form
     tail no keyless path can fetch.** The endpoint's `note_tweet` proves a
     longer body exists but hands back only an opaque ID stub, never the
     text. Say so to the step-2 extractor for that post — name it in
     `chart_read` or `gap_note` — rather than letting it extrapolate the
     missing tail from what little text it has. **The TRUNCATION HALT below fires on this and
     offers the operator the paste that is the only repair — naming it in
     `gap_note` is the fallback for text they declined to supply, not the
     first response.**

   **`video_present: true`** on the bookmarked post or a `chain_parent` ⇒ hand
   off that post's own URL to `/ingest-video`, don't make the operator
   re-paste. `tools/video_fetch.py` already matches X status URLs
   (`_X_RE`, `parse_video_url` → `("x", <status_id>)`) and the Groq whisper
   fallback covers caption-less X video, so the same URL runs there unchanged.
   Say plainly that you are handing it off, and carry over the rest of the
   resolved bundle. Do **not** attempt the vision pass here — this skill has
   no frame extraction.

   **TRUNCATION HALT — let the operator repair a cut-off body, BEFORE any
   extraction.** Keep step 1's JSON, then screen it:

   ```bash
   PYTHONPATH=. poetry run python tools/x_fetch.py <urls…> --resolve --json > /tmp/x-<date>.json
   python3 tools/x_truncated.py /tmp/x-<date>.json
   ```

   **Exit 1 means STOP.** Show the operator the block verbatim — it leads with the
   counts (`14 of 18 post(s) truncated across 5 of 7 bundle(s)`), stars the bookmarked
   posts, flags a post that appears in two bundles, and closes with the distinct-URL
   count. Ask them to paste the full bodies, then splice each pasted body over that
   post's `text` when you compose the step-2 prompt. Exit 0 carries straight on; exit 2
   means the JSON is unreadable — fix that, and never read it as "nothing truncated".

   **Why a HALT and not a note.** The endpoint's `note_tweet` proves a longer body
   exists and returns only an opaque ID stub, so **no re-fetch, no `--force` and no
   re-ingest recovers the tail — only a human paste does.** Step 1's trap bullet records
   the damage at the one moment it is still repairable, and then proceeds anyway.

   ⚠ **Ask for TEXT ONLY — never ask the operator to paste images.** `--resolve` has
   already downloaded every chart in the bundle to `.cache/x-media/` and step 2 reads
   them from disk; `text_truncated` is about the body alone. Asking for images costs the
   operator real effort for something already in hand, so say "text only" in the ask.

   **A partial paste is fine and is the common case.** The starred bookmarked posts
   drive their items; a depth-2 quoted post is context. Record what you got either way —
   step 5's `text_source` is what makes a partial round auditable later.

   **What the paste buys, measured 2026-08-26 on the astronomer_zero set: 6 of 7 pasted
   posts changed the extracted item, and two changed its SIGN.** One tail ended `"the
   hard SL … at 65.1k"` — truncated, `stop` was `""`, and a Stream C row with no stated
   stop scores **no `avg_r` at all**, so truncation was quietly converting stop-stating
   authors into stop-less ones. That bias is not random: it penalises exactly the authors
   disciplined enough to put invalidation in the long-form body. Another cut off one
   clause before `"I'm now not so bullish anymore"`, which would have filed a bullish
   continuation for a post announcing a bias flip.

2. **Extract via a subagent — ONE per resolved bundle, pinned to sonnet.** One
   pasted URL → one dispatch → one item. Dispatch a subagent (Task tool) **with
   `model: "sonnet"`** (do not inherit Opus) and **`subagent_type: "Explore"`** —
   measured **3.6× cheaper** than `general-purpose` at identical quality on exactly
   this task (24,187 vs 87,975 tokens, 2026-08-03 A/B on a real 2-post batch), the
   likely mechanism being that it does not inherit full project context. Cost here
   is **fixed per-subagent overhead, not payload** (6 varied posts landed inside a
   ±2% band), so the dispatch type is the lever, and bundle size is not.

   **Report this round's cost in the review digest and APPEND it to the series below, every
   run.** Nothing else catches a silent agent-type fallback: the item JSON is byte-identical
   either way, so an unannounced `general-purpose` dispatch — or an inherited Opus — restores
   the full cost while looking exactly like success. Series:

   | date | dispatches | mean | median | range | per image Read | reading |
   | --- | --- | --- | --- | --- | --- | --- |
   | 2026-08-26 | 10 | 27.7K | 24.9K | 22.4–41.3K | — | `Explore`, no fallback |

   **Record `tokens / images_read` beside the mean, not the mean alone.** A bare mean tracks
   bundle image count as much as it tracks agent type: both outliers in the baseline round are
   fully explained by tool_uses — **41.3K at 12 image Reads and 39.5K at 6, against 22–25K at
   1–3** — and X bundles carry 0–8 images where a chart batch is one per dispatch. So an
   unnormalised 41.3K against a 27.7K baseline is **1.49×**, which under `/ingest-charts`'
   ≥1.4×-is-a-fallback rule would fire on a perfectly clean round.

   ⚠ **Do NOT reinstate an absolute band, and note this file argued FOR one until 2026-09-01.**
   The reasoning was that cost here is fixed per-dispatch overhead rather than payload (6 varied
   posts inside a ±2% band), so unlike charts there is no legitimate drift to confuse with a
   step. **The ±2% was measured across posts at a FIXED image count and does not survive the
   image-count spread the same round measured** — 22.4K to 41.3K is 1.85× inside one batch with
   no fallback in it. Compare against the **LAST RECORDED ENTRY** instead.

   The discriminator is the **~3.6×** step of the A/B above (24.2K → 88.0K), far wider than the
   ~1.5× charts has to work with — so a per-image figure near 3× is `general-purpose`, one
   within ~1.2× is the same dispatch type, and **anything between is image count until you have
   ruled that out**. ⚠ **EXCLUDE revision dispatches from the mean** — a resumed agent re-run
   after a truncation repair is a second dispatch on the same bundle (3 such in the baseline
   round at 31.6K / 53.3K / 31.1K), so folding them in double-counts; if the truncation halt
   keeps firing, give revision cost its own column. Expect the baseline to climb regardless:
   the inline rubric below is pasted into every prompt and grows as verdicts are filed.

   **If subagent dispatch is unavailable, do the vision pass IN THE MAIN THREAD — do not
   skip it, and do not extract from text alone.** A session can be barred from dispatching
   (a harness policy, a classifier block), and the charts carry levels the text never
   states, so a text-only extraction is the one outcome worse than paying main-thread
   context. Read each `photo_paths` entry directly and produce the same item JSON.

   ⚠ **A refusal can be PER-DISPATCH and non-sticky, so retry the bundle in-thread before
   concluding dispatch is lost for the session.** Measured 2026-08-28: exactly **1 of 14**
   dispatches in one round was refused while the other 13 ran normally. Read a single block
   as evidence about that call, not about the harness — fall back to the main thread for
   **that bundle only**, and keep dispatching the rest. Treating one refusal as a
   session-wide bar pays main-thread context for a whole round that never needed it.
   **Record it**: put `main-thread vision (subagent dispatch withheld)` in the Stream C
   row's `extraction_path` and say so in the note. That field already records this case on
   a 2026-07-31 row (`main-thread vision (subagent dispatch blocked by classifier)`) and
   across the 2026-08-26 round, so it is the established home for it rather than a new
   field — and without it a later reader cannot tell a
   main-thread extraction from a sonnet one, which matters because the two have different
   cost and consistency profiles, not different authority.

   Give it **every post in the bundle, in `posts` order** (chain root → leaf, each
   quoted post after its referrer), and for each one: `role`, `depth`, `@author`,
   `post_ts_utc`, `text` (with `quoted_text` prefixed
   `"[quoting @<quoted_author>]"` when that quoted post could NOT be resolved into
   the bundle), `photo_paths`, `quoted_photo_paths`, and whether that post's
   `text_truncated` is `true`. Plus the schema below and the **inline rubric** in
   the next section. Instruct it to Read each image (vision) and return ONLY this
   JSON — it must NOT read any repo/SoT/memory file (the rubric below is
   self-contained; that is the whole point — image Reads, no 7K-token SoT re-read):

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
     "chart_read": "what the charts show (levels, structure, annotations), across the bundle; name any text_truncated post or missing image here",
     "content_type": "claim | setup | mechanic",
     "verdict": "NOVEL | ALREADY-TESTED | FROZEN-CATEGORY | NOT-FALSIFIABLE",
     "is_retrospective": "true | false",
     "gap_note": "one line: implied primitive + does the system already have/test/freeze it? name it here instead if chart_read doesn't apply"
   }
   ```

   **⚠ NEVER dispatch one post of a chain on its own.** A single post from a
   thread is a **fragment**, and extracting from a fragment is how **13 of 167
   cached posts** were classified without their context. The chain is one author's
   argument by construction — the upward walk stops the moment the author changes —
   so feed the **whole chain** to one subagent, in order, as one argument, and let
   it emit one item. **That is why the dispatch is per BUNDLE and not per post:**
   a per-post dispatch turns "also pass the siblings" into a prompt-content rule,
   and this repo has already lost that rule once by rewriting the step around it.
   One dispatch per bundle makes the fragment case *unreachable* rather than
   *forbidden*. Spec §10 Q1 (2026-08-15) records the same decision for quoted
   posts: they are context inside this item, not items of their own.

   **`role` and `depth` are weights, not decoration** (spec §7). `bookmarked` is
   the post the operator chose and `chain_parent` the same author's earlier
   argument — both `depth: 0`, both their own words, and both drive the item. A
   `quoted` post at `depth ≥ 1` is context the author **cited**, is frequently
   someone else's words, and is over-collected on purpose at depth 2: it qualifies
   the item, it never drives it alone. Tell the extractor to name in `raw_quote`
   which post the call came from — step 4 needs that to date and attribute the row.

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

   **A level named as the CONDITION for entry IS the `entry`, and a CONTINGENT
   stop-management instruction is NOT a `stop`.** Both were measured on the
   sibling lane (`/ingest-video`, 2026-08-13d) and both corrupt the same ledger
   this skill writes, so the rules are stated in both places rather than in
   whichever one happened to hit them first. A conditional plan HAS an entry —
   writing "no numeric trigger stated" leaves the field empty, the scorer falls
   back to the call-time price, and the author is booked in position on a trade
   they said they had not taken. And "move stop to breakeven at X if <event>" is
   not a stop: written as one against an equal `entry` it scores an instant
   **0.00R LOSS**, a manufactured loser. `stop` is the INVALIDATION level; the
   management instruction is already verbatim in `raw_quote`. An empty `stop` is
   legitimate and must stay legitimate.

   **Name every `text_truncated: true` post explicitly — by `role` and author —
   in the item's `chart_read` or `gap_note`** — e.g. "chain_parent @x cuts off
   mid-sentence, no long-form body recoverable" — so a partial reading is visible
   in the digest rather than inferred after the fact. Same for any image the
   bundle's `notes` said failed to download.

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
     and say why in `entry`, **never `setup_type`** — that field is on the pass-2
     item schema only, the Stream C line has no such key, and no row has ever
     carried it (**0 of 328** at 2026-08-26), while `entry` reaches the ledger and
     feeds `tag_family`. ⚠ Prose in
     `entry` is safe on a `neutral` row ONLY: `score_call` returns UNSCORED before
     `resolve_levels` parses it, so this licenses nothing where the machine-parsed
     contract below applies. A *missing* direction is rejected on the same
     grounds; it arrived as `""`, which is also not `"long"`, so absence scored as a
     short too. **A same-author dedup match in the OPPOSITE direction at the same
     level is the range case wearing two rows** (operator ruling 2026-08-27, S2):
     when the source frames both legs as one plan, route ONE `neutral` row naming
     both legs in `entry` — never the two contradictory directional rows, which
     mechanically hedge the author's own sample. A genuine flip is a new directional
     row, not a conflict; the discriminator is the source's own framing. Exemplar
     and the retroactive-routing bar: `/ingest-video`'s copy of this rule, in
     `references/pass-2-field-rules.md`.
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

3. **ONE consolidated review digest** for the whole batch. Print a single table,
   **one row per routable item — which is one row per pasted URL**: author (the
   one being credited) · `call_ts_utc` · symbol/direction · `content_type` ·
   `verdict` · proposed routing · **`is_retrospective`** · `gap_note`.

   Under each row, list that bundle's posts as **evidence lines**: `role` ·
   `depth` · `@author` · `post_ts_utc` · **truncated?** · **edited?** · image
   count, plus `referred_by` on every `quoted` post and `video_present` /
   `quoted_text` where set. Truncation and editing are per-POST facts, which is
   why they live there rather than only on the item row — same principle as
   `is_retrospective` below: the operator decides, but only about what they can
   see, and on every post rather than the ones the extractor happened to mention
   in prose. **A `quoted` post is evidence under its referrer, not a routing row
   of its own** (spec §10 Q1) — but name its author on that line, because when
   quoter and quoted differ, that name is the one the row will carry. Show each
   `chart_read` and the full extraction JSON below the table. **Close with this round's
   `subagent_tokens` — mean, range, and per image Read — the previous series entry, and which
   reading you got** (step 2). That comparison is the only fallback check there is, so the
   digest is where it has to land. Write NOTHING yet.

   **Surface `is_retrospective: true` in its own column, on EVERY row — including
   `claim` and `mechanic` rows that still route.** The drop in step 4 is
   **`setup`-only by design**, so a past trade's management notes typed as `mechanic`
   route to Stream B with nothing objecting. That is not a bug to fix in the router
   (a retrospective mechanic is often still a perfectly good mechanic), but it is
   yours to see and decide on, and it is precisely the hole `/ingest-feed` shipped
   #535 for. A `true` on a routing row is a prompt to the reviewer, not a block.

   **Run the dedup check before printing the digest**, once per non-dropped item, so
   its result appears *in* the digest rather than after approval:

   ```bash
   PYTHONPATH=. poetry run python tools/route_dedup.py check \
     --source-id <status id of the post the item came from> --item-ts 0 \
     --sink <route_target output> --text "<the gist being routed>"
   ```

   - `already_routed: true` → **do not append.** Show the row as "already routed",
     and route nothing for it in step 4. This is exact and needs no judgement.
   - `candidates` non-empty → **not a block.** Print each candidate's `excerpt` and
     `shared_levels` under that item's row and let the user decide: new row,
     corroboration line on the existing entry, or drop.
   - `semantic_scope` says what the near-duplicate pass compared against:
     `all-entries` (Streams A and B) or `same-source` (Stream C — only rows from this
     same status id, never another author's). Report it; never let an empty
     `candidates` list read as "checked against everything and clean". On Stream C the
     same-source scope stays **near-inert**, because every routed row carries a
     distinct source id — `_comparable_entries` compares only rows whose id matches,
     so two rows out of one bundle are never compared with each other, and every
     check runs before any write anyway. **The identity layer, not this pass, is what
     protects this sink** — which is why the `--source-id` rule in step 4 is the one
     that matters here.

4. **Route on a single approval.** After the user approves the batch, for each item
   compute the destination with
   `tools/x_route.py::route_target(content_type, verdict, retrospective=<is_retrospective>)`
   (returns the sink path or `None` for a drop) and append per this table. Pass the
   extracted `is_retrospective` through — never re-judge it here. Report a
   one-line result per item (routed → which file, or dropped → verdict).

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
     --source-id <status id of the post the item came from> --item-ts 0 \
     --sink <the FULL path route_target returned, e.g. docs/plans/pundit-calls.jsonl>
   ```

   ⚠ **A bare filename is REJECTED since ST99** — `--sink` is one of three full
   paths, because `_key` includes it and a bare `mechanics-backlog.md` writes a row
   nothing can ever match. 26 rows went in that way before the check existed.

   ⚠ **Write the `mark` calls out ONE PER LINE — never drive them from a shell
   loop.** `--sink` is an argparse `choice`, so a variable that arrives empty is
   rejected rather than defaulted, and the usual loop idiom (`for spec in "id path";
   do set -- $spec; …`) **silently passes an empty `$2` under zsh**, which does not
   word-split unquoted parameters the way bash does. Measured 2026-09-01: all four
   marks in one round errored this way. That failure was loud and total, which is the
   lucky case — a loop that marks SOME rows and not others leaves the round half
   recorded, and the reconciler then reports `unmarked`: the row IS in the sink but is
   dedup-blind from then on, so a later re-ingest of the same post appends a duplicate.
   **The repair for `unmarked` is to re-run `mark`, never to re-append** — the row is
   already there, and appending again is the defect the mark exists to prevent.

   **⚠ `--source-id` is THAT post's own status id — the `ResolvedPost` you are
   crediting — never the pasted URL's id by reflex.** `route_dedup._key` is
   `(source_id, round(item_ts), sink)` and this skill hardcodes `--item-ts 0`, so
   two rows sharing a source id and a sink collapse to one: the second reads
   `already_routed: true` and step 3 says **do not append**. One bundle now holds
   several posts, so reusing the bookmarked post's id for a second row silently
   drops exactly the chain-parent or quoted call this whole flow exists to recover.
   `route_dedup._key`'s own docstring is the same warning from the video side:
   "Never `source_id` alone — one video legitimately yields several items." When the
   post you are crediting IS the URL you pasted — the usual case — the two ids are
   identical and nothing changes.

   `mark` runs **after** the write, never before. Marking at check time would let an
   abandoned review consume the id and dedup away the real append later — the
   wifey-#68 watermark-on-send defect class. Never mark a dropped item.

   Stream C's near-duplicate exemption is **across sources only**: two pundits making
   the same call are two real observations and `tools/pundit_score.py` scores both
   authors, so collapsing those would delete signal. Within one `source_id` the pass
   does run — that matters for `/ingest-video`, where one video yields several items
   under a single id. Here each routed row carries its own post's id, so the pass stays
   near-inert and the exact `already_routed` block is the protection that fires.

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

   **⚠ `call_ts_utc` and `url` come from the POST the call came from, not from the
   bundle.** Every post keeps its own `post_ts_utc` for this reason: a thread spans
   time, so the call time is the timestamp of the post the item came from, or the
   **leaf** — the last post, the one you were told to bookmark — if it cannot be
   attributed to one. The leaf is the conservative choice because it gives the call
   the **shortest** forward window. `call_ts_utc` is scoring input for
   `tools/pundit_score.py`: dating a call to the root of a three-day thread hands
   that author three extra days to be right, and nothing downstream can see it.

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

5. **Write the per-bundle note — for DROPPED bundles too.** One file per resolved
   bundle (not per item) at
   `docs/plans/x-notes/<date>-<author-slug>-<status_id>.md`, already gitignored via
   `docs/plans/`. `<date>` is the ingest date (UTC, `YYYY-MM-DD`); `<status_id>` is
   the BOOKMARKED post's id, the same one that names the bundle everywhere else.

   ```bash
   author_slug=$(printf '%s' "$AUTHOR" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9' '-' | sed -E 's/^-+|-+$//g' | cut -c1-40)
   note_path="docs/plans/x-notes/$(date -u +%F)-$author_slug-$STATUS_ID.md"
   ```

   **`status_id`, not a text slug — this is the rule, not a collision fallback.** An X
   post has no title at all, so any slug is derived from body text: a CJK-only or
   emoji-only post slugifies to the empty string and every note from that account
   collapses onto one path, silently overwriting all but the last. `/ingest-video`
   learned this as an 8-way collision on a single `<date>-tiabtc-btc.md` (its step 9);
   here the failure is not an edge case but the default for a whole class of post.

   Carry `status_id:` in the FRONTMATTER, not only in the filename, so the note is
   greppable the way `/ingest-video`'s `video_id:` is — that grep is what lets a later
   run see this bundle was already ingested.

   Carry a `text_source:` map there too, one entry per post — `syndication` (full text
   as fetched), `operator_paste` (the tail was supplied by hand at the truncation halt), or
   `truncated_unrepaired` (truncated and NOT pasted). **Do not fold the third into the
   first.** "The text was complete" and "the text was cut and nobody fixed it" are
   different claims about the evidence, and only an explicit value tells a later reader
   which one a `raw_quote` rests on — the same reason `/ingest-video` keeps
   `captions_unknown` separate from `auto`.

   Body: the extraction JSON as returned, the routing decision per item (sink path, or
   dropped plus the verdict), and the drop reason in the author's own terms.

   **Carry `route:` in the FRONTMATTER as well** — the full sink path, or `dropped`.
   Prose alone is not enough: step 6 reconciles this key, and the 2026-08-26 round wrote
   the decision only as a `## Routing decision` section on 11 of its 18 notes. The
   reconciler reads that prose form as a fallback, but only the frontmatter key is a
   contract. One bundle routing several items to different sinks lists them
   comma-separated.

   **Why this exists, and why it is not optional.** A routed item leaves a ledger row,
   but a DROPPED bundle currently leaves ZERO trace anywhere — not in
   `routed-ledger.json` (`mark` never runs on a drop, deliberately), not in a sink
   file, not in the digest once the session ends. So the same post can be re-ingested
   and re-judged indefinitely, and a verdict nobody can find is a verdict nobody can
   challenge. `/ingest-video` has written this note since #522; this pipeline routes
   the same items into the same sinks and did not, which is the sibling-skill omission
   class `/post-branch` step 4 exists to catch.

   It costs no extra tokens: every field is already in hand at digest time.

6. **Reconcile the round before you report it. This is a GATE, not a summary.**

   ```bash
   PYTHONPATH=. poetry run python tools/route_reconcile.py docs/plans/x-notes/<date>-*.md
   ```

   Exit 1 means at least one declared route did not happen. **Fix it and re-run before
   telling the operator the round landed** — do not report a round the reconciler reds.

   **Why this is a separate step and not "be careful during step 4".** Step 4 records
   each route as it goes, which is per-item and unverified in aggregate. On the
   2026-08-25 round that was not enough: **19 Stream C setups were declared routed and
   never written.** Streams A and B landed, the A/B `mark` calls ran at 11:34:14-16, the
   notes were written at 11:35:43, and Stream C's append simply never ran. Nobody noticed
   for two days, and `route_dedup seed` could not have repaired it — seed reconstructs
   FROM the sink, and the sink was empty. **The notes were the only copy.**

   **A note's `route:` is a DECLARATION, and a ledger mark is a SECOND declaration.**
   Only the sink is evidence, which is why the tool reports `unperformed` even when the
   mark is present rather than letting the mark reassure you. Re-running the mutation on
   the repaired ledger reproduces exactly those 19.

   Read the verdicts as follows — `cited` and `undeclared` are the two that look clean
   and are not the same as `ok`:

   | verdict | means |
   | --- | --- |
   | `ok` | the row is in the sink and the ledger is marked |
   | `dropped` | declared dropped, absent from every sink |
   | `cited` | declared dropped but the id appears in a PROSE sink — normally a corroboration line on an existing entry, which is legitimate; confirm it did not become an entry of its own |
   | `unperformed` | **the 08-25 defect** — declared a sink, the row is not there |
   | `unmarked` | landed but no ledger mark, so it is dedup-blind from here; the tool names a malformed mark if one exists rather than letting it read as never-marked |
   | `phantom` | declared dropped but IS a live Stream C row — unreviewed content in the sink |
   | `undeclared` | no `route:` key and no recognised prose verdict; nothing was checked, so this is a finding, never a pass |
   | `unknown-sink` | the route names something that is not one of the three sinks (ST99's bare-filename shape) |

   ⚠ **Stream C attribution is exact and the prose sinks are not.** A Stream C row carries
   its own `url`; Streams A and B persist no per-entry source field, so an id inside an
   entry may be its origin or a predecessor / successor / corroboration citation. That is
   why a prose hit on a dropped item is `cited` rather than `phantom` — measured on the
   08-26 round, 4 of 4 such hits were corroboration lines, one saying so verbatim.

## Inline classification rubric (self-contained — paste into the subagent prompt)

> A distilled snapshot of the Frozen / Closed / Parked planning state so the subagent
> classifies from the prompt alone. **Refresh periodically from memory
> `project_do_not_relitigate.md` plus the `parked` and closed GitHub Issues** (planning
> left `project_todo_master.md` on 2026-09-29) — treat as a de-biasing prior, not gospel; NOVEL still passes the
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
- Structural first-touch entries (FVG / OB / EQH-EQL / BOS): the 2026-06-26 BUILD was
  **WITHDRAWN 2026-08-18** — NO-EDGE on all six cells under confirmation causality, all six
  flipping sign. Do not file it as a live opener.
- Funding **carry** sleeve: audited, FAILS the gate → shelved.
- Absolute **trend** (EWMAC): real but sub-gate (+0.36) → shelved as a diversifier.
- Cross-sectional **XS momentum**: the gate-clearing **deploy core** (+1.375) — not novel.
- **Coinbase premium** (H14): NO-EDGE — a genuinely NEW data source that still
  found nothing. New information buys a test, never an edge.
- **USD/JPY carry-unwind** (H15): NO-EDGE / INSUFFICIENT on every cell in every
  panel; the second new source in a row to come back empty.
- Spot-perp **CVD divergence**: all 10 pre-registered trials FAIL → shelved
  (XS −0.147, PBO 0.849; TS +0.183, DSR 0.532). Decorrelated from the deploy
  core, so the failure is missing signal rather than redundancy.
- **Indicator CHARACTER — RSI / Stoch RSI / MACD / TD Sequential / oscillator
  readings, overbought-oversold magnitude, and momentum DIVERGENCE of every kind
  (regular or hidden, bullish or bearish): H8 returned NO, and the amendment kept
  character at NO**; its price-*location* half was look-ahead and is withdrawn (#952). ⚠ **This
  bullet exists because the frozen list above contains NO oscillator**, so an
  RSI-divergence post reads as `NOVEL` to anyone classifying from the frozen list
  alone — and it is one of the most common shapes in this corpus (2 of 5 bundles on
  2026-09-01). The verdict is `ALREADY-TESTED`; reaching for `FROZEN-CATEGORY`
  is the wrong route and will read as a rubric bug to the next person.
- **Long-horizon MA rejection** (50W SMA and family) as a signal: measured as a
  **CLIFF with n_eff ~3** — hand-tradeable, structurally unbuildable, already covered.
- **Multi-timeframe / HTF agreement**: measured **INVERTED** — the live book is
  counter-trend, so "HTF and LTF agree" is not the edge it appears to be.

**Parked / data-blocked — a GROUPING, not a verdict.** The enum is exactly
`NOVEL` / `ALREADY-TESTED` / `FROZEN-CATEGORY` / `NOT-FALSIFIABLE`. Each item below
still emits one of those four, named inline. `PARKED` and `DATA-BLOCKED` are **not**
verdict values — `route_target` raises `ValueError: unroutable` if you emit one.

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
- No scraping, no login, no paid API — the public syndication endpoint
  (`cdn.syndication.twimg.com/tweet-result`) and manual-paste are the only two
  fetch paths.
- Traversal is upward-only: the endpoint exposes no replies/children field, so
  a thread can only be recovered from its LAST post — see the operator rule
  in step 1. A bookmarked parent yields nothing below it.
- Long-form post bodies are detectable (`text_truncated`) but not recoverable
  from this endpoint — `note_tweet` proves a longer body exists and hands
  back only an opaque ID stub, never the text.
