# P3 XS-solo — read-only daily target-position generator (design)

**Date:** 2026-06-20
**Sub-project:** #3 of 4 (live wiring), slice 1 — the read-only generator.
**Status:** approved, ready for plan.

## Context

XS cross-sectional momentum is the system's only gate-clearing sleeve (universe
Sharpe **+1.375**, DSR 0.997, PBO 0.295) and the validated deploy core. The
execution-realism capacity stress test (sub-project #1, PR #448) retired the
biggest deploy risk: the edge survives realistic size-aware costs and is GREEN
with >10× margin at the operator's ~\$10k scale. The natural next deliverable is
to **make the book real** — but the first thing that touches the real-money path
must be scoped conservatively.

This slice builds a **read-only daily target-position generator**: given fresh
1d data, emit today's XS target positions (side · governor-scaled leverage ·
\$ notional at ~\$10k). **No order routing** — that is a later, gated step.

### Success metric

The emitted per-instrument targets **exactly equal the validated +1.375 book's
next-bar positions**, proven by a frozen-clock reconciliation test against the
research replay. Anything else silently deploys a different book than the one
with the track record.

## Decisions (locked in brainstorm)

| Decision | Choice | Why |
| --- | --- | --- |
| Data path | Sync local `analytics.db` (Binance 1d), then read | Same source as the research book → byte-reconcilable targets; operator already trades Binance locally |
| Target scaling | Governor-scaled (apply today's causal 20%-vol governor `g`) | Equals the positions whose track record is +1.375; raw vol-parity diverges from the book during vol spikes |
| Live timing | **Next-period** leverage (latest causal forecast), NOT `xs_leverage.iloc[-1]` | The last row is the position the book *held during the last completed bar* (`.shift(1)` alignment); using it lags the validated book by one daily bar and bleeds the edge |
| Output | Terminal table + gitignored JSON snapshot | Snapshot gives an audit trail and a Δ/turnover view tomorrow |
| Module layout | Pure `analytics/xsmom/live.py` + `tools/xsmom_targets.py` + `make buibui-xsmom-targets` | Mirrors the xsmom pattern (tested pure math + read-only tool driver); promotable to a `buibui` subcommand when routing lands |
| Capital | `ForecastConfig()` defaults; capital ~\$10k, CLI-overridable | P1 operator scale |

## The next-period correctness invariant (load-bearing)

The research backtest's leverage matrix is **position-aligned**: `xs_leverage[d]`
is the position *held during bar `d`*, sized from info through `d-1` (the
`.shift(1)` in `xs_leverage`). Standing at the close of the last completed bar
`T`, the operator needs the position to **hold during the next bar `T+1`**, sized
from info through `T`. That is *not* `xs_leverage.iloc[-1]` (which is the bar-`T`
position, already past).

Derivation. Let `demeaned[d] = xs_demeaned_forecasts(closes, cfg)[d]` (causal,
uses closes ≤ `d`). `xs_leverage` is:

```text
xs_leverage[d] = (demeaned[d-1] / 10) * (vol_target / vol_ann[d])
vol_ann[d]     = ew_return_vol(close, vol_span)[d] * sqrt(ann)
ew_return_vol  = pct_change().ewm(span).std().shift(1)     # value at d uses returns <= d-1
```

The **next-period** leverage (hold during `T+1`, info through `T`) is the same
formula one step ahead:

```text
next_lev[T+1] = (demeaned[T] / 10) * (vol_target / vol_ann_asof_T)
vol_ann_asof_T = pct_change().ewm(span).std().iloc[-1] * sqrt(ann)   # UNSHIFTED at T -> uses returns <= T
```

Because `ew_return_vol` bakes in `.shift(1)`, the unshifted `.std().iloc[-1]` is
exactly the value `ew_return_vol` *would* report at a hypothetical `T+1`.
Therefore, **by construction**:

```text
next_period_leverage(closes through T) == xs_leverage(closes through T+1).loc[T+1]
```

i.e. the live target equals the research book's leverage for the first bar after
the cutoff. This is both the backtest↔live consistency guarantee and the
no-look-ahead proof (the live computation used only data ≤ `T`).

The governor is recovered the same way: `g[d]` uses the portfolio pre-governor
returns' trailing vol through `d-1`; the next-period governor uses the unshifted
trailing vol through `T`:

```text
g_next = clip(vol_target / (pre.rolling(gov_window).std().iloc[-1] * sqrt(ann)), g_min, g_max)
```

where `pre` is the sum-of-legs pre-governor return series from `run_xs_backtest`.

## Architecture

```text
make buibui-xsmom-targets
  └─ buibui analytics sync --universe (1d)            # operator/Makefile step; refreshes analytics.db
  └─ tools/xsmom_targets.py   (read-only driver)
       conn   = duckdb.connect(db, read_only=True)
       closes, fundings = load_daily_inputs(conn, universe)      # analytics/forecast/replay.py (reused)
       book   = build_target_book(closes, fundings, cfg, capital)# analytics/xsmom/live.py (NEW, pure)
       prev   = load_latest_snapshot(snapshot_dir)               # Δ / turnover
       print_target_table(book, prev)
       write_snapshot(book, snapshot_dir)                        # gitignored docs/plans/xsmom_targets/
```

The generator is **read-only** w.r.t. `analytics.db` and the live daemon. The
`sync` is an operator step the Makefile target runs in front of it; the Python
driver opens the DB `read_only=True`. The only file write is the gitignored
snapshot.

## Components

### `analytics/xsmom/live.py` (NEW, pure — no DB I/O)

- `next_period_leverage(closes, cfg) -> pd.Series` — per-instrument next-period
  vol-parity leverage (the math above). Demean is cross-sectional over the
  active set (skipna), matching `xs_demeaned_forecasts`. Returns NaN for
  not-yet-warmed-up instruments.
- `next_period_governor(pre_returns, cfg) -> float` — unshifted trailing-vol
  governor as-of-`T`, clipped `[g_min, g_max]`. Cold start (< `gov_window`
  history) → neutral `1.0`.
- `build_target_book(closes, fundings, cfg, capital) -> TargetBook` — runs
  `run_xs_backtest` once for the `pre` series, computes `g_next` ×
  next-period per-leg leverage, assembles `TargetPosition`s. Active legs only
  (NaN leverage → excluded / flat).
- `reconcile(closes, cfg, cutoff) -> float` — frozen-clock max-abs-diff between
  `next_period_leverage(closes[:cutoff])` and `xs_leverage(full).loc[first bar
  after cutoff]`. ~0 when correct. Used by the test and an optional
  `--reconcile` flag.
- Snapshot (de)serialization: `target_book_to_dict` / `target_book_from_dict`.

### Data shapes

```python
@dataclass(frozen=True)
class TargetPosition:
    symbol: str
    side: str            # "long" | "short" | "flat"
    leverage: float      # governor-scaled, signed
    notional_usd: float  # leverage * capital
    forecast: float      # demeaned (relative-strength) signal, for context

@dataclass(frozen=True)
class TargetBook:
    as_of_date: str          # ISO date of the last completed 1d bar (T)
    next_period_date: str    # ISO date these targets are held during (T+1)
    capital: float
    governor: float          # g_next
    active_count: int
    gross_leverage: float    # sum |leverage|
    net_leverage: float      # sum leverage (≈ small residual; XS is ~dollar-neutral)
    positions: list[TargetPosition]
```

### `tools/xsmom_targets.py` (NEW, read-only driver)

- Args: `--db` (default `analytics.db`), `--capital` (default 10_000),
  `--config` (optional TOML for `ForecastConfig.from_toml`), `--reconcile`
  (run the frozen-clock check on a recent cutoff and print max-abs-diff),
  `--no-snapshot` (skip the file write).
- Loads inputs, builds the book, loads the latest prior snapshot, prints the
  table with a Δ-vs-prev column, writes today's snapshot.

### Snapshot JSON (gitignored `docs/plans/xsmom_targets/<next_period_date>.json`)

```json
{
  "as_of_date": "2026-06-19",
  "next_period_date": "2026-06-20",
  "capital": 10000.0,
  "governor": 1.0,
  "active_count": 23,
  "gross_leverage": 1.84,
  "net_leverage": 0.09,
  "positions": [
    {"symbol": "BTCUSDT", "side": "long", "leverage": 0.12, "notional_usd": 1200.0, "forecast": 3.4}
  ]
}
```

`docs/plans/` is already fully gitignored.

## Terminal output

```text
XS target positions — as_of 2026-06-19 → hold 2026-06-20   capital $10,000
SYM        SIDE   LEV     $NOTIONAL    Δ$ vs prev
BTCUSDT    long   +0.12   +1,200       +150
ETHUSDT    short  -0.08     -800       -120
...
governor g=1.00   active=23   gross=1.84   net=+0.09
```

## Testing

- **Reconciliation** across several historical cutoffs: `reconcile(...)` ≈ 0
  (the backtest↔live equivalence + no look-ahead).
- **Governor reconciliation**: `next_period_governor` at cutoff `D` matches the
  book's governor for the first bar after `D`.
- **No-look-ahead perturbation** (mirrors the existing xsmom causality test):
  bumping any post-`D` close leaves the as-of-`D` target unchanged across the
  coupled cross-section.
- **Dollar-neutral sum**: active legs' demeaned forecasts sum ≈ 0.
- **Snapshot round-trip**: `to_dict` → `from_dict` is identity.
- **Δ computation** against a prior snapshot.
- **Warm-up / empty edge**: < `min_history` (288) bars → flat/empty book, no
  crash; cold-start governor → 1.0.

## Scope guard (YAGNI — explicitly out of slice 1)

- No order routing / exchange calls.
- No contract/quantity conversion (needs lot size + live mark price = routing).
- No Telegram push.
- No risk overlay (position limits / kill-switch / drawdown control = #4).
- No `buibui` CLI subcommand (tool + make target only; promote later).
- `run_xs_backtest` and the engine are **untouched** → goldens stay frozen; the
  change is purely additive and default-off.

## Definition of Done

- `make lint-py` ✓
- `make typecheck` ✓ (mypy strict)
- `make test` green (new tests above)
- `make test-regression` goldens **UNMOVED** (additive; no engine change)
- `make lint-md` ✓ for this spec + the eventual plan/verdict docs

## References

- Capacity verdict: `docs/audits/2026-06-20-p3-xsmom-capacity.md`
- XS sleeve: `analytics/xsmom/` (`book.py`, `replay.py`), `docs/audits/2026-06-16-p3-xsmom-sleeve.md`
- Shared inputs: `analytics/forecast/replay.py::load_daily_inputs`, `analytics/forecast/config.py`
- Monetization stack (L7–L8): `docs/redesign/2026-06-05-top-tier-quant-redesign.md`
