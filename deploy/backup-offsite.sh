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
# WHAT STOPS THIS TOUCHING ANYTHING ELSE ON THE DRIVE
# ---------------------------------------------------
# Three guards, deliberately at different layers, because the backup may share a
# drive with data this repo does not own and cannot restore:
#   1. rclone's own `root_folder_id`, pinned on the remote, so rclone resolves
#      every path relative to the backup folder and cannot address anything
#      above it. Strongest, but it lives in rclone.conf and is LOST when the
#      config is recreated -- which is what rotating a credential does.
#   2. a rejection here of any remote without a path component: a bare
#      `remote:` is the whole drive, and sync mirrors deletions into it.
#   3. a rejection here of a destination holding entries the local root does
#      not have, which catches a well-formed remote aimed at an UNRELATED
#      folder. It compares top level only, so it does NOT catch a sibling repo
#      whose tree is the same shape -- see the measured limit at that check.
# Guards 2 and 3 are tracked code and survive a reclone; guard 1 does not. Keep
# all three -- each covers a failure the others do not see.
#
# Sharing one drive with another repo is therefore a CONFINEMENT question, not a
# guard question: give each repo its own remote with its own `root_folder_id`.
#
# ONE-TIME SETUP, which is interactive and therefore not automatable here:
#   1. install rclone            (e.g. `brew install rclone`)
#   2. rclone config             (OAuth / key entry for your provider)
#   3. confine the remote to the backup folder -- REDO THIS AFTER ANY
#      `rclone config delete` / recreate, which silently drops it:
#        rclone mkdir <remote>:<folder>
#        rclone lsf <remote>: --dirs-only --format ip | grep <folder>
#        rclone config update <remote> root_folder_id=<ID> --non-interactive >/dev/null
#      Verify: `rclone lsf <remote>:` lists the folder's CONTENTS, not the
#      drive root. That check is the whole proof; run it, do not assume it.
#
#      ⚠ THE `>/dev/null` IS A SECURITY CONTROL, NOT TIDINESS. `rclone config
#      create` and `update` PRINT THE WHOLE REMOTE ON SUCCESS -- client_secret,
#      access_token, refresh_token -- unprompted and unflagged. Two live tokens
#      leaked into transcripts on 2026-08-15 this way, the second AFTER both
#      repos had written up the first. The prose rule ("never paste the output")
#      is what failed, because the tool emits the secret without being asked and
#      the burden was on a human to notice. Keep the redirect at every call
#      site, in docs too: people copy from docs, so a doc showing the bare
#      command IS the vulnerability.
#   4. put BUIBUI_BACKUP_REMOTE=<remote>:<path> in .env
#   5. only THEN enable the timer:
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

# The remote must name a PATH inside the drive, never a bare `remote:`.
#
# `sync` mirrors deletions into its destination, so a destination of `gdrive:`
# IS the whole drive -- one missing path component turns "back up" into "delete
# everything that is not a snapshot". That is a single-character typo away, and
# when the backup lands on a drive holding anything else, the blast radius is
# data this repo does not own and cannot restore.
#
# rclone's own `root_folder_id` confines the remote far more strongly, and it is
# set. It is NOT sufficient on its own: it lives in rclone.conf, and recreating
# the config drops it -- which is exactly what rotating a leaked token does. This
# check is tracked code, so it survives both a reclone and a config rebuild.
case "$REMOTE" in
    *:*) : ;;
    *)
        echo "ERROR: BUIBUI_BACKUP_REMOTE='$REMOTE' is not a remote." >&2
        echo "  Expected <remote>:<path>, e.g. gdrive:buibui-backups." >&2
        echo "  Without a colon rclone writes to a LOCAL directory, so there" >&2
        echo "  would be no off-machine copy while this job looked green." >&2
        exit 1
        ;;
esac
if [ -z "${REMOTE#*:}" ]; then
    echo "ERROR: BUIBUI_BACKUP_REMOTE='$REMOTE' has no path component." >&2
    echo "  A bare 'remote:' is the ENTIRE drive, and sync MIRRORS DELETIONS," >&2
    echo "  so this would delete every file on it that is not a local snapshot." >&2
    echo "  Use e.g. '${REMOTE}buibui-backups'." >&2
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

# Never sync INTO data this script did not put there.
#
# The two checks above catch a MALFORMED remote. This one catches a well-formed
# remote pointing somewhere unintended -- a real folder that simply is not ours.
# `sync` would delete everything in it that has no local counterpart, and on a
# drive shared with anything else that is unrecoverable.
#
# The allowed set is derived from the local root rather than hardcoded to
# daily/weekly, so a new tier added by backup-analytics.sh does not read as an
# intruder here. An absent or empty destination lists nothing and passes, which
# is what makes the first-ever sync work.
#
# ⚠ KNOWN LIMIT, MEASURED 2026-08-15 -- do not oversell this guard. It compares
# TOP-LEVEL entries only, so it cannot distinguish a SIBLING REPO's backup from
# our own: the wifey fork's tree is also `daily/` + `weekly/`, and a dry-run of a
# wifey-shaped root aimed at this repo's destination passed the guard and emitted
# `Skipped delete` on real snapshots. It catches an UNRELATED folder (`Photos/`,
# `resume.pdf`); it does not catch a same-shaped one.
#
# The fix for that case is NOT a second path under one confined root -- that
# leaves operator care as the only control. It is a second remote with its OWN
# `root_folder_id`, which makes the collision unreachable rather than detectable,
# since rclone cannot navigate above a pinned root. A marker file at the
# destination was considered and rejected: it would DETECT what a separate
# confined root PREVENTS.
unexpected=""
while IFS= read -r entry; do
    [ -z "$entry" ] && continue
    entry="${entry%/}"
    [ -e "$BACKUP_ROOT/$entry" ] || unexpected="${unexpected}  ${entry}
"
done <<EOF
$(rclone lsf "$REMOTE" 2>/dev/null)
EOF
if [ -n "$unexpected" ]; then
    echo "ERROR: $REMOTE holds entries this script did not create:" >&2
    printf '%s' "$unexpected" >&2
    echo "  Refusing to sync -- sync MIRRORS DELETIONS and would remove them." >&2
    echo "  Check BUIBUI_BACKUP_REMOTE points where you think it does." >&2
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
