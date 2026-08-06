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
#
# Optional env: NET_WAIT_SECS (default 60) · NET_WAIT_INTERVAL (2) ·
# NET_WAIT_HOSTS (api.telegram.org) — the resume-from-suspend resolver gate below.
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

# --- network-readiness gate ---------------------------------------------------
# A laptop user-timer with Persistent=true fires the run it missed the MOMENT the
# user manager resumes from suspend — before NetworkManager has re-associated. On
# 2026-08-06 that put this whole wrapper inside an ~18s window where nothing
# resolved, so all three network legs died at once: the /start ping, the job
# itself (rc=1 on a DNS NameResolutionError), and the /fail ping. healthchecks
# therefore saw NO ping rather than a failed one, and the operator got a Telegram
# that read like a broken bot instead of a laptop opening its lid.
#
# The committed VPS units order on network-online.target, and that cannot fix
# this: the target does not exist in the systemd USER manager at all, and even in
# system scope it is a BOOT-time barrier that stays active across suspend and
# never re-arms on resume. So the gate belongs here in the wrapper — which also
# places it ahead of the healthchecks pings, where a unit-level ordering
# dependency never reached.
#
# Default probe host is the Telegram API because it is the one host EVERY job in
# this kit needs: it is the failure-reporting channel, so if it resolves the
# resolver is up. Space-separated NET_WAIT_HOSTS succeeds on the FIRST host that
# answers.
#
# Bounded and outcome-preserving: a genuine outage still fails exactly as it does
# today, just NET_WAIT_SECS later. This gate only ever changes TIMING, never the
# exit code — so it cannot mask a real network failure.
net_wait_secs="${NET_WAIT_SECS:-60}"
net_wait_interval="${NET_WAIT_INTERVAL:-2}"
net_wait_hosts="${NET_WAIT_HOSTS:-api.telegram.org}"

wait_for_dns() {
    local deadline=$((SECONDS + net_wait_secs)) host
    while :; do
        for host in $net_wait_hosts; do
            getent hosts "$host" >/dev/null 2>&1 && return 0
        done
        # Deadline-based, not iteration-based, so NET_WAIT_INTERVAL=0 still
        # terminates instead of spinning forever.
        [ "$SECONDS" -ge "$deadline" ] && return 1
        sleep "$net_wait_interval"
    done
}

if ! wait_for_dns; then
    printf 'run-job.sh: waited %ss for DNS (%s) and got nothing — running anyway\n' \
        "$net_wait_secs" "$net_wait_hosts" >&2
fi

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
