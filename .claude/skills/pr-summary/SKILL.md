---
name: pr-summary
description: >
  Write a PR title, summary, and test plan to `/tmp/pr-<branch>.md` after a
  branch is complete (lint/typecheck/tests green, commit done). Never returns
  the content inline.
  Invoke automatically when a branch finishes — do not wait. Also triggers on
  the user saying "/pr-summary", "PR summary", "write a PR", or "finish up
  the branch".
allowed-tools: Bash, Write, Read
---

# PR Summary

Write a PR title + summary + test plan after finishing a branch. Always write to `/tmp/pr-<branch>.md` — never return as inline text.

## When to use

After every branch is complete: lint/typecheck/tests pass, commit done. Do not wait to be asked.

## Output location

Always write to `/tmp/pr-<branch-name>.md`. Return only the file path, not the content inline.

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

- [x] `make test` — <N> passed
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

`gh pr create` fails for this project (collaborator permission error). Provide the PR summary as a copyable file at `/tmp/pr-<branch>.md` — the user will paste it manually into GitHub.

## Conventional commit types for PR titles

- `feat(scope):` — new feature or behavior
- `fix(scope):` — bug fix
- `refactor(scope):` — code restructure, no behavior change
- `test(scope):` — new or updated tests only
- `docs(scope):` — documentation only
- `build(scope):` — build system / dependencies
- `chore(scope):` — maintenance (cleanup, config)

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
10. Write to `/tmp/pr-<branch-name>.md`
11. Return only the file path
