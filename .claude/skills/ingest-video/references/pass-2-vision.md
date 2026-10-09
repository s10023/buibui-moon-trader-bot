# Step 6 — whether to run pass 2, and how to dispatch it

**When:** Read before deciding to skip pass 2, before changing its subagent type, and when pass 2 returns more items than it was given.

Reference for `.claude/skills/ingest-video/SKILL.md`, which holds the run order, the steps and the condensed rules. This file holds the full text, including the dated incidents behind each rule. The passages below are carried over verbatim from the pre-split SKILL.md (#884), so where one says "this doc", "this document", "above", "below" or names a step, it means SKILL.md.

## Step 6 — when to run pass 2, and dispatch

**Non-empty `frame_paths` is the PRECONDITION, not the trigger — decide by CONTENT.**
Measured 2026-08-23 on the 5-video Cowen batch: vision was **644K of 905K subagent tokens
(71%)**, 106–156K per video against pass 1's 31–75K, and the whole batch overran its
`est_tokens` by 5.8×. Dispatching it on every video with frames is what makes a batch of
five unaffordable.

- **Run pass 2** when the kept items turn on numbers read off a chart — price levels,
  entries/stops/targets, a metric's band or threshold, an on-screen indicator value, or any
  claim whose meaning depends on what the chart shows. This is where the pipeline's value
  lives (round 5: 11 chart corrections, one a level 2.2% off; the Cowen batch: digit-checking
  ASR against the chart).
- **Skip pass 2** for narrative, talking-head or purely definitional content — a mechanic
  stated in prose, a framework walkthrough, an opinion segment. Mark every kept item
  `vision_confidence: "low"`, keep `frame_path` set to the nearest path **from the
  `frame_paths` list** so a later re-check is cheap (never one built from the item's own
  `ts` — see the schema rule in step 7), and write the health note "vision skipped
  (narrative content)".
  ⚠ **Only skip when the kept set contains NO `claim` items — `verdict` has no other
  source.** It is emitted by pass 2 alone, and `route_target` REQUIRES it to route a
  `claim` (NOVEL → thesis-inbox, anything else → drop), so a skipped video carrying claims
  has unroutable items. Narrative content is usually claim-free, which is exactly why this
  bites silently: check the kept set, never assume it from the content call. If a claim is
  in there, run pass 2 — this skip does not apply.
  ⚠ **Set `chart_present: null`. Do NOT set it `false`** — the frames exist and nobody
  looked at them, which is the same distinction step 5's download-failure branch turns on
  below. `null` is the only value that says *unexamined*: `false` claims a look that never
  happened, `true` claims a chart nobody saw. **Nothing in the tree parses this field**, so
  leaving it unpinned costs silent divergence between sessions rather than a crash — which
  has already happened, 18 notes carrying `null` in two spellings. Use the one that says
  why:
  `chart_present: null   # vision skipped - nobody looked; NOT a claim that no chart exists`
- **When in doubt on a mixed video, run it.** The failure mode this rule guards is *cost*,
  and a wrong skip on a numbers video costs correctness — the asymmetry is not close.

On a multi-video batch, apply this per video rather than to the batch. Three videos with
selective vision is the shape that fits the budget; five with unconditional vision is not.

When `frame_paths` is empty, which health note you write depends on step 5's `marks`
distinction:

- `marks` was also empty (a zero-duration video — rare): skip pass 2, treat every kept
  item from pass 1 as `vision_confidence: "low"`, `frame_path: null`, and record
  `chart_present: false` for that video in the digest and note.
- `marks` was non-empty (the ordinary empty-`frame_paths` case): this is the step-5
  download failure, not "no chart" — and only after step 5's hand re-run also came back
  empty. Skip pass 2, still mark every kept item
  `vision_confidence: "low"` / `frame_path: null`, but write the step-5 health note
  ("frame extraction failed (media download error)") instead of `chart_present: false`
  — you never actually looked, so don't claim you did.

No subagent dispatch in either case.

Otherwise, dispatch a `general-purpose` subagent, **`model: "sonnet"`**,
`subagent_type: "general-purpose"`. **This one stays `general-purpose` deliberately** —
pass 1 and `/ingest-x` moved to `Explore` on a measured 3.6× saving, but that A/B covered
single-image transcription, and pass 2 is a materially harder task (cross-references the
transcript against ≤15 frames, chart-corrects spoken levels, emits multi-item JSON with
per-item confidence) on a payload of real frame tokens that will not shrink. Pass 2 is
where this pipeline's value lives (round 5: 11 chart corrections, one a level 2.2% off
that would have triggered on a sweep that never happened). **A/B it on ONE video before
switching it** — do not assume the saving transfers. Give it: the `frame_paths` list (it Reads each one —
vision), the `transcript_path` from step 1 (**the path** — it Reads that file for context
on what was said; do not paste `segments`), the kept items from step 3
(`ts`, `content_type`, `gist`), and the item schema below. Instruct it not to read any
repo, SoT, or memory file.

**⚠ That bounds what the agent READS; it does not make the agent context-free — and this
doc used to claim "self-contained" as though it did.** Measured 2026-08-12g: pass 2 cited
`project_economic_calendar_gap`, `project_conditional_edge_test`,
`project_reference_level_triggers` and the xsrev verdict **without reading a file**. Three
of those four are nowhere in `CLAUDE.md` and only in `MEMORY.md`, so "it inherits
`CLAUDE.md`" does not explain the leak and **a dedicated agent type would not close it**.

This matters even though those notes were accurate: the rubric is deliberately a *distilled
snapshot* so the extractor classifies against a frozen prior, and an agent silently seeing
the live SoT is a different experiment from the documented one. **Settle the vector before
building anything** — dispatch one throwaway subagent of each type (`general-purpose`,
`Explore`, a `tools:`-restricted custom agent) and ask each what project context it can see
without reading a file.

**⚠ The item set is CLOSED — say so in the prompt, AND check the returned length.**
`item_cap` is applied by the pass-1 prompt only, so nothing downstream bounds pass 2:
measured 2026-08-27 on `Vg2mTFfT7S0`, it returned **11 items against an `item_cap` of 5**,
inventing timestamps pass 1 never kept (an establishing shot, an attribution line, two
tangent claims), and nothing caught it — the cap was restored by hand. Instruct it:
*"Return EXACTLY the kept items you were given. Do not add, split, merge or discover new
ones — the cap was already applied in pass 1."* Then assert `len(items) <= item_cap`
yourself before the digest, because a prompt directive alone is not a guarantee: the
`direction`-enum precedent broke **1 in 6** despite an explicit instruction.

Instruct it to return ONLY this JSON:

```json
{
  "chart_present": true,
  "items": [
    {
      "ts": 252.0,
      "frame_path": ".cache/video/<id>/frames/f_0252.jpg",
      "symbol": "BTCUSDT | null",
      "direction": "long | short | neutral | null",
      "entry": "...", "stop": "...", "target": "...",
      "horizon": "EXACTLY ONE OF: intraday | swing | unspecified — no other value",
      "setup_type": "free text",
      "raw_quote": "the sentence(s) the call/claim came from, ORIGINAL language",
      "raw_quote_en": "English translation of raw_quote",
      "chart_read": "what the frame shows (levels, structure, annotations)",
      "content_type": "claim | setup | mechanic",
      "retrospective": false,
      "rejected": false,
      "verdict": "NOVEL | ALREADY-TESTED | FROZEN-CATEGORY | NOT-FALSIFIABLE",
      "gap_note": "one line: implied primitive + does the system already have/test/freeze it?",
      "vision_confidence": "high | medium | low",
      "corrected_from": "the transcript's original value, or empty"
    }
  ]
}
```

## Step 6 — `confidence` vs `vision_confidence`

**`confidence` vs `vision_confidence` — never merge these, they mean different things.**
`confidence` means the same thing across **every** source already in
`docs/plans/pundit-calls.jsonl`: the pundit's verbatim hedging phrase, or empty. This
pipeline does not extract that from a video (pass 2's contract is a visual-corroboration
read, not a hedging-language read — see the next step's Stream C schema for how the two
fields coexist without colliding). `vision_confidence` is **video-only** and records
whether pass 2 could visually corroborate the item against a frame — a property no other
source in the ledger has or needs. Keeping them as separate columns means a future
`GROUP BY confidence` (or any other query over the hedging-language column) stays honest
across every source, instead of silently mixing two incompatible populations.
