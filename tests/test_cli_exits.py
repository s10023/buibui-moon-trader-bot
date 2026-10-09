"""`buibui exits` wiring and the live watch's alert-channel refusal (#981)."""

from __future__ import annotations

from typing import Any

import pytest

from cli import exits as cli_exits
from cli.main import build_parser


def test_exits_subcommands_parse() -> None:
    parser = build_parser()
    args = parser.parse_args(
        ["exits", "arm", "ethusdt", "--side", "LONG", "--stop", "1", "--tp1", "2"]
    )
    assert args.func is cli_exits.run_arm and args.tp1_frac == 0.5
    args = parser.parse_args(["exits", "watch", "--live", "--once"])
    assert args.func is cli_exits.run_watch and args.live and not args.no_telegram


def test_live_watch_refuses_without_telegram(monkeypatch: Any) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "")
    args = build_parser().parse_args(["exits", "watch", "--live", "--once"])
    with pytest.raises(SystemExit, match="--no-telegram"):
        cli_exits.run_watch(args)


def test_notifier_escapes_html_and_survives_a_failed_push(monkeypatch: Any) -> None:
    sent: list[str] = []

    def boom(text: str) -> None:
        sent.append(text)
        raise RuntimeError("telegram down")

    monkeypatch.setattr("utils.telegram.send_telegram_message", boom)
    cli_exits.make_notifier(telegram=True)("stop <moved> & gone")
    assert sent == ["stop &lt;moved&gt; &amp; gone"]
