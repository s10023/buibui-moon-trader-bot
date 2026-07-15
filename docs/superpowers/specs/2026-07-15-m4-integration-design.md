# M4 Integration — card-v2 rubric + /humanizer copy sweep (design)

- **Date:** 2026-07-15
- **Status:** approved in brainstorm (operator, this session); spec for a
  sonnet-SDD plan
- **Roadmap slot:** Brief-v2 M4 (`[[brief-v2-roadmap]]`); follows M3
  external context (PR #484, merged `dc1c1f8`)
- **Related:** F2 card spec `2026-07-08-f2-trade-card-design.md` (+
  2026-07-11 addendum) · M3 spec `2026-07-14-m3-external-context-design.md`
  · memory `feedback_humanizer_polish_targets`

## Context — what M4 actually still needs

The roadmap line for M4 reads "F2 trade-card consumes M1–M3 components +
copy-humanize sweep". Two thirds of the first half already shipped:

- **M1 indicators + M2 sessions are consumed today.** The 2026-07-11 F2
  addendum wired them: `card/state.py::snapshot_market_state` embeds the
  whole `SymbolPanel` in the state JSON, and the card-v1 rubric's Method
  step 1 explicitly reads the indicator and session blocks.
- **M3 external already reaches the card's data.** `SymbolPanel.external`
  is additive; `MarketState.to_dict()` serialises it with the rest of the
  panel. Nothing to plumb.
- **But the rubric is blind to it.** Method step 2 (liquidity map) cites
  only `panel.levels_above/below` and `panel.zones_above/below`; step 3
  (confluence) never mentions external. The model may stumble into the
  block; nothing directs it.

So the remaining work is (a) a rubric change teaching the card to use
`panel.external`, (b) the anti-AI-tell style directive for the card's
generated prose, and (c) the one-time `/humanizer` sweep of static copy.
Per `feedback_humanizer_polish_targets`, (a) and (b) are both prompt
changes to the ledgered `PROMPT_VERSION` — they ride **one** `card-v2`
bump, never a silent edit of `card-v1`.

## Goals

1. `card-v2`: the card uses verified external liquidation/book clusters as
   mapped liquidity (targets + warnings) and as capped confluence
   evidence, with trust guards; its generated prose follows the humanizer
   style rules.
2. Static copy on the four user-facing surfaces reads as human-written:
   `analytics/brief/render.py`, `web/ui/src/pages/Brief.svelte` legend
   copy, `card/render.py`, `signals/alert_formatter.py` (W1–W8 warnings +
   section blurbs).

## Non-goals

- No `MarketState` / state-JSON plumbing (external already flows).
- No card schema, post-pass, sizing, or hard-rule (code-enforced) changes.
- No M3 polish-batch items (venue field, widened dedup key, API parity
  test, band-collapse, `.PNG`) — that batch is tracked separately in the
  PR #484 review notes.
- No Brief page restructure (roadmap decision: one page with sections),
  no X-timeline revival.

## Decomposition — one spec, one sonnet-SDD plan, two PRs

| PR | Content | Character |
| --- | --- | --- |
| PR-1 | `card-v2` rubric bump + prompt tests + smoke A/B | Gated behavioral change, small diff |
| PR-2 | `/humanizer` copy sweep on 4 surfaces + string-assertion test updates | Wide but mechanical, wording-only |

Either PR reverts alone. PR-1 merges first (independent, but keeps the
ledger's card-v2 start date clean of copy noise).

## PR-1 — card-v2 (`card/prompt.py`)

`PROMPT_VERSION = "card-v2"`. Three rubric deltas; everything else in the
RUBRIC, the JSON schema, `build_prompt`, and the post-pass is unchanged.
**The final text below is normative** — the implementer applies it
verbatim (modulo source line-wrapping); no rewording.

### Delta 1 — Method step 2 (liquidity map) gains external clusters

Final text of step 2:

```text
2. Liquidity map: list the 3 nearest levels/zones ABOVE and BELOW from
panel.levels_above/below and panel.zones_above/below with their dist_atr,
timeframe, and swept flag. If panel.external is present, add its
clusters_above/clusters_below to the map: "liq" bands are magnets - price
tends to reach them, so a high-intensity liq cluster is a TP candidate,
and one sitting just beyond your SL is a stop-hunt warning; "book" bands
are resting orders - treat them as support/resistance. Skip any external
snapshot with spot_hint_deviation true; when snapshots disagree, trust
higher intensity and lower age_hours. Prefer unswept levels as targets,
swept-and-reclaimed as entries.
```

### Delta 2 — Method step 3 (confluence) adds external, capped at one input

The input list in step 3 gains one entry, inserted before the xs block:

```text
..., pundit priors (only authors/families with flagged=false), external
liquidity (all external snapshots together count as at most ONE agreeing
input), and the xs block (side + forecast = the system's own book lean).
```

The cap stops several screenshots of the same wall from inflating the 0–9
score.

### Delta 3 — Method step 5 + new Style block

Step 5 keeps the cite-a-number rule; the hedge-word ban moves into a new
Style block that also covers `invalidation` and `no_trade_reason`:

```text
5. Reasoning log: 5-8 bullets, each citing a concrete number from the
input JSON.

Style (applies to reasoning, invalidation, no_trade_reason): plain,
direct English in the active voice. No hedge words (might/could/perhaps),
no em dashes, no three-item rhetorical lists, no promotional adjectives,
no filler openers such as "Notably" or "Importantly". Short declarative
sentences.
```

### Tests (PR-1)

- Existing prompt tests updated: version string asserts `card-v2`.
- New content assertions: rubric mentions `panel.external`,
  `spot_hint_deviation`, the one-input confluence cap, and the Style
  block.
- No golden/regression impact (no backtest path touched).

### Acceptance (operator smoke A/B, after merge-ready but before merge)

1. `make buibui-card SYMBOL=BTCUSDT DRY=1` — composed prompt shows the
   external block in the state JSON and the three rubric deltas.
2. One real card on fresh external snapshots (`/ingest-charts` the same
   day): operator checks that targets/warnings reference the verified
   clusters sensibly and the prose passes the style bar.
3. Long-run: `ai-cards.jsonl` + the pundit dual-write already record
   `prompt_version`; `make buibui-pundit-score` separates card-v1 vs
   card-v2 cohorts with zero scorer changes.

## PR-2 — `/humanizer` static copy sweep

MODE-1 (one-time deterministic edit) on exactly four surfaces:

| Surface | Prose in scope |
| --- | --- |
| `analytics/brief/render.py` | Section labels, health notes, legend/blurb strings — number formatting untouched |
| `web/ui/src/pages/Brief.svelte` | Legend ⓘ card copy + UI-side labels |
| `card/render.py` | Terminal card template strings (distinct from the gated rubric) |
| `signals/alert_formatter.py` | W1–W8 warning strings + section blurbs |

Rules and constraints:

- Humanizer rules (from the installed skill): remove em-dash overuse,
  rule-of-three padding, promotional adjectives, vague attributions,
  passive voice, filler phrases, AI-vocabulary tells.
- **Wording only, semantics frozen** — no label changes meaning; no key,
  field, or format-string placeholder changes.
- Before→after string pairs are authored in the implementation plan (main
  thread writes them under the skill); the executor applies them
  mechanically and updates the tests that assert on those strings
  (`tests/test_brief_render.py` and any alert-formatter string tests) in
  the same PR.
- Blast-radius check (verified in brainstorm): nothing parses these
  strings downstream — Telegram and CLI output are human-read; the Svelte
  UI renders from the `/api/brief` JSON, not the markdown renderer.

## Degradation

`panel.external = None` (no snapshots, all stale, ingest never run) needs
no new handling: the rubric's standing rule — never cite inputs absent
from the JSON — plus the "If panel.external is present" conditioning
covers it. Per-snapshot trust degrades via `spot_hint_deviation` and
`age_hours`, both already computed by `analytics/brief/external.py`.

## Definition of Done (both PRs)

- `make lint-py` · `make typecheck` · `make test` green; `make lint-md`
  for this spec.
- `make test-regression` goldens unmoved (neither PR touches a backtest
  path; any movement is a defect).
- PR-1 additionally: smoke A/B artifacts noted in the PR body.

## Risks

- **Prompt regression** (card quality drops on card-v2): advisory-only
  surface, one-command revert (version + rubric constants), and the
  ledger cohort split measures it over time.
- **Style directive over-constrains reasoning**: bullets must still each
  cite a number; the style block only shapes phrasing. Smoke A/B is the
  check.
- **Copy sweep breaks a string assert**: caught by `make test` in the
  same PR; the plan lists affected tests per surface.
