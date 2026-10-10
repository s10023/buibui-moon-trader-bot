"""Orchestrator for the XS-solo executor: state -> book -> plan -> overlay -> submit.

The only stateful module: reads/writes a small gitignored JSON state file
(peak equity high-water mark + kill-switch + last-run summary). Sizes the target
book off live account equity, runs the fail-closed overlay before any write, and
isolates per-order submission failures. Reads the analytics DB read-only via
`replay_targets`; never writes it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.xsmom.live import TargetBook
from analytics.xsmom.replay import replay_targets
from portfolio.sizing import round_to_tick
from trade.binance_futures import make_client_order_id
from trade.overlay import AccountState, OverlayVerdict, RiskLimits, evaluate_overlay
from trade.routing import ExchangeFilters, OrderIntent, OrderPlan, build_order_plan


class _Adapter(Protocol):
    mode: str

    def get_equity(self) -> float: ...
    def get_positions(self) -> dict[str, float]: ...
    def get_marks(self, symbols: list[str]) -> dict[str, float]: ...
    def get_filters(self, symbols: list[str]) -> dict[str, ExchangeFilters]: ...
    def ensure_account_config(self, symbols: list[str], *, leverage: int) -> None: ...
    def get_book_tops(self, symbols: list[str]) -> dict[str, tuple[float, float]]: ...
    def get_open_orders(self) -> list[dict[str, Any]]: ...
    def cancel_order(
        self, symbol: str, *, order_id: int | None = None, algo_id: int | None = None
    ) -> None: ...
    def submit(
        self, intent: OrderIntent, price: float | None = None
    ) -> dict[str, Any]: ...


# Every order this executor sends carries `xs-<run>-<symbol>` as its client
# order id (#1023). The prefix is what lets a run cancel its OWN stale orders
# and nothing else; python-binance's own ids start `x-`, and Binance's app and
# web orders `ios_` / `android_` / `web_`, so none of them can match.
CLIENT_ID_PREFIX = "xs"


def xs_client_order_id(run_s: int, symbol: str) -> str:
    """One id per symbol per run: `build_order_plan` emits at most one intent per symbol."""
    return make_client_order_id(CLIENT_ID_PREFIX, run_s, symbol)


def is_xs_order(row: dict[str, Any]) -> bool:
    return str(row.get("clientOrderId") or "").startswith(f"{CLIENT_ID_PREFIX}-")


@dataclass(frozen=True)
class ExecutionResult:
    verdict: OverlayVerdict
    plan: OrderPlan
    book: TargetBook
    submitted: list[OrderIntent]
    failed: list[tuple[OrderIntent, str]]
    equity: float
    mode: str
    marks: dict[str, float] = field(default_factory=dict)
    positions: dict[str, float] = field(default_factory=dict)


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"peak_equity": 0.0, "kill_switch": False, "last_run": {}}
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("peak_equity", 0.0)
    data.setdefault("kill_switch", False)
    data.setdefault("last_run", {})
    return data


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _data_age_hours(as_of_date: str, now: pd.Timestamp) -> float:
    # The 1d bar labelled `as_of_date` closes at the end of that UTC day.
    close = pd.Timestamp(as_of_date, tz="UTC") + pd.Timedelta(days=1)
    return float((now - close).total_seconds() / 3600.0)


def run_once(
    conn: Any,
    adapter: _Adapter,
    cfg: ForecastConfig,
    symbols: list[str],
    limits: RiskLimits,
    *,
    no_trade_band_frac: float,
    exchange_leverage: int,
    state_path: Path,
    now: pd.Timestamp | None = None,
    capital_override: float | None = None,
) -> ExecutionResult:
    now = now if now is not None else pd.Timestamp(datetime.now(UTC))
    state = load_state(state_path)
    prior_peak = float(state["peak_equity"])
    kill = bool(state["kill_switch"])

    # `capital_override` sizes the book off a fixed capital (and reports it as
    # equity) instead of the live account balance — used for a capital-matched
    # testnet vs real-account A/B. Default keeps the live-equity behavior.
    equity = capital_override if capital_override is not None else adapter.get_equity()
    book = replay_targets(conn, cfg, equity, symbols=symbols, now=now)
    data_age = _data_age_hours(book.as_of_date, now)

    # Cancel stale resting orders BEFORE the planning read. A resting order is
    # not a position, so yesterday's unfilled order would otherwise sit on the
    # book while today's plan submits another one — an overshoot in which both
    # orders are individually correct. Scoped by client order id (#1023): only
    # orders this executor sent (the `xs-` prefix) are cancelled, each by its
    # own orderId, on whatever symbol they rest — so the operator's own orders
    # survive even on a symbol the book trades. This replaced a symbol-wide
    # cancel over the managed set. ⚠ It does NOT make a shared account safe:
    # `build_order_plan` still closes every position that is not in the book.
    # Runs BEFORE `evaluate_overlay` deliberately: a kill-switched or
    # drawdown-halted run still clears its own resting orders rather than
    # leaving them able to fill while the book is halted.
    stale = sorted(
        (r for r in adapter.get_open_orders() if is_xs_order(r)),
        key=lambda r: (str(r["symbol"]), int(r["orderId"])),
    )
    for row in stale:
        adapter.cancel_order(str(row["symbol"]), order_id=int(row["orderId"]))

    positions = adapter.get_positions()
    all_symbols = sorted(set(symbols) | set(positions))
    marks = adapter.get_marks(all_symbols)
    filters = adapter.get_filters(all_symbols)
    book_tops = adapter.get_book_tops(all_symbols)
    # Fail-safe: a held position with a missing/zero mark would under-count
    # current gross and wrongly trip the looser cold-start turnover cap. When
    # we cannot price the whole held book, pass None so the overlay falls back
    # to the tighter steady-state cap.
    current_gross_notional: float | None
    if positions and any(marks.get(sym, 0.0) == 0.0 for sym in positions):
        current_gross_notional = None
    else:
        current_gross_notional = sum(
            abs(qty) * marks[sym] for sym, qty in positions.items()
        )
    plan = build_order_plan(
        book,
        positions,
        marks,
        filters,
        no_trade_band_frac=no_trade_band_frac,
        capital=equity,
    )

    account = AccountState(equity=equity, peak_equity=prior_peak, kill_switch=kill)
    verdict = evaluate_overlay(
        plan,
        book,
        account,
        limits,
        data_age,
        current_gross_notional=current_gross_notional,
    )

    submitted: list[OrderIntent] = []
    failed: list[tuple[OrderIntent, str]] = []

    run_s = int(now.timestamp())
    if verdict.allowed:
        adapter.ensure_account_config(symbols, leverage=exchange_leverage)
        for planned in plan.intents:
            intent = planned
            price: float | None = None
            try:
                intent = replace(
                    planned, client_order_id=xs_client_order_id(run_s, planned.symbol)
                )
                if intent.order_type == "LIMIT":
                    top = book_tops.get(intent.symbol)
                    if top is None:
                        raise RuntimeError(f"skip:no_book_top {intent.symbol}")
                    bid, ask = top
                    raw = bid if intent.side == "BUY" else ask
                    tick = filters[intent.symbol].price_tick
                    price = round_to_tick(raw, tick, intent.side)
                adapter.submit(intent, price)
                submitted.append(intent)
            except Exception as exc:  # per-order isolation
                # Carry the price into the failure text. A GTX rejection means
                # the limit would have crossed, and price-beside-side is what
                # tells you the tick rounding went the wrong way — a rejection
                # RATE above noise is decision 4's diagnostic, and it is
                # unreadable without the price.
                detail = str(exc) if price is None else f"{exc} (price={price})"
                failed.append((intent, detail))

    # A `capital_override` run is a HYPOTHETICAL — it sizes the book off a fixed
    # capital instead of the account, which is all it is documented to do. Letting
    # it ratchet the real high-water mark turns a throwaway A/B into a mutation of
    # a safety control, and the ratchet never decays: on 2026-08-06 a single
    # `--capital 5000` against an account whose true equity was 1201.33 left
    # peak 5000.00 / floor 3750.00 standing — 3.1x the real equity — which would
    # have gone on halting the book even if the account tripled. Only a run that
    # measured real equity may move the peak.
    #
    # The A/B does NOT get its own state file, deliberately: `kill_switch` lives
    # here too, and a kill switch that did not apply to capital-pinned runs would
    # be a worse hole than the one this closes.
    new_peak = prior_peak if capital_override is not None else max(prior_peak, equity)
    state["peak_equity"] = new_peak
    state["last_run"] = {
        "ts": now.isoformat(),
        "next_period_date": book.next_period_date,
        "mode": adapter.mode,
        # Recorded because `last_run` keeps only the LAST run: without this, a pin
        # that poisons the peak leaves no trace on disk and the incident is not
        # diagnosable after the fact (it was not, on 2026-08-06).
        "capital_override": capital_override,
        "submitted": len(submitted),
        # The ids are the join key for a per-order fill or maker-share read.
        "client_order_ids": [i.client_order_id for i in submitted],
        "cancelled_stale": len(stale),
        "skipped": len(plan.skipped),
        "failed": len(failed),
        "aborts": verdict.aborts,
    }
    save_state(state_path, state)

    return ExecutionResult(
        verdict=verdict,
        plan=plan,
        book=book,
        submitted=submitted,
        failed=failed,
        equity=equity,
        mode=adapter.mode,
        marks=marks,
        positions=positions,
    )
