# M4 Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship Brief-v2 M4 — the F2 trade card learns to use the M3 external
liquidity block plus a prose style directive (`card-v2`), and the four
user-facing copy surfaces get their one-time /humanizer sweep.

**Architecture:** Two independent PRs from `main`. PR-1 (`feat/card-v2`)
edits only `card/prompt.py` + its test file — a versioned prompt bump; the
external data already flows into the state JSON, so no plumbing changes.
PR-2 (`style/m4-copy-humanize`) applies pre-authored before→after string
pairs to `signals/alert_formatter.py`, `analytics/brief/render.py`, and
`web/ui/src/pages/Brief.svelte`, updating the two test asserts that pin an
old string. All copy pairs in this plan are **normative** — apply the
exact bytes, do not reword.

**Tech Stack:** Python 3.11 / Poetry, pytest, ruff + mypy strict,
Svelte 5 + Vite (`make web-build`).

**Spec:** `docs/superpowers/specs/2026-07-15-m4-integration-design.md`

## Global Constraints

- Definition of Done per PR: `make lint-py` ✓ · `make typecheck` ✓ ·
  `make test` green · `make test-regression` goldens unmoved (neither PR
  touches a backtest path; any golden movement is a defect — stop and
  report).
- Conventional commits (`feat:`, `style:`, `test:`); branch names exactly
  as given per task.
- Copy changes are wording-only. Never change dict keys, field names,
  format-string placeholders (`{...}`), HTML tags, or emoji glyphs
  (⚠️/⚡/▲/▼/— as data glyphs are product design, not prose).
- `docs/plans/` and `.cache/` are gitignored — never `git add` them.
- Do not modify `PROMPT_VERSION` consumers (`card/ledger.py`,
  `card/card.py`, tests using `"card-v1"` as fixture data) except where a
  task explicitly says so.

---

### Task 1: card-v2 rubric — tests first

**Files:**

- Modify: `tests/test_card_prompt.py`

**Interfaces:**

- Consumes: `card.prompt.PROMPT_VERSION`, `card.prompt.RUBRIC` (existing).
- Produces: failing tests that pin the card-v2 contract for Task 2.

- [ ] **Step 1: Create the PR-1 branch from main**

```bash
git checkout main && git pull && git checkout -b feat/card-v2
```

- [ ] **Step 2: Update the version assert and add card-v2 content tests**

In `tests/test_card_prompt.py`, replace the `test_version_constant` method
(currently asserting `"card-v1"`) and add two new methods inside
`class TestPrompt`:

```python
    def test_version_constant(self) -> None:
        assert PROMPT_VERSION == "card-v2"

    def test_rubric_external_directions(self) -> None:
        # card-v2: external clusters are mapped liquidity with trust guards
        assert "panel.external" in RUBRIC
        assert "spot_hint_deviation" in RUBRIC
        assert "at most ONE agreeing input" in RUBRIC
        assert "stop-hunt warning" in RUBRIC

    def test_rubric_style_block(self) -> None:
        # card-v2: humanizer style directive covers all generated prose
        assert "Style (applies to reasoning, invalidation" in RUBRIC
        assert "No hedge words (might/could/perhaps)" in RUBRIC
        assert "no em dashes" in RUBRIC
```

- [ ] **Step 3: Run the test file to verify the new tests fail**

Run: `poetry run pytest tests/test_card_prompt.py -v`
Expected: 3 FAILURES (`test_version_constant`,
`test_rubric_external_directions`, `test_rubric_style_block`); the other
4 tests PASS.

- [ ] **Step 4: Commit the failing tests**

```bash
git add tests/test_card_prompt.py
git commit -m "test(card): pin card-v2 rubric contract (red)"
```

---

### Task 2: card-v2 rubric — implementation

**Files:**

- Modify: `card/prompt.py:10` (version) and `card/prompt.py:28-67`
  (RUBRIC)

**Interfaces:**

- Consumes: the failing tests from Task 1.
- Produces: `PROMPT_VERSION = "card-v2"` and the card-v2 `RUBRIC` —
  consumed downstream (unchanged) by `build_prompt`, `post_pass`, the
  ledger, and the renderer.

- [ ] **Step 1: Bump the version constant**

In `card/prompt.py` line 10:

```python
PROMPT_VERSION = "card-v2"
```

- [ ] **Step 2: Replace the RUBRIC block**

Replace the entire `RUBRIC = f"""...{_SCHEMA}"""` assignment
(`card/prompt.py:28-67`) with exactly:

```python
RUBRIC = f"""You are a disciplined crypto-futures analyst producing ONE \
trade card for the symbol in the MARKET STATE JSON below. Work ONLY from \
numbers present in that JSON — never invent values, never cite indicators \
that are not in the input.

Method (in order):
1. Multi-timeframe synthesis: read panel.regime_1d / panel.regime_4h, the \
indicator block (EMA stack + slope, range/run-length state, Monday-range \
state, PA character, Bollinger %B / bandwidth / squeeze, anchored-VWAP \
distances, volume-profile POC/VAH/VAL and vs_value) and the session block \
(clock, last-3-session recap, tendencies). State the directional bias each \
timeframe supports.
2. Liquidity map: list the 3 nearest levels/zones ABOVE and BELOW from \
panel.levels_above/below and panel.zones_above/below with their dist_atr, \
timeframe, and swept flag. If panel.external is present, add its \
clusters_above/clusters_below to the map: "liq" bands are magnets - price \
tends to reach them, so a high-intensity liq cluster is a TP candidate, \
and one sitting just beyond your SL is a stop-hunt warning; "book" bands \
are resting orders - treat them as support/resistance. Skip any external \
snapshot with spot_hint_deviation true; when snapshots disagree, trust \
higher intensity and lower age_hours. Prefer unswept levels as targets, \
swept-and-reclaimed as entries.
3. Confluence scan: score 0-9 how many independent inputs agree — zone/level \
geometry, indicator states, session tendency, recent_fires (weight by stars/\
avg_r/dsr; treat missing ratings or dsr < 0.95 as weak evidence), pundit \
priors (only authors/families with flagged=false), external liquidity (all \
external snapshots together count as at most ONE agreeing input), and the \
xs block (side + forecast = the system's own book lean).
4. Decision: TRADE only when a limit entry at a structural level, a \
structural SL beyond it, and TP1/TP2/TP3 at mapped liquidity give planned \
RR(tp1) >= 1. Otherwise NO_TRADE naming the failed gate in no_trade_reason.
5. Reasoning log: 5-8 bullets, each citing a concrete number from the input \
JSON.

Style (applies to reasoning, invalidation, no_trade_reason): plain, \
direct English in the active voice. No hedge words (might/could/perhaps), \
no em dashes, no three-item rhetorical lists, no promotional adjectives, \
no filler openers such as "Notably" or "Importantly". Short declarative \
sentences.

Hard rules (also enforced in code after you answer — violations are vetoed):
- Never propose a trade against an existing open position on this symbol \
(account.positions).
- If account.daily_r <= the circuit-breaker limit, answer NO_TRADE \
("circuit breaker").
- Entry must be within a few percent of panel.ref_close (no far-from-market \
limits).
- SL on the correct side of entry; TPs ordered away from entry.
- You never compute position size or risk USD — code does that.

Respond with ONLY a JSON object matching this schema (no prose before or \
after, no markdown fences):
{_SCHEMA}"""
```

Everything outside steps 2, 3, 5 and the new Style block is byte-identical
to card-v1 (the intro, step 1, step 4, hard rules, and schema footer keep
their existing em dashes — only the three deltas changed).

- [ ] **Step 3: Run the prompt tests**

Run: `poetry run pytest tests/test_card_prompt.py -v`
Expected: all 7 PASS.

- [ ] **Step 4: Run the full card test suite**

Run: `poetry run pytest tests/test_card_card.py tests/test_card_client.py tests/test_card_config.py tests/test_card_ledger.py tests/test_card_prompt.py tests/test_card_render.py tests/test_card_run.py tests/test_card_state.py tests/test_cli_card.py -v`
Expected: PASS. (`test_card_render.py` / `test_card_ledger.py` construct
fixtures with a literal `prompt_version="card-v1"` — that is fixture data
flowing through render/ledger, not the live constant; leave them alone.)

- [ ] **Step 5: Full gate**

Run: `make lint-py && make typecheck && make test && make test-regression`
Expected: all green, goldens unmoved.

- [ ] **Step 6: Commit**

```bash
git add card/prompt.py
git commit -m "feat(card): card-v2 — external liquidity in the rubric + style directive"
```

---

### Task 3: PR-1 smoke artifact + pull request

**Files:**

- Create: none (PR body only; dry-run output is quoted in the PR body)

**Interfaces:**

- Consumes: the card-v2 build from Task 2.
- Produces: PR-1 open on GitHub with the operator smoke checklist.

- [ ] **Step 1: Dry-run smoke — verify the composed prompt**

Run: `make buibui-card SYMBOL=BTCUSDT DRY=1 2>&1 | head -80`
Expected: no LLM call; output shows the rubric containing
`If panel.external is present` and the `Style (applies to ...)` block,
followed by the MARKET STATE JSON. If external snapshots exist in
`docs/plans/external-context/` and are <48h old, the state JSON contains
`"external":` with clusters; if absent/stale, `"external": null` — both
are acceptable for this step (note which occurred in the PR body).

- [ ] **Step 2: Push and open PR-1**

```bash
git push -u origin feat/card-v2
gh pr create --title "feat(card): card-v2 — external liquidity + style directive" --body "$(cat <<'EOF'
## Summary
- Bump PROMPT_VERSION card-v1 → card-v2 (spec: docs/superpowers/specs/2026-07-15-m4-integration-design.md)
- Rubric delta 1: panel.external clusters join the liquidity map (liq = magnet/TP candidate/stop-hunt warning, book = support/resistance; skip spot_hint_deviation snapshots, weight intensity + freshness)
- Rubric delta 2: external liquidity in the confluence scan, capped at ONE agreeing input
- Rubric delta 3: style block for reasoning/invalidation/no_trade_reason (plain active English, no em dashes, no rule-of-three, no promo adjectives, no filler openers; hedge-word ban moved here)
- No schema / post-pass / plumbing changes — external already flows through MarketState.to_dict()

## Test plan
- [x] tests/test_card_prompt.py: version + external-directions + style-block asserts (TDD)
- [x] Full gate: lint-py, typecheck, test, test-regression (goldens unmoved)
- [x] DRY=1 smoke: composed prompt shows all three deltas
- [ ] OPERATOR: real-card smoke on fresh external snapshots (/ingest-charts same day, then make buibui-card SYMBOL=BTCUSDT) — check targets reference verified clusters sensibly and prose passes the style bar
- [ ] OPERATOR: after merge, card-v1 vs card-v2 cohorts separate automatically in make buibui-pundit-score (prompt_version is already ledgered)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Expected: PR URL printed.

---

### Task 4: copy sweep — alert formatter warnings

**Files:**

- Modify: `signals/alert_formatter.py:234-280` (9 strings)
- Modify: `tests/test_alert_formatter.py:272,276` (2 asserts)

**Interfaces:**

- Consumes: nothing from other tasks (independent of PR-1).
- Produces: the swept W-warning strings later tasks do not touch.

- [ ] **Step 1: Create the PR-2 branch from main**

```bash
git checkout main && git pull && git checkout -b style/m4-copy-humanize
```

(PR-2 is independent of PR-1; branch from `main`, not from
`feat/card-v2`.)

- [ ] **Step 2: Update the two test asserts first**

In `tests/test_alert_formatter.py` lines 272 and 276, change both
occurrences of

```python
"⚠️ Low volume — weaker conviction"
```

to

```python
"⚠️ Low volume: weaker conviction"
```

- [ ] **Step 3: Run to verify red**

Run: `poetry run pytest tests/test_alert_formatter.py -v`
Expected: the test containing line 272 FAILS (old string still emitted);
the line-276 assertion (`not in msg`) may pass vacuously — red on at
least one test is required.

- [ ] **Step 4: Apply the 9 normative string pairs**

In `signals/alert_formatter.py`, each pair replaces the em-dash separator
inside a warning fragment with a colon. Old → new, exact bytes:

```text
"⚡ Volume spike — high conviction"
→ "⚡ Volume spike: high conviction"

"⚠️ Low volume — weaker conviction"
→ "⚠️ Low volume: weaker conviction"

"⚠️ Doji signal candle — direction uncertain"
→ "⚠️ Doji signal candle: direction uncertain"

"⚠️ Wickless candle — body tends to fill first"
→ "⚠️ Wickless candle: body tends to fill first"

"⚠️ Signal inside prior range — breakout unconfirmed"
→ "⚠️ Signal inside prior range: breakout unconfirmed"

f"⚠️ {wick_label} wick rejection — price resisted signal direction"
→ f"⚠️ {wick_label} wick rejection: price resisted signal direction"

"⚠️ Equal lows below — sell-side liquidity, sweep likely first"
→ "⚠️ Equal lows below: sell-side liquidity, sweep likely first"

"⚠️ Equal highs above — buy-side liquidity, sweep likely first"
→ "⚠️ Equal highs above: buy-side liquidity, sweep likely first"

f"⚠️ 3 {bias} candles in a row — possible overextension"
→ f"⚠️ 3 {bias} candles in a row: possible overextension"
```

Do NOT touch: code comments/docstrings with em dashes (lines 31-33, 203,
265, 302, 356, 430-439 — dev-facing, out of scope), the
`<b>SIGNAL — {symbol}...</b>` headers (title separator, product styling),
or the `• <code>{strategy}</code>{stars} — <code>{reason}</code>` line
(structured separator between code spans).

- [ ] **Step 5: Run to verify green**

Run: `poetry run pytest tests/test_alert_formatter.py tests/test_candle_warnings.py -v`
Expected: PASS. If `tests/test_candle_warnings.py` pins any of the other
8 old strings, update those asserts to the new bytes in the same way and
re-run.

- [ ] **Step 6: Commit**

```bash
git add signals/alert_formatter.py tests/test_alert_formatter.py tests/test_candle_warnings.py
git commit -m "style(alerts): humanizer sweep — W-warning fragments use colon separators"
```

(Drop `tests/test_candle_warnings.py` from `git add` if Step 5 needed no
changes there.)

---

### Task 5: copy sweep — brief renderer + card renderer verdict

**Files:**

- Modify: `analytics/brief/render.py:396` (1 string)
- Modify: `card/render.py` — **no changes** (see Step 3)

**Interfaces:**

- Consumes: nothing from other tasks.
- Produces: swept `_pundit_lines` copy; `tests/test_brief_render.py`
  keeps passing unmodified (its assert matches a surviving substring).

- [ ] **Step 1: Apply the single normative pair**

In `analytics/brief/render.py` line 396, replace

```python
        priors_bit = f"priors: {board.priors_status} — run make buibui-pundit-score"
```

with

```python
        priors_bit = f"priors {board.priors_status}; run make buibui-pundit-score"
```

(Fragment + instruction joined by a semicolon instead of the em dash; the
colon after "priors" also goes since the status directly follows the
noun, matching the healthy-path `priors 3d old` phrasing.)

Do NOT touch anything else in `render.py`: the remaining em/en dashes are
column separators (the `BUIBUI DAILY BRIEF —` header, `_call_line`'s em dash between
author and quoted entry), missing-value glyphs (`—R`, `— ATR-R`, `—20`),
or range dashes (`61,300–61,600`) — data glyphs, not prose.

- [ ] **Step 2: Run the brief render tests unmodified**

Run: `poetry run pytest tests/test_brief_render.py -v`
Expected: PASS with zero test edits — the only assert touching this
string is `assert "run make buibui-pundit-score" in out1`, and that
substring survives the pair.

- [ ] **Step 3: Record the card/render.py verdict (no edit)**

`card/render.py` was assessed against the humanizer patterns during
planning: every string is a structural label or numeric field
(`veto:`, `gate:`, `size:`, `confluence {n}/9`, the version footer); it
contains no prose sentences and no em dashes. The correct sweep outcome
is **no change**. Do not invent edits; this step is documentation-only
(the PR body notes the verdict in Task 7).

- [ ] **Step 4: Commit**

```bash
git add analytics/brief/render.py
git commit -m "style(brief): humanizer sweep — priors health note reads as one clause"
```

---

### Task 6: copy sweep — Brief.svelte legend (2 rewrites + External entry)

**Files:**

- Modify: `web/ui/src/pages/Brief.svelte:122-126` (ATR14), `:138-143`
  (Zones), and insert a new `<dt>/<dd>` after the `tendency` entry
  (currently lines 205-208), before `<dt>Pundit board</dt>`

**Interfaces:**

- Consumes: nothing from other tasks.
- Produces: legend copy incl. the previously missing External entry (M3
  gap found during planning: `panel.external` renders in panels but the
  legend never explains it).

- [ ] **Step 1: Rewrite the ATR14 legend entry**

Replace

```html
        <dt>ATR14</dt>
        <dd>
          Daily Wilder ATR in price units — the yardstick: every ± number on
          levels and zones is a distance in daily ATRs from the Last price.
        </dd>
```

with

```html
        <dt>ATR14</dt>
        <dd>
          Daily Wilder ATR in price units. Every ± number on levels and
          zones is a distance in daily ATRs from the Last price.
        </dd>
```

- [ ] **Step 2: Rewrite the Zones legend entry**

Replace

```html
        <dt>Zones</dt>
        <dd>
          Structural zones per timeframe — FVG fair-value gap · OB order block ·
          BOS break of structure · EQH/EQL equal highs/lows. Tag shows ATR
          distance, or "inside" when price is within the zone.
        </dd>
```

with

```html
        <dt>Zones</dt>
        <dd>
          Structural zones per timeframe: FVG fair-value gap · OB order block ·
          BOS break of structure · EQH/EQL equal highs/lows. Tag shows ATR
          distance, or "inside" when price is within the zone.
        </dd>
```

Do NOT touch the EMA entry's `— not enough history` (it quotes the
rendered glyph) or the session-clock time ranges (`08–14` en dashes are
numeric ranges).

- [ ] **Step 3: Add the External legend entry**

Insert between the `tendency` entry's closing `</dd>` and
`<dt>Pundit board</dt>`:

```html
        <dt>External</dt>
        <dd>
          Verified liquidation and order-book levels read from
          operator-dropped chart screenshots (Coinglass / MMT). Each line is
          one snapshot: source, panel (liq / book / map), window, capture
          age, then price bands with intensity (HIGH = brightest) and ATR
          distance. ⚠spot flags a snapshot whose printed spot price
          disagrees with the brief's reference price.
        </dd>
```

- [ ] **Step 4: Build the UI to verify the template compiles**

Run: `make web-build`
Expected: Vite build succeeds with no errors. (No JS tests cover the
legend copy — the build is the check.)

- [ ] **Step 5: Commit**

```bash
git add web/ui/src/pages/Brief.svelte
git commit -m "style(ui): humanizer sweep of Brief legend + add missing External entry"
```

---

### Task 7: PR-2 gate + pull request

**Files:**

- Create: none (PR only)

**Interfaces:**

- Consumes: Tasks 4-6 commits on `style/m4-copy-humanize`.
- Produces: PR-2 open on GitHub.

- [ ] **Step 1: Full gate**

Run: `make lint-py && make typecheck && make test && make test-regression`
Expected: all green, goldens unmoved.

- [ ] **Step 2: Push and open PR-2**

```bash
git push -u origin style/m4-copy-humanize
gh pr create --title "style: M4 /humanizer copy sweep — alerts, brief, UI legend" --body "$(cat <<'EOF'
## Summary
- One-time MODE-1 humanizer sweep per spec docs/superpowers/specs/2026-07-15-m4-integration-design.md; wording-only, semantics frozen
- signals/alert_formatter.py: 9 W-warning fragments switch em-dash separators to colons
- analytics/brief/render.py: absent-priors health note reads as one clause (semicolon join)
- web/ui/src/pages/Brief.svelte: ATR14 + Zones legend entries de-dashed; NEW External legend entry (M3 gap — the block rendered but was never explained)
- card/render.py assessed and intentionally unchanged: structural labels + numbers only, no prose
- Kept deliberately: column-separator and missing-value dashes/glyphs, numeric range dashes, functional ⚠️/⚡ emoji, dev-facing comments/docstrings

## Test plan
- [x] tests/test_alert_formatter.py asserts updated to the new strings (red→green)
- [x] tests/test_brief_render.py passes UNMODIFIED (substring assert survives)
- [x] make web-build (legend copy has no JS test coverage; build is the check)
- [x] Full gate: lint-py, typecheck, test, test-regression (goldens unmoved)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Expected: PR URL printed.
