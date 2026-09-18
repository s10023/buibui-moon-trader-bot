#!/usr/bin/env bash
# `EnvironmentFile=`, hand-rolled — the one thing systemd does for every unit that
# Task Scheduler has no equivalent for.
#
# Sourced by `deploy/windows/job.sh`. It lives in its own file ONLY so
# `tests/test_windows_load_env.py` can source it and assert on the parse; the CR case
# below is silent data corruption, and a loader that cannot be tested is where that
# hides.
#
# PARSED, NOT SOURCED
# -------------------
# `. ./.env` treats the file as SHELL, so an unquoted value containing a space
# (`EXEC_EXTRA_ARGS=--vol-target 0.10`) assigns only the first word and then tries to
# RUN `0.10` as a command. systemd's EnvironmentFile is not shell and never behaved that
# way, so a `.env` that works on the Linux box has to keep working here.
#
# ⚠ THE CARRIAGE RETURN IS THE DANGEROUS ONE, AND IT IS WINDOWS-ONLY
# ------------------------------------------------------------------
# Git Bash's bash tolerates CRLF in a SCRIPT — a CRLF script runs, its comparisons
# match, and `bash -n` is clean — but it does NOT strip CR from data a script READS at
# runtime. Measured 2026-09-18: a `TELEGRAM_BOT_TOKEN=123:abc` line in a CRLF file
# yields a value of length 8, not 7.
#
# That asymmetry is exactly what makes it expensive. Nothing fails loudly: the token
# still looks right in any print-out, and it is Telegram that rejects it, so the symptom
# is "the bot stopped alerting" with a healthy-looking config. `.env` is gitignored, so
# `.gitattributes` cannot protect it — any Windows editor that writes CRLF (Notepad
# does, by default) reintroduces this. The strip therefore belongs HERE, not in a
# checkout rule.

# Populate the environment from an EnvironmentFile-style file. A missing file is not an
# error: every unit uses `EnvironmentFile=-`, whose `-` prefix means exactly that.
load_env() {
    local file="${1:-.env}" line key val
    [ -f "$file" ] || return 0
    while IFS= read -r line || [ -n "$line" ]; do
        line=${line%$'\r'}
        case "$line" in '' | '#'*) continue ;; esac
        key=${line%%=*}
        val=${line#*=}
        # A key with shell-unsafe characters is a malformed line, not a variable.
        case "$key" in '' | *[!A-Za-z0-9_]*) continue ;; esac
        # One layer of surrounding quotes, as systemd's parser strips.
        case "$val" in
            \"*\") val=${val#\"} && val=${val%\"} ;;
            \'*\') val=${val#\'} && val=${val%\'} ;;
        esac
        export "$key=$val"
    done <"$file"
}
