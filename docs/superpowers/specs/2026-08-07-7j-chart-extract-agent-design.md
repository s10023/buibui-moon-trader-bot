# 7j — a dedicated `chart-extract` subagent for `/ingest-charts`

**Date:** 2026-08-07 · **Skill-fix:** 7j · **Status:** implemented and measured
2026-08-07k, −39.9% tokens (see the plan's Results section)

## Goal and success metric

Cut the per-image dispatch cost of `/ingest-charts` chart extraction, without
regressing extraction quality.

**Success metric:** measured tokens per image on a re-dispatch of the *same six
images*, against the 2026-08-07 baseline of **296,456 tokens / 6 panels**, with
cluster counts and per-panel confidence not below the recorded **3 `high`
(liq_map) / 3 `med` (liq_heatmap)** split.

No predicted saving is committed here. The claim is "measured after", not before.

## Background — what the cost actually is

Step 2 currently says only *"Dispatch each image to a **sonnet** subagent (Agent
tool)"* — **it names no agent type at all**, so the invoker picks one and the
practical default is a general-purpose agent. That silence is part of the defect,
not merely context for it: nothing in the skill pins the expensive half of the
dispatch. The 2026-08-07 batch took that default, one tool use each:

| panel | tokens | tool uses | confidence |
| --- | --- | --- | --- |
| BTC liq_map | 48,995 | 1 | high |
| BTC liq_heatmap | 49,829 | 1 | med |
| ETH liq_map | 48,995 | 1 | high |
| ETH liq_heatmap | 49,825 | 1 | med |
| SOL liq_map | 48,994 | 1 | high |
| SOL liq_heatmap | 49,818 | 1 | med |

**Total 296,456. Zero agents wandered** — against 384,430 and 3-of-6 wandering on
the previously filed run.

The images themselves are a rounding error. Measured dimensions, and the token
cost after the 1568px long-edge downscale:

| panel | dimensions | file | ≈ image tokens |
| --- | --- | --- | --- |
| liq_map | 1698×703 | ~140KB | ~1.4K |
| liq_heatmap | 1730×1091 | ~500KB | ~2.1K |

So **~47K of each ~49K dispatch is fixed overhead**: the general-purpose agent
system prompt plus ~20 tool schemas. That overhead, not the task, is the target.

### Two corrections to the filed 7j entry

**1. "Stops re-sending the brief" is wrong.** The filed entry claims a dedicated
agent "cuts the schema block and stops re-sending the brief." A subagent's system
prompt is sent on *every* invocation exactly like a user message, so relocating
the ~800-token rubric into the agent file saves **zero** tokens. Only two savings
are real: replacing the bloated default system prompt, and cutting ~20 tool
schemas to one. **This is why the rubric stays in `SKILL.md`** — moving it would
buy nothing and split the extraction contract across two files.

**2. The confidence correlation is not about wandering.** The filed entry records
"every multi-call agent returned `high` confidence; two of three single-call
agents returned `med`." On the 2026-08-07 batch **all six agents were single-call
and confidence split perfectly by panel type, 3/3** — every `liq_map` `high`,
every `liq_heatmap` `med`. The mechanical explanation fits better than a
behavioural one: a `liq_map` prints `Current Price:` as **text**, which survives
the downscale, while a heatmap's 1px y-axis gridlines and fine bands do not, and
it is the taller, denser image. Wandering is also not a stable property to budget
against — 3/6 one run, 0/6 the next.

Consequence for design: **any future zoom path should target heatmaps
specifically**, and the per-image budget floor is ~49K flat, with wandering as a
variable add-on rather than the driver.

## Scope — staged deliberately

**Phase 1 (this spec):** the agent definition and the dispatch change. Nothing else.

**Phase 2 (NOT in this spec, gated on measurement):** a crop/zoom path for
heatmaps. Built **only if** the phase-1 measurement shows heatmap confidence at or
below `med` after the change. Building it now would be speculative, and the
2026-08-07 batch is a clean control that can decide it on evidence.

## Design

### 1. `.claude/agents/chart-extract.md` (new)

Frontmatter: `model: sonnet`, `tools: Read`.

System prompt states, and states only:

- You receive the path to ONE chart screenshot.
- Read it exhaustively.
- Return exactly ONE bare JSON object — **no prose, no markdown fence**.
- You never write files.

`tools: Read` is doing real work beyond token savings: it structurally prevents
the file-writing misbehaviour observed on a previous run, and nothing in phase 1
needs any other tool.

### 2. `.gitignore` — add `!.claude/agents`

`.gitignore:15` is `.claude/*`, with re-includes at lines 16–19 for
`.claude/skills` and `.claude/context` only. **An agent file added without this
change is untracked and does not survive a reclone** — the same loss that already
applies to `.claude/hooks/` and `.claude/settings.json`. This would be the third
instance of that defect, so the re-include is part of the change, not a follow-up.

**A fourth file, `.markdownlint-cli2.jsonc`, needs the matching re-include** — its
own comment says it mirrors `.gitignore` deliberately, so skipping it would ship
committed markdown that nothing lints.

### 3. `.claude/skills/ingest-charts/SKILL.md` step 2

Replace *"Dispatch each image to a **sonnet** subagent"* with an explicit
`subagent_type: "chart-extract"`. **The rubric text is unchanged and stays in the
skill.** Naming the agent type is half the fix on its own — the current wording
leaves the costliest parameter of the dispatch to whoever happens to run it.

Data flow is otherwise untouched: one agent per image, image bytes never enter
main context, same JSON contract, same single review gate, same write path. This
changes *who* the dispatch goes to and nothing else.

### 4. Error handling — fall back, but never silently

If `chart-extract` does not resolve (fresh clone, or a harness without project
agents), dispatch general-purpose **and print a warning naming the cost**, visible
in the review digest rather than buried in a log.

The rule to write into the skill: **never dispatch the fallback silently.** A
silent fallback restores the ~49K cost while producing byte-identical output, so
the regression would be invisible — the exact "looks green while broken" shape
this repo treats as a defect class in its own right.

### 5. Guard rails carried forward unchanged

- **Do not substitute the stock `Explore` agent.** The saving is only its 5-tool
  delta, and its search-tuned prompt fights a "read ONE image exhaustively, emit
  bare JSON" contract.
- **Do not batch several images per agent.** Same-asset panels have near-identical
  price ranges; one-image-per-agent is precisely what prevents cross-panel number
  bleed.

## Verification

The six 2026-08-07 images are in `docs/plans/chart-drops/done/`, so they can be
re-dispatched **directly, outside the skill** — no writes, no ledger effect, no
Telegram. Same inputs, so this is a true A/B rather than a comparison across
batches.

Two axes, both against recorded baselines:

1. **Tokens** — per-image and batch total vs 296,456.
2. **Quality** — cluster count per panel, and confidence vs the recorded
   3 `high` / 3 `med` split.

**Falsification / phase-2 trigger:** if heatmap confidence drops below `med`, or
cluster counts fall on any panel, phase 1 has traded quality for cost. That is the
trigger to build the heatmap crop path — not a reason to retain a bloated agent.

**Reporting rule:** report the measured numbers plainly, including a null or
negative result. A dedicated agent that fails to cut cost meaningfully is a
result, and the entry should be closed as such rather than quietly kept.

## Out of scope

- The phase-2 heatmap crop/zoom path (gated on measurement above).
- A generic reusable `vision-extract` agent shared with `/ingest-video` pass 2 and
  `/ingest-x`. Each caller needs a different output contract, so the system prompt
  would have to go generic — and the bare-JSON contract is the thing that keeps
  breaking. Revisit only after 7j is measured.
- Any change to the review gate, the write path, or `tools/chart_drops.py`.
