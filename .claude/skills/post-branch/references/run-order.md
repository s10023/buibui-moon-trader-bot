# Run order — why the split around `gh pr create`

**When:** Read when you are tempted to reorder the steps, when the PR already exists, or before citing this skill from another doc.

Reference for `.claude/skills/post-branch/SKILL.md`, which holds the run order and the condensed rules. This file holds the full text, including the dated incidents behind each rule. The passages below are carried over from the pre-split SKILL.md (#884), so where one says "this file", "this skill", "above" or "below", it means SKILL.md.

## Step numbers are the run order

The step bodies in SKILL.md appear in the order they run (renumbered under #840 M9; they were numbered in a different order from how they ran, which silently stopped the numbering being guidance). Cite a step by number from any doc: `Step 7`.

## Always-run steps, situational rebase

Steps 0, 5 and 6 run **regardless** of the Step 1 gate: MEMORY.md lives outside the repo,
Issues live on GitHub and the handoff is gitignored, so none of them ever costs CI.
The rebase section is situational and belongs wherever it is needed.

## A step that depends on a later step's output

⚠ **Watch for a step whose output depends on a fact a LATER step creates.**
Step 5 writes a MEMORY.md bullet naming `#NNN`, which does not exist until Step 7
opens the PR — write the bullet with the number omitted and let Step 11's re-verify
fill it from the same `gh` query that rewrites the handoff. Do not reorder Step 5
after Step 7 to "fix" this: MEMORY.md must be written even when no PR is ever opened.

## Step 0 is not optional

**Step 0 is not optional and it is not a summary of the rest.** A hand walk is
not the walk: twelve of these legs were copy-by-hand shell blocks in this file until
2026-08-19, which means they ran only when a session remembered to copy them, and
two of them had shipped broken. A green sweep is *not* a green branch — it covers
none of the judgement in Steps 1–12.

## Why the split around `gh pr create`

**Do not "simplify" this into a blanket rule in either direction.** A blanket
"after" is what caused the defect; a blanket "before" is equally wrong, because
Steps 8–11 cannot run until the PR exists.

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
