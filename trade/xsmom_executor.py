"""Orchestrator for the XS-solo executor: state -> book -> plan -> overlay -> submit.

The only stateful module: reads/writes a small gitignored JSON state file
(peak equity high-water mark + kill-switch + last-run summary). Sizes the target
book off live account equity, runs the fail-closed overlay before any write, and
isolates per-order submission failures. Reads the analytics DB read-only via
`replay_targets`; never writes it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.xsmom.live import TargetBook
from analytics.xsmom.replay import replay_targets
from trade.overlay import AccountState, OverlayVerdict, RiskLimits, evaluate_overlay
from trade.routing import ExchangeFilters, OrderIntent, OrderPlan, build_order_plan


class _Adapter(Protocol):
    mode: str

    def get_equity(self) -> float: ...
    def get_positions(self) -> dict[str, float]: ...
    def get_marks(self, symbols: list[str]) -> dict[str, float]: ...
    def get_filters(self, symbols: list[str]) -> dict[str, ExchangeFilters]: ...
    def ensure_account_config(self, symbols: list[str], *, leverage: int) -> None: ...
    def submit_market(self, intent: OrderIntent) -> dict[str, Any]: ...


@dataclass(frozen=True)
class ExecutionResult:
    verdict: OverlayVerdict
    plan: OrderPlan
    book: TargetBook
    submitted: list[OrderIntent]
    failed: list[tuple[OrderIntent, str]]
    equity: float
    mode: str


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"peak_equity": 0.0, "kill_switch": False, "last_run": {}}
    data: dict[str, Any] = json.loads(path.read_text())
    data.setdefault("peak_equity", 0.0)
    data.setdefault("kill_switch", False)
    data.setdefault("last_run", {})
    return data


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2))


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
) -> ExecutionResult:
    now = now if now is not None else pd.Timestamp(datetime.now(UTC))
    state = load_state(state_path)
    prior_peak = float(state["peak_equity"])
    kill = bool(state["kill_switch"])

    equity = adapter.get_equity()
    book = replay_targets(conn, cfg, equity, symbols=symbols)
    data_age = _data_age_hours(book.as_of_date, now)

    positions = adapter.get_positions()
    marks = adapter.get_marks(symbols)
    filters = adapter.get_filters(symbols)
    plan = build_order_plan(
        book,
        positions,
        marks,
        filters,
        no_trade_band_frac=no_trade_band_frac,
        capital=equity,
    )

    account = AccountState(equity=equity, peak_equity=prior_peak, kill_switch=kill)
    verdict = evaluate_overlay(plan, book, account, limits, data_age)

    submitted: list[OrderIntent] = []
    failed: list[tuple[OrderIntent, str]] = []

    if verdict.allowed:
        adapter.ensure_account_config(symbols, leverage=exchange_leverage)
        for intent in plan.intents:
            try:
                adapter.submit_market(intent)
                submitted.append(intent)
            except Exception as exc:  # per-order isolation
                failed.append((intent, str(exc)))

    new_peak = max(prior_peak, equity)
    state["peak_equity"] = new_peak
    state["last_run"] = {
        "ts": now.isoformat(),
        "next_period_date": book.next_period_date,
        "mode": adapter.mode,
        "submitted": len(submitted),
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
    )
