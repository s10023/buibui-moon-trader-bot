"""`buibui exits` wiring and the live watch's alert-channel refusal (#981)."""

from __future__ import annotations

from typing import Any

import pytest

from cli import exits as cli_exits
from cli.main import build_parser
from trade.exit_manager import read_heartbeat


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


class _QuietAdapter:
    def is_dual_side(self) -> bool:
        return True


def _watch_once(tmp_path: Any, monkeypatch: Any, *extra: str) -> Any:
    monkeypatch.setattr(cli_exits, "_adapter", lambda live: _QuietAdapter())
    hb = tmp_path / "hb.json"
    argv = ["exits", "watch", "--once", "--no-telegram"]
    argv += ["--ledger", str(tmp_path / "ledger.jsonl"), "--heartbeat", str(hb)]
    cli_exits.run_watch(build_parser().parse_args([*argv, *extra]))
    return hb


def test_a_live_watch_writes_a_heartbeat_every_round(
    tmp_path: Any, monkeypatch: Any
) -> None:
    hb = _watch_once(tmp_path, monkeypatch, "--live")
    body = read_heartbeat(hb)
    assert body is not None and body["interval_s"] == 15.0 and body["errors"] == []


def test_a_dry_run_watch_claims_no_watcher(tmp_path: Any, monkeypatch: Any) -> None:
    """A dry run places nothing, so its heartbeat would read as protection that is absent."""
    assert not _watch_once(tmp_path, monkeypatch).exists()


def test_a_failed_heartbeat_write_alerts_and_keeps_watching(
    tmp_path: Any, monkeypatch: Any
) -> None:
    sent: list[str] = []
    monkeypatch.setattr(cli_exits, "make_notifier", lambda telegram: sent.append)

    def boom(path: Any, **kw: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(cli_exits, "write_heartbeat", boom)
    _watch_once(tmp_path, monkeypatch, "--live")  # returns, never raises
    assert len(sent) == 1 and "heartbeat write FAILED" in sent[0]
