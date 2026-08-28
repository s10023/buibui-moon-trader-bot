"""Buibui CLI — `card` subcommand (AI trade card; advisory — card-place is the one exception that routes orders)."""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import time
from typing import Any

import duckdb

from analytics.brief._common import parse_as_of_ms
from analytics.data_store import DEFAULT_DB_PATH
from card.client import ClaudeCliClient
from card.config import CARD_HORIZONS, CardConfig
from card.errors import CardError
from card.ledger import append_ledgers
from card.prompt import build_prompt
from card.render import render_card
from card.run import generate_card
from card.state import AccountProvider, OpenPosition, snapshot_market_state
from card.telegram import card_telegram_body
from portfolio.sizing import SizingConfig
from utils.telegram import send_telegram_message

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
        """Margin balance = wallet balance + cross unrealised PnL.

        **Not the bare `balance` field**, which is wallet-only and so
        under-reports equity while an open position is in profit (and
        over-reports while it is underwater). Every consumer treats this as
        equity: ``portfolio.sizing.resolve_capital`` turns it into the sizing
        capital *and* into the ``daily_r`` unit, so a wallet-only read
        mis-sizes every trade and mis-scales the daily circuit breaker
        together, in the same direction — the same coupled failure the
        ``10_000.0`` capital constant caused before #573.

        ``crossUnPnl`` is absent from some payloads, so a missing field
        degrades to wallet balance rather than to ``None``.

        **Deliberately equals what the live trading path already computes.**
        ``trade/binance_futures.py::get_equity`` reads
        ``futures_account()["totalMarginBalance"]``, which is exactly
        wallet + unrealised PnL — so the card and the XS executor now agree on
        what "equity" means, and Binance's own field name is the authority for
        that definition. The two are kept as separate calls on purpose:
        ``futures_account()`` is the heavier endpoint and the card already
        holds a ``futures_account_balance()`` response. They coincide for a
        USDT-margined account; ``totalMarginBalance`` is account-wide across
        assets, this sum is USDT-only. **If you ever change one, change both**
        — a card sizing off a different equity than the executor is the kind of
        divergence that only shows up in a post-mortem.
        """
        try:
            for b in self._client.futures_account_balance():
                if b.get("asset") == "USDT":
                    wallet = float(b.get("balance", 0) or 0)
                    unrealised = float(b.get("crossUnPnl", 0) or 0)
                    return wallet + unrealised
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


def _account_provider_for(
    args: argparse.Namespace,
) -> tuple[AccountProvider | None, str | None]:
    """(provider, skip_reason) — None reason means the provider was built.

    The live account is the one card input `--as-of` can NEVER pin: Binance
    serves only current positions and equity, so a past-dated run would splice
    today's account into a state claiming to be from the anchor. Omit it and
    say so, rather than pinning `now_ms` and quietly leaving this live.
    """
    if args.dry_run:
        return None, "no provider (--dry-run)"
    if args.as_of:
        return None, "omitted under --as-of (a live account cannot be pinned)"
    return _build_account_provider(), None


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
    if args.horizon is not None:
        cfg = dataclasses.replace(cfg, horizon=args.horizon)
    sizing = (
        SizingConfig.from_toml(cfg.sizing_toml) if cfg.sizing_toml else SizingConfig()
    )
    now_ms = parse_as_of_ms(args.as_of) if args.as_of else int(time.time() * 1000)
    provider, skip_reason = _account_provider_for(args)
    conn = duckdb.connect(str(args.db), read_only=True)
    try:
        state = snapshot_market_state(
            conn,
            args.symbol,
            cfg,
            sizing,
            now_ms=now_ms,
            account_provider=provider,
            account_skip_reason=skip_reason,
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
    # After the ledger on purpose: the push is best-effort (send_telegram_message
    # logs and returns rather than raising), so persistence never waits on it.
    if args.telegram:
        send_telegram_message(card_telegram_body(final))


def add_card_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    p = subparsers.add_parser(
        "card",
        help="AI trade card for one symbol (advisory; card-place places picked TRADE cards)",
    )
    p.add_argument("symbol", help="e.g. BTCUSDT")
    p.add_argument("--direction", choices=["long", "short"], default=None)
    p.add_argument(
        "--horizon",
        choices=list(CARD_HORIZONS),
        default=None,
        help=(
            "trade horizon, which selects the SCORING window the card is "
            "later resolved against: intraday = 48h, swing = 30d. swing also "
            "drops 1h from the recent-fires scan and re-anchors the rubric to "
            "4h/1d structure. (default: the config's horizon, itself intraday)"
        ),
    )
    p.add_argument(
        "--as-of",
        dest="as_of",
        default=None,
        help=(
            "ISO8601 anchor: composes state as of this moment, admitting only "
            "bars that had CLOSED by then and omitting the live account "
            "(unpinnable). The LLM itself is still nondeterministic, so a "
            "pinned run reproduces the INPUTS, not the card. (default: now)"
        ),
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
    p.add_argument(
        "--telegram",
        action="store_true",
        help=(
            "also push the rendered card to Telegram (every verdict, VETOED "
            "included). Opt-in per run so an exploratory or batch card does "
            "not reach the phone; --dry-run never pushes"
        ),
    )
    p.set_defaults(func=run_card_cmd)
