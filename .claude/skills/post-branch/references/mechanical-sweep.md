# Phase 0 — the mechanical sweep, leg by leg

**When:** Read before running Phase 0, and whenever a leg fires that you do not recognise.

Reference for `.claude/skills/post-branch/SKILL.md`, which holds the run order and the condensed rules. This file holds the full text, including the dated incidents behind each rule. The passages below are carried over verbatim from the pre-split SKILL.md (#884), so where one says "this file", "this skill", "above" or "below" about the phase table or a step, it means SKILL.md.

## The legs

```bash
make post-branch-checks
```

Thirteen legs, all advisory (`--exit-zero`). Triage each hit; a false positive costs
a glance, a silent miss ships a doc that reads as complete.

| Leg | Asks |
| --- | --- |
| `queue-items` | Does this branch **close** a task the handoff still lists as to-do? |
| `handoff-symbols` | Does the handoff claim something about a symbol or file this branch touched? |
| `new-files` | Does every added non-Python operator file reach an enumerating doc? |
| `new-modules` | Does every added module reach `.claude/context/`? |
| `new-targets` | Is every **added** Make target documented? (`buibui-` is stripped — AGENTS.md documents the subcommands) |
| `amended-targets` | For a target whose recipe this branch **changed**, which docs enumerate it and need re-reading? Added 2026-09-06: `new-targets` matches an added `+target:` line only, so #746's `DB=` on an existing target read clean while `AGENTS.md` and `README.md` both went one override short. It reports the docs, never the diff — scoping it to `$(if …)` would scope to the symptom, and a changed default fails the same silent way |
| `negative-claims` | Does a doc assert the absence of something this branch just added? |
| `doc-indexes` | Are the generated `INDEX.md` files current? |
| `md-atx` | Did a wrapped `#123` become an accidental MD018 heading? |
| `memory-cap` | Is MEMORY.md over its size / bullet cap? |
| `handoff-size` | Does the handoff's line-count stamp match the file? (**owner-only** — see below) |
| `stale-anchors` | Does any `§N` / `Step N` citation point at an anchor that no longer exists — **repo and memory tree**? |
| `sensitive-terms` | Would a public flip expose a work identifier? Asks three questions — tracked tree, commit CONTENT, commit MESSAGES. A missing term list is a FINDING, never a SKIP. ⚠ **It does NOT read the PR title/body** — that is the fourth surface, screened separately below |

## What it does not cover

**What it deliberately does NOT cover:** whether a doc is *correct*, whether the
behaviour gate should pass, or whether a claim is true. Those are phases 1–6 —
and since ST88 the sweep says so itself, closing with the `Step N` bodies it
does not reach. That block states the gap where a session running the mechanical
half will actually see it, which prose here cannot: both parallel sessions on
2026-08-25 had this paragraph available and substituted anyway.

⚠ **`stale-anchors` is the leg with no substitute.** A section number is not a
symbol, so no symbol-keyed check can see this class; and on its first run here 4
of 7 hits sat in the memory tree, which no repo-scoped check can reach at all.

## Handoff ownership — three legs read the handoff

⚠ **Three legs READ the handoff — `queue-items`, `handoff-symbols` and
`handoff-size` — and the handoff has exactly ONE owning session.** Solo, that is
you and there is nothing to decide. In a **parallel run it is one session and one
only**, per the operator's one-owner-per-shared-gitignored-doc rule, and the other
session must not write the file at all. So **establish ownership BEFORE running
phase 0, not when a leg fires**: if you are not the owner, discharge all three by
**reporting the finding to the owner** and record that you did — do not edit, do not
prune, and do not treat `handoff-size` as a gate on your branch. It is advisory
(`--exit-zero`) and it is measuring a file you have no write claim on.

⚠ **In a WORKTREE the handoff is ABSENT, and since 2026-08-25 (ST75) `handoff-size`
SKIPS instead of firing** — `in_linked_worktree()` compares `git rev-parse --git-dir`
against `--git-common-dir`, and the skip message NAMES the worktree. It previously
answered "handoff is absent — rewrite it", which was right solo and actively wrong
here: it pointed a non-owning session at creating a second copy of a single-copy file,
the one outcome nobody wants.

⚠ **The skip is NARROW, and absence in a normal checkout is still a hard finding.**
A blanket skip would make absent-because-worktree and absent-because-lost render
identically in the OTHER direction — "a SKIP is not a PASS" arriving from the far
side. `queue-items` and `handoff-symbols` also skip on an absent handoff, but for a
different and correct reason: they have nothing to check *against*, whereas this leg
owns "does it exist at all". ⚠ **`sensitive-terms` still reports NOT CONFIGURED from
a worktree and that is deliberate, not an oversight** — it guards an irreversible
publish, where "did not run" must never read as "passed", so copy the term list in
rather than expecting a skip.

**Why reporting is the whole fix rather than a lesser one:** the handoff is
gitignored and single-copy, so it has no remote and no merge. Two sessions pruning
it concurrently do not conflict — the second write silently erases the first, and
`Edit`-only discipline does not help, because the losing edit was already applied
to a file the winner had read before it. The failure is invisible at the time and
unrecoverable afterwards.
