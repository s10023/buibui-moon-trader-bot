---
name: pr-summary
effort: low
description: >
  Write a PR title and body to `docs/plans/scratch/pr-<branch>.md` — slashes in the branch
  name flattened to `-` — after a branch is complete (lint/typecheck/tests green, commit
  done). The body takes the shape of mattpocock's `pr` skill (Summary visual, Evidence, Merge
  Danger); this skill adds the repo's title, test-plan and output rules. Never returns the
  content inline. Invoke automatically when a branch finishes — do not wait. Also triggers on
  the user saying "/pr-summary", "PR summary", "write a PR", or "finish up the branch".
allowed-tools: Bash, Write, Read, Skill
---

# PR Summary

The body's **shape** comes from mattpocock's `pr` skill. This skill owns what that one cannot
know: where the file goes, how the title is written, which gates the body may claim, and how
`gh` is called here.

## Steps

1. Call the Skill tool with `mattpocock-skills:pr` for the body shape and its section guidance.
2. Gather the facts: `git branch --show-current`, `git log main..HEAD --oneline`,
   `git diff main..HEAD --stat`, and the Issue(s) the branch closes.
3. Write the title (rules below).
4. Write the body in the `pr` shape, with this repo's additions (below).
5. Screen it: `make post-branch-text FILE=<path>` and fix every finding. It gates; through
   `make`, read the banner rather than the exit code. On Windows pass a forward-slash or
   repo-relative path, never `$TEMP`, whose backslashes `make` strips.
6. Write it to the output path and return only the path.

## Output location

`docs/plans/scratch/pr-<flattened-branch-name>.md`, where every `/` in the branch name is
flattened to `-`. The branch convention is `feat/`, `fix/`, `docs/`, `chore/`, so an
unflattened name points into a directory that does not exist, and a path the next session
cannot predict defeats the "return only the path" contract. The directory is gitignored and
backed up.

```bash
BRANCH=$(git rev-parse --abbrev-ref HEAD)
mkdir -p docs/plans/scratch
OUT="docs/plans/scratch/pr-$(printf '%s' "$BRANCH" | tr '/' '-').md"
```

## Title

The file opens with the title in a code span under `## PR Title`, then the body.

- Conventional commit, under 70 characters: `feat` · `fix` · `refactor` · `test` · `docs` ·
  `build` · `chore`, with a scope.
- **Name the mechanism you changed, not the symptom.** Squash-merge makes the title the
  permanent commit message. The anti-pattern table is in `AGENTS.md` → PR titles.
- **No derived number** (line counts, file counts, sizes). A later commit moves it, and
  correcting a title means rewriting `main`; the body is one edit away from correct.

## Body additions to the `pr` shape

- **Summary** opens with one sentence of why, then `Closes #<n>` for each Issue the branch
  finishes, so the merge closes it.
- **Evidence** carries the gate checklist. **Tick only a command that has already returned:**
  a gate still running gets `[ ]` plus a note, and is ticked when it finishes. **Name the gate
  you ran:** `make preflight` replaces `make test` at the final gate, so ticking `make test`
  after running the preflight claims a command you did not run. Say whether
  `make test-regression` applied (the diff touches the backtest surface) or was skipped, and
  why.

  ```markdown
  - [x] `make preflight` — <N> passed on a clean clone *(or `make test` where preflight exits 3)*
  - [x] `make lint-py` · `make typecheck` · `make lint-md`
  - [ ] Manual: <item>
  ```

- **Merge Danger** names the live surfaces at risk when they apply: the signal-watch timer runs
  the working tree, so a config TOML or schema change is live within 15 minutes of landing.
- One line per paragraph or bullet, no hard wraps: GitHub renders a single newline as a break.
- End with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
- `/post-branch` Step 8 later appends a `## Documentation updates` section; leave room for it.

## Calling `gh` here

The active `gh` account is the work one, so prefix the call inline (never `export …;`, which
is not allowlisted, and never `gh auth switch`, which is the operator's to run):

```bash
GH_TOKEN=$(gh auth token --user s10023) gh pr create --title "<title>" --body-file "$OUT"
```

When `--body-file` is the whole file, strip the `## PR Title` block first. If `gh pr create`
fails with `must be a collaborator` straight after a visibility flip, retry once before
touching auth: GitHub re-evaluates permissions asynchronously after a flip.
