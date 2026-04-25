# Phase 1 — Foundations / DX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land tooling, lint, CI, repo-layout, Python-pin, and agent-skills baselines so Phase 2's monster-file split lands on a clean foundation. Behaviour unchanged. main green after every PR.

**Architecture:** 13 PRs across 3 independent tracks. Track A = foundations/CI sequential T1→T7. Track B = skills sequential S1→S3 (parallel to A). Track C = three independent hot-fixes from Phase 0a, started after T2 to avoid overlapping ruff lines.

**Tech Stack:** Poetry, ruff 0.15.x, mypy strict, pre-commit, pytest, GitHub Actions, DuckDB, FastAPI, Svelte 5.

---

## Source documents

- Spec: `docs/superpowers/specs/2026-04-26-phase1-foundations.md`
- Phase 0a strategy findings: `docs/superpowers/specs/2026-04-25-phase0-strategy-findings.md`
- Phase 0b skills audit: `docs/superpowers/specs/2026-04-25-phase0-skills-audit.md`
- Roadmap: `docs/superpowers/specs/2026-04-25-overhaul-roadmap.md`

## Branching convention

Each task ships as one PR off `main`. Branch names follow existing convention: `chore/`, `build:`, `fix/`, `feat/`, `test/`, `docs/`. Run `/post-branch` and `/pr-summary` skills after each branch lands.

After **every** code-touching task, the engineer must run:

```bash
make lint-py && make typecheck && make test
```

All three must pass before commit. Any task that breaks any of these blocks the merge.

## File map

| Path | Action per task | Owner task |
| --- | --- | --- |
| `.pre-commit-config.yaml` | Modify | T1 |
| `pyproject.toml` | Modify (ruff `select`, ruff `target-version`, `requires-python`) | T2, T3, T4 |
| `.github/workflows/security-scan.yaml` | Create | T5 |
| `.github/workflows/dependency-review.yaml` | Create | T5 |
| `.github/dependabot.yml` | Modify | T6 |
| `src/` | Delete | T7 |
| `.github/workflows/update-skills.yaml` | Create | S1 |
| `.github/scripts/update-skills.sh` | Create | S1 |
| `.claude/skills/*/SKILL.md` (15 files) | Modify (frontmatter) | S1 |
| `.claude/skills/new-strategy/SKILL.md` | Modify (path fix) | S2 |
| `.claude/skills/config-refresh/SKILL.md` | Modify (re-scope) | S2 |
| `.claude/skills/sanity-check/SKILL.md` | Modify (subcommand list) | S2 |
| `.claude/skills/db-update/SKILL.md` | Create | S3 |
| `.claude/skills/data-backfill/SKILL.md` | Create | S3 |
| `.claude/skills/confluence-backtest/SKILL.md` | Create | S3 |
| `.claude/skills/frontend-svelte/SKILL.md` | Create | S3 |
| `analytics/indicators_lib.py` | Modify (`STRATEGY_REGISTRY` line 218–250 region; comment block 3128) | H1 |
| `signals/registry.py` | Modify (header comment + `SIGNAL_REGISTRY`) | H1 |
| `config/signal_watch.toml`, `config/signal_watch_all.toml`, `config/signal_watch_weekdays.toml` | Modify (remove `'funding_reversion'`) | H1 |
| `tests/test_indicators_lib.py`, `tests/test_signal_lib.py`, `tests/test_signal_registry.py`, `tests/test_signal_config.py`, `tests/test_regression.py` | Verify still pass after H1 | H1 |
| `docs/superpowers/specs/2026-04-26-phase1-foundations.md` | Annotate | H2 |
| `analytics/indicators_lib.py` (`detect_inside_bar` docstring + `STRATEGY_REGISTRY['inside_bar'].description`) | Modify | H3 |
| `MEMORY.md` (root: `~/.claude-personal/projects/.../memory/MEMORY.md`) | Append session-state row per branch | every task |

---

## Track A — Foundations

### Task T1: Pre-commit hardening (gitleaks + check-toml)

**Files:**

- Modify: `.pre-commit-config.yaml`

- [ ] **Step 1: Read current `.pre-commit-config.yaml`**

Use Read tool. Confirm structure: `pre-commit-hooks` block already present at top.

- [ ] **Step 2: Add `check-toml` to existing pre-commit-hooks block**

Edit `.pre-commit-config.yaml` — under the existing `https://github.com/pre-commit/pre-commit-hooks` repo's `hooks:` list, add:

```yaml
      - id: check-toml
```

Insert after `- id: check-json`. Final block reads:

```yaml
      - id: check-yaml
      - id: check-json
      - id: check-toml
      - id: check-added-large-files
        args: ['--maxkb=1200']
      - id: check-merge-conflict
```

- [ ] **Step 3: Add gitleaks repo block**

Append a new repo block after the markdownlint block:

```yaml
  - repo: https://github.com/gitleaks/gitleaks
    rev: v8.21.2
    hooks:
      - id: gitleaks
```

- [ ] **Step 4: Install hooks locally and run against full repo**

```bash
pre-commit install
pre-commit run --all-files
```

Expected: all hooks pass. If `gitleaks` finds anything, **STOP** — investigate and rotate any leaked secret before continuing. Do not bypass.

- [ ] **Step 5: Run lint/typecheck/tests**

```bash
make lint-py && make typecheck && make test
```

Expected: all green.

- [ ] **Step 6: Commit**

```bash
git checkout -b chore/precommit-gitleaks-checktoml
git add .pre-commit-config.yaml
git commit -m "chore(precommit): add gitleaks + check-toml hooks"
```

Run `/pr-summary` and `/post-branch` skills. Open PR, merge.

---

### Task T2: Ruff rule wave 1 — add `B` (bugbear)

**Files:**

- Modify: `pyproject.toml` (line 108 `[tool.ruff.lint] select`)
- Modify: any source files flagged by new rules

- [ ] **Step 1: Add `B` to ruff select**

Edit `pyproject.toml`:

```toml
[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]
ignore = ["E501"]
```

- [ ] **Step 2: Run ruff with --fix and capture remaining violations**

```bash
poetry run ruff check --fix --unsafe-fixes .
poetry run ruff check . > /tmp/ruff_b_violations.txt 2>&1 || true
wc -l /tmp/ruff_b_violations.txt
```

If violation count >30, split this task into T2a (handle B0xx subset) and T2b (B9xx). Otherwise continue.

- [ ] **Step 3: Categorise findings**

Bucket the remaining violations by code:

- `B006` mutable-default-argument → fix to `None`-sentinel pattern
- `B008` function-call-in-default → for FastAPI, the user-pattern `Depends(...)` is intentional; add per-line `# noqa: B008 — FastAPI Depends pattern` only where needed
- `B904` raise-without-from → add `from None` or `from exc`
- `B007` unused-loop-variable → rename to `_var`
- `B023` function-uses-loop-variable → fix the closure
- Anything else → fix per ruff doc guidance

For each violation: prefer fix; reach for `# noqa: BXXX — <one-line reason>` only when the rule genuinely doesn't apply.

- [ ] **Step 4: Apply fixes file-by-file**

Use Edit tool for each fix. Re-run `poetry run ruff check .` after each file until clean.

- [ ] **Step 5: Run full quality gate**

```bash
make lint-py && make typecheck && make test
```

Expected: all green. The test suite is the real safety net — `B` rules occasionally flag working code as buggy; the tests confirm behaviour didn't shift.

- [ ] **Step 6: Commit**

```bash
git checkout -b chore/ruff-add-bugbear
git add -A
git commit -m "chore(lint): enable ruff bugbear (B) rules"
```

Open PR. **Reviewer focus:** every B-rule fix should be either a real correctness improvement or a `noqa` with a reason. No silent diffs.

---

### Task T3: Ruff rule wave 2 — add `W,SIM,C4,PIE`

**Files:**

- Modify: `pyproject.toml`
- Modify: source files flagged by new rules (mostly auto-fixable)

- [ ] **Step 1: Expand ruff select to full target**

Edit `pyproject.toml`:

```toml
[tool.ruff.lint]
select = ["E", "W", "F", "I", "UP", "B", "SIM", "C4", "PIE"]
ignore = ["E501"]
```

- [ ] **Step 2: Run auto-fix**

```bash
poetry run ruff check --fix .
poetry run ruff format .
poetry run ruff check . > /tmp/ruff_wave2.txt 2>&1 || true
cat /tmp/ruff_wave2.txt
```

Most violations auto-fix. Any remaining are likely `SIM108` (use ternary) or `C401` (unnecessary list comp) — fix manually.

- [ ] **Step 3: Sanity-skim the diff**

```bash
git diff --stat
git diff | head -200
```

Expect: small mechanical changes — comprehension rewrites, ternaries, dict-merge `|`, redundant `bool()` removed. If you see anything semantic (logic flip, condition reorder), revert that file and audit.

- [ ] **Step 4: Run full quality gate**

```bash
make lint-py && make typecheck && make test
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git checkout -b chore/ruff-add-w-sim-c4-pie
git add -A
git commit -m "chore(lint): enable ruff W,SIM,C4,PIE rules"
```

Open PR.

---

### Task T4: Pin Python to 3.13

**Files:**

- Modify: `pyproject.toml` (lines 9, 105)

- [ ] **Step 1: Update `requires-python`**

Edit `pyproject.toml`:

```toml
requires-python = ">=3.13,<3.14"
```

- [ ] **Step 2: Update ruff target-version**

Edit `pyproject.toml`:

```toml
[tool.ruff]
target-version = "py313"
```

- [ ] **Step 3: Confirm mypy already on 3.13**

Read `pyproject.toml` lines 77–80; confirm `python_version = "3.13"` is unchanged.

- [ ] **Step 4: Refresh poetry lock**

```bash
poetry lock --no-update
poetry install --no-root
```

Expected: lock regenerates without changing dep versions; install succeeds. If a transitive dep barks on 3.13, surface it in the PR description and decide whether to upgrade or pin.

- [ ] **Step 5: Run full quality gate**

```bash
make lint-py && make typecheck && make test
```

Expected: all green.

- [ ] **Step 6: Commit**

```bash
git checkout -b build/pin-python-3.13
git add pyproject.toml poetry.lock
git commit -m "build: pin Python to >=3.13,<3.14"
```

Open PR.

---

### Task T5: Add security-scan and dependency-review CI workflows

**Files:**

- Create: `.github/workflows/security-scan.yaml`
- Create: `.github/workflows/dependency-review.yaml`

- [ ] **Step 1: Create `security-scan.yaml`**

Write `.github/workflows/security-scan.yaml`:

```yaml
name: security-scan

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

permissions:
  contents: read
  security-events: write

jobs:
  codeql:
    name: CodeQL
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: github/codeql-action/init@v3
        with:
          languages: python
      - uses: github/codeql-action/analyze@v3
        with:
          category: "/language:python"

  trivy:
    name: Trivy filesystem scan
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: aquasecurity/trivy-action@master
        with:
          scan-type: fs
          scan-ref: .
          severity: CRITICAL,HIGH
          exit-code: '1'
          ignore-unfixed: true
```

- [ ] **Step 2: Create `dependency-review.yaml`**

Write `.github/workflows/dependency-review.yaml`:

```yaml
name: dependency-review

on:
  pull_request:
    branches: [main]

permissions:
  contents: read
  pull-requests: write

jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/dependency-review-action@v4
        with:
          fail-on-severity: high
          comment-summary-in-pr: on-failure
```

- [ ] **Step 3: Validate YAML**

```bash
poetry run python -c "import yaml; yaml.safe_load(open('.github/workflows/security-scan.yaml'))"
poetry run python -c "import yaml; yaml.safe_load(open('.github/workflows/dependency-review.yaml'))"
```

Expected: no output, no exception.

- [ ] **Step 4: Commit**

```bash
git checkout -b ci/add-security-scan-and-dependency-review
git add .github/workflows/security-scan.yaml .github/workflows/dependency-review.yaml
git commit -m "ci: add security-scan and dependency-review workflows"
```

Open PR. The workflows fire on this PR itself — confirm both pass before merging. If `trivy` flags an unfixable HIGH/CRITICAL in a transitive dep, allowlist it in the workflow with a one-line `--skip-dirs` or `.trivyignore` entry plus a tracking issue.

---

### Task T6: Dependabot polish

**Files:**

- Modify: `.github/dependabot.yml`

- [ ] **Step 1: Read current dependabot.yml**

Confirm structure (already exists per scout: `package-ecosystem: pip`, weekly, direct deps only).

- [ ] **Step 2: Add github-actions ecosystem and group dev deps**

Edit `.github/dependabot.yml` to:

```yaml
version: 2
updates:
  - package-ecosystem: "pip"
    directory: "/"
    schedule:
      interval: "weekly"
    allow:
      - dependency-type: "direct"
    open-pull-requests-limit: 5
    groups:
      dev-dependencies:
        dependency-type: "development"
      runtime-dependencies:
        dependency-type: "production"

  - package-ecosystem: "github-actions"
    directory: "/"
    schedule:
      interval: "weekly"
    open-pull-requests-limit: 3
```

- [ ] **Step 3: Validate YAML**

```bash
poetry run python -c "import yaml; yaml.safe_load(open('.github/dependabot.yml'))"
```

Expected: no exception.

- [ ] **Step 4: Commit**

```bash
git checkout -b chore/dependabot-grouping
git add .github/dependabot.yml
git commit -m "chore(dependabot): group dev deps + add github-actions ecosystem"
```

Open PR.

---

### Task T7: Remove empty `src/` stub

**Files:**

- Delete: `src/`

- [ ] **Step 1: Confirm src/ contents are non-essential**

```bash
find src -type f
```

Expected output: empty (only `__pycache__` directories with no real `.py` files). If any `.py` file appears, **STOP** — that's an in-flight refactor; investigate before deleting.

- [ ] **Step 2: Delete src/**

```bash
rm -rf src/
```

- [ ] **Step 3: Confirm no source references src/**

```bash
grep -rn "from src\.\|import src\." --include="*.py" .
grep -rn "src/buibui_moon_trader_bot" --include="*.py" --include="*.toml" --include="*.yml" --include="*.yaml" --include="Makefile" --include="*.json" .
```

Expected: no hits.

- [ ] **Step 4: Run full quality gate**

```bash
make lint-py && make typecheck && make test
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git checkout -b chore/remove-empty-src-stub
git add -A
git commit -m "chore: remove empty src/ stub (real layout migration rides Phase 2)"
```

Open PR.

---

## Track B — Skills

### Task S1: Standardise frontmatter + add update-skills automation

**Files:**

- Modify: 15 files under `.claude/skills/*/SKILL.md`
- Create: `.github/workflows/update-skills.yaml`
- Create: `.github/scripts/update-skills.sh`

- [ ] **Step 1: List the 15 skills**

```bash
ls .claude/skills/
```

Expected: `atr-sweep backtest-findings backtest-run config-refresh investigate-strategy new-strategy param-sweep-apply post-branch pr-summary recalibrate sanity-check signal-watch stats-dashboard volume-sweep wfo-sweep`.

- [ ] **Step 2: Define the standardised frontmatter template**

Each `SKILL.md` adopts:

```yaml
---
name: <skill-name>
description: >
  <2–4 sentences describing what the skill does and why.
  Invoke when the user says "/<skill-name>" or asks "<example phrase 1>",
  "<example phrase 2>", or needs <other trigger phrase>.
allowed-tools: <tool whitelist or `*` if broad>
---
```

`allowed-tools` whitelist suggestions (per Phase 0b):

| Skill | allowed-tools |
| --- | --- |
| `pr-summary` | `Bash, Write, Read` |
| `post-branch` | `Bash, Read, Edit` |
| `recalibrate` | `Bash` |
| `backtest-run` | `Bash, Read` |
| `atr-sweep`, `volume-sweep`, `wfo-sweep`, `param-sweep-apply`, `config-refresh` | `Bash, Read, Edit, Write` |
| `new-strategy`, `signal-watch`, `stats-dashboard`, `investigate-strategy` | `*` |
| `backtest-findings`, `sanity-check` | `Bash, Read, Edit` |

- [ ] **Step 3: Apply template to each of 15 skills**

For each `SKILL.md`: keep existing body content unchanged; only rewrite the frontmatter to the new template, preserving the existing `description` content but reformatted as block-scalar with explicit invocation phrases drawn from the existing CLAUDE.md skill table.

- [ ] **Step 4: Create `.github/scripts/update-skills.sh`**

Write `.github/scripts/update-skills.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

# Stub: validate every SKILL.md parses as YAML frontmatter + body.
# Future: pull updates from a shared skill registry.

SKILLS_DIR=".claude/skills"
fail=0

for skill_md in "$SKILLS_DIR"/*/SKILL.md; do
  if ! head -1 "$skill_md" | grep -q '^---$'; then
    echo "ERR: $skill_md missing frontmatter delimiter"
    fail=1
  fi
done

exit "$fail"
```

```bash
chmod +x .github/scripts/update-skills.sh
```

- [ ] **Step 5: Create `.github/workflows/update-skills.yaml`**

```yaml
name: update-skills

on:
  schedule:
    - cron: "0 3 * * *"
  workflow_dispatch:

permissions:
  contents: write
  pull-requests: write

jobs:
  sync:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Run skill sync
        run: ./.github/scripts/update-skills.sh
      - name: Open PR if changes
        run: |
          if git diff --quiet; then
            echo "No skill changes."
            exit 0
          fi
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git checkout -b chore/update-agent-skills
          git add .
          git commit -m "chore(skills): automated sync"
          git push origin chore/update-agent-skills
          gh pr create --title "chore(skills): automated sync" \
            --body "Automated sync from update-skills workflow." \
            --base main --head chore/update-agent-skills
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

- [ ] **Step 6: Local smoke test**

```bash
bash ./.github/scripts/update-skills.sh
echo "exit=$?"
```

Expected: `exit=0`, no output (all SKILL.md frontmatter valid).

- [ ] **Step 7: Run full quality gate (lint-md only — no Python changed)**

```bash
make lint-md
```

Expected: pass (markdownlint may flag long block-scalars; if so, allow `MD013` per-line via `<!-- markdownlint-disable-line MD013 -->` only inside the frontmatter content, never above the `---` delimiters).

- [ ] **Step 8: Commit**

```bash
git checkout -b chore/skills-frontmatter-and-automation
git add .claude/skills/ .github/workflows/update-skills.yaml .github/scripts/update-skills.sh
git commit -m "chore(skills): standardise frontmatter + add update-skills workflow"
```

Open PR. Manually trigger the workflow once via `gh workflow run update-skills.yaml` and confirm it exits successfully without opening a PR.

---

### Task S2: Refresh new-strategy, config-refresh, sanity-check

**Files:**

- Modify: `.claude/skills/new-strategy/SKILL.md`
- Modify: `.claude/skills/config-refresh/SKILL.md`
- Modify: `.claude/skills/sanity-check/SKILL.md`

- [ ] **Step 1: Fix `new-strategy` test path**

```bash
grep -n "tests/test_indicators\.py" .claude/skills/new-strategy/SKILL.md
```

For each hit, edit to `tests/test_indicators_lib.py`. Verify:

```bash
grep -n "tests/test_indicators" .claude/skills/new-strategy/SKILL.md
```

All hits should now read `tests/test_indicators_lib.py`.

- [ ] **Step 2: Re-scope `config-refresh`**

Open `.claude/skills/config-refresh/SKILL.md`. Rewrite the **When to use** section to clarify scope is now non-tp_r refresh:

```markdown
## When to use

Use this skill when a `signal_watch*.toml` config has drifted on **non-tp_r** dimensions:

- Strategy timeframe gaps after adding a new strategy
- `day_filter` toggle changes
- `volume_suppress` / `volume_spike_boost` flags

For **tp_r refresh**, use `/wfo-sweep` instead — it runs a full WFO IS/OOS pipeline
which `config-refresh` does not. `config-refresh` uses full-dataset sweep (no OOS),
which is fine for non-tp_r dimensions but produces overfit tp_r values.
```

Remove any tp_r-sweep step from the skill body. Preserve the timeframe-gap and flag-refresh logic.

- [ ] **Step 3: Verify `sanity-check` subcommand list**

Open `.claude/skills/sanity-check/SKILL.md`. Find the section that lists `buibui` CLI subcommands. Cross-check against current `buibui.py`:

```bash
poetry run python buibui.py --help 2>&1 | head -30
```

Update the skill's listed subcommands to match exactly: `monitor`, `signal`, `analytics`, `backtest`, `digest`, `param-audit`, `param-sweep`, `recalibrate`, `web`. Drop any stale entries.

- [ ] **Step 4: Run lint-md**

```bash
make lint-md
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git checkout -b chore/skills-refresh-three
git add .claude/skills/new-strategy/SKILL.md .claude/skills/config-refresh/SKILL.md .claude/skills/sanity-check/SKILL.md
git commit -m "chore(skills): refresh new-strategy path, config-refresh scope, sanity-check subcommands"
```

Open PR.

---

### Task S3: Add 4 new skills

**Files:**

- Create: `.claude/skills/db-update/SKILL.md`
- Create: `.claude/skills/data-backfill/SKILL.md`
- Create: `.claude/skills/confluence-backtest/SKILL.md`
- Create: `.claude/skills/frontend-svelte/SKILL.md`

- [ ] **Step 1: Author `db-update`**

Create `.claude/skills/db-update/SKILL.md` with:

- Frontmatter (name, multi-line description, `allowed-tools: Bash, Read`)
- **When to use:** after backtest/strategy changes; routine DB refresh.
- **Task:** Run `make db-update`, interpret regression diff.
- **Output:** PR-ready summary of changed star ratings + regression deltas.
- **Implementation files:** `Makefile` (`db-update`, `db-update-backtest`, `db-update-recalibrate`, `regression-update` targets); `tests/test_regression.py`; `tests/fixtures/`.

- [ ] **Step 2: Author `data-backfill`**

Create `.claude/skills/data-backfill/SKILL.md` with:

- Frontmatter (name, description with explicit `Invoke when the user says "/data-backfill" or "backfill OHLCV"`).
- **When to use:** DB wipe, new symbol, gap detected.
- **Task:** Run `buibui analytics backfill --since YYYY-MM-DD`; document `SINCE=2025-09-12` baseline from MEMORY.
- **Output:** Symbol×timeframe coverage report.
- **Implementation files:** `analytics/data_fetcher.py`, `analytics/data_sync.py`, `analytics/analytics_runner.py`.

- [ ] **Step 3: Author `confluence-backtest`**

Create `.claude/skills/confluence-backtest/SKILL.md` with:

- Frontmatter (name, description, `allowed-tools: Bash, Read, Edit`).
- **When to use:** D10 cross-strategy combo refresh; `backtest_combos` table feels stale.
- **Task:** Run `make buibui-backtest CONFIG=config/signal_watch.toml COMBO=1 SAVE=1`; interpret `analytics_runner` output.
- **Output:** Combo win-rate table + recommended INCOMPATIBLE_PAIRS additions.
- **Implementation files:** `analytics/backtest_lib.py`, `analytics/backtest_runner.py`, `analytics/backtest_config.py`.

- [ ] **Step 4: Author `frontend-svelte`**

Create `.claude/skills/frontend-svelte/SKILL.md` with:

- Frontmatter (name, description with `Invoke when the user says ".svelte file changed" or asks about "Vite dev server"`).
- **When to use:** any change to `web/ui/`.
- **Task:** `make web-dev` for live, `make web-build` before merging; verify in browser.
- **Output:** Lighthouse-style smoke checklist: build green, type-check via `svelte-check` green, golden-path UX walkthrough.
- **Implementation files:** `web/ui/`, `web/api/`, `vite.config.js`, `package.json`.

- [ ] **Step 5: Verify all four skills load**

```bash
bash ./.github/scripts/update-skills.sh
```

Expected: `exit=0`.

- [ ] **Step 6: Run lint-md**

```bash
make lint-md
```

Expected: pass.

- [ ] **Step 7: Commit**

```bash
git checkout -b feat/skills-add-four-new
git add .claude/skills/db-update .claude/skills/data-backfill .claude/skills/confluence-backtest .claude/skills/frontend-svelte
git commit -m "feat(skills): add db-update, data-backfill, confluence-backtest, frontend-svelte"
```

Open PR. Update `CLAUDE.md` skills table in a follow-up commit on the same branch — add the four new rows.

---

## Track C — Phase-0a hot-fixes

### Task H1: Remove dead `funding_reversion` from registry

**Files:**

- Modify: `analytics/indicators_lib.py` (lines ~218–250 region — `STRATEGY_REGISTRY['funding_reversion']` block; comment block near line 3128)
- Modify: `signals/registry.py` (header comment + `SIGNAL_REGISTRY` if present)
- Modify: `config/signal_watch.toml`, `config/signal_watch_all.toml`, `config/signal_watch_weekdays.toml` (remove `'funding_reversion'` from each)
- Modify: `tests/test_signal_registry.py`, `tests/test_signal_lib.py`, `tests/test_signal_config.py`, `tests/test_indicators_lib.py`, `tests/test_regression.py` — adjust any test asserting `funding_reversion` membership

Note: `detect_funding_extreme` function and its unit tests (`TestDetectFundingExtreme`) **stay** — they're valid in isolation. Only the registry wiring is removed.

- [ ] **Step 1: Read the STRATEGY_REGISTRY entry to remove**

Use Read on `analytics/indicators_lib.py` lines 215–260 to capture exact entry boundaries.

- [ ] **Step 2: Remove `funding_reversion` from `STRATEGY_REGISTRY`**

Use Edit to delete the entire `"funding_reversion": StrategySpec(...)` dict entry. Verify the surrounding entries still parse cleanly.

- [ ] **Step 3: Update line ~3128 comment**

The existing comment reads `# Strategies that require extra data (funding_reversion → funding rates,`. Edit to remove the `funding_reversion → funding rates,` clause; preserve the rest of the comment.

- [ ] **Step 4: Strip `funding_reversion` from `signals/registry.py` header**

Open `signals/registry.py`. The header comment lists `funding_reversion: requires live funding rate feed; fetch_funding_rates() is not …`. Remove that bullet. Confirm `SIGNAL_REGISTRY` itself has no `funding_reversion` entry already (it shouldn't — Phase 0b confirmed it's excluded from actionable strategies).

- [ ] **Step 5: Strip `'funding_reversion'` from all three TOMLs**

For each of `config/signal_watch.toml`, `config/signal_watch_all.toml`, `config/signal_watch_weekdays.toml`:

```bash
grep -n "'funding_reversion'" config/signal_watch.toml config/signal_watch_all.toml config/signal_watch_weekdays.toml
```

For each hit, Edit to remove the entry from the array. Mind trailing commas — keep the array syntactically valid.

- [ ] **Step 6: Run tests; expect failures, then patch**

```bash
make test
```

Any test asserting `'funding_reversion' in STRATEGY_REGISTRY` or `'funding_reversion' in active_strategies` will fail. Update assertions:

- `tests/test_signal_registry.py` — remove or invert any "funding_reversion is registered" assertion to "funding_reversion is NOT registered."
- `tests/test_signal_config.py` — drop fixture entries.
- `tests/test_signal_lib.py` — same.
- `tests/test_indicators_lib.py` — drop registry membership check.
- `tests/test_regression.py` — golden fixtures may include funding_reversion zero-trade rows. Run `make regression-update` after H1 lands so future regression baselines drop the column. Document this in the PR.

- [ ] **Step 7: Re-run full quality gate**

```bash
make lint-py && make typecheck && make test
```

Expected: all green. If `test_regression` fails because golden parquet still has the strategy, run `make regression-update` once and commit the refreshed fixtures.

- [ ] **Step 8: Update CLAUDE.md and MEMORY.md**

- `CLAUDE.md`: change "21 entries in `STRATEGY_REGISTRY`" → "20 entries"; update `(18 detectors)` if changed; update "21 strategies / 18 detectors / 19 actionable" line.
- MEMORY.md: append a session row noting registry now 20 strategies; remove "funding_reversion (P3)" from Deferred Issues.

- [ ] **Step 9: Commit**

```bash
git checkout -b fix/remove-dead-funding-reversion
git add -A
git commit -m "fix(strategies): remove dead funding_reversion from registry"
```

Open PR.

---

### Task H2: Verify `fib_golden_zone` + `ote_entry` test coverage

**Files:**

- Modify (annotation only): `docs/superpowers/specs/2026-04-25-phase0-strategy-findings.md`

- [ ] **Step 1: Confirm coverage exists in `tests/test_fib_strategies.py`**

```bash
grep -c "detect_fib_golden_zone\|detect_ote_entry" tests/test_fib_strategies.py
```

Expected: ≥6 hits. Confirms Phase 0a's "test coverage gap" was a false alarm — the tests live in `test_fib_strategies.py`, not in `test_candle_patterns.py`'s import list which Phase 0a inspected.

- [ ] **Step 2: Run the file in isolation to confirm green**

```bash
poetry run pytest tests/test_fib_strategies.py -v
```

Expected: all pass.

- [ ] **Step 3: Annotate the Phase 0a finding doc**

Edit `docs/superpowers/specs/2026-04-25-phase0-strategy-findings.md`. Find the cross-cutting bullet beginning "**No detector unit-tests directory `tests/test_indicators_lib.py` for `fib_golden_zone` and `ote_entry`?**" Append to that bullet:

```markdown

> **2026-04-26 update (H2):** Verified — coverage exists in `tests/test_fib_strategies.py`
> (D3 + D4 sections, ≥6 detector calls). The Phase 0a inspection only scanned
> `test_candle_patterns.py` imports. No new tests needed.
```

Also update the "Top-N critical/high findings" #3 entry to mark it resolved.

- [ ] **Step 4: Commit**

```bash
git checkout -b docs/h2-verify-fib-ote-coverage
git add docs/superpowers/specs/2026-04-25-phase0-strategy-findings.md
git commit -m "docs(phase0): mark fib_golden_zone+ote_entry coverage gap as false alarm (H2)"
```

Open PR.

---

### Task H3: Document `inside_bar` containment choice

**Files:**

- Modify: `analytics/indicators_lib.py` (`detect_inside_bar` docstring + `STRATEGY_REGISTRY['inside_bar'].description`)

- [ ] **Step 1: Locate `detect_inside_bar`**

```bash
grep -n "def detect_inside_bar\|inside_bar.*StrategySpec" analytics/indicators_lib.py
```

- [ ] **Step 2: Update detector docstring**

Read the function's current docstring. Rewrite to add an explicit note. Example added paragraph:

```python
"""Detect inside-bar continuation pattern.

Note on containment: this detector uses **body-only containment** —
current bar's max(open, close) ≤ prev bar's max(open, close) AND
current bar's min(open, close) ≥ prev bar's min(open, close). This
deviates from the canonical Nison/price-action definition which uses
high/low containment (high ≤ prev high AND low ≥ prev low).

The body-only choice avoids wick noise but reduces signal count materially
on volatile bars. A/B backtest deferred to Phase 3 strategy audit.
"""
```

- [ ] **Step 3: Update `STRATEGY_REGISTRY['inside_bar'].description`**

Find the spec entry. Append " (uses body-only containment vs canonical high/low — see detector docstring)" to the existing description string.

- [ ] **Step 4: Run full quality gate**

```bash
make lint-py && make typecheck && make test
```

Expected: all green. No behaviour change — only docstring + description string.

- [ ] **Step 5: Commit**

```bash
git checkout -b chore/inside-bar-document-containment
git add analytics/indicators_lib.py
git commit -m "chore(strategies): document inside_bar body-only containment choice (H3)"
```

Open PR.

---

## Final acceptance check

After all 13 PRs merge, run on `main`:

- [ ] `make lint-py && make typecheck && make test` — all green
- [ ] `pre-commit run --all-files` — all hooks pass
- [ ] `gh workflow run update-skills.yaml --ref main` — workflow succeeds, no PR opened (stub no-op)
- [ ] `grep -rn "funding_reversion" config/*.toml analytics/indicators_lib.py signals/registry.py` — no hits except inside detector function body and its tests
- [ ] `find src -type d` — directory absent
- [ ] `python -c "import sys; assert sys.version_info >= (3, 13)"` (Poetry env)
- [ ] `ls .claude/skills/ | wc -l` — `19` (15 existing + 4 new)

Update MEMORY.md current-state row marking Phase 1 complete and pointing forward to Phase 2 brainstorm.

---

Plan complete.
