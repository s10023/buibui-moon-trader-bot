# Claude Code

@AGENTS.md

`AGENTS.md` holds this repo's instructions and is imported above. This file keeps only what is
specific to Claude Code's harness — its tools, its model tiers, its hooks, its skills and its
memory tree.

⚠ **`@AGENTS.md` is a LIVE IMPORT, not a citation. Deleting that line — or fencing it in
backticks, or moving it inside a code block — silently unloads every repo
instruction in it.** There is no error and nothing looks different, so the session goes on working
against harness defaults it cannot see it lost: every verdict, footgun and gate in `AGENTS.md`
simply stops being in context. Nothing in CI reads this file, so no gate catches it either.
If the path must change, change it in place and re-verify the import still resolves.

**Token efficiency.** Skills are dormant until invoked. Redirect any command output over
~20 lines to a file and read the part you need. `/compact` at logical boundaries rather than
waiting for autocompaction. Delegate heavy reads to a subagent when the saved main-context
clutter outweighs the startup cost.

**Model delegation.** The main thread is orchestrator and tech lead — design, judgement,
review and routing stay here. Delegate bulk mechanical work down by tier: **sonnet** for
high-volume execution (vision extraction, file sweeps, boilerplate, test triage), **haiku**
for trivial lookups, **opus** subagents as a quota escape valve for long *parallel*
research. Every subagent brief carries goal + success metric + rubric inline, with no SoT or
memory re-reads. Verify subagent and background work directly (`ps`, `journalctl`,
`git status`) — self-reports can be stale.

**Effort is the second dial, beside the model.** It sets how much the model verifies, tests
edge cases and decides on its own, not how smart it is: it fixes missed edge cases and does
not fix a wrong approach. Rule of thumb: **low** for in-the-loop sketches and mechanical
edits, **medium** for feature work, **high** where verification or edge cases decide the
result (brownfield bug fixes, audits, research gates), **max** for fully autonomous hard
problems. Three places carry it:

- **Skills and subagents pin it with an `effort:` frontmatter key**, which overrides the
  session level while that skill or agent is active. Verification-heavy skills (`post-branch`,
  `sanity-check`, `research-distil`, `decay-review`, `investigate-strategy`, `wfo-sweep`,
  `backtest-findings`) pin `high`. Mechanical ones (`telegram-mode`, `backtest-run`,
  `pr-summary`, `data-backfill`, `db-update`) and the `chart-extract` agent pin `low`. The rest
  inherit the session level. ⚠ Do not pin the vendored `humanizer`: a local edit is
  overwritten on refresh.
- **Issues carry an `effort:*` label**, and the SessionStart digest prints it on every p1/p2
  row, showing `effort:?` when it is unset. **When starting an Issue, name its label and
  suggest `/effort <level>` before the work begins.** Claude cannot change its own session
  effort, so the operator has to act on the suggestion.
- **Feature loop:** spec, then implement on low, review, then verify on high.

Source: claude.dev "Spending your effort" (2026-09-25).

**Shell hygiene is a hook.** `.claude/hooks/guard-shell-hygiene.py` is advisory and speaks
once per rule per session. It flags six habits, each of which can turn an unverified result
into a claimed one: a hand-rolled waiter for work the harness already re-invokes you on; a
gate whose exit status is swallowed (a pipe into `tail`/`head`, or a trailing `; echo`); a
second waiter on a target already being waited on; more than one `/card` in one exec;
`gh auth switch`, which changes the account the operator's own terminal uses (scope it with
`GH_TOKEN=$(gh auth token --user s10023) gh <cmd>`); and editing the Python tree while a suite
runs. When it fires, redirect output to a file and read the file. Its suite,
`test_guard_shell_hygiene.py`, runs in CI's `markdownlint` job; re-run it after editing the
hook. How each rule matches, and why, is in the hook's own docstrings and
`.claude/context/tools.md`.

⚠ **Rules 3 and 6 NEVER FIRE on the Windows host.** Both probe live processes through `pgrep`,
and rule 6 also reads `/proc/<pid>/cwd`; neither exists there, and the hook stays silent by
design rather than guess. So `AGENTS.md`'s one hard constraint — never edit the Python tree
while a suite runs — has **no hook behind it on that host, only the prose**. The suite reports
both rules' blocks as SKIPPED, never as passed, and a skip under `CI` reds (ST144): until then
the rule-3 skip counted as a pass and rule 6's unguarded fixture crashed the run at check 45,
discarding every mutation case after it.

⚠ **The Bash tool on the Windows host HALVES every doubled backslash before bash parses the
command** — single quotes, double quotes and a quoted `<<'EOF'` heredoc alike (measured
2026-09-24: `printf '%s\n' 'a\\b'` prints `a\b`). A Windows path or regex in an inline
script arrives one escape level short, silently: a python heredoc dies on a truncated `\U`
escape, and a `sed` over a Windows path just matches nothing. Put anything
backslash-sensitive in a file with the Write tool and run the file.

**Guardrail.** A PreToolUse hook (`.claude/hooks/guard-destructive.py`) blocks catastrophic
Bash (rm -rf, git reset --hard, force-push, DB wipes) **and `rclone config create|update`
with no `>/dev/null`, which prints live tokens to stdout on SUCCESS**, and `git worktree remove` over
a tree holding a directory junction or symlink, which on Windows deletes the link's TARGET (#963) —
that rule also runs on the PowerShell tool. If blocked, surface
it rather than working around it. `test_guard_destructive.py` gates it in the same CI job.
Deep ref `.claude/context/tools.md`.

⚠ **Every wrapper in `.claude/settings.json` resolves `.venv` BEFORE `python3`, and that
ordering is the whole guard on Windows — do not "simplify" it back to a bare interpreter.**
`python3` here is the Store App Execution Alias: it passes `[ -f ]`, `[ -x ]` and
`command -v`, then exits **126**. Only exit 2 blocks, so a bare `python3` makes every hook —
the blocking guard included — fail **open on every command**, with no hook module broken for a
module test to see. The stub cannot be detected,
only out-ordered. Each wrapper also keeps an explicit `[ -f "$h" ] || exit 0`, because
CPython exits 2 on a missing script and absence must fail OPEN, never CLOSED.
`test_hook_wiring.py` reads the wrapper strings out of `settings.json` and gates both
properties, plus that EVERY hook command is such a wrapper — the inline `jq | grep`
`gh pr create` reminders were not, and never fired on the jq-less Windows host (#855); it sits in `.claude/hooks/` rather than `tests/` because the heavy CI leg is
paths-filtered to `**/*.py` and `settings.json` is not in it → SoT ST143.

**Footgun delivery lives in a hook, not in this file.** `.claude/hooks/context-guard.py` +
`context-map.json` deliver a card at the moment a guarded file is edited, which is what let
those rules leave the always-loaded tier (`_upsert`, `round_down_to_step`, the XS
maker/taker split, backtest run selection, the powered-null criterion, `bar` units,
DSR/MinTRL directionality). Both files are committed; keep them tracked, or a reclone keeps
the knowledge and loses the delivery. `test_context_guard.py` runs in CI's `markdownlint`
job; re-run it after any hook or card edit. **A card's globs must
cover every file its rule bites on**, and every card needs a MUTATION case proving the glob
is scoped rather than blanket.

## Session Memory Protocol

At the end of every session where anything changed, update the **Current State** section in
the memory tree's `MEMORY.md` without waiting to be asked. Keep current: a one-line
last-session summary, plus judgement and verdicts with pointers. **Open questions and pending
decisions are NOT memory content** — each becomes an Issue labelled `question` (planning
paragraph below).

⚠ **Resolve that path with `tools/memory_dir.py`, never by hand.** BOTH the config root
(`.claude-personal` on the old Linux box, `.claude` on the Windows laptop) and the project
slug (derived from the MAIN checkout's absolute path, so every worktree shares its owner's
tree) vary by host, so a hardcoded path is wrong on every machine but one.
`$(PYTHONPATH=. poetry run python tools/memory_dir.py)` prints it, or exits 1 naming every
path it tried; `make status` uses it.

**Planning lives in GitHub Issues on this repo (since 2026-09-29), and it carries the same
unprompted obligation.** A SessionStart hook (`.claude/hooks/open-issues.py`) opens every
session with the p1/p2 queue in context; a digest reading `NOT FETCHED` means you are
working blind, never that the queue is empty. **Every piece of open work becomes an Issue —
a to-do, a shower thought, a future plan, a skill fix, an open question or a pending decision
(the last two labelled `question`) — never a memory row and never a handoff list.** The
handoff (`docs/plans/next-conversation-prompt.md`) keeps only SEQUENCING: an ordered list of
Issue numbers, host state and standing hazards. `cloud-ok` marks an Issue a cloud session can
finish from tracked files alone — no `analytics.db`, `docs/plans/`, memory tree, keys, Binance
account or Windows-host specifics. **Close the Issue this session finished at closure time, with a one-line
verdict comment** — a PR body's `Closes #n` does it on merge. Issues publish with the repo on a
visibility flip, so redact account figures and screen the body with `make post-branch-text`.
`memory/project_todo_master.md` is now a pointer stub (rulings, gates, old-id → Issue map);
do not re-add status to it.

**A cloud session (claude.ai/code) is not the laptop.** It has no `analytics.db`, `docs/plans/`,
memory tree, keys or account plugins (`mattpocock-skills` included), a shallow clone, and
GitHub only through repo-scoped MCP tools. The digest prints a `CLOUD SESSION` banner when
`CLAUDE_CODE_REMOTE=true`. **From there, file every Issue as `needs-triage`, never
`ready-for-*`, and give work that needs the laptop a `## Local session prompt` block.** The
full table and filing rule: `.claude/context/cloud-sessions.md`.

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
  2026-08-17) · `/research-distil` after any book, repo or paper ingest · `/sync-child`
  weekly · `/retro` (mattpocock-skills) monthly (operator, 2026-10-10). `daily_check.py`
  tracks `/sanity-check`, `/decay-review`, `/sync-child` (7d) and `/retro` (30d) through
  markers in `docs/plans/task-marks/`, stamped by whoever runs them. Stamp `/retro` by hand
  afterwards (`date -u +%FT%TZ > docs/plans/task-marks/retro`), because a plugin skill is
  not ours to edit. A missing marker reads as overdue on purpose, `/db-update` has no
  marker, and nothing auto-runs.
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
holds the real directory, `.claude/skills/<name>` is a COPY of it (installed with `--copy`), and
`skills-lock.json` pins each by hash — all three tracked, because a pin nobody can verify is
not a pin. A local edit to a vendored copy is **silently overwritten** on the next refresh:
fix upstream and re-vendor. ⚠ `computedHash` is **not** a plain sha256 and the algorithm is
recorded nowhere, so the pin cannot be verified from this repo — do not "correct" it by hand.
Rule, and what is still owed (refresh CI): `.claude/rules/vendored-skills.md`, delivered at
edit time by the `vendored-skills` context card.

### Issue tracker

GitHub Issues on `s10023/buibui-moon-trader-bot`, every `gh` call prefixed with the personal
token, every body screened before it publishes. See `docs/agents/issue-tracker.md`.

### Triage labels

The five default roles (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`,
`wontfix`), beside the repo's priority, effort and kind labels. See
`docs/agents/triage-labels.md`.

### Domain docs

Single-context: `GLOSSARY.md` + `docs/adr/`, both created lazily, with filed research verdicts
treated as ADRs. See `docs/agents/domain.md`. The review standards are in `CODING_STANDARDS.md`.

### mattpocock-skills — this repo's adaptations

The plugin is installed account-wide; the flow and its cross-repo rules are in the account
`CLAUDE.md`. Here, five bindings:

- **`/implement`'s "full suite once at the end" is `make preflight`** at `/post-branch` Step 7,
  never `make test` and then `make preflight`.
- **`/implement-spec` runs implementers in worktrees, which hold tracked files only** — no
  `docs/plans/`, `analytics.db` or `.claude/sensitive-terms.txt`. Give a ticket that needs one
  to an implementer working in the main checkout, and push from a worktree with an explicit
  `HEAD:refs/heads/<branch>` refspec, because a worktree branch can track `origin/main`.
- **The PR body is Matt's `pr` shape, written through `/pr-summary`**, which adds this repo's
  title and test-plan rules and the file output.
- **`/handoff` is for forking a side task; it never replaces the standing handoff**
  (`docs/plans/next-conversation-prompt.md`, written by `/post-branch` Step 10).
- **`diagnosing-bugs` on a signal that did or did not fire builds its Phase 1 loop with
  `/investigate-strategy`**, which replays the detector at the candle.

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
