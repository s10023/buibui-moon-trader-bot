"""Every process entry point must re-encode stdout as UTF-8 before it prints.

`tests/test_utf8_output.py` pins `PYTHONUTF8=1` on the two surfaces an environment
variable can reach — `make` and the scheduled jobs. This pins the third, a bare
`buibui …` or `python tools/<name>.py`, which neither reaches. Measured 2026-10-09: a
`buibui backtest --save` with stdout redirected to a file saved all 57 runs, then died
on `format_sweep_table`'s `═` rule under cp1252 and exited 1.
"""

from __future__ import annotations

import ast
import importlib
import io
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from analytics.backtest.formatters import format_sweep_table
from utils.stdio import utf8_stdio

REPO_ROOT = Path(__file__).resolve().parents[1]

SCANNED_DIRS = (
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
)

# Both delegate to `cli.main.main`, which calls `utf8_stdio()` itself, because the
# CLI's entry is the function, not the block. `test_cli_entry_survives_cp1252_stdout`
# is what proves that function does it.
DELEGATES_TO_CLI_MAIN = frozenset({"buibui.py", "cli/main.py"})


def _cp1252_stream() -> tuple[io.TextIOWrapper, io.BytesIO]:
    """What Windows hands a redirected stdout on this host, plus its byte sink."""
    sink = io.BytesIO()
    return io.TextIOWrapper(sink, encoding="cp1252"), sink


def _main_block(tree: ast.Module) -> ast.If | None:
    for node in tree.body:
        if (
            isinstance(node, ast.If)
            and isinstance(node.test, ast.Compare)
            and isinstance(node.test.left, ast.Name)
            and node.test.left.id == "__name__"
        ):
            return node
    return None


def _entry_calls_utf8_stdio(source: str) -> bool | None:
    """Whether the module's `__main__` block calls `utf8_stdio()`; None if it has none."""
    block = _main_block(ast.parse(source))
    if block is None:
        return None
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "utf8_stdio"
        for stmt in block.body
        for node in ast.walk(stmt)
    )


def _entry_points() -> list[Path]:
    paths = [p for d in SCANNED_DIRS for p in (REPO_ROOT / d).rglob("*.py")]
    paths.append(REPO_ROOT / "buibui.py")
    return sorted(
        p
        for p in paths
        if "__pycache__" not in p.parts
        and _main_block(ast.parse(p.read_text(encoding="utf-8"))) is not None
    )


def test_the_sweep_table_kills_a_cp1252_stream() -> None:
    """Teeth: the crash is real, so the fix below is not vacuous."""
    stream, _ = _cp1252_stream()

    with pytest.raises(UnicodeEncodeError):
        print(format_sweep_table([]), file=stream)


def test_utf8_stdio_lets_the_sweep_table_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stream, sink = _cp1252_stream()
    monkeypatch.setattr(sys, "stdout", stream)
    monkeypatch.setattr(sys, "stderr", _cp1252_stream()[0])

    utf8_stdio()
    print(format_sweep_table([]))
    stream.flush()

    assert "═" * 62 in sink.getvalue().decode("utf-8")


def test_utf8_stdio_leaves_a_non_wrapper_stream_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`pythonw` sets the streams to None; the helper must not raise on that."""
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", io.StringIO())

    utf8_stdio()


def test_cli_entry_survives_cp1252_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real entry, end to end: a subcommand that prints the sweep table."""
    from cli.main import main
    from monitor import price_monitor

    stream, sink = _cp1252_stream()
    monkeypatch.setattr(sys, "stdout", stream)
    monkeypatch.setattr(sys, "stderr", _cp1252_stream()[0])

    def prints_the_table(**_: Any) -> None:
        print(format_sweep_table([]))

    with (
        patch.object(price_monitor, "main", side_effect=prints_the_table),
        patch("sys.argv", ["buibui.py", "monitor", "price"]),
    ):
        main()
    stream.flush()

    assert "═" in sink.getvalue().decode("utf-8")


def test_importing_the_cli_does_not_wrap_stdout() -> None:
    """`colorama.init()` ran at import of `monitor.price_monitor`, which `cli.main`
    imports for every subcommand, so a backtest's stdout was wrapped too."""
    before = sys.stdout

    importlib.reload(importlib.import_module("monitor.price_monitor"))

    assert sys.stdout is before


def test_every_entry_point_calls_utf8_stdio() -> None:
    entries = _entry_points()
    assert len(entries) > 50, "the scan found too few entry points to mean anything"

    missing = [
        p.relative_to(REPO_ROOT).as_posix()
        for p in entries
        if p.relative_to(REPO_ROOT).as_posix() not in DELEGATES_TO_CLI_MAIN
        and not _entry_calls_utf8_stdio(p.read_text(encoding="utf-8"))
    ]

    assert not missing, (
        "these entry points print without forcing UTF-8, so a bare run on Windows "
        "dies under cp1252 after doing its work. Add to the `__main__` block:\n"
        "    from utils.stdio import utf8_stdio\n\n    utf8_stdio()\n  "
        + "\n  ".join(missing)
    )


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ('if __name__ == "__main__":\n    main()\n', False),
        (
            'if __name__ == "__main__":\n'
            "    from utils.stdio import utf8_stdio\n\n"
            "    utf8_stdio()\n    main()\n",
            True,
        ),
        ("def main() -> None:\n    utf8_stdio()\n", None),
    ],
    ids=["bare-block-is-flagged", "called-block-passes", "no-block-is-not-an-entry"],
)
def test_the_entry_check_has_teeth(source: str, expected: bool | None) -> None:
    assert _entry_calls_utf8_stdio(source) is expected
