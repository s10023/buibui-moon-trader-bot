# F2 AI Trade Card v1 — Design

**Date:** 2026-07-08 · **Status:** approved design, pre-implementation ·
**Origin:** todo-master F2 row + `docs/redesign/buibui-ai-trade-suggestion-gaps.md`
(gaps G11 + G12, the two load-bearing gaps the redesign left open)

## Goal & success metric

One command — `buibui card BTCUSDT` — produces a structured, evidence-cited
trade card (TRADE / NO-TRADE / VETOED) from the system's already-shipped state
surfaces, with position size computed deterministically and every card
persisted as a scoreable forecast. **Success metric:** the card ledger
accumulates resolvable calls whose hit-rate/avg_r can be measured by the
existing pundit scorer — F2 ships with its own kill-test attached.

This is the manual-execution bridge: the operator asks for a card before
taking a manual trade; the card is advisory and routes no orders.

## Decisions log (user-approved 2026-07-08)

| # | Decision | Choice |
| --- | --- | --- |
| D1 | Trigger + surface | On-demand CLI only (`buibui card`); API/Svelte/Telegram deferred |
| D2 | LLM client | `claude-personal -p` subprocess (personal Pro plan, $0 marginal); NOT bare `claude` (work account); NOT the Anthropic API (pay-per-token) |
| D3 | Card inputs | Full state incl. live account (open positions + daily realised PnL) |
| D4 | Persistence | Dual-write ledger + auto-score via the existing pundit-scorer machinery |
| D5 | Architecture | New top-level `card/` package (approach A), brief-style pure lib + thin CLI |
| D6 | Money-math | The LLM never computes size/risk; deterministic post-pass enforces hard rules in code |

## Addendum — 2026-07-11 (post-Brief-M0/M1/M2 delta, pre-plan)

Written when the implementation plan was drawn up. Brief milestones M0
(#473), M1 (#474), M2 (#475) merged **after** this spec was approved; the
`panel` block is composed from `compute_brief`, so F2 inherits them with
zero extra plumbing. Where this addendum conflicts with the body above, the
addendum wins.

- **Panel block is richer than specced.** `SymbolPanel` now carries
  `ref_price_source` + a fresh-1h `ref_close` (M0), `indicators:
  IndicatorState | None` (M1 — EMA stack, regime run-length + range bounds,
  Monday-range state, yesterday candle patterns, PA character, BB %B /
  bandwidth / squeeze, weekly+monthly AVWAP dist, 60d volume-profile
  POC/VAH/VAL), and `sessions: SessionState | None` (M2 — last-3-session
  recap vs ATR14 + 180d tendencies). The bundle-level `session_clock`
  (`SessionClock`) also joins `MarketState` as its own field.
- **Rubric update.** The prompt may now cite indicator/session fields that
  actually exist (BB squeeze, AVWAP dist, profile `vs_value`, EMA stack, PA
  label, Monday-range state, session tendencies). The "G1/G2 indicator
  pack" non-goal is **partially superseded** — RSI/MACD still don't exist
  and still must not be claimed.
- **Pundit block = the brief's `PunditBoard`** (composition, not a second
  parser): `compute_brief` already loads priors + ledger with
  status/age/flagged handling, so `MarketState.pundit` is `PunditBoard |
  None` instead of a re-read of `pundit-priors.json`.
- **`recent_fires` annotation corrected.** `confidence_ratings` persists
  `stars / avg_r / win_rate / dsr` (there is no `n` column) — the fires
  block carries those four; the "sample-size < 30 ⇒ downgrade conviction"
  prompt rule keys on missing ratings / low `dsr` instead of `n`.
- **Pundit-row horizon key confirmed** (the spec's implementer note):
  `WINDOWS_MS` keys are `intraday` (48h) / `swing` (30d) / `unspecified`
  (14d) → the ledger row uses `"intraday"`.
- **Module deltas locked in the plan:** `card/run.py` (orchestrator:
  prompt → LLM → parse → one re-ask → post-pass, keeps the CLI thin) and
  `card/errors.py` (`CardError` / `CardValidationError`);
  `snapshot_market_state` additionally takes the `SizingConfig` (daily_r +
  XS capital need it) and injectable `brief_fn` / `targets_fn` seams
  (house DI pattern); `LLMClient.generate` returns an `LLMResponse`
  (text + notional cost + usage) so the ledger cost field stays typed.

## Non-goals / deferred (named, not built)

- Web AI tab, `GET /api/card`, Telegram delivery (v1.1+; the pure lib is the
  reusable core, surfaces are thin wrappers later — same pattern as the brief).
- Anthropic-SDK API-key backend (the `LLMClient` seam exists; add a backend
  only if the subscription path ever becomes limiting).
- G1/G2 indicator pack (RSI/MACD/vol-z) — the prompt is adapted to fields that
  exist today; indicators join the state JSON if/when they ship.
- Journal-derived style priors in the prompt (F2 v2; needs journal ingestion).
- Any order routing. The card is advisory output for a human.

## Architecture

New top-level `card/` package — peer of `portfolio/` and `trade/`, not under
`analytics/`, because it consumes both market data and live account state.
Layering stays clean: `card/` defines narrow provider protocols and the CLI
injects implementations (the account provider is built from
`trade/binance_futures.py` read methods at the CLI layer — `card/` never
imports `trade/`).

```text
card/
├── __init__.py   # public re-exports
├── config.py     # CardConfig frozen dataclass + from_toml ([card] block)
├── state.py      # G12 serialiser: snapshot_market_state(...) -> MarketState
├── prompt.py     # PROMPT_VERSION + system rubric + user payload builder
├── client.py     # LLMClient protocol + ClaudeCliClient subprocess backend
├── card.py       # TradeCard parse/validate + deterministic post-pass
├── ledger.py     # ai-cards.jsonl writer + pundit-calls.jsonl compatible row
└── render.py     # terminal markdown renderer
cli/card.py       # argparse wiring: buibui card SYMBOL [flags]
```

Data flow:

```text
cli/card.py
  └─ snapshot_market_state(conn, symbol, cfg, now, account_provider)
       ├─ brief panel        analytics/brief compute (levels/zones/regime/ATR/ADR/seasonality)
       ├─ pundit priors      docs/plans/pundit-priors.json (graceful-missing)
       ├─ xs forecast        today's docs/plans/xsmom_targets/<date>.json if fresh,
       │                     else analytics/xsmom/replay.replay_targets
       ├─ recent fires       signals history + confidence_ratings (avg_r/stars/dsr/n)
       └─ account state      injected provider → open positions, daily realised PnL
  └─ prompt.build(state)            deterministic; versioned
  └─ client.generate(prompt)        claude-personal -p, bare cwd, keys stripped
  └─ card.parse + card.post_pass    schema validation; sizing; hard rules in code
  └─ render.markdown(final_card)    terminal output
  └─ ledger.append(final_card)      ai-cards.jsonl (+ pundit-calls.jsonl if TRADE)
```

## Module specifications

### `config.py` — `CardConfig`

Frozen dataclass + `from_toml` reading a `[card]` block (house pattern —
mirror `SizingConfig.from_toml` / `BriefConfig`). Fields (defaults):

- `claude_bin: str = "claude"` + `claude_config_dir: str = "~/.claude-personal"`
  — `claude-personal` is a **zsh alias** (`CLAUDE_CONFIG_DIR=~/.claude-personal
  claude`), which `subprocess` cannot invoke; the wrapper reproduces it by
  setting `CLAUDE_CONFIG_DIR` in the child env. The personal config dir is the
  guard that keeps the work account untouched — never launch with the work
  config.
- `model: str = "sonnet"` — passed to `--model`.
- `timeout_s: float = 180.0` — subprocess wall clock.
- `min_rr: float = 1.0` — post-pass floor on planned RR.
- `daily_loss_limit_r: float = -2.0` — circuit breaker threshold in R.
- ~~`valid_hours: float = 12.0` — default card validity window.~~ Dropped as
  dead in the 2026-07-13 follow-up batch: validity is carried by the LLM's
  `valid_until_utc` field directly, and no post-pass rule ever read `valid_hours`.
- `entry_band_pct: float = 5.0` — post-pass sanity band: proposed entry must
  sit within ±this % of `ref_close`.
- `fires_lookback_bars: int = 4` — recent-detector-fire window per TF.
- `fires_timeframes: tuple[str, ...] = ("1h", "4h", "1d")`.
- `sizing_toml: str | None` — path forwarded to `SizingConfig.from_toml`
  (None = P1 defaults: 10k capital, 0.25% r_base, caps).
- `cards_path: str = "docs/plans/ai-cards.jsonl"` (gitignored).
- `pundit_calls_path: str = "docs/plans/pundit-calls.jsonl"` (gitignored).

### `state.py` — the G12 market-state serialiser

```python
def snapshot_market_state(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    cfg: CardConfig,
    *,
    now_ms: int,
    account_provider: AccountProvider | None,
    direction_hint: str | None = None,
) -> MarketState: ...
```

`MarketState` is a frozen dataclass with `to_dict()` (JSON-safe, `asdict`
style, deterministically ordered keys). **Composition, not re-serialisation**
(the standing instruction): every block below reuses a shipped surface.

| Block | Source | Notes |
| --- | --- | --- |
| `panel` | `analytics/brief` `_compute_panel`-equivalent via `compute_brief` with one symbol | levels above/below (dist_atr, swept), zones (dist_atr, inside), regime_1d/4h, atr14, adr_pct, ref_close, seasonality strip |
| `pundit` | `docs/plans/pundit-priors.json` | authors + families with n / hit_rate / avg_r / avg_atr_r; `null` + health note when file missing/stale |
| `xs` | today's `docs/plans/xsmom_targets/<date>.json` if `as_of_date` == today, else `replay_targets` | symbol's `{side, leverage, notional_usd, forecast}` + book `governor`; `null` when symbol not in the book |
| `recent_fires` | signals history + `confidence_ratings` | fired events in the last `fires_lookback_bars` bars per configured TF, each annotated with avg_r / stars / dsr / n (D9-style quality context) |
| `account` | injected `AccountProvider` | open positions `[{symbol, side, qty, entry, mark, upnl_usd}]`, `daily_pnl_usd` (Binance income history, UTC day), `daily_r = daily_pnl_usd / (capital × r_base)`, plus the ledger's resolved daily R as secondary context; `null` + `degraded=True` on provider failure |
| `health` | per-block try/except | one failing block never kills the snapshot (brief's per-symbol isolation contract, applied per block) |

`AccountProvider` is a `Protocol` defined in `card/state.py` (three read
methods); the CLI builds the real one from `trade/binance_futures.py`
(`mode="dry_run"` semantics irrelevant — reads only). Tests pass a fake.

`--as-of` determinism: `now_ms` is threaded everywhere (brief compute, fires
window, daily-PnL window) so the *inputs* are reproducible; the LLM output is
inherently nondeterministic, which is why the ledger stores the state digest.

### `prompt.py`

- `PROMPT_VERSION: str` constant (e.g. `"card-v1"`) — persisted with every
  card so prompt changes are visible in the ledger.
- `build_prompt(state: MarketState, cfg: CardConfig) -> str` — one combined
  prompt string: static rubric first (byte-stable across cards), then the
  state JSON. The rubric is the gaps-doc §1 enhanced prompt **adapted to
  fields that actually exist** (no RSI/MACD/OI-delta claims):
  1. Multi-TF synthesis from `regime_1d`/`regime_4h` + level/zone geometry.
  2. Liquidity map: 3 nearest levels/zones above and below, significance by
     TF + swept flag.
  3. Confluence scan across zones × recent fires × pundit priors × XS
     forecast, scored 0–9.
  4. Decision: TRADE (direction, limit entry, structural SL, TP1/TP2/TP3,
     invalidation, expected hold, valid-until) or NO_TRADE (failed gate named).
  5. Reasoning log: 5–8 bullets, each citing a concrete number from the input
     JSON; hedge words forbidden.
  - Hard rules stated in-prompt (position conflict, circuit breaker, no
    invented values, sample-size < 30 ⇒ downgrade conviction) — **and**
    re-enforced in code (see `card.py`).
  - `direction_hint` (operator-supplied `--direction`): included as context —
    "the operator is considering a {hint}; evaluate that side explicitly, and
    flag if the opposite side scores higher" — the LLM may still answer
    NO_TRADE.
  - Output contract: "respond with ONLY a JSON object matching this schema"
    (schema inlined in the rubric).

### `client.py` — the LLM seam

```python
class LLMClient(Protocol):
    def generate(self, prompt: str) -> str: ...


@dataclass(frozen=True)
class ClaudeCliClient:
    binary: str  # cfg.claude_bin — "claude-personal"
    model: str  # cfg.model
    timeout_s: float
    runner: RunnerFn = subprocess.run  # injectable for tests
```

Invocation contract (each item is load-bearing):

- Command: `[cfg.claude_bin, "-p", "--model", cfg.model, "--output-format",
  "json"]`, prompt on stdin.
- **cwd = a fresh temp dir** (scratch), NOT the repo — prevents `claude -p`
  reloading repo CLAUDE.md/skills into every card. Baseline overhead measured
  2026-07-08: ~7.5k input tokens per bare `-p` call (Claude Code's own system
  prompt), so a full card ≈ 15–30k plan tokens.
- **env = `os.environ` minus `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN`,
  plus `CLAUDE_CONFIG_DIR=<cfg.claude_config_dir expanded>`** — the cost +
  account guard. With keys stripped, the CLI can only use the subscription
  login (it can never silently switch to pay-per-token API billing), and the
  personal config dir keeps the work account out of the path. If no login
  exists the CLI errors → surfaced as a clean `CardError`, nothing written.
- `--output-format json` returns an envelope (verified 2026-07-08: keys incl.
  `subtype`, `is_error`, `result`, `usage`, `modelUsage`, `total_cost_usd`,
  `session_id`): check `subtype == "success"` and `is_error == False`, extract
  `result` text, strip Markdown code fences if present, then JSON-parse the
  card body. `total_cost_usd` is **notional** (computed from tokens even on
  subscription auth — not a charge); log it in the ledger for visibility.
- Timeout / non-zero exit / envelope parse failure → one retry, then
  `CardError`. No ledger writes on failure.

### `card.py` — schema + deterministic post-pass

`TradeCard` (LLM-produced, validated locally — no server-side schema on the
CLI path):

```json
{
  "verdict": "TRADE | NO_TRADE",
  "direction": "long | short | null",
  "entry": 0.0, "sl": 0.0, "tp1": 0.0, "tp2": 0.0, "tp3": 0.0,
  "confluence_score": 0,
  "reasoning": ["... cites a number ...", "..."],
  "invalidation": "...", "expected_hold": "...",
  "valid_until_utc": "ISO-8601",
  "no_trade_reason": "... | null"
}
```

Validation: required keys, types, enum membership, 5–8 reasoning bullets,
`confluence_score` ∈ [0, 9]. One re-ask on validation failure (append the
validation errors to the prompt), then `CardError`.

`post_pass(card, state, sizing_cfg, cfg) -> FinalCard` — pure, deterministic,
fully unit-tested. The LLM proposes prices; the code decides money and rules:

1. **Sizing (P1 reuse):** `risk_per_unit(entry, sl)` → units, notional, and
   `effective_risk_fraction` → risk USD via `portfolio/sizing.py`. Caps
   (`apply_caps`, cluster) applied against currently-open risk when account
   state is available.
2. **Hard-rule enforcement (code, not vibes):**
   - SL on the correct side of entry for the direction; TPs ordered.
   - Planned RR (tp1) ≥ `cfg.min_rr`.
   - No conflicting open position on the symbol.
   - Circuit breaker: `account.daily_r ≤ cfg.daily_loss_limit_r` ⇒ veto.
   - Entry sanity: within a configurable band of `ref_close` (default ±5%).
3. Any violation ⇒ `verdict = "VETOED"` with `veto_reasons: list[str]` —
   regardless of what the LLM said. LLM-declared NO_TRADE passes through
   with its named gate.
4. `account is None` (degraded) ⇒ rules 2c/2d unverifiable ⇒ card keeps its
   verdict but carries `warnings: ["account state unavailable — hard rules
   unverified"]` (warn, not veto: advisory output for a human).

`FinalCard` = TradeCard + `{size_units, notional_usd, risk_usd, risk_frac,
rr_tp1, warnings, veto_reasons, state_digest, prompt_version, model,
generated_at_ms}`.

### `ledger.py`

- **Every** card (TRADE / NO_TRADE / VETOED) → append one JSON line to
  `docs/plans/ai-cards.jsonl`: the full `FinalCard` + `state_digest`
  (sha256 of the canonical state JSON — post-hoc audit can re-serialize the
  inputs and verify).
- **TRADE cards only** → additionally append a pundit-calls-compatible row to
  `docs/plans/pundit-calls.jsonl`. The scorer's `load_ledger` /
  `LedgerCall` (verified 2026-07-08) reads exactly these 12 keys, all
  free-text strings parsed leniently by `parse_level_field`:
  `source` (`"ai-card"`), `author` (`"buibui_card"`), `url` (synthetic unique
  id `ai-card://<generated_at_ms>-<symbol>` — overrides key on `url`, so it
  must be unique), `call_ts_utc` (ISO-8601 Z), `symbol`, `direction`
  (lowercase), `entry`, `stop`, `target` (plain numeric strings; `target` =
  tp1), `horizon` (use the scorer's shortest pre-committed window key —
  implementer: confirm the exact `WINDOWS_MS` key strings, e.g. the 48h
  family; never invent a new key, unknown keys silently fall back to
  `"unspecified"`), `confidence` (confluence score as text), `raw_quote`
  (first reasoning bullet). Result: `make buibui-pundit-score` resolves the
  AI's calls with **zero scorer changes**, and `buibui_card` appears in
  `pundit-priors.json` → the brief's pundit board renders the AI's track
  record next to the humans.
- Both files are already gitignored (`docs/plans/`); no schema/DB change.

### `render.py`

Deterministic markdown renderer (brief's `render.py` pattern): verdict banner
(TRADE ▲ / NO_TRADE ─ / VETOED ✕ + reasons), price table (entry/SL/TPs/RR),
size block (units, notional, risk USD, % of capital, caps applied), reasoning
bullets, confluence score, invalidation + valid-until, warnings, and a
one-line cost/context footer (model, prompt version, state digest short-hash).

### `cli/card.py` + Makefile

```text
buibui card SYMBOL [--direction long|short] [--as-of ISO] [--json]
                   [--dry-run] [--no-ledger] [--config PATH]
```

- `--dry-run`: build state + prompt, print both, **skip the LLM call** (free
  smoke-test path; also what CI/tests exercise end-to-end).
- `--json`: emit the `FinalCard` JSON instead of markdown.
- `--no-ledger`: skip persistence (exploration).
- Makefile: `make buibui-card SYMBOL=BTCUSDT [DIRECTION=long] [DRY=1]`
  wrapping the CLI (house pattern).
- Registration in `cli/main.py` mirrors `brief`/`portfolio` subcommands.

## Cost model (explicit, per the operator's concern)

- No dollars are billed. **Verified end-to-end 2026-07-08** with a live probe
  (bare temp cwd, keys stripped, `CLAUDE_CONFIG_DIR=~/.claude-personal`,
  `--model sonnet --output-format json`): exit 0, model `claude-sonnet-5`,
  inner JSON parsed byte-exact, 7,552 in / 464 out tokens. The personal
  config's only credential is `claudeAiOauth` (subscription) and no
  `ANTHROPIC_API_KEY` exists on the box — there is no billing instrument for
  pay-per-token to charge. The envelope's `total_cost_usd` field is notional,
  not a charge.
- Each card consumes plan **usage allowance** (~15–30k tokens: ~7.5k baseline
  `-p` overhead + lean prompt + state JSON; no repo-context reload thanks to
  the bare cwd). A few cards a day is a small fraction of daily allowance,
  shared with dev sessions.
- Escape valve (deferred): an Anthropic-SDK backend behind the same
  `LLMClient` seam (Haiku ≈ 2–4¢/card) if plan limits ever bind.

## Error handling summary

| Failure | Behavior |
| --- | --- |
| One state block fails (priors missing, XS stale, DB gap) | field `null` + health note; card generation continues with DEGRADED banner |
| Account provider fails | `account = null`; hard rules 2c/2d unverified → warning, not veto |
| `claude-personal` missing / not logged in | clean `CardError` with remediation hint; no ledger write |
| LLM timeout / bad envelope | one retry → `CardError` |
| Card JSON fails schema | one re-ask with validation errors → `CardError` |
| Post-pass rule violation | card persisted as `VETOED` with reasons (that IS the output) |

## Testing strategy

- Pure units throughout; `duckdb.connect(":memory:")`; **no network, no
  subprocess** in tests (`runner` and `AccountProvider` injected fakes).
- `state.py`: block composition, per-block failure isolation, `--as-of`
  determinism (same `now_ms` ⇒ byte-identical `to_dict()`).
- `client.py`: command construction (binary/model/flags), **env-strip
  assertion** (`ANTHROPIC_API_KEY` never reaches the child env), cwd is not
  the repo, envelope parsing incl. fenced JSON, retry-then-error.
- `card.py`: schema validation matrix; post-pass table-driven cases — sizing
  math vs `portfolio/sizing.py` truth, each hard rule individually trips
  VETOED, degraded-account warning path, LLM NO_TRADE pass-through.
- `ledger.py`: ai-cards row round-trips; TRADE-only dual-write; the
  pundit-calls row parses through `tools/pundit_score.py`'s loader.
- `render.py`: golden-string asserts on a fixed FinalCard.
- Regression goldens untouched (no detector/backtest/stats change).

## Definition of Done

- `make lint-py` ✓ · `make typecheck` ✓ (mypy strict) · `make test` green ·
  `make test-regression` goldens unmoved.
- Manual smoke: `make buibui-card SYMBOL=BTCUSDT DRY=1` prints state + prompt
  with no LLM call; a live run produces a rendered card and one ai-cards.jsonl
  row (+ pundit-calls row iff TRADE).
- README + CLAUDE.md project-structure entries for `card/`; memory Current
  State + handoff updated.

## Follow-ups (v1.1+ backlog)

1. `GET /api/card` + Svelte Card page (thin wrappers over the lib).
2. Telegram delivery of TRADE cards.
3. Card-vs-outcome review loop: feed the AI's own scored track record
   (`buibui_card` priors) back into the rubric.
4. G1/G2 indicator pack → additional state blocks.
5. API-key `LLMClient` backend (only if plan limits bind).
