# Overhaul Roadmap — buibui-moon-trader-bot

**Date:** 2026-04-25
**Status:** Approved (decomposition + sequence)
**Owner:** s10023

## Why

The codebase has accumulated a few signals worth a coordinated cleanup:

- Top-N files ballooned: `indicators_lib.py` 3,152 LOC; `signal_lib.py` 1,727; `stats_lib.py` 1,532; `data_store.py` 1,434; `backtest_lib.py` 1,422; `buibui.py` (CLI) 1,141. Logic audit and review at this size is slow.
- Tooling lags reference repos in the org: ruff rules are minimal (`E,F,I,UP`), pre-commit lacks gitleaks/check-toml, package manager is Poetry while fairstone has migrated to **uv**.
- The 21 strategies have not had a coordinated logic audit against original sources (ICT, Crabel, Nison) since most were added.
- `src/` directory exists as an empty stub — partial layout migration never completed.
- CI has paths-filter and parallel jobs, but lacks security scanning, benchmark, and the broader workflow surface used by vor-stream.

This roadmap decomposes the overhaul into 5 independent sub-projects (plus a Phase 0 triage scan) and fixes the build order based on dependencies, so each phase can be designed, planned, and shipped in isolation without redoing earlier work.

## Reference comparison

| Aspect | buibui (today) | fairstone | vor-stream | Target |
| --- | --- | --- | --- | --- |
| Package manager | Poetry | **uv** | Poetry | **stay on Poetry** (justification weak for solo project; revisit if CI cost grows) |
| ruff rules | `E,F,I,UP` | `E,W,I,UP,B,SIM,C4,PIE` | `E,F,I,PLC` | superset (Phase 1) |
| Pre-commit | ruff, ruff-format, poetry-check, markdownlint | + addlicense, gitleaks, uv-pre-commit | varies | + gitleaks, check-toml (Phase 1) |
| Layout | flat top-level | `src/` with packages | multi-package | `src/` adoption (Phase 1) |
| CI | lint+test+regression+svelte-check | uv-based | 14 workflows incl. security-scan, benchmark, claude-code-review | + security-scan, dependency-review (Phase 1) |
| Python target | 3.11 (CI on 3.13) | 3.11 | 3.11 | pin 3.13 (Phase 1) |
| Agent skills | 15 project skills, no automation | per-repo `.claude/skills/` | `update-skills.yaml` automated sync workflow | audit (Phase 0) → rebuild via `/skill-creator` (Phase 1) |

## Five-track decomposition

1. **Foundations / DX** — ruff rule expansion (`E,W,I,UP,B,SIM,C4,PIE`), pre-commit hardening (`gitleaks`, `check-toml`), CI parity (security-scan, dependency-review), repo layout (`src/` adoption), Python pin (3.13), dependabot polish, **agent skills rebuild via `/skill-creator`** based on Phase 0 audit. **Stay on Poetry.**
2. **Core code architecture** — split monsters (`indicators_lib`, `signal_lib`, `stats_lib`, `data_store`, `backtest_lib`, `buibui.py` CLI), define module boundaries between `analytics/` and `signals/`, address known perf hot-spots (P1 from MEMORY).
3. **Strategy logic audit** — 21 strategies, one-by-one logic review against original sources, edge-case coverage, parameter ranges, known-bad-combo audit. Findings feed into #4.
4. **Backtest / signal pipeline** — incorporate strategy-audit findings into `backtest_lib`, `param_sweep`, WFO chain, fixture refresh. Mostly stable; targeted improvements.
5. **UI / API** — Svelte 5 + FastAPI surface, Stats dashboard alignment (F3 from MEMORY), deep links (C10/C10b), positions write actions (E1).

## Build order (dependency-driven)

```text
Phase 0 (triage) → 1 → 2 → 3 → (4 ∥ 5)
```

| # | Phase | Risk | Rough size | Parallelizable |
| --- | --- | --- | --- | --- |
| 0a | Strategy triage scan (read-only) | None | ½ day | ∥ with 0b |
| 0b | Agent skills audit + sync research (read-only) | None | ½ day | ∥ with 0a |
| 1 | Foundations / DX (incl. skills rebuild) | Low | 1–2 weeks | — |
| 2 | Core architecture | Medium | 2–4 weeks | — |
| 3 | Strategy logic audit (full) | Medium | 3–6 weeks | After #2 |
| 4 | Backtest / signal pipeline | Medium | 1–2 weeks | ∥ with #3 once #2 done |
| 5 | UI / API | Low | 1–3 weeks | ∥ with anything |

## Why this order

| Edge | Reason |
| --- | --- |
| **1 → 2** | Broader ruff (B/SIM/C4/PIE), stricter mypy, and `src/` layout adoption all change where files live and flag bugs *while* the refactor is happening. Doing #1 second means the #2 file split happens twice. uv install speed makes #2's tight test loop faster. |
| **2 → 3** | Auditing 21 strategies inside a 3,152-line `indicators_lib.py` is brutal. After #2, each strategy is its own file with a clean test seam, making logic audit 3–5× faster and review-able PR-by-PR. |
| **3 before 4** | Strategy audit will surface new params to sweep, broken combos, and SL/TP rules that change. #4 (backtest/WFO) absorbs those findings rather than getting redone. |
| **5 parallel** | UI/API depends only on stable read-models (`/api/...`), not on internal module boundaries. Can run on its own branch any time. |
| **4 parallel-ish** | Backtest/sweep is mostly stable; only `signal_lib`/`backtest_lib` shape changes touch it. After #2 settles their public API, #4 is independent. |

## Phase 0 — read-only audits (two parallel tracks)

Two ½-day read-only passes that produce findings docs feeding later phases. No code changes. Both can run in the same fresh conversation.

**0a — Strategy triage scan.** Pass over the 21 strategies in `analytics/indicators_lib.py`, flagging obvious logic bugs, off-by-one in lookbacks, and params that diverge from the original source (ICT, Crabel, Nison). Output: `docs/superpowers/specs/2026-04-25-phase0-strategy-findings.md`. Findings feed Phase 3 but also get hot-fixed if anything is bleeding now.

**0b — Agent skills audit + sync research.** Catalogue the 15 project skills in `.claude/skills/`, audit each for staleness against current code (commands, file paths, deprecated flags), compare structure/automation patterns against vor-stream (`update-skills.yaml` workflow) and fairstone (`.claude/` layout), recommend which skills to rewrite via `/skill-creator` vs. lightly edit vs. delete. Output: `docs/superpowers/specs/2026-04-25-phase0-skills-audit.md`. Findings feed the skills-rebuild workstream in Phase 1.

Cost is a day total; the upside is catching "we've been silently broken for months" and "our skills reference removed Makefile targets" before Phase 1's mechanical work begins.

## Process

- Each phase gets its own design spec (this file is the meta-spec).
- Each spec → implementation plan via `writing-plans` skill → execute via `executing-plans`.
- main stays green after every phase. Behaviour stays unchanged except where audit findings (Phase 3+) intentionally fix bugs.

## Open questions

- (Resolved 2026-04-25) Order of phases — approved.
- (Resolved 2026-04-25) uv vs Poetry — stay on Poetry; justification weak for solo project.
- (Resolved 2026-04-25) Plan everything vs step-by-step — step-by-step. Roadmap is the high-level plan; per-phase specs only when that phase is next.
- (Resolved 2026-04-25) Skills work placement — audit in Phase 0, rebuild in Phase 1.
- (Resolved 2026-04-26) Ruff rule scope — staged in two waves: `B` alone, then `W,SIM,C4,PIE` bundled. See `2026-04-26-phase1-foundations.md`.
- (Resolved 2026-04-26) `src/` layout extent — delete the empty stub in Phase 1; real `src/` adoption rides Phase 2 alongside monster-file splits to avoid a double-rename.
- (Resolved 2026-04-26) CI workflow additions — `security-scan` + `dependency-review` in Phase 1; benchmark deferred.
- (Resolved 2026-04-26) Python version pin — hard pin `>=3.13,<3.14`. CI already 3.13-only; mypy already on 3.13.
- (Resolved 2026-04-26) Agent skills rebuild scope — 3 PRs: standardise + automation workflow / refresh 3 / add 4 new (`db-update`, `data-backfill`, `confluence-backtest`, `frontend-svelte`).

## Process per phase

1. Brainstorm phase in a fresh conversation → spec saved to `docs/superpowers/specs/`.
2. `writing-plans` skill produces an implementation plan.
3. Execute the plan in a fresh conversation. main stays green per phase.
4. Behaviour stays unchanged except where audit findings (Phase 3+) intentionally fix bugs.

## Next

Execute **Phase 0** (both tracks) in a fresh conversation. Output two findings docs. Return here to brainstorm **Phase 1 — Foundations / DX** with Phase 0 audits in hand.
