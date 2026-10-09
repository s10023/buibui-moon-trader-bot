# Step 1 — the behaviour gate, in full

**When:** Read before Step 1 on any branch that is not obviously test-only, and always when the branch adds a rule, changes a verdict, or touches a scheduled job.

Reference for `.claude/skills/post-branch/SKILL.md`, which holds the run order and the condensed rules. This file holds the full text, including the dated incidents behind each rule. The passages below are carried over verbatim from the pre-split SKILL.md (#884), so where one says "this file", "this skill", "above" or "below" about the phase table or a step, it means SKILL.md.

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

## Reading the diff

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

## Skip signals and the two exceptions

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

## Strong refactor signals

**Strong refactor signals** — these almost always trigger user-facing doc
edits because they change paths users / docs reference:

- A module listed in AGENTS.md's "Project Structure" was renamed, moved, or
  reduced to a re-export shim (the path users `import` from is now stale)
- The CLI subcommand surface changed (`buibui --help` differs)
- A new `make buibui-*` target lands

When in doubt, ask the user: *"This PR touches X. I see [signals]; want me
to walk the docs, or is this internal-only?"*
