"""LLM seam: LLMClient protocol + claude-personal -p subprocess backend."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from card.errors import CardError

RunnerFn = Callable[..., "subprocess.CompletedProcess[str]"]

_STRIP_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")


@dataclass(frozen=True)
class LLMResponse:
    text: str
    model: str | None
    cost_usd_notional: float | None  # envelope total_cost_usd — NOT a charge
    input_tokens: int | None
    output_tokens: int | None


class LLMClient(Protocol):
    def generate(self, prompt: str) -> LLMResponse: ...


def _default_runner(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.run(*args, **kwargs)  # noqa: S603


_FENCE_RE = re.compile(r"```(?:json)?[ \t]*\r?\n(.*?)```", re.DOTALL)

# Every tool the CLI may expose. Disallowing the lot (with --strict-mcp-config)
# removes the model's ability to wander mid-card: measured 2026-08-05, the
# unrestricted call took 4 turns and read ~200K input tokens, the locked-down one
# took 1 turn and ~25.7K. ToolSearch belongs here for a specific reason — it was
# the ONE tool left reachable in the first experiment, the model called it, and
# then apologised for it in a preamble that broke JSON parsing outright.
_ALL_TOOLS = (
    "Agent",
    "Bash",
    "BashOutput",
    "Edit",
    "ExitPlanMode",
    "Glob",
    "Grep",
    "KillShell",
    "MultiEdit",
    "NotebookEdit",
    "Read",
    "Skill",
    "SlashCommand",
    "Task",
    "TodoWrite",
    "ToolSearch",
    "WebFetch",
    "WebSearch",
    "Write",
)


def _strip_fences(text: str) -> str:
    """Return the card JSON from the model's reply.

    This used to strip a fence ONLY when the reply *started* with one, so any
    preamble ahead of the fence took the whole card down with it. That is not
    hypothetical: measured 2026-08-05, a run prefixed "That tool search wasn't
    needed — disregard, I don't need any deferred tools for this analysis." to a
    complete, schema-valid card, and the card was lost to a JSONDecodeError. The
    model is under no contract to stay silent, so tolerate a preamble instead of
    requiring its absence.

    Prefers the first fenced block anywhere in the reply, then falls back to the
    historical opening-fence strip (which also covers an unterminated fence), then
    to the trimmed text.
    """
    t = text.strip()
    m = _FENCE_RE.search(t)
    if m:
        return m.group(1).strip()
    if t.startswith("```"):
        first_nl = t.find("\n")
        t = t[first_nl + 1 :] if first_nl != -1 else ""
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip()


@dataclass(frozen=True)
class ClaudeCliClient:
    """Subscription-auth `claude -p` backend.

    Load-bearing contract (see spec §client.py): keys stripped from env so
    the CLI can never bill pay-per-token; CLAUDE_CONFIG_DIR points at the
    personal config; cwd is a fresh temp dir so repo CLAUDE.md/skills are
    never reloaded into the card call.
    """

    binary: str
    model: str
    timeout_s: float
    config_dir: str
    runner: RunnerFn | None = None
    max_thinking_tokens: int | None = None
    restrict_tools: bool = False

    def generate(self, prompt: str) -> LLMResponse:
        last_exc: CardError | None = None
        for _ in range(2):  # one retry per spec
            try:
                return self._call(prompt)
            except CardError as exc:
                last_exc = exc
        raise CardError(f"LLM call failed after retry: {last_exc}") from last_exc

    def _call(self, prompt: str) -> LLMResponse:
        run = self.runner if self.runner is not None else _default_runner
        cmd = [
            self.binary,
            "-p",
            "--model",
            self.model,
            "--output-format",
            "json",
        ]
        if self.restrict_tools:
            cmd += ["--strict-mcp-config", "--disallowed-tools", *_ALL_TOOLS]
        env = {k: v for k, v in os.environ.items() if k not in _STRIP_ENV}
        env["CLAUDE_CONFIG_DIR"] = str(Path(self.config_dir).expanduser())
        if self.max_thinking_tokens is not None:
            env["MAX_THINKING_TOKENS"] = str(self.max_thinking_tokens)
        with tempfile.TemporaryDirectory() as tmp:
            try:
                proc = run(
                    cmd,
                    input=prompt,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_s,
                    cwd=tmp,
                    env=env,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise CardError(f"LLM timeout after {self.timeout_s}s") from exc
        if proc.returncode != 0:
            raise CardError(f"claude exited {proc.returncode}: {proc.stderr[:500]}")
        try:
            envelope: dict[str, Any] = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise CardError(f"bad envelope JSON: {exc}") from exc
        if not isinstance(envelope, dict):
            raise CardError("envelope is not a JSON object")
        if envelope.get("subtype") != "success" or envelope.get("is_error"):
            raise CardError(f"envelope not success: subtype={envelope.get('subtype')}")
        result = envelope.get("result")
        if not isinstance(result, str):
            raise CardError("envelope missing result text")
        usage = envelope.get("usage") or {}
        cost = envelope.get("total_cost_usd")
        in_tok = usage.get("input_tokens")
        out_tok = usage.get("output_tokens")
        return LLMResponse(
            text=_strip_fences(result),
            model=self.model,
            cost_usd_notional=(float(cost) if isinstance(cost, (int, float)) else None),
            input_tokens=int(in_tok) if isinstance(in_tok, int) else None,
            output_tokens=int(out_tok) if isinstance(out_tok, int) else None,
        )
