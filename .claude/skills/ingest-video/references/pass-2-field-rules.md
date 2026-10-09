# Step 6 — the per-field rules for the pass-2 subagent

**When:** Read before writing the pass-2 prompt, and whenever a returned `entry`, `stop`, `direction`, `horizon`, `frame_path` or `corrected_from` looks off.

Reference for `.claude/skills/ingest-video/SKILL.md`, which holds the run order, the steps and the condensed rules. This file holds the full text, including the dated incidents behind each rule. The passages below are carried over verbatim from the pre-split SKILL.md (#884), so where one says "this doc", "this document", "above", "below" or names a step, it means SKILL.md.

## Step 6 — rules for the subagent

Rules for the subagent:

- Where a frame shows a number/level/structure that **contradicts** the transcript, the
  chart wins: use the chart's value in `entry`/`stop`/`target`/`chart_read`, and record
  the transcript's original claim in `corrected_from`. When nothing was corrected, leave
  `corrected_from` empty.
- **The chart wins only on a DRAWN value** — a line, annotation, printed label, or
  measured readout placed on the chart. A value *inferred* from where the live price
  ticker happens to sit is **not** a correction: put that reading in `chart_read`, keep
  the spoken value in `entry`/`stop`/`target`, and leave `corrected_from` empty. A pundit
  is scored on the level they **stated**. (Observed failure: a stated 61,000 pivot was
  proposed as 61,600–61,800 because the ticker sat at a drawn arc's right anchor — ~700
  points onto a level the speaker never said.)
- **A level named as the CONDITION for entry IS the `entry`** — never "no numeric
  trigger stated". Measured (2026-08-13d): @Traderfengge's ETH short triggers on a close
  below the **1,800** double-bottom neckline, a number he names repeatedly; pass 2 wrote
  `entry` as prose ending "(no numeric trigger price stated)". Left empty, the scorer
  falls back to the call-time price and books him **in position on a trade he said he had
  not taken**. `entry: "1,800"` flips the row to `awaiting trigger` and `entry_quality`
  `fallback` → `ok`. A conditional plan has an entry; it just has not filled yet.
- **A CONTINGENT stop-management instruction is NOT a `stop`.** Same batch, opposite
  direction: 舒秦's "move stop to breakeven at 63,500 **if CPI prints bearish**" was
  written as `stop: "63,500"` against `entry: "63,500"`, and entry == stop scores an
  instant **0.00R LOSS** — a manufactured loser against the author. `stop` is the
  INVALIDATION level and nothing else; the management instruction is already verbatim in
  `raw_quote`, which is where it stays. ⚠ Not `setup_type` — that key is on the pass-2
  item schema only and has never reached the ledger (0 of 328). **An empty `stop` is
  legitimate** and must stay legitimate.
  ⚠ **No numeric guard replaces this rule, and one was priced rather than assumed**
  (2026-09-02, 356 live rows): an `entry` ∩ `stop` overlap fires on **13 of the 130** rows
  carrying both, and nearly every one is the CORRECT structural shape — enter at the zone,
  invalidate on that zone's own edge (`~57,850-58,000` against `below ~57,850`). The narrow
  "single bare number, equal in both fields" variant fires on **0**. Both failures above are
  semantic, so write-time judgement is the whole fix.
- **`direction` must be exactly one of `long` / `short` / `neutral` — a fifth value now
  costs you the whole row.** `tools/pundit_score.py:501` special-cases only the literal
  string `neutral` (returning UNSCORED); `:540` is then
  `dirsign = 1.0 if call.direction == "long" else -1.0`, so **anything unrecognised used
  to fall through to the short branch**. Measured on `JcMq-lyHIt4` (2026-08-05): pass 2
  emitted `direction: "range"` for a range-trade plan, which would have booked a
  deliberately non-directional call as a bearish one.
  **`analytics/pundit_direction.py` now enforces the enum in code** at both read
  boundaries — the scorer skips the line with a warning, and the Brief's pundit board
  skips it too. So the failure is no longer silent, but it is still a *loss*: a
  mis-typed direction means that call is never scored, and nothing tells you at write
  time. Casing is folded (`SHORT` → `short`), so only genuinely new values are rejected.
  A missing `direction` is rejected on the same grounds — it previously read as a short
  as well. **The pass-2 item schema's `null` is not a contradiction:** a claim or
  mechanic legitimately has no direction, and those never route to Stream C. `null` is
  only invalid on a row that reaches the ledger. A range/chop/two-sided plan is
  `neutral`; map it there and say why in `entry`, **never `setup_type`** — that field
  is on the pass-2 item schema only, the Stream C line has no such key, and no row has
  ever carried it (**0 of 328** at 2026-08-26), while `entry` reaches the ledger and
  feeds `tag_family`. ⚠ Prose
  in `entry` is safe on a `neutral` row ONLY: `score_call` returns UNSCORED before
  `resolve_levels` parses it, so this licenses nothing where the machine-parsed
  contract below applies. Check this value before writing any Stream C row.
  **A same-author dedup match in the OPPOSITE direction at the same level is the range
  case wearing two rows** (operator ruling 2026-08-27, S2): when the source frames both
  legs as one plan — the level is a range edge, or the matched row fades what this item
  targets — route ONE `neutral` row naming both legs in `entry`, never the two
  contradictory directional rows, which mechanically hedge the author's own sample. A
  genuine flip (the author revised the view) is a new directional row, not a conflict —
  the discriminator is the source's own framing, so read it before routing. Exemplar:
  `2026-08-08-tiabtc-ZSdvqtzmN0Y` ts 28.96 — a long to 68,000 against his filed short on
  a fake-breakout above ~68,088, two legs of "range-bound 58-68k, drift to the top". That
  item predates this rule and STAYS dropped: its outcome is now known, so routing it
  retroactively would select on it.
- **`horizon` is the same closed-enum rule, with one deliberate difference: absence
  is legitimate.** An unrecognised value used to take `window_ms`'s 14-day
  `unspecified` window instead of intraday's 48h or swing's 30d — a different
  WIN / LOSS / NOT_TRIGGERED verdict for the same call, with nothing raising.
  `analytics/pundit_horizon.py` now rejects it at both read boundaries, so the row
  is skipped rather than mis-windowed. But `unspecified` is a real member of the
  enum, not a fallback for it: a pundit who states no timeframe has still made a
  scoreable call, so **omit the field or write `unspecified`** — do not invent a
  value to fill it. What gets rejected is the plausible near-miss: `daily`, `1h`,
  `short-term`, `position`, `scalp`. Casing is folded, as with `direction`.
- **`corrected_from` carries chart-vs-transcript corrections ONLY.** A symbol
  normalisation (`BTCUSD` → `BTCUSDT`, so the scorer resolves against perp bars) is not
  one: normalise `symbol` and leave `corrected_from` empty. Same reason `confidence` and
  `vision_confidence` stay separate — a field carrying two semantics can be queried for
  neither.
- Anything the frames do **not** visually corroborate (no frame near that `ts`, or the
  nearest frame doesn't show what was said) gets `vision_confidence: "low"`. Reserve
  `"high"` for a frame that directly confirms the claim; `"medium"` for
  partial/ambiguous support.
- **`frame_path` is COPIED from the supplied `frame_paths` list — never constructed from
  the item's own `ts`.** `video_marks.select()` dedupes candidates inside a 45s window and
  then caps the survivors at `FRAME_CAP` (15), so **there is usually no frame at an item's
  own timestamp**; the correct value is the nearest path that was actually supplied, which
  may sit tens of seconds away. Building `f_<ts>.jpg` out of `ts` yields a path that does
  not exist, and nothing downstream opens the file — so the note ships a dead pointer that
  only an existence check catches (measured 2026-08-25: two such paths in one tranche).
  When no supplied path is near the item, write `null`: **a missing frame is a fact, an
  invented path is a claim.**
- `raw_quote` stays in the transcript's original language (Chinese stays Chinese);
  `raw_quote_en` is always English (identical to `raw_quote` when the source is already
  English).
- `verdict` applies only when `content_type = claim`; for `setup`/`mechanic` default it
  to `NOVEL` (non-blocking — routing uses `content_type` for those, same as `/ingest-x`).
- `chart_present: false` when no frame in this video shows a chart at all (pure
  talking-head) — still emit `items` from the transcript alone, all
  `vision_confidence: "low"`, `frame_path: null`.
- `retrospective` — `true` when the speaker is reviewing or managing a position entered
  **before** this video (past-tense entry, "we entered yesterday", "已经进场", a
  screenshot of a prior day's fill), rather than issuing a new actionable call. When the
  entry timing is unclear, `true` — the conservative direction for ledger integrity
  (a retrospective stamped with this video's `call_ts_utc` scores forward from a point
  where the outcome is partly known, flattering the author's hit rate). For
  `claim`/`mechanic` items always `false`.
- `rejected` — `true` when the speaker walks through a trade and then argues **against
  taking it** ("but I wouldn't touch this", "我不会进"). The setup is real and fully
  specified, which is exactly why it is dangerous: it looks identical to a live call,
  so `route_target` would send it to Stream C and `tools/pundit_score.py` would score
  the author on a trade they declined. This shipped once (2026-07-31 round 3) and only
  the human reading the digest caught it. Distinct from `retrospective` — that one is
  about *when* the position was entered, this one about *whether it was taken at all*;
  both can be false, either can be true. For `claim`/`mechanic` items always `false`.
