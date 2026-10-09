# Step 8 — routing, marks, deep links and the Stream C line

**When:** Read before the first Stream C write of a session, and whenever a `target`/`entry` carries a range or a parenthetical.

Reference for `.claude/skills/ingest-video/SKILL.md`, which holds the run order, the steps and the condensed rules. This file holds the full text, including the dated incidents behind each rule. The passages below are carried over verbatim from the pre-split SKILL.md (#884), so where one says "this doc", "this document", "above", "below" or names a step, it means SKILL.md.

## Step 8 — route on a single approval

After the user approves the batch, for each item compute the destination with
`tools/x_route.py::route_target(content_type, verdict, retrospective=…, rejected=…)`
(same taxonomy, unchanged import — do not fork it) and append per this table:

| content_type | verdict | Append to |
| --- | --- | --- |
| setup (`retrospective: false`, `rejected: false`) | — | `docs/plans/pundit-calls.jsonl` (one JSON line, schema below) |
| setup (`retrospective: true`) | — | **drop** — reason "retrospective — call predates video"; shown in the digest and the per-video note, never a Stream C write |
| setup (`rejected: true`) | — | **drop** — reason "rejected — speaker argued against taking it"; shown in the digest and the per-video note, never a Stream C write |
| mechanic | — | `docs/plans/mechanics-backlog.md` (a `-` list bullet ending in a `Status:` line, see SKILL.md step 8) |
| claim | NOVEL | `docs/plans/thesis-inbox.md` (a draft `H` row) |
| claim | ALREADY-TESTED / FROZEN-CATEGORY / NOT-FALSIFIABLE | **drop** — state "seen, verdict X", write nothing |

**Pass both flags to `route_target` — do not hand-apply the two setup rows.** The
function returns `None` for either, so the drop is a code path with a test behind it
rather than a table you have to remember to consult at the end of a six-video batch.
That is precisely how the `rejected` case shipped. `/ingest-x` does not extract either
flag, so both default to `False` there and its behaviour is unchanged.

Create the sink file with a one-line header if it does not exist. Report a one-line
result per item (routed → which file, or dropped → verdict).

**After each successful append, record it:**

```bash
PYTHONPATH=. poetry run python tools/route_dedup.py mark \
  --source-id <meta.video_id> --item-ts <item ts> \
  --sink <the FULL path route_target returned, e.g. docs/plans/pundit-calls.jsonl>
```

⚠ **A bare filename is REJECTED since ST99** — `--sink` is one of three full paths,
because `_key` includes it and a bare `mechanics-backlog.md` writes a row nothing can
ever match. 26 rows went in that way before the check existed.

`mark` runs **after** the write, never before. Marking at check time would let an
abandoned review consume the id and dedup away the real append later — the wifey-#68
watermark-on-send defect class, the same rule ST10's feed ledger follows. Never mark a
dropped item.

Stream C's near-duplicate exemption is **across sources only**: two pundits making the
same call are two real observations and `tools/pundit_score.py` scores both authors, so
collapsing those would delete signal. It never justified one video restating its own
call, which is how an entry leg and a target leg of a single position became two rows —
so within one `source_id` the pass does run (step 7's `same-source` scope plus the
`pairs` call). Stream C also still gets the exact `already_routed` block.

**Stream C requires a real `symbol` — never route a `setup` item with `symbol: null` or
`symbol: ""` to `pundit-calls.jsonl`.** `tools/pundit_score.py` has no null check of its
own; it would read the literal string `"None"` as a symbol and pollute the scored
ledger. If pass 2 could not resolve a symbol for a `setup` item, treat it as a dropped
candidate instead (reason: "no symbol resolved") in the digest and the per-video note,
not a Stream C write.

The dedup check from step 7 covers this — a `claim` whose check returned candidates must
have shown them in the digest, and an `already_routed: true` claim is not appended at all.

**Deep-link rule — separator-aware, do not reintroduce the bug.** A YouTube timestamp
deep link must respect whatever the URL already has:

- if the URL contains `?` (e.g. `https://www.youtube.com/watch?v=<id>`), append
  `&t=<ts>s`
- if it does **not** (e.g. `https://youtu.be/<id>`), append `?t=<ts>s` instead

Blindly appending `&t=<ts>s` to a `youtu.be` URL produces
`https://youtu.be/<id>&t=90s`, which is **broken** — with no prior `?`, the `&` never
starts a query string and the timestamp is silently dropped by the player. Always branch
on whether `"?"` is already in the URL before appending.

X video URLs get **no** timestamp deep link at all (the platform doesn't support one) —
persist the plain URL, and carry the offset separately in the `ts` field instead (added
below for every source, not just X, so it's never only recoverable by re-parsing the
URL).

**Stream C line** (`pundit-calls.jsonl`, one JSON line, extends the `/ingest-x` schema):

```json
{"source":"youtube","author":"<handle>","attribution":"first-hand|relay","relayed_by":"<relaying channel handle, relay rows only>","attribution_confidence":"<roster confidence, relay rows only>","url":"<url, with the deep link above for youtube>","ts":252.0,"call_ts_utc":"<resolved call time>","call_ts_source":"stated|publish|publish_relay","publish_ts_utc":"<publish time>","stated_ts_utc":"<the stated time step 4 was GIVEN, or null>","stated_ts_raw":"<verbatim quote or empty>","ingested_ts_utc":"<now>","backlog":false,"symbol":"...","direction":"...","entry":"...","stop":"...","target":"...","horizon":"...","confidence":"","vision_confidence":"high|medium|low","raw_quote":"<original language>","raw_quote_en":"<english>","corrected_from":"<transcript's original value, or empty>"}
```

**⚠ `target` and `entry` are MACHINE-PARSED — the format is a contract, not prose.**
`tools/pundit_score.py` takes ONE number from the string, and **a hyphenated range anywhere
in it beats a `/`-separated ladder and resolves to the range's LOW end.** Write `target` as
a bare `/`-separated ladder — `67,000 / 70,362.23 / 82,000` — with ranges in `raw_quote`
instead. **A clarifying parenthetical re-breaks it**: the constraint is on the whole field,
not its leading number. `entry` degrades gently (a `60.0K-61.2K` box parses to the 60,600
midpoint — fine for a zone, wrong for a target).

Round 10: `@Traderfengge` aimed 67,000 and parsed **70,362.23**, `@KoluniteVIP` aimed
64,600 and parsed **68,000**. The error only ever pushes the target further away, turning a
reachable call into a near-permanent OPEN and **understating that author's hit rate** — so
it reads as "these pundits are bad" rather than as a bug.

**Run `make buibui-pundit-score` as the last action of the round** — reading the row back
does not show you the parse, and at round-end every row is still OPEN, so a bad parse is
free to fix then and invisible later.

**`author` is the person who MADE the call, never the channel that reported it.** On a
first-hand row those are the same and `attribution` is `"first-hand"` with `relayed_by`
omitted. On a relay, `author` is the roster-resolved handle from step 4a and `relayed_by`
is this channel — `tools/pundit_score.py` groups on `author` (`:155`), so this is what
makes the scorer credit the right person with **zero** changes to it. `source` keeps its
meaning below (the medium) and is never overloaded to carry the relaying channel.
`load_ledger` reads per-key with `obj.get(...)`, so the three new keys are
backward-compatible and every pre-existing row keeps scoring exactly as before.

`source` is `youtube` or `x-video` (from `meta.source`, verbatim — `tools/video_fetch.py`
already resolves this). **`confidence` is always written as an empty string for a video
row.** It keeps its `/ingest-x` meaning (the pundit's verbatim hedging phrase) — this
pipeline does not currently extract that from a video, so the honest value is "not
collected," not a repurposed visual-corroboration score. `vision_confidence` carries pass
2's high/medium/low rating instead; see the "never merge these" note in step 6.
