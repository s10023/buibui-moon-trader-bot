# Subagent prompt payloads — the two return schemas and the rubric

**When:** Read on EVERY run, when writing the pass-1 prompt (step 3) and the pass-2 prompt (step 6). Paste each block into the prompt verbatim.

Reference for `.claude/skills/ingest-video/SKILL.md`, which holds the run order, the steps and the condensed rules. This file holds the full text, including the dated incidents behind each rule. The passages below are carried over verbatim from the pre-split SKILL.md (#884), so where one says "this doc", "this document", "above", "below" or names a step, it means SKILL.md.

## Pass-1 return schema (step 3)

Instruct the pass-1 subagent to return ONLY this JSON:

```json
{
  "summary": "one paragraph, English",
  "stated_ts_utc": "ISO-8601 with an explicit UTC offset, or null",
  "stated_date_only": false,
  "stated_ts_raw": "verbatim quote or empty",
  "candidates": [
    {"ts": 252.0, "content_type": "setup|claim|mechanic", "specificity": 1-5,
     "is_relay": false, "originating_author": "@ThisChannel",
     "item_stated_ts_utc": null, "item_stated_ts_raw": "",
     "is_intro_recap": false, "retrospective": false, "gist": "..."}
  ]
}
```

## Pass-2 return schema (step 6)

Instruct the pass-2 subagent to return ONLY this JSON:

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

## Pass-2 field rules (step 6) — paste into the pass-2 prompt

Condensed from the verbatim text in `references/pass-2-field-rules.md`, which carries the incident behind each rule:

- A frame that **contradicts** the transcript wins — **only on a DRAWN value**; record the
  transcript's value in `corrected_from`. A reading off the live ticker goes in `chart_read` only.
- **A level named as the CONDITION for entry IS the `entry`.** **A CONTINGENT stop-management
  instruction is NOT a `stop`**; `stop` is the invalidation level only, and an empty `stop`
  is legitimate.
- **`direction` is exactly `long` / `short` / `neutral`.** A range plan → `neutral`, explained
  in `entry`, **never `setup_type`**. **A same-author opposite-direction match at one level,
  framed by the source as one plan, is ONE `neutral` row naming both legs**; a genuine flip is
  a new directional row.
- **`horizon` is `intraday` / `swing` / `unspecified`, or omitted** — never invent one.
- **`corrected_from` carries chart-vs-transcript corrections ONLY**, never a symbol normalisation.
- `vision_confidence`: `high` = a frame directly confirms, `medium` = partial, `low` = none.
- **`frame_path` is COPIED from the supplied list — never built from the item's `ts`**;
  `null` when no supplied path is near.
- `raw_quote` in the original language; `raw_quote_en` always English. `verdict` applies to
  `claim`, default `NOVEL` otherwise. `chart_present: false` when no frame shows a chart.
- `retrospective: true` when the position predates the video (unclear → `true`);
  `rejected: true` when the speaker argues against taking it. Both `false` on claims/mechanics.

## Inline classification rubric (self-contained — paste into BOTH the pass-1 and pass-2 subagent prompts)

> A distilled snapshot of the Frozen / Closed / Parked planning state so each subagent
> classifies from the prompt alone. **Refresh periodically from memory
> `project_do_not_relitigate.md` plus the `parked` and closed GitHub Issues** (planning
> left `project_todo_master.md` on 2026-09-29) — treat as a de-biasing prior, not gospel; NOVEL still passes the
> human gate. This block is shared verbatim with `/ingest-x`'s rubric — keep the two in
> sync when either is refreshed. `content_type`: **setup** = a specific
> symbol+direction+levels trade call → Stream C; **mechanic** = an exit/risk/data/
> microstructure execution rule → Stream B; **claim** = a generalizable
> market-behaviour assertion → verdict below.

**Frozen — never propose a new TA detector.** The 22-strategy detector family
(wicks, marubozu, ORB, liquidity sweep, FVG, BOS/market-structure, funding extreme,
SMT, EQH/EQL, order block, CVD divergence, trend day, engulfing, pin bar, inside
bar, hammer, doji, morning/evening star, fib retracement / golden zone, OTE, EMA)
is frozen. A claim that just restates one of these candlestick/structure patterns →
`FROZEN-CATEGORY`.

**Already-tested (verdict known → `ALREADY-TESTED`, drop unless materially new evidence):**

- DOW / day-of-week seasonality (e.g. "Monday is the weekly high → short"): base
  rate real but the tradeable edge decays OOS; the gorgeous version is look-ahead.
- Reference-level proximity (PDH/PDL, weekly/monthly H/L, DO/WO/MO opens): audited
  NO-EDGE / underpowered-positive; revisit only when the live long-near-level cell
  ~doubles (n≥100).
- Structural first-touch entries (FVG / OB / EQH-EQL / BOS): the 2026-06-26 BUILD was
  **WITHDRAWN 2026-08-18** — NO-EDGE on all six cells under confirmation causality, all six
  flipping sign. Do not file it as a live opener.
- Funding **carry** sleeve: audited, FAILS the gate → shelved.
- Absolute **trend** (EWMAC): real but sub-gate (+0.36) → shelved as a diversifier.
- Cross-sectional **XS momentum**: the gate-clearing **deploy core** (+1.375) — not novel.
- **Coinbase premium** (H14): NO-EDGE — a genuinely NEW data source that still
  found nothing. New information buys a test, never an edge.
- **USD/JPY carry-unwind** (H15): NO-EDGE / INSUFFICIENT on every cell in every
  panel; the second new source in a row to come back empty.
- Spot-perp **CVD divergence**: all 10 pre-registered trials FAIL → shelved
  (XS −0.147, PBO 0.849; TS +0.183, DSR 0.532). Decorrelated from the deploy
  core, so the failure is missing signal rather than redundancy.

**Parked / data-blocked — a GROUPING, not a verdict.** The enum is exactly
`NOVEL` / `ALREADY-TESTED` / `FROZEN-CATEGORY` / `NOT-FALSIFIABLE`. Each item below
still emits one of those four, named inline. `PARKED` and `DATA-BLOCKED` are **not**
verdict values — `route_target` raises `ValueError: unroutable` if you emit one.

- Price-distribution "candle outcome cone": parked (operator tool, not an edge).
- Liquidity/liquidation heatmap (magnet levels): `NOVEL` in principle but **data-blocked**
  (paid Coinglass/Hyblock; no free clean feed) — say so in `gap_note`.
- USDT.D dominance top → crypto bottom: **already captured** in `thesis-inbox.md`
  ([[usdt-dominance-hypothesis]]) — if a video reasserts it, `ALREADY-TESTED`-style
  "already in thesis-inbox", don't duplicate the H-row.

**`NOT-FALSIFIABLE`:** vibes / no testable prediction / unfalsifiable hindsight.
**`NOVEL`:** a genuinely new, testable, uncovered market-behaviour claim.
