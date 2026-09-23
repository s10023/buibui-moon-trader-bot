# Claude Code

@AGENTS.md

`AGENTS.md` holds this repo's instructions and is imported above. This file keeps only what is
specific to Claude Code's harness — its tools, its model tiers, its hooks, its skills and its
memory tree.

⚠ **`@AGENTS.md` is a LIVE IMPORT, not a citation. Deleting that line — or fencing it in
backticks, or moving it inside a code block — silently unloads ~830 lines of repo
instructions.** There is no error and nothing looks different, so the session goes on working
against harness defaults it cannot see it lost: every verdict, footgun and gate in `AGENTS.md`
simply stops being in context. Nothing in CI reads this file, so no gate catches it either.
If the path must change, change it in place and re-verify the import still resolves.

**Token efficiency.** Skills are dormant until invoked. Use the context-mode `ctx_*` tools
for any command or output over ~20 lines. `/compact` at logical boundaries rather than
waiting for autocompaction. Delegate heavy reads to a subagent when the saved main-context
clutter outweighs the startup cost.

**Model delegation.** The main thread is orchestrator and tech lead — design, judgement,
review and routing stay here. Delegate bulk mechanical work down by tier: **sonnet** for
high-volume execution (vision extraction, file sweeps, boilerplate, test triage), **haiku**
for trivial lookups, **opus** subagents as a quota escape valve for long *parallel*
research. Every subagent brief carries goal + success metric + rubric inline, with no SoT or
memory re-reads. Verify subagent and background work directly (`ps`, `journalctl`,
`git status`) — self-reports can be stale.

**Shell hygiene is a HOOK too, because the prose was in context and lost twice in one
session.** `.claude/hooks/guard-shell-hygiene.py` (advisory, `PreToolUse` on `Bash` for rules 1-5 and on
`Edit|Write|NotebookEdit|MultiEdit` for rule 6, said ONCE PER RULE PER SESSION so it cannot train
you to skip it) flags six habits that cost you a
verified result: a **hand-rolled waiter** (`until … pgrep`) for work the harness already
re-invokes you on — its `pgrep` guard names a CLASS, so the next run of the same kind
re-arms a fired waiter and it prints a STALE file as the fresh result — a **gate whose exit
status is SWALLOWED** — by a pipe into `tail`/`head`, or equally by a trailing
`; echo "EXIT=$?"` or `; tail -8 f`, since a `;`-sequence exits with its LAST segment's
status — turning a red run green. ⚠ **The rule's own remediation string was an
instance of this until 2026-09-22, and a negative test pinned that exact command as
correct** —
and a **DUPLICATE waiter**, which is rule 1's own blind spot: the SANCTIONED
`make wait-ci PR=743` matches no hand-rolled shape, so two ran at once. Rule 3 therefore
probes for a live process on the same TARGET rather than matching the command, and speaks
once per target rather than once per session. Redirect and read the file instead.
Rules 4-5 (2026-09-08, ST120 (c)+(d)) are **more than one `/card` in one exec** — matched as
the CLASS, any two card invocations however joined, since `;` loses the same cards the
prose's `&&` names — and **`gh auth switch`**, which mutates gh's GLOBAL account that the
operator's own terminal shares, where scoping it costs one prefix
(`GH_TOKEN=$(gh auth token --user s10023) gh <cmd>`). Both ANCHOR where a command can start,
and a mutation case pins that anchor: unanchored, rule 5 fires on prose merely NAMING the
command — the defect that once had `guard-destructive.py` blocking its own commit message.
**Rule 6 (2026-09-09, ST120 (b)) is EDITING THE PYTHON TREE WHILE A SUITE IS LIVE** — the one
rule with no command string to match, which is why it needed a second matcher rather than a sixth
regex. `make test` and `make preflight` run BYTE-IDENTICAL argv, so the discriminator is the pytest
process's **cwd**: preflight's sits in its clone, so its exemption is structural rather than a name
match, and a working-tree edit provably cannot reach that run. It dedups per suite PID, not per
session. ⚠ **Its probe is ANCHORED at an interpreter actually running pytest** — a bare
`pgrep -f pytest` matched the SHELL that had merely typed the word, re-confirming rule 3's trap
live. `test_guard_shell_hygiene.py` (82 cases, 16 mutation) runs in CI's dependency-free
`markdownlint` job; re-run it after any edit to either. ⚠ **Both rule-6 mutations first passed
VACUOUSLY**: a mutation copy in `/tmp` has no `.git` above it, so it bailed before reaching the
mutated line — the fixture now sets `CLAUDE_PROJECT_DIR` the way the harness does. ⚠ **Scoping a guard to the SYMPTOM
you noticed rather than to the CLASS is the recurring defect here** — mutation tests cannot
reveal it, because they only probe rules that exist.

**Guardrail.** A PreToolUse hook (`.claude/hooks/guard-destructive.py`) blocks catastrophic
Bash (rm -rf, git reset --hard, force-push, DB wipes) **and `rclone config create|update`
with no `>/dev/null`, which prints live tokens to stdout on SUCCESS** — prose had already
failed at that twice, and `AGENTS.md` says so in its own words. If blocked, surface it
rather than working around it silently. `test_guard_destructive.py` (33 cases, 4 mutation)
gates it in the same CI job — added 2026-09-03, since the BLOCKING hook was the one of the
three with no suite at all. Deep ref `.claude/context/tools.md`.

⚠ **Every wrapper in `.claude/settings.json` resolves `.venv` BEFORE `python3`, and that
ordering is the whole guard on Windows — do not "simplify" it back to a bare interpreter.**
`python3` here is the Store App Execution Alias: it passes `[ -f ]`, `[ -x ]` and
`command -v`, then exits **126**. Only exit 2 blocks, so from the 2026-09-18 host move until
2026-09-23 all five hooks — the BLOCKING guard included — failed **OPEN on every command**,
and no hook MODULE was broken, so no module test could see it. The stub cannot be detected,
only out-ordered. Each wrapper also keeps an explicit `[ -f "$h" ] || exit 0`, because
CPython exits 2 on a missing script and absence must fail OPEN, never CLOSED.
`test_hook_wiring.py` reads the wrapper strings out of `settings.json` and gates both
properties; it sits in `.claude/hooks/` rather than `tests/` because the heavy CI leg is
paths-filtered to `**/*.py` and `settings.json` is not in it → SoT ST143.

**Footgun delivery lives in a hook, not in this file.** `.claude/hooks/context-guard.py` +
`context-map.json` deliver a card at the moment a guarded file is edited, which is what let
those rules leave the always-loaded tier (`_upsert`, `round_down_to_step`, the XS
maker/taker split, backtest run selection, the powered-null criterion, `bar` units,
DSR/MinTRL directionality). **Both files are COMMITTED since 2026-08-19** — they were
gitignored, so a reclone kept the knowledge and lost the DELIVERY. `test_context_guard.py`
(35 cases) now runs in CI's dependency-free `markdownlint` job, so the hooks finally have a
gate that reports to somebody. Re-run it after any hook or card edit. **A card's globs must
cover every file its rule bites on**, and every card needs a MUTATION case proving the glob
is scoped rather than blanket.

## Session Memory Protocol

At the end of every session where anything changed, update the **Current State** section in
the memory tree's `MEMORY.md` without waiting to be asked. Keep current: a one-line
last-session summary, and open questions or pending decisions (or "none").

⚠ **Resolve that path with `tools/memory_dir.py`, never by hand.** BOTH the config root
(`.claude-personal` on the old Linux box, `.claude` on the Windows laptop) and the project
slug (derived from the repo's ABSOLUTE path) vary by host, so a hardcoded path is wrong on
every machine but one — which is what it was, here and in three skills, until 2026-09-19.
`$(PYTHONPATH=. poetry run python tools/memory_dir.py)` prints it; `make status` uses it.

⚠ **The SoT (`memory/project_todo_master.md`) carries the SAME unprompted obligation, and it
is stated here because omitting it is what made it drift.** The end-of-session rules named
`MEMORY.md` and the handoff; those two get written every session, while the surface declared
SINGLE SOURCE OF TRUTH was written only when someone happened to think of it. That is the rule
set working as written, not carelessness — which is why prose reminders elsewhere did not fix
it. Reconcile the row this session touched **at closure time, as one line plus a pointer into
[[todo-archive-closed]]** — never as a later sweep, since the 2026-08-26d sweep of 26 rows /
~80KB only became necessary because nobody did it per-row. ⚠ **A row is not finalised while it
says "PR pending"**: four rows read `✅ FIXED … (PR pending)` days after their PRs merged, and
`daily_check.py`'s detector never examined them — it looks for rows marked OPEN, so the stale
clause was invisible to it by construction.

The index is read into context every session, so its cost is paid on every conversation:

- **Current State holds at most 6 bullets.** Adding a 7th means first rolling the oldest,
  verbatim, into `memory/project_session_log_<month>.md`.
- **"Latest" is at most 2 lines; every other bullet is exactly 1 line.** Detail belongs in a
  topic file or the session log.
- Session logs have no size limit — that is what they are for. Prune by MOVING.

## Agent Skills

Skills live in `.claude/skills/<name>/SKILL.md` (committed) and are invoked as
`/skill-name`. **Use them proactively.** The harness injects every skill's name and
description into each session, and that injected list is authoritative — only the rules it
cannot carry live here:

- **Always load `/frontend-design` before any Svelte / CSS / UI change**, paired with
  `/frontend-svelte`.
- **Cadence** the descriptions don't convey: `/sanity-check` weekly or after any large
  refactor · `/decay-review` weekly · `/db-update` after any detector, strategy or config
  change · `/recalibrate` after any `make buibui-backtest SAVE=1` · `/journal-trade` whenever
  a manual trade closes · `/ingest-charts` + `/ingest-feed` + `/card` daily (operator,
  2026-08-17) · `/research-distil` after any book, repo or paper ingest. The first three are marker-tracked in `docs/plans/task-marks/`, stamped by
  whoever runs them; a missing marker reads as overdue on purpose, and nothing auto-runs.
- **`/research-distil` emits at most THREE hypotheses per run, and that cap is the point.**
  The intake's own header says the bottleneck is testing capacity, not idea capture, and
  trial count dominates n — so a skill that turns a book into forty hypotheses pushes every
  cell out of reach. "Unreachable, do not build" is a successful output, not a failure.
- **Does a skill upgrade force a RE-INGEST of the old corpus? Ask what the defect changed.**
  A defect that changed **coverage** — what got dropped, capped or never fetched — requires
  one, because the missing rows are unrecoverable from the per-item notes. A defect that
  changed only **presentation, attribution or routing** of material already captured does
  not: the evidence is still on disk and a cheaper repair exists. Measured both ways —
  `/ingest-video`'s item-cap left 29 items unrecoverable, while the roster fix repaired
  attribution and `mechanics-backlog` roughly doubled with **no LLM call**. This lives here
  rather than in the four ingest skills because a rule spelled four ways drifts, which is
  how the powered-null family reached six sites.
- **Take `tp_r` only from `/wfo-sweep`**, the trusted production path. `/config-refresh` runs
  on the full dataset with no out-of-sample split.
- **Run one `/card` per background exec** — never `&&`-chain them.

**Vendored skills are COMMITTED, hash-pinned, and NOT ours to edit.** `.agents/skills/<name>/`
holds the real directory, `.claude/skills/<name>` is a *relative* symlink into it, and
`skills-lock.json` pins each by hash — all three tracked, because a pin nobody can verify is
not a pin. A local edit to a vendored copy is **silently overwritten** on the next refresh:
fix upstream and re-vendor. ⚠ `computedHash` is **not** a plain sha256 and the algorithm is
recorded nowhere, so the pin cannot be verified from this repo — do not "correct" it by hand.
Rule, and what is still owed (refresh CI): `.claude/rules/vendored-skills.md`, delivered at
edit time by the `vendored-skills` context card.

### Subagent definitions — `.claude/agents/<name>.md`

A **skill** is a workflow you invoke; an **agent** is who a skill dispatches work TO.
Frontmatter carries `name` / `description` / `model` / `tools`; address as
`subagent_type: "<name>"`.

**The reason is measured.** An unnamed general-purpose dispatch carries the full default
system prompt plus ~20 tool schemas — **~19.7K tokens of overhead per dispatch**.
`chart-extract` (`model: sonnet`, `tools: Read`) cut `/ingest-charts` **−39.9%** (296,456 →
178,123) with quality neutral-to-better. The saving is *constant per dispatch*, so **payoff
scales with dispatch count, not task size**.

- **A new `.claude/` subtree now ships TRACKED but UNLINTED.** Since 2026-08-19 `.gitignore`
  tracks `.claude/` by default, so the dies-on-a-clone half of this trap is closed — but
  `.markdownlint-cli2.jsonc` still excludes `.claude` wholesale and re-includes named
  subtrees only, so **add the new subtree there**. ⚠ **The two configs deliberately no
  longer mirror each other**: the vendored tree is committed *and* unlinted, because we
  version an upstream copy without owning its style.
- **`tools:` is a structural guarantee; prose is not.** `tools: Read` is why an extractor
  *cannot* write files — a general-purpose one did. But the "bare JSON, no fence" rule still
  broke 1-in-6 despite an explicit directive, so **keep tolerating malformed output at the
  consuming end**.
