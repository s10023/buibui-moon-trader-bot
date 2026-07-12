"""ClaudeCliClient: command/env/cwd contract, envelope parsing, retry."""

from __future__ import annotations

import json
import subprocess
from typing import Any

import pytest

from card.client import ClaudeCliClient, LLMResponse
from card.errors import CardError


def _envelope(result: str) -> str:
    return json.dumps(
        {
            "subtype": "success",
            "is_error": False,
            "result": result,
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "total_cost_usd": 0.01,
        }
    )


class RecordingRunner:
    def __init__(self, outputs: list[Any]) -> None:
        self.outputs = outputs
        self.calls: list[dict[str, Any]] = []

    def __call__(self, cmd: list[str], **kwargs: Any) -> Any:
        self.calls.append({"cmd": cmd, **kwargs})
        out = self.outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


def _proc(stdout: str, returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["claude"], returncode=returncode, stdout=stdout, stderr=""
    )


def _client(runner: RecordingRunner) -> ClaudeCliClient:
    return ClaudeCliClient(
        binary="claude",
        model="sonnet",
        timeout_s=5.0,
        config_dir="~/.claude-personal",
        runner=runner,
    )


class TestClaudeCliClient:
    def test_command_env_and_cwd_contract(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-be-stripped")
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok-should-be-stripped")
        runner = RecordingRunner([_proc(_envelope('{"x": 1}'))])
        resp = _client(runner).generate("hello")
        call = runner.calls[0]
        assert call["cmd"] == [
            "claude",
            "-p",
            "--model",
            "sonnet",
            "--output-format",
            "json",
        ]
        assert call["input"] == "hello"
        env = call["env"]
        assert "ANTHROPIC_API_KEY" not in env
        assert "ANTHROPIC_AUTH_TOKEN" not in env
        assert env["CLAUDE_CONFIG_DIR"].endswith(".claude-personal")
        assert "~" not in env["CLAUDE_CONFIG_DIR"]
        import os

        assert call["cwd"] != os.getcwd()  # never the repo
        assert isinstance(resp, LLMResponse)
        assert resp.text == '{"x": 1}'
        assert resp.cost_usd_notional == 0.01
        assert resp.input_tokens == 10

    def test_fenced_result_is_stripped(self) -> None:
        fenced = '```json\n{"x": 1}\n```'
        runner = RecordingRunner([_proc(_envelope(fenced))])
        assert _client(runner).generate("p").text == '{"x": 1}'

    def test_retry_once_then_success(self) -> None:
        runner = RecordingRunner([_proc("", returncode=1), _proc(_envelope("ok"))])
        assert _client(runner).generate("p").text == "ok"
        assert len(runner.calls) == 2

    def test_retry_then_carderror(self) -> None:
        runner = RecordingRunner(
            [_proc("", returncode=1), _proc("not json", returncode=0)]
        )
        with pytest.raises(CardError):
            _client(runner).generate("p")

    def test_timeout_is_carderror(self) -> None:
        exc = subprocess.TimeoutExpired(cmd="claude", timeout=5.0)
        runner = RecordingRunner([exc, exc])
        with pytest.raises(CardError, match="timeout"):
            _client(runner).generate("p")

    def test_error_envelope_is_carderror(self) -> None:
        bad = json.dumps({"subtype": "error", "is_error": True, "result": ""})
        runner = RecordingRunner([_proc(bad), _proc(bad)])
        with pytest.raises(CardError):
            _client(runner).generate("p")
