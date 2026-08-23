#!/usr/bin/env bash
# Daily XS workflow: refresh the universe bars (1h/4h/1d/1w), then run the executor.
# Invoked by the buibui-xsmom systemd timer via run-job.sh.
#
# Driven by env (systemd EnvironmentFile=/opt/buibui/.env):
#   EXEC_MODE       dry_run | testnet | live   (default dry_run)
#   EXEC_CAPITAL    optional fixed sizing capital for a testnet A/B (omit for live)
#   EXEC_EXTRA_ARGS optional extra executor flags, e.g. "--vol-target 0.10"
set -euo pipefail
cd "$(dirname "$0")/.."

export DATA_SOURCE="${DATA_SOURCE:-binance}"

# `tools/xsmom_execute.py` imports the `analytics` package by name, and running a
# script from tools/ puts tools/ on sys.path -- not the repo root. The Makefile
# target sets PYTHONPATH=. for exactly this reason; this script did not, so the
# EXECUTOR half of the daily workflow failed with
# `ModuleNotFoundError: No module named 'analytics'` while the sync half above
# succeeded (buibui.py is a root-level entry point and needs no help).
#
# The split failure is why this went unnoticed: the job did real work, wrote real
# bars, and only then died. Found 2026-08-07 the first time this script was run
# under systemd rather than by hand.
export PYTHONPATH="${PYTHONPATH:-$PWD}"

# The XS book sizes on 1d, but this is the ONLY universe sync anything SCHEDULES
# — the Make target `buibui-universe-sync` is hand-run — so its timeframe list is
# the universe's whole routine coverage, not just this job's input. Syncing 1d
# alone here is what froze 22 of 25 symbols for 11 weeks on 1h/4h/1w (ST61a); the
# fix landed on the Make target and this line kept the bug (ST61b).
# Pinned by tests/test_ohlcv_freshness.py::TestUniverseSyncCoverage.
poetry run python buibui.py analytics sync --universe --timeframes 1h 4h 1d 1w

args=(--mode "${EXEC_MODE:-dry_run}")
if [ -n "${EXEC_CAPITAL:-}" ]; then
    args+=(--capital "${EXEC_CAPITAL}")
fi
if [ -n "${EXEC_EXTRA_ARGS:-}" ]; then
    # Intentional word-splitting: EXEC_EXTRA_ARGS is operator-supplied flags.
    # shellcheck disable=SC2206
    args+=(${EXEC_EXTRA_ARGS})
fi

poetry run python tools/xsmom_execute.py "${args[@]}"
