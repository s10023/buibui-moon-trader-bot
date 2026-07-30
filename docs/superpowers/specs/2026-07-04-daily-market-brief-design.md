# Daily Market Brief — Design

Date: 2026-07-04 · Status: approved (brainstorm session) · Branch: `feat/market-brief`

## Goal (one line)

A deterministic, read-only daily prep sheet for the operator's discretionary trading —
where price sits versus measured reference levels and structure, what a typical such
day does (own stats), and what the scored pundit board says — computed by one pure
`analytics/brief/` lib and rendered to terminal/markdown and the Web UI.

## Background

- Operator goal: daily/weekly/monthly market analysis made concrete. Template:
  @cryptic_heych's "Native FP" panel (ingest batch 7) — a compact per-symbol gauge of
  price vs key levels with a directional read and invalidation. Our level vocabulary
  is `analytics/reference_levels.py` (PDH/PDL/PWH/PWL/DO/WO/MO/MonH/MonL) plus
  `analytics/zones_lib.py` structure (FVG/OB/EQH-EQL/BOS).
- Every input already exists: the stats package (P1P2/ADR/DOW/session/weekly cards),
  `analytics/regime.py`, `zones_lib` + `reference_levels`, and — once the
  pundit-ledger scorer (PR #469 plan) runs — `docs/plans/pundit-priors.json`, which
  was explicitly designed as "the machine hook for the daily market brief".
- The entry-sim audit (2026-06-26) found the fvg/bos/eqh_eql structural families carry
  real information at first touch — the same zones the brief surfaces.

## Decisions (settled in brainstorm, 2026-07-04)

| Question | Decision |
| --- | --- |
| Primary job | Discretionary prep sheet (system state is a footer, not the point) |
| Surfaces v1 | Pure lib + terminal/markdown + Web UI page; Telegram push = v1.1 |
| Cadence | On-demand, cron-ready (`--as-of` reproducibility); VPS daily timer later |
| Symbols | coins.json majors (configurable); universe symbols not in v1 panels |
| Grain | Daily brief with HTF context strip; no separate weekly brief in v1 |
| Rendering | Deterministic template; no LLM anywhere in v1 |
| Home | `analytics/brief/` package (stats//forecast//xsmom/ conventions) |
| v1 sections | Level gauge · regime · zones · seasonality strip · pundit board · health footer |
| Deferred | Funding line, XS-book section, Telegram, weekly digest, LLM narration |

## Non-goals (v1)

- **No signal generation, no verdicts.** The brief describes; it never gates or fires.
- **No DB writes, no schema change, no golden movement.** Read-only over `analytics.db`
  (`duckdb.connect(..., read_only=True)` in the CLI driver; shared `get_db` in the API).
- **No dependency on the pundit scorer's code.** File contract only
  (`pundit-calls.jsonl` + `pundit-priors.json`); the brief must work while PR #469's
  implementation is still in flight, and degrade gracefully when either file is absent.
- **No new detector, no new data source.** Detector family stays FROZEN.
- **No Telegram in v1** (standing rule: Telegram is push-only; wiring happens with the
  VPS timer in v1.1).

## Architecture

```text
analytics/brief/                 pure lib — no module-level side effects
├── __init__.py     re-exports: BriefConfig, BriefBundle, compute_brief, render_markdown
├── config.py       BriefConfig (frozen dataclass)
├── types.py        LevelRow, ZoneRow, SeasonalityStrip, SymbolPanel,
│                   PunditAuthorPrior, PunditCallRow, PunditBoard, HealthRow,
│                   HealthReport, BriefBundle (+ bundle_to_dict)
├── levels.py       reference-level gauge (compute_levels + ATR distances + sweeps)
├── zones.py        nearest active structural zones (zones_lib, 4h + 1d)
├── seasonality.py  day-ahead strip distilled from analytics/stats computes
├── pundit.py       pundit board from the two gitignored files (no code import)
├── health.py       staleness + file-presence report
├── bundle.py       compute_brief(conn, cfg) → BriefBundle (orchestrator)
└── render.py       render_markdown(bundle) → str
```

Thin surfaces (no logic beyond arg parsing / model mapping):

- `cli/brief.py` — `buibui brief` subcommand registered in `cli/main.py`; Makefile
  target `buibui-brief`.
- `web/api/routers/brief.py` — `GET /api/brief`; pydantic response models in
  `web/api/models/brief.py`; wired in the FastAPI app like the other routers.
- `web/ui` — new `Brief` Svelte page + nav entry rendering the bundle JSON natively
  (the UI does NOT render the markdown string).

## Config

```python
@dataclass(frozen=True)
class BriefConfig:
    symbols: tuple[str, ...]  # default: coins.json keys; fallback
    # ("BTCUSDT", "ETHUSDT", "SOLUSDT") if absent
    as_of_ms: int  # resolved by the caller; drives everything
    stats_days: int = 180  # stats-package convention
    zone_tfs: tuple[str, ...] = ("4h", "1d")
    max_levels_per_side: int = 4
    max_zones_per_side: int = 2  # per timeframe
    recent_call_days: int = 14  # pundit board recency window
    max_recent_calls: int = 10
    ledger_path: Path = Path("docs/plans/pundit-calls.jsonl")
    priors_path: Path = Path("docs/plans/pundit-priors.json")
```

`symbols` resolution happens in the surfaces (CLI/API), not the lib: CLI default =
`load_coins_config()` keys (coins.json is gitignored; on `FileNotFoundError` fall back
to the tuple above and note it in the health footer).

## Anchor semantics & determinism (load-bearing)

- `as_of_ms` defaults to *now* in the CLI; `--as-of <ISO8601>` overrides. **Fixed
  `as_of` + fixed DB + fixed files ⇒ byte-identical markdown and JSON** (same
  reproducibility contract as the pundit scorer).
- **No wall-clock anywhere in the bundle.** `as_of` is the only timestamp. Ages
  (candle staleness, priors-file age, call age) are all computed relative to `as_of`.
- A bar is **completed** iff `open_time + tf_ms <= as_of_ms`. The local Binance sync
  keeps the still-forming candle in `ohlcv` (the trap `xsmom/replay._drop_unclosed_daily`
  solved) — every consumer below states which side of that line it uses.
- **Reference price** = close of the last completed 1d bar. Rationale: the intended
  read time (MYT morning) is right after the 00:00 UTC close, and the daily cron will
  run at ~00:10 UTC — the 1d close is minutes-to-hours old. A fresher 1h-close
  reference is a v1.1 option, not v1.
- The **day ahead** (the day being prepped) = the UTC calendar day containing `as_of`.
  Its weekday drives the seasonality strip; `compute_levels(daily_df, as_of_ms)`
  receives the *full* daily frame **including** the forming bar so DO/WO/MO (opens fix
  at period start) resolve for the current day, while the module's own look-ahead rules
  keep prior-period extremes honest.

## Section semantics (pre-committed)

### 1. Level gauge (`levels.py`)

- `compute_levels(daily_df, as_of_ms)` → the 9 `LEVEL_NAMES` (None-safe).
- `atr14` = Wilder ATR(14) over **completed** 1d bars (same formula as
  `analytics/regime.py::_atr_wilder`; reimplemented locally — that helper is private
  and the brief must not import private names).
- Per level: `dist_atr = (level − ref_close) / atr14`, side = above/below by sign,
  sorted by `|dist_atr|`, capped at `max_levels_per_side` per side.
- Sweep flags for the 6 extreme levels (PDH/PDL/PWH/PWL/MonH/MonL) via
  `reference_levels.sweep_flag` on the completed-1d frame at the last completed bar
  index, default `lookback=3`; direction `"long"` for lows (wick below, close back
  above), `"short"` for highs. Rendered as `swept✓`.
- Panel header also shows `atr14` (absolute) and ADR% — computed **locally** from the
  completed-1d frame (mean of `(high − low) / open` over the last 14 completed bars),
  NOT via `stats.adr.compute_adr` (that reads 1h data and windows from wall-clock
  *now*, which would break `--as-of` determinism).

### 2. Structural zones (`zones.py`)

- Extractors: `extract_fvg_zones`, `extract_order_block_zones`, `extract_eqh_eql_zones`,
  `extract_bos_zones` — default params, over **completed** bars of each `zone_tfs` tf.
- Keep `active == True` zones only. `dist_atr` is signed like levels:
  `(nearest_edge − ref_close) / atr14` in ATR14(1d) units (one consistent unit across
  tfs) — positive above, negative below; `0.0` + `inside` marker when
  `zone_low <= ref_close <= zone_high`.
- Pool all four types per tf, sort by `|dist_atr|`, keep `max_zones_per_side` above and
  below per tf. Label: `4h FVG·bull 60,800–61,050 (−0.25)`; when
  `zone_low == zone_high` (line-like zones, e.g. BOS) render the single price.

### 3. Regime read (part of `SymbolPanel`)

- `regime.classify_series` over completed 1d and 4h frames; take the last label of
  each (`trend` / `range` / `high_vol` / `unknown`). Insufficient history renders
  `unknown` — never an error.

### 4. Seasonality strip (`seasonality.py`)

All from the existing stats computes at `stats_days`, keyed to the day-ahead weekday
(short name, e.g. `"Fri"`):

- **DOW line** — from `compute_dow_patterns` row for that weekday: `bull_pct`,
  `avg_range_pct`, `avg_return_pct`, `sample_days`.
- **Session line** — from `compute_session_breakdown`: the session with max `high_pct`
  and the session with max `low_pct` (e.g. `high most often NY 44% · low Asia 39%`).
- **Weekly line** — `compute_weekly_p2_timing`: `low_still_ahead_by_dow[dow]` and
  `high_still_ahead_by_dow[dow]`; plus `compute_weekly_p1p2` modal `low_day`/`high_day`
  (`typical week: low Mon · high Fri`).

Stats computes raise `ValueError` on missing data — `seasonality.py` catches per
symbol and returns an empty strip (rendered as `seasonality: n/a`), never crashes.

**Determinism amendment (found at plan time):** the four consumed stats functions
(`compute_dow_patterns`, `compute_session_breakdown`, `compute_weekly_p1p2`,
`compute_weekly_p2_timing`) window from wall-clock *now* via `_start_ms(days)`. They
gain an **additive keyword-only `end_ms: int | None = None`** parameter — `None`
keeps the exact current behaviour (all existing callers unchanged, byte-identical);
the brief passes `end_ms=as_of_ms` so the strip is `--as-of`-reproducible. These
queries read **1h** OHLCV, so the brief's seasonality strip depends on 1h data.

### 5. Pundit board (`pundit.py`) — file contract only

- **Priors** (`pundit-priors.json`, written by the scorer): parse `generated_at`,
  `policy.min_n_marker`, `authors{}`, `families{}`. Render top authors and top
  families by `n` desc (cap 8 each — a render constant, not config) with
  `n / hit_rate / avg_r / avg_atr_r`, `⚠`
  when `n < min_n_marker`. File absent → render the calls list plus a one-line note:
  `priors: not found — run make buibui-pundit-score`. Malformed → same note with
  `unreadable`; never crash.
- **Recent calls** (`pundit-calls.jsonl`): lines with `call_ts_utc` within
  `recent_call_days` of `as_of`, newest first, cap `max_recent_calls`. Per row:
  author (+ prior stats inline when the priors file has that author), symbol,
  direction, `entry`/`target` free text (truncated at 60 chars with `…`), `horizon`,
  age in days, and a `●` marker when the symbol is one of the brief's panel symbols.
  Unparseable lines are skipped and counted (`n skipped` in the health footer).
- The board deliberately does **not** compute trigger-distance from the free-text
  levels — that needs the scorer's parsed per-call output (v1.1 follow-up).

### 6. Health footer (`health.py`)

- Per symbol × tf in `zone_tfs + ("1h",)` (default `("4h", "1d", "1h")` — health
  tracks exactly the tfs the brief consumes: panels read 4h/1d, the seasonality
  stats read 1h): age of the last **completed** bar relative to
  `as_of`; `⚠ stale` when at least one full bar is missing (age ≥ 2·tf_ms), `✓`
  otherwise, `✗ no data` when the frame is empty.
- Files: priors `generated_at` age in days (or `absent`); ledger call count +
  skipped-line count (or `absent`).
- Header `data OK` ⇔ no `⚠`/`✗` anywhere; otherwise `data ⚠ (see health)`.

## Types (sketch — exact fields frozen at plan time)

```python
@dataclass(frozen=True)
class LevelRow:
    name: str
    price: float
    dist_atr: float
    swept: bool


@dataclass(frozen=True)
class ZoneRow:
    tf: str
    zone_type: str
    direction: str
    zone_low: float
    zone_high: float
    dist_atr: float
    inside: bool


@dataclass(frozen=True)
class SymbolPanel:
    symbol: str
    ref_close: float
    ref_close_ts_ms: int
    atr14: float
    adr_pct: float | None
    regime_1d: str
    regime_4h: str
    levels_above: list[LevelRow]
    levels_below: list[LevelRow]
    zones_above: list[ZoneRow]
    zones_below: list[ZoneRow]
    seasonality: SeasonalityStrip | None
    error: str | None  # per-symbol failure stub (panel renders the error)


@dataclass(frozen=True)
class BriefBundle:
    as_of_ms: int
    day_ahead: str  # "Fri 2026-07-04"
    panels: list[SymbolPanel]
    pundit: PunditBoard
    health: HealthReport
```

`bundle_to_dict(bundle)` produces the JSON-safe dict (ms ints preserved; floats
rounded at render time only, not in the dict) consumed by the API and `--json`.

## Rendering (`render.py`)

Deterministic markdown, one string, no wall-clock. Layout (abbreviated mock):

```text
BUIBUI DAILY BRIEF — Fri 2026-07-04 · as-of 2026-07-04 00:00 UTC · data OK

── BTCUSDT ──────────────────────────────────────────────
Close 61,420 · Regime 1d range / 4h high_vol · ATR14(1d) 2,180 · ADR 3.6%
Levels   above → DO 61,980 (+0.26) · PDH 62,120 (+0.32) · WO 63,010 (+0.73) · PWH 64,850 (+1.57)
         below → PDL 60,340 (−0.50, swept✓) · MonL 59,720 (−0.78) · PWL 58,900 (−1.16) · MO 55,400 (−2.76)
Zones    4h FVG·bull 60,800–61,050 (−0.25) · 4h BOS·bull 60,980 (−0.20) · 1d OB·bear 63,200–63,900 (+0.90)
Day/Week Fri: bull 58% · avg range 2.9% (n=24) · high most often NY 44% · low Asia 39%
         week: low still ahead 42% · high still ahead 65% · typical low Mon / high Fri

── PUNDIT BOARD (priors 0d old · ledger 36 calls · 14 recent) ─
● cryptic_heych (n=3 · 67% · +0.9 ATR-R̄) BTC short — "~61,770 pwVAH reject" → "pwPOC 59,803 / pwVAL 58,450" · swing · 1d
● exitpumpBTC (⚠ n=1) BTC long — "50–60k accumulation" · swing · 2d
FAMILIES  sweep_reclaim/long n=6 · 60% · +1.1 ATR-R̄ | vp_level/short n=4 ⚠

── HEALTH ── BTC 1d✓ 4h✓ · ETH 1d✓ 4h✓ · SOL 1d✓ 4h⚠ stale (2 bars) · priors 0d · ledger 36 (0 skipped)
```

Formatting rules: prices `,`-grouped with the symbol's natural precision (≥1000 → 0
decimals, ≥1 → 2, else 4); ATR distances signed, 2 decimals; percentages 0 decimals.
Times shown in UTC in markdown (level anchors are UTC-native); the **UI** additionally
shows MYT per the standing timezone convention.

## CLI

```shell
poetry run python buibui.py brief \
  [--symbols BTCUSDT ETHUSDT] [--db analytics.db] \
  [--as-of 2026-07-04T00:10:00Z] [--days 180] \
  [--json out.json] [--markdown out.md]
```

Prints the markdown brief to stdout; `--json`/`--markdown` additionally write files.
Opens the DB `read_only=True`. Exit 0 unless **every** panel failed (then 1).
Makefile target `buibui-brief` (accepts `SYMBOLS=`/`AS_OF=` overrides). This is the
same entry point the future VPS timer wraps (`deploy/run-*.sh` pattern).

## API + UI

- `GET /api/brief?symbols=BTCUSDT,ETHUSDT&days=180&as_of=<ISO>` — all optional;
  defaults mirror the CLI. Uses `Depends(get_db)` + `Depends(require_token)` like
  `live_outcomes.py`; **never cached** (computes fresh per request); response model
  `BriefResponse` mirrors `bundle_to_dict`.
- Svelte `Brief` page: one card per panel (levels rendered as a vertical
  gauge/ladder around the close, zones and strip beneath), a pundit-board card, a
  health strip. Dark minimal style; MYT display timestamps. Implementation task MUST
  load `/frontend-design` + `/frontend-svelte` first (CLAUDE.md rule) and verify with
  a Playwright screenshot.

## Error handling

- Per-symbol try/except in `compute_brief`: a failing symbol yields a `SymbolPanel`
  stub with `error` set; the brief still renders (mirrors `run_backfill` resilience).
- Pundit files: absent/malformed → notes, never exceptions (see §5).
- Stats `ValueError` (no data) → empty strip per §4; empty OHLCV → `✗ no data` panel
  stub + health row.
- The API returns 200 with error stubs embedded (the UI shows them); it returns 4xx
  only for invalid params.

## Testing

- Pytest, in-memory DuckDB, synthetic OHLCV fixtures; no network (repo convention).
- Per-module: level distances/side-split/sweep flags; zone selection incl. `inside`
  and the active-only filter; seasonality distillation from seeded frames; pundit
  parsing (happy path, absent file, malformed JSON line, absent priors); health
  staleness boundaries (exactly 1 bar missing vs fresh); completed-bar boundary
  (`open_time + tf_ms == as_of_ms` is completed).
- End-to-end: fixed `as_of` + seeded DB + fixture ledger/priors → **byte-stable
  markdown** assertion (golden string in the test, not a fixtures/ golden — the
  regression suite's goldens must not move).
- API: FastAPI `TestClient` happy path + param validation.
- UI: manual Playwright screenshot at review time (no automated UI test in v1).

## Definition of Done

`make lint-py` ✓ · `make typecheck` ✓ (mypy strict) · `make test` green ·
`make test-regression` goldens **unmoved** · `make lint-md` ✓ for docs ·
README + CLAUDE.md updated (new CLI subcommand, package, API route, UI page) ·
first real run eyeballed against the mock.

## Follow-ups (out of scope for v1)

1. **Telegram push** — VPS daily timer runs `buibui brief` and sends a condensed
   digest via `utils/telegram.py` (with the existing dead-man's-switch wrapper).
2. **Funding line** — per-symbol current/trailing funding from `funding_rates`.
3. **XS-book section** — fold `tools/xsmom_targets.py`'s summary into the footer.
4. **Trigger-distance pundit rows** — consume the scorer's parsed per-call levels
   (requires promoting the parser to `analytics/` per the scorer spec's rule, or an
   additive per-call block in `pundit-priors.json`).
5. **Weekly digest grain** — Monday edition leaning on the weekly stats family.
6. **LLM narration** — a `--narrate` synthesis paragraph; belongs with the F2
   trade-card work, not here.
7. **Fresher reference price** — optional last-completed-1h close for intraday runs.
