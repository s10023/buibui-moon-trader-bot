# `AGENTS.md` split + `docs/agents/` config — design (2026-08-19)

**Date:** 2026-08-19 · **Status:** approved, not yet implemented
**Companion:** `docs/research/2026-08-19-cross-repo-workflow-standard.md` (items 1 and 2 of its
reference-repo port list) · SoT ST44

---

## Why this is worth doing

The cross-repo standard measured skill drift between this repo and the wifey fork and found it
*inverted*: domain skills drift 8–19%, workflow skills **89% (`post-branch`) and 84%
(`sanity-check`)**. The cause is structural rather than sloppy — both skills carry
repo-specific facts inline, so the same workflow written twice becomes two workflows.

The named fix is to make the skills generic and let each repo carry its own variance in a
config the skills read. That fix needs a place for the variance to live, and this repo has
none. `post-branch/SKILL.md:99` proves the shape is already wanted: it holds a `surfaces:`
YAML block labelled *"When porting this skill to another repo, edit only this block — the
rest of the workflow stays the same."* The block is the config; it is simply trapped inside
the skill, where no other consumer can read it and where the four checker constants that
encode the same list have already drifted out of its sight.

Separately, `CLAUDE.md` is 935 lines of which most is agent-neutral project fact. Naming it
after one vendor is an accident of history, and it leaves no seam along which the file can be
split by audience.

## Non-goals

- **Not a context-budget cut.** `CLAUDE.md` imports `AGENTS.md`, so the always-loaded cost is
  unchanged by construction. Demoting verdict prose out of the always-loaded tier was
  considered and rejected: `CLAUDE.md` states the opposing rule deliberately — *a guard rail
  behind a pointer is not a guard rail*.
- **Not the drift fix itself.** This lands the config the fix will read. De-repo-ifying
  `post-branch/SKILL.md` (1200 lines) and `sanity-check/SKILL.md` (247 lines) is a follow-up
  PR, and is worth doing when the fork can receive the shared result.
- **Not a rewrite of any prose.** Sections move between two files; their text does not change.
- **No new dependency.** `tomllib` is stdlib on this Python, which is what keeps the
  dependency-free CI job able to run the checkers.

## Design

### 1. The seam

`AGENTS.md` becomes the real file and takes the agent-neutral body:

| Section | Why neutral |
| --- | --- |
| Project Overview · Key Commands · CLI | Facts about the software, not about a harness |
| Project Structure, incl. sleeve verdicts, standing research verdicts, code-level rules, the gate and powered nulls, spec-reconcile counting | Research verdicts bind any agent that proposes work |
| Code Style · Testing · Dependencies · Documentation | Toolchain facts |
| Git Conventions, incl. PR titles and the CI-quota visibility flip | `git` and `gh`, not Claude |
| Working Agreement: Persona · Definition of Done · background-vs-foreground · Anti-drift | Engineering discipline, vendor-independent |

`CLAUDE.md` becomes `@AGENTS.md` followed by the harness-only residue:

| Section | Why Claude-only |
| --- | --- |
| Working Agreement: Token efficiency (`ctx_*` tools) · Model delegation (sonnet/haiku/opus tiers) | Names this harness's tools and model tiers |
| Working Agreement: Guardrail · Footgun delivery | `.claude/hooks/` `PreToolUse`, context cards |
| Session Memory Protocol | `~/.claude-personal/` memory tree |
| Agent Skills · Subagent definitions | `.claude/skills/`, `.claude/agents/`, `subagent_type` |

Roughly 740 lines to `AGENTS.md`, 195 to `CLAUDE.md`.

**The import is required, and carries no double-load risk — SETTLED 2026-08-19 by measurement,
not by reading.** Two bare temp dirs, each holding a distinct canary phrase:

| Probe | Setup | Result |
| --- | --- | --- |
| A | `AGENTS.md` alone, no `CLAUDE.md` | `NONE` — the canary never reached context |
| B | `CLAUDE.md` containing only `@AGENTS.md` | the canary, returned verbatim |

The docs give the mechanism behind that: Claude Code walks up the directory tree checking each
level for **`CLAUDE.md` and `CLAUDE.local.md` only**. A root `AGENTS.md` is never auto-discovered,
and the docs name `@AGENTS.md` as the supported alternative to symlinking `CLAUDE.md` at it. So
the import is not an optional nicety — **without it `AGENTS.md` is invisible**, which is the
failure this probe existed to rule out in the other direction.

⚠ **This is a fact about the current build, so it can change under us.** Re-verify with the
`InstructionsLoaded` hook, which logs which instruction files load and why, or by reading
`/context`'s **Memory files** list. A future build that auto-discovers `AGENTS.md` would turn
the import into the double-load this section was written to prevent.

### 2. `docs/agents/surfaces.toml`

One file, three tables. It is data because its consumers are scripts; prose here would leave
the checkers on their own hardcoded copies, which is the drift being fixed.

```toml
[repo]
flip_target = "s10023/buibui-moon-trader-bot"
gh_user = "s10023"

[budgets]
handoff_lines = 200
handoff_ceiling = 600
memory_state_bullets = 6
memory_state_ceiling = 8

[[surface]]
id = "agents_md"
path = "AGENTS.md"
purpose = "Agent-neutral project context: structure, commands, verdicts, conventions"
roles = ["anchor", "enumerating", "negative_claim", "sanity"]
```

`roles` is the mechanism. Each of the four currently-hardcoded tuples becomes a derived view
over the single `[[surface]]` list, so a surface added or removed is added or removed
everywhere at once:

| Role | Replaces |
| --- | --- |
| `anchor` | `post_branch_checks.ANCHOR_FILES` |
| `enumerating` | `post_branch_checks.ENUMERATING_DOCS` |
| `negative_claim` | `post_branch_checks.NEGATIVE_CLAIM_PATHS` |
| `sanity` | `sanity_checks.SURFACE_FILES` |

### 3. The loader

New `tools/agents_config.py` — stdlib only, one entry point:

```text
load(root: Path) -> AgentsConfig      # frozen dataclass
AgentsConfig.paths_with_role(role)    # -> tuple[str, ...]
AgentsConfig.budgets                  # -> Budgets
```

**A missing or malformed config raises, and every caller renders that as a FINDING.** It must
never degrade to SKIPPED. This repo has shipped that defect twice — three ported legs once
printed `0 findings, exit 0` while reporting SKIPPED, indistinguishable from a correct CI run —
and the leg this spec repairs is itself an instance of the same family.

### 4. Consumers repointed

- `tools/post_branch_checks.py` — three constants become `paths_with_role` calls.
- `tools/sanity_checks.py` — `SURFACE_FILES` likewise.
- `.claude/skills/post-branch/SKILL.md` — the `surfaces:` block is replaced by a pointer at
  the TOML. The block's own instruction ("edit only this block when porting") moves with it.
- `docs/plans/daily_check.py` — `_HANDOFF_BUDGET, _HANDOFF_CEIL` read from `[budgets]`, so the
  daily ratchet and the branch gate cannot disagree about the number. ⚠ This consumer is
  **gitignored**, so no CI job can catch it breaking against a config change; it degrades to an
  `i` line by its own `except` and must be exercised by hand once after the repoint.
- Prose pointers: `README.md:130` and the "Moved out of CLAUDE.md" headers in the six
  `.claude/context/*.md` files.

### 5. The handoff-size leg gets a real threshold

`post_branch_checks._check_handoff_size` currently compares a `Line count: **N**` stamp against
the real count. The stamp was deleted; the leg was not. On a regex miss it returns `[]`, so it
is **vacuously green forever** — precisely what the cross-repo standard warned against in
writing (*"delete the stamp and the leg in the SAME change"*), and no test covers it.

Replacement: drop the stamp comparison, read `[budgets]`, and report over-budget and
at-ceiling as findings of increasing severity.

⚠ **A "grew this branch" delta check is not buildable and must not be attempted.** The handoff
is gitignored, so no baseline exists to diff against. Budget plus ceiling is the whole
mechanism.

### 6. What deliberately does not change

- **The ~90 dated plan, spec and audit docs citing `CLAUDE.md`.** They are correct history.
  `stale_anchors.is_dated_path` already excludes those trees — verified, not assumed.
- **`deploy/README.md:393`** refers to the account-level `~/.claude-personal/CLAUDE.md` restore
  path. Unrelated file, unrelated tree.
- **Verdict and footgun prose stays always-loaded**, per the rule quoted in Non-goals.
- **`.markdownlint-cli2.jsonc` needs no edit.** Root `AGENTS.md` is already inside `**/*.md`,
  so it inherits the existing gate.

## Failure modes

| Failure | How it shows | Control |
| --- | --- | --- |
| A future build auto-discovers `AGENTS.md`, so the import double-loads it | Boot cost roughly doubles | Measured absent on this build; re-verify via the `InstructionsLoaded` hook or `/context` |
| Config unreadable in CI | Checkers silently report clean | Loader raises; callers render FINDING, never SKIP — mutation-tested |
| A checker keeps a private copy of the list | Config and enforcement drift apart again | Mutation test: dropping a role must shrink the derived tuple |
| Sections land in the wrong file | A harness rule becomes unreadable to Claude, or a project fact invisible to other agents | The audience table above is the acceptance criterion; prose is moved, never rewritten |
| Someone re-adds a threshold-free budget leg | Vacuous green returns | The new leg's tests assert firing at both budget and ceiling |

## Testing

New `tests/test_agents_config.py`, plus extensions to `tests/test_post_branch_checks.py` and
`tests/test_sanity_checks.py`. Three mutation cases carry the weight, because this repo's
checkers have twice passed while measuring nothing:

1. **Malformed TOML must fail, not skip.** Assert the finding, and assert it is not a SKIP.
2. **Dropping a `role` from the config must shrink the derived tuple.** This is what proves the
   checker reads the config rather than a surviving hardcoded copy.
3. **`handoff-size` must fire at budget and again at ceiling.** The current leg passes on every
   input; a test that only asserts "clean config is clean" would reproduce the defect.

Gates: `make lint-py`, `make typecheck`, `make test`, `make lint-md`. `make test-regression` is
**not** required — the diff touches no path in the backtest surface. `make docs-index` after
this file lands.

## Decision log

| Decision | Reversed by |
| --- | --- |
| TOML data rather than Markdown prose for `docs/agents/` | A second repo adopting the config and needing judgement text a script cannot consume — then split prose out beside the data, keeping the data authoritative |
| Config and seam only; skills stay repo-specific this PR | The fork becoming ready to receive a shared skill, which is what makes de-repo-ifying pay |
| Boot cost held constant; no prose demoted | A measurement showing the always-loaded tier is causing skipped reads, which would outweigh *a guard rail behind a pointer is not a guard rail* |
| One `[[surface]]` list with `roles`, not four lists | A role needing per-role attributes beyond a path, at which point the views stop being views |
| `@AGENTS.md` import over a plain cross-reference | A build that auto-discovers `AGENTS.md`, making the import a double-load. Measured absent 2026-08-19 — a cross-reference would leave the file unloaded entirely |

## What this does not answer

- Whether `post-branch` and `sanity-check` should ultimately be **vendored** shared skills
  across both repos rather than two copies reading two configs. That is the endpoint the
  cross-repo standard implies; this spec only removes the reason they cannot be.
- Whether `.claude/context/` should move to a vendor-neutral path. `AGENTS.md` will point a
  non-Claude agent at a `.claude/` directory, which is odd but harmless, and moving it would
  churn every context card glob for no measured gain.
