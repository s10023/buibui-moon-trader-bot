"""Behavioural tests for `deploy/run-job.sh` — the systemd job wrapper.

These drive the REAL script with a stubbed PATH rather than re-implementing its
logic, so what they pin is the observable command SHAPE and ordering. That
distinction is the point: a test that fakes the subprocess *and* the script is
green no matter what the script does.

The defect they guard against was measured on 2026-08-06. A laptop user-timer
with `Persistent=true` fires the run it missed the instant the user manager
resumes from suspend — before NetworkManager has re-associated. The whole
wrapper then executed inside an ~18-second window in which no hostname
resolved, so all three network legs died at once: the healthchecks `/start`
ping, the job itself (rc=1 on a DNS `NameResolutionError`), and the `/fail`
ping. The dead-man's-switch saw *no* ping rather than a failed one.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
RUN_JOB = REPO_ROOT / "deploy" / "run-job.sh"

# Any hostname works as the resolver canary — `getent` is stubbed, and the
# script must not care which host it was handed.
PROBE_HOST = "api.telegram.org"


def _write_exec(path: Path, body: str) -> None:
    """Write `body` to `path` and make it executable."""
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _stub_path(tmp_path: Path, *, dns_ok_after: int, job_rc: int) -> tuple[Path, Path]:
    """Build a stub bin dir shadowing getent/curl/poetry plus a fake job.

    `dns_ok_after` is the 1-indexed getent call that first succeeds; set it
    absurdly high to simulate a network that never comes back. Every stub
    appends to a shared log so the test can assert ORDER across binaries.

    Returns (stub_dir, log_file).
    """
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    log = tmp_path / "calls.log"

    _write_exec(
        stub_dir / "getent",
        "#!/bin/sh\n"
        f'count="{tmp_path}/getent.count"\n'
        'n=$(cat "$count" 2>/dev/null || echo 0)\n'
        "n=$((n + 1))\n"
        'printf %s "$n" > "$count"\n'
        f'printf "getent\\n" >> "{log}"\n'
        f'[ "$n" -ge {dns_ok_after} ] || exit 2\n'
        f'printf "dns-up\\n" >> "{log}"\n'
        "exit 0\n",
    )
    # The wrapper calls curl only for healthchecks pings; record the suffix so
    # the test can tell /start from /fail.
    _write_exec(
        stub_dir / "curl",
        f'#!/bin/sh\nprintf "curl %s\\n" "$*" >> "{log}"\nexit 0\n',
    )
    # Only reached on the failure path (the Telegram notify).
    _write_exec(
        stub_dir / "poetry",
        f'#!/bin/sh\nprintf "telegram\\n" >> "{log}"\nexit 0\n',
    )
    _write_exec(
        stub_dir / "fake-job",
        f'#!/bin/sh\nprintf "job\\n" >> "{log}"\nexit {job_rc}\n',
    )
    return stub_dir, log


def _run(
    tmp_path: Path,
    *,
    dns_ok_after: int = 1,
    job_rc: int = 0,
    net_wait_secs: str = "10",
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    """Invoke the real wrapper against the stubs; return (proc, ordered calls)."""
    stub_dir, log = _stub_path(tmp_path, dns_ok_after=dns_ok_after, job_rc=job_rc)
    env = dict(os.environ)
    env["PATH"] = f"{stub_dir}{os.pathsep}{env['PATH']}"
    env["HC_TEST_URL"] = "https://hc-ping.com/deadbeef"
    env["NET_WAIT_SECS"] = net_wait_secs
    # Busy-poll so the deadline is the only thing bounding the test's runtime.
    env["NET_WAIT_INTERVAL"] = "0"
    env["NET_WAIT_HOSTS"] = PROBE_HOST

    proc = subprocess.run(
        [str(RUN_JOB), "testjob", "HC_TEST_URL", "--", str(stub_dir / "fake-job")],
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )
    calls = log.read_text().splitlines() if log.exists() else []
    return proc, calls


def test_waits_for_dns_before_any_network_leg(tmp_path: Path) -> None:
    """The resolver must answer BEFORE the start ping or the job is attempted.

    This is the regression guard for the resume race: pre-fix there is no
    resolver probe at all, so `dns-up` never appears and every leg fires into a
    dead network.
    """
    proc, calls = _run(tmp_path, dns_ok_after=3)

    assert proc.returncode == 0, proc.stderr
    assert "dns-up" in calls, f"wrapper never probed the resolver: {calls}"

    dns_up = calls.index("dns-up")
    first_curl = next(i for i, c in enumerate(calls) if c.startswith("curl"))
    assert first_curl > dns_up, f"healthchecks ping raced the resolver: {calls}"
    assert calls.index("job") > dns_up, f"job raced the resolver: {calls}"


def test_start_ping_still_precedes_the_job(tmp_path: Path) -> None:
    """DNS already up: the existing /start -> job -> success ordering is intact."""
    proc, calls = _run(tmp_path, dns_ok_after=1)

    assert proc.returncode == 0, proc.stderr
    pings = [c for c in calls if c.startswith("curl")]
    assert any("/start" in c for c in pings), f"no /start ping: {calls}"
    start_ping = next(i for i, c in enumerate(calls) if "/start" in c)
    assert start_ping < calls.index("job"), f"/start must precede the job: {calls}"
    # Success path pings the bare URL and must not report a failure.
    assert not any("/fail" in c for c in pings), f"unexpected /fail: {calls}"
    assert "telegram" not in calls, "success path must not send Telegram"


def test_exhausted_wait_still_runs_the_job_and_preserves_rc(tmp_path: Path) -> None:
    """A real outage must not become a NEW failure mode.

    When the budget expires the wrapper proceeds anyway, so the outcome is
    exactly what it is today — the job runs, fails on its own DNS error, and
    its exit code is preserved. The gate only ever changes TIMING.
    """
    proc, calls = _run(tmp_path, dns_ok_after=10**6, job_rc=1, net_wait_secs="1")

    assert "dns-up" not in calls
    assert "job" in calls, f"wrapper swallowed the job on a dead network: {calls}"
    assert proc.returncode == 1, "wrapped exit code must be preserved"
    assert any("/fail" in c for c in calls if c.startswith("curl")), calls
    assert "waited" in proc.stdout + proc.stderr, "a dead network must be logged"
