"""Venv integrity probe: name the installed packages whose files are gone (#963).

Measured 2026-10-08 15:46 UTC: `git worktree remove --force` followed a `.venv`
directory junction inside a throwaway worktree and deleted files from the MAIN
checkout's venv before failing. `aiohappyeyeballs` went whole, most of `aiohttp`
with it, and every scheduled task then died at import for about nine hours. The
daily check stayed silent about the cause because nothing in it looked at the
venv; the guard in `guard-destructive.py` now blocks the remove, and this probe
is the post-hoc half, so the next damage, from any cause, is named at 09:10
rather than found by the next session.

Three legs, because each one is blind where the next one looks:

- **RECORD vs disk.** Every installed distribution lists its files in
  `*.dist-info/RECORD`. A partial deletion (most of `aiohttp`) leaves the RECORD
  and removes the files it names. `.pyc` files and `__pycache__` entries are
  skipped: they are rebuilt on import, and pip's own RECORD lists bare
  `__pycache__` directories that a clean venv does not have (39 on the healthy
  laptop venv, all of them false positives).
- **Requirement closure.** A package deleted WHOLE takes its `dist-info` with it,
  so it has no RECORD left to check and the first leg cannot see it. Something
  still requires it, though: `aiohttp` requires `aiohappyeyeballs`.
- **Direct dependencies.** A package that nothing else requires, deleted whole,
  is invisible to both legs above. `pyproject.toml`'s `[project].dependencies`
  names those.

Its one repo import, the stdlib-only `utils.stdio`, happens inside the `__main__`
block after a scoped path insert, so a bare `python tools/venv_integrity.py` still works
on a venv too broken to import anything else. It lives here rather than inside the gitignored
`docs/plans/daily_check.py` so the logic reaches CI and a reclone.
"""

from __future__ import annotations

import csv
import os
import re
import sys
import sysconfig
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass
from importlib.metadata import Distribution, distributions
from pathlib import Path, PurePath

REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Damage:
    """One damaged or missing distribution.

    `kind` is "files" (RECORD names files absent from disk), "requirement"
    (an installed package requires one that is not installed) or "direct" (a
    `pyproject.toml` dependency is not installed).
    """

    name: str
    version: str
    kind: str
    detail: str


def normalize(name: str) -> str:
    """PEP 503 name normalisation, so `Typing_Extensions` matches `typing-extensions`."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _skippable(entry: str) -> bool:
    parts = PurePath(entry.replace("\\", "/")).parts
    return entry.endswith(".pyc") or "__pycache__" in parts


def missing_record_files(site_dir: Path) -> list[Damage]:
    """Distributions under `site_dir` whose RECORD names files that are not on disk."""
    found: list[Damage] = []
    for record in sorted(site_dir.glob("*.dist-info/RECORD")):
        missing: list[str] = []
        with record.open(newline="", encoding="utf-8") as fh:
            for row in csv.reader(fh):
                if not row or _skippable(row[0]):
                    continue
                # RECORD paths are relative to the site dir, and a script entry
                # climbs out of it (`../../Scripts/x.exe`), which joining handles.
                # lexists, not exists: a recorded symlink counts as present.
                if not os.path.lexists(site_dir / row[0]):
                    missing.append(row[0])
        if missing:
            dist = Distribution.at(record.parent)
            sample = ", ".join(missing[:3])
            found.append(
                Damage(
                    dist.metadata["Name"] or record.parent.name,
                    dist.version,
                    "files",
                    f"{len(missing)} recorded file(s) missing: {sample}",
                )
            )
    return found


def _installed(site_dirs: Iterable[Path]) -> dict[str, Distribution]:
    out: dict[str, Distribution] = {}
    for dist in distributions(path=[str(d) for d in site_dirs]):
        name = dist.metadata["Name"]
        if name:
            out.setdefault(normalize(name), dist)
    return out


def unmet_requirements(site_dirs: Iterable[Path]) -> list[Damage]:
    """Installed distributions that require one which is not installed.

    Extras-only requirements are skipped: their marker reads `extra == "x"`,
    which evaluates False against an empty extra.
    """
    from packaging.requirements import Requirement  # noqa: PLC0415

    installed = _installed(site_dirs)
    found: list[Damage] = []
    for key in sorted(installed):
        dist = installed[key]
        for raw in dist.requires or []:
            req = Requirement(raw)
            if req.marker is not None and not req.marker.evaluate({"extra": ""}):
                continue
            if normalize(req.name) not in installed:
                found.append(
                    Damage(
                        req.name,
                        "",
                        "requirement",
                        f"required by {dist.metadata['Name']} but not installed",
                    )
                )
    return found


def missing_direct(site_dirs: Iterable[Path], pyproject: Path) -> list[Damage]:
    """`[project].dependencies` entries that are not installed."""
    from packaging.requirements import Requirement  # noqa: PLC0415

    with pyproject.open("rb") as fh:
        deps = tomllib.load(fh).get("project", {}).get("dependencies", [])
    installed = _installed(site_dirs)
    found: list[Damage] = []
    for raw in deps:
        req = Requirement(raw)
        if req.marker is not None and not req.marker.evaluate({"extra": ""}):
            continue
        if normalize(req.name) not in installed:
            found.append(
                Damage(req.name, "", "direct", "pyproject dependency not installed")
            )
    return found


def site_dirs_of_running_interpreter() -> list[Path]:
    """The purelib and platlib of the interpreter running this probe, deduplicated."""
    paths = sysconfig.get_paths()
    dirs: list[Path] = []
    for key in ("purelib", "platlib"):
        p = Path(paths[key])
        if p not in dirs:
            dirs.append(p)
    return dirs


def check(
    site_dirs: list[Path] | None = None, pyproject: Path | None = None
) -> list[Damage]:
    """All three legs. An empty list is a clean venv.

    A requirement already reported as missing by the direct leg is not repeated,
    so one deleted package reads as one finding.
    """
    dirs = site_dirs if site_dirs is not None else site_dirs_of_running_interpreter()
    proj = pyproject if pyproject is not None else REPO_ROOT / "pyproject.toml"
    found: list[Damage] = []
    for d in dirs:
        found.extend(missing_record_files(d))
    direct = missing_direct(dirs, proj)
    seen = {normalize(x.name) for x in direct}
    for dmg in unmet_requirements(dirs):
        if normalize(dmg.name) not in seen:
            seen.add(normalize(dmg.name))
            found.append(dmg)
    found.extend(direct)
    return found


def repair_hint(damage: list[Damage]) -> str:
    """The command that restores the damage, or "" for a clean venv.

    Partial damage needs a pinned force-reinstall: `poetry install` sees the
    dist-info and skips the package, which is why the 10-08 repair was a pinned
    `pip install --force-reinstall --no-deps`. A package gone whole has no
    dist-info, so `poetry install` does reinstall it.
    """
    pins = [f"{d.name}=={d.version}" for d in damage if d.kind == "files"]
    whole = any(d.kind != "files" for d in damage)
    parts: list[str] = []
    if pins:
        parts.append(
            "poetry run python -m pip install --force-reinstall --no-deps "
            + " ".join(pins)
        )
    if whole:
        parts.append("poetry install --no-root")
    return "  &&  ".join(parts)


def main() -> int:
    damage = check()
    if not damage:
        print("venv integrity: clean")
        return 0
    for d in damage:
        ver = f" {d.version}" if d.version else ""
        print(f"{d.kind:<11} {d.name}{ver}: {d.detail}")
    print(f"repair: {repair_hint(damage)}")
    return 1


if __name__ == "__main__":
    # Repo root, for utils.stdio: a bare `python tools/<name>.py` puts only
    # tools/ on the path. Scoped to the entry so an import mutates nothing.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from utils.stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main())
