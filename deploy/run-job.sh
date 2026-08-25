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

# Bold headline + a <pre> body, with the body HTML-ESCAPED.
#
# The escaping is a bug fix, not cosmetics. utils/telegram.py sends parse_mode=HTML
# and documents the consequence: a traceback carries `line 33, in <module>`, which
# Telegram's HTML parser reads as an unclosed tag and rejects with a 400 -- so the
# failure alert failed on precisely the crashes it exists to report (observed
# 2026-08-07 on an xsmom traceback). That fallback still exists and still works;
# escaping means it no longer has to, and the message keeps its formatting.
#
# <pre> also preserves COLUMN ALIGNMENT. The daily-check report is aligned ASCII,
# and Telegram's proportional font destroys it into unreadable ragged text.
#
# Telegram hard-caps a message at 4096 chars and rejects the whole send past it,
# so the body is capped well under that -- an over-long alert fails exactly like
# no alert.
#
# <pre> is also why the body must be FOLDED here. It preserves alignment by never
# soft-wrapping, so one over-wide line drags the entire report sideways on a phone
# (measured 2026-08-18: a single 164-char daily-check line did exactly that). The
# fold runs BEFORE the byte cap deliberately -- folding inserts a newline every
# TG_FOLD_WIDTH columns, so capping first would add those newlines on top of the
# budget the cap exists to hold. Both legs live in tg_send rather than at the call
# sites so the success and failure paths cannot drift apart.
TG_FOLD_WIDTH=46   # phone-readable column inside Telegram's <pre> monospace
TG_BODY_CAP=3400   # bytes, kept well under Telegram's 4096 hard cap

tg_send() { # $1 = headline, $2 = body
    local body
    body="$(printf '%s\n' "$2" | fold -w "$TG_FOLD_WIDTH" -s | tail -c "$TG_BODY_CAP")"
    HEAD="$1" BODY="$body" poetry run python -c \
        'import html, os; from utils.telegram import send_telegram_message as s; s("<b>" + html.escape(os.environ["HEAD"]) + "</b>\n<pre>" + html.escape(os.environ["BODY"]) + "</pre>")' \
        || true
}

# --- soft-fail dispatch -------------------------------------------------------
# SOFT_FAIL_RC names ONE exit code this job uses for "success, with warnings".
#
# Opt-in per job, for exactly the reason TELEGRAM_ALWAYS is: a bare exit code is
# not self-describing. argparse exits 2 on a USAGE error, so reading 2 as soft
# for EVERY job would turn a broken `signal watch` invocation into a heartbeat --
# the precise failure this wrapper exists to make loud.
#
# Why it exists: buibui-daily-check runs `daily_check.py --exit-on-tier2`, which
# returns non-zero on a routine tier-2 red (chart-drops, freshness). Under the
# old two-branch dispatch that suppressed the heartbeat, pinged /fail and titled
# the push FAILED -- so a dead timer and a routine tier-2 nudge were identical on
# the operator's phone. That defeats the heartbeat doctrine set out above: the
# whole point of TELEGRAM_ALWAYS is that silence stays falsifiable, and a daily
# false FAILED trains the reader to ignore the channel just as effectively.
#
# Losing this setting is LOUD, not silent -- the job reverts to paging FAILED.
# That is the fail-safe direction, the same one the task-marks rule takes when it
# reads a MISSING marker as overdue.
soft_rc="${SOFT_FAIL_RC:-}"
# A non-numeric value disables the branch rather than erroring `test -eq`.
# Disabling is the safe direction: it can only make a job louder, never quieter.
case "$soft_rc" in ''|*[!0-9]*) soft_rc="" ;; esac

if [ "$rc" -eq 0 ]; then
    hc_ping ""
    # TELEGRAM_ALWAYS=1 turns "silence = healthy" into a POSITIVE heartbeat.
    #
    # Why this is not merely nice-to-have: with failure-only push, a dead timer
    # and a green day are indistinguishable on the operator's phone, and the
    # delivery path is therefore only ever exercised on a red day -- the one day
    # you need it to already work. A daily success message makes silence
    # falsifiable. Opt-in per job, because the 15-minute signal-watch would
    # otherwise send 96 messages a day.
    if [ -n "${TELEGRAM_ALWAYS:-}" ]; then
        tg_send "buibui [$label] ok — $start_ts → $end_ts" \
            "$(tail -n 60 "$log")"
    fi
elif [ -n "$soft_rc" ] && [ "$rc" -eq "$soft_rc" ]; then
    # Success-with-warnings. The run COMPLETED, so the dead-man's-switch must see
    # a healthy ping -- /fail here would mean "this job is not running", which is
    # false and is what made a tier-2 red indistinguishable from a dead timer.
    hc_ping ""
    # Pushed unconditionally, NOT behind TELEGRAM_ALWAYS: a job only reaches this
    # branch by opting in via SOFT_FAIL_RC, and it opted in precisely because the
    # warning is worth reading. Same 60-line tail as the ok path, because the
    # warning detail sits at the END of the report.
    tg_send "buibui [$label] ok, warnings rc=$rc — $start_ts → $end_ts" \
        "$(tail -n 60 "$log")"
else
    hc_ping "/fail"
    tg_send "buibui [$label] FAILED rc=$rc — $start_ts → $end_ts" \
        "$(tail -n 25 "$log")"
fi

exit "$rc"
