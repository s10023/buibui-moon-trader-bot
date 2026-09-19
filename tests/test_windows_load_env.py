"""Teeth for `deploy/windows/load-env.sh` — the Windows stand-in for `EnvironmentFile=`.

Runs the real script through `bash`, which both hosts have: Git Bash on the Windows box
and the system shell on the ubuntu runner. So these cases gate on CI even though the
code they cover only ever executes on Windows — the same reason `tools/task_probe.py`
injects its reader.

The load-bearing case is `test_a_crlf_file_does_not_smuggle_a_carriage_return`. Git
Bash tolerates CRLF in a script — it runs, its comparisons match, `bash -n` is clean —
but does NOT strip CR from data the script READS. So a `.env` saved by any Windows
editor that writes CRLF puts a trailing CR inside every value, nothing fails loudly, and
the first thing anyone notices is that Telegram stopped accepting the token.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_LOADER = Path(__file__).resolve().parents[1] / "deploy/windows/load-env.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None, reason="no bash on PATH to run the loader"
)


def _load(tmp_path: Path, contents: str, var: str, *, newline: str = "\n") -> str:
    """Write `contents` as an env file, load it, and echo one variable's exact value.

    `printf '%s'` rather than `echo`, and the length is asserted by the caller where it
    matters, because a trailing CR is invisible in ordinary output — it just returns the
    cursor. A test that eyeballs the value cannot see the defect this file is about.
    """
    env_file = tmp_path / "env.file"
    env_file.write_bytes(contents.replace("\n", newline).encode("utf-8"))
    script = f'. "{_LOADER.as_posix()}"; load_env "{env_file.as_posix()}"; printf "%s" "${var}"'
    r = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        check=True,
    )
    return r.stdout


def test_reads_a_plain_assignment(tmp_path: Path) -> None:
    assert _load(tmp_path, "TELEGRAM_CHAT_ID=12345\n", "TELEGRAM_CHAT_ID") == "12345"


def test_a_crlf_file_does_not_smuggle_a_carriage_return(tmp_path: Path) -> None:
    """The silent one. Asserted on LENGTH, because the CR is invisible in the value."""
    got = _load(
        tmp_path, "TELEGRAM_BOT_TOKEN=123:abc\n", "TELEGRAM_BOT_TOKEN", newline="\r\n"
    )

    assert got == "123:abc"
    assert len(got) == 7, f"a carriage return survived the parse: {got!r}"
    assert "\r" not in got


def test_a_quoted_value_keeps_its_spaces(tmp_path: Path) -> None:
    """Why this parses instead of sourcing.

    `. ./.env` would assign `EXEC_EXTRA_ARGS=--vol-target` and then try to RUN `0.10`
    as a command. systemd's EnvironmentFile never behaved that way, so a `.env` carried
    over from the Linux box has to keep working unchanged.
    """
    assert (
        _load(tmp_path, 'EXEC_EXTRA_ARGS="--vol-target 0.10"\n', "EXEC_EXTRA_ARGS")
        == "--vol-target 0.10"
    )
    assert (
        _load(tmp_path, "EXEC_EXTRA_ARGS='--vol-target 0.10'\n", "EXEC_EXTRA_ARGS")
        == "--vol-target 0.10"
    )


def test_a_value_containing_equals_survives(tmp_path: Path) -> None:
    """Split on the FIRST `=` only — a base64 secret or a URL query would otherwise be
    truncated at its own separator, which is a corrupted credential rather than a
    missing one."""
    assert (
        _load(
            tmp_path,
            "HEALTHCHECKS_URL_SIGNAL=https://hc.io/p?a=b&c=d\n",
            "HEALTHCHECKS_URL_SIGNAL",
        )
        == "https://hc.io/p?a=b&c=d"
    )


@pytest.mark.parametrize(
    "line",
    ["# TELEGRAM_CHAT_ID=999", "   ", "not-an-assignment", "BAD-KEY=1", "=orphan"],
    ids=["comment", "blank", "no-equals", "hyphen-in-key", "empty-key"],
)
def test_a_malformed_line_is_skipped_not_fatal(tmp_path: Path, line: str) -> None:
    """A stray line must not take the whole environment down with it.

    `.env.example` ships 20-odd comment lines, and the operator hand-edits this file
    under time pressure. systemd skips what it cannot parse; so does this.
    """
    got = _load(tmp_path, f"{line}\nTELEGRAM_CHAT_ID=12345\n", "TELEGRAM_CHAT_ID")

    assert got == "12345"


def test_a_missing_file_is_not_an_error(tmp_path: Path) -> None:
    """Every unit says `EnvironmentFile=-`, and the `-` prefix means exactly this."""
    script = (
        f'. "{_LOADER.as_posix()}"; load_env "{tmp_path.as_posix()}/nope"; echo rc=$?'
    )
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True)

    assert r.returncode == 0
    assert "rc=0" in r.stdout


def test_a_final_line_without_a_newline_is_still_read(tmp_path: Path) -> None:
    """An editor that does not terminate the last line would otherwise drop it, and the
    dropped one is whichever secret happens to sit at the bottom of the file."""
    assert _load(tmp_path, "TELEGRAM_CHAT_ID=12345", "TELEGRAM_CHAT_ID") == "12345"
