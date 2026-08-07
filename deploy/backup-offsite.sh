#!/usr/bin/env bash
# Off-machine leg of the backup: rclone the local snapshot tree to a remote.
#
# WHY THIS EXISTS SEPARATELY FROM backup-analytics.sh
# ---------------------------------------------------
# That script fixes the LIKELY failure (fat-finger delete, bad script, tool bug)
# by writing verified snapshots outside the repo. It explicitly does NOT fix the
# TOTAL failure -- disk death, laptop lost or stolen -- because every copy it
# makes lives on the same disk as the original. `analytics.db` is single-copy
# and its live outcome rows are NOT reconstructible: Binance will not re-serve
# historical signal fires, and per SoT N8 restarting collection yields a
# differently-BIASED sample rather than an equivalent one. So the local leg
# alone still loses everything to one hardware event.
#
# backup-analytics.sh shaped $BUIBUI_BACKUP_ROOT so this leg is only ever an
# `rclone sync` -- dated directories, no in-place mutation, a MANIFEST.json per
# snapshot. This script is that sync and nothing more; it deliberately does no
# snapshotting of its own, so there is exactly one place that decides what a
# good snapshot is.
#
# SYNC, NOT COPY -- AND WHY THE RETENTION ORDER MATTERS
# -----------------------------------------------------
# `sync` mirrors deletions, which is what makes remote retention match local
# retention instead of growing without bound. The consequence to respect: a bug
# that empties $BACKUP_ROOT would, on the next run, empty the remote too. That
# is why this refuses to sync a source that has no snapshots in it -- an empty
# or missing backup root is treated as a fault, never as "nothing to do".
#
# Usage:  backup-offsite.sh [--dry-run]
#
# Env: BUIBUI_BACKUP_REMOTE  (REQUIRED, e.g. "b2:buibui-backups" or "gdrive:buibui")
#      BUIBUI_BACKUP_ROOT    (default ~/backups/buibui -- same default as the local leg)
#      BUIBUI_RCLONE_FLAGS   (optional extra flags, e.g. --bwlimit 2M)
#
# ONE-TIME SETUP, which is interactive and therefore not automatable here:
#   1. install rclone            (e.g. `brew install rclone`)
#   2. rclone config             (OAuth / key entry for your provider)
#   3. put BUIBUI_BACKUP_REMOTE=<remote>:<path> in .env
#   4. only THEN enable the timer:
#      systemctl --user enable --now buibui-backup-offsite.timer

set -uo pipefail

cd "$(dirname "$0")/.." || exit 1

BACKUP_ROOT="${BUIBUI_BACKUP_ROOT:-$HOME/backups/buibui}"
REMOTE="${BUIBUI_BACKUP_REMOTE:-}"
DRY=0
for a in "$@"; do
    case "$a" in
        --dry-run) DRY=1 ;;
        *) echo "unknown argument: $a" >&2; exit 2 ;;
    esac
done

# Fail loudly rather than exiting 0. An unconfigured remote on an ENABLED timer
# is the silent-never-ran failure this whole file exists to prevent -- it would
# look green forever while protecting nothing. Configure first, enable second.
if [ -z "$REMOTE" ]; then
    echo "ERROR: BUIBUI_BACKUP_REMOTE is unset." >&2
    echo "  Off-machine backup is NOT running. Set it in .env (e.g." >&2
    echo "  BUIBUI_BACKUP_REMOTE=b2:buibui-backups) after \`rclone config\`," >&2
    echo "  or disable this timer: systemctl --user disable --now buibui-backup-offsite.timer" >&2
    exit 1
fi

if ! command -v rclone >/dev/null 2>&1; then
    echo "ERROR: rclone is not installed, so no off-machine copy exists." >&2
    echo "  Install it (brew install rclone), then \`rclone config\`." >&2
    exit 1
fi

if [ ! -d "$BACKUP_ROOT" ]; then
    echo "ERROR: backup root $BACKUP_ROOT does not exist -- run backup-analytics.sh first." >&2
    exit 1
fi

# Guard the sync-mirrors-deletions hazard described above: a source with no
# verified snapshot is a fault, not an empty workload. MANIFEST.json is the
# local leg's own completeness marker (a mid-build crash once left a 100MB
# directory that DuckDB opened while reporting ZERO tables), so counting
# manifests counts snapshots that actually finished.
manifests=$(find "$BACKUP_ROOT" -name MANIFEST.json -type f 2>/dev/null | wc -l)
if [ "$manifests" -eq 0 ]; then
    echo "ERROR: no MANIFEST.json under $BACKUP_ROOT -- refusing to sync." >&2
    echo "  Syncing now would mirror the empty tree and DELETE the remote copies." >&2
    exit 1
fi

echo "off-site backup: $BACKUP_ROOT -> $REMOTE  ($manifests verified snapshot(s))"

# --checksum, not size+mtime: these are large immutable snapshot files, and an
# rclone re-upload triggered by clock skew alone would cost real bandwidth.
# --transfers 2 keeps a laptop's uplink usable while it runs.
flags=(--checksum --transfers 2 --stats-one-line --stats 30s)
[ "$DRY" -eq 1 ] && flags+=(--dry-run)
# shellcheck disable=SC2086  # BUIBUI_RCLONE_FLAGS is intentionally word-split
rclone sync "$BACKUP_ROOT" "$REMOTE" "${flags[@]}" ${BUIBUI_RCLONE_FLAGS:-}
rc=$?

if [ "$rc" -ne 0 ]; then
    echo "ERROR: rclone sync failed (rc=$rc) -- the off-machine copy is STALE." >&2
    exit "$rc"
fi

echo "off-site backup OK"
