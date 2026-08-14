# Workflow enforcement audit — what is actually enforced, where, and by what

**Date:** 2026-08-14.
**Scope:** the daily operating loop across all three personal repos —
`buibui-moon-trader-bot`, `buibui-wifey-wall-street-bot`, `fpl`.
**Method:** read the hook configs, `CLAUDE.md` files and CI workflow triggers directly.
No claim here is quoted from memory; every number is measured.

## Verdict

**There is no global rule layer at all.** No `~/.claude-personal/CLAUDE.md` exists. The
only account-level enforcement is two hooks that carry no working rule. Everything the
operator thinks of as "our workflow" is per-repo prose, duplicated up to three times, with
nothing keeping the copies in sync — and the measured divergence is large.

**Three defects found, one of them systematic and live:**

1. Every recent merge leaves `main`'s CI **red for billing reasons**, 4 in a row.
2. The described loop's `post-branch → landing chain` ordering is wrong, and it produced a
   real defect on PR #626 the same day this audit was written.
3. `buibui`'s `CLAUDE.md` asserts a hook event does not exist. **It does, and `wifey`
   already uses it** — which would have prevented defect 2.

## 1. What is enforced GLOBALLY

Account-level config lives in `~/.claude-personal/settings.json`. Its entire hook surface:

| event | command | enforces a working rule? |
| --- | --- | --- |
| `SessionStart` | `context-mode-cache-heal.mjs` | no — tooling repair |
| `SessionEnd` | `budget.py --line` | no — spend accounting |

**There is no `~/.claude-personal/CLAUDE.md`.** Therefore **zero** behavioural rules are
globally enforced. Every rule below is per-repo.

**Consequence for the operator's question "should some rules be global, like being
concise":** BE CONCISE is currently a bullet in *buibui's* `MEMORY.md`. Memory is
per-project, so **`wifey` and `fpl` never see it**. Measured: the string appears **0 times**
in all three `CLAUDE.md` files. It is not a global rule, it is a buibui-only memory note.

## 2. What is enforced PER-REPO, and by what mechanism

Enforcement comes in exactly three strengths. Only the first can stop anything.

| strength | mechanism | count in buibui |
| --- | --- | --- |
| **BLOCKING** | hook exits 2 | **1** (`guard-destructive.py`) |
| **ADVISORY** | hook returns `additionalContext` | **3** (`guard-branch`, `context-guard`, post-branch reminder) |
| **PROSE** | `CLAUDE.md` / `MEMORY.md`, model must remember | everything else |

**All four hooks are gitignored.** No CI can reach them, a reclone loses every one, and the
only gate on them is a hand-run `test_context_guard.py` (31 cases). The strongest
enforcement in the system is also the least durable.

### Cross-repo enforcement surface

| | buibui | wifey | fpl |
| --- | --- | --- | --- |
| `CLAUDE.md` lines (always loaded) | 563 | 595 | **1,593** |
| hook scripts | **3** + test | 0 | 0 |
| `settings.json` hooks | PreToolUse ×2, PostToolUse ×1 | PreToolUse ×1 | **no settings.json** |
| destructive-command guard | **yes (blocking)** | **none** | **none** |
| skills | 28 | 24 | 6 |

### Rule presence across the three `CLAUDE.md` files (regex hit counts)

| rule | buibui | wifey | fpl |
| --- | --- | --- | --- |
| BE CONCISE | 0 | 0 | 0 |
| ONE plan = ONE PR | 0 | 0 | 0 |
| BRANCH FIRST | 0 | 0 | 0 |
| daily check unprompted | 2 | 0 | 0 |
| Definition of Done gate | 1 | 0 | 0 |
| post-branch split at PR | 1 | **4** | 0 |
| public-flip / visibility | 0 | **6** | 0 |
| powered-null criterion | **10** | 2 | 0 |
| scratch-is-output-not-code | **3** | 0 | 1 |
| subagent delegation | 12 | 11 | 0 |
| handoff / next-conversation | **4** | 1 | 0 |
| token efficiency | 1 | 0 | 0 |
| guard hooks | **13** | 4 | 0 |

**The top three rows are the headline: the three rules the operator treats as most
universal are written down in none of the three repos.** They survive only in buibui's
memory, which does not travel.

## 3. The described loop vs. reality

The operator's stated loop, checked step by step.

| # | described | accurate? | correction |
| --- | --- | --- | --- |
| 1 | open session, ask to read handoff, ask what's next | **incomplete** | The **daily check runs unprompted first** — it is a standing rule and it is not in the described loop. `MEMORY.md` is auto-loaded; the handoff is **not**, so asking for it is genuinely required. |
| 2 | read handoff + memory + SoT, suggest per category, recommend 1 | **accurate** | Matches `[[whats-next-reads-sot]]`: the handoff alone is not an answer. Categories A/B/C/D1/D2 exist in the handoff by design. |
| 3 | straightforward → main thread; complicated → spec + subagent | **accurate** | Matches the delegation policy and `[[fable-planning-priority]]`. |
| 4 | **post-branch, then landing chain** | **WRONG** | `/post-branch` **splits around `gh pr create`**. Steps 1–5b + 7 are commit-producing and run BEFORE; only 6 and 10a/10c run after. Treating them as two sequential blocks is what caused defect 2 below. |
| 5 | flip back after main's CI "gets triggered and starts running" | **insufficient** | The last-queued job starts minutes after the first. Flipping at "starts running" is what leaves main red — defect 1. |
| 6 | then take notes (handoff, memory) | **redundant** | Already `/post-branch` Step 5 (MEMORY.md), 5b (SoT) and 10b (handoff). Listing it again invites doing it twice or skipping it as "already done". |
| 7 | "what else before I delete session" → *?* | **should not need asking** | See §5. |

## 4. Defects found

### D1 — `main`'s post-merge CI is permanently red for billing reasons (systematic)

`lint.yaml` and `security-scan.yaml` both trigger on `push: branches: [main]`, so every
merge starts a fresh run on `main`. Flipping the repo back to private while those jobs are
still queued drops them onto the exhausted allowance. Measured across the last four merges:

| merge | CI on main |
| --- | --- |
| `9a4d420` (#623) | **all 4 jobs `steps: 0`** — pure billing wall |
| `09c8cfa` (#624) | 2 jobs failed mid-flight (steps present) |
| `b65b63e` (#625) | markdownlint failed, Regression `steps: 0` |
| `25a2988` (#626) | Regression `steps: 0` |

**Nothing here is a code failure.** The cost is that `main`'s CI signal is now dead: a
genuine regression on `main` would be indistinguishable from this noise, and every session
must re-derive "it's billing" from scratch.

Three candidate fixes, none chosen — this is an operator call:

- **(a) Flip back only after main's run fully completes.** Costs ~5 more minutes of public
  exposure per merge. Smallest change, keeps the signal.
- **(b) Drop `push: branches: [main]`** from `lint.yaml`. The post-merge run verifies the
  *identical tree* the PR just verified, so it is near-redundant with squash-merge on an
  up-to-date branch. Eliminates the class outright; costs independent verification of main.
- **(c) Raise the Actions spending limit.** The actual root fix, operator-only, and it
  retires the whole public-flip dance.

### D2 — the `post-branch` ordering error, reproduced live on #626

The doc walk was run *by hand* pre-PR, judged complete, and the PR opened. The skill was
then invoked (fired by the `PostToolUse` backstop) and its **mechanical** added-file check
immediately found `tools/era_power_price.py` missing from `.claude/context/tools.md`, which
enumerates all 19 other tools by name.

**The lesson is not "invoke the skill earlier".** A hand walk covers the surfaces you think
of; the mechanical check covers the ones you do not. *A hand walk is not the walk.*

### D3 — `buibui/CLAUDE.md` asserts a hook event that does exist, and wifey uses it

buibui's `CLAUDE.md` states:

> *"there is no hook event for 'about to open a PR', which is why prose has to carry the
> rule — but know it fires **after** creation"*

**False.** `wifey/.claude/settings.json` carries a **`PreToolUse`** hook on `Bash` matching
`gh pr create`, whose message reads *"you are ABOUT TO run `gh pr create`. Invoke the
`/post-branch` skill FIRST, while the branch is still local-only."*

buibui uses `PostToolUse` for the same trigger — which can only ever fire too late. **Had
buibui carried wifey's hook, D2 could not have happened.** This is the single highest-value
port in the audit and it costs one config edit.

### D4 — a context-guard card glob that does not cover its own rule (recurrence)

The `audit-verdict` card globs `tools/*audit*.py` and `tools/multi_regime_*.py`. It does not
match `tools/era_power_price.py` — a file whose entire purpose is DSR bars and CI
containment. The **claim** trigger caught the defect instead, by luck of the write
containing a negative claim.

`CLAUDE.md` already warns about exactly this class (*"`backtest-run-id` shipped covering 2
of 7"*). This is its second occurrence. Widen the card to `tools/*.py`, or accept that path
coverage is best-effort and the claim trigger is the real net.

## 5. What happens at "anything else before I delete the session?"

This is `[[end-of-task-self-improve]]`, and **the rule is that it runs UNPROMPTED.** That
the operator asks it every time means the rule is failing in practice — the question is
supposed to already be on the table.

Its content, in order:

1. **If a skill was invoked** — name the concrete friction hit during *this* run and offer
   the specific edit. Not a generic review.
2. **Standing items**, assuming the session is about to be deleted: rewrite
   `docs/plans/next-conversation-prompt.md`; update `MEMORY.md` Current State (6-bullet
   cap); reconcile the SoT; flag anything only the operator can do.
3. **Conditionally** — when the operator says they are about to delete — offer
   `/cleanup-conversations` with a transcript count. Never unconditional.

**Spend archival is deliberately NOT on this list.** A `SessionEnd` hook carries it. The
standing note attached to that decision is worth more than the decision: *a hook guarantees
the step RUNS, never that it WORKS* — that hook ran daily for four days while the data it
protected was being destroyed.

## 6. Recommended changes, ranked

1. **GLOBAL — create `~/.claude-personal/CLAUDE.md`** holding only the rules that are
   genuinely repo-independent: BE CONCISE, ONE plan = ONE PR, BRANCH FIRST, the
   handoff/memory protocol, and the end-of-session checklist. Today these live in one
   repo's memory and do not travel. **Keep it short** — it is paid on every session in
   every repo.
2. **GLOBAL — port wifey's `PreToolUse` `gh pr create` hook to buibui and fpl** (D3).
   Retire buibui's `PostToolUse` version or keep it as a belt-and-braces backstop.
3. **buibui → wifey/fpl:** `guard-destructive.py` (both currently have **no** protection
   against `rm -rf` / `git reset --hard` / force-push), `guard-branch.py`, and the
   `context-guard` mechanism.
4. **wifey → buibui:** the public-flip landing chain is documented in wifey's `CLAUDE.md`
   (6 hits) and **not at all** in buibui's (0) — buibui keeps it only in memory.
5. **fpl:** has no `settings.json`, no hooks, 6 skills, and a **1,593-line** always-loaded
   `CLAUDE.md` — 2.8× buibui's post-shed size. It is the biggest single context cost in the
   system and has had no shed pass.
6. **Fix the loop description itself** so step 4 reads *walk → flip → create → CI → merge →
   flip back → re-verify*, and delete step 6 as redundant.

## 7. What this audit does NOT claim

It did not read `fpl`'s or `wifey`'s memory trees, skills or handoffs — only their hook
configs and `CLAUDE.md`. The rule-presence matrix is a **regex hit count on one file per
repo**, so a rule enforced through a skill or a memory file reads as 0 here. Treat the
matrix as a map of the always-loaded tier, not of everything each repo knows.
