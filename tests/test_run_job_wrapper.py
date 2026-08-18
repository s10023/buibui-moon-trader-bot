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

# The phone-width column `tg_send` folds the Telegram body to, and the byte cap
# it applies afterwards. Both are asserted rather than imported because the
# script is shell -- the test's job is to pin the numbers the operator reads.
TG_FOLD_WIDTH = 46
TG_BODY_CAP = 3400


def _write_exec(path: Path, body: str) -> None:
    """Write `body` to `path` and make it executable."""
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _stub_path(
    tmp_path: Path, *, dns_ok_after: int, job_rc: int, job_stdout: str = ""
) -> tuple[Path, Path]:
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
    # Only reached on the failure path (the Telegram notify). It records the
    # BODY it was handed, which is what the wrapper actually sends -- asserting
    # on that rather than on the script's source is what makes the fold test
    # behavioural.
    _write_exec(
        stub_dir / "poetry",
        f'#!/bin/sh\nprintf "telegram\\n" >> "{log}"\n'
        f'printf "%s" "$BODY" > "{_body_file(tmp_path)}"\nexit 0\n',
    )
    job_out = tmp_path / "job-stdout.txt"
    job_out.write_text(job_stdout)
    _write_exec(
        stub_dir / "fake-job",
        f'#!/bin/sh\nprintf "job\\n" >> "{log}"\ncat "{job_out}"\nexit {job_rc}\n',
    )
    return stub_dir, log


def _body_file(tmp_path: Path) -> Path:
    """Where the poetry stub parks the Telegram body it was handed."""
    return tmp_path / "telegram-body.txt"


def _run(
    tmp_path: Path,
    *,
    dns_ok_after: int = 1,
    job_rc: int = 0,
    net_wait_secs: str = "10",
    job_stdout: str = "",
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    """Invoke the real wrapper against the stubs; return (proc, ordered calls)."""
    stub_dir, log = _stub_path(
        tmp_path, dns_ok_after=dns_ok_after, job_rc=job_rc, job_stdout=job_stdout
    )
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


def test_telegram_body_is_folded_to_phone_width(tmp_path: Path) -> None:
    """A long log line must not force horizontal scroll inside `<pre>`.

    Telegram renders the body in `<pre>`, which NEVER soft-wraps, so a single
    over-wide line drags the whole report sideways on a phone -- measured
    2026-08-18 on the daily check, where one 164-char line did exactly that.
    """
    long_line = (
        "  + signal-watch               timer ok, last success 2m ago "
        "(08-18 22:47 MYT) -- 93 runs/24h (expect ~96)"
    )
    assert len(long_line) > TG_FOLD_WIDTH, "fixture must actually be over-wide"

    _, calls = _run(tmp_path, job_rc=1, job_stdout=long_line + "\n")

    assert "telegram" in calls, f"failure path did not notify: {calls}"
    body = _body_file(tmp_path).read_text()
    # Positive control: the log tail really did reach the body, so the
    # line-width assertion below cannot pass by being handed nothing.
    assert "signal-watch" in body, f"log tail never reached the body: {body!r}"
    too_wide = [ln for ln in body.splitlines() if len(ln) > TG_FOLD_WIDTH]
    assert not too_wide, f"unfolded lines reached Telegram: {too_wide}"


def test_fold_precedes_the_byte_cap(tmp_path: Path) -> None:
    """Folding must happen BEFORE the byte cap, not after.

    Folding inserts a newline roughly every `TG_FOLD_WIDTH` characters. Cap
    first and those newlines are pure addition, pushing the message back over
    the budget the cap exists to hold -- and Telegram rejects the whole send
    past 4096, so an over-long alert fails exactly like no alert.
    """
    # The failure path sends `tail -n 25`, so the fixture has to clear the byte
    # cap AFTER that line cut or the cap never bites and this test is vacuous
    # (it was, on the first draft: 25x120 chars is under the cap in both
    # orders). 40 lines of 300 chars leaves ~7.5KB against a 3400-byte cap.
    noisy = ("x" * 300 + "\n") * 40

    _, calls = _run(tmp_path, job_rc=1, job_stdout=noisy)

    assert "telegram" in calls, f"failure path did not notify: {calls}"
    body = _body_file(tmp_path).read_text()
    assert body, "positive control: the body must not be empty"
    # Positive control for the cap itself: without this, a body that never
    # reached the budget would satisfy the length assertion by being small.
    assert len(body) > TG_BODY_CAP - 200, (
        f"body is only {len(body)} chars -- the fixture never reached the cap, "
        "so the ordering assertion below proves nothing"
    )
    assert max(len(ln) for ln in body.splitlines()) <= TG_FOLD_WIDTH
    assert len(body) <= TG_BODY_CAP, (
        f"body is {len(body)} chars: the fold ran AFTER the cap, "
        "so its newlines were added on top of the budget"
    )
