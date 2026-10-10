"""CLI for the exit manager (#981): `buibui exits arm|disarm|status|watch|report`.

`watch` is the only command that can move money, and only with `--live`;
without it, it prints what it would place and records nothing.

Notification surface, chosen because this moves money: every state change
(exits placed, TP1 filled, stand-down, close) and every error prints to the
terminal AND pushes to Telegram. Quiet polls push nothing. A live watch
refuses to start while Telegram is unconfigured unless `--no-telegram` says
the terminal is the only surface on purpose: an unattended watcher whose
alert channel is dead looks exactly like one with nothing to report. Repeats
of one error push once until a clean poll clears them, so a dead IP
allowlist (-2015) alerts once per outage rather than once per poll.
"""

from __future__ import annotations

import argparse
import html
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from card.config import CardConfig
from portfolio.sizing import SizingConfig
from trade.binance_futures import BinanceFuturesAdapter
from trade.exit_manager import (
    DEFAULT_HEARTBEAT_PATH,
    DEFAULT_LEDGER_PATH,
    DEFAULT_TP1_FRAC,
    PollContext,
    SignedPathRejected,
    arm,
    disarm,
    metric_report,
    poll_episode,
    read_ledger,
    write_heartbeat,
)


def _now_ms() -> int:
    return int(datetime.now(tz=UTC).timestamp() * 1000)


def _adapter(live: bool) -> BinanceFuturesAdapter:
    from utils.binance_client import create_client

    return BinanceFuturesAdapter(create_client(), mode="live" if live else "dry_run")


def _sizing(config: str | None) -> SizingConfig:
    cfg = CardConfig.from_toml(config) if config else CardConfig()
    return (
        SizingConfig.from_toml(cfg.sizing_toml) if cfg.sizing_toml else SizingConfig()
    )


def make_notifier(*, telegram: bool) -> Callable[[str], None]:
    def notify(text: str) -> None:
        print(f"[{datetime.now(tz=UTC):%H:%M:%S}Z] {text}")
        if not telegram:
            return
        from utils.telegram import send_telegram_message

        try:
            # parse_mode is HTML; Telegram decodes only &lt; &gt; &amp;.
            send_telegram_message(html.escape(text, quote=False))
        except Exception as exc:  # noqa: BLE001 - a push failure never stops a poll
            print(f"! Telegram push failed ({exc!r}); the line above is the alert")

    return notify


def _telegram_configured() -> bool:
    from utils.telegram import _get_env_credentials

    token, chat = _get_env_credentials()
    return bool(token and chat)


def run_arm(args: argparse.Namespace) -> None:
    adapter = _adapter(live=False)  # arming reads; it never places
    row = arm(
        adapter,
        Path(args.ledger),
        symbol=args.symbol.upper(),
        side=args.side,
        stop=args.stop,
        tp1=args.tp1,
        tp1_frac=args.tp1_frac,
        existing=args.existing,
        dual_side=adapter.is_dual_side(),
        now_ms=_now_ms(),
    )
    when = "on the next poll" if args.existing else "once the entry fills"
    print(
        f"armed {row['episode_id']}: stop {row['stop']}, TP1 {row['tp1']} x "
        f"{row['tp1_frac']} {when}. Nothing is placed until `buibui exits "
        "watch --live` runs."
    )


def run_disarm(args: argparse.Namespace) -> None:
    print(
        disarm(
            Path(args.ledger),
            symbol=args.symbol.upper(),
            side=args.side,
            now_ms=_now_ms(),
        )
    )


def run_status(args: argparse.Namespace) -> None:
    episodes, dropped = read_ledger(Path(args.ledger))
    if dropped:
        print(f"!! {dropped} unparseable line(s) in {args.ledger}")
    active = [ep for ep in episodes if ep.active]
    if not active:
        print("nothing armed")
    for ep in active:
        legs = ", ".join(
            f"{leg.leg} {leg.price}" + (" (filled)" if leg.done else "")
            for leg in ep.legs.values()
        )
        reason = f" - {ep.stand_down_reason}" if ep.stand_down_reason else ""
        print(
            f"{ep.symbol:<10} {ep.side:<5} {ep.status:<10} stop {ep.stop} "
            f"tp1 {ep.tp1} | placed: {legs or 'none'} | fills {len(ep.fills)}{reason}"
        )


def run_report(args: argparse.Namespace) -> None:
    episodes, _ = read_ledger(Path(args.ledger))
    m = metric_report(episodes)

    def pct(x: float | None) -> str:
        return f"{x * 100:.1f}%" if x is not None else "n/a"

    fee = f"{m['fee_r_mean']:.3f}R" if m["fee_r_mean"] is not None else "n/a"
    print(f"episodes {m['episodes']} of {m['target_episodes']} (metric window)")
    print(
        f"maker share of exit fills: {pct(m['maker_exit_share'])} over "
        f"{m['exit_fills']} exit fills (baseline {pct(m['baseline_maker_exit_share'])}, "
        f"#916, n={m['baseline_episodes']})"
    )
    print(
        f"fee R per trade: {fee} over {m['fee_r_n']} episodes "
        f"(baseline {m['baseline_fee_r']:.3f}R, #916)"
    )


def run_watch(args: argparse.Namespace) -> None:
    ledger = Path(args.ledger)
    telegram = not args.no_telegram
    if args.live and telegram and not _telegram_configured():
        raise SystemExit(
            "Telegram is not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID). "
            "A live exit manager must have an alert channel; pass --no-telegram "
            "to run with the terminal as the only surface."
        )
    notify = make_notifier(telegram=telegram and args.live)
    adapter = _adapter(live=args.live)
    sizing = _sizing(args.config)
    dual_side = adapter.is_dual_side()
    print(
        f"exit manager {'LIVE' if args.live else 'DRY RUN - nothing is placed or recorded'}; "
        f"{'hedge' if dual_side else 'one-way'} account; ledger {ledger}"
    )
    alerted: set[str] = set()
    while True:
        episodes, dropped = read_ledger(ledger)
        if dropped:
            # A torn line can be an `intent` or `placed` row, and replaying
            # without it would place a second set of exits. Stop rather than guess.
            notify(
                f"exit-manager: {dropped} unparseable line(s) in {ledger}; refusing "
                "to poll until the ledger is repaired"
            )
            raise SystemExit(2)
        errors: set[str] = set()
        for ep in [e for e in episodes if e.active]:
            ctx = PollContext(
                adapter=adapter,
                ledger_path=ledger,
                dual_side=dual_side,
                sizing=sizing,
                notify=notify,
                now_ms=_now_ms(),
            )
            try:
                poll_episode(ep, ctx)
            except SignedPathRejected as exc:
                errors.add("-2015")
                if "-2015" not in alerted:
                    notify(f"exit-manager: {exc}")
                break  # every signed call fails alike; skip the rest this round
            except Exception as exc:  # noqa: BLE001 - one episode never stops the rest
                key = f"{ep.episode_id}:{type(exc).__name__}"
                errors.add(key)
                if key not in alerted:
                    notify(f"exit-manager {ep.symbol} {ep.side}: poll FAILED ({exc!r})")
        if args.live:  # a dry run protects nothing, so it must not claim a watcher
            try:
                write_heartbeat(
                    Path(args.heartbeat),
                    now_ms=_now_ms(),
                    interval_s=args.interval,
                    errors=sorted(errors),
                )
            except OSError as exc:  # the heartbeat must never stop the watch it reports
                errors.add("heartbeat")
                if "heartbeat" not in alerted:
                    notify(
                        f"exit-manager: heartbeat write FAILED ({exc!r}); still polling"
                    )
        if alerted - errors:
            print(f"cleared: {sorted(alerted - errors)}")
        alerted = errors
        if args.once:
            return
        time.sleep(args.interval)


def add_exits_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    exits = subparsers.add_parser(
        "exits", help="exit manager: rest a stop + maker TP1 on an armed position"
    )
    sub = exits.add_subparsers(dest="exits_command", required=True)

    def _common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--ledger", default=DEFAULT_LEDGER_PATH)

    a = sub.add_parser("arm", help="hand the manager one position (by symbol + side)")
    a.add_argument("symbol")
    a.add_argument("--side", choices=("LONG", "SHORT"), required=True)
    a.add_argument("--stop", type=float, required=True, help="stop trigger price")
    a.add_argument("--tp1", type=float, required=True, help="TP1 limit price")
    a.add_argument(
        "--tp1-frac",
        type=float,
        default=DEFAULT_TP1_FRAC,
        help=f"share of the position TP1 takes (default {DEFAULT_TP1_FRAC})",
    )
    a.add_argument(
        "--existing",
        action="store_true",
        help="protect a position that is already open (never counts to the metric)",
    )
    _common(a)
    a.set_defaults(func=run_arm)

    d = sub.add_parser("disarm", help="stop managing a position")
    d.add_argument("symbol")
    d.add_argument("--side", choices=("LONG", "SHORT"), required=True)
    _common(d)
    d.set_defaults(func=run_disarm)

    s = sub.add_parser("status", help="list armed episodes")
    _common(s)
    s.set_defaults(func=run_status)

    r = sub.add_parser("report", help="the #981 success metric against #916")
    _common(r)
    r.set_defaults(func=run_report)

    w = sub.add_parser("watch", help="poll armed positions; --live to place exits")
    w.add_argument("--live", action="store_true", help="place real orders")
    w.add_argument("--once", action="store_true", help="one poll, then exit")
    w.add_argument("--interval", type=float, default=15.0, help="seconds between polls")
    w.add_argument(
        "--no-telegram",
        action="store_true",
        help="terminal only; a live watch otherwise requires Telegram",
    )
    w.add_argument("--config", default=None, help="card config TOML (for [bet_sizing])")
    w.add_argument(
        "--heartbeat",
        default=DEFAULT_HEARTBEAT_PATH,
        help="file a live watch rewrites every poll round (the daily check reads it)",
    )
    _common(w)
    w.set_defaults(func=run_watch)
