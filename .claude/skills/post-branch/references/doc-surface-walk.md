# Steps 2–4 — the artifact list and the doc-surface walk

**When:** Read before Step 2. The `.claude/context/` and `.claude/skills/` sections are the ones that bite: each records a way a green check missed a real omission.

Reference for `.claude/skills/post-branch/SKILL.md`, which holds the run order and the condensed rules. This file holds the full text, including the dated incidents behind each rule. The passages below are carried over verbatim from the pre-split SKILL.md (#884), so where one says "this file", "this skill", "above" or "below" about the phase table or a step, it means SKILL.md.

## Step 2 — the artifact list

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

## Step 3 — the per-surface procedure

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

## Step 4 — surface-specific checks

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
  Steps 1–6 run before the commit — so the check reported clean on three genuinely
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
