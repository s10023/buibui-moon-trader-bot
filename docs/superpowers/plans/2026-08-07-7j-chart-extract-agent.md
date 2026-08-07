# chart-extract Subagent (skill-fix 7j) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut `/ingest-charts` chart-extraction cost from a measured ~49K tokens per image by dispatching a dedicated, tool-restricted subagent instead of an unnamed general-purpose one.

**Architecture:** Three small changes — a new `.claude/agents/chart-extract.md` definition (`model: sonnet`, `tools: Read`), a `.gitignore` re-include so that file is actually tracked, and a step-2 edit in `ingest-charts/SKILL.md` naming `subagent_type: "chart-extract"` with a never-silent fallback. The extraction rubric does **not** move. A final task measures the result against a recorded same-input baseline and reports it honestly, including a null result.

**Tech Stack:** Markdown skill/agent definitions, git, `markdownlint-cli2` via `make lint-md`. No Python.

**Spec:** `docs/superpowers/specs/2026-08-07-7j-chart-extract-agent-design.md` (committed `a01ea13`)

**Branch:** `fix/7j-chart-extract-agent`

## Global Constraints

Every task's requirements implicitly include this section.

- **Phase 1 ONLY.** No crop/zoom path, no pre-cropping. That is phase 2 and is gated on Task 3's measurement.
- **The step-2 extraction rubric stays in `SKILL.md`, byte-unchanged.** Moving it into the agent file saves zero tokens (a subagent system prompt is re-sent per invocation exactly like a user message) and would split the extraction contract across two files.
- **One image per agent. NEVER batch several images into one agent.** Same-asset panels have near-identical price ranges; one-image-per-agent is what prevents cross-panel number bleed.
- **Do not substitute the stock `Explore` agent.** Its saving is only a 5-tool delta and its search-tuned prompt fights a "read ONE image exhaustively, emit bare JSON" contract.
- **The fallback must never be silent.** A silent fallback to a general-purpose agent restores the ~49K cost while producing byte-identical output.
- **Baseline to beat (2026-08-07, recorded):** 296,456 tokens across 6 panels; per-panel 48,995 / 49,829 / 48,995 / 49,825 / 48,994 / 49,818; all `tool_uses: 1`; confidence 3 `high` (every `liq_map`) / 3 `med` (every `liq_heatmap`).
- **Gate:** `make lint-md` must pass. No Python file is touched, so `make lint-py`, `make typecheck`, `make test` and `make test-regression` do **not** apply — state that branch explicitly rather than claiming them green.
- **No `cmd || echo "PASSES ✓"` guards anywhere.** Every check in this plan must be capable of failing, and each one below states what would make it print success while doing nothing.

---

## File Structure

| File | Responsibility |
| --- | --- |
| `.claude/agents/chart-extract.md` (create) | The agent definition: model, tool allowlist, and the output contract. Nothing about *how* to read a chart — that stays in the skill. |
| `.gitignore` (modify, line 19) | Re-include `.claude/agents` so the definition survives a clone. |
| `.markdownlint-cli2.jsonc` (modify, line 18) | Mirror that re-include so the new committed markdown is actually linted. Its own comment states the two files mirror each other deliberately. |
| `.claude/skills/ingest-charts/SKILL.md` (modify, line ~90) | Names the agent type in the dispatch, and states the never-silent fallback rule. |
| `docs/superpowers/plans/2026-08-07-7j-chart-extract-agent.md` | This plan; Task 3 records the measured result into it. |

---

### Task 1: Tracked agent definition

The `.gitignore` change and the agent file ship together on purpose: an untracked agent definition is worthless (it dies on a clone), so a reviewer cannot meaningfully approve one without the other.

**Files:**

- Create: `.claude/agents/chart-extract.md`
- Modify: `.gitignore:19` (append a line after the existing `!.claude/context/*.md`)
- Modify: `.markdownlint-cli2.jsonc:18` (add `.claude/agents/*.md` after the `.claude/context/*.md` glob)

**Interfaces:**

- Produces: an agent addressable as `subagent_type: "chart-extract"`. Task 2 depends on that exact string.

- [ ] **Step 1: Record the failing state of the tracking check**

This is the discrimination fixture, and it is proven capable of failing because it fails right now.

Run:

```bash
cd /home/kng/repo/buibui-moon-trader-bot
mkdir -p .claude/agents && touch .claude/agents/chart-extract.md
git check-ignore -v .claude/agents/chart-extract.md
```

Expected NOW: prints the matching rule `.gitignore:15:.claude/*`, then a tab, then the path — and **exits 0**, where 0 means "this path IS ignored".

Also capture the positive control, which must already pass:

```bash
git check-ignore -q .claude/skills/ingest-charts/SKILL.md; echo "skills SKILL.md ignored? exit=$? (expect 1 = NOT ignored)"
```

*What would make this print success while doing nothing?* Nothing — `git check-ignore` exit 0 means ignored and exit 1 means not ignored, and the two controls point in opposite directions. A check that only ran the positive control would pass even with the fix absent; that is exactly why the negative control on the agent path is the one that gates.

- [ ] **Step 2: Add the re-include**

Modify `.gitignore`. The existing block is lines 15-19:

```gitignore
.claude/*
!.claude/skills
!.claude/skills/*.md
!.claude/context
!.claude/context/*.md
```

Append one line so it reads:

```gitignore
.claude/*
!.claude/skills
!.claude/skills/*.md
!.claude/context
!.claude/context/*.md
!.claude/agents
```

Use the **bare directory** form, matching the `!.claude/skills` precedent proven in Step 1. Do not write `!.claude/agents/**` — the `*/**` incident in this repo installed cleanly and matched nothing.

- [ ] **Step 3: Verify the check flipped**

Run:

```bash
git check-ignore -q .claude/agents/chart-extract.md; echo "agent file ignored? exit=$? (expect 1 = NOT ignored)"
git status --short .claude/agents/
```

Expected: exit **1**, and `git status --short` lists `?? .claude/agents/chart-extract.md`.

If exit is still 0, the re-include matched nothing — do not proceed, and do not "fix" it by force-adding the file. `git add -f` under a gitignored path is exactly how a rule gets bypassed instead of corrected.

- [ ] **Step 4: Write the agent definition**

Write `.claude/agents/chart-extract.md`. **The `# chart-extract` H1 below is required, not
decorative** — this draft originally omitted it, and prose directly after frontmatter fails
`MD041/first-line-heading`, which would make this task's own "`make lint-md` must pass" gate
unsatisfiable. Corrected 2026-08-07k after the implementer hit it:

```markdown
---
name: chart-extract
description: Reads exactly ONE Coinglass/MMT chart screenshot and returns a single bare JSON object describing its liquidation/book clusters. Dispatched one-image-per-agent by /ingest-charts step 2, which supplies the full extraction rubric in the prompt.
model: sonnet
tools: Read
---

# chart-extract

You extract structured data from a single chart screenshot.

Your caller gives you one image path and the complete extraction rubric. Read
the image exhaustively and answer with the JSON the rubric specifies.

Three rules govern your output, and they override any habit to be helpful:

1. **Return exactly ONE bare JSON object. No prose before or after it, and no
   markdown code fence.** Your entire response is parsed as JSON by a tool. A
   fenced json block has broken this contract on a previous run.
2. **Read only the one image you are given.** You are not searching a codebase
   and you have no other files to consult. Never read a second image; panels for
   the same asset have near-identical price ranges and mixing them corrupts the
   numbers.
3. **You cannot and must not write files.** Your only output is the JSON in your
   reply.

If the image is not an extractable chart panel, say so *within* the JSON via the
rubric's `status: "skip"` and `skip_reason` fields. Never substitute prose for
the object.
```

- [ ] **Step 5: Put the agents tree under the linter, and PROVE it is gated**

`.gitignore` is only half the mirror. `.markdownlint-cli2.jsonc` excludes `.claude` wholesale and re-includes exactly two subtrees, and its own comment says those globs *"mirror it deliberately"*. Adding the `.gitignore` re-include alone therefore ships committed markdown that **nothing lints** — the precise trap recorded against this repo before, where skill files were believed ungated for months.

Modify `.markdownlint-cli2.jsonc`, adding one glob after the `.claude/context/*.md` line:

```jsonc
        "!.claude",
        ".claude/skills/*/SKILL.md",
        ".claude/context/*.md",
        ".claude/agents/*.md",
```

Now prove the file is actually gated. **Reading the glob list is how this got it wrong before — inject a violation and look:**

```bash
printf '\n\n\n' >> .claude/agents/chart-extract.md   # MD012, multiple blank lines
make lint-md 2>&1 | tail -3
```

Expected: markdownlint reports an **MD012** error naming `.claude/agents/chart-extract.md`. If it reports `0 issues`, the glob did not take and the file is ungated — stop and fix the glob before continuing.

Then revert the injection and confirm clean:

```bash
git checkout .claude/agents/chart-extract.md 2>/dev/null || true   # untracked: re-write it from Step 4 instead
make lint-md 2>&1 | tail -3
```

Expected: `0 issues`, and the file count is **197** (196 after the spec and plan commits, plus the agent file).

*What would make this print success while doing nothing?* A bare `make lint-md` passing proves nothing here — a file the linter never sees also produces `0 issues`. Only the injected MD012 discriminates, because it is a violation the linter must report *if and only if* the glob matched. The file-count check is corroboration, not proof: it would also rise if some unrelated `.md` were added in the same edit.

- [ ] **Step 6: Verify the definition is staged**

```bash
git add .gitignore .markdownlint-cli2.jsonc .claude/agents/chart-extract.md
git status --short .claude/agents/
```

Expected: `git status --short` shows `A  .claude/agents/chart-extract.md` — staged, which proves the `.gitignore` re-include worked end to end rather than merely that `check-ignore` changed its answer.

- [ ] **Step 7: Commit**

```bash
git commit -m "$(cat <<'EOF'
feat(agents): add a tool-restricted chart-extract subagent

/ingest-charts step 2 names no agent type, so each image dispatch defaults to a
general-purpose agent: measured 2026-08-07 at ~49K tokens per image, of which
only ~1.4-2.1K is the image itself. The rest is the default system prompt plus
~20 tool schemas.

tools: Read does double duty here. It removes the schema block, and it
structurally prevents the file-writing an extraction agent did on a previous
run. The rubric deliberately stays in SKILL.md: a subagent system prompt is
re-sent per invocation exactly like a user message, so relocating it would save
zero tokens while splitting the extraction contract across two files.

The .gitignore re-include ships in the same commit because an untracked agent
definition dies on a clone -- the loss that already claims .claude/hooks/ and
settings.json. Verified by discrimination rather than assertion: the path
reported IGNORED via .gitignore:15 before the change and NOT ignored after, with
.claude/skills/ as the positive control for the bare-directory re-include form.

The matching .markdownlint-cli2.jsonc glob ships with it because that config
excludes .claude wholesale and re-includes named subtrees, and its own comment
says the two files mirror each other deliberately. Landing only the .gitignore
half would commit markdown that nothing lints -- the same trap that left skill
files believed ungated for months. Proven by injecting an MD012 violation and
confirming the linter reports it, since a bare passing run is exactly what an
unmatched glob also produces.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Name the agent in the dispatch

**Files:**

- Modify: `.claude/skills/ingest-charts/SKILL.md:88-92` (the step-2 heading and dispatch sentence)

**Interfaces:**

- Consumes: the `chart-extract` agent name from Task 1.
- Produces: nothing later tasks depend on structurally; Task 3 measures the behaviour this task enables.

- [ ] **Step 1: Confirm the current wording, so the edit is against known text**

Run:

```bash
sed -n '88,92p' .claude/skills/ingest-charts/SKILL.md
```

Expected, verbatim:

```text
## 2. Extract (one sonnet subagent per pending image)

Dispatch each image to a **sonnet** subagent (Agent tool). The prompt is
self-contained — no repo/SoT reads. Template (fill `<path>`, `<source>`,
`<symbol>`):
```

- [ ] **Step 2: Replace the dispatch sentence**

Replace exactly this text:

```text
Dispatch each image to a **sonnet** subagent (Agent tool). The prompt is
self-contained — no repo/SoT reads. Template (fill `<path>`, `<source>`,
`<symbol>`):
```

with:

```text
Dispatch each image to the **`chart-extract`** subagent (Agent tool,
`subagent_type: "chart-extract"`, defined at `.claude/agents/chart-extract.md`).
The prompt is self-contained — no repo/SoT reads. Template (fill `<path>`,
`<source>`, `<symbol>`):

**Naming the agent type is half the fix.** This line said only "a **sonnet**
subagent" until 2026-08-07, naming no type at all — so the costliest parameter of
the dispatch was left to whoever happened to run the skill, and the practical
default was a general-purpose agent at a measured **~49K tokens per image**
against a ~1.4–2.1K image. `chart-extract` pins `model: sonnet` and
`tools: Read`, which removes ~20 tool schemas and structurally prevents the
file-writing an extraction agent did on an earlier run.

**If `chart-extract` does not resolve** (fresh clone before the `.gitignore`
re-include propagates, or a harness without project agents), fall back to a
general-purpose sonnet agent **and say so prominently in the review digest** —
name the fallback and the cost. **Never fall back silently:** the output is
byte-identical either way, so an unannounced fallback restores the full cost
while looking exactly like success. Do NOT substitute the stock `Explore` agent,
and do NOT batch several images into one agent.
```

- [ ] **Step 3: Verify the edit landed and the old wording is gone**

Run both — the second is the one that discriminates:

```bash
grep -c 'subagent_type: "chart-extract"' .claude/skills/ingest-charts/SKILL.md
grep -c 'Dispatch each image to a \*\*sonnet\*\* subagent' .claude/skills/ingest-charts/SKILL.md
```

Expected: first prints `1` or more; second prints `0`.

*What would make this print success while doing nothing?* The positive grep alone would pass if the new text were **appended** while the old dispatch sentence still stood — leaving two contradictory instructions, which is the failure mode this repo has hit repeatedly. The second grep is what forecloses that, which is why it must read exactly `0` and not merely "fewer".

- [ ] **Step 4: Verify the rubric is untouched**

The global constraint says the rubric moves nowhere. Prove it:

```bash
git diff .claude/skills/ingest-charts/SKILL.md | grep -c '^-.*price_lo\|^-.*liq_heatmap\|^-.*intensity'
```

Expected: `0` — no rubric line was removed.

*What would make this print success while doing nothing?* If the rubric were deleted wholesale in a way that did not match those three tokens. They are chosen because all three appear only inside the rubric's JSON contract, so any real deletion trips at least one.

- [ ] **Step 5: Lint and commit**

```bash
make lint-md 2>&1 | tail -3
git add .claude/skills/ingest-charts/SKILL.md
git commit -m "$(cat <<'EOF'
docs(skills): dispatch ingest-charts extraction to chart-extract

Step 2 named no agent type -- only "a sonnet subagent" -- so the costliest
parameter of the dispatch was left to whoever ran the skill. Pins
subagent_type: "chart-extract".

The fallback is deliberately loud. Output is byte-identical whichever agent
serves it, so an unannounced fallback to general-purpose would restore the full
~49K/image cost while looking exactly like success -- the failure shape this repo
treats as a defect class of its own.

The extraction rubric is unchanged and stays here.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Measure the A/B and report it honestly

Same six images, same rubric, new agent. This task produces the number that decides whether the change is kept.

**Files:**

- Modify: `docs/superpowers/plans/2026-08-07-7j-chart-extract-agent.md` (this file — record results in the Results section at the bottom)

**Interfaces:**

- Consumes: the working `chart-extract` dispatch from Tasks 1-2.

- [ ] **Step 1: Confirm the six baseline images are present and unchanged**

```bash
ls -1 docs/plans/chart-drops/done/*20260807-170*.png | wc -l
sha256sum docs/plans/chart-drops/done/*20260807-170*.png
```

Expected: `6`. The sha256s must match the ones recorded in the scan output of the 2026-08-07 ingest (`f886dfdc…` BTC-1700, `c8f66c8b…` BTC-1701, `2d254812…` ETH-1700, `4468e86a…` ETH-1701, `572a2511…` SOL-1700, `fc77efb3…` SOL-1701). Different bytes would make this a different experiment.

- [ ] **Step 2: Re-dispatch all six through `chart-extract`**

Dispatch six agents, **one per image**, with `subagent_type: "chart-extract"` and the step-2 rubric prompt copied verbatim from `SKILL.md` (same `<path>`, `<source>`, `<symbol>` substitutions as the baseline run). Point them at the `done/` paths.

This runs **outside** `/ingest-charts`: no `chart_drops.py write`, no `mark`, no ledger update, no Telegram. The agent's `tools: Read` makes writes structurally impossible, which is a property of Task 1 rather than a promise made here.

- [ ] **Step 3: Record tokens and quality from the task notifications**

Each completion notification carries `<usage><subagent_tokens>N</subagent_tokens><tool_uses>N</tool_uses></usage>`. Record, per panel: tokens, tool uses, `confidence`, and cluster count.

- [ ] **Step 4: Fill in the Results table below and state the verdict**

Compare against the recorded baseline in Global Constraints. The verdict is one of:

- **KEEP** — batch total materially below 296,456, and no panel's confidence or cluster count is below its baseline.
- **KEEP + open phase 2** — cost cut, but heatmap confidence or cluster counts fell. That is the pre-registered trigger to build the heatmap crop path, and it must be filed, not noted in passing.
- **REVERT** — no material cost cut. Say so plainly and close 7j as a measured null. A dedicated agent that does not cut cost is a result; keeping it quietly because the work is already done is how this queue accumulates entries nobody re-reads.

*What would make this print success while doing nothing?* Reporting only the token delta. A cost cut with silently degraded extraction would read as a clean win, since cluster counts and confidence never appear. Both axes are therefore required fields in the table, and a missing quality column makes the verdict inadmissible rather than merely incomplete.

- [ ] **Step 5: Commit the results**

```bash
git add docs/superpowers/plans/2026-08-07-7j-chart-extract-agent.md
git commit -m "docs(plan): record the 7j chart-extract A/B result"
```

---

## Results — filled in by Task 3

Measured 2026-08-07k. Same six images (sha256 confirmed identical), same rubric prompt
verbatim, dispatched outside the skill. Only the agent type changed.

| panel | baseline tokens | new tokens | Δ | baseline conf | new conf | baseline clusters | new clusters |
| --- | --- | --- | --- | --- | --- | --- | --- |
| BTC liq_map | 48,995 | 29,234 | **−40.3%** | high | high | 10 | 8 |
| BTC liq_heatmap | 49,829 | 30,286 | **−39.2%** | med | med | 8 | 8 |
| ETH liq_map | 48,995 | 29,237 | **−40.3%** | high | high | 6 | 8 |
| ETH liq_heatmap | 49,825 | 30,066 | **−39.7%** | med | **high** | 8 | 8 |
| SOL liq_map | 48,994 | 29,234 | **−40.3%** | high | high | 10 | 10 |
| SOL liq_heatmap | 49,818 | 30,066 | **−39.7%** | med | med | 10 | 10 |
| **total** | **296,456** | **178,123** | **−39.9%** | 3 high / 3 med | **4 high / 2 med** | **52** | **52** |

All six were single-call (`tool_uses: 1`), as in the baseline. Per-panel cost is now
near-constant by panel type — ~29.2K for a map, ~30.1K for a heatmap — against a baseline
that was ~49K flat.

**Verdict: KEEP, and the pre-registered phase-2 trigger fired — but on the wrong panel type.**

Cost is unambiguous: **−118,333 tokens per batch (−39.9%)**, with tight variance and no
panel worse than −39.2%. Quality is neutral-to-better in aggregate: total clusters
identical at 52, and confidence improved net (ETH heatmap `med` → `high`, nothing
regressed).

The pre-registered floor was "no panel's confidence or cluster count is below its
baseline." **BTC liq_map went 10 → 8, so that floor is tripped and the trigger fires as
written.** Honouring the pre-registration rather than arguing past it: the trigger is
real. But it points somewhere phase 2 does not go — phase 2 was designed as a *heatmap*
crop path, and every heatmap held or improved. The regression is on a **map**, and it is
a change in *granularity*, not detection: the new BTC map merges near-price bands
(63,579–64,029, 450 wide) where the baseline split them (63,700–63,850, 150 wide), while
also reaching further out (it adds 10x tails at ~58,000 and ~71,000 the baseline missed).
Fewer, wider, longer-range bands — not missed liquidity.

**So: keep the change, and file the map-granularity question as its own item rather than
folding it into phase 2, which would not address it.** Cluster count is a crude quality
proxy and this is the run that showed it — a coarser clustering of the same liquidity
scores as a regression under it.

**Notes for the next batch:**

- **The bare-JSON contract still breaks at the same rate.** One of six returned a fenced
  block (ETH heatmap), against one of six in the baseline (SOL heatmap). The agent
  definition's explicit rule 1 did **not** eliminate it — a directive in a system prompt
  is not a parser. Step 3's review gate must keep tolerating a fence; do not assume the
  agent file fixed this.
- **The BTC heatmap's axis spot read is reproducibly wrong**, independently of agent type:
  65,600 in the baseline, 65,550 now, against a live 64,781 (~+1.2% both times). This is a
  property of the panel, not the extractor, and it corroborates the cross-panel check
  added to `/ingest-charts` step 3 the same day.
- `tools: Read` held: no agent attempted a write, and none read a second image.
