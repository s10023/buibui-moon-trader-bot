#!/usr/bin/env bash
# Daily XS workflow: refresh the universe 1d bars, then run the executor.
# Invoked by the buibui-xsmom systemd timer via run-job.sh.
#
# Driven by env (systemd EnvironmentFile=/opt/buibui/.env):
#   EXEC_MODE       dry_run | testnet | live   (default dry_run)
#   EXEC_CAPITAL    optional fixed sizing capital for a testnet A/B (omit for live)
#   EXEC_EXTRA_ARGS optional extra executor flags, e.g. "--vol-target 0.10"
set -euo pipefail
cd "$(dirname "$0")/.."

export DATA_SOURCE="${DATA_SOURCE:-binance}"

# The XS book runs on 1d only — sync that timeframe before sizing the book.
poetry run python buibui.py analytics sync --universe --timeframes 1d

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
