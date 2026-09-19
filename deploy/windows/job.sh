#!/usr/bin/env bash
# Windows Task Scheduler entry point. Loads `.env`, then hands off to `run-job.sh`
# UNCHANGED.
#
# WHY THIS IS A SHIM AND NOT A REWRITE
# ------------------------------------
# `run-job.sh` is 192 lines of healthchecks pinging, resume-from-suspend network
# gating, Telegram-on-failure, HTML escaping, 46-column folding and soft-exit handling,
# and every one of those behaviours is portable: it is `curl`, `date` and POSIX shell,
# all of which Git Bash provides. Reimplementing it in PowerShell would fork the one
# wrapper both hosts run and guarantee they drift. So Windows gets an ADAPTER for the
# single thing systemd does that Task Scheduler cannot -- `EnvironmentFile=` -- and
# nothing else.
#
# Usage (from a Task Scheduler action, via Git Bash):
#   bash.exe -lc "deploy/windows/job.sh <label> <HC_ENV_VAR_NAME> -- <command...>"
#
# Per-job `Environment=` lines become an ordinary env prefix on that command line:
#   bash.exe -lc "TELEGRAM_ALWAYS=1 deploy/windows/job.sh daily-check ... -- ..."

set -uo pipefail

# This file is <repo>/deploy/windows/job.sh, so the root is two levels up -- one more
# than `run-job.sh`'s own `cd`, which is the kind of off-by-one that silently runs the
# whole job in the wrong directory and reports success.
cd "$(dirname "$0")/../.." || exit 1

# --- EnvironmentFile=, hand-rolled -------------------------------------------------
#
# Extracted to its own file so it can be tested directly -- see `load-env.sh` for why
# it parses rather than sources, and for the CRLF case that silently corrupts every
# value a Windows editor touches.
# shellcheck source=deploy/windows/load-env.sh
. "$(dirname "$0")/load-env.sh"
load_env .env

# --- PATH ---------------------------------------------------------------------------
#
# The systemd units pin `Environment=PATH=...` because a user unit inherits almost
# nothing. A Task Scheduler action inherits the SYSTEM PATH, which is close enough for
# `curl` and `git` but does NOT contain the venv -- and `tools/video_fetch.py` resolves
# `yt-dlp` BY NAME through the shell. Prepended, never appended: `yt-dlp` is held at a
# dated nightly, so a stale system copy earlier on PATH would shadow the pinned one and
# every media leg would probe a version the pipeline never runs.
# ABSOLUTE, not relative: a relative PATH entry is resolved against whatever the
# CURRENT directory is at the moment a subprocess is spawned, so any `cd` downstream
# silently drops the venv back off the front.
if [ -d "$PWD/.venv/Scripts" ]; then
    PATH="$PWD/.venv/Scripts:$PATH"
    export PATH
fi

# --- the journal's stand-in ----------------------------------------------------------
#
# `deploy/README.md` says of the Linux host: "There is no logfile. Everything goes to
# the systemd journal, which handles its own rotation -- deliberately, so nothing grows
# an unmanaged file on a laptop." Windows has no journal, and a Task Scheduler action's
# stdout goes NOWHERE, so on this host that same sentence would mean no record at all:
# `run-job.sh` ends by tailing 60 lines of the job's output, and every one of them would
# be discarded. That tail is the thing the Linux operator actually reads.
#
# So the output is tee'd to ONE file per job and trimmed after every run. The cap is the
# point -- it keeps the README's promise (nothing grows unmanaged) on a host whose
# scheduler will not keep it for us.
# --- encoding --------------------------------------------------------------------
#
# Force UTF-8 for every Python child. Windows defaults a redirected stdout to the ANSI
# codepage (cp1252 here), and this repo's own output is full of emoji and em-dashes --
# the signal daemon opens with a 📅 line.
#
# ⚠ NOT cosmetic, which is what the mangled log undersells. Measured 2026-09-18:
# `python -c "print('📅 — dash')"` with stdout redirected exits **1** with a
# UnicodeEncodeError traceback under the default codepage, and exits 0 printing clean
# UTF-8 with PYTHONUTF8=1. The first scheduled signal-watch run survived only because
# of which path happened to emit the emoji; a different one kills the job outright.
# Four of the five jobs carry no healthchecks URL, so that death would be silent.
#
# Set HERE rather than per-task in `install-tasks.ps1` so it covers every job at once
# and takes effect without re-registering anything.
PYTHONUTF8=1
export PYTHONUTF8

LOG_DIR="${BUIBUI_LOG_DIR:-logs}"
LOG_MAX_LINES="${BUIBUI_LOG_MAX_LINES:-2000}"
mkdir -p "$LOG_DIR"
logfile="$LOG_DIR/$1.log"

# NOT `exec`: the pipeline's left-hand status has to be read back, and an exec'd process
# has no shell left to read it.
#
# `${PIPESTATUS[0]}` rather than `$?`. ⚠ Measured: with `set -o pipefail` above, a bare
# `$?` gives the SAME answer for every case tested, so this is not the bug fix it looks
# like -- it is independence from two things. It does not rely on `pipefail` staying set
# at the top of this file, and it distinguishes "the JOB failed" from "TEE failed",
# which `pipefail` deliberately conflates: a full disk would otherwise be reported as a
# failed signal-watch run.
#
# The exit code is load-bearing twice over: `run-job.sh` preserves the wrapped command's
# code on purpose, and `tools/task_probe.py` reads it back out of Task Scheduler to
# decide whether the daily check's soft exit 2 was a real failure.
"${RUN_JOB:-deploy/run-job.sh}" "$@" 2>&1 | tee -a "$logfile"
rc=${PIPESTATUS[0]}

if [ -f "$logfile" ]; then
    tail -n "$LOG_MAX_LINES" "$logfile" >"$logfile.trim" && mv "$logfile.trim" "$logfile"
fi

exit "$rc"
