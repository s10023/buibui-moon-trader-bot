#!/usr/bin/env bash
# Generic job wrapper for the buibui systemd timers.
#
# Runs a command, pings a healthchecks.io dead-man's-switch, and Telegrams the
# log tail on failure. The exit code of the wrapped command is always preserved.
#
# Usage:  run-job.sh <label> <HC_ENV_VAR_NAME> -- <command> [args...]
#
# The healthchecks URL is looked up *by env-var name* (indirect expansion), so an
# unset/empty URL degrades gracefully instead of breaking positional parsing.
# Telegram creds (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID) are read from the env
# (systemd EnvironmentFile=/opt/buibui/.env).
set -uo pipefail

label="${1:?usage: run-job.sh <label> <HC_ENV_VAR_NAME> -- <command...>}"
hc_var="${2:?usage: run-job.sh <label> <HC_ENV_VAR_NAME> -- <command...>}"
shift 2
if [ "${1:-}" = "--" ]; then shift; fi

# Run from the repo root (this script lives in <repo>/deploy/).
cd "$(dirname "$0")/.." || exit 1

hc_url="${!hc_var:-}"

hc_ping() {  # $1 = suffix ("" | "/start" | "/fail"); best-effort, never fails the job
    [ -n "$hc_url" ] || return 0
    curl -fsS -m 10 --retry 3 "${hc_url}${1}" -o /dev/null || true
}

log="$(mktemp)"
trap 'rm -f "$log"' EXIT

hc_ping "/start"
start_ts="$(date -u +%FT%TZ)"
"$@" >"$log" 2>&1
rc=$?
end_ts="$(date -u +%FT%TZ)"

tail -n 60 "$log"

if [ "$rc" -eq 0 ]; then
    hc_ping ""
else
    hc_ping "/fail"
    msg="$(printf 'buibui [%s] FAILED rc=%s\n%s -> %s\n\n%s' \
        "$label" "$rc" "$start_ts" "$end_ts" "$(tail -n 25 "$log")")"
    MSG="$msg" poetry run python -c \
        'import os; from utils.telegram import send_telegram_message as s; s(os.environ["MSG"])' \
        || true
fi

exit "$rc"
