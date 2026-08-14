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

**⚠ THIS MATRIX HAS FALSE NEGATIVES AND BOTH PEERS FOUND ONE.** It is a regex hit count on
one file per repo, so it measures *my patterns*, not the rules. Corrections received
2026-08-14g:

- **BRANCH FIRST is in wifey's `CLAUDE.md`** (Git Conventions, with the full recipe). It
  reads 0 above because wifey writes `git switch -c` and my pattern searched
  `branch first|checkout -b`. **The rule was there; my grep was not.**
- **wifey's flip/visibility count is 12, not 6** — again a narrower pattern than the text.
- **wifey carries BE CONCISE and ONE-PR too**, in `feedback_be_concise_everywhere.md`,
  `MEMORY.md`, the handoff and `post-branch/SKILL.md` — none of which this matrix reads.

This is the same defect the `/post-branch` skill documents about its own presence checks: a
grep that reports "absent" cannot distinguish *absent* from *phrased differently*. **The
corrected headline is weaker but still true: the three universal rules are enforced only in
per-repo memory and skills, both of which are gitignored in every repo, so none of them
survives a reclone and none is shared.** fpl genuinely has none of the three.

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

### D1 — main's post-merge CI loses its `needs:`-chained job on every flip

**⚠ This section's first draft said "main's CI is permanently red for billing, the signal is
dead". That framing was WRONG and is corrected here.** The wifey session measured its own
merge and found a narrower mechanism; re-measuring buibui confirms wifey's version. The
error is recorded rather than silently fixed, because the frame travelled further than the
figures did — see §8.

`lint.yaml` and `security-scan.yaml` trigger on `push: branches: [main]`, so every merge
starts a fresh run on main. **What is lost is not "the run" — it is whichever jobs are
CREATED after the flip back to private.** `lint.yaml:136` declares
`needs: lint-typecheck-test` on the Regression job, so GitHub does not create that job until
the lint job finishes — which on a real run is ~5 minutes later, long after the flip.

Measured, per job, last four merges:

| merge | outcome |
| --- | --- |
| `9a4d420` (#623) | all 4 jobs `steps: 0` in ~2s — **repo was already private at merge**; a different failure, not this one |
| `09c8cfa` (#624) | lint + frontend failed **with** steps (14/9) in 7s; markdownlint passed |
| `b65b63e` (#625) | lint + frontend passed (7s short-circuit); **markdownlint failed with 7 steps — a real failure, not billing**; Regression `steps: 0` |
| `25a2988` (#626) | **lint-typecheck-test SUCCESS, 16 steps, 5m04s — the full suite genuinely ran on main.** markdownlint + frontend green. Only Regression `steps: 0` |

**So 3 of 4 jobs on the most recent merge are genuinely green with real step counts.** The
corrected claim is narrow and predictable: *the `needs:`-chained job is lost whenever the
flip precedes the end of the chain.* Two things this audit wrongly folded into "billing"
belong outside it — `9a4d420` (never public) and `b65b63e`'s markdownlint failure (7 steps,
so it ran and failed on its merits).

**Chosen fix (operator, 2026-08-14g): flip back only after main's run COMPLETES.** Priced
from `25a2988`: the chain ended 07:47:28 against a 07:42:15 merge, so the wait is **~5
minutes**. The alternative of dropping the `push: main` trigger was rejected — with the
narrow mechanism understood, the run is worth keeping.

### D1b — `9a4d420`'s all-`steps: 0` run is a SEPARATE failure

That merge produced four 2-second, zero-step jobs. That is the repo being private *at merge
time*, not a flip-timing race. Wifey reproduced the identical shape on its own docs-only
merge that never went public. **Do not conflate the two: one is fixed by waiting, the other
by being public at the moment of merge.**

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

## 7. THE REAL FINDING — three repos, three enforcement LAYERS, each holding a third

Both peer sessions verified their own halves and returned the same structural point from
opposite directions. It is more useful than anything in §2.

| repo | enforces at | can it fire at edit time? | can it fail a build? |
| --- | --- | --- | --- |
| **buibui** | **hook layer** — 4 hooks, 1 blocking | **yes** | **no** |
| **wifey** | **suite layer** — rules as pytest cases in `make test` | no | **yes** |
| **fpl** | **CI layer** — eslint/prettier/vitest, `dorny/paths-filter` gated | no | **yes** |

**buibui's hooks cannot fail a build; wifey's and fpl's tests cannot fire at edit time.**
They are complements, not competitors, and no repo has more than one of the three.

**wifey's suite-layer examples, none of which a hook could do:**
`test_schema_insert_arity.py` (ties every positional INSERT to its table's real columns),
`test_docs_index.py` (regenerates both indexes and compares byte-for-byte, so an unindexed
audit reddens the suite), `test_outcome_backfill.py::TestMaxHoldCalibrationCoverage` (walks
every live config, fails on a timeframe with no hold cap), plus advisory
`make check-orphan-tests` / `check-dead-surfaces`.

**fpl's version is sharper still — source-pin tests**, greps over the sources run by
`make check` and by CI on every PR: *".toFixed() only ever builds label strings in .gs
sources"*, *"playerByWebName is gone from every .gs source"*.

**And fpl's argument, which should change buibui's shed strategy:**

> If a footgun is expressible as a grep, a source-pin test **dominates** a context-guard
> card. The card is advisory and fires only if a guarded file is edited *by an agent that
> then heeds it*; the test fails the build regardless of who wrote the code or whether
> anyone read anything.

So the shed rule becomes: **if a paragraph can be mechanised as a grep, make it a test and
DELETE the paragraph** — deletion candidate, not relocation candidate. That reframes both
fpl's 1,593 lines and buibui's remaining 563. fpl learned it expensively: a reconciliation
tool printed `FAILED` for five days unseen because it only ran by hand. Their standing
memory is **"a self-check outside CI is not a check"** — which is buibui's own gitignored
`test_context_guard.py` problem, stated better.

**wifey's `/post-branch` has two steps buibui's lacks**, and one has a track record:
**Step 5c, a claims audit, has caught a false quantitative claim on NINE consecutive PRs**,
including one the same day. Step 5d checks the docs index. Both are portable.

## 8. NEW — the account-level flat `.md` "skills" have never once loaded

Measured by the fpl session against its own loaded-skill listing. **This closes the
"account-level vs repo-level skill precedence is UNVERIFIED" question that had been open
since the earlier peer exchange, and the answer is that the question was malformed.**

A skill is a **directory** containing `SKILL.md` with YAML frontmatter.
`~/.claude-personal/skills/` holds 12 flat `.md` files and 9 directories:

- all 9 repo skills load; **8 of 9 account DIRECTORY skills load**;
- **ZERO of the 12 flat `.md` files load** — `pr-summary`, `atr-sweep`, `backtest-findings`,
  `backtest-run`, `new-strategy`, `param-sweep-apply`, `recalibrate`, `signal-watch`,
  `stats-dashboard`, `volume-sweep` are **inert**;
- `pr-summary` appears in the listing, but its description is verbatim the **repo**
  `SKILL.md` frontmatter. **It did not win a precedence contest — the account file was never
  a candidate.**

**The one genuine precedence datapoint:** `django-expert` is a valid account-level directory
skill and is ABSENT from fpl's listing, because fpl's `.claude/settings.local.json` sets
`skillOverrides: {django-expert: off}`. **So account directory skills DO load into a repo
session, and repo-level config can switch them off — repo beats account.**

Actionable: if any repo treats those flat files as fallbacks, they are a silent no-op. It
also means "the same skill exists in four places" was never the risk it read as.

## 9. Methodology — the frame travels further than the figures

Both peers were asked to treat this audit as a lead and verify. Both did, and **the thing
that needed correcting was the framing, not the numbers.** D1's figures were right; its
sentence *"main's CI signal is now dead"* was not, and that sentence is what a reader
inherits. Wifey named it explicitly: *verify the frame, not just the figures.*

Two corrections to fpl's section came the same way: *"no `.claude/settings.json` at all"* was
right in conclusion (no hooks) and wrong in premise (`settings.local.json` exists, and
`.gitignore` already encodes a shared-vs-local split any port must respect); and *"fpl has
essentially no operating discipline"* was simply false — it has CI, it just enforces at a
different layer.

## 10. What this audit does NOT claim

The first draft did not read `fpl`'s or `wifey`'s memory trees, skills or handoffs — only
their hook configs and `CLAUDE.md`. §§7–9 exist **because both peer sessions read their own
and corrected me**; that content is their measurement, not mine, and it is the half that
turned out to matter. The rule-presence matrix remains a regex hit count on one file per
repo, with known false negatives (see the warning under it).

**Still unread here:** wifey's and fpl's memory trees and handoffs in full, and the content
of fpl's 1,593 `CLAUDE.md` lines — which fpl notes likely contains a meaningful slice of
rules already machine-enforced by its own tests, i.e. deletion candidates. Nobody has
measured that slice.
