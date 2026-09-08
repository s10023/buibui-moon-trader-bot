"""Tests for the `.env*` ignore rule in `.gitignore`.

A bare `.env` pattern matches that exact name and nothing else, so a `.env.bak`
left behind by an edit was untracked but **fully committable**. This repo's
`.env` carries `GROQ_API_KEY` and exchange credentials, AGENTS.md's Git
Conventions say "Never commit `.env`", and the repo is flipped PUBLIC for CI
minutes — at which point the whole history is published, not `HEAD`. So the rule
the prose states was not the rule git was enforcing.

Both directions are pinned deliberately. The widening is one character and the
negation that keeps `.env.example` tracked is a separate line, so a future edit
that drops `!.env.example` would silently stop shipping the one env file the
setup instructions tell a new clone to copy — a failure with no error, in the
opposite direction from the one this rule exists to fix.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def _is_ignored(name: str) -> bool:
    """True when the ignore RULES match `name`.

    `check-ignore` exits 1 when nothing matches, which is a legitimate answer
    rather than a failure, so the return code is read instead of raised on.

    ``--no-index`` is load-bearing and not a tidy-up. By default `check-ignore`
    reports a TRACKED path as un-ignored whatever the patterns say, so without
    it the `.env.example` assertion below tests the index rather than
    `.gitignore` — it passed identically with the `!.env.example` negation
    deleted, i.e. it was a control that could not fail. Measured 2026-09-08 while
    mutation-checking this very file.
    """
    return (
        subprocess.run(  # noqa: S603 - fixed argv, shell=False
            ["git", "check-ignore", "-q", "--no-index", name],
            cwd=REPO,
            capture_output=True,
            check=False,
        ).returncode
        == 0
    )


class TestEnvIgnoreGlob:
    @pytest.mark.parametrize("name", [".env", ".env.bak", ".env.local", ".env.save"])
    def test_every_env_variant_is_ignored(self, name: str) -> None:
        """`.env.bak` is the measured case — it was committable before 2026-09-08."""
        assert _is_ignored(name), f"{name} is committable and may hold secrets"

    def test_the_example_file_stays_committable(self) -> None:
        """Positive control for the negation.

        Without this, `.env*` with no `!.env.example` passes every assertion
        above while quietly un-tracking the one env file that MUST ship.
        """
        assert not _is_ignored(".env.example")

    def test_the_example_file_is_actually_tracked(self) -> None:
        """The negation is only worth anything if the file is really in the index."""
        tracked = subprocess.run(  # noqa: S603 - fixed argv, shell=False
            ["git", "ls-files", ".env.example"],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=False,
        ).stdout
        assert ".env.example" in tracked
