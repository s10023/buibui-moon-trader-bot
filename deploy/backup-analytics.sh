#!/usr/bin/env bash
# Local snapshot of the irreplaceable analytics state.
#
# WHAT THIS PROTECTS AGAINST, AND WHAT IT DOES NOT
# ------------------------------------------------
# `analytics.db` is ~320MB, gitignored, and SINGLE-COPY. The committed
# `live_signal.duckdb` looks like a backup and is not one: its
# `signal_alert_outcomes` table has 0 rows, so it ships the schema and none of
# the evidence. Losing the live file costs every live outcome row, and those are
# NOT reconstructible -- Binance will not re-serve historical signal fires, and
# per SoT N8 a restart yields a differently-BIASED sample rather than an
# equivalent one.
#
# This script fixes the LIKELY failure (fat-finger delete, bad script, a tool
# bug) by writing verified snapshots outside the repo. It does NOT fix the TOTAL
# failure (disk death, laptop lost/stolen) -- that needs different hardware, and
# this directory is deliberately shaped so that leg is only ever `rclone sync`
# of $BACKUP_ROOT to a remote.
#
# SNAPSHOT METHOD
# ---------------
# Primary is DuckDB's own `COPY FROM DATABASE`, not `cp`. Measured on the real
# 320MB file: 4.6s and 258MB out (19% smaller -- it repacks free space) versus
# 0.6s and 320MB for a byte copy. The extra 4s buys a transactionally
# consistent read, which a byte copy cannot promise: `cp` of a live DuckDB can
# tear, and if a `.wal` sidecar exists at that instant the two files cannot be
# captured atomically by two separate `cp` calls.
#
# Byte copy survives only as the FALLBACK, because it takes no DuckDB lock and
# therefore still works when the signal-watch timer holds the database. It
# copies the `.wal` too when one is present -- DuckDB replays it on open.
#
# LOCK CONTENTION IS EXPECTED, NOT EXCEPTIONAL
# --------------------------------------------
# The signal-watch user timer fires at :01/:16/:31/:46 and takes an exclusive
# lock. On duckdb 1.5.5 a second PROCESS is refused even with read_only=True --
# only reader-vs-reader shares -- so "open read-only to dodge the writer" does
# not work and was measured not working. Hence: schedule off the quarter hour,
# retry with backoff, and fall back to the lock-free byte copy.
#
# NEVER kill the signal-watch daemon to clear a lock here. It is the OOS ledger
# writer; a backup must never cost you the thing it exists to protect.
#
# Usage:  backup-analytics.sh [--weekly] [--dry-run]
#   --weekly   also EXPORT DATABASE to parquet (format-independent archive)
#   --dry-run  report what would happen, write nothing
#
# Env: BUIBUI_BACKUP_ROOT (default ~/backups/buibui)
#      BUIBUI_KEEP_DAILY  (default 14)
#      BUIBUI_KEEP_WEEKLY (default 8)
#      BUIBUI_LOCK_RETRIES (default 10)  BUIBUI_LOCK_SLEEP (default 30s)

set -uo pipefail

cd "$(dirname "$0")/.." || exit 1
REPO="$PWD"

BACKUP_ROOT="${BUIBUI_BACKUP_ROOT:-$HOME/backups/buibui}"
KEEP_DAILY="${BUIBUI_KEEP_DAILY:-14}"
KEEP_WEEKLY="${BUIBUI_KEEP_WEEKLY:-8}"
LOCK_RETRIES="${BUIBUI_LOCK_RETRIES:-10}"
LOCK_SLEEP="${BUIBUI_LOCK_SLEEP:-30}"

DB="$REPO/analytics.db"
LEDGERS=(
    "docs/plans/pundit-calls.jsonl"
    "docs/plans/ai-cards.jsonl"
    "docs/plans/pundit-overrides.jsonl"
    "config/youtube_channels.toml"
)

# The venv interpreter is named directly rather than via `poetry run` -- one less
# moving part on the minimal PATH a systemd user unit gets.
PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || PY="python3"

want_weekly=0
dry_run=0
for arg in "$@"; do
    case "$arg" in
        --weekly)  want_weekly=1 ;;
        --weekly-if-due) weekly_if_due=1 ;;
        --dry-run) dry_run=1 ;;
        *) printf 'backup-analytics.sh: unknown arg %s\n' "$arg" >&2; exit 2 ;;
    esac
done

stamp="$(date -u +%Y-%m-%d)"
now="$(date -u +%FT%TZ)"
final_dir="$BACKUP_ROOT/daily/$stamp"
weekly_dir="$BACKUP_ROOT/weekly/$stamp"

# EVERYTHING is built under a staging name and renamed into place only after it
# verifies. This is not hypothetical tidiness -- it was added after a real laptop
# crash on 2026-08-07 killed a run mid-copy and left a 100MB directory at the
# FINAL path. That leftover was the dangerous kind of broken: DuckDB opened it
# without error and reported ZERO tables, i.e. the exact live_signal.duckdb
# failure -- a file that looks like a backup and holds no evidence. The row-count
# guard below would have caught it, but the crash killed the script long before
# any guard ran.
#
# `mv` within one filesystem is atomic, so $final_dir either does not exist or
# holds a verified snapshot. There is no state in between for a freshness check
# to mistake for success.
daily_dir="$BACKUP_ROOT/daily/.staging-$$"

log() { printf '%s\n' "$*"; }
die() { printf 'backup-analytics.sh: %s\n' "$*" >&2; exit 1; }

[ -f "$DB" ] || die "no database at $DB"

# --weekly-if-due: run the parquet export when the newest one is >=7 days old.
#
# Deliberately NOT `OnCalendar=Sun` in the timer. This is a laptop that is off or
# suspended a large part of the day -- a fixed weekday means a weekend away skips
# the weekly export entirely and nothing ever notices. Asking "is the newest one
# stale?" self-heals on whatever day the machine is next awake.
#
# Age comes from the directory's DATE-STAMPED NAME, not its mtime. Same reason
# the daily-check external-context line had to be fixed on 2026-08-07: mtime
# answers "when was this touched", which is a different question from "how old is
# the thing inside", and the two silently diverge.
if [ "${weekly_if_due:-0}" -eq 1 ]; then
    newest="$(find "$BACKUP_ROOT/weekly" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | sort -r | head -1)"
    if [ -z "$newest" ]; then
        want_weekly=1
    else
        newest_epoch="$(date -u -d "$(basename "$newest")" +%s 2>/dev/null || echo 0)"
        age_days=$(( ( $(date -u +%s) - newest_epoch ) / 86400 ))
        [ "$age_days" -ge 7 ] && want_weekly=1
    fi
fi

if [ "$dry_run" -eq 1 ]; then
    log "DRY RUN -- nothing will be written"
    log "  source     $DB ($(du -h "$DB" | cut -f1))"
    log "  daily  ->  $final_dir (staged, then renamed on verify)"
    [ "$want_weekly" -eq 1 ] && log "  weekly ->  $weekly_dir (parquet)"
    log "  retention  ${KEEP_DAILY} daily / ${KEEP_WEEKLY} weekly"
    for f in "${LEDGERS[@]}"; do
        [ -f "$REPO/$f" ] && log "  ledger     $f ($(du -h "$REPO/$f" | cut -f1))"
    done
    exit 0
fi

# Sweep staging dirs abandoned by earlier crashed runs. Safe to do unconditionally:
# a staging dir is by definition unverified, so nothing of value can be in one.
find "$BACKUP_ROOT/daily" -mindepth 1 -maxdepth 1 -type d -name '.staging-*' \
    -exec rm -rf {} + 2>/dev/null

mkdir -p "$daily_dir" || die "cannot create $daily_dir"
# A failure anywhere below leaves no trace at the final path.
trap 'rm -rf "$daily_dir"; rm -f "${err_file:-}"' EXIT

# --- snapshot the database ----------------------------------------------------
# Returns 0 on a clean COPY FROM DATABASE, 1 if the source lock was never free.
snapshot_clean() {
    "$PY" - "$DB" "$daily_dir/analytics.db" <<'PYEOF'
import sys, duckdb, json

src_path, dst_path = sys.argv[1], sys.argv[2]
hub = duckdb.connect(":memory:")
hub.execute(f"ATTACH '{src_path}' AS src (READ_ONLY)")
hub.execute(f"ATTACH '{dst_path}' AS bk")
hub.execute("COPY FROM DATABASE src TO bk")

# Verification runs inside the SAME connection that made the copy, so source and
# snapshot are compared against a consistent view rather than against a live file
# that may have gained rows in between. Any mismatch here is a real defect, not a
# race -- which is why this is an equality check and not a tolerance.
tables = [r[0] for r in hub.execute(
    "SELECT table_name FROM information_schema.tables "
    "WHERE table_catalog='src' ORDER BY table_name").fetchall()]
counts, bad = {}, []
for t in tables:
    a = hub.execute(f'SELECT count(*) FROM src."{t}"').fetchone()[0]
    b = hub.execute(f'SELECT count(*) FROM bk."{t}"').fetchone()[0]
    counts[t] = a
    if a != b:
        bad.append(f"{t}: src={a} snapshot={b}")
hub.close()

if bad:
    print("VERIFY-FAIL " + "; ".join(bad), file=sys.stderr)
    sys.exit(3)
print(json.dumps(counts))
PYEOF
}

# Fallback: takes no DuckDB lock, so it works while the writer holds the file.
snapshot_bytes() {
    cp "$DB" "$daily_dir/analytics.db" || return 1
    # Copy the WAL when one exists; DuckDB replays it on open. Order matters --
    # db first, then wal -- so the wal is never older than the db it replays into.
    [ -f "$DB.wal" ] && cp "$DB.wal" "$daily_dir/analytics.db.wal"
    return 0
}

method=""
counts_json=""
# No trap here -- the EXIT trap set at staging time already removes this.
err_file="$(mktemp)"
for attempt in $(seq 1 "$LOCK_RETRIES"); do
    # Assign and capture rc on SEPARATE lines. Folding this into `if cmd; then`
    # loses the real exit code: a false if-condition with no else leaves $? at 0,
    # so the rc==3 branch below would be unreachable and a verification mismatch
    # would silently retry as though it were lock contention.
    counts_json="$(snapshot_clean 2>"$err_file")"
    rc=$?
    if [ "$rc" -eq 0 ]; then
        method="copy-from-database"
        break
    fi
    # rc 3 is a verification mismatch, which retrying cannot fix.
    if [ "$rc" -eq 3 ]; then
        cat "$err_file" >&2
        die "snapshot verification FAILED -- source and copy disagree"
    fi
    log "database locked (attempt $attempt/$LOCK_RETRIES) -- retrying in ${LOCK_SLEEP}s"
    sleep "$LOCK_SLEEP"
done

if [ -z "$method" ]; then
    log "lock never cleared after $LOCK_RETRIES attempts -- falling back to byte copy"
    snapshot_bytes || die "byte-copy fallback also failed"
    method="byte-copy"
fi

# --- verify the snapshot opens standalone -------------------------------------
# The in-transaction check above proves the COPY was faithful. This proves the
# resulting FILE is independently openable -- the property that actually matters
# at restore time, and the only check the byte-copy path gets at all.
verify_json="$("$PY" - "$daily_dir/analytics.db" <<'PYEOF'
import sys, duckdb, json
try:
    c = duckdb.connect(sys.argv[1], read_only=True)
    tables = [r[0] for r in c.execute("SHOW TABLES").fetchall()]
    counts = {t: c.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0] for t in tables}
    c.close()
    print(json.dumps(counts))
except Exception as e:
    print(f"OPEN-FAIL {type(e).__name__}: {e}", file=sys.stderr)
    sys.exit(1)
PYEOF
)" || die "snapshot at $daily_dir/analytics.db does NOT open -- refusing to keep a backup that cannot be restored"

outcomes="$(printf '%s' "$verify_json" | "$PY" -c \
    'import sys,json; print(json.load(sys.stdin).get("signal_alert_outcomes","?"))')"

# A snapshot whose crown-jewel table is empty is the exact failure mode that made
# live_signal.duckdb useless as a backup. Refuse to record it as a good one.
[ "$outcomes" = "0" ] && die "snapshot has 0 signal_alert_outcomes rows -- that is the live_signal.duckdb failure, not a backup"

# --- ledgers ------------------------------------------------------------------
for f in "${LEDGERS[@]}"; do
    if [ -f "$REPO/$f" ]; then
        mkdir -p "$daily_dir/$(dirname "$f")"
        cp "$REPO/$f" "$daily_dir/$f"
    fi
done

# --- manifest -----------------------------------------------------------------
{
    printf '{\n'
    printf '  "captured_at_utc": "%s",\n' "$now"
    printf '  "method": "%s",\n' "$method"
    printf '  "source": "%s",\n' "$DB"
    printf '  "source_bytes": %s,\n' "$(stat -c %s "$DB")"
    printf '  "snapshot_bytes": %s,\n' "$(stat -c %s "$daily_dir/analytics.db")"
    printf '  "git_commit": "%s",\n' "$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || echo unknown)"
    printf '  "duckdb": "%s",\n' "$("$PY" -c 'import duckdb; print(duckdb.__version__)' 2>/dev/null || echo unknown)"
    printf '  "row_counts": %s\n' "$verify_json"
    printf '}\n'
} > "$daily_dir/MANIFEST.json"

# --- publish atomically -------------------------------------------------------
# Only now, with the snapshot verified and manifested, does it take the name a
# freshness check looks for. Replacing an existing same-day dir is deliberate:
# re-running on the same UTC date should refresh, and the incoming copy has
# already passed every check the outgoing one did.
rm -rf "$final_dir"
mv "$daily_dir" "$final_dir" || die "could not publish snapshot to $final_dir"
trap 'rm -f "${err_file:-}"' EXIT   # staging is gone; stop trying to remove it

log "daily snapshot ok  [$method]  $final_dir"
log "  signal_alert_outcomes = $outcomes rows"

# --- weekly parquet export ----------------------------------------------------
# A raw .db snapshot is hostage to the DuckDB storage format, which has broken
# across versions before -- a file archived today may not open on a future
# DuckDB. EXPORT DATABASE writes parquet + SQL instead: portable, compressible,
# and readable by anything, at the cost of being slower and losing DuckDB-native
# structure. Belt (fast daily .db) and braces (portable weekly parquet).
if [ "$want_weekly" -eq 1 ]; then
    mkdir -p "$weekly_dir"
    if "$PY" - "$final_dir/analytics.db" "$weekly_dir/parquet" <<'PYEOF'
import sys, duckdb
# Export from the SNAPSHOT, never the live file: the snapshot is already verified
# and takes no lock away from the signal-watch writer.
c = duckdb.connect(sys.argv[1], read_only=True)
c.execute(f"EXPORT DATABASE '{sys.argv[2]}' (FORMAT PARQUET)")
c.close()
PYEOF
    then
        log "weekly parquet export ok  $weekly_dir/parquet ($(du -sh "$weekly_dir/parquet" | cut -f1))"
    else
        # Non-fatal: the daily .db snapshot is the primary artifact and it already
        # succeeded. Losing the portable copy is a degraded run, not a failed one.
        log "WARNING: weekly parquet export FAILED -- daily snapshot is still good"
    fi
fi

# --- retention ----------------------------------------------------------------
# Prune by count, oldest first. Directory names are UTC date stamps, so a plain
# reverse sort is chronological.
prune() {
    local dir="$1" keep="$2" label="$3" n=0
    [ -d "$dir" ] || return 0
    while IFS= read -r d; do
        n=$((n + 1))
        if [ "$n" -gt "$keep" ]; then
            rm -rf "$d" && log "pruned old $label snapshot $(basename "$d")"
        fi
    done < <(find "$dir" -mindepth 1 -maxdepth 1 -type d | sort -r)
}
prune "$BACKUP_ROOT/daily" "$KEEP_DAILY" daily
prune "$BACKUP_ROOT/weekly" "$KEEP_WEEKLY" weekly

log "backup root now $(du -sh "$BACKUP_ROOT" | cut -f1) across \
$(find "$BACKUP_ROOT/daily" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l) daily / \
$(find "$BACKUP_ROOT/weekly" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l) weekly"
