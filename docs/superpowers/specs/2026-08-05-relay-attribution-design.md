# Relay attribution — routing second-hand calls to their originating author (design)

**Goal + success metric (one line):** a call relayed by an aggregator channel reaches
`docs/plans/pundit-calls.jsonl` under the **originating** trader's canonical handle with an
auditable timestamp, and **zero** rows are attributed to a channel that did not make the
call.

**Status:** design, pre-registered 2026-08-05. No SoT item ID assigned yet — give it one
when it lands in `project_todo_master.md`, and do not reuse `R1`/`R7`, which already mean
*ingest round 1 / round 7* in the video-ingest notes.

**Unblocks:** Kolunite社区 (`UCbZIP3-hbPOuaeHM0pWeM0w`), paused since round 7.

## 1. Problem

`tools/pundit_score.py` groups on `author` (`:155`, via `normalize_author`), and `author`
comes from `meta.author` — the **channel**, not the caller. So a routed relay credits the
aggregator with someone else's call. The failure is silent by construction: nothing
crashes, nothing validates, and the only visible symptom is that one trader's track record
is short while a channel's is full of trades they never took.

Because it cannot be detected downstream, the current rule is **drop every relayed
`setup`** (`.claude/skills/ingest-video/SKILL.md:235`, step 2) and preserve it verbatim in
the per-video note. That is correct, and it is why Kolunite is paused.

### 1.1 What changed on 2026-08-05 — the yield argument is dead

The pause also rested on measured economics: round 1 routed 1 row from 25 candidates for
~225k tokens, round 7 routed 0 rows from 5 kept items. **Both rounds measured routed rows
per token, and both implicitly assumed a relay is a lower-grade duplicate of something
obtainable first-hand.**

Operator, 2026-08-05: **Kolunite is a member of these traders' paid VIP Discord groups**,
so a relayed call is frequently information that exists nowhere public — not on the
originating trader's own YouTube channel, not on their X. For a VIP-sourced relay there is
no first-hand copy to prefer.

**Do not re-kill this work by quoting the token economics.** The correctness problem in
§1 is the only remaining blocker, and it is a code problem, not an editorial one.

The operator wants **both** Kolunite and the originating traders' own channels: public and
VIP are different information sets, not duplicates. That promotes the cross-source dedup
gap (§4.5) from theoretical to routine.

## 2. Already built — audited 2026-08-05, do not rebuild

| Piece | Where | State |
| --- | --- | --- |
| `is_relay` + `originating_author` on each pass-1 candidate | `SKILL.md:197` | **exists** |
| Attribution filter runs BEFORE `ITEM_CAP` | `SKILL.md:235`, step 2 | **shipped** — the round-7 regression is closed, though memory still lists it as an open action |
| Relays preserved verbatim in the note with `originating_author` | `SKILL.md:257` | **exists** — the 13-video backlog is minable without re-fetching |
| Curated name→handle roster | gitignored `config/pundit_roster.toml`, schema in the committed `.example` | **exists** — 11 mapped, 1 unmapped (`分`), 2 `never_auto_attribute` |
| `normalize_author` applied on both sides of the ledger↔priors join | `analytics/pundit_authors.py` | **shipped** (#555) |

### 2.1 One line in SKILL.md will mislead the implementer

`SKILL.md:283` says *"Do not solve this by writing `originating_author` into the Stream C
`author` field."* **That prohibition is scoped to the raw extracted name** — an
unnormalised, possibly ASR-mangled string, which would manufacture phantom pundits.

Writing the **roster-resolved canonical handle** into `author` is the intended end state
and is what this spec pre-registers. It costs the scorer **zero** changes. Verified
2026-08-05: `load_ledger` builds `LedgerCall` with a per-key `obj.get(...)`, so unknown
keys are ignored and new fields are backward-compatible.

Building a parallel `originating_author` ledger column instead leaves every relay scoring
under the channel — the exact bug being fixed, shipped with the appearance of a fix.
**Amend that line as part of this work.**

## 3. Pre-registered decisions

Operator decisions, 2026-08-05. Recorded before implementation so they are not re-litigated
mid-build.

| # | Decision | Chosen | Rejected alternative and why |
| --- | --- | --- | --- |
| D1 | Relay `call_ts_utc` | **Per-item stated time; clamp to publish and FLAG when absent** | Dropping un-timestamped relays loses the majority of VIP calls; clamping silently bakes an invisible systematic lag into exactly the rows this work exists to capture |
| D2 | Roster `confidence` | **Route all entries, record `attribution_confidence` on the row** | Verified-only would drop 8 of 11 pundits and ship the feature near-inert; recording turns an invisible misattribution into a filterable, revocable one |
| D3 | `[[ambiguous]]` strings | **Surface at the review gate for manual attribution, drop if unactioned** | A blind drop discards three traders' VIP calls, and Kolunite relays those three together |

Two further rules are set by this spec rather than by the operator:

- **D4 — never match by spelling similarity, substring, or character overlap.** Only the
  explicit `aliases` list resolves. `波浪` and `柳玉东` are one person with no shared
  characters; `军长`/`君掌` are homophones. A fuzzy matcher cannot get either right and
  will get `陈志峰` catastrophically wrong.
- **D5 — the safe direction is always DROP.** Unknown name, unmapped name, ambiguous
  string, or unresolvable timestamp all drop the `setup`. Dropping costs one row;
  misattributing corrupts a track record invisibly and permanently.

## 4. Architecture

### 4.1 `tools/pundit_roster.py` — pure resolution, IO at the edge

Mirrors `tools/video_calltime.py`: pure functions plus a thin CLI the skill invokes.

```python
def build_index(roster: dict) -> RosterIndex: ...
def resolve(index: RosterIndex, name: str) -> Resolution: ...
```

`Resolution` is one of four outcomes, and the caller must handle all four:

| Outcome | Meaning | Routing effect |
| --- | --- | --- |
| `MAPPED(handle, confidence)` | exact alias hit | route under `handle` |
| `AMBIGUOUS(text, members)` | `[[ambiguous]]` entry | D3 — manual gate |
| `UNMAPPED(text)` | `[[unmapped]]` entry | drop, surface in digest |
| `UNKNOWN(text)` | not in the roster at all | drop, surface in digest |

`UNMAPPED` and `UNKNOWN` route identically today but are **kept distinct**: unmapped means
"we have seen this and are chasing it", unknown means "new name, add it or an alias". The
digest reads differently for each (§4.7).

Matching is exact on the normalised alias string after `strip()`, with `normalize_author`
applied to the handle. **No case folding** — `analytics/pundit_authors.py` deliberately
does not fold case, and this must stay in step with it.

Roster IO stays in the CLI layer. **Tests pass parsed dicts directly** and must never read
the real `config/pundit_roster.toml`, which is gitignored and operator-specific.

### 4.2 `tools/x_route.py` — a third suppressor

`route_target` already has the exact shape needed:

```python
def route_target(
    content_type: str,
    verdict: str,
    *,
    retrospective: bool = False,
    rejected: bool = False,
    unattributable: bool = False,
) -> str | None: ...
```

`unattributable` is **`setup`-only**, like its two siblings, and defaults to `False` so
every existing caller keeps its behaviour. It is set by the caller when §4.1 returned
anything other than `MAPPED`, or when D3's manual gate was not actioned.

### 4.3 Ledger row shape

For a routed relay:

| Field | Value |
| --- | --- |
| `author` | the **roster-resolved canonical handle** (§2.1) |
| `relayed_by` | the relaying channel's handle, e.g. `kolunitevip` |
| `attribution` | `"relay"` or `"first-hand"` |
| `attribution_confidence` | the roster entry's `confidence` (D2) |
| `call_ts_source` | extended per §4.4 |

`source` keeps its current meaning (the medium) and is **not** overloaded to carry the
relaying channel — that is what `relayed_by` is for.

A first-hand item sets `attribution: "first-hand"` and omits `relayed_by`. The scorer needs
no change to any of this (§2.1).

### 4.4 Per-item call time for relays (D1)

**This is the design finding this spec exists for, and it is not on any prior task list.**

`stated_ts_utc` is currently **video-level** (`SKILL.md:192`, one value in the pass-1
envelope) and step 4 resolves `call_ts_utc` **once per video**. A VIP call relayed in a
daily roundup was necessarily made earlier — hours, sometimes a day.

That is structurally the `retrospective` defect, except it applies to **100% of relays**
rather than the 3-of-18 that motivated the original rule. It does not average out:
`pundit_score.py` replays forward from `call_ts`, so a relay of a call that already reached
its target scores as a non-hit (penalising the author), while one that already stopped out
can catch a later recovery (flattering them). Systematic, signed error on the VIP rows.

Changes:

1. **Pass 1 emits a per-candidate `stated_ts_utc` / `stated_date_only` / `stated_ts_raw`**
   for `is_relay: true` candidates, populated when the host says when the original call was
   made (「峰哥今早发的」, a timestamped screenshot). The video-level fields stay for
   host-own items and as the fallback.
2. **Step 4 resolves each relay separately** through the existing
   `tools/video_calltime.py`. **Reuse it unchanged** — it already rejects naive
   offset-less timestamps, bounds a stated time strictly below publish, caps the lead at
   168h, and clamps a date-only value to the conservative end-of-day edge. All four
   guarantees are needed here and none need reimplementing.
3. **When no stated time exists**, clamp to publish and write
   `call_ts_source: "publish_relay"` — a value distinct from the existing `publish`, so the
   population carrying unknown lag is filterable in the ledger rather than indistinguishable
   from a first-hand row.

`publish_relay` is what makes D1 auditable: once enough rows exist, compare the scores of
`stated`-sourced relays against `publish_relay`-sourced ones for the same author. A large
gap means the clamp is doing real damage and the policy should tighten toward dropping.

### 4.5 Cross-source dedup (§1.1 makes this live)

`tools/route_dedup.py`'s Stream C semantic pass is scoped to `same-source`, so a relay can
never be checked against the original author's own row — different `source_id` by
construction. Round 7 hit the live instance: 峰哥 relayed inside a Kolunite roundup **in
the same batch as his own first-hand video**.

Pre-registered rule:

> **Dedup Stream C across sources when the RESOLVED `author` matches. Keep the existing
> across-source exemption when the authors differ.**

The existing exemption's rationale — two pundits agreeing is two observations — is correct
and unchanged. It simply never covered the case where two *sources* carry **one** author's
**one** call.

Note the interaction with §1.1: VIP-only content cannot collide by construction, so this
fires only for calls the trader posted in both places. It must still exist, because the
operator is now ingesting both.

### 4.6 Cap and ranking for roundup channels

`ITEM_CAP = 5` (`tools/video_marks.py`) is wrong for an aggregator. Kolunite relays roughly
eight traders per video; round 1 produced 25 candidates. Once relays survive the attribution
filter they compete for five slots ranked by `specificity` — **and specificity is the wrong
axis, measured twice**: it promoted relays over host-own calls in round 1, and
retrospectives over forward calls in round 2.

Required: a **per-channel cap** in `config/youtube_channels.toml`, defaulting to the
current 5. Ranking within the cap must place *attributable, forward, in-subject* items
above everything else, with `specificity` as the tiebreak rather than the primary key.

Do not raise the global constant. `FRAME_CAP`/`ITEM_CAP` are a-priori constants and the
frame budget is what pass 2 spends ~80% of a video's tokens on.

### 4.7 Digest requirements

The review gate is the only runtime control, so these are not cosmetic.

- **An UNRESOLVED-NAMES section is mandatory.** Round 7 measured 2 of 5 originating names
  ASR-mangled (`输情`/`瞬间` → `舒琴`, recovered only from a Discord channel title in a
  frame). New mangles will keep arriving, miss the exact-alias lookup, and drop. **If the
  digest does not list them, the roster never grows and VIP calls are lost silently.** This
  is the feedback loop that makes the roster a living artifact.
- **Ambiguous items get a manual-attribution prompt** (D3), listing the members from the
  roster entry, defaulting to drop.
- **Every relay row shows `attribution_confidence`** so an operator-supplied mapping is
  visibly weaker than a corroborated one (D2).
- Relay rows should carry their `vision_confidence` prominently: round 7 measured relay
  corroboration at 0 high / 2 medium / 3 low, because a relay passes through their chart →
  their screenshot → host reads aloud → ASR → our frame grab. Strictly more error surface
  than first-hand, and not fixable by better prompting.

## 5. Out of scope

- **Scorer changes.** `pundit_score.py` needs none (§2.1). If this work appears to require
  one, the resolved handle is not reaching `author` and the design has drifted.
- **Verifying the 8 operator-supplied handles.** D2 records confidence instead; verification
  is separate operator work.
- **Resolving `分`** — almost certainly an ASR fragment, not a person. Resolve from audio
  before treating it as a handle.
- **Skill-fix 7g** (keeping a paused channel quiet in `/ingest-feed`) becomes moot for
  Kolunite once it unpauses, but stays open for any future pause.
- **Backfilling the 13-video Kolunite queue.** Sequenced after the smoke run (§7).

## 6. Test plan

Standard gates apply: `make lint-py`, `make typecheck`, `make test`, `make test-regression`
goldens unmoved.

### 6.1 Resolution

- Exact alias hits resolve; `handle` round-trips through `normalize_author`.
- Each of the four outcomes in §4.1 is asserted, including `UNMAPPED` vs `UNKNOWN` staying
  distinct.
- **Discrimination fixture for D4, and it must be shown to discriminate.** Assert that the
  merged string `陈志峰` resolves to `AMBIGUOUS` and **not** to `Traderfengge`, even though
  峰哥 is a known member of it. A matcher keying on character overlap resolves it to
  `Traderfengge`; an exact-alias matcher cannot. **Prove the fixture bites by running the
  test against a deliberately substring-based implementation before trusting it** — a
  fixture that passes under both implementations is the H15 DST-timestamp defect in a new
  location.
- Assert `陈哥` does not resolve to `traderchenge` (it is deliberately `never_auto_attribute`
  pending operator confirmation).

### 6.2 Routing

- `unattributable=True` drops a `setup` and leaves `claim`/`mechanic` untouched — the same
  setup-only property as `retrospective`/`rejected`, and for the same reason.
- Every existing `route_target` caller keeps its behaviour with the parameter defaulted.

### 6.3 Call time (§4.4)

- Relay with a stated time before publish → accepted, `call_ts_source: "stated"`.
- Relay with a stated time after publish → rejected by `video_calltime.py`, falls back.
- Relay with no stated time → `publish_relay`, distinct from `publish`.
- Two relays in one video with different stated times resolve **independently** — this is
  the assertion that fails against the current per-video resolution and is therefore the
  one that proves the change landed.

### 6.4 Dedup (§4.5)

- Same resolved author + shared level across two `source_id`s → flagged.
- Different authors, same level, two sources → **not** flagged (the exemption survives).

### 6.5 Schema

- The committed `config/pundit_roster.toml.example` parses and satisfies the schema. This
  also closes a standing gap: `check-toml` matches `\.toml$`, so `.example` files are
  unchecked by pre-commit and the hook prints "(no files to check)", which reads exactly
  like a pass.

### 6.6 Mutation proofs — required, and required to be non-vacuous

Per the standing findings, running a mutation is necessary and not sufficient.

- Stub `resolve` to return the raw name unchanged; §6.1 and §6.2 must go red.
- Stub the `unattributable` suppressor to `False`; §6.2 must go red.
- **`assert target in source` before writing the mutant**, and print the mutant's actual
  behaviour. A broken splice silently leaves the original code active (#554 shipped this
  twice in one session).
- **Never `cmd || echo "PASSES"`** — `||` fires on any non-zero exit including "no tests
  matched". Assert the selected-test count, and quote `-k` expressions (`-k "a or b"`,
  never `-k a_or_b`, which is matched as a substring and selects nothing).
- Back up any new, untracked file by `cp` before mutating it — `git checkout <file>` is a
  silent no-op on an untracked file.

## 7. Deliverables and sequencing

Staged so each PR is independently reviewable and nothing routes until resolution is
proven.

| PR | Contents | Routes anything? |
| --- | --- | --- |
| A | `tools/pundit_roster.py` (pure + CLI) + §6.1 + §6.5 | no |
| B | `route_target` suppressor, ledger row shape (§4.3), `SKILL.md:283` amendment + §6.2 | no — wiring only |
| C | Per-item relay call time (§4.4) + §6.3 | no |
| D | Dedup scope (§4.5), per-channel cap (§4.6), digest requirements (§4.7) | **yes** — unpauses Kolunite |

**After D, run ONE Kolunite video and review it before touching the 13-video backlog.**
Round 1 and round 7 both found that this channel breaks assumptions that held everywhere
else; a single video costs ~245k tokens and a bad batch costs thirteen times that.

The smoke run passes when: every routed relay row carries a roster handle in `author`,
`relayed_by: kolunitevip`, and a `call_ts_source` that is accurate; the digest lists any
unresolved names; and **no row is written with `author == "kolunitevip"` while
`attribution == "relay"`** — that last one is the success metric from the top of this
document, stated as a check.
