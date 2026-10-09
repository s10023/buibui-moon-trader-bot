# Steps 3-4c — relays, the roster and attribution

**When:** Read whenever a kept `setup` has `is_relay: true`, when the roster returns anything but `mapped`, and before editing `config/pundit_roster.toml`.

Reference for `.claude/skills/ingest-video/SKILL.md`, which holds the run order, the steps and the condensed rules. This file holds the full text, including the dated incidents behind each rule. The passages below are carried over verbatim from the pre-split SKILL.md (#884), so where one says "this doc", "this document", "above", "below" or names a step, it means SKILL.md.

## Step 3 — why relays are filtered, and how they are attributed

**The attribution half is here because it was diagnosed and then NOT shipped, and
regressed.** Round 1 (2026-07-31, Kolunite `6qjuqdlmRVE`) found that `ITEM_CAP` ranks on
`specificity` alone and that this **inverts an aggregator video**: pass 1 returned 25
candidates, the top 5 by specificity were **all second-hand relays**, and the host's own
call ranked 6th — a naive top-5 keep would have routed five unattributable calls and
dropped the only legitimate one. That run fixed it with inline channel context and never
wrote it down here. **Round 7 (2026-08-03) reproduced the identical inversion: 5 of 5 kept
items were relays, 0 host-own.**

Why it is a correctness issue and not an ergonomics one: `tools/pundit_score.py` groups on
`author`, which comes from `meta.author` — the **channel**, not the caller. A routed relay
therefore credits the channel with someone else's call. Round 7 made the consequence
concrete: 峰哥's call was relayed inside a Kolunite roundup **in the same batch as 峰哥's
own video**, under an ASR-mangled name that would not collide with `@Traderfengge` — so
the double-count would also have been invisible to any author-level grouping.
`tools/route_dedup.py`'s Stream C semantic pass is scoped to `same-source` and
**structurally cannot** catch a relay against the original author's own row.

**Do not write the RAW extracted `originating_author` into the Stream C `author` field.**
Relayed names fragment (`输情`/`瞬间` and `舒琴` are one person; ASR mangles CJK names) and
collide (`陈志峰` is three traders run together). Attributing on an unnormalised extracted
name manufactures phantom pundits with fake track records while starving the real ones of
rows.

**The roster-RESOLVED canonical handle is different, and it is what belongs in `author`.**
`tools/pundit_score.py` groups on `author` (`:155`, via `normalize_author`), so writing the
resolved handle there is what makes the scorer credit the right person with **zero**
changes to it. Adding a parallel `originating_author` ledger column instead leaves every
relay scoring under the channel — the exact bug this is meant to fix, wearing the
appearance of a fix. Resolution comes from `tools/pundit_roster.py`; anything it does not
resolve to `MAPPED` sets `unattributable=True` and drops.

A routed relay row carries: `author` = the resolved handle · `relayed_by` = the relaying
channel's handle · `attribution` = `"relay"` · `attribution_confidence` = the roster
entry's `confidence`. A first-hand row sets `attribution: "first-hand"` and omits
`relayed_by`. `source` keeps its current meaning (the medium) and is never overloaded to
carry the relaying channel. `load_ledger` reads per-key with `obj.get(...)`, so these new
keys are backward-compatible and cost the scorer nothing.

**The roster is WIRED — do not rebuild it from scratch.** Gitignored
`config/pundit_roster.toml` (schema + rationale in the committed
`config/pundit_roster.toml.example`) holds the confirmed name→handle mappings, an
`[[unmapped]]` list of names still being chased, and `[[ambiguous]]` entries marked
`never_auto_attribute` for strings like `陈志峰` that are several people.
`tools/pundit_roster.py` reads it at step 4a and returns one of four outcomes; only
`mapped` routes. **A relay whose name does not resolve still drops** — the rule changed
from drop-every-relay to drop-what-cannot-be-attributed.

**Treat `confidence = "operator"` entries as unverified assertions.** A roster entry is
an assertion we make, not a fact the pipeline derives, and a wrong mapping attributes
real calls to the wrong trader with **no downstream check able to see it** — strictly
worse than dropping the relay. When an operator-confidence mapping carries a routed row,
say so in the digest so the approver can weigh it.

## Step 4a — resolve the originating author

For every `setup` candidate with `is_relay: true`, resolve the verbatim
`originating_author` through the roster:

```bash
PYTHONPATH=. poetry run python tools/pundit_roster.py "<originating_author>"
```

Four outcomes, and each has exactly one action:

- `mapped` — **route it.** `author` = the returned `handle` · `relayed_by` = this
  channel's handle · `attribution` = `"relay"` · `attribution_confidence` = the returned
  `confidence`. Treat `confidence: "operator"` as an unverified assertion and say so in
  the digest.
  **Write `relayed_by` verbatim as the `handle` in `config/youtube_channels.toml`,
  leading `@` included** — e.g. `"@KoluniteVIP"`, never `"KoluniteVIP"`. Both forms are
  already in the ledger (the 2026-08-06 rows carry `@`, the earlier `JcMq-lyHIt4` batch
  does not) because this line used to say only "this channel's handle" and left the `@`
  to taste. Harmless so far — `pundit_score.py` groups on `author`, not on this field —
  but the moment anything does `GROUP BY relayed_by` the channel splits into two records,
  which is #555's split-track-record failure in a second column. `normalize_author`
  strips the `@` but deliberately does **not** fold case, so it cannot rescue a
  divergence that is only about the prefix on a field nothing normalises.
- `ambiguous` — **surface it in the digest for manual attribution**, listing `members`.
  The operator assigns it from the audio or declines. Default if unactioned: **drop**.
  This is a positive instruction, not missing data: `陈志峰` is three traders, and the
  members being individually identified still does not say which one spoke a given line.
- `unmapped` / `unknown` — set `unattributable=True` so `route_target` drops it, and
  **list the name in the digest's UNRESOLVED NAMES section**. They stay distinct so the
  digest can tell "a name we are already chasing" from "a name we have never seen".

**Never resolve by eye.** `波浪` and `柳玉东` are one person with no shared characters,
`军长`/`君掌` are homophones, and `波浪理论` is Elliott Wave Theory — a phrase, not a
person. Only the roster's explicit alias list gets these right, which is why the resolver
matches on exact equality and nothing else.

## Step 4c — check a relay against the originating author's own rows

The Stream C dedup pass is `same-source` scoped, so it structurally cannot compare a
relay against the original author's own first-hand row — and ingesting both the
aggregator and the originating channels makes that collision routine rather than
theoretical. Pass the resolved handle so the check widens from one source to one author:

```bash
PYTHONPATH=. poetry run python tools/route_dedup.py check \
  --source-id <video id> --item-ts <t> --sink docs/plans/pundit-calls.jsonl \
  --text "<the call>" --author <resolved handle>
```

`semantic_scope` comes back `same-author` instead of `same-source`. The across-source
exemption is unchanged for **different** authors — two pundits making the same call are
two genuine observations, not a duplicate.
