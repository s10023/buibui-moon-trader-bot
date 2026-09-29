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

import subprocess
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
    for raw in path.read_text(encoding="utf-8").splitlines():
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


def _is_gitignored(rel: str) -> bool:
    """True when git ignores ``rel``.

    This check exists to catch a RENAMED TRACKED FILE — a unit that starts and
    then fails at runtime because the script moved. Gitignored paths are a
    different thing entirely: `.env`, `.venv/bin/python` and the operator
    tooling under `docs/plans/` are supplied by the environment and are absent
    from every clean checkout, CI included. Asserting those exist tests the
    machine, not the units.

    Asked of git rather than kept as a hardcoded exemption list, because a list
    drifts silently the moment a new gitignored path lands in a unit — which is
    exactly how this reached CI: three such paths, and the suite only ever
    surfaced the first, one failure at a time.

    A missing or broken `git` raises rather than skipping. The repo is a git
    checkout by construction, and a test that goes quiet when its tool is absent
    is green without having run — the same silent-surface class this file exists
    to close.
    """
    proc = subprocess.run(
        ["git", "check-ignore", "-q", "--", rel], cwd=REPO, capture_output=True
    )
    if proc.returncode not in (0, 1):
        raise RuntimeError(
            f"git check-ignore failed on {rel!r} (rc={proc.returncode}): "
            f"{proc.stderr.decode(errors='replace').strip()}"
        )
    return proc.returncode == 0


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
            # whether or not the file is there, so asserting it exists
            # contradicts the unit's own declaration. A MANDATORY
            # EnvironmentFile (no dash) still has to resolve.
            if key == "EnvironmentFile" and token.startswith("-"):
                continue
            if not token.startswith(HARDCODED_ROOT):
                continue
            rel = token[len(HARDCODED_ROOT) :].lstrip("/")
            if not rel:
                continue  # the repo root itself, e.g. `WorkingDirectory=`
            if _is_gitignored(rel):
                # A MANDATORY EnvironmentFile pointing at a gitignored path is a
                # real defect rather than an environment fact: systemd refuses to
                # start the unit when it is missing, so a fresh machine gets a
                # dead timer. `ExecStart` is different — `.venv/bin/python` is
                # built by `poetry install`, and depending on it is the design.
                assert key != "EnvironmentFile", (
                    f"{unit.name}: mandatory EnvironmentFile points at gitignored "
                    f"{rel} — prefix it with '-' to make it optional, or track it"
                )
                continue
            assert (REPO / rel).exists(), f"{unit.name}: {key} points at missing {rel}"


# --- ST77: the daily-check soft-fail contract ---------------------------------


def _env(unit: Path) -> dict[str, str]:
    """The unit's `Environment=` assignments, as a dict."""
    out: dict[str, str] = {}
    for _section, key, value in parse(unit):
        if key != "Environment":
            continue
        name, _, val = value.partition("=")
        out[name.strip()] = val.strip()
    return out


def test_daily_check_declares_its_soft_fail_code() -> None:
    """`--exit-on-tier2` is only half the contract; the wrapper needs the other.

    daily_check.py exits 2 for "tier 1 clear, tier 2 red". Without
    SOFT_FAIL_RC=2 here, deploy/run-job.sh reads that as a failed job:
    heartbeat suppressed, healthchecks /fail pinged, push titled FAILED. A
    routine tier-2 red then looks exactly like a dead timer on the phone, which
    is the confusion this unit's own TELEGRAM_ALWAYS comment exists to remove.

    The two directives are pinned TOGETHER because either alone is incoherent:
    the flag without the code pages daily, and the code without the flag
    declares a soft exit the job can never produce.
    """
    unit = UNIT_DIR / "buibui-daily-check.service"
    assert unit.exists(), "the daily-check unit vanished — this test is vacuous"

    exec_starts = [v for _s, k, v in parse(unit) if k == "ExecStart"]
    assert any("--exit-on-tier2" in v for v in exec_starts), (
        "daily-check no longer opts into tier-2 exits; drop SOFT_FAIL_RC too"
    )
    assert _env(unit).get("SOFT_FAIL_RC") == "2", (
        "buibui-daily-check.service must declare SOFT_FAIL_RC=2 so run-job.sh "
        "reads a tier-2 red as success-with-warnings rather than FAILED"
    )


def test_no_other_unit_declares_a_soft_fail_code() -> None:
    """Opt-in, and provably so.

    argparse exits 2 on a USAGE error, so a soft code on signal-watch, xsmom or
    backup would turn a broken invocation into a heartbeat. This is the
    specificity control for the test above: without it, a blanket rollout of
    SOFT_FAIL_RC would pass unnoticed.
    """
    offenders = {
        unit.name: _env(unit)["SOFT_FAIL_RC"]
        for unit in UNITS
        if unit.name != "buibui-daily-check.service" and "SOFT_FAIL_RC" in _env(unit)
    }
    assert not offenders, (
        f"SOFT_FAIL_RC is opt-in per job and only daily-check exits 2 by "
        f"design; remove it from {offenders}"
    )
