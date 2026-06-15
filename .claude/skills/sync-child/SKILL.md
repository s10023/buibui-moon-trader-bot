---
name: sync-child
description: >
  Reverse catch-up tool — surfaces NET-NEW work from the wifey fork
  (buibui-wifey-wall-street-bot) worth back-porting into this parent repo.
  Mirror of the fork's `sync-parent`, run in the opposite direction. Scans
  wifey's merged PRs since the last sync point, drops dependabot / docs-config /
  already-ported-from-here noise, classifies the remainder PORT / EVALUATE, and
  writes a context-rich report to /tmp/child-sync-<date>.md.
  Invoke when the user says "/sync-child", asks to "check the fork", "what's
  net-new in wifey", "back-port from wifey", or for a periodic fork catch-up.
allowed-tools: Bash, Read, Edit
---

# Sync from child (wifey) fork

Read-only catch-up tool, opposite direction to `sync-parent`. Surfaces wifey-fork
PRs that introduced **net-new** work (not ported from here) and may be worth
back-porting into this parent repo. **It never edits parent code** — a human
ports in a fresh session.

The fork mostly ports *from* this repo, so the net-new surface is small and
signal-dense. The job is to filter the noise and flag the few genuine net-new
items.

## When to use

- Periodic catch-up with the fork (the fork iterates on shared infra — signal
  daemon, data-quality, research guards — sometimes ahead of here).
- After a known wifey feature/fix you want triaged for back-port.
- Bootstrap (first run) — scans wifey's full PR history (all of it is post-fork).

## Critical gotchas (read before running)

- **`gh` in THIS repo's dir defaults to the PARENT**, not wifey — the wifey clone
  has an `upstream` remote pointing here, and `gh repo view` resolves to
  `s10023/buibui-moon-trader-bot`. A bare `gh pr view 69` silently reads *this*
  repo's #69. **Every wifey query MUST pass `-R s10023/buibui-wifey-wall-street-bot`.**
- `gh` must be on the `s10023` account (`gh auth switch --user s10023` if you hit
  "could not resolve repo" / GraphQL errors).
- Wifey clone lives at `~/repo/buibui-wifey-wall-street-bot` — only needed if you
  want to read a PR's file contents locally; PR metadata comes from `gh -R`.

## Classification rules

| Bucket | Rule |
| --- | --- |
| **SKIP — dependabot** | author is `app/dependabot` |
| **SKIP — docs/config** | `docs(...)` / `chore(...)` title, OR files only touch `*.md`, `config/`, `docs/`, `.github/` |
| **SKIP — ported from here** | title/body says "port #N" / "(port #N)", OR the conventional-commit subject matches a parent PR title, OR it belongs to a known port campaign (`T6 PR-*`, `P0a*`, `P0b*`, `Bucket C`, etc.). Cross-check against `gh pr list` (default = parent here). **No divergence diffing** — excluded entirely. |
| **PORT** | net-new wifey feature/fix whose mechanism is domain-neutral (signal-daemon ops, data-quality, causality/lookahead, research methodology) → likely transferable |
| **EVALUATE** | net-new but equity-domain-specific (cost models, yfinance, universe-as-of) → methodology may transfer, values/impl won't |

When unsure between PORT and EVALUATE, prefer EVALUATE (forces a transferability
judgment in the fresh-session port).

## Workflow

1. **Read the watermark.** State file
   `~/.claude-personal/projects/-home-kng-repo-buibui-moon-trader-bot/memory/project_child_sync_state.md`
   holds the last-reviewed wifey PR number + date. Absent / first run → scan all
   wifey PRs (bootstrap).
2. **Pull wifey merged PRs since the watermark** (explicit `-R`):

   ```bash
   gh pr list -R s10023/buibui-wifey-wall-street-bot --state merged --limit 60 \
     --json number,title,author,mergedAt,files \
     -q '.[] | "#\(.number)\t\(.author.login)\t\(.title)"'
   ```

   Filter to `number > watermark`.
3. **Pull parent PR titles for the port-match check** (default repo = parent):

   ```bash
   gh pr list --state merged --limit 80 --json number,title -q '.[] | "#\(.number)\t\(.title)"'
   ```

4. **Classify each** wifey PR per the table. For each **PORT / EVALUATE**, fetch
   detail and enrich:

   ```bash
   gh pr view <N> -R s10023/buibui-wifey-wall-street-bot \
     --json title,files,body -q '.title + "\n" + (.files|map("  "+.path)|join("\n")) + "\n\n" + .body'
   ```

   Record: one-line what-it-does · wifey files → parent target files · a
   crypto-vs-equity transferability note · suggested approach
   (`verify-only` / `cherry-pick-with-edits` / `re-implement`) · any
   dependency on an earlier wifey PR (note it as a prerequisite).
5. **Write the report** to `/tmp/child-sync-<date>.md`: a bucket-count summary
   table, then PORT / EVALUATE / SKIP sections (PORT and EVALUATE as full detail
   blocks; SKIP as a compact list with the skip reason).
6. **Summarise for the user** (bucket counts + the PORT/EVALUATE shortlist) and
   print the bump hint: "advance watermark to #<highest reviewed>". Only update
   the state file once the user confirms the range is decided — never auto-bump.

## Notes / common mistakes

- The single biggest failure mode is forgetting `-R` and reading parent PRs by
  mistake. If a "wifey" PR's title/files look identical to recent parent work,
  you probably dropped the `-R`.
- A PR that builds on an earlier net-new wifey PR (e.g. `--catch-up` builds on
  the watermark-on-send fix) needs that prerequisite triaged too — surface both.
- Correctness fixes (NaN crash, lookahead, watermark) outrank nice-to-have
  features: a wifey fix may be a *latent parent bug*. Rank those first in the
  PORT section.
- This skill recommends only. The actual port happens in a fresh session with
  the report's detail block + the wifey PR pasted in.
