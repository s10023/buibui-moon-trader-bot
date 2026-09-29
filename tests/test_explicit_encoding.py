"""Every text-mode file read or write in this repo's code must name its encoding.

Windows defaults text I/O to the ANSI codepage (cp1252 on this host), so a bare
`read_text()` over a file holding CJK or `⚠` raises `UnicodeDecodeError`. Measured
2026-09-22 (SoT ST140): `pytest tests/test_web_brief.py` failed 5 of 10 on
`analytics/brief/pundit.py`'s `cfg.ledger_path.read_text()`.

⚠ **`make test` cannot see this class, by design.** `Makefile` exports
`PYTHONUTF8=1` (for stdout, see `test_utf8_output.py`), and UTF-8 mode also flips
the default FILE encoding -- so every bare call passes under `make` and fails under
a bare `pytest`, `python tools/<x>.py` or a hook, none of which carry the export.
The same full run that day reported `4 failed` and never named the file. Hence a
STATIC gate: it reads the source, so no environment variable can mask it.

⚠ **Name-based on purpose.** ruff's `PLW1514` infers the receiver's type and so
misses any `Path` reached through an attribute -- it did not flag the ST140 site
itself. This scan flags every `read_text` / `write_text` / text-mode `open` call
by NAME, and a false positive costs one `encoding=` argument.

`tests/` is IN scope. A tmp-file round trip looks symmetric and is not: cp1252
cannot ENCODE CJK, so `write_text(VTT)` dies at the write. Measured on this branch:
a bare targeted run -- the iteration path AGENTS.md prescribes, which never goes
through `make` -- failed 15 tests across `test_video_fetch.py` and
`test_layering.py` on test-side calls alone.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# `.claude/hooks` is in because hooks run under a bare venv interpreter with no
# PYTHONUTF8 -- the unmasked path this gate exists for.
SCANNED_DIRS = (
    "tests",
    "analytics",
    "card",
    "cli",
    "migrations",
    "monitor",
    "portfolio",
    "scripts",
    "signals",
    "tools",
    "trade",
    "utils",
    "web",
    ".claude/hooks",
)
SCANNED_FILES = ("buibui.py",)

# Methods that take `encoding=` and default it from the locale.
_TEXT_METHODS = frozenset({"read_text", "write_text"})

# `<receiver>.open(...)` receivers that are not file opens in text mode.
_NON_FILE_OPEN_RECEIVERS = frozenset(
    {"webbrowser", "Image", "tarfile", "zipfile", "os"}
)

# The site that failed, kept so the teeth test below stays anchored to reality.
KNOWN_SITE = Path("analytics/brief/pundit.py")


def _mode_literal(call: ast.Call, positional_index: int) -> str | None:
    """The mode string when it is a literal, else None."""
    for kw in call.keywords:
        if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
            return str(kw.value.value)
    if len(call.args) > positional_index:
        arg = call.args[positional_index]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            return arg.value
    return None


def _needs_encoding(call: ast.Call) -> bool:
    """True when `call` is a text-mode file open that leaves encoding to the locale."""
    keywords = {kw.arg for kw in call.keywords}
    if "encoding" in keywords or None in keywords:  # None = **kwargs, cannot tell
        return False

    func = call.func
    if isinstance(func, ast.Attribute) and func.attr in _TEXT_METHODS:
        return True

    if isinstance(func, ast.Name) and func.id == "open":
        mode_index = 1  # open(file, mode, ...)
    elif isinstance(func, ast.Attribute) and func.attr == "open":
        receiver = func.value
        if isinstance(receiver, ast.Name) and receiver.id in _NON_FILE_OPEN_RECEIVERS:
            return False
        mode_index = 0  # Path.open(mode, ...)
    else:
        return False

    mode = _mode_literal(call, mode_index)
    return mode is None or "b" not in mode


def find_bare_calls(source: str, filename: str = "<src>") -> list[int]:
    """Line numbers of every text-mode file call in `source` lacking `encoding=`."""
    tree = ast.parse(source, filename=filename)
    return sorted(
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _needs_encoding(node)
    )


def _scanned_files() -> list[Path]:
    files = [REPO_ROOT / name for name in SCANNED_FILES]
    for directory in SCANNED_DIRS:
        files.extend(sorted((REPO_ROOT / directory).rglob("*.py")))
    return files


def test_shipped_code_names_every_text_encoding() -> None:
    offenders = []
    for path in _scanned_files():
        rel = path.relative_to(REPO_ROOT).as_posix()
        for line in find_bare_calls(path.read_text(encoding="utf-8"), rel):
            offenders.append(f"{rel}:{line}")

    assert not offenders, (
        "text-mode file I/O without `encoding=` -- Windows decodes it as cp1252, and "
        '`make test` hides that because PYTHONUTF8=1. Add `encoding="utf-8"`:\n  '
        + "\n  ".join(offenders)
    )


@pytest.mark.parametrize(
    "snippet",
    [
        "cfg.ledger_path.read_text()",  # the ST140 shape: receiver type is unknowable
        "p.write_text(data)",
        "open(p)",
        "open(p, 'w')",
        "p.open()",
        "p.open('a')",
    ],
)
def test_flags_bare_calls(snippet: str) -> None:
    """Teeth: each of these defaults to the locale encoding."""
    assert find_bare_calls(snippet) == [1]


@pytest.mark.parametrize(
    "snippet",
    [
        "p.read_text(encoding='utf-8')",
        "p.write_text(data, encoding='utf-8')",
        "open(p, encoding='utf-8')",
        "open(p, 'rb')",
        "open(p, mode='wb')",
        "p.open('rb')",
        "p.read_bytes()",
        "webbrowser.open(url)",
        "p.read_text(**kw)",
    ],
)
def test_does_not_flag_explicit_or_binary(snippet: str) -> None:
    """Specificity: a gate that flags everything cannot tell clean from blind."""
    assert find_bare_calls(snippet) == []


def test_known_site_is_in_scope() -> None:
    """The file that failed must stay inside the scanned set, or the gate is blind to it."""
    assert REPO_ROOT / KNOWN_SITE in _scanned_files()
