# Run order — why phases, why the split around `gh pr create`

**When:** Read when you are tempted to reorder the phases, when the PR already exists, or before citing this skill from another doc.

Reference for `.claude/skills/post-branch/SKILL.md`, which holds the run order and the condensed rules. This file holds the full text, including the dated incidents behind each rule. The passages below are carried over verbatim from the pre-split SKILL.md (#884), so where one says "this file", "this skill", "above" or "below" about the phase table or a step, it means SKILL.md.

## Phases are the run order, step numbers are not

⚠ **Run the phases in the order below. The `Step N` headings further down are
ordered differently and are NOT the run order** — they are the bodies each phase
executes. This is the defect wifey's copy fixed first: numbering steps in one
order and running them in another means the numbering silently stops being
guidance. Read the phase table; treat the step headings as a table of contents.

## Cite the step, not the phase

⚠ **CITE THE STEP, NOT THE PHASE, from any doc outside this file.** Phases 1-6 exist only
as rows in the table above — they declare no headings — so `tools/stale_anchors.py`
correctly flags "post-branch phase 4" in another doc as a dead anchor, and a reader
following it finds no such section. That cost a peer session two rounds on #667. Inside
this file the phase names are the run order and stay; outside it, name `Step 7`.

## Always-run phases, situational steps

Phases 0 and 3 run **regardless** of the phase-1 gate: MEMORY.md lives outside the repo,
Issues live on GitHub and the handoff is gitignored, so none of them ever costs CI.
Steps 8 (rebase) and 9 (output format) are situational and belong wherever they
are needed.

## A phase that depends on a later phase's output

⚠ **Watch for a phase whose output depends on a fact a LATER phase creates.**
Phase 3 writes a MEMORY.md bullet naming `#NNN`, which does not exist until phase
4 — write the bullet with the number omitted and let phase 6's re-verify fill it
from the same `gh` query that rewrites the handoff. Do not reorder phase 3 after
phase 4 to "fix" this: MEMORY.md must be written even when no PR is ever opened.

## Phase 0 is not optional

**Phase 0 is not optional and it is not a summary of the rest.** A hand walk is
not the walk: twelve of these legs were copy-by-hand shell blocks in this file until
2026-08-19, which means they ran only when a session remembered to copy them, and
two of them had shipped broken. A green sweep is *not* a green branch — it covers
none of the judgement in phases 1–6.

## Why the split around `gh pr create`

**Do not "simplify" this into a blanket rule in either direction.** A blanket
"after" is what caused the defect; a blanket "before" is equally wrong, because
Steps 6/10a/10c cannot run until the PR exists.

**The measurement behind the split:** on #557 the walk ran after creation, found
a real `context/tools.md` omission, and the fix-push re-ran the entire suite via
`pull_request: synchronize` — ~3000 tests plus a 93s regression job, for one
paragraph. On #558 the walk ran first and the doc commit shipped in the initial
push: one CI run, not two. On #568 the old wording was followed again and cost
another redundant run.

**If `gh pr create` has already run**, do not skip the walk — run the whole thing
now and accept the extra CI run. A stale doc costs more than one CI cycle.

## The `PostToolUse` hook is a backstop

**The `PostToolUse` hook is a backstop, not the trigger.** It fires on
`gh pr create`, which is necessarily *after* — there is no hook event for "about
to open a PR", which is exactly why the ordering rule has to live in prose here
and in `AGENTS.md`. Two caveats worth knowing: the hook lives in
`.claude/settings.json`, tracked since 2026-08-19 so it now DOES survive a
reclone (it did not before), and it matches the
**whole command string**, so a `grep` or heredoc merely *containing*
`gh pr create` will fire it spuriously.
