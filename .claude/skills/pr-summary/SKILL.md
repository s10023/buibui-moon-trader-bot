---
name: pr-summary
description: >
  Write a PR title, summary, and test plan to `docs/plans/scratch/pr-<branch>.md` —
  slashes in the branch name flattened to `-` — after a branch is complete (lint/typecheck/tests
  green, commit done). Never returns the content inline.
  Invoke automatically when a branch finishes — do not wait. Also triggers on
  the user saying "/pr-summary", "PR summary", "write a PR", or "finish up
  the branch".
allowed-tools: Bash, Write, Read
---

# PR Summary

Write a PR title + summary + test plan after finishing a branch. Always write to
`docs/plans/scratch/pr-<branch>.md` with slashes flattened to `-` (see Output location) — never
return as inline text.

## When to use

After every branch is complete: lint/typecheck/tests pass, commit done. Do not wait to be asked.

## Output location

Always write to `docs/plans/scratch/pr-<flattened-branch-name>.md`. Return only the file
path, not the content inline. The directory is gitignored and backed up; `/tmp` is cleared
on reboot, which is the same move `/sync-child` made for its report.

**Flatten every `/` in the branch name to `-` first.** This repo's branch convention is
`docs/`, `feat/`, `fix/`, `chore/`, so a raw `docs/plans/scratch/pr-<branch>.md`
is `docs/plans/scratch/pr-docs/relay-attribution-spec.md` — a path under a directory that does not
exist. The write then fails, or a session silently invents its own flattening and the
next session cannot find the file. Since this skill's whole contract is "return only the
file path", a path nobody can predict defeats it.

Derive it exactly this way, so every session picks the same name:

```bash
BRANCH=$(git rev-parse --abbrev-ref HEAD)
mkdir -p docs/plans/scratch
OUT="docs/plans/scratch/pr-$(printf '%s' "$BRANCH" | tr '/' '-').md"
```

`fix/small-queue-clearing` ⇒ `docs/plans/scratch/pr-fix-small-queue-clearing.md`.

## Template

```md
## PR Title

`<type>(scope): short imperative description under 70 chars`

## Background

<1-2 sentences: what problem or gap this addresses, why it matters now, and any relevant context (e.g. strategy source, prior limitation, user-facing impact)>

Reviewers should understand the motivation before the mechanics.

## Summary

- <bullet 1>
- <bullet 2>
- <bullet 3>

## How it works

<1-3 paragraphs or bullets explaining the implementation — keep it readable for someone who hasn't seen the code>

## Params / Config

<table or bullets of new params, defaults, where configured — omit if none>

## Test plan

Items already verified by CI at commit time are pre-ticked. Manual items remain unchecked.

**Only tick a command that has already returned.** "Verified at commit time" means the
result is in hand — not that the command is running and expected to pass. A gate still
in flight gets `[ ]` plus a note, and is ticked once it finishes. A PR body is durable
and gets read as a claim about what was checked, so a hopeful tick is a false statement
even when the run later goes green. This bit on 2026-08-03: `make test-regression` was
pre-ticked while still executing.

**Name the gate you actually ran.** At a branch's final gate `make preflight` REPLACES
`make test` (`AGENTS.md`, Definition of Done), so a session that ran the pre-flight and
ticked the `make test` line has asserted a command it deliberately did not run — the same
false claim as a hopeful tick, arriving by a different route.

- [x] `make test` — <N> passed  *(or `make preflight` — <N> passed on a clean clone)*
- [x] `make lint-py` — ruff clean
- [x] `make typecheck` — mypy clean
- [x] `make lint-md` — markdownlint clean (only if MD files changed)
- [ ] Manual: <item 1>
- [ ] Manual: <item 2>

## Stats

- Tests: <N> total (<+N> new)
- Files changed: <list>

🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

## Note on GitHub CLI

`gh pr create` **works** for this project — it created PRs #522–#525 across
2026-08-01/02/03. This section used to claim it fails with a collaborator
permission error; that was stale and cost several PRs a manual paste for no
reason. Still write the file at the flattened `docs/plans/scratch/pr-<branch>.md` (it is the deliverable of
this skill, and useful as a `--body-file`), but do not tell the user the CLI is
unavailable.

⚠ **"That was stale" is TOO STRONG — the collaborator error still occurs, for a
different reason, and the fix is to RETRY (2026-08-17, #639).** `gh pr create` run
immediately after `gh repo edit --visibility public` failed with
`GraphQL: must be a collaborator (createPullRequest)`; the identical command
succeeded seconds later with nothing changed. **Diagnose before believing either the
error or this doc** — under the personal token, `gh api user` returned `s10023` and
`gh repo view` returned `PUBLIC`, so auth and visibility were both already correct.
GitHub re-evaluates permissions asynchronously after a visibility flip. **Retry once
before touching auth**; a `git push` succeeding proves nothing here, since the remote
is an SSH alias and never exercises the token.

If `gh` ever does fail with "Could not resolve to a Repository", that is the
account, not the permission. Don't debug `gh` config past that — **prefix the call
with the personal token, inline:**

```bash
GH_TOKEN=$(gh auth token --user s10023) gh pr create --body-file "$OUT"
```

**Inline, never `export … ;`.** The allowlist matches on a command's first word, so
the `export` form is two commands joined by `;` whose first word is `export` — not
allowlisted, so it prompts every time. The inline form's command word is `gh`, which
matches. **The prefix cannot just be dropped:** the *active* gh account here is the
work one, so a bare `gh` call authenticates as the wrong user.

⚠ **`gh auth switch` is something to ASK the operator to run, never to run yourself** —
it changes global state affecting their other work.

## Conventional commit types for PR titles

- `feat(scope):` — new feature or behavior
- `fix(scope):` — bug fix
- `refactor(scope):` — code restructure, no behavior change
- `test(scope):` — new or updated tests only
- `docs(scope):` — documentation only
- `build(scope):` — build system / dependencies
- `chore(scope):` — maintenance (cleanup, config)

## No derived number in the title

**Line counts, file counts and sizes go under `## Stats`, never in the title.**
Squash-merge makes the title the permanent commit message, so correcting a stale
figure there means rewriting `main` — while the same figure in the body is one edit
away from correct, and the title is the most quotable line in the whole PR. A later
commit moves every derived number: wifey's #203 reads `822 lines / 61KB` against a
true `873 / 65.8KB` because the branch grew after the summary was written.

## Task: write a PR summary

When the user asks to write a PR summary or after finishing a branch:

1. Get the current branch name: `git branch --show-current`
2. Get commit list: `git log main..HEAD --oneline`
3. Get files changed: `git diff main..HEAD --stat`
4. Draft the PR title (under 70 chars, conventional commit format)
5. Write background context — why this change exists, not just what it does
6. Write summary bullets — 3–5 key changes
7. Write "How it works" — implementation details for reviewers
8. Fill in Params/Config section if any new TOML keys or CLI flags were added
9. Fill in test plan — check CI items, list remaining manual verification steps
10. Write to `docs/plans/scratch/pr-<branch-name>.md`, slashes flattened to `-`
11. Return only the file path
