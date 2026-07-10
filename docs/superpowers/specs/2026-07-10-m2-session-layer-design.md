# M2 Session Layer — Design

Brief-v2 milestone M2 (roadmap approved 2026-07-08; follows M0 fixes
PR #473 and M1 indicator states PR #474). Brainstormed + user-approved
2026-07-10.

## Goal (one line)

Additive, read-only session layer for the daily brief — a bundle-level
session clock, a per-symbol recap of the last 3 completed sessions, and
the 180d session-tendency percentages — deterministic at fixed `--as-of`,
mirroring the M0/M1 pattern.

## Background

The brief currently answers "where is price relative to structure"
(levels/zones, M0) and "what state are the indicators in" (M1). It says
nothing about the intraday session cycle the operator actually trades
around: which session is open now, what the last few sessions did, and
which session historically prints the day's extremes. All three are
computable from data the bundle already fetches (62 days of 1h bars per
symbol, M1's `_H1_FETCH_DAYS`) plus one existing stats compute.

## Decisions (settled in brainstorm, 2026-07-10)

1. **Content = clock + 24h recap + tendencies** — the fullest reading of
   the roadmap line "session clock + prior-session recap, reusing
   `stats/session.py`".
2. **Session convention = `stats/session.py` verbatim** — one convention
   across the app; recap windows, clock, and tendency percentages agree
   by construction. No gapless re-partition for the brief.
3. **Recap = the 3 most recently completed sessions** relative to
   `as_of`, whichever calendar day they fall on. The in-progress session
   appears only in the clock — no forming row.
4. **Architecture = Approach A**: a new pure calendar-math primitive at
   `analytics/` level (F2-reusable) + a conn-free brief adapter;
   `bundle.py` stays the only DB-toucher.

## Non-goals

- No trading logic: no new detectors, signals, gates, or live-daemon
  behavior — these are **display states**; the detector family stays
  FROZEN. No edge claim is made for any state.
- No DB schema changes, no new tables, no writes — the brief stays
  read-only.
- No regression-golden movement: nothing on the backtest path changes.
- No new OHLCV fetches (the M1 1h frame covers the recap windows) and no
  4h computes.
- No Telegram surface (brief v1.1 backlog), no session-conditioned
  pundit priors, no F2 consumption yet (F2 is a separate spec).
- No volume-vs-session-average recap column (follow-up).

## Session convention (load-bearing, pre-committed)

`stats/session.py` classifies 1h bars by MYT hour (UTC+8, no DST) with
this SQL CASE, in order:

```text
Asia:   HOUR between 8 and 13
London: HOUR between 14 and 21
NY:     HOUR >= 20 OR HOUR <= 3   (unreachable for 20–21: London wins)
else Off
```

Reused **verbatim as a bar-classification**, this yields a de-facto
partition in MYT: **Asia [08:00, 14:00) · London [14:00, 22:00) ·
NY [22:00, 04:00) (crosses midnight) · Off [04:00, 08:00)**. The
docstring's "NY ≥ 20" hours 20–21 are credited to London by CASE order,
so the effective NY window starts 22:00 MYT — recap windows MUST match
this partition so recap numbers and tendency percentages are computed on
identical windows. The clock labels 20:00–21:59 MYT as "London (NY
overlap)" — a display label only, no window-math change.

All window arithmetic is fixed UTC-offset math (`MYT = UTC+8`, no DST);
windows are derived from `as_of_ms` only.

## Architecture

New modules (→ = imports from):

```text
analytics/session_windows.py    pure calendar math (no DB, no pandas):
                                session_at(as_of_ms), last_completed_windows(as_of_ms, n=3)
analytics/brief/sessions.py     conn-free panel adapter → session_windows;
                                consumes the bundle's completed-1h frame,
                                atr14, and a precomputed SessionResult
```

Touched (all additive):

```text
analytics/brief/types.py        + SessionClock, SessionRecapRow,
                                SessionTendencyRow, SessionState;
                                BriefBundle.session_clock: SessionClock | None;
                                SymbolPanel.sessions: SessionState | None
analytics/brief/bundle.py       + clock computed once per bundle; per panel:
                                compute_session_breakdown(conn, symbol, 180,
                                end_ms=as_of) → adapter call
analytics/brief/render.py       + clock line in the header block;
                                + "Sessions" block per panel
web/api/models/brief.py         + additive optional nested models
web/ui/src/pages/Brief.svelte   + header clock chip + per-card Sessions
                                sub-section + legend entries
```

### `analytics/session_windows.py` (new, pure)

No DB, no pandas — stdlib time arithmetic only.

- `session_at(as_of_ms) -> CurrentSession`: label (`Asia` | `London` |
  `NY` | `Off`), window `start_ms`/`end_ms` containing `as_of_ms`,
  `is_overlap` (true during 20:00–21:59 MYT), next session label +
  `next_start_ms`. From `Off` the next session is `Asia`; from `London`
  the next is `NY` at 22:00 (the partition boundary, not the overlap
  start). Boundary semantics: windows are `[start, end)` — an `as_of`
  exactly at 14:00:00.000 MYT is London.
- `last_completed_windows(as_of_ms, n=3) -> list[SessionWindow]`: the
  `n` most recent Asia/London/NY windows with `end_ms <= as_of_ms`
  (Off windows never count), returned chronologically. Pure — data
  availability is the adapter's concern.

### `analytics/brief/sessions.py` (new adapter)

`build_session_state(completed_1h, atr14, as_of_ms, tendency) ->
tuple[SessionState | None, list[str]]` (state + health notes), mirroring
M1's `build_indicator_state` shape. Conn-free: the tendency argument is
the `SessionResult` the bundle already computed. Exact signature frozen
at plan time.

Recap per window (chronological, oldest → newest):

- Slice the completed-1h frame to `[start_ms, end_ms)` by bar
  `open_time`.
- OHLC: first bar's open, max high, min low, last bar's close.
- `net_pct` = (close − open) / open × 100.
- `net_atr` = (close − open) / atr14 and `range_atr` = (high − low) /
  atr14; if `atr14 <= 0` both are `None`.
- `n_bars` actual vs `expected_bars` = window hours (Asia 6 / London 8 /
  NY 6), derived from the window, never a constant table.
- `made_set_high` / `made_set_low`: whether this session printed the
  highest high / lowest low across the recap rows **present**.

## Component semantics (pre-committed)

### 1. Session clock (bundle-level)

Pure arithmetic from `as_of_ms`; cannot fail; computed once per bundle,
not per symbol. Renderer derives elapsed/remaining from `as_of` and the
window bounds and formats everything in MYT (repo-wide timezone rule).
`Off` renders as "between sessions · next Asia 08:00".

### 2. Recap (per symbol)

Three rows from the last 3 completed windows as computed above. A window
whose 1h slice is **empty** is omitted from the recap with a health note
(this also covers stale-1h symbols: their recent windows simply have no
bars). A **partial** window (`n_bars < expected_bars`) is kept and
rendered with a coverage marker (e.g. `4/6`). If ALL three windows are
empty → `recap = None` + one note.

### 3. Tendency (per symbol)

Three `(session, high_pct, low_pct)` rows copied from
`compute_session_breakdown(conn, symbol, days=180, end_ms=as_of)` —
no re-derivation, no new SQL. The stats dataclass is mirrored into a
brief-local `SessionTendencyRow` (API-model stability; `by_dow` is NOT
carried — Stats-page detail, not brief material). If the compute raises
(no 1h history) → `tendency = None` + note.

### Independence contract

Recap and tendency degrade independently to `None` + a health note; the
panel never becomes an error panel because of the session block. The
clock is bundle-level and cannot fail. Identical to M1's sub-block
contract.

## Determinism & anchors (load-bearing)

- Every window derives from `as_of_ms` alone; recap consumes **completed
  1h bars only** (`completed_bars`, as the rest of `analytics/brief/`).
  Only windows with `end_ms <= as_of_ms` are eligible — no forming
  session, no forming bar.
- `compute_session_breakdown` already takes `end_ms` (M0 work) — passed
  `as_of`, it is as-of-deterministic.
- Same `as_of` ⇒ byte-identical markdown; the existing brief determinism
  test extends to the clock line and Sessions block.

## Types (sketch — exact fields frozen at plan time)

```text
SessionWindow(label: str, start_ms: int, end_ms: int)          # session_windows.py
CurrentSession(label, start_ms, end_ms, is_overlap: bool,
               next_label: str, next_start_ms: int)             # session_windows.py

SessionClock(label, start_ms, end_ms, is_overlap,
             next_label, next_start_ms)                         # brief/types.py
SessionRecapRow(session, start_ms, end_ms,
                open/high/low/close: float,
                net_pct: float, net_atr: float | None,
                range_atr: float | None,
                n_bars: int, expected_bars: int,
                made_set_high: bool, made_set_low: bool)
SessionTendencyRow(session, high_pct: float, low_pct: float)
SessionState(recap: list[SessionRecapRow] | None,
             tendency: list[SessionTendencyRow] | None)

BriefBundle.session_clock: SessionClock | None   # default None; always set by bundle
SymbolPanel.sessions: SessionState | None        # default None
```

All frozen dataclasses, serialised through the existing `asdict`-based
`bundle_to_dict` (additive keys only).

## Rendering (`render.py`)

Exact formats frozen at plan time (M1 precedent). Sketch:

```text
Session: London 14:00–22:00 MYT (NY overlap 20:00+) · 3h12m in / 4h48m left · next NY 22:00
```

in the header block, and per panel:

```text
Sessions (last 3 completed):
  Asia   Thu 08–14 MYT   6/6   net +0.32% (+0.4 ATR)   range 1.1 ATR   set-high
  London Wed 14–22 MYT   8/8   net −0.85% (−1.1 ATR)   range 1.8 ATR   set-low
  NY     Wed 22–04 MYT   6/6   net +0.10% (+0.1 ATR)   range 0.7 ATR
Tendency 180d — day-high: Asia 31% · London 35% · NY 34% · day-low: Asia 38% · London 27% · NY 35%
```

Coverage marker shows only when `n_bars < expected_bars`. Times in MYT.

## API + UI

- `web/api/models/brief.py`: additive optional nested Pydantic models
  mirroring the dataclasses; `GET /api/brief` grows `session_clock`
  (top level) and `sessions` (per panel). No breaking change.
- `Brief.svelte`: a clock chip in the header area + a Sessions
  sub-section per symbol card mirroring the markdown block; the M0
  legend ⓘ card gains entries for the recap columns and the tendency
  line. Frontend tasks load `/frontend-design` + `/frontend-svelte`
  first (repo rule — carried in the sonnet task brief).

## Error handling

Per the independence contract above: empty window → row omitted + note;
all empty → `recap None` + note; tendency compute raises → `tendency
None` + note; notes flow through the existing per-symbol health-note
channel (`notes.append(f"{symbol}: ...")`). Clock cannot fail.

## Testing

- `session_windows`: hour classification at every session incl. the NY
  midnight crossing and the Off gap; overlap flag on/off at 19:59 /
  20:00 / 21:59 / 22:00 MYT; `[start, end)` boundary at exactly 14:00;
  `last_completed_windows` at as_of points inside each session, inside
  Off, and exactly at a boundary (always 3 windows, Off never counted,
  chronological order).
- Adapter: synthetic 1h frames with hand-computed OHLC / net / range
  expectations; empty-window omission; partial-window coverage marker;
  all-empty → `None` + note; `atr14 = 0` → ATR fields `None`;
  set-high/low markers over a reduced row set; tendency embedding +
  tendency-failure degradation.
- Bundle integration on in-memory DuckDB (`:memory:`, repo test rule);
  renderer snapshot; determinism test extension (same `as_of` ⇒
  byte-identical markdown).

## Definition of Done

- `make lint-py` ✓ · `make typecheck` ✓ (mypy strict) · `make test` green
- `make test-regression` — goldens UNMOVED
- `make web-build` clean
- `make lint-md` ✓ (docs)
- CLAUDE.md brief section + README synced (post-branch sweep)

## Execution shape

Spec + plan on `docs/m2-session-layer`; implementation on a single
branch (`feat/m2-session-layer`) → one PR. Sonnet SDD, ~6 tasks,
expected slicing (frozen in the plan doc):

1. `analytics/session_windows.py` + tests
2. Types + `analytics/brief/sessions.py` recap adapter + tests
3. Tendency embed + bundle wiring (clock + per-panel call + health
   notes) + tests
4. Renderer (clock line + Sessions block) + determinism extension
5. API models + Svelte (clock chip, Sessions sub-section, legend) —
   loads `/frontend-design` + `/frontend-svelte`
6. Docs sync (CLAUDE.md / README / `.claude/context/web.md`)

Per-task review + opus final whole-branch review, as in M0/M1.

## Follow-ups (out of scope for M2)

- Volume-vs-session-average recap column.
- Session-conditioned pundit priors / F2 trade-card consumption of the
  clock primitive.
- Telegram brief surface (v1.1 backlog).
- M3 external context (Coinglass/MMT screenshot ingester) per the
  roadmap.
