"""Repo-specific facts that generic tooling reads.

``tomllib`` is stdlib, and that is load-bearing rather than incidental: the
checkers run in CI's dependency-free ``markdownlint`` job, so a loader that
raised ``ImportError`` there would turn every leg into a SKIP — and a SKIP
prints ``0 findings, exit 0``, indistinguishable from a correct run. This repo
has shipped that exact confusion twice.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CONFIG = Path("docs/agents/surfaces.toml")


class ConfigError(RuntimeError):
    """The config is absent, malformed or incomplete.

    Deliberately fatal. Every caller renders this as a FINDING; none may
    degrade it to a SKIP or a soft default, because the whole value of a
    single source is that its absence is loud.
    """


@dataclass(frozen=True)
class Budgets:
    handoff_lines: int
    handoff_ceiling: int
    memory_state_bullets: int
    memory_state_ceiling: int
    memory_bytes_cap: int


@dataclass(frozen=True)
class Surface:
    id: str
    purpose: str
    path: str = ""
    path_glob: str = ""
    roles: tuple[str, ...] = ()


@dataclass(frozen=True)
class AgentsConfig:
    flip_target: str
    gh_user: str
    budgets: Budgets
    surfaces: tuple[Surface, ...]

    def paths_with_role(self, role: str) -> tuple[str, ...]:
        """Every surface path carrying ``role``, in declaration order.

        Order is kept so the config reads as the list a human would write; no
        consumer depends on it.
        """
        return tuple(s.path for s in self.surfaces if role in s.roles)


def _surface(raw: dict[str, Any]) -> Surface:
    try:
        surface = Surface(
            id=str(raw["id"]),
            purpose=str(raw["purpose"]),
            path=str(raw.get("path", "")),
            path_glob=str(raw.get("path_glob", "")),
            roles=tuple(str(r) for r in raw.get("roles", ())),
        )
    except KeyError as exc:
        raise ConfigError(
            f"{CONFIG}: surface is missing a required key: {exc}"
        ) from exc
    if surface.roles and not surface.path:
        raise ConfigError(
            f"{CONFIG}: surface {surface.id!r} carries roles but no concrete path — "
            "a role feeds a checker that opens the file, so a glob reads as missing"
        )
    return surface


def load(root: Path | None = None) -> AgentsConfig:
    """Read the repo config. Raises :class:`ConfigError` rather than defaulting."""
    path = (root or Path.cwd()) / CONFIG
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(
            f"{CONFIG} is missing — the checkers have no surface list"
        ) from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{CONFIG} is malformed: {exc}") from exc

    try:
        repo = raw["repo"]
        budgets = Budgets(**raw["budgets"])
        surfaces = tuple(_surface(s) for s in raw["surface"])
        return AgentsConfig(
            flip_target=str(repo["flip_target"]),
            gh_user=str(repo["gh_user"]),
            budgets=budgets,
            surfaces=surfaces,
        )
    except (KeyError, TypeError) as exc:
        raise ConfigError(f"{CONFIG} is missing a required key: {exc}") from exc
