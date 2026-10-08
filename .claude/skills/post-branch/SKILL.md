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
fixing it is still cheap.

## Running order — PHASES, not step numbers

⚠ **Run the phases in the order below. The `Step N` headings further down are
ordered differently and are NOT the run order** — they are the bodies each phase
executes. This is the defect wifey's copy fixed first: numbering steps in one
order and running them in another means the numbering silently stops being
guidance. Read the phase table; treat the step headings as a table of contents.

| Phase | What | Step bodies | Costs CI? |
| --- | --- | --- | --- |
| **0** | **`make post-branch-checks`** — the mechanical sweep. Run it FIRST; its hits feed every later phase | — | no |
| **1** | Behaviour gate: is this PR user-facing? | 1 | no |
| **2** | Identify changed artifacts, walk each doc surface | 2, 3, 4 | no |
| **3** | Always-run regardless of the gate: MEMORY.md, Issue reconcile | 5, 5b | no |
| **4** | Commit and push, run **`make preflight`** (clean-clone gate — it REPLACES this branch's `make test`), **decide the visibility flip**, then `gh pr create` | 7 | **one run** |
| **5** | PR body | 6 | no |
| **6** | Pre-merge check, handoff, re-verify PR state **last** | 10a, 10b, 10c | no |

⚠ **CITE THE STEP, NOT THE PHASE, from any doc outside this file.** Phases 1-6 exist only
as rows in the table above — they declare no headings — so `tools/stale_anchors.py`
correctly flags "post-branch phase 4" in another doc as a dead anchor, and a reader
following it finds no such section. That cost a peer session two rounds on #667. Inside
this file the phase names are the run order and stay; outside it, name `Step 7`.

Phases 0 and 3 run **regardless** of the phase-1 gate: MEMORY.md lives outside the repo,
Issues live on GitHub and the handoff is gitignored, so none of them ever costs CI.
Steps 8 (rebase) and 9 (output format) are situational and belong wherever they
are needed.

⚠ **Watch for a phase whose output depends on a fact a LATER phase creates.**
Phase 3 writes a MEMORY.md bullet naming `#NNN`, which does not exist until phase
4 — write the bullet with the number omitted and let phase 6's re-verify fill it
from the same `gh` query that rewrites the handoff. Do not reorder phase 3 after
phase 4 to "fix" this: MEMORY.md must be written even when no PR is ever opened.

**Phase 0 is not optional and it is not a summary of the rest.** A hand walk is
not the walk: twelve of these legs were copy-by-hand shell blocks in this file until
2026-08-19, which means they ran only when a session remembered to copy them, and
two of them had shipped broken. A green sweep is *not* a green branch — it covers
none of the judgement in phases 1–6.

**Do not "simplify" this into a blanket rule in either direction.** A blanket
"after" is what caused the defect; a blanket "before" is equally wrong, because
Steps 6/10a/10c cannot run until the PR exists.

**The measurement behind the split:** on #557 the walk ran after creation, found
a real `context/tools.md` omission, and the fix-push re-ran the entire suite via
`pull_request: synchronize` — ~3000 tests plus a 93s regression job, for one
paragraph. On #558 the walk ran first and the doc commit shipped in the initial
push: one CI run, not two. On #568 the old wording was followed again and cost
another redundant run.

**If `gh pr create` has already run**, do not skip the walk — run the whole thing
now and accept the extra CI run. A stale doc costs more than one CI cycle.

**The `PostToolUse` hook is a backstop, not the trigger.** It fires on
`gh pr create`, which is necessarily *after* — there is no hook event for "about
to open a PR", which is exactly why the ordering rule has to live in prose here
and in `AGENTS.md`. Two caveats worth knowing: the hook lives in
`.claude/settings.json`, tracked since 2026-08-19 so it now DOES survive a
reclone (it did not before), and it matches the
**whole command string**, so a `grep` or heredoc merely *containing*
`gh pr create` will fire it spuriously.

---

## Doc-surface configuration

The doc-surface list lives in `docs/agents/surfaces.toml`, not here. **When porting this skill
to another repo, that file is the only thing to write** — the workflow below is repo-agnostic.
`tools/post_branch_checks.py` and `tools/sanity_checks.py` read the same file, so the list the
skill describes and the list the checkers sweep cannot drift apart.

Two surfaces this skill walks are deliberately NOT in that file, because no checker opens them:
account-level `MEMORY.md` (Step 5, always updated) and the gitignored operator tooling
`docs/plans/daily_check.py` / `.claude/hooks/*` / `.claude/settings.json` (Step 4).

```yaml
# Files that, if changed, almost always require a doc walk:
behavior_signal_globs:
  - "buibui.py"
  - "cli/**/*.py"
  - "Makefile"
  - "deploy/**"                     # scripts + systemd units ARE operator-facing
                                    # behaviour. Added 2026-08-07j: #582 shipped a
                                    # backup script and two timers while this list
                                    # had no deploy entry at all, so the gate saw
                                    # no signal from the PR's largest change.
  - "docker-compose.yml"
  - ".github/workflows/**/*.yaml"   # NOT *.yml — every workflow here uses .yaml,
                                    # so the old .yml glob never once matched
  - "pyproject.toml"
  - "config/strategy_params.toml"
  - "config/*signal_watch*.toml"

# Files that almost never require a doc walk (internal-only refactor space):
behavior_skip_globs:
  - "analytics/**/_*.py"          # underscore-private package internals
  - "analytics/**/*.py"            # detector / signal / store internals (per-PR judgement)
  - "tests/**"
  - "**/*_test.py"
  - "poetry.lock"
  - "*.parquet"
  - "tests/fixtures/**"
```

The `behavior_signal_globs` and `behavior_skip_globs` are heuristics, not
absolute rules. A move that adds a new public symbol *is* user-facing even
under `analytics/**`. Always read the diff before deciding.

---

## Phase 0 body — the mechanical sweep

```bash
make post-branch-checks
```

Thirteen legs, all advisory (`--exit-zero`). Triage each hit; a false positive costs
a glance, a silent miss ships a doc that reads as complete.

| Leg | Asks |
| --- | --- |
| `queue-items` | Does this branch **close** a task the handoff still lists as to-do? |
| `handoff-symbols` | Does the handoff claim something about a symbol or file this branch touched? |
| `new-files` | Does every added non-Python operator file reach an enumerating doc? |
| `new-modules` | Does every added module reach `.claude/context/`? |
| `new-targets` | Is every **added** Make target documented? (`buibui-` is stripped — AGENTS.md documents the subcommands) |
| `amended-targets` | For a target whose recipe this branch **changed**, which docs enumerate it and need re-reading? Added 2026-09-06: `new-targets` matches an added `+target:` line only, so #746's `DB=` on an existing target read clean while `AGENTS.md` and `README.md` both went one override short. It reports the docs, never the diff — scoping it to `$(if …)` would scope to the symptom, and a changed default fails the same silent way |
| `negative-claims` | Does a doc assert the absence of something this branch just added? |
| `doc-indexes` | Are the generated `INDEX.md` files current? |
| `md-atx` | Did a wrapped `#123` become an accidental MD018 heading? |
| `memory-cap` | Is MEMORY.md over its size / bullet cap? |
| `handoff-size` | Does the handoff's line-count stamp match the file? (**owner-only** — see below) |
| `stale-anchors` | Does any `§N` / `Step N` citation point at an anchor that no longer exists — **repo and memory tree**? |
| `sensitive-terms` | Would a public flip expose a work identifier? Asks three questions — tracked tree, commit CONTENT, commit MESSAGES. A missing term list is a FINDING, never a SKIP. ⚠ **It does NOT read the PR title/body** — that is the fourth surface, screened separately below |

**What it deliberately does NOT cover:** whether a doc is *correct*, whether the
behaviour gate should pass, or whether a claim is true. Those are phases 1–6 —
and since ST88 the sweep says so itself, closing with the `Step N` bodies it
does not reach. That block states the gap where a session running the mechanical
half will actually see it, which prose here cannot: both parallel sessions on
2026-08-25 had this paragraph available and substituted anyway.

⚠ **`stale-anchors` is the leg with no substitute.** A section number is not a
symbol, so no symbol-keyed check can see this class; and on its first run here 4
of 7 hits sat in the memory tree, which no repo-scoped check can reach at all.

⚠ **Three legs READ the handoff — `queue-items`, `handoff-symbols` and
`handoff-size` — and the handoff has exactly ONE owning session.** Solo, that is
you and there is nothing to decide. In a **parallel run it is one session and one
only**, per the operator's one-owner-per-shared-gitignored-doc rule, and the other
session must not write the file at all. So **establish ownership BEFORE running
phase 0, not when a leg fires**: if you are not the owner, discharge all three by
**reporting the finding to the owner** and record that you did — do not edit, do not
prune, and do not treat `handoff-size` as a gate on your branch. It is advisory
(`--exit-zero`) and it is measuring a file you have no write claim on.

⚠ **In a WORKTREE the handoff is ABSENT, and since 2026-08-25 (ST75) `handoff-size`
SKIPS instead of firing** — `in_linked_worktree()` compares `git rev-parse --git-dir`
against `--git-common-dir`, and the skip message NAMES the worktree. It previously
answered "handoff is absent — rewrite it", which was right solo and actively wrong
here: it pointed a non-owning session at creating a second copy of a single-copy file,
the one outcome nobody wants.

⚠ **The skip is NARROW, and absence in a normal checkout is still a hard finding.**
A blanket skip would make absent-because-worktree and absent-because-lost render
identically in the OTHER direction — "a SKIP is not a PASS" arriving from the far
side. `queue-items` and `handoff-symbols` also skip on an absent handoff, but for a
different and correct reason: they have nothing to check *against*, whereas this leg
owns "does it exist at all". ⚠ **`sensitive-terms` still reports NOT CONFIGURED from
a worktree and that is deliberate, not an oversight** — it guards an irreversible
publish, where "did not run" must never read as "passed", so copy the term list in
rather than expecting a skip.

**Why reporting is the whole fix rather than a lesser one:** the handoff is
gitignored and single-copy, so it has no remote and no merge. Two sessions pruning
it concurrently do not conflict — the second write silently erases the first, and
`Edit`-only discipline does not help, because the losing edit was already applied
to a file the winner had read before it. The failure is invisible at the time and
unrecoverable afterwards.

---

## Step 1 — Behaviour gate: is this PR user-facing?

Before walking any docs, decide if the PR changes behaviour a user or
operator would notice. **If not, stop after MEMORY.md update — don't churn
docs for invisible changes.**

Read the PR's diff:

```bash
gh api repos/s10023/buibui-moon-trader-bot/pulls/<PR#> --jq '{title, body, base: .base.ref, head: .head.ref}'
gh api repos/s10023/buibui-moon-trader-bot/pulls/<PR#>/files --paginate --jq '.[].filename'
git diff main...<branch> -- .
git log main..<branch> --oneline
```

(If `<PR#>` is omitted, infer from the current branch with
`gh api "repos/s10023/buibui-moon-trader-bot/pulls?head=s10023:$(git branch --show-current)" --jq '.[0].number'`.)

Every `gh` call in this skill is REST (`gh api`), never `gh pr view --json` or
`gh run view --json`: those go through GraphQL, which a cloud session refuses with
a 403 (#894, same defect as #888).

User-facing signals — **walk the docs** if any are present:

- New CLI subcommand or flag (`buibui.py`, `cli/`)
- New Make target or changed default
- New TOML config key or changed default
- New environment variable
- Renamed or moved file referenced from docs
- New error class users will see (new exit code, new alert format)
- New external dependency or system requirement
- Behaviour change to an existing public command
- New long-running daemon or one-shot tool (docker-compose)
- **Anything on the notification-decision list below**
- **Any change to the doc surfaces themselves** — `AGENTS.md`, `CLAUDE.md`, the
  handoff, `MEMORY.md`, `.claude/context/*.md`. ⚠ **This gate reads FALSE on exactly
  the PR class where the walk pays most**, because it asks whether behaviour changed
  and the docs *are* the change. #629 shipped a 217-line handoff against
  `daily_check.py`'s enforced 200 and a header restating a different budget instead of
  pointing at the constant; the walk caught both and a hand read missed them.

### Notification surface — decide it, never default to it

**Telegram is an operator-facing output surface and belongs in this gate**, but it
was not in the surface list, so the decision was made by whoever happened to think
of it. Result, audited 2026-08-10: the executor could submit live orders silently,
the drawdown halt could latch and clear silently, and the daily check pushed
*nothing on a green day* — which made a dead timer indistinguishable from a healthy
one.

**Trigger — this PR needs an explicit notification decision if it adds or changes
any of:**

1. A **scheduled job** (timer/cron). Silence becomes ambiguous the moment nobody is
   watching a terminal.
2. An **irreversible or money-moving action** — order submitted, position closed,
   capital committed.
3. A **latching state transition** — a halt engaging or clearing, a gate flipping.
   The *transition* is the event, not the state.
4. A **failure path visible only in a log** the operator does not read.
5. A **periodic summary a human is meant to act on.**

**Record one of four verdicts, and never leave it implicit:**

| verdict | when |
| --- | --- |
| `always` | a human must act on every occurrence, or the channel needs a heartbeat |
| `on-change` | only transitions matter; steady state is noise |
| `on-failure-only` | the default `run-job.sh` behaviour — correct for jobs nobody reads when healthy |
| `never` | **must state why**, in one line |

**The generalisable rule, and the reason the daily check changed:** *a channel whose
only signal is failure is unfalsifiable.* You cannot tell "healthy" from "broken"
without a positive heartbeat, and the delivery path then gets exercised for the
first time on the day you most need it working. If a job is `on-failure-only`,
confirm something else proves it is alive.

**Volume is the counterweight** — `TELEGRAM_ALWAYS=1` is opt-in per job precisely
because the 15-minute signal-watch would otherwise send 96 messages a day. Cheap
test: multiply by the schedule before choosing `always`.

Skip signals — **stop here** (after MEMORY.md update) if the PR is purely:

- Internal refactor that preserves the public API surface (byte-identical
  re-export shim, registry/key order preserved, etc.)
- Bug fix with a regression test added and no behaviour change
- Dependency version bump with no API change
- Lint/format-only commit
- Test-only changes
- Comment/docstring edits inside source files (not in the doc surfaces)
- Regression-fixture refresh (`make regression-update`) with goldens unchanged

**Rule-claim exception — applies even when the gate above says skip.** If the
PR *adds or changes a rule* in any doc surface ("never run X", "always use
Y", "the gate is Z"), grep the other surfaces for instructions that violate
that rule before you stop. A rule is worth less than nothing while a sibling
doc still tells the reader to do the opposite — the reader now gets
contradictory instructions from the same repo.

This is not hypothetical. PR #539 added "never diagnose golden drift from a
bare `pytest tests/`" to CLAUDE.md. The gate correctly classified it as
doc-only and would have stopped there — while `README.md:1482` was still
instructing readers to run exactly that command. One targeted grep for the
new rule's subject caught it. Cost: one grep. Value: the PR's own claim
stops being contradicted by the file new contributors read first.

**Claim-falsification exception — the mirror image, and the one this skill
kept missing.** The rule above catches a doc that contradicts a rule the PR
*adds*. It does **not** catch a doc that asserts something the PR has just
made **false**. That is a different search: instead of grepping for the new
rule's subject, ask *"what did any doc previously claim about the thing I
just changed?"* — then verify each claim still holds.

Two instances, both caught only by luck:

- PR #542 split the Trivy scan so secrets gate the build. `README.md:1416`
  still asserted "`exit-code: '0'` means a finding never fails the build" —
  false the moment the PR pushed.
- PR #546 implemented H8's MinTRL leg. `.claude/context/analytics.md`
  described that same leg as an **H14-specific addition**, which it never
  was, and `CLAUDE.md` counted H8's verdict toward "conditioning axes are
  0-for-6" when that verdict came from a build whose AVOID branch could not
  fire.

Cheap procedure: for each behaviour the PR changes, grep the doc surfaces for
that behaviour's *name* **and** for the **verdict or guarantee** attached to
it (`exit-code`, `never fails`, "0-for-", "H14-specific", the audit's own
verdict string). A claim about a result is as perishable as a claim about a
flag — and research verdicts are the most expensive kind to leave stale,
because AGENTS.md's whole purpose there is to stop the next session
re-litigating settled work.

⚠ **The doc surfaces are not the whole corpus — grep the MEMORY TREE too when the claim is
about a SERIES.** Measured on #729: a branch appended an entry to `/ingest-charts`' cost
series and claimed that step was the first ever to go DOWN. The falsifying evidence — a full
6-panel batch measured 37.1K two weeks earlier — sat in `project_skill_fix_queue.md` in the
account-level memory tree, filed there as a defect instead of appended to the series. **No
mechanical leg could have caught it**: `queue-items` and `handoff-symbols` read the handoff,
every other leg is repo-scoped, and `stale-anchors` is the one leg reaching memory but it
checks anchors rather than data.

So when a branch appends to a series — costs, counts, timings, any running measurement — ask
what else measured the SAME QUANTITY and never reached it. **A series' COMPLETENESS is the
hypothesis, not just its values**, and a series that merely looks monotone is evidence of what
somebody remembered to write down. The conclusion on #729 survived and its stated reason did
not, which is the cheap outcome; the expensive one is a rule rewritten on a premise the corpus
had already falsified.

**The same goes for a filed DIAGNOSIS of a recurring failure**, which this wording's focus on
series does not reach. Before diagnosing anything that has failed before — a stable failure
count, a known-flaky gate, a host symptom — grep the memory tree for it. A stable-looking count
invites a fresh diagnosis on every run. Measured twice: 2026-09-21 re-derived `make test`'s four
Windows failures with `windows-host-migration.md` in the session's own index, and 2026-09-30
re-opened the paged-pool leak whose suspect driver was already filed in the handoff.

**When a branch fixes a CODE-PATTERN class, grep the skills' code fences for it too.** Readers
copy a `SKILL.md` snippet verbatim, so a pattern fixed in the tree and left in a fence comes
back. Measured on #792: `/ingest-video` held five bare `read_text()` snippets after the tree was
fixed, found only by an ad-hoc grep — the static gate reads `.py` files, never Markdown fences.

**Strong refactor signals** — these almost always trigger user-facing doc
edits because they change paths users / docs reference:

- A module listed in AGENTS.md's "Project Structure" was renamed, moved, or
  reduced to a re-export shim (the path users `import` from is now stale)
- The CLI subcommand surface changed (`buibui --help` differs)
- A new `make buibui-*` target lands

When in doubt, ask the user: *"This PR touches X. I see [signals]; want me
to walk the docs, or is this internal-only?"*

---

## Step 2 — Identify changed artifacts

From the diff, build a concrete list the doc walk will key off:

- Each new/renamed/deleted **file** (especially modules listed in AGENTS.md
  Project Structure)
- Each new **CLI flag/subcommand** in `buibui.py` / `cli/`
- Each new **Make target** (lines added like `^[a-z_-]+:` in `Makefile`)
- Each new **TOML config key** or changed default in `config/*.toml`
- Each module that became a **shim** (line count drops drastically and body
  is just `from X import …`) — the path users `import` from now points to
  thin re-exports rather than real code

Keep this list short and concrete — it's the basis for every doc diff.

---

## Step 3 — Walk each doc surface

For each surface in the config, do the following:

1. **Locate the relevant files.** Use `path` or `path_glob`. For `scope:
   any_referencing_changed_artifact`, grep the doc tree for the artifact
   name (script name, flag, Make target, module path).

2. **Read the doc.** Look for:
   - Outdated examples (old flag names, removed scripts)
   - Missing entries (new flag/script/target absent from the listing)
   - Broken file paths (post-rename, post-shim)
   - Stale defaults
   - Stale module-purpose descriptions ("module X holds Y" when Y has moved
     to the package next door)

3. **Decide if an edit is warranted.** Bias toward minimal, targeted edits.
   Don't rewrite docs that aren't affected. If a `README.md` doesn't mention
   the changed artifact at all and never did, leave it alone.

4. **Propose the edit.** Show the user a unified-diff-style proposal:

   ```diff
   # AGENTS.md (line 47)
   - - `data_store.py` — DB schema, upsert/query helpers, `confidence_ratings`, …
   + - `store/` — package: `schema.py`, `signals.py`, `backtest_runs.py`,
   +   `backtest_cache.py`, `confidence.py`, `combos.py`, `stats_cache.py`.
   +   `data_store.py` is a re-export shim for the 30+ external import sites.
   ```

   Wait for confirmation before writing.

5. **Apply via the `Edit` tool.** Never use `Write` to overwrite a doc —
   always targeted edits.

---

## Step 4 — Surface-specific checks

### AGENTS.md

- "Project Structure" section: every module listed should match its real
  current home. If a `*.py` file is now a shim, rename or annotate to
  point at the package that holds the real code.
- "Key Commands" / "CLI" sections: every subcommand should still resolve.

### CLAUDE.md

- "Agent Skills": skills added/removed since last sweep are reflected in the
  rules that section carries. There is deliberately NO skills table to diff.

### README.md

- CLI subcommand list matches `buibui --help`.
- Quickstart still works (commands referenced still exist).

### Makefile

- Every `buibui.py` subcommand has a `make buibui-<name>` target.
- Every public daemon has a `docker-up` / `docker-down` line.

### docker-compose.yml

- Long-running daemons → `restart: unless-stopped`.
- One-shot tools → `profiles: [tools]` so they don't auto-start.

### `.claude/context/*.md`

- Module references match the current package layout. These are the most
  refactor-sensitive docs.

- **`any_referencing_changed_artifact` is BLIND TO OMISSION — this is how
  `analytics.md` rotted for months.** That scope greps the doc tree for the
  changed artifact's name. When a PR *adds* a package, grepping for `xsmom`
  finds zero hits, so the sweep concludes "no change needed" — when the
  correct conclusion is the exact opposite: the doc is missing a module.
  A scope that can only detect drift in things the doc already mentions can
  never detect the module it has never heard of. Same defect shape as
  markdownlint's `!.claude` glob: the check reported green because it could
  not see the files.

  **So for context docs, run a presence check, not only a mention grep — and it is the
  `packages` leg of `make post-branch-checks`** (Step 0), which asks whether every
  top-level package, tracked or newly added, is named in `.claude/context/`. It reads
  untracked files one by one (`--untracked-files=all`), because plain `git status`
  folds a new directory into one `?? dir/` line the presence legs had to skip — so a
  brand-new package was invisible to them until its first commit. Measured 2026-10-08
  with a probe package, which read clean before that flag and reports now.

  **The `-w` is load-bearing — do not drop it back to a bare substring match.**
  Without it this check has the exact blind spot it was written to fix, in the
  other direction: grepping `state_audit` returns hits in three files and reads
  as "documented", but every hit is inside **`premium_state_audit`** — the
  actual module `analytics/state_audit.py` has zero coverage and is skipped.
  **A false-positive presence check is worse than none, because it reports
  covered.** `-w` works here for a non-obvious reason: `_` is a
  word-constituent character, so `state_audit` has no word boundary inside
  `premium_state_audit` yet still matches `analytics/state_audit.py`, where `/`
  and `.` are boundaries. Proven by injection against the real docs tree —
  `dicator_condition`, `tate_audit`, `enue_premium`, `udit_guard` all report
  COVERED under a bare `grep` and MISSING under `-w`, 4 for 4.

  **If you re-verify this, pick a probe that can still fail.** `state_audit`
  itself no longer discriminates now that it IS documented — a discrimination
  test needs a fixture capable of failing.

- **FOURTH INSTANCE — a new FILE inside a directory the doc already covers.** The
  presence check above iterates top-level *packages* (`for d in */`), so it passes
  the moment `deploy/` is mentioned anywhere — while every individual script inside
  it goes unchecked. The mention-grep is no help either: grepping `backup-offsite`
  finds zero hits and reads as "no change needed", when the correct reading is
  "the doc has never heard of this file". **Both checks report green on a directory
  whose contents have changed.**

  Measured on #582: it added `deploy/backup-offsite.sh` plus two unit files, and
  `execution.md` — whose `deploy/` entry enumerates *every other script by name* —
  was not flagged by anything. It was caught only because a separate claim-
  falsification grep happened to hit the same paragraph.

  **So diff the directory, not just the package list — the `new-files` and `new-modules`
  legs do exactly that.** They check every file this branch adds (committed, staged and
  untracked) against the enumerating docs and `.claude/context/` respectively. Each added
  file lands in one of THREE outcomes, defined in `coverage()` in
  `tools/post_branch_checks.py`: the full path is named (credited), no probe name appears
  at all (`UNDOCUMENTED`), or only the basename appears while another tracked file shares
  it (`AMBIGUOUS basename (verify by hand)`). The third outcome lived in this skill's shell
  block and not in the tool until 2026-10-08, so the sweep credited a basename collision
  silently — the #643 shape below, on the surface Step 0 says to run FIRST.

  **SIXTH INSTANCE, and it is the `-w` trap one level up: a basename that collides
  ACROSS PACKAGES.** The single-`basename` form above this fix greps `telegram.py`,
  which matches the long-documented `utils/telegram.py`, and therefore reported the
  newly added **`card/telegram.py` as COVERED** while `signals.md` -- whose `card/`
  entry enumerates every other module in the package by name -- had never heard of it.
  Measured 2026-08-18 on #643, and caught only because someone read the doc.

  **`-w` cannot fix this and neither can any tightening of the pattern**, because the
  string genuinely appears: the ambiguity is real, so the only honest output is to say
  so — hence the three outcomes above. Verified by counterfactual on that branch, not by
  assertion: the two-outcome form printed nothing for `card/telegram.py`, the
  three-outcome form printed `AMBIGUOUS`; `TestThreeOutcomes` pins the same pair.

  Noise ceiling, measured on the same tree: **47 of 906 tracked files share a basename**,
  concentrated in the per-sleeve pattern (`report.py`, `replay.py`, `config.py`, 6-8 each),
  so a PR adding a whole sleeve draws a few AMBIGUOUS lines. That is the intended trade --
  the same asymmetry the bullets above state, since a false positive costs a glance and a
  silent miss ships a doc that enumerates six of seven modules and reads as complete.

  **The two `docs/` trees are excluded because a STRONGER gate already covers them,
  not to quiet the output.** `make docs-index` generates their `INDEX.md` and
  `tests/test_docs_index.py` **fails CI** until it is current — CI-enforced, where
  this grep is advisory. Without the exclusion every added audit and spec
  false-positives, which is a whole predictable class rather than the occasional
  dismissable hit the bullets above accept (measured 2026-08-17 on #638). ⚠ **Do not
  extend this exclusion to a tree that has no such gate** — that would re-create the
  omission blindness this section exists to fix. The test is "is it indexed by a
  CI-gated generator", never "is it noisy".

  **⚠ The single-source form FALSE-GREENS, and it did on 2026-08-14d.**
  `--diff-filter=A main...HEAD` cannot see a file that is not committed yet, and
  Steps 1–5b run before the commit — so the check reported clean on three genuinely
  undocumented files. **A probe that cannot fail at the moment it runs is worse than
  no probe**, because it launders the gap as verified.

  **One blind spot survives even the three-source form: gitignored additions.**
  `--exclude-standard` drops them, and dropping the flag floods the output with
  `analytics.db` and `.venv` — so files under `.claude/hooks/` and `docs/plans/` need
  a deliberate look. That is exactly what the 08-14d miss was.

  Same `-w` rule and same over-reporting tradeoff as above: not every added file
  belongs in a context doc (tests never do), so treat a hit as a candidate to
  dismiss in seconds, not a defect. The asymmetry is the point — a false positive
  costs a glance, a silent miss ships a doc that enumerates six of seven scripts
  and reads as complete.

- **Makefile check, second half.** Step 4's Makefile bullet says "every `buibui.py`
  subcommand has a `make buibui-<name>` target" — but the repo also wraps
  **scripts** (`buibui-backup` → `deploy/backup-analytics.sh`), and #582 added a
  sibling script with no target because the stated rule only covers subcommands.
  Also check: every `deploy/*.sh` an operator runs by hand should have a wrapper,
  or none of them should. **And `tools/*.py`, which is where most hand-run scripts
  actually live** (~30 of them; the audit tools nearly all carry a
  `make buibui-*-audit` wrapper). The convention there is genuinely mixed —
  `docs_index.py` is wrapped, `combo_health.py` is not — so this bullet should
  prompt a judgement, not assert a rule. Found 2026-08-12d while promoting
  `tools/decay_review.py` (#607): the convention had to be inferred by grepping
  siblings, which is exactly the "a person plus luck" non-rule this step replaces.

- **SIXTH INSTANCE of the omission blind spot: a GENERATED index that nothing reads
  back.** Every check above asks whether the artifact is *correct*; none asks whether
  anything *acts on* it. ⚠ **This is the inverse of the usual failure — the artifact
  was right and unread, so a staleness gate cannot see it.** `docs/audits/INDEX.md`
  was generated, CI-enforced and always current while it carried the only BUILD
  verdict in 47 audits, unowned for seven weeks. **A generated index that nothing
  consumes is a surface, not a check: ask what reads it, and whether an actionable
  row can go unowned.** `docs/superpowers/specs/INDEX.md` has the same shape — whether
  it has the same gap is unverified.

- **AGENTS.md must not re-absorb this content.** The 2026-08-04 split left
  the always-loaded tier holding a package index plus verdicts, and the context
  docs holding the detail. `analytics.md` went stale in the first place *because*
  the always-loaded copy was a duplicate that was auto-loaded and therefore visibly
  wrong, so it got maintained while the context file silently diverged. Two
  sources of truth, one of them invisible, always rots the invisible one. If
  a PR adds module detail to AGENTS.md's Project Structure, move it.

### `.claude/skills/*/SKILL.md`

- A skill that names a tool, flag, path or constant drifts exactly like
  CLAUDE.md does. Grep the skill tree for the changed artifact's name — a
  renamed flag or a moved module leaves a skill quietly instructing the next
  session to run something that no longer exists.

- **THIRD INSTANCE of the omission blind spot: a rule a SIBLING skill lacks.**
  The two cases above catch drift in what a doc already says, and a package a
  doc has never heard of. Neither catches **two skills that write the same
  artifact where only one carries the rule governing it.** Measured: #559 added
  a `direction`-enum rule to `/ingest-video`; `/ingest-x` writes the *same*
  Stream C rows to the *same* `pundit-calls.jsonl` from the *same* item schema
  and had **no such rule at all**. Nothing flagged it. It was found by hand-
  grepping the skill tree, which is luck plus a person — the same non-rule that
  `/ingest-x`'s own `is_retrospective` guard exists to replace.

  **So when a PR adds or changes a RULE in one skill, check its siblings.**
  Identify the artifact the rule governs, find every skill that writes it, and
  report which ones lack the rule:

  ```bash
  # ART = the artifact the changed rule governs; LOCUS = where it is enforced
  ART=pundit-calls.jsonl
  LOCUS=pundit_direction
  grep -rls "$ART" .claude/skills/*/SKILL.md | while read -r s; do
    grep -qw "$LOCUS" "$s" || echo "SIBLING WRITES $ART BUT LACKS THE RULE: $s"
  done
  ```

  **Grep for the enforcement locus, NOT the field name** — this is the whole
  trick, and getting it wrong makes the check vacuous. Searching `-w direction`
  returns **4 hits** in `main`'s `/ingest-x` (it names the field in its schema,
  its digest table, and its Stream C line) and would have reported green on the
  exact gap it was meant to catch. A skill that carries a rule cites *where the
  rule is enforced*; one that merely handles the field does not. Use `-w`:
  substring matching silently conflates `pundit_direction` with a longer name,
  the same trap that made skill-fix 7p's own filed fix wrong.

  ⚠ **A PROMPT-SIDE rule has NO enforcement locus, so the recipe degrades to the
  artifact grep plus a READ — and that is the common case, not the edge one.** The
  trick above works because `direction` is enforced in code at
  `analytics/pundit_direction.py`, giving a string only a rule-carrying skill would
  cite. A rule that lives entirely in the prompt ("a level named as the condition for
  entry IS the entry") has no such string: every candidate spelling is either prose the
  sibling would phrase differently or a field name it mentions anyway, which is the
  vacuous case this bullet already warns about. So when the changed rule is prompt-side,
  use `grep -rls "$ART" .claude/skills/*/SKILL.md` to get the candidate list and then
  READ each hit for the rule's *substance* — the grep can only narrow the set, never
  decide it. Measured 2026-09-02: `/ingest-x` carried neither of two Stream C entry/stop
  rules `/ingest-video` had, and it was found by reading, with no locus available to
  grep for.

  **A hit is a candidate, not a finding — it over-reports by design.** The grep
  cannot tell "writes this artifact" from "mentions this artifact", so confirm
  with one read before proposing anything. Run against this tree it returns two
  known-benign hits, and **neither is a bug to fix**: `/ingest-feed` names the
  file only to say it does *not* write it (the writes live inside
  `/ingest-video`'s flow), and this skill names it as the worked example above.
  Tightening the grep to exclude them would re-create the omission blindness
  this bullet exists to fix — a check that can only see what it already expects.
  Prefer two false positives you dismiss in ten seconds over one silent miss.

  Verified by counterfactual, not by assertion: replayed against `main`, the
  recipe emits `WOULD HAVE FLAGGED: ingest-x/SKILL.md`.
- **`make lint-md` DOES cover this tree — just run it.** No special recipe, no
  `cd /tmp`, no explicit `--config`. `.markdownlint-cli2.jsonc` excludes
  `.claude` wholesale and then **re-includes** the two committed subtrees:

  ```jsonc
  "!.claude",
  ".claude/skills/*/SKILL.md",
  ".claude/context/*.md",
  "!.claude/skills/humanizer",
  ```

  The `humanizer` re-exclusion is deliberate — `.claude/skills/` also holds
  symlinks to installed external skills, which are not ours to lint and would
  make `make lint-md` permanently red on a machine that has them. `.gitignore`
  encodes the same intent.

  **Corrected 2026-08-04h (PR #550).** This bullet previously asserted the exact
  opposite — that `.claude` was excluded outright, that `make lint-md` skipped
  the whole tree, and that you had to run `markdownlint-cli2` from outside the
  repo with an explicit `--config`. That was true before the re-includes landed
  and false afterwards, and it survived because nobody re-tested the claim. It
  had two costs: sessions ran an obsolete workaround, and — worse — believed
  skill files were **ungated** when every commit touching one is in fact linted
  by both `make lint-md` and the `markdownlint-cli2` pre-commit hook.

  Verified empirically rather than by reading the config: injecting `MD012` +
  `MD038` + `MD040` into a `SKILL.md` and running `npx markdownlint-cli2` from
  the repo root reported all three (`Linting: 183 files`), then reverted clean.
  **If you ever doubt whether a path is gated, inject a violation and look —
  reading the glob list is how this bullet got it wrong for months.**

### Gitignored operator tooling — `docs/plans/daily_check.py`, `.claude/hooks/*`

**FIFTH INSTANCE of the omission blind spot, and the first that is invisible to every
check above.** The three-source added-file check drops gitignored paths
(`--exclude-standard`), the package presence check iterates tracked directories, and the
mention-grep only sees what a doc already names. So this class is unreachable by any
mechanical probe in this skill — which makes it the one that needs a prompt.

**Ask one question: does this PR add behaviour that nothing else monitors?** If yes,
`daily_check.py` probably owes it a line. Its own docstring carries a four-part inclusion
rule — rots silently, named consequence, one cheap field, one action clears it — so apply
that rule rather than inventing a threshold. Measured on #631: the PR shipped an off-site
backup leg whose failure mode is literal silence (`run-job.sh` Telegrams a non-zero exit,
and a timer that never fires has no exit code), and nothing in this skill prompted the
check that caught it.

**Then say so in the PR body, explicitly.** These files ship no code: an edit here reaches
neither a reclone, nor CI, nor a fork. A PR that quietly relies on a gitignored monitor
reads as covered and is not. One line — *"⚠ `daily_check.py` gained X but is gitignored, so
it is not in this diff"* — is the whole fix, and it is also what tells the sibling repo that
it has to write its own.

**Hooks are not this class.** `.claude/` is tracked, and every hook carries a suite in CI's
dependency-free `markdownlint` job (every `.claude/hooks/test_*.py`, each its own step in
`lint.yaml`), so a hook change IS in the diff and IS gated. What remains in
this class is `docs/plans/daily_check.py`, which is genuinely gitignored.

---

## Step 5 — MEMORY.md update (always)

Regardless of the behaviour gate, **always update MEMORY.md's "Current
State"** at the end of every session. This is project policy (CLAUDE.md
"Session Memory Protocol"):

- **Rewrite the "Latest" bullet** to today's date + a one-line summary of what
  changed. "Latest" is **at most 2 lines**; every other Current State bullet is
  exactly 1 line.
- **Current State holds at most 6 bullets.** If yours would be the 7th, first
  roll the oldest bullet **verbatim** into
  `memory/project_session_log_<month>.md`. Prune by MOVING, never by deleting —
  session logs have no size limit; that is what they are for.
- Convert any relative dates ("Thursday") to absolute (`2026-05-01`)
- **Do NOT record open questions or pending decisions here.** File each as an Issue
  labelled `question` (Step 5b), and if MEMORY.md still carries such a line, move it to an
  Issue and delete it. MEMORY.md keeps the last-session line plus judgement and verdicts;
  open work lives in Issues only (CLAUDE.md, operator ruling 2026-10-01, #865).

**There is no "Previous session" bullet, and there has not been one for
months.** This step used to say *"Set 'Last session' … move the previous to
'Previous session'"*, which described a protocol MEMORY.md does not implement,
so every run silently worked around it by hand — filed as skill-fix `7h` after
it bit three consecutive sessions. **A step that is wrong every run and correct
never is worse than no step.** The authority is CLAUDE.md's "Session Memory
Protocol"; if the two ever disagree again, CLAUDE.md wins and this text is the
one to fix.

This step runs even when the behaviour gate skipped the user-facing doc
walk, because MEMORY.md tracks **what changed in the session**, not just
behaviour-visible changes.

---

## Step 5b — Issue reconcile (always, and it is NOT covered by Step 5)

Planning lives in **GitHub Issues** on this repo since 2026-09-29; the memory SoT
(`project_todo_master.md`) is a pointer stub carrying rulings and an old-id → Issue map, and
takes no status.

**Two questions. First: does this branch close, change, or contradict an open Issue?**
If it closes one, put `Closes #<n>` in the PR body (Step 6) so the merge closes it, and add a
one-line verdict comment. If it only changes or contradicts one, comment on the Issue **now,
in this same session**. A branch that surfaces a NEW to-do, future plan, skill fix, open
question or pending decision files a new Issue — never a memory row, never a handoff list.
Questions and decisions waiting on the operator carry the `question` label. Issues publish with the repo on a visibility flip: redact account figures and screen any
composed body with `make post-branch-text FILE=<path>`.

Cheap way to find the Issue — the old SoT id (e.g. `ST139`) survives in each migrated body:

```bash
GH_TOKEN=$(gh auth token --user s10023) gh issue list -R s10023/buibui-moon-trader-bot \
  --state open --search "$TOPIC in:title,body" --json number,title
```

⚠ **An empty search is not "nothing to reconcile"** when `gh` failed — check the exit code
before trusting a blank answer.

### The second question: did this branch land a `docs/research/` doc that RECOMMENDS work?

If yes, **file its own GitHub Issue naming the filename, in this same session.** A research doc
that recommends a book, a repo, an ingest or a build and files no row has no owner, and
nothing anywhere will ever ask for one.

`tools/docs_index.py` indexes `docs/audits/` and `docs/superpowers/specs/` **only**
(`AUDIT_DIR` / `SPEC_DIR`, `:46-47`), so a `docs/research/` file sits outside the generated
INDEX, outside `TestEveryNewAuditExposesItsVerdict`, and outside `daily_check.py`'s tier-2
`audit verdicts` join. It is a directory no tool reads. Measured 2026-08-17e:
`2026-08-14-trading-canon-audit.md` recommended **three books and a repo** and sat unowned for
three days with every gate green — the reason ST33 was invisible was structural, not judgement.

⚠ **This is prose enforcement, and it is chosen with its weakness in view.** The alternative
was to add `docs/research/` to the docs-index surface, which would be machine-enforced but
would import the verdict-prose rule and the frozen-set machinery into a directory
`AGENTS.md` deliberately exempts — a real cost on every future research doc, to catch a case
that arrives a few times a year. Operator ruling 2026-09-06: take the cheap narrow rule. So
the honest caveat is that ST34 exists *because* prose enforcement already failed here once;
if a second research doc lands unowned, that is the trigger to revisit the machine-enforced
option, not a reason to restate this paragraph.

**Why this step exists, and why it is separate from Step 5.** The session-memory wiring
(CLAUDE.md "Session Memory Protocol", this skill's Step 5, `/sanity-check`, `/backtest-findings`) touches
**MEMORY.md**; nothing else closes planning items. Under the memory SoT that gap let rows drift
for months — the reason planning moved to Issues, where `Closes #n` closes on merge.

**The failure it prevents is misinformation, not clutter.** Found 2026-08-09: N8 still sat as
an **OPEN defect with ~1,900 characters of live diagnosis two days after PR #580 closed it**,
beside a row quoting a `_SCAN_WINDOW=200` that #580 had changed to 600 for 15m. A session
picking up work from it would have re-opened a solved problem and coded against a stale
constant. **An open Issue that is already done is worse than a missing one**, because it reads
as current evidence — verify it against the code or comment that it is unverified, dated.

---

## Step 6 — Update the PR body

Once edits are approved and applied (or the gate decided no edits were
needed), append a "Documentation updates" section to the PR body so
reviewers see the doc reasoning:

```markdown
## Documentation updates

- `AGENTS.md`: rewrote Project Structure entry for `analytics/store/` after
  data_store.py reduced to a re-export shim
- `README.md`: no change needed (no CLI surface change)
- `MEMORY.md`: Current State updated with strat-2 summary
```

Use the three-step fetch → append → push sequence:

```bash
# 1. Fetch the current body
gh api repos/s10023/buibui-moon-trader-bot/pulls/<PR#> --jq .body > /tmp/pr_body.md

# 2. Append the new section (Edit tool, or heredoc)
cat >> /tmp/pr_body.md <<'EOF'

## Documentation updates

- `<file>`: <what changed>
EOF

# 3. Push the new body
gh pr edit <PR#> --body-file /tmp/pr_body.md
```

If the original PR body already has a "Documentation updates" section, open
`/tmp/pr_body.md` in the Edit tool and update it in place — don't append a
duplicate.

---

## Step 7 — Commit and push

Commit doc edits as a single follow-up commit on the PR branch:

```bash
git add <files>
git commit -m "docs: sync docs with PR behavior changes"
git push
```

**MEMORY.md is never committed.** It lives outside the repo under
`~/.claude-personal/...`, so it is not part of any project commit — save it
via the `Edit` tool only, and never `git add` it. If the MEMORY.md update is
the only thing this step produced, there is simply nothing to commit here;
say so and move on.

**Push rules:**

- Default: `git push` (no force).
- If a rebase happened, use `--force-with-lease` and **only** with explicit
  user approval. Never `--force`.
- Never push to `main` from this skill. Ever.

### Then run the clean-clone pre-flight — it REPLACES `make test`

```bash
make preflight
```

**Order is load-bearing: this runs AFTER the commit above, never before.** A
clone only ever sees *committed* state, so running it earlier — in phase 0's
sweep, say — tests stale HEAD and reports green while the doc commits this
phase just produced go untested. The script refuses outright on a dirty tree
rather than reporting that green.

**It IS this branch's one full-suite run** — measured 2026-08-20 on 4205 tests:
**300.1s against `make test`'s 294.9s, +1.8%**, so the hermetic form costs five
seconds. ⚠ **Running `make test` first and then this is the same suite twice for
nothing**, and it happened on 2026-08-26 to a session reading the old "supersedes
the final `make test`" wording, which reads as an exception to a gate rather than
as a replacement for it.

**On a host where it exits 3 (`INFRA`)** — the Windows laptop, where the clone cannot
`poetry install` numpy — run `make test` as the substitute, name it in the PR body, and
state that CI is the only clean-clone verifier for this branch.

⚠ **Do not read "replacement" as "stop running `make test` while you work."** A
clone cannot see uncommitted code — the same property that makes this correct at
Step 7 makes it useless mid-branch, and it refuses on a dirty tree rather than
pretending otherwise. `make test` stays the tool until the work is committed. It clones to a temp dir with
`--no-hardlinks` (plain `--local` fails `Invalid cross-device link` onto `/tmp`
here), runs `poetry install --no-root` in the clone (**6.69s** against a warm
cache), then the same pytest invocation `make test` uses.

**Why it exists here rather than as another CI job: CI already IS this gate** —
a clean checkout, which is why it caught #666. The gap is TIMING. On a private
repo, detection after a push costs a metered cycle, a red PR and a visibility
flip just to read the failure. This is the last moment that is still free.

⚠ **Read the banner, not the exit code** — make collapses any recipe failure to
its own exit 2. `REFUSED` means the tree was dirty and nothing ran; `INFRA`
means the clone or install died; only `FAILED` is a real finding. ⚠ **On the Windows
host, compare a `FAILED` set against the host baseline tracked in #869 before
diagnosing** — an exact match is that baseline, any other failure is a real finding.
Drop this sentence when #869 closes.

⚠ **Two things it does NOT cover**, so do not read a pass as a clean bill: an
*absolute* default (`$HOME/...`) survives a clone untouched — `EXTERNAL_LEDGERS`
in `deploy/backup-analytics.sh` is that shape — and it only sees code some test
actually exercises, never an untested CLI branch.

⚠ **Scope: run it when the diff can REACH the suite, and prove that with a check rather than a
judgement.** The positive test is two greps — does the diff contain Python, and does any test
read a changed path (`grep -rl <changed-path> tests/`)? If both answer no, the clone re-runs
the whole suite to reproduce `main`'s own result; **say in the PR body which gate you ran
instead and why**, naming the two greps. Measured on #730, a lockfile line plus a
`.github/dependabot.yml` entry: zero Python, no test reading either file, 4762 tests that
could not be affected.

⚠ **A `grep -rl` hit inside a COMMENT or DOCSTRING still counts as a RUN.** The check cannot
tell a citation from a read, and it is only allowed to be wrong in the expensive direction.
Measured on #736: a doc-only amend to `.claude/context/signals.md` hit
`tests/test_post_branch_checks.py`, where the path appears in a docstring naming that file as
a known false positive while the test itself runs on a synthetic fixture — so a 5-minute
clean-clone run was spent on a diff that provably could not reach the suite. ⛔ **Do not fix
that by narrowing the grep**: the spec-reconcile counter cannot tell a citation from a
disclaimer either, and every narrowing there re-created the blindness it was meant to remove.

⛔ **The default stays RUN, and the discriminator must be that positive check — never "this
looks harmless".** That judgement is exactly what #586 and #666 defeated, both of which were
diffs nobody expected to reach anything. This scope line exists because a gate with no stated
scope makes the correct call look like a deviation, which is how it gets dropped later on a
diff that DID need it.

### Then decide the visibility flip — BEFORE `gh pr create`

**Gate it on `sensitive-terms` first.** Phase 0's sweep carries the leg; read it before
flipping, because the flip publishes the whole history and nothing downstream can take that
back. `NOT CONFIGURED` means the gitignored term list is missing (reclone, fresh machine, **or a
WORKTREE** — a tracked-files-only checkout never receives a gitignored file, so this leg fires
on every worktree run) — restore or copy it in rather than flipping past it. A hit on the branch's own commits is
a STOP: scrubbing in a follow-up commit does not unexpose the blob.

**Then screen the PR title and body, which that leg cannot see.** They are neither the tree
nor a commit, so the sweep reports `clean` on a body naming every term — and a PR body is the
only INDEXABLE one of the four surfaces, served and crawled on its own:

```bash
make post-branch-text FILE=docs/plans/scratch/pr-<branch>.md  # the body /pr-summary wrote
printf '%s' "$TITLE" | make post-branch-text FILE=-             # the title, via stdin
```

It GATES rather than advising. ⚠ Through `make` the exit code is make's own **2**, never the
tool's 1 — read the banner. An unreadable `FILE` is also 2, deliberately: it must not render
as a clean one-check run. Findings print line numbers and a masked term, never the matching
line. Run it on the FINAL text; an edited body does not unpublish the posted one.

Pushing costs no CI; the meter starts at `gh pr create`. So this is the last free
moment, and it is the one decision in the whole skill that must be put to the
user every single time.

⚠ **THE FLIP IS OPERATOR-RUN — you cannot perform it.** `gh repo edit --visibility` is
blocked by the permission classifier, and it is blocked in BOTH directions, so the flip back
in phase 6 is the operator's too. Do not discover this mid-chain: on #669 it surfaced with
the branch already pushed and the PR body already written, costing a round trip. **Order:
finish the PR body FIRST, then hand over the exact command and WAIT for confirmation, then
`gh pr create`** — an unconfirmed flip plus a created PR is a billing-red for nothing.

```bash
# hand this to the operator; they run it with a leading `!` in the prompt
GH_TOKEN=$(gh auth token --user s10023) gh repo edit s10023/buibui-moon-trader-bot \
  --visibility public --accept-visibility-change-consequences
```

**Confirm it landed before creating the PR** — `gh repo view … --json visibility` is a read
and is not blocked. Never assume the flip happened because you printed the command.

⚠ **Confirm the flip with the user on every occasion.** AGENTS.md > CI quota
makes the **mechanics** standing authorisation and the **timing** not, because
the public window publishes this repo's whole history for its duration and only
the operator knows whether now is a good moment. Ask the unsettled half; never
re-ask the settled one.

- **A docs-only diff skips the flip** — the path-filtered checks execute zero
  steps on a `.md`-only change. Phase 1 has already read the diff, so this is
  already known by the time you get here.
- **Phase 6 closes the other half of the pair** — the flip BACK, gated on
  `make wait-ci-main`. Do not treat the flip as done when the PR opens.
- ⚠ **`make wait-ci-main` settles on ONE workflow; the flip affects ALL of them.** It gates
  on the `CI` workflow's job-count floor, so a *different* workflow starting after CI settles
  is invisible to it — the same vacuous-check shape as the chained-job defect, one layer up.
  Observed on #669: `Dependency Graph` began at 05:55:45Z, after CI had settled, and was
  `in_progress` at the moment of the flip back. It did not bite there — but **do NOT file this
  as latent.** On #670 the late workflow was `security-scan` (Trivy), which consumes Actions
  minutes, so flipping mid-run kills it and reds main for billing. The risk is *which*
  workflow starts late, never whether the listing was honest.
  ⚠ **And one call does NOT close it — the rule is check → flip → RE-VERIFY.** A listing
  cannot see a workflow that does not yet EXIST: on #670 and again on #672 the pre-flip
  `gh run list` read clean on every workflow, the operator flipped, and `Dependency Graph`
  was created on the merge SHA *after* the check. That is the same vacuous-check shape at a
  THIRD NESTED LAYER (a count of layers, not of sightings) — a chained job does not exist
  until its dependency ends · the waiter watches one workflow and cannot see a sibling · a
  listing of all workflows cannot see one not yet created. ⚠ **It has now recurred on the flip-back
  for #674, #675 and #678 — five sightings**, the last created at 07:07:09Z on `a306410` and
  still `queued` at the moment of the flip; every one was caught by the post-flip re-verify and
  by nothing else — a pre-flip check cannot see a run that does not yet exist, so **the
  re-verify is the ONLY step that catches this class**, never a belt-and-braces extra.
  **A check is only ever true about the scope it looked at, at the moment it looked**, so
  re-run it after the operator confirms the flip:

  ```bash
  GH_TOKEN=$(gh auth token --user s10023) gh api \
    "repos/s10023/buibui-moon-trader-bot/actions/runs?head_sha=<merge-sha>" \
    --jq '.workflow_runs[] | [.name, .status, .conclusion] | @tsv'
  ```

  Never a branch listing (`gh run list --branch main`, REST `?branch=main`) for this: it has returned months-old runs at arbitrary
  moments (ST147; REST form 2026-09-30/10-01), so it can read clean on the wrong commits. Read an empty answer as
  UNVERIFIED, not clean.

---

## Step 8 — Rebase handling (only when needed)

Sometimes a relevant doc lives on `main` but not on the PR branch (e.g. it
landed in a sibling PR). The diff at Step 3 won't surface it. If suspected:

1. Check if the doc exists on main: `git ls-tree main -- <doc-path>`
2. If yes and missing on the PR branch, ask the user:
   *"Doc X is on main but not this branch. Rebase onto main so we can
   update it here, or skip and let the next PR handle it?"*
3. Rebase only on explicit OK:

   ```bash
   git fetch origin main
   git rebase origin/main
   ```

4. Resolve conflicts the user's way, not by force.

---

## Step 9 — Output format

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

---

## Step 10 — Post-PR handoff

After the doc walk closes, the user usually wants two more things before
moving on: a quick pre-merge readiness check, and a self-contained prompt
they can paste into a fresh conversation when this branch is done. Bake
both in here so the user doesn't have to ask each time.

### 10a — Pre-merge readiness check

Prefer the tool over a hand-rolled waiter:

```bash
make wait-ci PR=<n>     # resolves the SHA, prints steps=EXECUTED/DECLARED per job
```

⚠ **`wait_ci.py`'s exit codes do not survive `make`** — GNU make collapses any
recipe failure to its own exit 2, so read the printed banner (3 = Actions
allowance exhausted, `steps=0`, a **billing** failure never a code one; 1 = real
failure; 4 = green but the step counts were unreadable, which is not a pass).

⚠ **Read the EXECUTED half, not the declared one.** A paths-filtered job declares
its full step list on every diff and skips the body, so `steps=5/14` is a docs diff
correctly skipping the heavy leg — while the bare `14` this banner used to print
read as the opposite (ST50(f), measured on #670). Billing is unchanged: an
exhausted allowance declares nothing.

Then run a short status sweep and report any blockers in one line each:

```bash
git status --short                                      # working tree clean?
git log @{u}..HEAD --oneline 2>/dev/null || true        # unpushed commits?
GH_TOKEN=$(gh auth token --user s10023) \
gh api repos/s10023/buibui-moon-trader-bot/pulls/<PR#> --jq '{mergeable, mergeable_state, sha: .head.sha}'
GH_TOKEN=$(gh auth token --user s10023) \
gh api repos/s10023/buibui-moon-trader-bot/pulls/<PR#>/reviews --jq '[.[].state] | last'
GH_TOKEN=$(gh auth token --user s10023) \
gh api repos/s10023/buibui-moon-trader-bot/commits/<sha>/check-runs --paginate \
  --jq '.check_runs[] | {name, conclusion, started_at, completed_at}'
```

**Prefix every `gh` call inline like that, never `export … ;`** — the allowlist matches
a command's first word, so the export form prompts every time while the inline form's
command word is `gh`. It cannot be dropped either: the *active* gh account here is the
work one, so a bare call authenticates as the wrong user. ⚠ **`gh auth switch` is
something to ASK the operator for, never to run yourself.**

Flag, do not fix:

- Uncommitted changes in the working tree
- Local commits not pushed to the PR branch
- `mergeable: false` or `mergeable_state: dirty` (`null` means GitHub is still
  computing it — re-query)
- Failing required checks in `check-runs`
- A latest review state of `CHANGES_REQUESTED`

**⚠ Before reporting ANY failing check, compute its runtime from
`started_at`/`completed_at` — that is why they are in the `--jq` above.**

**The discriminator is the STEP LIST, not a duration.** When the GitHub Actions
allowance is exhausted, every job fails in 2–5 seconds with **zero steps executed**,
which renders identically to a real test failure. Pull the steps and look:

```bash
gh api repos/s10023/buibui-moon-trader-bot/actions/runs/<id>/jobs --jq '.jobs[] | {name, steps: [.steps[] | {name, conclusion}]}'
```

`steps: []` on a **FAILED** job is billing. A populated step list is a real run,
whatever the clock says. ⚠ **`steps: []` on a SKIPPED job is neither** — a failed
`needs:` dependency or a job-level `if:` — and reading it as billing points at a public
flip to debug someone else's failure. ST125, 2026-09-08: `wait_ci.py` did exactly that
on main `b9ce0ef`, where `Regression tests` was skipped because `lint-typecheck-test`
had failed on a timed-out test.

**⚠ Do NOT use a flat "under ~10 seconds never ran" rule — it is wrong in both
directions, and this skill carried it until 2026-08-11.** Trivy died at *exactly* 10s
against a real 22s baseline, so the constant cleared a check that genuinely failed;
and a fast *green* can be legitimate path-filtering (#592: 8s against a 3m45s norm).
The dangerous half is the green one — a fast red gets investigated, a fast green gets
merged. **When you do compare durations, compare a check against ITS OWN normal
runtime, never against a shared constant**: `lint-typecheck-test` runs ~4m18s here,
Regression ~2m41s, Trivy ~22s. Three checks, three different "too fast".

Report it as such — *"4 checks failed in 2–3s each: GHA billing, not code"* — and point at
the standing workaround (flip the repo public for the open-PR window, private again on
merge; confirm with the operator every time). **Never open a debugging session on that
shape.** Observed on #589 and #590; on #590 it rendered as four `FAILURE`s while
`make lint-py` / `typecheck` / `test` / `lint-md` were all green locally.

Output one line per item. If everything is green, say so explicitly:
`pre-merge: clean — ready when you are.`

### 10b — Fresh-conversation handoff prompt

**Precondition — do you OWN the handoff?** This step assumes the running session is
the one that maintains `docs/plans/next-conversation-prompt.md`, which is true solo
and false in a parallel run. If another session owns it, **skip 10b** and instead hand
that session what it needs to fold in: this branch's PR number and state, what it
closes, and any handoff row it makes stale. Say in your final report that 10b was
skipped for ownership and to whom the content went — a skipped step and a forgotten
one look identical next session, which is the whole reason the handoff exists.

Otherwise: offer (don't auto-write) to draft a self-contained prompt the user can
paste into the next conversation. Same shape as `/pr-summary` —
**file-only output, never inline**.

This is the STANDING handoff, and mattpocock's `/handoff` does not replace it: that skill writes a
one-off portable doc to `%TEMP%` for forking a side task, and the operator invokes it.

If the user accepts, write to **`docs/plans/next-conversation-prompt.md`** —
gitignored, but inside the repo and therefore durable. **Not `/tmp`:** the
user deletes conversations, and a handoff that evaporates on reboot defeats
the point. Keep updating that same file rather than starting a new one; it is
a standing document whose whole value is being current, and keeping it so is
a final step of every task, not only of this skill.

**The handoff carries SEQUENCING, never open work** (operator ruling of
2026-10-01, Issue #865). It holds an ordered list of Issue numbers to work, host state, and
standing hazards. A to-do, skill fix, open question, pending decision, "operator
also wants" or "offered, not ruled" item is an Issue (Step 5b) — if you find one
written here, file it and replace it with its Issue number. Lists that lived only
in this file were never filed and went unseen by every Issue query.

**Update it with targeted `Edit`s. NEVER `Write` the whole file.** Its back
half carries standing content — Standing findings and hazards — that the
template below does not reproduce, so a wholesale
overwrite silently destroys it. This is not theoretical: it is why the
"Standing blocks" subsection exists two paragraphs down, and it was
re-confirmed on 2026-08-06j and again on 2026-08-07 when a 449-line prune ran
entirely as `Edit`s and every standing block survived.

**PRUNE it on every task, not only when it gets big** (operator instruction,
2026-08-07). Merged PRs, completed tasks and resolved incidents do NOT belong
here once they land — **move every ✅ item out of the START HERE block into the
month's session log as you write it; the daily check's `handoff done items` line
goes amber while one remains (#905)** — the file is read in full at the start of every session,
so its cost is paid on every conversation. Left alone it reached **1622
lines**, roughly a quarter of it narration of work already merged and recorded
elsewhere. The test is not "old vs new", it is **"does this change what the
next session DOES"**:

- **Delete outright:** merged-PR narration, resolved incidents, superseded
  dated readings (keep the newest only), "kept for provenance" blocks, and any
  closed-task write-up whose verdict already lives in AGENTS.md, a
  `docs/audits/` verdict, or a memory file.
- **Condense to one line + pointer:** a closed task whose VERDICT still binds
  ("do not rebuild X", "do not re-run Y"). The verdict survives; the story of
  reaching it does not.
- **Keep:** the daily operator check, Standing findings and hazards, host state,
  the ordered Issue list.
- **Move to an Issue, then delete:** any skill-fix queue, open question, pending
  decision or task list still written here.

**Read a closed section before deleting it — open items hide inside sections
headed "DONE".** Measured on 2026-08-07: an uncoded `xs_gate_verdict` item sat
inside a block titled "DONE 2026-08-06d. Do NOT redo", and a live card-validity
finding sat inside a closed `/card` task. Deleting on the header alone loses
both. Prune by MOVING to the durable home, never by deleting outright.

**Two structural rules, operator instruction 2026-08-08d. They live here rather than
behind a pointer because a guard rail behind a pointer is not a guard rail:**

- **At most ONE "Just shipped" section, superseded on each merge — never accumulated.**
  Do not add a section per PR. Before writing the new one, move the outgoing one's
  binding verdict to its durable home (AGENTS.md, a `docs/audits/` verdict, or a memory
  file) and delete the rest. The pile had reached **six** such sections before the
  operator asked.
- **Resolve an "Open work" row by DELETING it, never by striking it through.** A struck
  row still costs a read and still reads as state. Move any verdict that still binds
  first, then delete the row. Four struck rows had accumulated by the same date.

**Also check the file's own budget while you are in it** — `daily_check.py` carries a
handoff-size line, and the fix for an over-budget file is to **move the next standing
block to a memory topic file**, not to trim prose. The fat is structural, not narrative.

Structure:

```markdown
# Next conversation — <one-line context>

## DO THIS FIRST — daily operator check (unprompted, every session)

<Carry this block forward VERBATIM, refreshing only the "State at <date>"
line. See "Standing blocks" below — it is not optional and not per-PR.>

## READ FIRST — PR state (snapshot, re-verify before acting)

| PR | Branch | Contents | State at write time |
| --- | --- | --- | --- |
| #<num> | `<branch>` | <one line> | OPEN / MERGED |

**This table is a snapshot, not live state.** First move:
`gh api repos/s10023/buibui-moon-trader-bot/pulls/<num> --jq '{state, merged}'`. If merged, sync main, delete the branch,
and start on a task below — do not re-litigate merged work.

## Just shipped
- PR #<num>: <title> — <one-line outcome / verdict / lift>
- Key finding: <the surprising or load-bearing result, if any>

## State of the world
<2–4 bullets, drawn from MEMORY.md "Current State" + the PR body —
what's live, what's in soft mode, what's still pending. Absolute dates.>

## Reference
- Memory: the tree `tools/memory_dir.py` resolves — `$(PYTHONPATH=. poetry run python tools/memory_dir.py)/MEMORY.md`. ⚠ Write the RESOLVED path into the handoff, never the template string: the config root and the project slug both vary by host.
- <Other docs / tools / branches the next session will need>

## Order of work (Issue numbers — the Issue holds the detail)

1. #<n> — <title>. <one line: why it is first, or what it is blocked on>
2. #<n> — <title>
3. #<n> — <title>
```

### Standing blocks — EDIT IN PLACE, never regenerate

**Do not `Write` this file. Apply targeted `Edit`s to the dated sections** — the
state-at line, the PR table, "just shipped", the task list — and append new
findings. That is the same rule Step 5 states for every other doc, and it is
stated separately here because the template above is *not* the whole file:
anything not in it that a `Write` touches is silently deleted, and this file
carries standing operational content that belongs to the project, not to this
PR.

**Reading the template as a regenerate instruction is the failure mode.** Taken
that way it says: rebuild from a template that omits most of the file, then
manually re-add the rest — which is a step one tired session will skip. Editing
in place makes verbatim preservation the *default* instead of a manual chore.

Blocks that outlive any one PR — refresh only their dated lines:

- **The daily operator check.** `poetry run python docs/plans/daily_check.py`,
  run unprompted every session, reds relayed verbatim. Keep it the FIRST
  section, above the PR table. **Do NOT reinstate the old hand-run commands
  here** (`make buibui-xsmom-daily`, `CATCH_UP=1 make buibui-signal-watch`) —
  both are owned by systemd timers since #579, and hand-running
  `make buibui-signal-watch` is the *looping* form, which races the timer for
  `signal_state.json` and duplicates Telegram. Force a run with
  `systemctl --user start <name>.service`.
- **Standing hazards and host state** — these outlive any one PR. ⚠ The
  skill-fix queue and open questions USED to be standing blocks here; since
  2026-10-01 (#865) they are Issues (`question` label for the latter), so do
  not re-create either block.
- **The lessons corpus** — as of 2026-08-07 this no longer lives in the handoff.
  It grew append-only to 347 lines that reduced to two principles restated
  sixteen times. It now lives in memory, read on trigger:
  `reference_shell_and_tooling_gotchas.md`,
  `feedback_filed_artifact_is_a_hypothesis.md`,
  `project_xsmom_causality_test_vacuous.md`. **File a new finding into whichever
  of those it belongs to, not into the handoff** — and if it is a restatement of
  one already there, add it as a one-line example rather than a new bullet.

This subsection exists because on 2026-08-03 the operator had to ask "why didn't
you remind me to run the xsmom daily?". The reminder was real but lived only in
a dense memory bullet, and once added to the handoff it would have been erased
by the very next run of this skill.

Source the content from:

1. **MEMORY.md "Current State"** — the top bullets are usually the right
   candidates. (There is no "Next focus" section; this said so until
   2026-08-07.) Convert any relative dates to absolute.
2. **This PR's findings** — if the PR closed an option or unblocked one,
   say so plainly so the next session doesn't re-ask.
3. **Open Issues** — the SessionStart digest's p1/p2 queue, plus any
   `question` Issue this PR answered or made actionable. Name Issues by number;
   never restate their bodies here.

Keep it tight: 1–3 Issue numbers in order, not a backlog dump. The goal is a
prompt that costs zero context to bring a fresh session up to speed.

Print only the path + a one-line description. Do **not** echo the
contents.

### 10c — Re-verify PR state as the LAST action (never skip)

This skill writes the handoff *before* the merge, so its most prominent
instruction is the first thing to go stale. On 2026-08-03 all three PRs
(#524, #525, #526) merged within minutes of their handoff being written, and
PR #530 merged the same way — each left the next session with a wrong
opening move. The handoff is the one artifact that survives a session
delete, so a stale first line there is the most expensive kind of stale.

Immediately before you report done — after **every** other step, including
any commit and push — re-query every PR named in the handoff, not just the
one this run created:

```bash
gh api repos/s10023/buibui-moon-trader-bot/pulls/<PR#> --jq '"\(.state) \(.merged_at)"'
```

Then rewrite the state table in place to match. If a PR merged in the
meantime, update the "first move" line too: the next session should be told
to start on a task, not to merge something already merged. If it merged and
the local branch still exists, say so — the branch delete is
`gh pr view` -gated by `[[verify-merge-before-branch-delete]]` and is the
natural first action for the next session.

One API call per PR. That is the whole cost of the difference between a
handoff that opens the next session productively and one that sends it down
a dead path.

---

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

---

## When the skill should NOT run

- The PR is closed or merged (too late — open a follow-up `docs:` PR).
- The user said "skip docs" explicitly in the prompt.
- The PR is from Dependabot or another bot.
- The branch has no diff yet (PR was created against the wrong base).

In these cases, say so and stop.

---

## PR Summary template

Write the PR summary through `/pr-summary`; do not compose it from scratch. It takes the body
shape from mattpocock's `pr` skill (Summary visual, Evidence, Merge Danger) and adds this repo's
title rules, the honest-tick gate checklist under Evidence, `Closes #n`, and the Claude Code
footer.
