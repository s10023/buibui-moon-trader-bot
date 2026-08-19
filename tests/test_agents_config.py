"""Tests for the repo-specific agent config.

Two of these are mutation controls rather than behaviour tests. The list this
config replaces lived in four hardcoded copies, so "the checker returns the
right paths" proves nothing on its own — a surviving hardcoded tuple returns
them too. `test_dropping_a_role_shrinks_the_view` is the one that can tell the
difference.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.agents_config import ConfigError, load

MINIMAL = """
[repo]
flip_target = "owner/repo"
gh_user = "owner"

[budgets]
handoff_lines = 200
handoff_ceiling = 600
memory_state_bullets = 6
memory_state_ceiling = 8
memory_bytes_cap = 17408

[[surface]]
id = "agents_md"
path = "AGENTS.md"
purpose = "Agent-neutral project context"
roles = ["anchor", "enumerating"]

[[surface]]
id = "readme"
path = "README.md"
purpose = "User-facing overview"
roles = ["anchor"]
"""


def _write(tmp_path: Path, body: str) -> Path:
    (tmp_path / "docs" / "agents").mkdir(parents=True)
    (tmp_path / "docs" / "agents" / "surfaces.toml").write_text(body, encoding="utf-8")
    return tmp_path


def test_loads_repo_and_budget_scalars(tmp_path: Path) -> None:
    cfg = load(_write(tmp_path, MINIMAL))
    assert cfg.flip_target == "owner/repo"
    assert cfg.gh_user == "owner"
    assert cfg.budgets.handoff_lines == 200
    assert cfg.budgets.handoff_ceiling == 600
    assert cfg.budgets.memory_bytes_cap == 17408


def test_paths_with_role_filters_and_keeps_config_order(tmp_path: Path) -> None:
    cfg = load(_write(tmp_path, MINIMAL))
    assert cfg.paths_with_role("anchor") == ("AGENTS.md", "README.md")
    assert cfg.paths_with_role("enumerating") == ("AGENTS.md",)
    assert cfg.paths_with_role("nonexistent") == ()


def test_dropping_a_role_shrinks_the_view(tmp_path: Path) -> None:
    """The mutation control: a consumer reading a stale hardcoded copy passes
    every other test in this file and fails this one."""
    body = MINIMAL.replace('roles = ["anchor", "enumerating"]', 'roles = ["anchor"]')
    cfg = load(_write(tmp_path, body))
    assert cfg.paths_with_role("enumerating") == ()


def test_missing_config_raises_rather_than_defaulting(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="missing"):
        load(tmp_path)


def test_malformed_toml_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="malformed"):
        load(_write(tmp_path, "[repo\nflip_target = "))


def test_missing_required_key_raises(tmp_path: Path) -> None:
    body = MINIMAL.replace('gh_user = "owner"\n', "")
    with pytest.raises(ConfigError, match="required key"):
        load(_write(tmp_path, body))


def test_a_role_bearing_surface_must_have_a_concrete_path(tmp_path: Path) -> None:
    """Globs are for the skill's human-facing table. A role feeds a checker that
    opens the path, so a glob there would read as a missing file."""
    body = MINIMAL.replace('path = "README.md"', 'path_glob = ".claude/context/*.md"')
    with pytest.raises(ConfigError, match="concrete path"):
        load(_write(tmp_path, body))


def test_the_real_repo_config_loads() -> None:
    """Guards against a committed config that only the fixtures can parse."""
    cfg = load(Path.cwd())
    assert cfg.paths_with_role("anchor")
    assert cfg.budgets.handoff_lines > 0
