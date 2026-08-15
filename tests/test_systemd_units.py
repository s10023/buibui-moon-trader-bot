"""Tests for the committed systemd units in `deploy/systemd/user/`.

`deploy/README.md` has said all along: *"verify a unit parses before trusting it
— `systemctl start` will happily report a typo'd directive as a runtime failure,
while `verify` names the line."* That sentence was correct, specific, and
changed nothing, because nothing ever ran it. This file is that rule as a gate
instead of as prose.

The failure class it exists for is SILENT. A `[Unit]`-only directive placed in
`[Service]` — `OnFailure=` is the one that actually happened, in the wifey fork
on 2026-08-15 — makes systemd log `Unknown key ... ignoring` and then start the
unit anyway. The unit works. The directive does nothing. There, `OnFailure` was
the only failure signal that fork had, so a silently-failing backup was exactly
what the inert alert existed to prevent.

Two deliberate design choices:

1. **Pure parsing — this does NOT shell out to `systemd-analyze`.** That binary
   is not guaranteed on a CI runner, and a test that skips when its tool is
   missing is green without ever having run. Same silent-surface class the file
   is guarding against.
2. **A positive control.** Every check here is parametrised over discovered
   files, and a parametrised check over zero files passes vacuously. If the
   discovery glob ever breaks, `test_unit_discovery_is_not_vacuous` fails rather
   than the suite going quietly green.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
UNIT_DIR = REPO / "deploy" / "systemd" / "user"

# The path the committed units hardcode, exactly as the VPS units hardcode
# /opt/buibui. Mapped onto this checkout so the test survives a clone elsewhere.
HARDCODED_ROOT = "/home/kng/repo/buibui-moon-trader-bot"

# Directives that are valid in EXACTLY ONE section. A key from one of these sets
# appearing under a different section is the silent-inert bug. Not exhaustive by
# design: an unknown key is left alone, because the defect being caught is
# MISPLACEMENT of a real directive, not a typo'd name (systemd logs those too,
# but they cannot silently half-work the way a misplaced one does).
SECTION_ONLY: dict[str, str] = {
    # [Unit] -- OnFailure is the one that actually bit
    "Description": "Unit",
    "Documentation": "Unit",
    "Requires": "Unit",
    "Requisite": "Unit",
    "Wants": "Unit",
    "BindsTo": "Unit",
    "PartOf": "Unit",
    "Conflicts": "Unit",
    "Before": "Unit",
    "After": "Unit",
    "OnFailure": "Unit",
    "OnSuccess": "Unit",
    "StopWhenUnneeded": "Unit",
    "ConditionPathExists": "Unit",
    "AssertPathExists": "Unit",
    # [Service]
    "Type": "Service",
    "ExecStart": "Service",
    "ExecStartPre": "Service",
    "ExecStartPost": "Service",
    "ExecStop": "Service",
    "ExecReload": "Service",
    "WorkingDirectory": "Service",
    "EnvironmentFile": "Service",
    "Environment": "Service",
    "SyslogIdentifier": "Service",
    "TimeoutStartSec": "Service",
    "TimeoutStopSec": "Service",
    "Restart": "Service",
    "RestartSec": "Service",
    "RemainAfterExit": "Service",
    "KillMode": "Service",
    "Nice": "Service",
    "IOSchedulingClass": "Service",
    # [Timer]
    "OnCalendar": "Timer",
    "OnBootSec": "Timer",
    "OnStartupSec": "Timer",
    "OnActiveSec": "Timer",
    "OnUnitActiveSec": "Timer",
    "OnUnitInactiveSec": "Timer",
    "Persistent": "Timer",
    "RandomizedDelaySec": "Timer",
    "AccuracySec": "Timer",
    "WakeSystem": "Timer",
    # [Install]
    "WantedBy": "Install",
    "RequiredBy": "Install",
    "Alias": "Install",
    "Also": "Install",
}

UNITS = sorted(UNIT_DIR.glob("*")) if UNIT_DIR.is_dir() else []
UNIT_NAMES = {p.name for p in UNITS}


def parse(path: Path) -> list[tuple[str, str, str]]:
    """Return (section, key, value) for every directive in a unit file."""
    out: list[tuple[str, str, str]] = []
    section = ""
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            out.append((section, key.strip(), value.strip()))
    return out


def test_unit_discovery_is_not_vacuous() -> None:
    """Positive control: every check below is parametrised over UNITS.

    Without this, a broken glob turns the whole file into a green no-op.
    """
    assert UNIT_DIR.is_dir(), f"{UNIT_DIR} is missing"
    assert any(p.suffix == ".service" for p in UNITS), "no .service units found"
    assert any(p.suffix == ".timer" for p in UNITS), "no .timer units found"
    # The parser must actually yield directives, not just an empty list.
    assert any(parse(p) for p in UNITS), "parsed zero directives from every unit"


@pytest.mark.parametrize("unit", UNITS, ids=lambda p: p.name)
def test_no_directive_is_in_the_wrong_section(unit: Path) -> None:
    """The silent-inert bug: systemd ignores the key and starts the unit anyway."""
    misplaced = [
        f"{key}= is a [{SECTION_ONLY[key]}] directive but appears in [{section}]"
        for section, key, _ in parse(unit)
        if key in SECTION_ONLY and SECTION_ONLY[key] != section
    ]
    assert not misplaced, f"{unit.name}: " + "; ".join(misplaced)


@pytest.mark.parametrize("unit", UNITS, ids=lambda p: p.name)
def test_onfailure_names_a_unit_that_exists(unit: Path) -> None:
    """A dangling alert reference fails precisely when the alert is needed."""
    for section, key, value in parse(unit):
        if key != "OnFailure":
            continue
        for target in value.split():
            assert target in UNIT_NAMES, (
                f"{unit.name} [{section}] OnFailure={target}, "
                f"which is not committed in {UNIT_DIR.relative_to(REPO)}"
            )


@pytest.mark.parametrize("unit", UNITS, ids=lambda p: p.name)
def test_in_repo_paths_resolve(unit: Path) -> None:
    """Every hardcoded repo path a unit references must still exist.

    Checks EVERY token under the hardcoded root, not just the executable — the
    wrapper form is `ExecStart=.../run-job.sh <label> <VAR> -- .../<script>`, so
    the script that does the actual work sits in the arguments. Renaming it
    would otherwise leave a unit that starts and then fails at runtime.
    """
    for _section, key, value in parse(unit):
        if key not in ("ExecStart", "EnvironmentFile", "WorkingDirectory"):
            continue
        for token in value.split():
            # `EnvironmentFile=-/path` means OPTIONAL: systemd starts the unit
            # whether or not the file is there. Asserting it exists contradicts
            # the unit's own declaration, and every one of ours points at `.env`
            # — gitignored, so absent in any clean checkout, CI included. A
            # MANDATORY EnvironmentFile (no dash) still has to resolve.
            if key == "EnvironmentFile" and token.startswith("-"):
                continue
            if not token.startswith(HARDCODED_ROOT):
                continue
            rel = token[len(HARDCODED_ROOT) :].lstrip("/")
            assert (REPO / rel).exists(), f"{unit.name}: {key} points at missing {rel}"
