# XS-solo Order Routing + Risk Overlay — Design Spec

P3 deploy-hardening **sub-project #3 (live wiring), slice 2 + sub-project #4 (risk
overlay)** for the XS-solo deploy core. This slice turns the read-only daily
`TargetBook` (shipped in slice 1, PR #451) into an executable order plan, gated by a
risk overlay, with a Binance Futures adapter that runs **dry-run by default** and can
submit on **testnet** for end-to-end validation. The mainnet flip is deliberately left
for a later supervised slice.

## Goal & success metric

**Goal:** convert the validated XS-solo target book into actual orders, safely.

**Success metric (one line):** a single command produces a correct, overlay-gated
order plan from `analytics.db` + live account state, prints it in dry-run, and submits
it verbatim on Binance Futures **testnet** — with zero code touching `analytics/xsmom/`'s
pure/read-only invariant and zero possibility of an unintended mainnet order.

## Background for the implementer

- The XS-solo sleeve is the deploy core: universe Sharpe **+1.375**, gate-cleared
  (DSR 0.997, PBO 0.295), and validated through execution-realism capacity stress at the
  operator's ~$10k scale (`docs/audits/2026-06-20-p3-xsmom-capacity.md`, GREEN with >10×
  margin). The research question is closed; the binding constraint is now **converting
  validated paper edge into realized PnL**.
- Slice 1 (PR #451) shipped the pure, read-only `analytics/xsmom/live.py`
  (`TargetBook` / `TargetPosition` / `build_target_book` / `replay_targets`) and the
  read-only `tools/xsmom_targets.py`. This slice consumes `TargetBook`; it does **not**
  modify slice-1 code.
- The codebase uses `python-binance` `Client` (authenticated futures methods:
  `futures_position_information`, `futures_account`, `futures_exchange_info`,
  `futures_mark_price`, `futures_create_order`, `futures_change_leverage`,
  `futures_change_margin_type`, `futures_get_position_mode`). `utils/binance_client.py::
  create_client()` returns an authenticated client. There is **no existing order code**
  (`trade/open_trades.py` is empty) — this slice is greenfield and finally populates the
  intended `trade/` package.
- `analytics/xsmom/` is documented as **"Pure, causal, read-only."** All execution
  (I/O, state mutation) code therefore lives under `trade/`, preserving that invariant.

## Locked decisions

| Decision | Choice | Rationale |
| --- | --- | --- |
| Execute boundary | **Testnet-validated executor**: dry-run default · testnet-capable · mainnet gated | Proves the whole path end-to-end at zero capital risk; mainnet flip = later config change |
| Rebalance semantics | **Market orders + no-trade band** | Matches daily cadence + capacity cost model; avoids churning tiny deltas |
| Capital basis | **Actual account equity each run** | Compounds with realized PnL; matches the compounding governor-feedback basis `portfolio/book.py` computes; governor still does vol-targeting so double-adapt is bounded |
| Risk overlay | kill-switch · drawdown halt · gross-leverage cap · per-instrument notional cap · per-run turnover guard · data-staleness guard | From the handoff Task-2 list, plus two bug guards |
| Architecture | **Approach A** — pure core (`routing` + `overlay`) + thin Binance adapter, all under `trade/` | Mirrors the codebase pure-logic / thin-wrapper pattern; isolates real-money I/O into one auditable surface |
| Exchange leverage default | **5×** (overridable) | Modest margin headroom for a ~1–2× gross market-neutral book; does not affect position size |
| Position / margin mode | **one-way + cross** | XS book = one signed net position per symbol; hedge mode → hard abort |

## Architecture (Approach A)

```text
analytics/xsmom/live.py   → TargetBook (slice 1, pure)            ── UNCHANGED
trade/routing.py          → PURE: TargetBook + positions + filters + marks → OrderPlan
trade/overlay.py          → PURE: OrderPlan + book + account + limits → OverlayVerdict
trade/binance_futures.py  → I/O adapter over python-binance Client (injectable)
                              read:  positions, equity, exchangeInfo filters, marks
                              write: market order, set leverage/margin/position-mode
                              modes: dry_run (no write) · testnet · live (gated)
trade/xsmom_executor.py   → orchestrator: state → book(equity) → plan → overlay
                              → dry-run-print | submit; persists gitignored state file
tools/xsmom_execute.py    → CLI + `make buibui-xsmom-execute`
```

Dependency direction: `tools/` → `trade/xsmom_executor` → {`trade/routing`, `trade/overlay`,
`trade/binance_futures`, `analytics/xsmom/replay`}. The pure modules (`routing`, `overlay`)
import nothing from `binance` and do no I/O.

## Data contracts

### `trade/routing.py` (pure)

```python
@dataclass(frozen=True)
class ExchangeFilters:        # per symbol, from futures_exchange_info
    symbol: str
    qty_step: float           # LOT_SIZE stepSize
    min_qty: float            # LOT_SIZE minQty
    min_notional: float       # MIN_NOTIONAL notional

@dataclass(frozen=True)
class OrderIntent:
    symbol: str
    side: str                 # "BUY" | "SELL"
    qty: float                # positive, lot-rounded
    reduce_only: bool         # True when shrinking / closing a leg
    delta_notional: float     # signed, for logging / overlay
    reason: str               # "rebalance" | "open" | "close" | "skip:<why>"

@dataclass(frozen=True)
class OrderPlan:
    intents: list[OrderIntent]    # actionable orders only
    skipped: list[OrderIntent]    # below band / minNotional / no-op (audit trail)
    target_gross_leverage: float
    target_net_leverage: float

def build_order_plan(
    book: TargetBook,
    current_positions: dict[str, float],   # symbol -> signed qty (from exchange)
    marks: dict[str, float],               # symbol -> mark price
    filters: dict[str, ExchangeFilters],
    *,
    no_trade_band_frac: float,             # band as frac of capital (e.g. 0.005)
    capital: float,
) -> OrderPlan: ...
```

`build_order_plan` logic:

- `target_qty = round_down(|notional_usd| / mark, qty_step)`, signed by `notional_usd`.
- `delta_qty = target_qty − current_qty`; `side = BUY if delta_qty > 0 else SELL`.
- `reduce_only = True` when the order shrinks the magnitude of the existing position
  toward zero or flips through zero's reducing leg (closes/trims).
- **Skip** (route to `skipped` with `reason="skip:band"` / `"skip:min_notional"` /
  `"skip:min_qty"` / `"skip:noop"`) when `|delta_notional| < no_trade_band_frac*capital`,
  or the order qty `< min_qty`, or `|delta_notional| < min_notional`, or `delta_qty == 0`.
- **Close intents**: any symbol with a nonzero `current_position` that is absent from the
  target book (or targets flat) gets a `reduce_only` close intent for the full position.
- A symbol missing a `mark` or `filters` entry is skipped with an explicit reason (never
  crashes the plan).

### `trade/overlay.py` (pure)

```python
@dataclass(frozen=True)
class RiskLimits:
    max_gross_leverage: float           # e.g. 3.0
    max_position_notional_frac: float   # per-instrument cap, frac of capital (e.g. 0.5)
    max_drawdown_frac: float            # halt if equity < peak*(1-x) (e.g. 0.25)
    max_run_turnover_frac: float        # abort if a run trades > x of capital (e.g. 1.0)
    max_data_staleness_hours: float     # abort if last 1d bar older than this (e.g. 36)

@dataclass(frozen=True)
class AccountState:
    equity: float
    peak_equity: float                  # from state file, max-updated each run
    kill_switch: bool                   # from state file / flag

@dataclass(frozen=True)
class OverlayVerdict:
    allowed: bool
    aborts: list[str]                   # human-readable breach reasons (empty = allowed)

def evaluate_overlay(
    plan: OrderPlan,
    book: TargetBook,
    account: AccountState,
    limits: RiskLimits,
    data_age_hours: float,
) -> OverlayVerdict: ...
```

Overlay checks (independent; **all must pass**, fail-closed):

| Check | Abort when |
| --- | --- |
| Kill-switch | `account.kill_switch` is True |
| Drawdown halt | `equity < peak_equity * (1 − max_drawdown_frac)` |
| Gross-leverage cap | `plan.target_gross_leverage > max_gross_leverage` |
| Per-instrument notional | any book leg `abs(notional_usd) > max_position_notional_frac * capital` |
| Per-run turnover guard | `Σ abs(intent.delta_notional) > max_run_turnover_frac * capital` |
| Data staleness | `data_age_hours > max_data_staleness_hours` |

Any breach → `allowed=False` with every breach reason collected; the orchestrator submits
**zero** orders (not per-order).

### `trade/binance_futures.py` (I/O adapter)

Thin wrapper over an **injectable** `python-binance` `Client` (tests pass a `MagicMock`).
`mode: Literal["dry_run", "testnet", "live"]`. Read methods always hit the API; write
methods are **no-op-and-log in `dry_run`**.

```python
class BinanceFuturesAdapter:
    def __init__(self, client: Any, mode: str) -> None: ...
    def get_positions(self) -> dict[str, float]:        # symbol -> signed positionAmt
    def get_equity(self) -> float:                       # futures_account totalWalletBalance (+uPnL)
    def get_filters(self, symbols: list[str]) -> dict[str, ExchangeFilters]:
    def get_marks(self, symbols: list[str]) -> dict[str, float]:
    def ensure_account_config(self, symbols, *, leverage: int) -> None:  # one-way+cross+lev; skip in dry_run
    def submit_market(self, intent: OrderIntent) -> dict[str, Any]:      # futures_create_order; skip+log in dry_run
```

`ensure_account_config`:

- `futures_get_position_mode()` → if hedge mode (`dualSidePosition == True`), **raise**
  with a clear message (do not guess `positionSide`).
- `futures_change_margin_type(symbol, "CROSSED")` per symbol, swallowing the idempotent
  "no need to change" error (code `-4046`).
- `futures_change_leverage(symbol, leverage)` per symbol.

### `trade/xsmom_executor.py` (orchestrator)

```python
@dataclass(frozen=True)
class ExecutionResult:
    verdict: OverlayVerdict
    plan: OrderPlan
    submitted: list[OrderIntent]
    failed: list[tuple[OrderIntent, str]]   # (intent, error message)
    equity: float
    mode: str

def run_once(
    conn, adapter: BinanceFuturesAdapter, cfg: ForecastConfig,
    symbols: list[str], limits: RiskLimits, *,
    no_trade_band_frac: float, exchange_leverage: int,
    state_path: Path,
) -> ExecutionResult: ...
```

## Run flow (`run_once`)

```text
 1. Load state file (peak_equity, kill_switch, last_run).             [gitignored JSON]
 2. equity = adapter.get_equity()                                     (the sizing capital)
 3. book = replay_targets(conn, cfg, capital=equity, symbols)         (read_only DB)
 4. data_age_hours = now_utc − book.as_of_date close
 5. positions / marks / filters = adapter.get_*()
 6. plan = build_order_plan(book, positions, marks, filters, band, capital=equity)
 7. account = AccountState(equity, peak_equity, kill_switch)
    verdict = evaluate_overlay(plan, book, account, limits, data_age_hours)
 8. if not verdict.allowed:  print aborts; update+write state; return (no orders)
 9. adapter.ensure_account_config(symbols, leverage=exchange_leverage)  (no-op in dry_run)
10. for intent in plan.intents:  try adapter.submit_market(intent)  (per-order try/except)
11. peak_equity = max(peak_equity, equity); write state (last_run summary)
12. return ExecutionResult; CLI prints the summary
```

## Modes & gating

- **`dry_run` (default):** steps 1–8 run for real against live read endpoints; steps 9–10
  log the orders they *would* place and submit nothing. The daily "what would I trade"
  report.
- **`testnet`:** `Client(key, secret, testnet=True)` with `BINANCE_TESTNET_API_KEY` /
  `BINANCE_TESTNET_API_SECRET`; steps 9–10 submit **real testnet orders**. The end-to-end
  validation path for this slice.
- **`live`:** mainnet writes. **Double-gated** — requires `--i-understand-live` *and*
  `BINANCE_ALLOW_LIVE=1`; both absent by default. Not exercised in this slice; present so
  the later flip is a config change, not a code change.

## Exchange mechanics (adapter defaults)

- **Position mode:** one-way; hedge mode → hard abort (above).
- **Margin type:** cross (idempotent set; swallow `-4046`).
- **Exchange leverage:** `--exchange-leverage` default **5×** — margin headroom only;
  does not change position size (size comes from the book's notional).
- **Qty conversion:** `round_down(|notional|/mark, qty_step)`; drop `< min_qty` or
  `notional < min_notional`.
- **Mark price:** `futures_mark_price()` for qty conversion + notional checks.
- **No-trade band:** `--no-trade-band` default **0.005** (0.5% of capital).

## State persistence

`docs/plans/xsmom_targets/execution_state_<scope>.json` (gitignored; `<scope>` ∈
{`dry_run`, `testnet`, `live`} so testnet can never corrupt the mainnet peak):

```json
{
  "peak_equity": 10250.0,
  "kill_switch": false,
  "last_run": {
    "ts": "2026-06-21T08:00:00Z", "next_period_date": "2026-06-22",
    "mode": "dry_run", "submitted": 12, "skipped": 13, "failed": 0,
    "aborts": []
  }
}
```

- `peak_equity` monotonic-max-updated every run (any mode) → true high-water mark for
  drawdown.
- `kill_switch` toggled by `--kill` / `--resume` (deliberate, separate human action; these
  flags only mutate state and exit, they do not run the cycle).

## Safety invariants (non-negotiable)

1. **Dry-run is the default**; submitting requires an explicit non-default mode.
2. **Overlay runs before any write**; a breach blocks *all* orders, not per-order.
3. **`live` mode double-gated** (`--i-understand-live` + `BINANCE_ALLOW_LIVE=1`).
4. **Per-order isolation** in the submit loop — a rejected order (e.g. `-2019` margin) is
   caught, logged, counted `failed`, and does not abort the rest or crash the run.
5. **Read-only DB** — `replay_targets` opens `analytics.db` `read_only=True`; the executor
   never writes the analytics DB and never touches the live signal daemon.
6. **Reduce-only on every shrink/close** so a trim/close can never accidentally flip a
   position.

## Error handling

- Missing per-symbol `mark`/`filters` → that leg skipped with an explicit reason; the plan
  still builds for the rest.
- API/auth failure on a read step (equity/positions/marks/filters) → fail fast with a
  clear message before any plan is built (no partial execution).
- `submit_market` failure → caught per-order, recorded in `ExecutionResult.failed`.
- Insufficient equity for the plan's margin is surfaced as a per-order `-2019`/`-4131`
  failure (the per-run turnover guard + drawdown halt are the proactive backstops).

## Testing strategy

All tests are offline (no real network); the adapter takes an injected `MagicMock` client.
Goldens are **untouched** — this slice is purely additive (no engine/`analytics` change).

- **`tests/trade/test_routing.py`** — `build_order_plan`: qty rounding to `qty_step`;
  band skip; min-notional / min-qty skip; close-absent-symbol intent; reduce-only on
  trims and flips; missing mark/filters skip; gross/net leverage aggregation.
- **`tests/trade/test_overlay.py`** — one test per check (kill-switch, drawdown,
  gross cap, per-instrument cap, turnover guard, staleness) + the all-pass case + a
  multi-breach case collecting several reasons.
- **`tests/trade/test_binance_futures.py`** — adapter parses `positionAmt`/equity/filters/
  marks from MagicMock responses; `submit_market` is a no-op in `dry_run` and calls
  `futures_create_order` with the right args in `testnet`/`live`; `ensure_account_config`
  raises on hedge mode and swallows `-4046`.
- **`tests/trade/test_xsmom_executor.py`** — `run_once` happy path (fake adapter + in-memory
  DuckDB seeded like the xsmom replay tests); overlay-breach path submits zero orders and
  exits with reasons; state file peak-equity update; per-order failure isolation.

## CLI & Make

`tools/xsmom_execute.py`:

```text
--db PATH                 (default DEFAULT_DB_PATH, opened read_only)
--config PATH             (optional ForecastConfig TOML)
--symbols CSV             (override universe)
--mode {dry_run,testnet,live}   (default dry_run)
--no-trade-band FLOAT     (default 0.005)
--exchange-leverage INT   (default 5)
--max-gross-leverage / --max-position-notional-frac / --max-drawdown-frac /
  --max-run-turnover-frac / --max-data-staleness-hours   (RiskLimits)
--i-understand-live       (live gate 1; live gate 2 = BINANCE_ALLOW_LIVE=1 env)
--kill / --resume         (toggle kill-switch in state, then exit)
--state-dir PATH          (default docs/plans/xsmom_targets/)
```

`make buibui-xsmom-execute` wraps the dry-run invocation (`MODE=`/`CAPITAL`-style overrides
following the existing `buibui-*` target convention).

## Scope boundaries

**In scope (this slice):** pure routing + overlay, Binance adapter (dry-run/testnet/gated-live),
orchestrator, state file, CLI + Make target, full unit-test coverage. Validated on
**dry-run + testnet**.

**Out of scope (explicit follow-ups):**

- The **mainnet live flip** — a later supervised slice (the code path exists, gated off).
- **Limit/maker orders** and fill-management — deferred (`Market + band` chosen for v1).
- **Scheduling/cron** automation — run manually (or wired later, like the signal-watch GH
  Actions path).
- **Survivorship magnitude check** (sub-project #2) — independent pre-capital rigor audit,
  not a blocker for this code.

## Definition of Done

- `make lint-py` ✓ · `make typecheck` ✓ (mypy strict) · `make test` green (new
  `tests/trade/` suite passes) · `make test-regression` goldens **UNMOVED** (additive).
- `make lint-md` ✓ for this spec + any doc updates.
- `analytics/xsmom/` and all goldens untouched; the live signal daemon untouched.
- CLAUDE.md + README updated for the new `trade/` execution layer + Make target.

## Self-review notes

- No placeholders / TBDs; every field and check is concrete.
- The compounding-equity capital basis is a deliberate, flagged divergence from the
  fixed-notional research headline (matches the compounding governor-feedback basis); the
  vol governor bounds the double-adapt.
- Scope is a single implementation plan: ~5 new files (`trade/routing`, `trade/overlay`,
  `trade/binance_futures`, `trade/xsmom_executor`, `tools/xsmom_execute`) plus 4 test
  modules and Make/doc updates. No analytics or golden changes.
