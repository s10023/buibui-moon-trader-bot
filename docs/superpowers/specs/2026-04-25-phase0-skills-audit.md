# Phase 0b — Agent Skills Audit + Sync Research

**Date:** 2026-04-25
**Scope:** Read-only audit of 15 project skills in `.claude/skills/`, plus pattern research on `vor-stream/.github/workflows/update-skills.yaml` and `fairstone/.claude/`. No code or skill edits.

## Per-skill audit table

| Skill | Status | Blockers / notes |
| --- | --- | --- |
| `atr-sweep` | **keep** | 114 lines. References `make buibui-backtest CONFIG=...`, `--atr-sl-values`, `--atr-sl-multiplier` — all live targets/flags. File-path refs to `analytics/backtest_lib.py`, `backtest_config.py`, `signal_config.py` all current. |
| `backtest-findings` | **keep** | Frontmatter clean. Workflow-agnostic ("interpret any sweep table"), low rot risk. |
| `backtest-run` | **keep** | 136 lines. CLI flag reference. Depends on `buibui backtest` flag stability — watch in Phase 2 CLI split. |
| `config-refresh` | **refresh** | 183 lines. Step-by-step TOML refresh. Overlaps significantly with `wfo-sweep` (full automated chain) — confusing user choice. MEMORY note: "config-refresh uses full-dataset sweep (no OOS); wfo-sweep uses WFO IS/OOS — trust wfo-sweep for production tp_r." Either delete config-refresh, or re-scope it to "non-tp_r refreshes" (timeframes, day_filter, volume) so the two skills don't compete. |
| `investigate-strategy` | **keep** | Uses `buibui signal test` replay path. Live. |
| `new-strategy` | **refresh** | 168 lines. References `tests/test_indicators.py` — actual file is `tests/test_indicators_lib.py`. Path is stale. Easy fix. |
| `param-sweep-apply` | **keep** | 86 lines. Manual-paste workflow; complementary to `wfo-sweep` (automated). Both can coexist. |
| `post-branch` | **keep** | 66 lines. Auto-runs on branch end. Light, references CLAUDE.md, README.md, MEMORY.md, Makefile, docker-compose.yml. |
| `pr-summary` | **keep** | Writes to `/tmp/pr-<branch>.md`. MEMORY note: "User's GitHub CLI cannot create PRs (collaborator error)" — skill correctly avoids `gh pr create`. |
| `recalibrate` | **keep** | 100+ lines. References `buibui recalibrate --apply`, `confidence_ratings` DB table, `make regression-update` — all live. |
| `sanity-check` | **refresh** | Full health-check skill. Multi-section. **References its own freshness check ("Skills list in `.claude/skills/` looks correct") so it should be self-updating** — but the actual list of subcommands it audits could drift. Verify in Phase 1 after CLI splits in Phase 2. |
| `signal-watch` | **keep** | 179 lines. Workflow + TOML reference. Live commands. |
| `stats-dashboard` | **keep** | Card inventory + caching constraints. Tied to `stats_lib.py` + `web/api/routers/stats.py` — both live. Phase 2 may split `stats_lib.py` (1,532 LOC); skill will need update then. |
| `volume-sweep` | **keep** | 117 lines. Uses A14b directional `volume_suppress`. Implementation refs match current code. |
| `wfo-sweep` | **keep** | Automated WFO chain — flagship skill. MEMORY says "trust wfo-sweep for production tp_r." |

**Summary:** 12 keep · 3 refresh · 0 delete · 0 rewrite. No skills are catastrophically broken; the rot is concentrated in path drift (`tests/test_indicators.py` → `tests/test_indicators_lib.py`) and overlapping scope (`config-refresh` vs `wfo-sweep`).

## Style consistency

Most buibui skills use a **single-line description** in frontmatter (`description: "..."`). Sections vary widely — some use step-numbered headers (`config-refresh`: Step 0–8), others use task-shaped "Task: <verb>" sections (`pr-summary`, `signal-watch`), others use reference-style sections (`backtest-run`: Most common invocations, All CLI flags). **No single template.**

Drift to call out:

- `config-refresh`, `signal-watch`, `wfo-sweep` are long (~150–180 lines).
- `post-branch`, `pr-summary` are short (~60–90 lines).
- `param-sweep-apply` uses **decision-rule sections** with `### Selecting best tp_r` style.

Recommendation: standardise frontmatter to include explicit invocation phrases (matching fairstone pattern) and adopt a 4-section template for long skills: `When to use` / `Task` / `Output` / `Implementation files`.

## Pattern findings — vor-stream `update-skills.yaml`

```yaml
on:
  schedule: { cron: "0 3 * * *" }   # daily 03:00 UTC
  workflow_dispatch:
permissions: { contents: write, pull-requests: write }
```

The workflow:

1. Checks out branch `v26.1` (pinned).
2. Runs `./update-skills.sh` (a repo-local sync script — content not in workflow).
3. Diffs `.agents/skills/`, `.claude/skills/`, `skills-lock.json` for changes.
4. If changes, opens PR on branch `chore/update-agent-skills` via `gh`.

**Notable patterns:**

- Dual location: `.agents/skills/` AND `.claude/skills/` — vor-stream maintains skills in both surfaces.
- Lockfile `skills-lock.json` — pins skill versions (likely from a shared registry).
- Daily cron + auto-PR — pulls skill updates without manual intervention.
- Pinned ref (`v26.1`) — skills sync targets a stable branch, not main.

**Adopt for buibui (Phase 1):**

- A `chore/update-agent-skills` PR-on-change pattern (cron daily, even if `./update-skills.sh` is a no-op stub initially).
- Single-location is fine — buibui doesn't have `.agents/skills/`. Don't over-engineer.
- Skip the lockfile until we have multiple skill sources.

## Pattern findings — fairstone `.claude/skills/`

26 skills, mostly Go-language reference (`go-code-review`, `go-error-handling`, `go-naming`, `go-testing`, etc.) plus 5 workflow skills (`analyze-vor-changelog`, `compare-regression`, `deploy-vor`, `extract-benchmark-data`, `upgrade-vor`).

**Frontmatter style (all skills):**

```yaml
---
name: analyze-vor-changelog
description: >
  Use to analyze ... Invoke when the user says "/analyze-vor-changelog",
  "what breaks in v26.1", "check breaking changes", or needs to know ...
  This is Step 2 of the /upgrade-vor workflow.
license: proprietary
allowed-tools: Bash(bash:*)
---
```

**Notable patterns:**

- **YAML block-scalar description (`description: >`)** — multi-line, includes explicit invocation phrases ("Invoke when the user says X").
- **`license: proprietary`** field — useful for repos that ship with mixed licenses.
- **`allowed-tools: Bash(bash:*)`** — restrictive tool whitelist per skill. Buibui skills currently inherit full tool access.
- **Workflow bundling** — `analyze-vor-changelog` is "Step 2 of /upgrade-vor". Multi-step workflows have an orchestrator skill that calls step-skills.
- **Reference-pack pattern** — Go style skills (one per topic: control-flow, naming, error-handling) function like a reusable knowledge library, not a workflow.

**Adopt for buibui (Phase 1):**

- Multi-line `description: >` with explicit invocation phrases — clearer auto-invocation triggers.
- `allowed-tools` whitelisting — meaningful for `pr-summary` (Bash + Write only), `post-branch` (Read + Bash only), `recalibrate` (Bash only). Tighter blast radius.
- Skip `license` — single-license project.
- Workflow bundling concept — `wfo-sweep` is already a "bundled workflow"; not strictly needed, but `config-refresh` could be re-scoped as part of a `wfo-sweep` family.

## Pattern findings — `~/.claude-personal/skills/` (user-level)

Mixed format: some skills are flat `.md` files (`atr-sweep.md`, `pr-summary.md`, `sanity-check.md` — duplicates of project skills as single files), others are folders with subskills (`brainstorming/`, `code-simplifier/`, `find-skills/`, `skill-creator/`, `writing-plans/`, `writing-skills/`). Generic skills live there; project-specific skills are duplicated for portability.

**Implication:** project skills in `.claude/skills/<name>/SKILL.md` (folder-based) is the modern format; flat `.md` is the older format. No action — buibui already uses the modern format.

## Coverage gaps — workflows not yet a skill

Reading MEMORY + Makefile, these recurring workflows are **not covered by an existing skill**:

1. **`db-update` workflow** — `make db-update` (= `db-update-backtest` → `db-update-recalibrate` → `regression-update`). Common after backtest/strategy changes. Currently only documented in CLAUDE.md. Worth a skill: when to run, what changes are expected, how to read regression diffs.
2. **Data backfill** — `buibui analytics backfill SINCE=...` is a recurring action (MEMORY: "Re-run backfill with `SINCE=2025-09-12` if DB is ever wiped"). No skill explains the flow.
3. **CME gap workflow** — `cme_gap_lib` exists but no skill explains how to investigate / replay CME gap signals.
4. **Zones overlay workflow** — `zones_lib.py` shipped (C6); no skill explains how to add a new zone type, debug a zone in `GET /api/zones`, or verify chart overlay rendering.
5. **`make web-build` / `make web-dev` workflow** — Svelte+Vite frontend workflow. Phase 5 work. No skill covers "I changed a Svelte file — what now?" (Note: project-level skill does exist at `~/.claude-personal/skills/angular-frontend-dev.md` but is wrong stack — buibui is Svelte 5.)
6. **Confluence (D10) backtest** — `make buibui-backtest CONFIG=... COMBO=1 SAVE=1` is documented in MEMORY but not a skill.
7. **Profiling / perf hunt** — `analytics/perf_timer.py` exists; MEMORY P1 says "Profile all compute components." No skill exists for "use perf_timer to find a hot spot."

## Recommendation summary for Phase 1 — skills rebuild

1. **Refresh 3 skills** (path drift, scope drift): `new-strategy` (path), `config-refresh` (scope vs wfo-sweep), `sanity-check` (verify after CLI split).
2. **Adopt fairstone's frontmatter style**: multi-line `description: >`, explicit `Invoke when the user says ...` phrases, `allowed-tools` whitelist per skill.
3. **Adopt vor-stream's automation pattern**: add `.github/workflows/update-skills.yaml` with daily cron + auto-PR, even if `./update-skills.sh` starts as a stub that just runs the existing `sanity-check` "Skills list looks correct" assertion.
4. **Add 4 new skills** (high-value, low-build): `db-update`, `data-backfill`, `confluence-backtest` (D10), `frontend-svelte` (replaces wrong-stack `angular-frontend-dev` reference). Defer the rest (CME gap, zones, perf-hunt) to need-driven creation.
5. **Standardise template** for long skills: `## When to use` / `## Task` / `## Output` / `## Implementation files`. Existing skills already mostly follow this — codify it in `/skill-creator` defaults so new skills don't drift.
6. **Use `/skill-creator` for the 4 new + 3 refreshed skills** — Phase 1 implementation plan should batch these as a single PR per skill (or bundle the 4 new ones if they share scope).

Total Phase 1 skills work: ~7 skills touched (4 new + 3 refresh) + 1 CI workflow added. Roughly 1–2 days.
