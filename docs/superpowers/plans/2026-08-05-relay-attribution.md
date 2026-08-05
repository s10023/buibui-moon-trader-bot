# Relay Attribution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps
> use checkbox (`- [ ]`) syntax for tracking.

**Goal:** route a call relayed by an aggregator channel into `pundit-calls.jsonl` under the
**originating** trader's canonical handle, with an auditable timestamp, and never under the
relaying channel's.

**Architecture:** a curated roster resolves an extracted name to a canonical handle by
**exact alias match only**; anything that does not resolve drops. Resolution is a pure
function over a parsed dict (`tools/pundit_roster.py`), the drop is a third suppressor on
the existing `route_target`, and the resolved handle is written into the ledger's existing
`author` field so `tools/pundit_score.py` needs **zero** changes.

**Tech Stack:** Python 3.11+, stdlib only (`tomllib`, `dataclasses`, `argparse`), pytest,
ruff, mypy strict.

**Spec:** `docs/superpowers/specs/2026-08-05-relay-attribution-design.md`. Read §3
(pre-registered decisions D1–D5) before starting — they are operator decisions, not
suggestions, and D5 ("the safe direction is always DROP") decides every ambiguous case in
this plan.

## Global Constraints

- **Never match by spelling similarity, substring, prefix, or character overlap** (spec D4).
  Only exact alias equality after `.strip()`. `波浪` and `柳玉东` are one person with no
  shared characters; `军长`/`君掌` are homophones.
- **No case folding.** `analytics/pundit_authors.py` deliberately does not fold case, and
  resolution must stay in step with it or the ledger↔priors join splits.
- **Every function needs type annotations including the return type** (`-> None` on tests);
  mypy runs strict.
- **Tests never read `config/pundit_roster.toml`** — it is gitignored and
  operator-specific. Pure functions take a parsed dict; tests pass literals.
- **Tests make no network calls.** Pass a `MagicMock` where a client is needed.
- **Definition of Done per task:** `make lint-py`, `make typecheck`, `make test`, and
  `make test-regression` goldens unmoved. State each result plainly; if one was skipped,
  say so.
- **`make lint-md` gates every `.md` this plan touches**, including
  `.claude/skills/*/SKILL.md`.
- **Mutation-proof rules (spec §6.6), which exist because the H15 plan shipped two guards
  that could not fail:**
  - **Never write `cmd || echo "PASSES"`.** `||` fires on any non-zero exit including "no
    tests matched", so a tooling error renders as success. Assert the selected-test count.
  - **Quote `-k` expressions**: `-k "a or b"`, never `-k a_or_b`, which is matched as a
    substring and selects nothing.
  - **`assert target in source` before writing a mutant**, then print the mutant's actual
    behaviour. A broken splice leaves the original code active.
  - **A fixture for a discrimination test must be shown to discriminate** — run it against
    the wrong implementation and watch it go red before trusting it.
  - `git checkout <file>` is a **silent no-op on an untracked file**. Back up new files by
    `cp` before mutating.

## File Structure

| File | Responsibility | Task |
| --- | --- | --- |
| `tools/pundit_roster.py` | **new** — pure name→handle resolution + thin CLI | 1 |
| `tests/test_pundit_roster.py` | **new** — resolution, discrimination, schema | 1 |
| `tools/x_route.py` | add the `unattributable` suppressor | 2 |
| `tests/test_x_route.py` | suppressor behaviour + defaults unchanged | 2 |
| `.claude/skills/ingest-video/SKILL.md` | amend the misleading `author`-field line; wire resolution into steps 3/4; digest rules | 2, 6 |
| `tools/video_calltime.py` | `relay=` → `call_ts_source: "publish_relay"` | 3 |
| `tests/test_video_calltime.py` | relay fallback labelling | 3 |
| `tools/route_dedup.py` | author-scoped cross-source Stream C dedup | 4 |
| `tests/test_route_dedup.py` | same-author across sources flags; different authors do not | 4 |
| `tools/yt_feed.py` | per-channel `item_cap` | 5 |
| `tests/test_yt_feed.py` | cap loads and surfaces in the hint command | 5 |
| `config/youtube_channels.toml.example` | document `item_cap` | 5 |

**Task → PR mapping** (spec §7): Task 1 = PR A · Task 2 = PR B · Task 3 = PR C ·
Tasks 4–6 = PR D. **Only PR D routes anything.**

---

### Task 1: `tools/pundit_roster.py` — exact-alias resolution

**Files:**

- Create: `tools/pundit_roster.py`
- Create: `tests/test_pundit_roster.py`

**Interfaces:**

- Consumes: `analytics.pundit_authors.normalize_author` (strips a leading `@` and
  whitespace; idempotent).
- Produces: `build_index(roster: dict[str, Any]) -> RosterIndex`,
  `resolve(index: RosterIndex, name: str) -> Resolution`, and the four outcome constants
  `MAPPED` / `AMBIGUOUS` / `UNMAPPED` / `UNKNOWN`. Task 2 calls `resolve` and branches on
  `Resolution.routable`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_pundit_roster.py`:

```python
"""Resolution is exact-alias only; every non-MAPPED outcome must drop."""

from typing import Any

from tools.pundit_roster import (
    AMBIGUOUS,
    MAPPED,
    UNKNOWN,
    UNMAPPED,
    build_index,
    resolve,
)

ROSTER: dict[str, Any] = {
    "schema_version": 1,
    "pundit": [
        {
            "handle": "Traderfengge",
            "aliases": ["峰哥", "风哥"],
            "confidence": "high",
        },
        {
            "handle": "wugudehaore",
            "aliases": ["波浪", "柳玉东"],
            "confidence": "operator",
        },
        {
            "handle": "junzhangbtc",
            "aliases": ["军长", "BTC君掌", "君掌"],
            "confidence": "operator",
        },
    ],
    "unmapped": [{"text": "分"}],
    "ambiguous": [
        {
            "text": "陈志峰",
            "variants": ["陈智峰", "陈志锋"],
            "members": ["traderchenge", "bitezhi", "Traderfengge"],
            "action": "never_auto_attribute",
        },
        {"text": "陈哥", "action": "never_auto_attribute"},
    ],
}


def test_exact_alias_resolves_to_the_handle() -> None:
    got = resolve(build_index(ROSTER), "峰哥")
    assert got.outcome == MAPPED
    assert got.handle == "Traderfengge"
    assert got.confidence == "high"
    assert got.routable is True


def test_the_handle_itself_resolves() -> None:
    assert resolve(build_index(ROSTER), "Traderfengge").handle == "Traderfengge"


def test_leading_at_sign_is_stripped_from_the_stored_handle() -> None:
    roster: dict[str, Any] = {
        "pundit": [{"handle": "@Traderfengge", "aliases": ["峰哥"]}]
    }
    assert resolve(build_index(roster), "峰哥").handle == "Traderfengge"


def test_unrelated_aliases_for_one_person_both_resolve() -> None:
    """波浪 and 柳玉东 share no characters. Only an explicit list can join them."""
    index = build_index(ROSTER)
    assert resolve(index, "波浪").handle == "wugudehaore"
    assert resolve(index, "柳玉东").handle == "wugudehaore"


def test_homophone_variants_both_resolve() -> None:
    index = build_index(ROSTER)
    assert resolve(index, "军长").handle == "junzhangbtc"
    assert resolve(index, "君掌").handle == "junzhangbtc"


def test_ambiguous_string_is_never_mapped() -> None:
    got = resolve(build_index(ROSTER), "陈志峰")
    assert got.outcome == AMBIGUOUS
    assert got.routable is False
    assert got.members == ("traderchenge", "bitezhi", "Traderfengge")


def test_ambiguous_variants_are_also_ambiguous() -> None:
    assert resolve(build_index(ROSTER), "陈智峰").outcome == AMBIGUOUS


def test_chen_ge_is_ambiguous_not_a_traderchenge_alias() -> None:
    """Pending operator confirmation it is deliberately never_auto_attribute."""
    assert resolve(build_index(ROSTER), "陈哥").outcome == AMBIGUOUS


def test_unmapped_and_unknown_stay_distinct() -> None:
    index = build_index(ROSTER)
    assert resolve(index, "分").outcome == UNMAPPED
    assert resolve(index, "某个新名字").outcome == UNKNOWN


def test_neither_unmapped_nor_unknown_is_routable() -> None:
    index = build_index(ROSTER)
    assert resolve(index, "分").routable is False
    assert resolve(index, "某个新名字").routable is False


def test_substring_of_an_alias_does_NOT_resolve() -> None:
    """THE DISCRIMINATION TEST — see Step 2b. 波浪理论 is Elliott Wave Theory, a
    phrase, not a person; it merely contains the alias 波浪. A substring matcher
    attributes every mention of wave theory to wugudehaore."""
    assert resolve(build_index(ROSTER), "波浪理论").outcome == UNKNOWN


def test_alias_containing_another_alias_resolves_to_its_own_entry() -> None:
    """BTC君掌 contains 君掌. Exact matching gets this right by construction; a
    prefix/suffix matcher can pick either, and here they agree — which is why the
    test above, not this one, is the discriminating case."""
    assert resolve(build_index(ROSTER), "BTC君掌").handle == "junzhangbtc"


def test_whitespace_is_stripped_but_case_is_NOT_folded() -> None:
    index = build_index(ROSTER)
    assert resolve(index, "  峰哥  ").handle == "Traderfengge"
    assert resolve(index, "traderfengge").outcome == UNKNOWN


def test_empty_name_is_unknown_not_a_crash() -> None:
    assert resolve(build_index(ROSTER), "   ").outcome == UNKNOWN


def test_ambiguous_wins_over_an_alias_collision() -> None:
    """Safety ordering: a never_auto_attribute string must not become routable
    because some other entry happens to alias it."""
    roster: dict[str, Any] = {
        "pundit": [{"handle": "somebody", "aliases": ["陈哥"]}],
        "ambiguous": [{"text": "陈哥", "action": "never_auto_attribute"}],
    }
    assert resolve(build_index(roster), "陈哥").outcome == AMBIGUOUS
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `poetry run pytest tests/test_pundit_roster.py -v`

Expected: collection error, `ModuleNotFoundError: No module named 'tools.pundit_roster'`.
**Confirm 0 tests passed** — a collection error that still reports passes means you are
running a stale file.

- [ ] **Step 3: Write the implementation**

Create `tools/pundit_roster.py`:

```python
"""Curated name -> handle resolution for relayed pundit calls.

Pure resolution plus a thin CLI, mirroring tools/video_calltime.py. The roster file is
gitignored and operator-specific, so every pure function takes an already-parsed dict
and the IO lives only in `main`.

EXACT ALIAS MATCHING ONLY. Fuzzy, substring or similarity matching is forbidden: 波浪
and 柳玉东 are one person with no shared characters, 军长 and 君掌 are homophones, and
陈志峰 is three different traders run together. No heuristic gets all three right, and
a wrong mapping attributes real calls to the wrong trader with no downstream check able
to see it.
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from analytics.pundit_authors import normalize_author

MAPPED = "mapped"
AMBIGUOUS = "ambiguous"
UNMAPPED = "unmapped"
UNKNOWN = "unknown"


@dataclass(frozen=True)
class Resolution:
    """The outcome of resolving one extracted name."""

    outcome: str
    text: str
    handle: str = ""
    confidence: str = ""
    members: tuple[str, ...] = ()

    @property
    def routable(self) -> bool:
        """Only a MAPPED name may be written into the ledger's `author` field."""
        return self.outcome == MAPPED


@dataclass(frozen=True)
class RosterIndex:
    by_alias: dict[str, tuple[str, str]]
    ambiguous: dict[str, tuple[str, ...]]
    unmapped: frozenset[str]


def build_index(roster: dict[str, Any]) -> RosterIndex:
    """Flatten the parsed roster into exact-match lookup tables."""
    by_alias: dict[str, tuple[str, str]] = {}
    for entry in roster.get("pundit", []):
        handle = normalize_author(str(entry.get("handle", "")))
        if not handle:
            continue
        confidence = str(entry.get("confidence", ""))
        names = [handle, *(str(a) for a in entry.get("aliases", []))]
        for name in names:
            key = name.strip()
            if key:
                by_alias[key] = (handle, confidence)

    ambiguous: dict[str, tuple[str, ...]] = {}
    for entry in roster.get("ambiguous", []):
        members = tuple(str(m) for m in entry.get("members", ()))
        texts = [
            str(entry.get("text", "")),
            *(str(v) for v in entry.get("variants", [])),
        ]
        for text in texts:
            key = text.strip()
            if key:
                ambiguous[key] = members

    unmapped = frozenset(
        key
        for entry in roster.get("unmapped", [])
        if (key := str(entry.get("text", "")).strip())
    )
    return RosterIndex(by_alias=by_alias, ambiguous=ambiguous, unmapped=unmapped)


def resolve(index: RosterIndex, name: str) -> Resolution:
    """Resolve one extracted name. Anything but MAPPED must drop the setup."""
    key = name.strip()
    if not key:
        return Resolution(outcome=UNKNOWN, text=key)
    # Ambiguous is checked FIRST on purpose: a never_auto_attribute string must not
    # become routable because some other entry happens to alias the same text.
    if key in index.ambiguous:
        return Resolution(outcome=AMBIGUOUS, text=key, members=index.ambiguous[key])
    if key in index.by_alias:
        handle, confidence = index.by_alias[key]
        return Resolution(
            outcome=MAPPED, text=key, handle=handle, confidence=confidence
        )
    if key in index.unmapped:
        return Resolution(outcome=UNMAPPED, text=key)
    return Resolution(outcome=UNKNOWN, text=key)


def load_roster(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SystemExit(
            f"roster not found: {path} (copy {path}.example and fill it in)"
        )
    return tomllib.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Resolve a relayed name to a handle.")
    parser.add_argument("names", nargs="+", help="extracted originating_author strings")
    parser.add_argument(
        "--roster",
        type=Path,
        default=Path("config/pundit_roster.toml"),
        help="path to the roster TOML",
    )
    args = parser.parse_args(argv)
    index = build_index(load_roster(args.roster))
    out = [
        {
            "text": r.text,
            "outcome": r.outcome,
            "handle": r.handle,
            "confidence": r.confidence,
            "members": list(r.members),
            "routable": r.routable,
        }
        for r in (resolve(index, n) for n in args.names)
    ]
    json.dump(out, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `poetry run pytest tests/test_pundit_roster.py -v`
Expected: **16 passed**. If the count is lower, tests were deselected — investigate before
continuing rather than accepting a green run.

- [ ] **Step 5: Prove the discrimination fixture actually discriminates**

The `波浪理论` test only earns its place if it fails against a substring matcher. Prove it:

```bash
cp tools/pundit_roster.py /tmp/pundit_roster.py.bak   # NOT git checkout: new file
python - <<'PY'
from pathlib import Path
p = Path("tools/pundit_roster.py")
src = p.read_text(encoding="utf-8")
target = "    if key in index.by_alias:"
assert target in src, "MUTATION DID NOT APPLY - splice target missing"
mutant = src.replace(
    target,
    "    for _a, _v in index.by_alias.items():\n"
    "        if _a in key:\n"
    "            return Resolution(outcome=MAPPED, text=key, handle=_v[0], confidence=_v[1])\n"
    "    if key in index.by_alias:",
    1,
)
assert mutant != src, "MUTATION DID NOT APPLY - no substitution made"
p.write_text(mutant, encoding="utf-8")
print("mutant written")
PY
poetry run pytest tests/test_pundit_roster.py -v
```

Expected: **`test_substring_of_an_alias_does_NOT_resolve` FAILS.** Read the failure and
confirm it reports `波浪理论` resolving to `wugudehaore` — that is the defect the test
exists to catch. Then restore:

```bash
cp /tmp/pundit_roster.py.bak tools/pundit_roster.py
poetry run pytest tests/test_pundit_roster.py -q
```

Expected: 16 passed again. **If the mutant run was all-green, the fixture is vacuous —
stop and fix the fixture before continuing.**

- [ ] **Step 6: Add the schema test for the committed `.example`**

Append to `tests/test_pundit_roster.py`:

```python
def test_committed_example_roster_parses_and_indexes() -> None:
    """`check-toml` matches `\\.toml$`, so `.example` files are skipped by pre-commit
    and the hook prints "(no files to check)" — which reads exactly like a pass. This
    is the only thing validating that file."""
    import tomllib
    from pathlib import Path

    path = Path("config/pundit_roster.toml.example")
    roster = tomllib.loads(path.read_text(encoding="utf-8"))
    index = build_index(roster)
    assert isinstance(index.by_alias, dict)
    for entry in roster.get("pundit", []):
        assert entry.get("handle"), "every [[pundit]] needs a handle"
        assert entry.get("evidence"), "every [[pundit]] must carry its evidence"
```

Run: `poetry run pytest tests/test_pundit_roster.py -v` → **17 passed**.

- [ ] **Step 7: Full gates**

```bash
make lint-py
make typecheck
make test
make test-regression
```

Report each result plainly. `make test-regression` goldens must be unmoved — this task
changes no backtest behaviour, so any golden movement is a real signal, not noise.

- [ ] **Step 8: Commit**

```bash
git add tools/pundit_roster.py tests/test_pundit_roster.py
git commit -m "feat(ingest): exact-alias roster resolution for relayed calls

Resolves an extracted originating_author to a canonical handle by exact
alias match only. Fuzzy matching is forbidden and tested against: 波浪 and
柳玉东 are one person with no shared characters, 军长/君掌 are homophones,
and 陈志峰 is three traders run together.

Four outcomes; only MAPPED is routable. UNMAPPED and UNKNOWN both drop but
stay distinct so the digest can tell 'chasing this one' from 'new name'.
Ambiguous is checked first so a never_auto_attribute string cannot become
routable via an alias collision.

Nothing calls this yet - wiring is the next task."
```

---

### Task 2: the `unattributable` suppressor

**Files:**

- Modify: `tools/x_route.py:12-46`
- Modify: `tests/test_x_route.py`
- Modify: `.claude/skills/ingest-video/SKILL.md:283`

**Interfaces:**

- Consumes: Task 1's `Resolution.routable`.
- Produces: `route_target(..., unattributable: bool = False)`. Task 6's skill steps set it.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_x_route.py`:

```python
def test_unattributable_drops_a_setup() -> None:
    assert route_target("setup", "", unattributable=True) is None


def test_unattributable_leaves_a_mechanic_alone() -> None:
    """Setup-only, like retrospective and rejected: a mechanic scores nobody, so one
    mis-set flag must not be able to delete a routable item."""
    assert route_target("mechanic", "", unattributable=True) == (
        "docs/plans/mechanics-backlog.md"
    )


def test_unattributable_leaves_a_novel_claim_alone() -> None:
    assert route_target("claim", "NOVEL", unattributable=True) == (
        "docs/plans/thesis-inbox.md"
    )


def test_unattributable_defaults_false_so_existing_callers_are_unchanged() -> None:
    assert route_target("setup", "") == "docs/plans/pundit-calls.jsonl"
```

- [ ] **Step 2: Run to verify they fail**

Run: `poetry run pytest tests/test_x_route.py -v -k "unattributable"`

Expected: **3 failures** with `TypeError: route_target() got an unexpected keyword
argument 'unattributable'`, and the 4th (`defaults_false`) passing. **Confirm pytest
reports 4 selected**, not 0 — `-k` matching nothing exits 5 and prints no failures, which
is indistinguishable from success if you only read the exit code.

- [ ] **Step 3: Implement**

In `tools/x_route.py`, extend the signature and the docstring:

```python
def route_target(
    content_type: str,
    verdict: str,
    *,
    retrospective: bool = False,
    rejected: bool = False,
    unattributable: bool = False,
) -> str | None:
```

Add to the docstring's suppressor list:

```text
    - `unattributable` — the speaker relayed someone else's call and the roster could
      not resolve that person to a canonical handle (unknown name, unmapped name, or a
      string marked never_auto_attribute). Routing it would credit the relaying channel
      with a call they did not make, and `tools/pundit_score.py` groups on `author`, so
      no downstream check can see the error.
```

Change the `setup` branch:

```python
    if content_type == "setup":
        if retrospective or rejected or unattributable:
            return None
        return "docs/plans/pundit-calls.jsonl"
```

- [ ] **Step 4: Run to verify they pass**

Run: `poetry run pytest tests/test_x_route.py -v`
Expected: all pass, including every pre-existing test (the default keeps old behaviour).

- [ ] **Step 5: Prove the suppressor is not inert**

```bash
cp tools/x_route.py /tmp/x_route.py.bak
python - <<'PY'
from pathlib import Path
p = Path("tools/x_route.py")
src = p.read_text(encoding="utf-8")
target = "if retrospective or rejected or unattributable:"
assert target in src, "MUTATION DID NOT APPLY - splice target missing"
p.write_text(src.replace(target, "if retrospective or rejected:", 1), encoding="utf-8")
print("mutant written")
PY
poetry run pytest tests/test_x_route.py -v -k "unattributable"
```

Expected: **`test_unattributable_drops_a_setup` FAILS**, 4 tests selected. Restore with
`cp /tmp/x_route.py.bak tools/x_route.py` and re-run to green.

- [ ] **Step 6: Amend the misleading SKILL.md line**

`.claude/skills/ingest-video/SKILL.md:283` currently opens with a bolded sentence
forbidding the writing of the extracted originating author into the Stream C `author`
field. That prohibition is correct only for the **raw extracted name**. Replace the
paragraph's opening sentence with:

```markdown
**Do not write the RAW extracted `originating_author` into the Stream C `author`
field.** Relayed names fragment (`输情`/`瞬间` and `舒琴` are one person; ASR mangles
CJK names) and collide (`陈志峰` is three traders run together). Attributing on an
unnormalised extracted name manufactures phantom pundits with fake track records while
starving the real ones of rows.

**The roster-RESOLVED canonical handle is different, and it is what belongs in
`author`.** `tools/pundit_score.py` groups on `author` (`:155`, via
`normalize_author`), so writing the resolved handle there is what makes the scorer
credit the right person with **zero** changes to it. Adding a parallel
`originating_author` ledger column instead leaves every relay scoring under the
channel — the exact bug this is meant to fix, wearing the appearance of a fix.
Resolution comes from `tools/pundit_roster.py`; anything it does not resolve to
`MAPPED` sets `unattributable=True` and drops.
```

Then document the row shape immediately after it:

```markdown
A routed relay row carries: `author` = the resolved handle · `relayed_by` = the
relaying channel's handle · `attribution` = `"relay"` · `attribution_confidence` = the
roster entry's `confidence`. A first-hand row sets `attribution: "first-hand"` and omits
`relayed_by`. `source` keeps its current meaning (the medium) and is never overloaded to
carry the relaying channel. `load_ledger` reads per-key with `obj.get(...)`, so these
new keys are backward-compatible and cost the scorer nothing.
```

- [ ] **Step 7: Gates**

```bash
make lint-py
make typecheck
make test
make lint-md
make test-regression
```

- [ ] **Step 8: Commit**

```bash
git add tools/x_route.py tests/test_x_route.py .claude/skills/ingest-video/SKILL.md
git commit -m "feat(ingest): unattributable suppressor + resolved-handle row shape

route_target gains a third setup-only suppressor, defaulting False so every
existing caller is unchanged. Set when the roster returns anything but MAPPED.

Also amends the SKILL.md line that forbade writing originating_author into
the ledger author field. That prohibition was scoped to the RAW extracted
name; the roster-resolved handle is exactly what belongs there, and it is
what lets pundit_score.py credit the right person with zero changes."
```

---

### Task 3: per-item relay call time

**Files:**

- Modify: `tools/video_calltime.py:52-89`
- Modify: `tests/test_video_calltime.py`

**Interfaces:**

- Produces: `resolve_call_ts(..., relay: bool = False)`. When the stated time is absent or
  fails a bound **and** `relay=True`, `CallTime.call_ts_source` is `"publish_relay"`
  instead of `"publish"`.

**Why:** spec §4.4. A relayed call was made before the roundup, so stamping it with the
video's timestamp is the `retrospective` defect applied to 100% of relays. The scorer
replays forward from `call_ts`, so the error is signed: a relay of a call that already hit
target scores as a non-hit, one that already stopped out can catch a later recovery.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_video_calltime.py`:

```python
def test_relay_without_a_stated_time_is_labelled_publish_relay() -> None:
    got = resolve_call_ts("2026-08-03T12:00:00+00:00", relay=True)
    assert got.call_ts_utc == "2026-08-03T12:00:00+00:00"
    assert got.call_ts_source == "publish_relay"


def test_non_relay_without_a_stated_time_is_still_plain_publish() -> None:
    assert resolve_call_ts("2026-08-03T12:00:00+00:00").call_ts_source == "publish"


def test_relay_with_a_valid_stated_time_is_still_stated() -> None:
    """`relay` labels the FALLBACK only. A stated time that survives every bound is
    the better answer regardless of who made the call."""
    got = resolve_call_ts(
        "2026-08-03T12:00:00+00:00",
        stated_ts_utc="2026-08-03T09:30:00+00:00",
        relay=True,
    )
    assert got.call_ts_source == "stated"
    assert got.call_ts_utc == "2026-08-03T09:30:00+00:00"


def test_relay_with_a_naive_stated_time_falls_back_to_publish_relay() -> None:
    """A naive timestamp is rejected, not assumed UTC — and the relay label must
    survive that rejection, or the row silently rejoins the first-hand population."""
    got = resolve_call_ts(
        "2026-08-03T12:00:00+00:00", stated_ts_utc="2026-08-03T09:30:00", relay=True
    )
    assert got.call_ts_source == "publish_relay"


def test_relay_with_an_after_publish_stated_time_falls_back_to_publish_relay() -> None:
    got = resolve_call_ts(
        "2026-08-03T12:00:00+00:00",
        stated_ts_utc="2026-08-03T14:00:00+00:00",
        relay=True,
    )
    assert got.call_ts_source == "publish_relay"


def test_relay_beyond_the_lead_bound_falls_back_to_publish_relay() -> None:
    got = resolve_call_ts(
        "2026-08-03T12:00:00+00:00",
        stated_ts_utc="2026-07-01T09:00:00+00:00",
        relay=True,
    )
    assert got.call_ts_source == "publish_relay"
```

- [ ] **Step 2: Run to verify they fail**

Run: `poetry run pytest tests/test_video_calltime.py -v -k "relay"`
Expected: **6 selected**, 5 failing on `TypeError: unexpected keyword argument 'relay'`.
Confirm the selected count before reading the result.

- [ ] **Step 3: Implement**

In `resolve_call_ts`, add the parameter and build the fallback label from it:

```python
def resolve_call_ts(
    publish_ts_utc: str,
    *,
    stated_ts_utc: str | None = None,
    stated_date_only: bool = False,
    stated_ts_raw: str = "",
    max_lead_h: int = STATED_TS_MAX_LEAD_H,
    relay: bool = False,
) -> CallTime:
    """Stated time if it survives every bound, else publish time.

    `relay` labels the FALLBACK only. A relayed call was made before the video that
    reports it, so a relay that falls back to publish carries an unknown, one-sided
    lag; `publish_relay` keeps that population filterable in the ledger instead of
    indistinguishable from a first-hand row.
    """
    publish = _parse(publish_ts_utc)
    if publish is None or publish.tzinfo is None:
        raise ValueError(f"publish_ts_utc is not ISO-8601: {publish_ts_utc!r}")

    fallback = CallTime(
        call_ts_utc=publish_ts_utc,
        call_ts_source="publish_relay" if relay else "publish",
        publish_ts_utc=publish_ts_utc,
        stated_ts_raw=stated_ts_raw,
    )
```

The rest of the function is unchanged. Add `--relay` as a `store_true` flag in `main` and
pass it through.

- [ ] **Step 4: Run to verify they pass**

Run: `poetry run pytest tests/test_video_calltime.py -v`
Expected: all pass, pre-existing tests included.

- [ ] **Step 5: Prove it bites**

```bash
cp tools/video_calltime.py /tmp/video_calltime.py.bak
python - <<'PY'
from pathlib import Path
p = Path("tools/video_calltime.py")
src = p.read_text(encoding="utf-8")
target = 'call_ts_source="publish_relay" if relay else "publish",'
assert target in src, "MUTATION DID NOT APPLY - splice target missing"
p.write_text(src.replace(target, 'call_ts_source="publish",', 1), encoding="utf-8")
print("mutant written")
PY
poetry run pytest tests/test_video_calltime.py -v -k "relay"
```

Expected: **4 of the 6 fail** (the two that assert a non-fallback path stay green).
Restore with `cp /tmp/video_calltime.py.bak tools/video_calltime.py`.

- [ ] **Step 6: Gates and commit**

```bash
make lint-py && make typecheck && make test && make test-regression
git add tools/video_calltime.py tests/test_video_calltime.py
git commit -m "feat(ingest): label relay call-time fallbacks publish_relay

A relayed call was made before the roundup that reports it, so falling back
to the video's publish time carries an unknown one-sided lag. Labelling that
population separately keeps it filterable instead of indistinguishable from
a first-hand row, which is what makes the clamp policy revisitable on
evidence later.

relay labels the FALLBACK only - a stated time that survives every bound is
still the better answer."
```

---

### Task 4: author-scoped cross-source dedup

**Files:**

- Modify: `tools/route_dedup.py:374-401` (`semantic_scope`, `_comparable_entries`)
- Modify: `tests/test_route_dedup.py`

**Interfaces:**

- Produces: `semantic_scope(sink, source_id, author=None) -> str`, which returns
  `"same-author"` when an author is supplied for the JSONL sink.

**Why:** spec §4.5. The Stream C pass is `same-source` scoped, so a relay can never be
checked against the original author's own row. Round 7 hit the live case — 峰哥 relayed
inside a Kolunite roundup in the same batch as his own video. Ingesting both channels makes
this routine.

- [ ] **Step 1: Write the failing tests**

```python
def test_author_scope_is_reported_when_an_author_is_supplied() -> None:
    assert (
        semantic_scope("docs/plans/pundit-calls.jsonl", "abc123", author="Traderfengge")
        == "same-author"
    )


def test_source_scope_is_unchanged_when_no_author_is_supplied() -> None:
    assert semantic_scope("docs/plans/pundit-calls.jsonl", "abc123") == "same-source"


def test_same_author_across_two_sources_is_comparable() -> None:
    """One author's one call, arriving twice: once first-hand, once relayed."""
    sink_text = (
        '{"url": "https://youtu.be/AAA", "author": "Traderfengge", '
        '"symbol": "BTCUSDT", "direction": "long", "entry": "64000"}\n'
        '{"url": "https://youtu.be/BBB", "author": "someone_else", '
        '"symbol": "BTCUSDT", "direction": "long", "entry": "64000"}\n'
    )
    got = _comparable_entries(
        "docs/plans/pundit-calls.jsonl", sink_text, "CCC", author="Traderfengge"
    )
    assert len(got) == 1
    assert "64000" in got[0][0]


def test_different_authors_are_NOT_compared_so_the_exemption_survives() -> None:
    """Two pundits agreeing is two observations, not a duplicate. This is the test
    that fails if author scoping is applied too broadly."""
    sink_text = (
        '{"url": "https://youtu.be/AAA", "author": "someone_else", '
        '"symbol": "BTCUSDT", "direction": "long", "entry": "64000"}\n'
    )
    got = _comparable_entries(
        "docs/plans/pundit-calls.jsonl", sink_text, "CCC", author="Traderfengge"
    )
    assert got == []


def test_author_matching_normalises_the_at_sign_on_both_sides() -> None:
    sink_text = (
        '{"url": "https://youtu.be/AAA", "author": "@Traderfengge", '
        '"symbol": "BTCUSDT", "direction": "long", "entry": "64000"}\n'
    )
    got = _comparable_entries(
        "docs/plans/pundit-calls.jsonl", sink_text, "CCC", author="Traderfengge"
    )
    assert len(got) == 1
```

- [ ] **Step 2: Run to verify they fail**

Run: `poetry run pytest tests/test_route_dedup.py -v -k "author"`
Expected: 5 selected, failing on the unexpected `author` keyword.

- [ ] **Step 3: Implement**

```python
def semantic_scope(sink: str, source_id: str | None, author: str | None = None) -> str:
    """What `find_similar` will actually compare against — `check` reports this so a
    digest can never read an empty candidate list as "checked and clean".

    `author` scoping exists for relays: the same author's same call can arrive from two
    different sources (their own upload, and an aggregator relaying them), which
    source scoping structurally cannot see. The across-source exemption survives for
    DIFFERENT authors — two pundits agreeing is two observations, not a duplicate.
    """
    if sink in SEMANTIC_SINKS:
        return "all-entries"
    if not sink.endswith(".jsonl"):
        return "none"
    if author:
        return "same-author"
    return "same-source" if source_id else "none"


def _comparable_entries(
    sink: str, sink_text: str, source_id: str | None, author: str | None = None
) -> list[tuple[str, str]]:
    """`(text to score, excerpt)` per in-scope entry."""
    scope = semantic_scope(sink, source_id, author)
    if scope == "none":
        return []
    entries = split_entries(sink, sink_text)
    if sink in SEMANTIC_SINKS:
        return [(e, e) for e in entries]
    wanted_author = normalize_author(author) if author else None
    scoped: list[tuple[str, str]] = []
    for entry in entries:
        row = _parse_jsonl_entry(entry)
        if row is None:
            continue
        if wanted_author is not None:
            if normalize_author(str(row.get("author") or "")) != wanted_author:
                continue
        elif parse_source_id(str(row.get("url") or "")) != source_id:
            continue
        content = pundit_content_text(row)
        scoped.append((content, content))
    return scoped
```

Add `from analytics.pundit_authors import normalize_author` to the imports, and thread
`author` through `find_similar` and `_cmd_check` (a new `--author` CLI flag).

- [ ] **Step 4: Run to verify they pass, then run the whole file**

```bash
poetry run pytest tests/test_route_dedup.py -v
```

Expected: all pass. The pre-existing `same-source` tests must still be green — if any
turned red, author scoping replaced source scoping instead of adding to it.

- [ ] **Step 5: Prove it bites**

```bash
cp tools/route_dedup.py /tmp/route_dedup.py.bak
python - <<'PY'
from pathlib import Path
p = Path("tools/route_dedup.py")
src = p.read_text(encoding="utf-8")
target = '    if author:\n        return "same-author"\n'
assert target in src, "MUTATION DID NOT APPLY - splice target missing"
p.write_text(src.replace(target, "", 1), encoding="utf-8")
print("mutant written")
PY
poetry run pytest tests/test_route_dedup.py -v -k "author"
```

Expected: at least `test_author_scope_is_reported_when_an_author_is_supplied` and
`test_same_author_across_two_sources_is_comparable` FAIL. Restore.

- [ ] **Step 6: Gates and commit**

```bash
make lint-py && make typecheck && make test && make test-regression
git add tools/route_dedup.py tests/test_route_dedup.py
git commit -m "feat(ingest): author-scoped cross-source dedup for Stream C

A relay of X's call and X's own post are ONE observation arriving from two
sources, which same-source scoping structurally cannot see. Round 7 hit the
live case: 峰哥 relayed inside a Kolunite roundup in the same batch as his
own video.

The across-source exemption survives for DIFFERENT authors - two pundits
agreeing is two observations - and there is a test that fails if author
scoping is applied that broadly."
```

---

### Task 5: per-channel `item_cap`

**Files:**

- Modify: `tools/yt_feed.py:51-67` (`ChannelConfig`), `:103` (loader), `:766` (hint output)
- Modify: `tests/test_yt_feed.py`
- Modify: `config/youtube_channels.toml.example`

**Why:** spec §4.6. `ITEM_CAP = 5` ranked by `specificity` is wrong for a roundup relaying
eight traders (round 1 produced 25 candidates), and specificity has mis-ranked in two
measured rounds — promoting relays over host-own calls in round 1, and retrospectives over
forward calls in round 2.

- [ ] **Step 1: Write the failing tests**

```python
def test_item_cap_defaults_to_the_global_constant() -> None:
    cfg = load_feed_config(
        _write_config(tmp_path, "[[channel]]\nid = 'UC" + "A" * 22 + "'\n")
    )
    assert cfg.channels[0].item_cap == 5


def test_item_cap_is_read_from_the_channel_block() -> None:
    cfg = load_feed_config(
        _write_config(
            tmp_path,
            "[[channel]]\nid = 'UC" + "A" * 22 + "'\nitem_cap = 12\n",
        )
    )
    assert cfg.channels[0].item_cap == 12
```

Follow the existing fixture idiom in `tests/test_yt_feed.py` for building a config file —
read the file first and match it rather than inventing `_write_config` if a helper already
exists.

- [ ] **Step 2: Run to verify they fail**

Run: `poetry run pytest tests/test_yt_feed.py -v -k "item_cap"`
Expected: 2 selected, failing on the missing attribute.

- [ ] **Step 3: Implement**

Add to `ChannelConfig`, directly beneath `intro_recap_s`:

```python
    # Max items /ingest-video keeps from ONE video of this channel, before ranking.
    # Defaults to tools/video_marks.py::ITEM_CAP. An aggregator that relays eight
    # traders per upload needs more than a single-voice channel does; specificity
    # ranking has mis-ordered twice (relays over host-own, retrospectives over
    # forward calls), so a too-small cap on a roundup silently discards the payload.
    item_cap: int = 5
```

In the loader beside `intro_recap_s=int(raw.get("intro_recap_s", 0)),`:

```python
item_cap = (int(raw.get("item_cap", 5)),)
```

In the hint command's output dict beside `"intro_recap_s"`:

```python
                    "item_cap": match.item_cap if match else 5,
```

- [ ] **Step 4: Run to verify they pass**

Run: `poetry run pytest tests/test_yt_feed.py -v` → all pass.

- [ ] **Step 5: Document it in the committed example**

Append to the `[[channel]]` block in `config/youtube_channels.toml.example`:

```toml
# Max items /ingest-video keeps from ONE video of this channel (default 5).
# Raise for aggregator channels that relay several traders per upload — the
# default was sized for a single-voice channel, and a roundup that relays eight
# people will silently lose most of them to the cap.
# item_cap = 12
```

- [ ] **Step 6: Gates and commit**

```bash
make lint-py && make typecheck && make test && make lint-md
git add tools/yt_feed.py tests/test_yt_feed.py config/youtube_channels.toml.example
git commit -m "feat(ingest): per-channel item_cap

ITEM_CAP=5 was sized for a single-voice channel. An aggregator relaying eight
traders per upload loses most of them to the cap, and specificity ranking has
mis-ordered twice, so the survivors are not the best ones.

Defaults to the existing global constant, so every configured channel keeps
its current behaviour until someone opts in."
```

---

### Task 6: wire resolution into the skill, and unpause

**Files:**

- Modify: `.claude/skills/ingest-video/SKILL.md` (steps 3, 4, and the digest section)
- Modify: `config/youtube_channels.toml` (gitignored — operator action, not a commit)

**This is the only task that changes what routes.** Everything before it is inert.

- [ ] **Step 1: Change step 3's relay rule from drop-all to resolve-then-drop**

Replace filter rule 2 (`SKILL.md:235`) so relays are kept when they resolve:

```markdown
2. **Relayed `setup` candidates are kept ONLY if the roster resolves the originating
   name.** Set `is_relay: true` whenever the speaker is reading out, reacting to, or
   summarising a call made by **someone else**, and put that person's name verbatim in
   `originating_author` — do not normalise, correct or "fix" it, because the raw string
   is what the roster's alias lists are built from and a helpful correction destroys the
   evidence that grows them. Set `is_relay: false` and `originating_author` to the
   channel's own handle only for the speaker's own calls. **`claim` and `mechanic`
   candidates are exempt** — an idea is portable regardless of who first said it, and
   Streams A/B score nobody.
```

- [ ] **Step 2: Add the resolution call to step 4**

```bash
PYTHONPATH=. poetry run python tools/pundit_roster.py "<originating_author>"
```

Document the four outcomes and what each does:

```markdown
- `mapped` — route it. `author` = the returned `handle`, `relayed_by` = this channel's
  handle, `attribution` = `"relay"`, `attribution_confidence` = the returned
  `confidence`.
- `ambiguous` — **surface it in the digest for manual attribution**, listing `members`.
  The operator assigns it from the audio or declines. Default if unactioned: drop.
- `unmapped` / `unknown` — set `unattributable=True` so `route_target` drops it, and
  **list the name in the digest's UNRESOLVED NAMES section**.
```

- [ ] **Step 3: Add the per-item relay call time to step 4**

```markdown
**Resolve a relayed call's time separately from the video's.** A relayed call was made
before the roundup, so the video-level `call_ts_utc` is wrong for it. When pass 1
returned a per-candidate stated time, run `tools/video_calltime.py` again for that item
with `--relay`; when it did not, run it with `--relay` and no `--stated` so the row is
labelled `publish_relay`.
```

- [ ] **Step 4: Add the UNRESOLVED NAMES digest section**

```markdown
### UNRESOLVED NAMES (mandatory — never omit, never abbreviate)

List every `originating_author` that did not resolve, with the video and timestamp it
came from and the verbatim string. **This is the only mechanism by which the roster
grows.** Round 7 measured 2 of 5 originating names ASR-mangled — `输情`/`瞬间` → `舒琴`,
recovered only from a Discord channel title visible in a frame. New mangles arrive every
round, miss the exact-alias lookup, and drop. If they are not listed here they are lost
silently, and the roster stops improving while appearing to work.
```

- [ ] **Step 5: `make lint-md` and commit**

```bash
make lint-md
git add .claude/skills/ingest-video/SKILL.md
git commit -m "feat(ingest): route resolvable relays, unpause aggregator channels

Relayed setups are no longer dropped wholesale - they are dropped only when
the roster cannot resolve the originating name. Adds the mandatory
UNRESOLVED NAMES digest section, which is the only mechanism by which the
roster grows, and the per-item relay call time.

Kolunite has been paused since round 7 for the correctness reason this
closes. The token-economics half of that pause is void: the operator is in
these traders' paid VIP Discord groups, so a relay is frequently information
that exists nowhere public."
```

- [ ] **Step 6: Smoke run — ONE video, not the backlog**

```bash
PYTHONPATH=. poetry run python tools/yt_feed.py poll
```

Pick **one** Kolunite video and run `/ingest-video` on it. Passing means:

- every routed relay row has a roster handle in `author`, `relayed_by: kolunitevip`,
  `attribution: "relay"`, and an `attribution_confidence`
- `call_ts_source` is `stated` or `publish_relay`, never bare `publish`, on relay rows
- the digest lists any unresolved names
- **no row has `author == "kolunitevip"` while `attribution == "relay"`** — this is the
  spec's success metric stated as a check

Verify the last one directly rather than by reading the digest:

```bash
poetry run python -c "
import json, pathlib
rows = [json.loads(l) for l in pathlib.Path('docs/plans/pundit-calls.jsonl').read_text().splitlines() if l.strip()]
bad = [r for r in rows if r.get('attribution') == 'relay' and r.get('author') == 'kolunitevip']
print('relay rows:', sum(1 for r in rows if r.get('attribution') == 'relay'))
print('MISATTRIBUTED:', len(bad))
"
```

`MISATTRIBUTED: 0` with a non-zero relay count is the pass. **A zero relay count is not a
pass** — it means nothing routed and the run proved nothing.

- [ ] **Step 7: Only then, the 13-video backlog**

Round 1 and round 7 both found this channel breaks assumptions that held everywhere else.
One video costs ~245k tokens; thirteen bad ones cost thirteen times that.

---

## Self-Review

**1. Spec coverage.** §4.1 → Task 1. §4.2 → Task 2. §4.3 → Task 2 Step 6. §4.4 → Task 3 +
Task 6 Step 3. §4.5 → Task 4. §4.6 → Task 5. §4.7 → Task 6 Steps 2/4. §2.1 (the misleading
SKILL.md line) → Task 2 Step 6. §6.5 (`.example` schema test) → Task 1 Step 6. §6.6 →
Global Constraints + a mutation step in Tasks 1–4. D1 → Task 3. D2 → Task 2's row shape
(`attribution_confidence`). D3 → Task 6 Step 2. D4 → Task 1's discrimination test. D5 →
every non-MAPPED path drops.

**No gaps.** One deliberate deferral: the spec's §5 lists verifying the 8 operator-supplied
handles as out of scope, and no task covers it — correct, it is operator work.

**2. Placeholder scan.** No TBDs. Every code step carries literal code. Task 5 Step 1 says
to match the existing fixture idiom in `tests/test_yt_feed.py` rather than inventing a
helper — that is a deliberate instruction to read a specific file, not a placeholder, and
the assertions themselves are literal.

**3. Type consistency.** `Resolution.routable` (Task 1) is the property Task 2 branches on.
`semantic_scope(sink, source_id, author=None)` keeps its first two positional parameters so
existing callers are unchanged. `resolve_call_ts(..., relay=False)` is keyword-only, like
every other option on that function. `item_cap` defaults to `5`, matching
`tools/video_marks.py::ITEM_CAP`, in all three places it appears.

**4. Anti-vacuity check (spec §6.6).** Tasks 1–4 each carry a mutation step that asserts the
splice applied before trusting the result. No step uses `cmd || echo`. Every `-k` is
quoted. Every mutation restores from a `cp` backup, never `git checkout`, because
`tools/pundit_roster.py` is untracked when Task 1 mutates it. Task 1 Step 5 additionally
requires watching the discrimination fixture fail against a substring matcher before
trusting it — the guard on the guard.
