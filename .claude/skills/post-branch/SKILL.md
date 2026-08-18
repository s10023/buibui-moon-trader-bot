---
name: post-branch
description: >
  Post-branch docs sweep + handoff — diff the branch's behaviour changes against
  the doc surfaces (CLAUDE.md, README.md, MEMORY.md, Makefile, docker-compose.yml,
  .claude/context/*.md, .claude/skills/*/SKILL.md)
  and propose targeted edits where they've drifted, then run a pre-merge
  readiness check and offer a fresh-conversation handoff prompt. SPLIT around
  `gh pr create`: run Steps 1-5 and 7 BEFORE creating the PR so doc fixes ship in
  the initial push, then Steps 6 and 10a/10c after it exists. Invoke it on every
  branch — if `gh pr create` has already run, start it immediately, before
  reporting the PR URL back to the user. Skip for pure refactors, bug fixes
  covered by tests, dependency bumps, and lint-only commits — the behaviour gate
  (Step 1) decides. Confirm every edit before writing; never force-push without
  explicit OK. Also triggers on the user saying "/post-branch", "wrap up the
  branch", "docs check", "pre-merge check", or "next conversation prompt".
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

## When each step runs — the skill SPLITS around `gh pr create`

This file used to say "It runs **after** the PR exists." That was wrong, and it
cost a full redundant CI run every time it was followed (see below).

| When | Steps | Why they belong there |
| --- | --- | --- |
| **BEFORE `gh pr create`** | 1–5, then 7 | They are **commit-producing**. Walking the docs first means the fixes land in the branch's initial push, so the PR opens complete. |
| **AFTER the PR exists** | 6, then 10a/10c | Step 6 edits the PR body and 10a/10c report + hand off. They need a PR number and produce **no commits**. |
| Either | 8, 9, 10b | Rebase (8) and output formatting (9) are situational; 10b writes a gitignored file, so it is free either way. |

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
and in `CLAUDE.md`. Two caveats worth knowing: the hook lives in gitignored
`.claude/settings.json` so it does not survive a reclone, and it matches the
**whole command string**, so a `grep` or heredoc merely *containing*
`gh pr create` will fire it spuriously.

---

## Doc-surface configuration

Each entry is a class of doc that might need updating when behaviour
changes. **When porting this skill to another repo, edit only this block —
the rest of the workflow stays the same.**

```yaml
surfaces:
  - id: claude_md
    path: CLAUDE.md
    purpose: Authoritative project context for Claude Code (project structure, key commands, code style, agent skills)

  - id: readme
    path: README.md
    purpose: User-facing project overview (CLI subcommands, install, quickstart)

  - id: memory_md
    path: ~/.claude-personal/projects/-home-kng-repo-buibui-moon-trader-bot/memory/MEMORY.md
    purpose: Cross-session memory; "Current State" section MUST be updated every session
    always_update: true   # see Step 5

  - id: makefile
    path: Makefile
    purpose: Make targets — every `buibui.py` subcommand should have a `buibui-*` wrapper
    scope: any_referencing_changed_artifact

  - id: docker_compose
    path: docker-compose.yml
    purpose: Long-running services (daemons → restart:unless-stopped) and one-shot tools (profiles:[tools])
    scope: any_referencing_changed_artifact

  - id: context_docs
    path_glob: ".claude/context/*.md"
    purpose: Long-form module references (analytics, research-sleeves, tools, signals, web, execution)
    scope: any_referencing_changed_artifact + new_module_presence   # see below

  - id: skill_docs
    path_glob: ".claude/skills/*/SKILL.md"
    purpose: Workflow instructions that name tools, flags and file paths — they drift exactly like CLAUDE.md does
    scope: any_referencing_changed_artifact
    lint: manual   # see below

  - id: gitignored_ops
    paths: ["docs/plans/daily_check.py", ".claude/hooks/*", ".claude/settings.json"]
    purpose: Operator-facing surfaces that ship NO code — monitoring, guards, harness config
    scope: any_behaviour_this_pr_adds_that_nothing_else_monitors   # see Step 4

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

## Step 1 — Behaviour gate: is this PR user-facing?

Before walking any docs, decide if the PR changes behaviour a user or
operator would notice. **If not, stop after MEMORY.md update — don't churn
docs for invisible changes.**

Read the PR's diff:

```bash
gh pr view <PR#> --json title,body,baseRefName,headRefName,files
git diff main...<branch> -- .
git log main..<branch> --oneline
```

(If `<PR#>` is omitted, infer from the current branch with
`gh pr view --json number`.)

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
because CLAUDE.md's whole purpose there is to stop the next session
re-litigating settled work.

**Strong refactor signals** — these almost always trigger user-facing doc
edits because they change paths users / docs reference:

- A module listed in CLAUDE.md's "Project Structure" was renamed, moved, or
  reduced to a re-export shim (the path users `import` from is now stale)
- The CLI subcommand surface changed (`buibui --help` differs)
- A new `make buibui-*` target lands

When in doubt, ask the user: *"This PR touches X. I see [signals]; want me
to walk the docs, or is this internal-only?"*

---

## Step 2 — Identify changed artifacts

From the diff, build a concrete list the doc walk will key off:

- Each new/renamed/deleted **file** (especially modules listed in CLAUDE.md
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
   # CLAUDE.md (line 47)
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

### CLAUDE.md

- "Project Structure" section: every module listed should match its real
  current home. If a `*.py` file is now a shim, rename or annotate to
  point at the package that holds the real code.
- "Key Commands" / "CLI" sections: every subcommand should still resolve.
- "Agent Skills" table: skills added/removed since last sweep are listed.

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

  **So for context docs, run a presence check, not only a mention grep.** For
  every package directory added or renamed in this PR, confirm the matching
  context doc gained an entry. Cheap version:

  ```bash
  # every top-level package vs. what the context docs actually document
  for d in */; do d=${d%/}
    case $d in tests|docs|config|scripts|__pycache__|.*) continue;; esac
    grep -rqsw "$d" .claude/context/ || echo "UNDOCUMENTED: $d"; done
  ```

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

  **So diff the directory, not just the package list.** For every directory a
  context doc enumerates by filename, check that files this PR added are named:

  ```bash
  # files added by this branch, in directories the context docs enumerate.
  # THREE sources, not one: committed adds, staged adds, still-untracked files --
  # this walk runs PRE-COMMIT by design, so `main...HEAD` alone sees nothing.
  { git diff --name-only --diff-filter=A main...HEAD
    git diff --name-only --diff-filter=A --cached
    git ls-files --others --exclude-standard; } | sort -u \
    | grep -Ev '^docs/(audits|superpowers/specs)/' | while read -r f; do
    b=$(basename "$f")
    grep -rqsw "$f" .claude/context/ && continue          # PATH named: documented
    if ! grep -rqsw "$b" .claude/context/; then echo "UNDOCUMENTED FILE: $f"; continue; fi
    # basename hit -- but is the doc talking about THIS file? Another file sharing
    # the basename makes the hit unreliable, so hand it to a human rather than
    # silently crediting it.
    if [ -n "$(git ls-files "*/$b" "$b" | grep -vx "$f")" ]; then
      echo "AMBIGUOUS basename (verify by hand): $f"
    fi
  done
  ```

  **SIXTH INSTANCE, and it is the `-w` trap one level up: a basename that collides
  ACROSS PACKAGES.** The single-`basename` form above this fix greps `telegram.py`,
  which matches the long-documented `utils/telegram.py`, and therefore reported the
  newly added **`card/telegram.py` as COVERED** while `signals.md` -- whose `card/`
  entry enumerates every other module in the package by name -- had never heard of it.
  Measured 2026-08-18 on #643, and caught only because someone read the doc.

  **`-w` cannot fix this and neither can any tightening of the pattern**, because the
  string genuinely appears: the ambiguity is real, so the only honest output is to say
  so. Hence three outcomes rather than two -- path named (credit it), no hit at all
  (report it), basename hit with a colliding sibling (**ask a human**). Verified by
  counterfactual on that branch, not by assertion: the old form printed nothing for
  `card/telegram.py`, the new form printed `AMBIGUOUS`.

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

- **CLAUDE.md must not re-absorb this content.** The 2026-08-04 split left
  CLAUDE.md holding a package index plus verdicts, and the context docs
  holding the detail. `analytics.md` went stale in the first place *because*
  CLAUDE.md carried a duplicate that was auto-loaded and therefore visibly
  wrong, so it got maintained while the context file silently diverged. Two
  sources of truth, one of them invisible, always rots the invisible one. If
  a PR adds module detail to CLAUDE.md's Project Structure, move it.

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

Hooks are the same class: gitignored, no CI, and `test_context_guard.py` covers only
`context-guard` — both BLOCKING guards are unpinned.

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
- Update / remove "Open questions / pending decisions" as appropriate

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

## Step 5b — SoT reconcile (always, and it is NOT covered by Step 5)

**Ask one question: does this branch close, change, or contradict a row in the
SoT** (`~/.claude-personal/projects/-home-kng-repo-buibui-moon-trader-bot/memory/project_todo_master.md`)?
If yes, reconcile it **now, in this same session** — move the row to **Closed**
with a one-line verdict, per that file's own rule ("Move items there with a
one-line verdict; never delete"). Like MEMORY.md it lives outside the repo, so
it is **never committed** and costs no CI.

Cheap way to find the row — search for the item ID and the PR number:

```bash
SOT=~/.claude-personal/projects/-home-kng-repo-buibui-moon-trader-bot/memory/project_todo_master.md
grep -n 'N8\|ST15\|#580' "$SOT"     # the IDs and PRs this branch touched
grep -n 'OPEN\|not yet\|unfixed' "$SOT" | grep -i "$TOPIC"
```

**Why this step exists, and why it is separate from Step 5.** Nothing auto-updates
the SoT — the session-memory wiring (CLAUDE.md "Session Memory Protocol", this
skill's Step 5, `/sanity-check`, `/backtest-findings`) all touches **MEMORY.md**,
not the SoT. **The SoT predicted this failure in its own "How to use this file"
section** — *"Consider adding an SoT-reconcile step to `post-branch` if drift
recurs."* Drift recurred repeatedly and nobody acted on the trigger, so the step
is now here.

**The failure it prevents is misinformation, not clutter.** Found 2026-08-09: N8
still sat in "Ongoing watches" as an **OPEN defect with ~1,900 characters of live
diagnosis two days after PR #580 closed it**, and the N6 row beside it still
pointed at "the OPEN N8 row" and still quoted a `_SCAN_WINDOW=200` that #580 had
changed to 600 for 15m. A session picking up work from the SoT would have
re-opened a solved problem and coded against a stale constant. Same shape as
ST13 (listed open twice after being done) and H14's "3 deferred minors".

**A stale row is worse than a missing one**, because it reads as current
evidence. If you are unsure whether a row is still true, do not leave it —
either verify it against the code or mark it unverified with today's date.

---

## Step 6 — Update the PR body

Once edits are approved and applied (or the gate decided no edits were
needed), append a "Documentation updates" section to the PR body so
reviewers see the doc reasoning:

```markdown
## Documentation updates

- `CLAUDE.md`: rewrote Project Structure entry for `analytics/store/` after
  data_store.py reduced to a re-export shim
- `README.md`: no change needed (no CLI surface change)
- `MEMORY.md`: Current State updated with strat-2 summary
```

Use the three-step fetch → append → push sequence:

```bash
# 1. Fetch the current body
gh pr view <PR#> --json body --jq .body > /tmp/pr_body.md

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

CLAUDE.md          — updated: <what> | no change needed: <reason>
README.md          — updated: <what> | no change needed: <reason>
MEMORY.md          — updated: Current State + <other>  (never committed)
SoT reconcile      — <row> moved to Closed | no SoT row affected  (never committed)
Makefile           — no change needed: no new CLI commands
docker-compose.yml — no change needed: no new processes
.claude/context/*  — updated: analytics.md (store/ paths) | no change needed
.claude/skills/*   — updated: <skill> | no change needed: <reason>
gitignored ops     — daily_check.py line added (NOT in the diff) | no change needed: <reason>
PR summary         — written to /tmp/pr-<branch>.md   (slashes flattened to -)
PR body            — appended "Documentation updates" section
pre-merge          — clean | <blocker> (see Step 10a)
handoff prompt     — written to docs/plans/next-conversation-prompt.md | declined
PR state re-check  — #<num>: <OPEN | MERGED>, handoff table rewritten to match
```

**The PR-summary path flattens `/` to `-`.** Every branch here is `docs/…`,
`feat/…`, `fix/…` or `chore/…`, so a literal `/tmp/pr-<branch>.md` names a
directory that does not exist and the write fails. `pr-summary/SKILL.md` owns
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

Run a short status sweep and report any blockers in one line each:

```bash
git status --short                                      # working tree clean?
git log @{u}..HEAD --oneline 2>/dev/null || true        # unpushed commits?
GH_TOKEN=$(gh auth token --user s10023) \
gh pr view <PR#> --json mergeable,mergeStateStatus,reviewDecision,statusCheckRollup \
  --jq '{mergeable,mergeStateStatus,reviewDecision,
         checks: [.statusCheckRollup[] | {name, conclusion, startedAt, completedAt}]}'
```

**Prefix every `gh` call inline like that, never `export … ;`** — the allowlist matches
a command's first word, so the export form prompts every time while the inline form's
command word is `gh`. It cannot be dropped either: the *active* gh account here is the
work one, so a bare call authenticates as the wrong user. ⚠ **`gh auth switch` is
something to ASK the operator for, never to run yourself.**

Flag, do not fix:

- Uncommitted changes in the working tree
- Local commits not pushed to the PR branch
- `mergeable: CONFLICTING` or `mergeStateStatus: DIRTY`
- Failing required checks in `statusCheckRollup`
- `reviewDecision: CHANGES_REQUESTED`

**⚠ Before reporting ANY failing check, compute its runtime from
`startedAt`/`completedAt` — that is why they are in the `--jq` above.**

**The discriminator is the STEP LIST, not a duration.** When the GitHub Actions
allowance is exhausted, every job fails in 2–5 seconds with **zero steps executed**,
which renders identically to a real test failure. Pull the steps and look:

```bash
gh run view <id> --json jobs --jq '.jobs[] | {name, steps: [.steps[] | {name, conclusion}]}'
```

`steps: []` is billing. A populated step list is a real run, whatever the clock says.

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

Offer (don't auto-write) to draft a self-contained prompt the user can
paste into the next conversation. Same shape as `/pr-summary` —
**file-only output, never inline**.

If the user accepts, write to **`docs/plans/next-conversation-prompt.md`** —
gitignored, but inside the repo and therefore durable. **Not `/tmp`:** the
user deletes conversations, and a handoff that evaporates on reboot defeats
the point. Keep updating that same file rather than starting a new one; it is
a standing document whose whole value is being current, and keeping it so is
a final step of every task, not only of this skill.

**Update it with targeted `Edit`s. NEVER `Write` the whole file.** Its back
half carries standing content — Standing findings, the skill-fix queue, open
questions — that the template below does not reproduce, so a wholesale
overwrite silently destroys it. This is not theoretical: it is why the
"Standing blocks" subsection exists two paragraphs down, and it was
re-confirmed on 2026-08-06j and again on 2026-08-07 when a 449-line prune ran
entirely as `Edit`s and every standing block survived.

**PRUNE it on every task, not only when it gets big** (operator instruction,
2026-08-07). Merged PRs, completed tasks and resolved incidents do NOT belong
here once they land — the file is read in full at the start of every session,
so its cost is paid on every conversation. Left alone it reached **1622
lines**, roughly a quarter of it narration of work already merged and recorded
elsewhere. The test is not "old vs new", it is **"does this change what the
next session DOES"**:

- **Delete outright:** merged-PR narration, resolved incidents, superseded
  dated readings (keep the newest only), "kept for provenance" blocks, and any
  closed-task write-up whose verdict already lives in CLAUDE.md, a
  `docs/audits/` verdict, or a memory file.
- **Condense to one line + pointer:** a closed task whose VERDICT still binds
  ("do not rebuild X", "do not re-run Y"). The verdict survives; the story of
  reaching it does not.
- **Keep:** the daily operator check, Standing findings, the skill-fix queue,
  open questions.

**Read a closed section before deleting it — open items hide inside sections
headed "DONE".** Measured on 2026-08-07: an uncoded `xs_gate_verdict` item sat
inside a block titled "DONE 2026-08-06d. Do NOT redo", and a live card-validity
finding sat inside a closed `/card` task. Deleting on the header alone loses
both. Prune by MOVING to the durable home, never by deleting outright.

**Two structural rules, operator instruction 2026-08-08d. They live here rather than
behind a pointer because a guard rail behind a pointer is not a guard rail:**

- **At most ONE "Just shipped" section, superseded on each merge — never accumulated.**
  Do not add a section per PR. Before writing the new one, move the outgoing one's
  binding verdict to its durable home (CLAUDE.md, a `docs/audits/` verdict, or a memory
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
`gh pr view <num> --json state`. If MERGED, sync main, delete the branch,
and start on a task below — do not re-litigate merged work.

## Just shipped
- PR #<num>: <title> — <one-line outcome / verdict / lift>
- Key finding: <the surprising or load-bearing result, if any>

## State of the world
<2–4 bullets, drawn from MEMORY.md "Current State" + the PR body —
what's live, what's in soft mode, what's still pending. Absolute dates.>

## Reference
- Memory: `~/.claude-personal/projects/<project-slug>/memory/MEMORY.md`
- <Other docs / tools / branches the next session will need>

## Suggested next tasks (pick one, or work in order)

### Task 1 — <name>
<2–4 sentences: what, why, where to start (file paths). Include the
"cheapest move" or "recommended endgame" framing if there's a clear
ranking.>

### Task 2 — <name>
<…>

### Task 3 — <name>
<…>
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
- **Skill-fix queue** and **open questions** — these outlive any one PR.
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
3. **Open questions / pending decisions** — pull anything that becomes
   immediately actionable now that this PR shipped.

Keep it tight: 1–3 task suggestions, not a backlog dump. The goal is a
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
gh pr view <PR#> --json state,mergedAt --jq '"\(.state) \(.mergedAt)"'
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

- **Confirm every edit.** This skill is a proposer, not an applier. The
  user always gets a chance to say no.
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

The PR summary itself follows the template in
`.claude/skills/pr-summary/SKILL.md` exactly — read that skill before
writing. Do not compose from scratch or skip sections. The template
requires: PR Title, Background, Summary, How it works, Params/Config,
Test plan (CI items pre-ticked), Stats, and the Claude Code footer.
