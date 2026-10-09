---
name: post-branch
effort: high
description: >
  Post-branch docs sweep and handoff: diff the branch's behaviour changes against
  the doc surfaces, propose edits where they drifted, run the pre-merge readiness
  check and write the fresh-conversation handoff. Invoke on every branch; its own
  behaviour gate decides how much work a branch needs. It splits around
  `gh pr create`, so start it before opening the PR, or immediately after if the
  PR already exists. Also triggers on "/post-branch", "wrap up the branch",
  "docs check", "pre-merge check" or "next conversation prompt".
allowed-tools: Bash, Read, Edit, Write
---

# Post-Branch Docs Sweep

The mental model: a PR's diff is the source of truth for what changed. The
docs are claims about how the codebase behaves. After a PR introduces new
flags, scripts, defaults, files, or commands, those claims often go stale —
sometimes silently. This skill walks a fixed list of doc surfaces, diffs
each one against the PR's actual behaviour, surfaces the drift, and proposes
edits the user can approve.

Its job is not to gatekeep the PR but to catch doc drift before merge — when
fixing it is still cheap. **Done** means every surface the diff reaches reads either
"updated: …" or "no change needed: <reason>" (Step 9), MEMORY.md and the Issues are
reconciled, the PR costs ONE CI run, and the handoff matches live PR state.

This file holds the run order, the rule at each step and the **Gotchas**. The dated
incidents behind each rule live in `references/`, and each step names the file to open.
**Read the Gotchas before Phase 0** — each is filed under the step it bites at.

## Running order — PHASES, not step numbers

⚠ **Run the phases in the order below. The `Step N` headings further down are
ordered differently and are NOT the run order** — they are the bodies each phase
executes. Treat the step headings as a table of contents.

| Phase | What | Step bodies | Costs CI? |
| --- | --- | --- | --- |
| **0** | **`make post-branch-checks`** — the mechanical sweep. Run it FIRST; its hits feed every later phase | — | no |
| **1** | Behaviour gate: is this PR user-facing? | 1 | no |
| **2** | Identify changed artifacts, walk each doc surface | 2, 3, 4 | no |
| **3** | Always-run regardless of the gate: MEMORY.md, Issue reconcile | 5, 5b | no |
| **4** | Commit and push, run **`make preflight`** (clean-clone gate — it REPLACES this branch's `make test`), **decide the visibility flip**, then `gh pr create` | 7 | **one run** |
| **5** | PR body | 6 | no |
| **6** | Pre-merge check, handoff, re-verify PR state **last** | 10a, 10b, 10c | no |

Phases 0 and 3 run **regardless** of the phase-1 gate. Phases 0–4 run **before**
`gh pr create`, so doc fixes ship in the initial push; Steps 6 and 10a/10c need the PR to
exist. **Do not "simplify" this into a blanket rule in either direction.** Steps 8 and 9
are situational. **If `gh pr create` has already run**, do not skip the walk — run the
whole thing now and accept the extra CI run. A stale doc costs more than one CI cycle.

## References — open each one at its step

| File | Open it |
| --- | --- |
| `references/run-order.md` | before reordering a phase, citing this skill elsewhere, or running after the PR exists |
| `references/mechanical-sweep.md` | before Phase 0; whenever a leg fires that you do not recognise |
| `references/behaviour-gate.md` | before Step 1 (it also holds the doc-surface glob config) |
| `references/doc-surface-walk.md` | before Step 2; it carries the Step 4 per-surface checks and recipes |
| `references/memory-and-issues.md` | before Step 5; always when the branch lands a `docs/research/` doc |
| `references/preflight.md` | before `make preflight` at Step 7, and before deciding to skip it |
| `references/visibility-flip.md` | before the flip decision at Step 7, and again before the flip back |
| `references/pr-body.md` | at Step 6 — the fetch → append → push commands |
| `references/pre-merge-check.md` | before Step 10a, and before reporting ANY failing check |
| `references/handoff.md` | before writing or pruning the handoff at Step 10b, and before Step 10c |
| `references/output-format.md` | at Step 9, when writing the final per-surface report |

## Phase 0 body — the mechanical sweep

```bash
make post-branch-checks
```

All legs advisory (`--exit-zero`); triage each hit. **Establish who owns the handoff
BEFORE running it** (Gotchas → Phase 0).

## Step 1 — Behaviour gate: is this PR user-facing?

Before walking any docs, decide if the PR changes behaviour a user or
operator would notice. **If not, stop after MEMORY.md update — don't churn
docs for invisible changes.** The surface list is `docs/agents/surfaces.toml`; the
globs are heuristics, so always read the diff.

**Walk the docs** on any of: a new CLI subcommand or flag; a new Make target or changed
default; a new TOML key, env var or system dependency; a renamed or moved file docs
reference; a new user-visible error or alert format; a behaviour change to a public
command; a new daemon or one-shot tool; and **any change to the doc surfaces themselves**.

**Notification surface — decide it, never default to it.** A new or changed scheduled
job, money-moving action, latching state transition, log-only failure path or actionable
periodic summary records ONE verdict — `always` / `on-change` / `on-failure-only` /
`never` (**must state why**). A channel whose only signal is failure is unfalsifiable;
multiply by the schedule before choosing `always`.

**Skip** (after MEMORY.md) only for a pure internal refactor, a bug fix with a test and
no behaviour change, a dependency bump, lint/format, test-only, in-source comments, or a
fixture refresh with goldens unchanged — **subject to the two exceptions** in Gotchas →
Step 1, which apply even when the gate says skip. When in doubt, ask the user.

## Step 2 — Identify changed artifacts

From the diff, build a short, concrete list the doc walk keys off: each new, renamed or
deleted **file**; each new **CLI flag/subcommand**; each new **Make target**; each new
**TOML key** or changed default; each module that became a **shim**
(`references/doc-surface-walk.md` spells out how to spot each).

## Step 3 — Walk each doc surface

For each surface: locate it, read it for outdated examples, missing entries, broken
paths, stale defaults and stale module-purpose descriptions, and **decide whether an edit
is warranted — minimal and targeted**. Propose each edit as a unified diff and **wait for
confirmation before writing**; apply via `Edit`, never `Write`.

## Step 4 — Surface-specific checks

**Open `references/doc-surface-walk.md` for this step** — it carries the per-surface
checks, the presence and three-outcome added-file checks, and the sibling-skill recipe.
Minimum bar:

- AGENTS.md Project Structure matches each module's real home; Key Commands / CLI resolve.
  **AGENTS.md must not re-absorb context-doc detail** — move it back out.
- README's CLI list matches `buibui --help`; every `buibui.py` subcommand has a
  `make buibui-<name>` target; daemons `restart: unless-stopped`, one-shots
  `profiles: [tools]`.
- A rule added to one skill: check every sibling skill that writes the same artifact.
- Does this PR add behaviour that nothing else monitors? Then `daily_check.py` probably
  owes it a line, and the PR body says so explicitly, because that file is gitignored.

## Step 5 — MEMORY.md update (always)

Regardless of the behaviour gate, **always update MEMORY.md's "Current
State"** (CLAUDE.md "Session Memory Protocol" is the authority; if they disagree,
CLAUDE.md wins):

- **Rewrite the "Latest" bullet** to today's date + a one-line summary of what
  changed. "Latest" is **at most 2 lines**; every other Current State bullet is
  exactly 1 line.
- **Current State holds at most 6 bullets.** If yours would be the 7th, first
  roll the oldest bullet **verbatim** into
  `memory/project_session_log_<month>.md`. Prune by MOVING, never by deleting.
- Convert any relative dates ("Thursday") to absolute (`2026-05-01`)
- **Do NOT record open questions or pending decisions here.** File each as an Issue
  labelled `question` (Step 5b). There is no "Previous session" bullet.

## Step 5b — Issue reconcile (always, and it is NOT covered by Step 5)

**First: does this branch close, change, or contradict an open Issue?** Closing → put
`Closes #<n>` in the PR body (Step 6) plus a one-line verdict comment. Changing or
contradicting → comment on the Issue **now, in this same session**. A NEW to-do, future
plan, skill fix, open question or pending decision becomes a new Issue — never a memory
row, never a handoff list; operator-waiting items carry `question`. Redact account
figures and screen any composed body with `make post-branch-text FILE=<path>`.

**Second: did this branch land a `docs/research/` doc that RECOMMENDS work?** If yes,
**file its own GitHub Issue naming the filename, in this same session** — no tool will
ever ask for one.

## Step 6 — Update the PR body

Once edits are approved and applied (or the gate decided no edits were needed), append a
"Documentation updates" section to the PR body — one line per surface — with the
fetch → append → push sequence in `references/pr-body.md`. If the body already has that
section, update it in place; don't append a duplicate.

## Step 7 — Commit and push

Commit doc edits as a single follow-up commit on the PR branch:

```bash
git add <files>
git commit -m "docs: sync docs with PR behavior changes"
git push
```

**MEMORY.md is never committed** — it lives outside the repo; save it with `Edit` and
never `git add` it. If it was the only change, say there is nothing to commit and move on.

**Push rules:**

- Default: `git push` (no force).
- If a rebase happened, use `--force-with-lease` and **only** with explicit
  user approval. Never `--force`.
- Never push to `main` from this skill. Ever.

### Then run the clean-clone pre-flight — it REPLACES `make test`

```bash
make preflight
```

It runs **after** the commit above, never before, and it IS this branch's one full-suite
run. `REFUSED` = dirty tree, nothing ran; `INFRA` = clone or install died (on the Windows
laptop run `make test` instead, name it in the PR body, and state that CI is the only
clean-clone verifier); only `FAILED` is a real finding.

### Then decide the visibility flip — BEFORE `gh pr create`

Every command for this sub-step is in `references/visibility-flip.md`. In order:

1. **Gate on the `sensitive-terms` leg.** `NOT CONFIGURED` → restore or copy the term
   list in; a hit on the branch's own commits is a STOP.
2. **Screen the FINAL PR title and body** with `make post-branch-text` (`FILE=<path>` for
   the body `/pr-summary` wrote, `FILE=-` for the title on stdin) — that leg cannot see them.
   On Windows give `FILE=` a forward-slash or repo-relative path, never `$TEMP`: `make`
   strips its backslashes and the screen dies on a missing file instead of running.
3. **Ask the user**, every time; a docs-only diff skips the flip. If flipping, finish the
   PR body FIRST, hand the operator the `gh repo edit … --visibility public` command and
   WAIT.
4. **Confirm it landed** (`gh repo view … --json visibility`, a read), then `gh pr create`.
5. Phase 6 closes the pair — the flip BACK, gated on `make wait-ci-main`, then
   **check → flip → RE-VERIFY** with the by-merge-SHA runs listing.

## Step 8 — Rebase handling (only when needed)

Sometimes a relevant doc lives on `main` but not on the PR branch (e.g. it landed in a
sibling PR), and the diff at Step 3 won't surface it. Check with
`git ls-tree main -- <doc-path>`; if it is missing here, ask the user whether to rebase or
leave it to the next PR. Rebase only on explicit OK (`git fetch origin main`, then
`git rebase origin/main`), and resolve conflicts the user's way, not by force.

## Step 9 — Output format

Output a per-surface report — one line per surface, `updated: <what>` or
`no change needed: <reason>` — from the template in `references/output-format.md`, whose
PR-summary path flattens `/` to `-`. Be explicit. "no change needed: internal refactor
only" is useful; silence is not.

## Step 10 — Post-PR handoff

### 10a — Pre-merge readiness check

```bash
make wait-ci PR=<n>     # resolves the SHA, prints steps=EXECUTED/DECLARED per job
```

Then run the status sweep in `references/pre-merge-check.md` and **flag, do not fix**:
uncommitted changes, unpushed commits, `mergeable: false` / `mergeable_state: dirty`
(`null` = still computing, re-query), failing required checks, a latest review of
`CHANGES_REQUESTED`. One line per item; if everything is green, say so explicitly:
`pre-merge: clean — ready when you are.`

### 10b — Fresh-conversation handoff prompt

**Precondition — do you OWN the handoff?** If another session owns it, **skip 10b**, hand
that session this branch's PR number and state, what it closes and any row it makes stale,
and say so in your final report. Otherwise **offer** (don't auto-write) to update
`docs/plans/next-conversation-prompt.md` — file-only output, never inline, never `/tmp`.
It carries SEQUENCING only (ordered Issue numbers, host state, standing hazards); update it
with targeted `Edit`s, prune on every task, and print only the path plus one line.

### 10c — Re-verify PR state as the LAST action (never skip)

Immediately before you report done — after **every** other step, including
any commit and push — re-query every PR named in the handoff, not just the
one this run created:

```bash
gh api repos/s10023/buibui-moon-trader-bot/pulls/<PR#> --jq '"\(.state) \(.merged_at)"'
```

Then rewrite the state table in place to match, and if a PR merged, the "first move"
line too.

## Gotchas

Each is a rule a past run broke; the incident is in the reference named beside it.

**Run order** (`references/run-order.md`)

- ⚠ **CITE THE STEP, NOT THE PHASE, from any doc outside this file.** Phases exist only as
  table rows, so `tools/stale_anchors.py` flags "post-branch phase 4" as a dead anchor.
- ⚠ **A phase whose output depends on a LATER phase:** write the MEMORY.md bullet with
  `#NNN` omitted and let phase 6 fill it. Do not reorder phase 3 after phase 4 —
  MEMORY.md must be written even when no PR is ever opened.
- **Phase 0 is not optional and it is not a summary of the rest.** A green sweep is *not*
  a green branch, and a hand walk is not the walk.

**Phase 0** (`references/mechanical-sweep.md`)

- ⚠ **`queue-items`, `handoff-symbols` and `handoff-size` READ the handoff, which has ONE
  owning session.** Not the owner? Report the finding to the owner and record that you
  did — do not edit, do not prune, and do not treat `handoff-size` as a gate.
- ⚠ **In a WORKTREE `handoff-size` SKIPS; in a normal checkout an absent handoff is a hard
  finding.** `sensitive-terms` still reports NOT CONFIGURED there — copy the list in.
- ⚠ **On the Windows host a worktree has no `.venv`, so every `poetry run` target fails
  there.** Run each leg by hand from the worktree root with the main checkout's
  interpreter: `PYTHONPATH=. <main-checkout>/.venv/Scripts/python.exe tools/<tool>.py`.
- ⚠ **`stale-anchors` is the leg with no substitute**, and it reaches the memory tree.
  ⚠ **`sensitive-terms` does NOT read the PR title/body** — Step 7 screens those.

**Step 1** (`references/behaviour-gate.md`)

- Every `gh` call here is REST (`gh api`), never `gh pr view --json` / `gh run view --json`
  — a cloud session refuses GraphQL with a 403.
- ⚠ **The gate reads FALSE on exactly the PR class where the walk pays most** — a change to
  the doc surfaces themselves. Walk it anyway.
- **Rule-claim exception:** a PR that adds or changes a rule greps the other surfaces for
  instructions that violate it before stopping. **Claim-falsification exception:** for
  each changed behaviour, grep for its *name* **and** the **verdict or guarantee** attached.
- ⚠ **Grep the MEMORY TREE too** for a claim about a SERIES and before diagnosing anything
  that has failed before; for a CODE-PATTERN fix, grep the skills' code fences too.

**Steps 3–4** (`references/doc-surface-walk.md`)

- **`any_referencing_changed_artifact` is BLIND TO OMISSION** — use the presence legs.
  **The `-w` is load-bearing — do not drop it back to a bare substring match.**
- `AMBIGUOUS basename (verify by hand)` is a real finding. ⚠ **Do not extend the `docs/`
  exclusion to a tree with no CI-gated generator.**
- **⚠ The single-source added-file form FALSE-GREENS** on uncommitted files; gitignored
  additions (`.claude/hooks/`, `docs/plans/`) need a deliberate look.
- ⚠ **A generated index that nothing reads back is a surface, not a check.**
- Sibling skills: **grep for the enforcement locus, NOT the field name**; ⚠ **a PROMPT-SIDE
  rule has NO locus — grep the artifact, then READ each hit.** A hit is a candidate.
- **If you ever doubt whether a path is gated, inject a violation and look.**

**Steps 5–5b** (`references/memory-and-issues.md`)

- ⚠ **An empty `gh issue list` is not "nothing to reconcile" when `gh` failed** — check the
  exit code. **An open Issue that is already done is worse than a missing one.**

**Step 7 — preflight** (`references/preflight.md`)

- ⚠ **`make test` then `make preflight` is the same suite twice.** ⚠ **"Replacement" does
  not mean stop running `make test` while you work** — a clone cannot see uncommitted code.
- ⚠ **Read the banner, not the exit code** — make collapses failure to its own 2. On the
  Windows host, compare a `FAILED` set against the #869 baseline before diagnosing.
- ⚠ **Not covered:** an absolute `$HOME` default, or a CLI branch no test reaches.
- ⚠ **Skip only when BOTH greps say no** — Python in the diff; `grep -rl <changed-path>
  tests/` — and name the gate you ran instead in the PR body. ⚠ **A comment or docstring
  hit still counts as a RUN**; ⛔ **do not narrow the grep**; ⛔ **the default stays RUN —
  never "this looks harmless".**

**Step 7 — flip** (`references/visibility-flip.md`)

- ⚠ **THE FLIP IS OPERATOR-RUN — you cannot perform it**, in either direction. Never assume
  it happened because you printed the command.
- ⚠ **Confirm the flip with the user on every occasion** — the mechanics are standing
  authorisation, the timing is not.
- ⚠ `make post-branch-text` GATES, but through `make` its exit code is **2** — read the
  banner. Run it on the FINAL text; an edited body does not unpublish the posted one.
- ⚠ **`make wait-ci-main` settles on ONE workflow; the flip affects ALL of them.**
  Check → flip → **RE-VERIFY** by merge SHA, never a branch listing; an empty answer is
  UNVERIFIED, not clean.

**Step 10a** (`references/pre-merge-check.md`)

- ⚠ **`wait_ci.py`'s exit codes do not survive `make`** — read the banner (3 = billing,
  1 = real failure, 4 = unreadable step counts, not a pass). **Read the EXECUTED half.**
- Prefix every `gh` call inline with `GH_TOKEN=$(gh auth token --user s10023)`, never
  `export … ;`. ⚠ **`gh auth switch` is something to ASK the operator for, never to run.**
- **⚠ Before reporting ANY failing check, read its step list:** `steps: []` on a FAILED job
  is billing — **never debug that shape** — and on a SKIPPED job it is neither.
- **⚠ Do NOT use a flat "under ~10 seconds never ran" rule** — compare each check against
  ITS OWN normal runtime.

**Steps 10b–10c** (`references/handoff.md`)

- **Update the handoff with targeted `Edit`s. NEVER `Write` the whole file.** **Read a
  closed section before deleting it** — open items hide under "DONE" headers.
- At most ONE "Just shipped" section; resolve an "Open work" row by DELETING it.
- ⚠ Write the RESOLVED memory path, never the `tools/memory_dir.py` template string.
  **Do NOT reinstate the old hand-run commands**, and do not re-create the skill-fix-queue
  or open-questions blocks — those are Issues.

## Safety rails (always)

- **Confirm every edit — scoped by `~/.claude-personal/CLAUDE.md` > Session
  hygiene, which this rail defers to rather than restates.** That file is the
  one definition: *write the handoff and the memory index unprompted; confirm
  every other edit, including untracked single-copy data like ledgers and
  journals.* So this skill is a proposer for tracked doc surfaces and for the
  gitignored ledgers under `docs/plans/`, and an applier for exactly two
  surfaces — the handoff and MEMORY.md.
  ⚠ **Do not restate the rule here.** A restated copy is what let the rail and
  the standing "write it unprompted" protocol contradict each other, which was
  resolved toward asking and manufactured a round-trip per task. Cite, don't
  duplicate.
- **Don't rename or move files.** Path churn breaks others' in-flight
  work. If a doc lives at the wrong path, propose the edit in place and
  flag the path issue separately for the user to triage.
- **Never use `Write` to overwrite a doc.** Always targeted `Edit`.
- **No force-push without explicit OK.** `--force-with-lease` only, after
  the user types yes.
- **Stop on uncertainty.** If you can't tell whether a doc claim is stale,
  show the user the doc snippet and the relevant diff hunk and ask.
- **Draft-PR default:** if `gh pr create` was run with `--draft`, don't flip
  it to ready-for-review as a side effect of this skill.

## When the skill should NOT run

- The PR is closed or merged (too late — open a follow-up `docs:` PR).
- The user said "skip docs" explicitly in the prompt.
- The PR is from Dependabot or another bot.
- The branch has no diff yet (PR was created against the wrong base).

In these cases, say so and stop.

## PR Summary template

Write the PR summary through `/pr-summary`; do not compose it from scratch. It takes the body
shape from mattpocock's `pr` skill (Summary visual, Evidence, Merge Danger) and adds this repo's
title rules, the honest-tick gate checklist under Evidence, `Closes #n`, and the Claude Code
footer.
