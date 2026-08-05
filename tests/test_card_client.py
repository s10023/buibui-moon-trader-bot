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

    def test_preamble_before_fence_still_yields_the_card(self) -> None:
        """A preamble ahead of the fence must not cost the whole card.

        Regression for a real 2026-08-05 measurement: the model emitted
        "That tool search wasn't needed - disregard..." ahead of a complete,
        schema-valid card. The old strip only fired when the reply STARTED with
        a fence, so the preamble stayed attached and the card died in
        json.loads downstream. The model is under no contract to stay silent.
        """
        reply = 'That tool search wasn\'t needed - disregard.\n\n```json\n{"x": 1}\n```'
        runner = RecordingRunner([_proc(_envelope(reply))])
        assert _client(runner).generate("p").text == '{"x": 1}'

    def test_unterminated_fence_falls_back_to_historical_strip(self) -> None:
        """No closing fence must not regress to returning the opening marker."""
        runner = RecordingRunner([_proc(_envelope('```json\n{"x": 1}'))])
        assert _client(runner).generate("p").text == '{"x": 1}'

    def test_unfenced_result_is_untouched(self) -> None:
        runner = RecordingRunner([_proc(_envelope('{"x": 1}'))])
        assert _client(runner).generate("p").text == '{"x": 1}'

    def test_thinking_and_tools_are_off_by_default(self) -> None:
        """The shipped default must not change how a card reasons.

        Both knobs are opt-in: the 8x speedup they unlock was validated on ONE
        sample, and the verdict moved against baseline in that sample. This test
        is the guard that nobody flips the default without doing that work.
        """
        runner = RecordingRunner([_proc(_envelope("ok"))])
        _client(runner).generate("p")
        call = runner.calls[0]
        assert "--disallowed-tools" not in call["cmd"]
        assert "--strict-mcp-config" not in call["cmd"]
        assert "MAX_THINKING_TOKENS" not in call["env"]

    def test_restrict_tools_locks_out_every_tool(self) -> None:
        """Assert the command SHAPE — a mocked subprocess cannot catch a bad arg.

        ToolSearch is checked by name on purpose: it was the one tool left
        reachable in the first 2026-08-05 experiment, the model called it, and
        the resulting apology preamble is what broke JSON parsing.
        """
        runner = RecordingRunner([_proc(_envelope("ok"))])
        ClaudeCliClient(
            binary="claude",
            model="sonnet",
            timeout_s=5.0,
            config_dir="~/.claude-personal",
            runner=runner,
            restrict_tools=True,
        ).generate("p")
        cmd = runner.calls[0]["cmd"]
        assert "--strict-mcp-config" in cmd
        assert "--disallowed-tools" in cmd
        for tool in ("Bash", "Read", "WebFetch", "Task", "ToolSearch", "Skill"):
            assert tool in cmd[cmd.index("--disallowed-tools") :]

    def test_max_thinking_tokens_sets_env_when_configured(self) -> None:
        runner = RecordingRunner([_proc(_envelope("ok"))])
        ClaudeCliClient(
            binary="claude",
            model="sonnet",
            timeout_s=5.0,
            config_dir="~/.claude-personal",
            runner=runner,
            max_thinking_tokens=0,
        ).generate("p")
        assert runner.calls[0]["env"]["MAX_THINKING_TOKENS"] == "0"

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

    def test_non_dict_envelope_is_carderror(self) -> None:
        runner = RecordingRunner([_proc("[]"), _proc("[]")])
        with pytest.raises(CardError):
            _client(runner).generate("p")
        assert len(runner.calls) == 2  # retry happened, no AttributeError escape

    def test_error_envelope_is_carderror(self) -> None:
        bad = json.dumps({"subtype": "error", "is_error": True, "result": ""})
        runner = RecordingRunner([_proc(bad), _proc(bad)])
        with pytest.raises(CardError):
            _client(runner).generate("p")

    def test_empty_stdout_on_success_exit_is_carderror(self) -> None:
        # returncode 0 but empty stdout -> json.loads("") must surface as a
        # CardError (bad envelope), not an uncaught JSONDecodeError.
        runner = RecordingRunner([_proc("", returncode=0), _proc("", returncode=0)])
        with pytest.raises(CardError):
            _client(runner).generate("p")
        assert len(runner.calls) == 2  # retried, then raised
