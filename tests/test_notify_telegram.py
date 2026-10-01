"""Tests for tools/notify_telegram.py — the /telegram-mode CLI and the sender (#828).

Detection, rendering, dedup and the hook itself are covered by the dependency-free
suite `.claude/hooks/test_telegram_notify.py`, which CI runs in the markdownlint job.
This file covers what needs the project venv: the allowlist CLI, `send` routing
through `utils.telegram`, and the bare invocation.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from tools import notify_telegram as nt

REPO = Path(__file__).resolve().parent.parent


def _mode(tmp_path: Path, *args: str) -> int:
    return nt.main(["mode", *args, "--allowlist", str(tmp_path / "allow.toml")])


class TestModeCli:
    def test_status_without_a_file_reads_off(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert _mode(tmp_path, "status") == 0
        out = capsys.readouterr().out
        assert "telegram-mode: OFF · skills: (none)" in out
        assert not (tmp_path / "allow.toml").exists()

    def test_add_turns_the_mode_on_and_round_trips(self, tmp_path: Path) -> None:
        assert _mode(tmp_path, "add", "/card") == 0
        assert _mode(tmp_path, "add", "brief") == 0
        allow = nt.load_allowlist(tmp_path / "allow.toml")
        assert allow.enabled is True
        assert sorted(allow.skills) == ["brief", "card"]

    def test_off_keeps_the_list_and_on_restores_it(self, tmp_path: Path) -> None:
        _mode(tmp_path, "add", "card")
        _mode(tmp_path, "off")
        off = nt.load_allowlist(tmp_path / "allow.toml")
        assert off.enabled is False and off.skills == ["card"]
        assert not off.wants("card")
        _mode(tmp_path, "on")
        assert nt.load_allowlist(tmp_path / "allow.toml").wants("card")

    def test_remove(self, tmp_path: Path) -> None:
        _mode(tmp_path, "add", "card")
        _mode(tmp_path, "add", "brief")
        _mode(tmp_path, "remove", "card")
        assert nt.load_allowlist(tmp_path / "allow.toml").skills == ["brief"]

    def test_add_without_a_skill_is_a_usage_error(self, tmp_path: Path) -> None:
        assert _mode(tmp_path, "add") == 2

    def test_test_push_renders_and_sends_once(self, tmp_path: Path) -> None:
        with patch.object(nt, "send_html") as send:
            assert _mode(tmp_path, "test") == 0
        send.assert_called_once()
        message = send.call_args.args[0]
        assert message.startswith("<b>buibui: /telegram-mode finished</b>")
        body = message.split("<pre>", 1)[1].rsplit("</pre>", 1)[0]
        assert max(len(line) for line in body.splitlines()) <= nt.WIDTH


class TestAllowlistFile:
    def test_bad_skills_type_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "a.toml"
        p.write_text('skills = "card"\n', encoding="utf-8")
        with pytest.raises(ValueError, match="skills"):
            nt.load_allowlist(p)

    def test_bad_enabled_type_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "a.toml"
        p.write_text('enabled = "yes"\nskills = []\n', encoding="utf-8")
        with pytest.raises(ValueError, match="enabled"):
            nt.load_allowlist(p)


class TestSend:
    def test_send_reads_stdin_and_routes_through_utils_telegram(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sent: list[str] = []
        fake = MagicMock(side_effect=lambda text: sent.append(text))
        monkeypatch.setattr("utils.telegram.send_telegram_message", fake)
        monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: True)
        monkeypatch.setattr(sys, "stdin", _Stdin("<b>x</b>\n<pre>y</pre>"))
        assert nt.main(["send"]) == 0
        assert sent == ["<b>x</b>\n<pre>y</pre>"]


class _Stdin:
    def __init__(self, text: str) -> None:
        self._text = text

    def read(self) -> str:
        return self._text


class TestModuleImportIsStdlibOnly:
    """The hook suite runs in a dependency-free CI job, so importing must not pull deps."""

    def test_import_does_not_load_requests_or_dotenv(self) -> None:
        code = (
            "import sys; import tools.notify_telegram; "
            "bad = [m for m in ('requests', 'dotenv', 'utils.telegram') if m in sys.modules]; "
            "print(','.join(bad)); sys.exit(1 if bad else 0)"
        )
        proc = subprocess.run(
            [sys.executable, "-c", code], cwd=REPO, capture_output=True, text=True
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr


class TestBareInvocation:
    def test_bare_invocation_works(self, tmp_path: Path) -> None:
        proc = subprocess.run(
            [
                sys.executable,
                "tools/notify_telegram.py",
                "mode",
                "status",
                "--allowlist",
                str(tmp_path / "a.toml"),
            ],
            cwd=REPO,
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, proc.stderr
        assert "telegram-mode: OFF" in proc.stdout


def test_wants_matches_plugin_prefixed_names() -> None:
    allow: Any = nt.Allowlist(enabled=True, skills=["card"])
    assert allow.wants("card") and allow.wants("superpowers:card")
    assert not allow.wants("cards")
