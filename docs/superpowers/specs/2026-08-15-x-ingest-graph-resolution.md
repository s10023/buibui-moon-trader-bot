# X ingest — graph resolution and truncation honesty

**Date:** 2026-08-15 · **Status:** proposed, not implemented
**Extends:** the 2026-08-10 X thread-walk design (upward reply walking, `notes`, `max_hops`)
**Related:** `.claude/skills/ingest-x/SKILL.md`, `.claude/skills/ingest-video/SKILL.md`
(same Stream A/B/C sinks), `tools/x_fetch.py`

---

## 1. What this answers

An X post is a **node in a graph**, not a document. It can reply to a parent, quote another
post, and carry images — and each of those neighbours can do the same. Today `tools/x_fetch.py`
resolves that graph partially, and **every gap fails silently**: the subagent receives less
evidence than the operator believes it sent, extracts confidently from the remainder, and
returns a well-formed JSON object that nothing downstream can tell apart from a complete one.

Measured on the 2026-08-15 @sergio_tesla_ batch (6 posts, 3 recovered chains):

- **Every long post came back cut mid-sentence.** The endpoint truncates at
  `display_text_range[1] = 279`. The lost tails held the actual conclusions — one post's
  forward-return numbers, two CPI posts' payoff percentages, one week-analog verdict.
- **Five quoted posts arrived as truncated text with no images**, though their images were
  sitting unread in the same JSON payload already fetched.
- **The thread walk returned image URLs it never downloaded.** Recovering the parent charts
  took an undocumented second `--batch` pass that only happened because the operator was
  watching. A session following the skill literally would have handed remote
  `pbs.twimg.com` URLs to a subagent whose only tool is `Read`, and lost all five charts
  with no error raised.

That last one is the shape of the whole problem: **the failure is invisible at the point it
occurs and indistinguishable from success afterwards.**

## 2. Non-goals

- **No scraping, no login, no paid API.** `cdn.syndication.twimg.com/tweet-result` and the
  manual-paste fallback remain the only two fetch paths.
- **No downward traversal.** The endpoint exposes no replies/children field. The "bookmark the
  LAST post of a thread" operator rule survives this change unchanged.
- **No recovery of long-form bodies.** Measured below: not available at this endpoint. We make
  the loss *visible*, we do not fix it.
- **No change to classification, routing, the four verdicts, or the Stream A/B/C sinks.** This
  spec changes what evidence reaches the extractor, not what is done with it.

## 3. What the endpoint actually provides — measured 2026-08-15

Raw payload inspection, not inference from code.

**Top-level keys:** `__typename`, `conversation_count`, `created_at`, `display_text_range`,
`edit_control`, `entities`, `favorite_count`, `id_str`, `isEdited`, `isStaleEdit`, `lang`,
`mediaDetails`, `news_action_type`, `note_tweet`, `photos`, `possibly_sensitive`, `text`,
`user`.

| Field | Measured content | Parsed today? |
| --- | --- | --- |
| `display_text_range` | `[0, 279]` on a truncated post — **but see below, not a usable signal** | **No** |
| `note_tweet` | `{'id': '<opaque>'}` — **an ID stub, no body** | **No** |
| `isEdited` / `isStaleEdit` | present on every post | **No** |
| `quoted_tweet.id_str` | real status id (`2062887573790359920`) | **No** |
| `quoted_tweet.mediaDetails` / `.photos` | **1 photo, real `media_url_https`** | **No** |
| `quoted_tweet.text` | truncated at `[0, 276]` | Yes (as `quoted_text`) |
| `quoted_tweet.quoted_tweet` | **absent** — payload nests one level only | n/a |
| `quoted_tweet.in_reply_to_status_id_str` | **absent/None** even when the quoted post is a reply | n/a |

Two consequences drive the whole design:

1. **A quoted post's images and identity are already in a payload we have paid for.** Parsing
   them is free — no extra request. The current gap is an unread field, not an API limit.
2. **The quoted payload is deliberately shallow** (no nested quote, no reply pointer), but it
   carries `id_str` — so anything deeper is reachable by *re-fetching that id as a full post*,
   at one request each.

Long-form text is the one genuine loss: `note_tweet` proves a longer body exists and does not
contain it. **Detectable, not recoverable.**

### 3.1 `display_text_range` is NOT a truncation signal — measured

The obvious check, `display_text_range[1] < len(text)`, **false-positives on every post
carrying media**, because the trailing `t.co` link always sits outside the display range.
Measured across three posts:

| Post | reads as | `len(text)` | `display_text_range` | `dtr[1] < len` | `note_tweet` |
| --- | --- | --- | --- | --- | --- |
| `2077667306172236028` | complete | 232 | `[0, 208]` | **True** | absent |
| `2080609907561124004` | truncated | 303 | `[0, 279]` | True | **present** |
| `2077348334214222280` | truncated | 301 | `[0, 277]` | True | **present** |

The range check is 1-for-3 wrong; `note_tweet` is 3-for-3 right. **`text_truncated` is
therefore `bool(note_tweet)` and nothing else.** Recorded because the range check is the
design a reader would otherwise reach, and it looks correct until tested against a
complete post with an image.

⚠ **`quoted_tweet` sub-payloads carry no `note_tweet` key at all**, even when the quoted post
is long-form — the 2026-08-15 measurement showed a quoted body cut mid-word at
`[0, 276]` with `note_tweet: None`. **Truncation is therefore undetectable from the nested
object; only a full fetch of the quoted id can tell.** This is an independent argument for
phase B beyond the missing images.

## 4. Architecture

### 4.1 New fields on `XPost` (phase A)

```python
text_truncated: bool = False   # bool(note_tweet) — see §3.1, the range check is unusable
edited: bool = False           # isEdited or isStaleEdit
```

`text_truncated` reads `note_tweet` **only**. §3.1 measures why: the intuitive
`display_text_range` check flags complete posts that merely carry an image.

Both fields need `= False` defaults for the same load-bearing reason the thread fields do —
`_load_cached` does `XPost(**raw)` and every cache entry written before these existed lacks
the keys.

Why `edited` matters and is not cosmetic: the pundit ledger scores an author against text
attributed to a timestamp. If the post was edited after the call, **the text being scored is
not the text that was posted**, and nothing today records that.

### 4.2 Quoted-post promotion (phase B)

`quoted_tweet` becomes a first-class `XPost` built by the **same** parser as the containing
post — so it inherits `text_truncated`, `edited`, and media handling for free. Its images
download alongside the parent's, keyed by the quoted post's own id.

The single most important rule here: **`quoted_author` already governs attribution in prose
(credit whoever MADE the call, not whoever quoted it), and this change is what finally gives
that rule data to stand on.** The 2026-08-03f audit found 28/28 quote-tweets correctly
attributed, but all 28 were self-quotes — cases where the rule cannot be observed to fire.
Every post in the 2026-08-15 batch was likewise a self-quote. **The rule remains untested
against a genuine third-party quote.**

### 4.3 `resolve` — one command, one bundle (phase C)

Replaces the operator's choice between `--batch` and `--thread`, and absorbs the undocumented
second pass.

```text
resolve(url, max_quote_depth=2, max_hops=25) -> Bundle
  posts: [ResolvedPost]     # flat, ordered: chain root -> leaf, quoted posts after their referrer
  notes: [str]              # every stop, cap, cycle and skip, verbatim
```

Each `ResolvedPost` carries `role ∈ {bookmarked, chain_parent, quoted}`, `depth`,
`referred_by` (id), plus the full `XPost` and **local** `photo_paths`.

Traversal:

1. Fetch the bookmarked url.
2. If it is a reply → walk up (existing `walk_thread`: stops on author change, missing parent,
   unavailable hop, or `max_hops`; every stop appended to `notes`).
3. For **every** post reached, if it quotes something → resolve that id as a full post, to
   `max_quote_depth`. Recursion is breadth-first so the shallowest evidence lands first.
4. Download images for **every** post in the bundle, whatever its role.

**A `visited` set of status ids is mandatory, not defensive.** A quote cycle (A quotes B, B
quotes A) is constructible, and self-quoting authors — the norm in this corpus — make near-
cycles routine. `walk_thread` already carries `max_hops` for exactly this reason.

Every bound that bites appends to `notes`. **A truncated bundle that reads as a complete one
is the defect this spec exists to remove**, so silence on a cap is a bug, not a tidy default.

### 4.4 Skill changes

Step 1 collapses to a single `resolve` call. Step 2 hands the subagent the bundle, and the
prompt gains a line naming `text_truncated` posts explicitly so the extractor states what it
could not see rather than extrapolating. Step 3's digest gains a **truncation/edited column**,
on the same principle that put `is_retrospective` on every row: the operator decides, but only
about what they can see.

Two fixes already identified in the 2026-08-15 session land here too:

- The step-2 extraction schema must state the `entry`/`stop`/`target` format contract —
  **bare number, no parenthetical, no hyphenated range**. Step 4 states it where the row is
  written, but the subagent that produces the values never sees it, and on 2026-08-15 it
  returned prose with parentheticals for all three. It was harmless only because
  `direction: neutral` short-circuits before the parse.
- Delete the "run `--batch` again over the parents" tribal step by making it unnecessary.

## 5. Coverage — before and after

| Scenario | Today | After |
| --- | --- | --- |
| Plain post, images | ✅ | ✅ |
| Post text over 279 chars | ⚠ silently cut | ✅ flagged |
| Post was edited | ⚠ invisible | ✅ flagged |
| Quoted post text | ⚠ truncated, unflagged | ✅ flagged |
| Quoted post images | ❌ | ✅ |
| Quoted post is itself a quote | ❌ | ✅ to depth 2 |
| Quoted post is a thread leaf | ❌ | ✅ walked |
| Quoted post by a third party | text only, prose rule | ✅ full post + images |
| Reply chain | ✅ (separate command) | ✅ (automatic) |
| Chain parents' images | ⚠ URLs only, manual pass | ✅ downloaded |
| Quote inside a chain post | ⚠ text only | ✅ resolved |
| Bookmarked a middle post | ❌ children invisible | ❌ **endpoint limit** |
| Multi-author thread | stops, noted | stops, noted |
| Deleted middle post | stops, noted | stops, noted |
| Long-form body text | ❌ | ❌ **endpoint limit** — but flagged |

Three rows stay ❌. They are endpoint limits, and the operator rule ("bookmark the LAST post")
is the only control for the first of them.

## 6. Testing and acceptance

Fixtures are **captured payloads**, not hand-authored dicts. A hand-written fixture that omits
`note_tweet` cannot fail a `note_tweet` check — the fixture makes the guard vacuous, and this
repo has shipped that defect before.

Required cases:

1. Truncated post (`note_tweet` present) → `text_truncated is True`.
2. **Complete post that carries an image** → `text_truncated is False`. This is the
   regression test for §3.1: it fails against the `display_text_range` design and passes
   against the `note_tweet` one, so it is the case that pins the decision.
3. Edited post → `edited is True`.
4. Quoted post with media → its `photo_paths` are non-empty **and distinct from the parent's**.
5. Third-party quote → the quoted post resolves with its own author, and a digest built from
   the bundle names that author, not the quoter. *(No such post exists in the corpus yet —
   this fixture must be captured deliberately.)*
6. Quote cycle A↔B → terminates, and `notes` says why.
7. `max_quote_depth` exceeded → terminates, `notes` says why.
8. Chain parents' `photo_paths` are populated **from `resolve` alone**, with no second call.

**Mutation requirement.** For cases 1, 4 and 8, break the production code and confirm the test
fails. A passing test proves the line is reached; it does not prove the assertion is scoped to
what it claims — so case 8 must additionally assert *which* posts have paths, not merely that
some do. That distinction is exactly what a reachability-only mutation test misses.

## 7. Failure modes

- **Request amplification.** A 4-post chain where each post quotes something becomes up to 12
  fetches at depth 2. The randomized cooldown makes that slow, and the per-id cache makes a
  re-run free. `max_quote_depth=2` is the throttle; raising it is not a small change.
- **Bundle size into the subagent.** More posts and more images per dispatch. The measured
  cost driver is fixed per-dispatch overhead rather than payload — six varied posts landed
  inside a ±2% band — so this should be mild, but it is the thing to watch first if per-item
  cost jumps.
- **A quoted post that is itself unavailable.** Must degrade to today's behaviour (surface the
  truncated `quoted_text`, note the failure), never abort the bundle.
- **Over-collection.** Depth-2 quote resolution can drag in context the author never intended
  as part of the argument. The `role` and `depth` fields exist so the extractor can weight
  `bookmarked` above `quoted`, and the digest shows what was pulled in.

## 8. Files

| File | Change |
| --- | --- |
| `tools/x_fetch.py` | `text_truncated` / `edited`; quoted-post promotion; `resolve` + `visited`; `--resolve` CLI |
| `tests/test_x_fetch.py` | 8 cases above + captured fixtures |
| `.claude/skills/ingest-x/SKILL.md` | step 1 → `resolve`; step 2 schema gains the level-format contract; step 3 digest gains truncation/edited column |
| `.claude/skills/ingest-video/SKILL.md` | step-8 level-format contract already present — verify parity, no change expected |

Phases are independently shippable and ordered by value density: **A** (flags) unblocks honest
digests immediately and touches no traversal; **B** (quoted media) uses data already fetched;
**C** (`resolve`) is the structural change.

## 9. Decision Log

| Decision | Why | What would reverse it |
| --- | --- | --- |
| Flag truncation rather than recover it | `note_tweet` measured to carry an ID stub only | Any keyless path returning the long-form body |
| `text_truncated = bool(note_tweet)`, ignoring `display_text_range` | §3.1: the range check is 1-for-3 wrong, flagging complete posts that carry an image | A complete post measured WITHOUT `note_tweet` but genuinely cut — would mean `note_tweet` under-detects and a second signal is needed |
| Promote quoted posts to full `XPost` | Their media and `id_str` are already in the fetched payload | If quoted-post evidence proved consistently irrelevant to extraction — it did not in this batch |
| `max_quote_depth = 2` | Depth 1 misses quote-of-quote; depth 3+ amplifies requests for context increasingly far from the argument | A measured case where depth-3 context changed a verdict |
| Keep upward-only traversal | The endpoint exposes no children field | An endpoint or keyless path exposing replies |
| One `resolve` over `--batch` + `--thread` | The two-pass flow was undocumented and silently lossy | If `resolve`'s request amplification proved painful enough to want manual control back |
| Captured fixtures, not authored dicts | An authored fixture omitting a field cannot fail that field's guard | Nothing — this is a standing repo rule |
| No downward walk, keep the operator rule | Endpoint limit | As above |

## 10. Open questions

1. **Should a third-party quoted post route as its own item?** Today one post yields one item
   and the quoted post is context. If a quoted third-party post carries its own call, the
   author attribution rule says credit them — but nothing decides whether that is one row or
   two. Unresolved because the corpus contains **zero** genuine third-party quotes; deferring
   until one exists is deliberate, since inventing the rule now means inventing the test too.
2. **Should `edited: true` block Stream C?** A call whose text changed after the timestamp is
   weak ledger evidence. Blocking is safe but loses real calls; flagging is honest but relies
   on the reviewer. Leaning flag-only, consistent with how `is_retrospective` handles routing
   rows — but it deserves a decision rather than a default.
3. **Does the video handoff belong inside `resolve`?** A quoted post can contain video that
   the current check, which reads only the bookmarked post, never sees.
