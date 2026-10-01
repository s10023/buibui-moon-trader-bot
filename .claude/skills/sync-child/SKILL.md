---
name: sync-child
description: >
  Reverse catch-up tool — surfaces NET-NEW work from the wifey fork
  (buibui-wifey-wall-street-bot) worth back-porting into this parent repo.
  Mirror of the fork's `sync-parent`, run in the opposite direction. Scans
  wifey's merged PRs since the last sync point, drops dependabot / docs-config /
  already-ported-from-here noise, classifies the remainder PORT / EVALUATE, and
  writes a context-rich report to docs/plans/scratch/<date>-child-sync.md.
  Invoke when the user says "/sync-child", asks to "check the fork", "what's
  net-new in wifey", "back-port from wifey", or for a periodic fork catch-up.
allowed-tools: Bash, Read, Edit
---

# Sync from child (wifey) fork

Read-only catch-up tool, opposite direction to `sync-parent`. Surfaces wifey-fork
PRs that introduced **net-new** work — originating in the fork rather than in
this repo — and may be worth back-porting into this parent repo. **It never edits
parent code** — a human ports in a fresh session.

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
- `gh` must resolve as the `s10023` account. **Prefix each call inline —
  `GH_TOKEN=$(gh auth token --user s10023) gh -R ...`** — never `export ... ;` (the
  allowlist matches a command's first word, so the export form prompts every time)
  and never bare (the *active* account here is the work one).
  ⚠ **`gh auth switch` is something to ASK the operator to run, never to run
  yourself** — it changes global state affecting their other work.
- Wifey clone lives at `~/repo/buibui-wifey-wall-street-bot` — only needed if you
  want to read a PR's file contents locally; PR metadata comes from `gh -R`.

## What this scan CANNOT see — read before reporting "nothing net-new"

This skill keys on **merged PRs**. Anything that never became one is invisible, and the
scan's silence reads as coverage. Four shapes, all observed:

1. **A verdict or a retraction.** "We tested X, it failed" produces no PR. The most
   valuable thing a fork learns is often the thing it decided *not* to build.
2. **A design rationale.** Why a rule exists lives in prose, and prose that landed inside
   an otherwise-`SKIP — docs/config` PR is excluded by the classifier twice over.
3. **A measurement.** A number someone derived and filed leaves no diff.
4. **An UNMERGED PR — the worst of them**, because it correlates with importance rather
   than against it. On 2026-08-15 PR #631 was simultaneously the most consequential thing
   in this repo and a zero-hit on wifey's mirror scan; it crossed only because a session
   sent a message by hand. **The channel that worked was the one with no mechanism behind
   it.** When a scan returns little, check open PRs and the peer's handoff before
   concluding the fork is quiet.

**Ship this line verbatim in any port report:**

> Port the rule, re-derive the reason. A rationale is a claim about THIS repo's costs,
> coverage and constraints — verify it here before writing it down, even when the rule
> itself transfers unchanged.

It is not boilerplate. Measured 2026-08-15: of the guidance sent across on the off-site
backup work, the *rules* transferred intact while two *reasons* did not — the fork had no
`backup-offsite.sh` at all, so "you likely need this" was simply false, and the
personal-vs-dedicated account caveat had no account model to attach to. A rule copied with
a reason that does not hold here is worse than no rule, because the wrong reason is what a
future session will reason FROM.

### When you message the fork directly

**Lead with the MERGED item, not the open PR.** The open one is louder and usually the
thing you just finished, which makes it the easy opener and the wrong one — the fork can
act on merged work today and can only file an open PR as a watch item. Measured on the
same exchange: `/research-distil` had merged and was the ping wifey had been waiting on for
a day; it went out in a subordinate clause under an open PR that turned out not to be
portable at all.

## Classification rules

| Bucket | Rule |
| --- | --- |
| **SKIP — dependabot** | author is `app/dependabot` |
| **SKIP — docs/config** | `docs(...)` / `chore(...)` title, OR files only touch `*.md`, `config/`, `docs/`, `.github/` |
| **SKIP — ported from here** | title/body says "port #N" / "(port #N)", OR the conventional-commit subject matches a parent PR title, OR it belongs to a known port campaign (`T6 PR-*`, `P0a*`, `P0b*`, `Bucket C`, etc.). Cross-check against `gh pr list` (default = parent here). **No divergence diffing** — excluded entirely. |
| **SKIP — already applied here** | net-new in wifey, but this repo's target file already carries the same fix (e.g. wifey #376's `install-tasks.ps1` `$RepoRoot`). Cite the parent `file:line` that shows it. |
| **PORT** | net-new wifey feature/fix whose mechanism is domain-neutral (signal-daemon ops, data-quality, causality/lookahead, research methodology) → likely transferable |
| **EVALUATE** | net-new but equity-domain-specific (cost models, yfinance, universe-as-of) → methodology may transfer, values/impl won't |

When unsure between PORT and EVALUATE, prefer EVALUATE (forces a transferability
judgment in the fresh-session port).

## Workflow

1. **Read the watermark.** State file
   `$(PYTHONPATH=. poetry run python tools/memory_dir.py)/project_child_sync_state.md`
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

   **Before filing a PORT, confirm the defect is live HERE** — read or grep the parent
   target file and cite the line. Classify per part, not per PR: a "ported from here" PR
   can carry a wifey-only delta on top (wifey #312's `piped-gate` scoping), and one leg of
   an otherwise-SKIP PR can be a latent parent bug (wifey #374's `jq`-free hook, where the
   parent's `settings.json` reminders depended on a binary the Windows host lacks).
5. **Write the report** to `docs/plans/scratch/<date>-child-sync.md` (gitignored and
   backed up; `/tmp` is cleared on reboot, and every round since 2026-08-18 wrote here): a bucket-count summary
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
- **Stamp the cadence marker as the final step** (after the report is written):

  ```bash
  date -u +%FT%TZ > docs/plans/task-marks/sync-child
  ```

  `daily_check.py` reads it to decide whether the weekly fork catch-up is
  overdue. A missing marker reads as overdue by design. Stamp *after* the work.
