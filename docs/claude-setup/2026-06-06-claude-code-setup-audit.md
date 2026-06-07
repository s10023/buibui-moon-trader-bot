# Claude Code Setup Audit & Top-Tier Upgrade Plan

**Date:** 2026-06-06
**Scope:** Best-in-class Claude Code setup — general + tailored to buibui-moon-trader-bot.
**Researched sources:** [skills.sh](https://www.skills.sh/) ecosystem, [obra/superpowers](https://github.com/obra/superpowers), [hesreallyhim/awesome-claude-code](https://github.com/hesreallyhim/awesome-claude-code), Claude Code docs (hooks, statusline, best-practices).

---

## TL;DR — you are ~80% maxed already

You already run the hard-to-set-up pieces most people lack:

- **context-mode plugin** (raw tool output stays in a sandbox; FTS5-indexed; `ctx_*` tools) — top-tier token efficiency.
- **Anthropic baseline skill suite**: `brainstorming`, `test-driven-development`, `writing-plans`, `executing-plans`, `writing-skills`, `skill-creator`, `find-skills`, `code-simplifier`.
- **A deep project-skill library** (~25 buibui skills: sweeps, recalibrate, post-branch, sanity-check, journal-trade…) + a disciplined `CLAUDE.md` + file-based **memory protocol**.
- **Specialist agents** (code-architect, code-explorer, code-reviewer, claude-code-guide) and plan mode.

The honest gap is **3 things**, all directly mapped to your stated goals:

| Your goal | Gap | Fix (priority) |
| --- | --- | --- |
| Top-tier guardrails vs destructive mistakes | No PreToolUse safety hook | **P0** safety-net hook |
| Live token progress bar, no manual website checks | No statusline | **P0** ccusage/ccstatusline statusline |
| Best architecture / autonomous multi-step work | Missing superpowers *orchestration* skills | **P1** full superpowers plugin |
| Prompt-engineering mastery → guide high-quality work | CLAUDE.md is process-doc, not a persona/quality contract | **P1** output-style + prompt patterns |
| Max token efficiency | Have the tools; lack the *discipline rules* | **P1** context-budget conventions |

---

## Is this overkill? — honest verdict

**Mostly no — with one caveat.** The dividing line: tooling that is *passive, cheap, and prevents catastrophe* is always worth it; tooling that *adds process overhead per task* is overkill for a focused solo project.

| Item | Verdict |
| --- | --- |
| context-mode, skills, memory (already have) | Good practice — earns its keep on a codebase this size. Not overkill. |
| Safety hook + token statusline + CLAUDE.md contract | **Good practice.** Near-zero ongoing cost, direct payoff (you have an unrecoverable gitignored DB + a documented live-Telegram footgun). Do them. |
| Superpowers **full autonomous** orchestration (subagent-driven-dev, worktrees, multi-hour autonomous runs) | **Mild overkill if adopted wholesale.** You already have brainstorming/TDD/plans + post-branch/pr-summary/sanity-check. Cherry-pick `systematic-debugging` + `verification-before-completion` + `requesting/receiving-code-review`; skip the full driver unless you scale to large multi-day features. |
| Mega orchestrators (`ruflo`, `sudocode`) | **Overkill** for a solo focused project. Study the patterns; don't adopt. |

Bottom line: you're not over-tooling — you're *under-guardrailed and under-instrumented*. Fixing P0 closes that. Resist the urge to bolt on heavyweight multi-agent frameworks.

## Trust vetting (GitHub stars + maintenance, checked 2026-06-06)

Your rule — high stars, well-maintained, no random 0-star/brand-new repos — applied:

| Repo | Stars | Last push | Verdict |
| --- | --- | --- | --- |
| obra/superpowers | 219k | 2026-06-03 | ✅ Trust (canonical, active) |
| jarrodwatts/claude-hud | 24.6k | 2026-06-04 | ✅ Trust |
| ryoppippi/ccusage | 15.6k | 2026-06-06 | ✅ Trust |
| sirmalloc/ccstatusline | 10.3k | 2026-06-02 | ✅ Trust |
| kenryu42/cc-safety-net | 1.4k | 2026-06-05 | 🟡 OK (solid, active) — optional layer |
| disler/claude-code-damage-control | 473 | 2026-01-04 | ⚠️ Stale (~5mo) — skip |
| ldayton/Dippy | 235 | 2026-03-29 | ⚠️ Low + ~2.5mo stale — skip |
| zcaceres/claude-rm-rf | 36 | 2025-12-18 | ❌ Reject (low + stale) |
| **cleyton1986/claude-usage-bar** | **0** | 2026-05-25 | ❌ **Reject** — 0 stars, brand new. *(Originally recommended; retracted on your trust filter.)* |

**Guardrail principle going forward:** the most trustworthy third-party tool is *no* third-party tool. For the safety hook we wrote our own (below) — nothing external to trust. Only reach for a repo when ✅-tier (>5k stars, pushed within ~30 days, not archived).

## P0 — Quick wins (low risk, high leverage, do first)

### 1. Destructive-command guardrails (PreToolUse hook)

A `PreToolUse` hook fires *after* Claude picks a tool call but *before* it executes — a synchronous veto point. It can return `permissionDecision: "deny"` for dangerous patterns (`rm -rf`, `git reset --hard`, `git push --force`, `git clean -fdx`, DB wipes) and the command never runs. This is the single biggest lever for "prevent unrepairable mistakes."

**SHIPPED (2026-06-06): self-authored hook — `.claude/hooks/guard-destructive.py`**, wired in `.claude/settings.json`. Zero third-party trust required (every rule is reviewable). Blocks `rm -rf`, `.duckdb`/`.db` deletion, `git reset --hard`, `git clean -fd`, force-push, `DROP/TRUNCATE`, `clean-db`; has a commented-off optional rule for the live `signal watch` daemon. Verified live (blocks destructive, allows `make test`/`git status`). *Known tradeoff:* substring-matches the whole command, so it can fail-safe on commands that merely mention a pattern — override by rephrasing or editing the hook.

**Optional broader layer (vetted):** [`kenryu42/cc-safety-net`](https://github.com/kenryu42/claude-code-safety-net) (1.4k★, active) for wider multi-CLI coverage. Rejected on your trust filter: `claude-rm-rf` (36★, stale), `Dippy` (235★, stale), `damage-control` (473★, stale).

**Buibui-specific patterns to add to the deny/confirm list:**

- `clean-db`, any `DROP TABLE`, `rm *.duckdb`, `rm analytics.db` — your DB is gitignored and not trivially recoverable.
- `git push --force` / `--force-with-lease` on `main`.
- Writes to `config/coins.json` / `.env` (gitignored secrets).
- Note: CLAUDE.md already warns "`signal watch --once` fired real Telegram + wrote real DB" — encode that as a hook that blocks `signal watch` without `DATA_SOURCE` set to a safe value or without an explicit smoke flag.

### 2. Live token / context / cost statusline

Claude Code's `statusLine` setting renders a custom line fed live session JSON (incl. `context_window` with real token counts as of ccusage v2.1.132). No more checking a website.

**SHIPPED (2026-06-07): ccstatusline installed (global binary) + wired on BOTH accounts** — live context progress bar confirmed working. Personal `statusLine` in `~/.claude-personal/settings.json`, work in `~/.claude/settings.json`; shared appearance config at `~/.config/ccstatusline/settings.json` (use `--config <path>` per account to diverge). `ccusage` (linuxbrew) also available to embed as a cost widget.

Vetted picks (the 0★ `claude-usage-bar` is **rejected** per your trust filter — see table above):

- ✅ [`sirmalloc/ccstatusline`](https://github.com/sirmalloc/ccstatusline) (10.3k★, active) — **recommended.** Themeable powerline statusline, interactive config (`npx ccstatusline@latest`), includes a context-window usage segment/bar. Closest trustworthy match to "live progress bar."
- ✅ [`ryoppippi/ccusage`](https://github.com/ryoppippi/ccusage) (15.6k★, pushed today) — `ccusage statusline`: session cost, today's cost, 5-hour block + time remaining, burn rate, model; uses live `context_window` token counts. Add this if you also want **cost/burn-rate** visibility.
- They compose: ccstatusline for the context bar, ccusage for cost. Both are config-only, no trading-logic risk.

Install (you run it): `npx ccstatusline@latest` then pick the context-usage segment, or set `statusLine` in `~/.claude/settings.json`. I can wire the `statusLine` setting once you've installed one.

---

## P1 — Methodology & quality (the "becoming a master" layer)

### 3. Full Superpowers plugin (you have the skills, not the orchestration)

You have superpowers' *building-block* skills but **not** the orchestration/workflow skills that make it autonomous:

- Missing: `subagent-driven-development`, `dispatching-parallel-agents`, `using-git-worktrees`, `finishing-a-development-branch`, `requesting-code-review`, `receiving-code-review`, `systematic-debugging` (4-phase root-cause), `verification-before-completion`, `using-superpowers`.
- The flow it enforces: **brainstorm spec → approve in digestible chunks → write junior-proof plan (true red/green TDD + YAGNI + DRY) → subagent-driven execution with two-stage review (spec compliance, then code quality) → verify → finish branch.** Claude can then run autonomously for long stretches without drifting — directly serving your "better steering, don't drift" goal.

Install (official marketplace):

```bash
/plugin install superpowers@claude-plugins-official
```

(or `/plugin marketplace add obra/superpowers-marketplace` then `/plugin install superpowers@superpowers-marketplace`.)

**Caveat:** superpowers' git-worktree + subagent-heavy flow overlaps your existing branch/PR conventions (`post-branch`, `pr-summary`). Adopt incrementally — start with `systematic-debugging` + `verification-before-completion` + `requesting-code-review`, which fill genuine gaps, before letting it drive the whole branch lifecycle.

### 4. Prompt-engineering: turn CLAUDE.md from a manual into a contract

Your CLAUDE.md is an excellent *reference* but is thin on *persona + quality bar + refusal rules*. Add a short top section:

- **Role/persona:** "You are a senior quant-systems engineer. Bias toward de-biased, OOS-validated evidence (DSR/PBO/MinTRL); never commit an overfit param."
- **Quality contract:** definition-of-done you already use (lint-py ✓, mypy strict ✓, `make test` green, regression goldens unmoved) stated as a *gate*, not a habit.
- **Anti-drift:** "Before multi-step work, restate the goal + success metric in one line; if a step doesn't serve it, stop and ask."
- Consider a Claude Code **output style** (`/output-style`) for a terse, diff-first working mode to cut tokens.

### 5. Token-efficiency discipline (rules, not just tools)

You have context-mode; codify the habits that 2026 best-practice guides converge on:

- **Lazy skills:** don't invoke skills you don't need; their bodies are dormant until called.
- **Proactive `/compact` with hints** at logical boundaries — don't wait for autocompaction (it fails when remaining-work direction is unpredictable).
- **Subagent rule of thumb:** delegate heavy reads/long analysis to a subagent *only when the saved main-context clutter outweighs startup overhead* — not for quick git/shell ops.
- **Model tiering:** draft/cheap work on Sonnet; escalate to Opus for deep refactors/analysis.
- Run `ctx-stats` periodically to see context saved.

---

## P2 — Ecosystem hygiene

- **`find-skills` (you have it):** use `npx skills find <query>` and the [skills.sh leaderboard](https://www.skills.sh/) before hand-rolling. Prefer 1K+ installs + reputable source (Anthropic/Vercel). Popular validated picks: `subagent-driven-development` (99.5K), `writing-plans` (129.8K), `receiving-code-review` (93.7K).
- **Notable from awesome-claude-code:** [`Claude HUD`](https://github.com/jarrodwatts/claude-hud) (rich statusline: context, tools, agents, todos), orchestrators `ruvnet/ruflo` & `sudocode` (study the patterns even if you don't adopt), `ctoth/claudio` (OS sound hooks).
- **`/sanity-check` cadence:** add "skills/hooks freshness" — re-audit installed plugins + this doc quarterly.

---

## Recommended order of operations

1. ~~Self-authored safety hook~~ ✅ **DONE** (`.claude/hooks/guard-destructive.py` + settings wiring, verified live).
2. ~~CLAUDE.md persona/quality-contract + token rules~~ ✅ **DONE** (Working Agreement section).
3. ~~ccstatusline token bar~~ ✅ **DONE** — global binary wired on both work + personal accounts; live context bar confirmed.
4. **superpowers plugin** (`/plugin install superpowers@claude-plugins-official`), adopt `systematic-debugging` + `verification-before-completion` first — *not* the full autonomous driver. *(pending — user-run)*
5. Consider an output style (`/output-style`) for terse, diff-first mode. *(optional)*
6. `/ctx-upgrade` (context-mode plugin crashed mid-session, v1.0.89 → v1.0.162). *(pending — user-run)*
7. Separate `/sanity-check` session: MEMORY.md trim + memory/skill prune. *(pending)*

All P0/P1 items are config/plugin-level — none touch trading logic, so zero risk to the strategy stack.

---

## Sources

- skills.sh: <https://www.skills.sh/> · find-skills: <https://www.skills.sh/vercel-labs/skills/find-skills>
- Superpowers: <https://github.com/obra/superpowers>
- Awesome Claude Code: <https://github.com/hesreallyhim/awesome-claude-code>
- Statusline: <https://code.claude.com/docs/en/statusline> · ccusage: <https://github.com/ryoppippi/ccusage> · claude-usage-bar: <https://github.com/cleyton1986/claude-usage-bar> · ccstatusline: <https://github.com/sirmalloc/ccstatusline>
- Hooks: <https://code.claude.com/docs/en/hooks> · safety-net: <https://github.com/kenryu42/claude-code-safety-net> · claude-rm-rf: <https://github.com/zcaceres/claude-rm-rf> · Dippy: <https://github.com/ldayton/Dippy>
- Best practices: <https://code.claude.com/docs/en/best-practices>
