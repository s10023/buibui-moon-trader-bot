"""The web dashboard binds loopback unless the operator opts out (#986).

It serves live positions and account data, so `make buibui-web` binding
0.0.0.0 by default handed both to everyone on a shared network. The CLI default
is already 127.0.0.1; these pin the Make target and the README to it.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _makefile() -> str:
    return (ROOT / "Makefile").read_text(encoding="utf-8")


def _recipe(target: str) -> str:
    """Return a Makefile target's recipe lines (tab-indented, after the rule)."""
    lines = _makefile().splitlines()
    start = lines.index(f"{target}:")
    body: list[str] = []
    for line in lines[start + 1 :]:
        if not line.startswith("\t"):
            break
        body.append(line)
    return "\n".join(body)


def test_web_host_defaults_to_loopback() -> None:
    match = re.search(r"^WEB_HOST \?= (\S+)$", _makefile(), re.MULTILINE)
    assert match is not None, "Makefile must declare an overridable WEB_HOST default"
    assert match.group(1) == "127.0.0.1"


def test_buibui_web_binds_web_host() -> None:
    recipe = _recipe("buibui-web")
    assert "--host $(WEB_HOST)" in recipe
    assert "0.0.0.0" not in recipe


def test_readme_never_shows_a_wildcard_bind_as_the_default() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "--host 0.0.0.0" not in readme
    # The opt-in is documented as an explicit override, nothing else.
    for line in readme.splitlines():
        if "0.0.0.0" in line:
            assert "WEB_HOST=0.0.0.0" in line, line
