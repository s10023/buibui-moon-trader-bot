# Phase 1 — Foundations / DX (Design Spec)

**Date:** 2026-04-26
**Status:** Draft (ready for `/writing-plans`)
**Owner:** s10023
**Predecessors:** `2026-04-25-overhaul-roadmap.md`, `2026-04-25-phase0-strategy-findings.md`, `2026-04-25-phase0-skills-audit.md`

## Goal

Land tooling, lint, CI, repo-layout, Python-pin, and agent-skills baselines so Phase 2's monster-file split lands on a clean foundation. Behaviour unchanged. main green after every PR.

## Scope (in / out)

**In:** ruff rule expansion, pre-commit hardening (gitleaks, check-toml), CI parity (security-scan, dependency-review), Python pin to 3.13, dependabot polish, src/ stub disposition, agent-skills rebuild (3 refresh + 4 new + automation workflow), 3 cheap Phase-0a hot-fixes.

**Out:** monster-file splits (Phase 2), strategy-logic fixes beyond the 3 hot-fixes (Phase 3), backtest-pipeline changes (Phase 4), UI work (Phase 5), uv migration (decided no), benchmark CI (deferred).

## Open-question resolutions

### Q1. ruff rule scope — full superset day-one, or staged?

**Decision: staged, two waves.**
Current `E,F,I,UP` → target `E,W,I,UP,B,SIM,C4,PIE` (fairstone parity; note `F` is already implicit-on with ruff defaults plus we re-add it).

- **Wave 1 — `B` (bugbear) alone.** Real-bug rules (mutable defaults, B008 in FastAPI, raise-without-from). Manual fix likely; review value high.
- **Wave 2 — `W,SIM,C4,PIE` bundled.** Mostly auto-fixable; one merged PR after wave 1 settles.

Rationale: `B` is the only rule set that surfaces logic bugs. The rest are stylistic/comprehension lints that ruff `--fix` handles. Splitting `B` keeps its diff reviewable without burying real findings under whitespace churn.

Per-rule `noqa` is allowed only with a one-line reason; no blanket file-level disables.

### Q2. src/ layout — full move or partial?

**Decision: delete the empty stub now; defer real `src/` adoption to Phase 2 (bundled with monster-file splits).**

`src/buibui_moon_trader_bot/{monitor,utils}/` contains only `__pycache__` residue. A real move means renaming `analytics/`, `signals/`, `monitor/`, `utils/`, `web/`, `buibui.py`, plus updating ~all test imports, the Makefile, `pyproject.toml` `[tool.coverage.run]`, and Dockerfile. Doing it now means Phase 2 file-splits replay the same pain — exactly the "happens twice" anti-pattern the roadmap calls out for the lint/refactor edge.

Phase 1 PR: `rm -rf src/` + a one-line note in the roadmap that `src/` adoption rides with Phase 2.

### Q3. Python 3.13 pin — hard requirement or floor?

**Decision: hard pin `>=3.13,<3.14`.**

CI already runs 3.13 only; mypy `python_version = "3.13"`; solo project, no library consumers. `requires-python` and `[tool.ruff] target-version` both move to `py313`. Unlocks 3.13-only syntax (PEP 695 `type` alias, improved generics) and removes a stale-defaults trap. Risk: zero — it's already the only Python in CI.

### Q4. Skills rebuild — bundle into one PR or one PR per skill?

**Decision: 3 PRs, batched by intent.**

- **PR-S1:** `update-skills.yaml` workflow + frontmatter standardisation pass on existing 15 skills (multi-line `description: >`, explicit `Invoke when the user says ...` phrases, `allowed-tools` whitelist where meaningful — `pr-summary`, `post-branch`, `recalibrate`). Cosmetic; high-confidence.
- **PR-S2:** Refresh 3 skills — `new-strategy` (path: `tests/test_indicators.py` → `tests/test_indicators_lib.py`), `config-refresh` (re-scope to non-tp_r refresh: timeframes, day_filter, volume; defer tp_r to `wfo-sweep`), `sanity-check` (re-verify list of audited subcommands).
- **PR-S3:** Add 4 new skills — `db-update`, `data-backfill`, `confluence-backtest`, `frontend-svelte`. Generated via `/skill-creator` against the standardised template.

Rationale: 7 PRs is too slow; one mega-PR buries semantic refresh under cosmetic churn. Three buckets keep diffs small and let PR-S1's automation workflow start firing the moment it lands.

### Q5. Order of work within Phase 1

**Decision: two independent tracks, plus a small hot-fix track.**

```text
Track A (foundations/CI):  T1 → T2 → T3 → T4 → T5 → T6 → T7
Track B (skills):          S1 → S2 → S3
Track C (hot-fixes):       H1, H2, H3  (any order, parallel)
```

Tracks A and B can run truly in parallel (different files). Track C hits `analytics/indicators_lib.py` + tests; sequence after Track A's wave-1 ruff pass to avoid double-touching the same lines.

## PR-by-PR breakdown

### Track A — foundations

| PR | Title | Risk | Notes |
| --- | --- | --- | --- |
| **T1** | `chore(ci): add gitleaks + check-toml to pre-commit` | Low | Add `gitleaks/gitleaks` and `pre-commit-hooks: check-toml`. Run `pre-commit run --all-files` once; expect zero hits if no secrets ever committed. |
| **T2** | `chore(lint): ruff rule wave 1 — add B (bugbear)` | **Med** | Add `B` to `select`; run `ruff check --fix --unsafe-fixes` then manual review. Expect real findings (mutable default args, B008 FastAPI Depends, missing `from None` on raises). Each finding either fixed or `# noqa: BXXX — reason` with rationale. |
| **T3** | `chore(lint): ruff rule wave 2 — add W,SIM,C4,PIE` | Low | `ruff check --fix`; verify diff is mechanical only. |
| **T4** | `build: pin Python to 3.13` | Low | `requires-python = ">=3.13,<3.14"`, `[tool.ruff] target-version = "py313"`, drop the stale `target-version = "py311"` line. |
| **T5** | `ci: add security-scan + dependency-review workflows` | Low | Two `.github/workflows/` files mirroring vor-stream's surface (CodeQL or `aquasecurity/trivy-action`; `actions/dependency-review-action`). Required on PRs only. Skip benchmark per scope. |
| **T6** | `chore: dependabot polish` | Low | Already exists implicitly via dependabot PRs in git log. Add explicit `.github/dependabot.yml` if missing; group dev-dependency bumps into one weekly PR. |
| **T7** | `chore: remove empty src/ stub` | Low | `rm -rf src/`. Add note to roadmap that `src/` adoption rides Phase 2. |

### Track B — skills

| PR | Title | Risk | Notes |
| --- | --- | --- | --- |
| **S1** | `chore(skills): standardise frontmatter + add update-skills workflow` | Low | All 15 skills' frontmatter rewritten to `description: >` block-scalar with explicit invocation phrases. `allowed-tools` added where blast-radius matters. New `.github/workflows/update-skills.yaml` (daily cron, auto-PR on diff; `update-skills.sh` initially a stub that runs `sanity-check`'s "skills list looks correct" assertion). |
| **S2** | `chore(skills): refresh new-strategy, config-refresh, sanity-check` | Low | Path drift fix on `new-strategy`; scope re-cut on `config-refresh`; subcommand-list verify on `sanity-check`. |
| **S3** | `feat(skills): add db-update, data-backfill, confluence-backtest, frontend-svelte` | Low | Generated via `/skill-creator` against the standardised template. `frontend-svelte` replaces the wrong-stack `angular-frontend-dev` user-level skill. |

### Track C — Phase-0a hot-fixes

| PR | Title | Risk | Notes |
| --- | --- | --- | --- |
| **H1** | `fix(strategies): remove dead funding_reversion from registry` | Low | Phase-0a finding #1 (critical). `STRATEGY_REGISTRY` entry, signals registry exclusion, CLI mention, MEMORY note all removed. Detector code stays in `indicators_lib.py` with a `# kept for re-wiring once funding feed lands` comment until G2 either ships or gets cancelled. Tests for the detector stay (still valid in isolation). |
| **H2** | `test(strategies): add coverage for fib_golden_zone + ote_entry` | Low | Phase-0a finding #3 (high). Verify gap; add unit tests if missing. Pure additive — no logic change. |
| **H3** | `chore(strategies): document inside_bar containment choice` | Low | Phase-0a finding #2 (high). Decision deferred to Phase 3 backtest A/B; for Phase 1 just **document** the body-vs-high/low containment deviation in the docstring + `STRATEGY_REGISTRY` description so future readers don't trip on it. No behaviour change. |

Three other Phase-0a `[high]` items (no-future-leakage property test, smt_divergence gating, BOS docstring drift) **stay in Phase 3** — too coupled to the strategy audit to do cheaply now.

## Risk register

| Risk | Likelihood | Mitigation |
| --- | --- | --- |
| Ruff B-rule wave surfaces 50+ violations, blocks Track A | Med | Cap T2 at "review and fix all findings"; if violation count >30, split into T2a/T2b by rule. |
| `update-skills.sh` stub is meaningless and creates noisy daily PRs | Low | Stub runs only when `.claude/skills/` actually changes (`git diff --quiet` guard). No skill edits → no PR. |
| H1 (funding_reversion remove) breaks a config that references it | Low | Grep all TOML configs for `funding_reversion` before merging. Add a one-line removal-note to MEMORY. |
| security-scan workflow times out on Binance API integration tests | Low | Scope to static SAST only (no network); CodeQL handles this. |
| Python 3.13 pin breaks a transitive dep | Very low | CI already on 3.13; if it works today, it works tomorrow. |

## Acceptance criteria

- `make lint-py && make typecheck && make test` green after every PR.
- `pre-commit run --all-files` clean.
- All 15 existing skills load and invoke without warnings (manual smoke test post-S1).
- `update-skills.yaml` runs once successfully on workflow_dispatch (no PR opened, since stub is no-op).
- `funding_reversion` no longer appears in `STRATEGY_REGISTRY` or any TOML.
- `src/` directory absent from working tree.

## Build order summary

```text
T1 ─ T2 ─ T3 ─ T4 ─ T5 ─ T6 ─ T7        (foundations, sequential)
                                          ↑ each lands independently
S1 ─ S2 ─ S3                              (skills, sequential within track,
                                          ║  parallel to A)
H1, H2, H3                                (hot-fixes, parallel to each other,
                                          start after T2 so ruff B doesn't
                                          re-touch the same lines)
```

Total: **13 PRs**. Estimated 1–2 calendar weeks at solo pace.

---

Ready for `/writing-plans` to produce the implementation plan.
