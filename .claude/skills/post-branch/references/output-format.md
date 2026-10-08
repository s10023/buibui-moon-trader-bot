# Step 9 — the per-surface report

**When:** Read when writing the final report, at the end of the run.

Reference for `.claude/skills/post-branch/SKILL.md`, which holds the run order and the condensed rules. This file holds the full text, including the dated incidents behind each rule. The passages below are carried over verbatim from the pre-split SKILL.md (#884), so where one says "this file", "this skill", "above" or "below" about the phase table or a step, it means SKILL.md.

Output a per-surface report so the user has a clear summary:

```text
PR #<num> behaviour gate: <walked | skipped (pure refactor)>

AGENTS.md          — updated: <what> | no change needed: <reason>
CLAUDE.md          — updated: <what> | no change needed: <reason>
README.md          — updated: <what> | no change needed: <reason>
MEMORY.md          — updated: Current State + <other>  (never committed)
Issue reconcile    — Closes #<n> in body | commented #<n> | filed #<n> | no Issue affected
Makefile           — no change needed: no new CLI commands
docker-compose.yml — no change needed: no new processes
.claude/context/*  — updated: analytics.md (store/ paths) | no change needed
.claude/skills/*   — updated: <skill> | no change needed: <reason>
gitignored ops     — daily_check.py line added (NOT in the diff) | no change needed: <reason>
PR summary         — written to docs/plans/scratch/pr-<branch>.md   (slashes flattened to -)
PR body            — appended "Documentation updates" section
pre-merge          — clean | <blocker> (see Step 10a)
handoff prompt     — written to docs/plans/next-conversation-prompt.md | declined
PR state re-check  — #<num>: <OPEN | MERGED>, handoff table rewritten to match
```

**The PR-summary path flattens `/` to `-`.** Every branch here is `docs/…`,
`feat/…`, `fix/…` or `chore/…`, so a literal `docs/plans/scratch/pr-<branch>.md`
names a directory that does not exist and the write fails. `pr-summary/SKILL.md` owns
the rule and the exact derivation; this line is the sibling that referenced the
same artifact without it, which is the blind spot Step 4 describes.

Be explicit. "no change needed: internal refactor only" is useful;
silence is not.
