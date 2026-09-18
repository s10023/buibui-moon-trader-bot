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

exec deploy/run-job.sh "$@"
