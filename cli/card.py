"""Buibui CLI — `card` subcommand (AI trade card; advisory, routes no orders)."""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

import duckdb

from analytics.brief._common import parse_as_of_ms
from analytics.data_store import DEFAULT_DB_PATH
from card.client import ClaudeCliClient
from card.config import CardConfig
from card.errors import CardError
from card.ledger import append_ledgers
from card.prompt import build_prompt
from card.render import render_card
from card.run import generate_card
from card.state import AccountProvider, OpenPosition, snapshot_market_state
from portfolio.sizing import SizingConfig

_INCOME_TYPES = {"REALIZED_PNL", "COMMISSION", "FUNDING_FEE"}
_INCOME_PAGE_LIMIT = 1000


class BinanceAccountProvider:
    """AccountProvider over the raw python-binance futures client (reads only)."""

    def __init__(self, client: Any) -> None:
        self._client = client

    def positions(self) -> list[OpenPosition]:
        out: list[OpenPosition] = []
        for p in self._client.futures_position_information():
            amt = float(p.get("positionAmt", 0) or 0)
            if amt == 0.0:
                continue
            out.append(
                OpenPosition(
                    symbol=str(p["symbol"]),
                    side="long" if amt > 0 else "short",
                    qty=abs(amt),
                    entry=float(p.get("entryPrice", 0) or 0),
                    mark=float(p.get("markPrice", 0) or 0),
                    upnl_usd=float(p.get("unRealizedProfit", 0) or 0),
                )
            )
        return out

    def daily_pnl_usd(self, start_ms: int, end_ms: int) -> float:
        """Sum realized income over [start_ms, end_ms], paginating the cap.

        Binance /fapi/v1/income returns at most `_INCOME_PAGE_LIMIT` rows in
        ascending time order; a high-churn day exceeds it, and one truncated
        fetch would undercount PnL and mis-fire the loss-limit gate. Walk
        forward by advancing startTime to the last row's time, deduping by
        tranId (rows can share a millisecond) until a short page ends it.
        """
        total = 0.0
        seen: set[Any] = set()
        cursor = start_ms
        while True:
            rows = self._client.futures_income_history(
                startTime=cursor, endTime=end_ms, limit=_INCOME_PAGE_LIMIT
            )
            if not rows:
                break
            for r in rows:
                tran_id = r.get("tranId")
                if tran_id is not None:
                    if tran_id in seen:
                        continue
                    seen.add(tran_id)
                if r.get("incomeType") in _INCOME_TYPES:
                    total += float(r.get("income", 0) or 0)
            if len(rows) < _INCOME_PAGE_LIMIT:
                break
            last_time = max(int(r.get("time", cursor) or cursor) for r in rows)
            if last_time <= cursor:
                break  # no forward progress -> avoid an infinite loop
            cursor = last_time
        return float(total)

    def equity_usd(self) -> float | None:
        try:
            for b in self._client.futures_account_balance():
                if b.get("asset") == "USDT":
                    return float(b.get("balance", 0) or 0)
        except Exception:
            return None
        return None


def _build_account_provider() -> AccountProvider | None:
    """Real provider, or None (degraded, warned) when keys/network absent."""
    try:
        from utils.binance_client import create_client

        return BinanceAccountProvider(create_client())
    except Exception:
        return None


def _fetch_qty_step(symbol: str) -> float | None:
    """Symbol LOT_SIZE step, or None when the exchange is unreachable.

    Reuses the executor's filter parsing rather than re-reading exchangeInfo,
    so the card and the XS router round to the same step. `mode="dry_run"`
    because the card places no orders — this is a pure read.
    """
    try:
        from trade.binance_futures import BinanceFuturesAdapter
        from utils.binance_client import create_client

        adapter = BinanceFuturesAdapter(create_client(), mode="dry_run")
        filt = adapter.get_filters([symbol]).get(symbol)
    except Exception:
        return None
    if filt is None or filt.qty_step <= 0:
        return None
    return float(filt.qty_step)


def run_card_cmd(args: argparse.Namespace) -> None:
    cfg = CardConfig.from_toml(args.config) if args.config else CardConfig()
    sizing = (
        SizingConfig.from_toml(cfg.sizing_toml) if cfg.sizing_toml else SizingConfig()
    )
    now_ms = parse_as_of_ms(args.as_of) if args.as_of else int(time.time() * 1000)
    provider = None if args.dry_run else _build_account_provider()
    conn = duckdb.connect(str(args.db), read_only=True)
    try:
        state = snapshot_market_state(
            conn,
            args.symbol,
            cfg,
            sizing,
            now_ms=now_ms,
            account_provider=provider,
            direction_hint=args.direction,
        )
    finally:
        conn.close()

    if args.dry_run:
        print(json.dumps(state.to_dict(), indent=2, sort_keys=True))
        print("\n----- PROMPT (no LLM call made) -----\n")
        print(build_prompt(state, cfg))
        return

    client = ClaudeCliClient(
        binary=cfg.claude_bin,
        model=cfg.model,
        timeout_s=cfg.timeout_s,
        config_dir=cfg.claude_config_dir,
        max_thinking_tokens=cfg.max_thinking_tokens,
        restrict_tools=cfg.restrict_tools,
    )
    try:
        final = generate_card(
            state,
            cfg,
            sizing,
            client,
            generated_at_ms=int(time.time() * 1000),
            qty_step=_fetch_qty_step(args.symbol),
        )
    except CardError as exc:
        print(f"card generation failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    if args.json:
        print(json.dumps(final.to_dict(), indent=2, sort_keys=True))
    else:
        print(render_card(final))
    if not args.no_ledger:
        for path in append_ledgers(final, cfg):
            print(f"ledger: {path}")


def add_card_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    p = subparsers.add_parser(
        "card",
        help="AI trade card for one symbol (advisory; routes no orders)",
    )
    p.add_argument("symbol", help="e.g. BTCUSDT")
    p.add_argument("--direction", choices=["long", "short"], default=None)
    p.add_argument(
        "--as-of",
        dest="as_of",
        default=None,
        help="ISO8601 anchor for reproducible inputs (default: now)",
    )
    p.add_argument("--db", default=str(DEFAULT_DB_PATH), help="DuckDB path")
    p.add_argument("--config", default=None, help="TOML with a [card] block")
    p.add_argument("--json", action="store_true", help="emit FinalCard JSON")
    p.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="print state + prompt, skip the LLM call (free smoke test)",
    )
    p.add_argument(
        "--no-ledger",
        dest="no_ledger",
        action="store_true",
        help="skip ledger persistence (exploration)",
    )
    p.set_defaults(func=run_card_cmd)
