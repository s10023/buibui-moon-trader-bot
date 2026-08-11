"""The scheduled write jobs must open analytics.db through connect_with_retry.

Background: `buibui-xsmom-daily` died on a DuckDB `Conflicting lock` on 2026-08-11.
`Persistent=true` timers catch up in the SAME second after a resume from suspend, so
several jobs contend at once, and the holder is variable — signal_runner alone opens
~10 connections per run. That is why the fix is retry-on-any-holder rather than
anything aimed at one process, and why this is a structural guard: the failure mode
is someone adding an 11th direct `duckdb.connect` months from now, which no
behavioural test of today's call sites would catch.

Scope is the UNATTENDED jobs only. Hand-run audit tools under tools/ are excluded on
purpose — an operator watching a traceback can just rerun, and there are ~50 of them.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Modules the systemd timers reach. analytics_runner was wired in #593; the other
# two are the follow-up that #593's own handoff flagged as still outstanding.
SCHEDULED_JOB_MODULES = (
    "analytics/analytics_runner.py",
    "analytics/recalibrate_runner.py",
    "analytics/signal_runner.py",
)

_DIRECT_CONNECT = re.compile(r"\bduckdb\.connect\s*\(")


def _direct_connects(source: str) -> list[str]:
    return [ln.strip() for ln in source.splitlines() if _DIRECT_CONNECT.search(ln)]


@pytest.mark.parametrize("rel", SCHEDULED_JOB_MODULES)
def test_scheduled_job_has_no_direct_duckdb_connect(rel: str) -> None:
    path = ROOT / rel
    assert path.exists(), f"{rel} moved — update SCHEDULED_JOB_MODULES"
    offenders = _direct_connects(path.read_text())
    assert not offenders, (
        f"{rel} opens analytics.db directly; use "
        f"analytics.db_retry.connect_with_retry so a busy lock waits instead of "
        f"aborting the job. Offending lines: {offenders}"
    )


@pytest.mark.parametrize("rel", SCHEDULED_JOB_MODULES)
def test_scheduled_job_imports_the_retrying_connect(rel: str) -> None:
    source = (ROOT / rel).read_text()
    assert "connect_with_retry" in source, (
        f"{rel} neither connects directly nor imports connect_with_retry — "
        f"it probably stopped touching the DB, so re-scope this guard."
    )


def test_the_detector_actually_fires() -> None:
    """Non-vacuity proof.

    Every assertion above passes trivially against a file with no DB access at all,
    so the detector is exercised here against a deliberate violation. Without this,
    a typo in the regex would make the whole module green forever — the vacuous-guard
    failure this repo has hit repeatedly.
    """
    violating = "conn = duckdb.connect(str(db_path))\nwith duckdb.connect(p) as c:\n"
    assert len(_direct_connects(violating)) == 2

    clean = "conn = connect_with_retry(db_path)\n# duckdb_connect is not a call\n"
    assert _direct_connects(clean) == []
