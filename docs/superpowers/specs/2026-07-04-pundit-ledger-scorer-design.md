# Pundit-Ledger Scorer — Design

- **Date:** 2026-07-04
- **Status:** approved (brainstorm 2026-07-04; approach C + output (b) chosen by operator)
- **Deliverable:** `tools/pundit_score.py` + `make buibui-pundit-score`

## Goal (one line)

Read-only tool that resolves every `docs/plans/pundit-calls.jsonl` call against stored
OHLCV and reports hit-rate + realized-R proxy per **author × setup-family × direction**
— converting the ingested X-call ledger into measured priors (which pundits / setup
families deserve a real hypothesis test; which are noise), plus a machine-readable
priors file for the upcoming daily market brief / F2 trade card.

## Background & data snapshot (2026-07-04, n=36)

The `/ingest-x` pipeline (Stream C) has accrued 36 calls (2026-06-10 → 2026-07-03).
Ledger schema per line: `source, author, url, call_ts_utc, symbol, direction, entry,
stop, target, horizon, confidence, raw_quote`. Levels are **free text**. Measured:

| Fact | Value |
| --- | --- |
| Symbols | BTCUSDT 29, SOLUSDT 3, ETHUSDT/HYPEUSDT/INJUSDT/ONDOUSDT 1 each |
| Authors | 22 distinct; max n per author = 5 |
| Direction | 26 long / 8 short / 2 neutral |
| Horizon | 9 intraday / 25 swing / 2 unspecified |
| Entry text | 26 zone/range, 9 numeric-ish, 1 unspecified; ~0 cleanly numeric |
| Stop text | **17 unspecified**, 10 numeric-ish, 4 zone, 4 text-only, 1 numeric |
| Target text | 15 zone, 13 numeric-ish, 3 text-only, 5 unspecified |

Consequences baked into this design: per-author cells are tiny (report raw counts, no
verdicts); free-text parsing carries most of the weight; the 17 stop-less calls need an
ATR-normalized R proxy; many swing calls are still OPEN at first run.

## Non-goals

- **No verdict gates.** Output is *descriptive priors*, not ENABLE/BUILD verdicts;
  `analytics/audit_guard.py`-style de-biased gates come later, only if a cell earns
  real n. A `⚠ n<5` marker is descriptive, not a gate.
- **No detector.** Detector family stays FROZEN; the scorer produces priors only.
- **No DB writes, no schema change, no golden movement.** Pure read-only.
- **No `/ingest-x` change in this PR** (the additive numeric-fields tweak is a
  follow-up; see §12).

## Architecture (approach C — hybrid)

Single module `tools/pundit_score.py`: pure functions (parse → resolve → score →
render) + thin `main()`, mirroring `tools/journal_fetch.py`. Deterministic parser in
the tool **plus** a human-editable overrides sidecar for rows the parser can't
confidently resolve; a per-call audit trail makes every parse and exclusion visible.
Promotion to `analytics/` happens only when the daily brief actually imports it.

## Inputs

### Ledger — `docs/plans/pundit-calls.jsonl` (source of truth)

One JSON object per line, schema above. Lines MAY additively carry numeric
`entry_px`, `stop_px`, `target_px` (floats) — when present the parser prefers them
over free text (future `/ingest-x` lines; old lines stay valid).

### Overrides sidecar — `docs/plans/pundit-overrides.jsonl` (optional)

One JSON object per line, keyed by `url`. Fields (all optional except `url`):
`entry_px`, `stop_px`, `target_px` (floats), `family` (string), `skip` (bool),
`note` (string). Override values win over parsed values; `skip: true` excludes the
call (state SKIPPED, shown in the audit trail). Seeded once from the first audit
trail by the assistant; reviewed by the operator. Both files live in gitignored
`docs/plans/`.

### OHLCV — `analytics.db` via the store getters

1h candles drive the walk for every call; 1d candles feed swing-horizon ATR14.
**Runbook step 0:** `buibui analytics sync --universe --timeframes 1h 1d` (universe
alts go stale between sessions; majors are refreshed by the daemon).

## Level parsing (deterministic, flagged)

1. **Normalization:** strip `$`, commas, `~`; expand `k`/`K` suffix (×1000).
2. **Zone pattern:** `a-b` / `a–b` / `a to b` → `(lo, mid, hi)`. Fill price for a
   zone entry = **mid**. Stop zone → **far edge** (relative to direction: below for
   long, above for short). Target zone → **near edge** (conservative for the pundit).
3. **Ambiguity rule:** parse the level **field text only** (`entry`/`stop`/`target`),
   never `raw_quote` (that is family-tagging input only). Prefer a zone match; else
   the first number in the field; multiple disjoint single numbers →
   `parse_confidence=low`.
4. **Sanity gate:** a parsed level must lie within `[0.2×, 5×]` of the call-candle
   close, else it is rejected (→ fallback path + `parse_confidence=low`). Catches
   unit confusion ("60.5k" vs "61,696.80").
5. **Fallbacks (pre-committed):** entry unparseable/unspecified → thesis-entry at the
   **close of the call candle** (the 1h candle containing `call_ts_utc`); stop/target
   missing → expiry-window scoring (§Scoring).
6. Every call gets `parse_confidence ∈ {ok, low, override, fallback}`, shown in the
   audit trail.

## Scoring semantics (pre-committed before first run)

### Trigger

- Walk 1h candles starting at the first candle with `open_time > call_ts_utc`
  (strictly after — no look-ahead; the call candle contributes only its close as the
  thesis-entry fill proxy and the ATR window endpoint).
- Level entry (numeric/zone-mid): **filled** on the first candle whose range contains
  the fill price (`low ≤ px ≤ high`) — direction-agnostic touch, covering both
  pullback and breakout entries. Thesis-only entry: filled immediately at the call
  candle close.
- No touch within the horizon window (measured from `call_ts_utc`) →
  **NOT_TRIGGERED** (excluded from hit-rate, reported).

### Windows (from `horizon`)

| Horizon | Window |
| --- | --- |
| intraday | 48h |
| swing | 30d |
| unspecified | 14d |

The trigger window runs from `call_ts_utc`; the resolution window restarts at fill.

### Resolution & R (adverse-first on same-bar ties — repo convention)

| Case | Rule |
| --- | --- |
| stop + target known | target hit → `R = (target − entry) / (entry − stop)` for longs, `(entry − target) / (stop − entry)` for shorts; stop hit → `R = −1` |
| stop known, no target | stop hit → `−1R`; else exit at expiry close, `R = move / risk` |
| no stop (17/36) | **ATR-proxy R** = `direction × (exit − entry) / ATR14`; exit = target if touched else expiry close |
| no stop, no target | ATR-proxy R at expiry close |

- **ATR14 horizon-matched:** intraday → ATR14 over the last 14 completed **1h**
  candles before fill; swing/unspecified → ATR14 over the last 14 completed **1d**
  candles. Same TR-mean convention as the backtest engine's `_compute_atr14`.
- **WIN** = target touched (when specified) or positive directional move at expiry
  (when not). Hit-rate denominator = resolved calls only.

### Call states

| State | Meaning | In hit-rate? |
| --- | --- | --- |
| WIN / LOSS | resolved: stop/target hit, or classified by sign of the directional move at expiry (exactly-flat expiry → LOSS, conservative) | yes |
| OPEN | filled, window not yet elapsed at `--as-of` | no (listed) |
| NOT_TRIGGERED | entry never touched in window | no (listed) |
| UNSCORED | `direction = neutral` (2 calls) | no (listed) |
| UNRESOLVABLE / STALE | missing symbol data / OHLCV ends before needed window | no (listed) |
| SKIPPED | override `skip: true` | no (listed) |

Nothing is silently dropped — every ledger line appears in the audit trail with its
state and reason.

## Setup-family tagging

Crude first-match keyword map over `raw_quote` + `entry` text (a grouping key, not a
model), in priority order: `sweep_reclaim` → `vp_level` (POC/VAL/VAH) → `ref_level`
(PDL/PDH/PWH/PWL/range high-low) → `ema_trend` → `flow` (CVD/OI/absorption/delta) →
`accumulation_zone` → `breakout_deviation` → `other`. The assigned family prints in
the audit trail; mistags are fixable via the overrides sidecar (`family` field).

## Outputs

### 1. Markdown report (stdout)

1. Per-author roll-up: n, triggered, open, wins, losses, hit%, avg R, avg ATR-R,
   `⚠ n<5` marker (threshold via `--min-n`, default 5; descriptive only).
2. Per setup-family × direction roll-up (same columns).
3. **Per-call audit trail**: author · symbol · dir · call_ts · parsed entry/stop/
   target · parse_confidence · family · state · R — so misparses are visible.

### 2. Priors sidecar — `docs/plans/pundit-priors.json`

```json
{
  "generated_at": "2026-07-04T12:00:00Z",
  "as_of": "2026-07-04T00:00:00Z",
  "policy": {
    "windows": {"intraday": "48h", "swing": "30d", "unspecified": "14d"},
    "atr": "atr14 1h intraday / 1d swing",
    "min_n_marker": 5
  },
  "authors": {
    "JordiCharts": {
      "n": 4, "triggered": 3, "open": 1, "resolved": 2,
      "hit_rate": 0.5, "avg_r": 0.4, "avg_atr_r": 0.9,
      "families": {"flow": {"n": 3}, "vp_level": {"n": 1}}
    }
  },
  "families": {
    "sweep_reclaim": {"long": {"n": 6, "hit_rate": 0.6, "avg_atr_r": 1.1}}
  }
}
```

Gitignored (lives in `docs/plans/`). This is the machine hook for the daily market
brief and the F2 trade card.

## CLI

```shell
PYTHONPATH=. poetry run python tools/pundit_score.py \
  [--ledger docs/plans/pundit-calls.jsonl] \
  [--overrides docs/plans/pundit-overrides.jsonl] \
  [--db analytics.db] \
  [--as-of 2026-07-04T00:00:00Z] \
  [--json docs/plans/pundit-priors.json] \
  [--min-n 5]
```

`--as-of` defaults to now; a fixed `--as-of` + fixed inputs ⇒ byte-identical output
(reproducibility + testability). The priors JSON is **always written** (default path
`docs/plans/pundit-priors.json`; `--json` overrides the path). Makefile wrapper:
`make buibui-pundit-score`.

## Error handling

- Malformed JSONL line → warn with line number, skip (counted in the report header).
- Unknown symbol / no OHLCV → UNRESOLVABLE, listed with reason.
- OHLCV max timestamp earlier than the call's needed window at `--as-of` → STALE,
  listed with a "sync first" hint.
- DuckDB opened `read_only=True` (matches the other audit drivers).

## Testing (pytest, no network, in-memory DuckDB)

- Parser: numeric, `k`-suffix, comma/`$`, zone (hyphen/en-dash/"to"), multi-number
  low-confidence, sanity-gate rejection, `entry_px` preference, override precedence,
  `skip`.
- Walk: trigger touch vs NOT_TRIGGERED, thesis-entry fill, adverse-first same-bar
  tie, OPEN at `--as-of`, expiry exits.
- R: all four branches; ATR14 horizon-matching (1h vs 1d).
- Family tagger: one case per family + priority order.
- Outputs: priors JSON schema, `--as-of` determinism (two runs byte-identical).

## Definition of Done

`make lint-py` ✓ · `make typecheck` ✓ (mypy strict) · `make test` green ·
`make test-regression` goldens unmoved · `make lint-md` ✓ (this spec) — each result
stated plainly.

## Follow-ups (out of scope for this PR)

1. **`/ingest-x` additive numeric fields** — teach the skill to emit optional
   `entry_px/stop_px/target_px` on new ledger lines (old lines stay valid).
2. **De-biased verdicts** — point `audit_guard`-style gates at author/family cells
   once any cell reaches real n (≥30).
3. **Daily market-brief integration** — the brief consumes `pundit-priors.json`
   (next queued task after this tool).
4. **Promotion to `analytics/`** — only when another consumer imports the logic.
