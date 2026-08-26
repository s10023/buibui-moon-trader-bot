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

# A GLOB over docs/plans/, not an allowlist of its files.
#
# Every top-level file there is gitignored and single-copy, so this list IS the only
# copy -- and an allowlist over such a tree defaults to UNCOVERED. That default has
# now been found short THREE times (2026-08-08 six artifacts, 2026-08-11 five more,
# 2026-08-20 two more: `test_daily_check_verdict_join.py`, a hand-run gate living
# beside the health check, and an `ai-cards.jsonl.bak-*` taken by hand before a
# schema change). Each round added the files someone happened to notice; none
# changed the default that made them invisible. A glob covers the next artifact the
# day it appears, with nobody needing to notice.
#
# Cost was never the reason anything stayed out: the whole tree is ~1MB against a
# 264MB snapshot. The glob therefore also sweeps in the two files a 2026-08-11 note
# excluded on regenerability grounds -- `coverage-*.txt` and the sister fork's
# `wifey-handoff-prompt-*.md`, 23KB together. Deciding per file cost more attention
# than the bytes ever did.
#
# The classes that make this load-bearing, none of which holds research content:
#   yt-feed-state.json  lost => every already-consumed video re-presents as new
#   routed-ledger.json  lost => re-ingests double-write into the streams
#   daily_check.py      the health-check system ITSELF, gitignored, dead on a reclone
#   regime-log.jsonl    append-only and UNRECONSTRUCTIBLE
# Losing a watermark destroys no past data -- it silently changes future behaviour,
# which is why they are the expensive ones rather than the obvious ones.
#
# `__pycache__` is a directory, which the `-f` test below rejects; the subdirectories
# that DO carry content are in LEDGER_DIRS.
LEDGERS=(
    "docs/plans/*"
    "config/youtube_channels.toml"
    # Gitignored, single-copy, and NOT reconstructible: 16 author entries whose
    # alias mappings are accumulated operator rulings. The committed
    # pundit_roster.toml.example carries 2 schema-demo entries and is not a
    # backup. Losing this drops every relay as unattributable.
    "config/pundit_roster.toml"
    # The X follow roster. Gitignored and single-copy like its YouTube sibling, but
    # WORSE to lose: a YouTube channel id is re-resolvable from a handle, while this
    # file also carries the per-author `intent` and `poll` rulings, which are operator
    # judgement and re-derivable from nothing. Before it existed the roster lived only
    # as a hand-typed `(from:a OR from:b OR ...)` string inside one gitignored scratch
    # JSON, so each discovery run retyped it from memory.
    "config/x_authors.toml"
    # The sharpest entry in the file: the ONLY record that a chart drop was handled,
    # and it lives under .cache/ -- the one directory every cleanup treats as
    # disposable. A plain recursive delete of .cache/ resets chart dedup with no
    # other trace.
    ".cache/chart-drops/processed.json"
)

# Expand one LEDGERS entry, which may be a literal path OR a glob, to the
# repo-relative files it matches. Prints nothing when it matches none.
_ledger_matches() {
    local pattern="$1" f
    # Unquoted on purpose -- this is where the glob expands. A pattern that matches
    # nothing expands to itself, which the -f test then rejects, so a missing file is
    # a skip rather than a failure (same contract as the external-dir loop below).
    for f in $REPO/$pattern; do
        [ -f "$f" ] || continue
        printf '%s\n' "${f#"$REPO"/}"
    done
}

# Directories copied wholesale. Kept separate from LEDGERS because the copy loop
# below is `[ -f ]`-guarded on purpose — a directory silently failed that test and
# was skipped without a word, which is how these went uncovered.
#
# task-marks is the 2026-08-11 addition that matters most here: it is the same
# watermark class as yt-feed-state.json above — losing it destroys no past data but
# silently changes future behaviour, because a missing marker reads as OVERDUE, so
# every weekly cadence would re-present at once. scratch/ holds live research scripts
# the handoff points at by path (ensemble_monotonicity.py).
LEDGER_DIRS=(
    "docs/plans/video-notes"
    # Added with /ingest-x's per-bundle note (2026-08-20). A note directory is the
    # ONLY record a DROPPED bundle leaves, so an uncovered one loses the verdicts
    # rather than merely the copies. Listed here the day the writer shipped, not
    # after an audit finds it -- that gap has now been found five times.
    "docs/plans/x-notes"
    "docs/plans/journal"
    "docs/plans/external-context"
    "docs/plans/task-marks"
    "docs/plans/scratch"
    "docs/plans/chart-drops"
    "docs/plans/xsmom_targets"
    # The gitignored enforcement layer: hooks, settings, and the pre-flip
    # sensitive-terms list. All single-copy, all dead on a reclone, and the
    # term list is what stops `sensitive-terms` degrading to NOT CONFIGURED.
    ".claude"
)

# Files OUTSIDE the repo, as "absolute-source:path-under-the-snapshot". Kept separate
# from LEDGERS because that loop is "$REPO/$f"-relative and would silently resolve an
# absolute path to nonsense.
#
# history.jsonl is the account-level prompt log: sessionId + project + timestamp per
# prompt, for every personal repo. It is the ONLY record of a session that survives
# transcript cleanup, which makes it the independent census the spend tracker checks
# its own coverage against (budget.py --census). Measured 2026-08-14: it showed 35
# buibui sessions in W33 where the spend ledger had ever seen 4, and 52 in W32 where
# the ledger had no entry at all -- the evidence that those weeks are floors. Losing it
# does not lose money already spent, it loses the ability to know what was missed.
#
# It is single-copy and gitignored-by-location (it is not in the repo at all), so the
# allowlist-defaults-to-UNCOVERED rule that produced the 08-08 / 08-11 / 08-12 audits
# applies to it exactly. This is the fourth such addition; see the fifth below.
#
# CLAUDE.md is the ACCOUNT-LEVEL instruction file -- it governs every repo, it is loaded
# into every session in all of them, and it lives in no git remote. settings.json is the
# account-level harness config. Both are small, hand-authored and single-copy.
#
# ⚠ DELIBERATELY NOT COPIED, and do not "complete the set" by adding them:
# `.credentials.json` is a live auth token and `.claude.json` (81 KB) can carry MCP
# server config with secrets in it. The off-site leg rclone-syncs this whole snapshot
# root to a cloud drive, so anything added here is COPIED TO A THIRD PARTY. That is the
# same failure shape as the 2026-08-15 rclone token leak: the damage is done at copy
# time, and noticing afterwards does not undo it.
EXTERNAL_LEDGERS=(
    "$HOME/.claude-personal/history.jsonl:claude-personal/history.jsonl"
    "$HOME/.claude-personal/CLAUDE.md:claude-personal/CLAUDE.md"
    "$HOME/.claude-personal/settings.json:claude-personal/settings.json"
)

# Out-of-repo DIRECTORIES, as "absolute-source-glob:path-under-the-snapshot".
#
# ⚠ A THIRD ARRAY, and the reason is the same one that created LEDGER_DIRS: the
# EXTERNAL_LEDGERS loop is `[ -f ]`-guarded, so a directory fails that test and is
# skipped SILENTLY. That mechanism has now produced a gap twice; do not "simplify" a
# directory into the file array.
#
# The one entry is every project's memory tree -- the cross-session knowledge base:
# research verdicts, do-not-re-litigate rulings, operator corrections. Measured
# 2026-08-14: 333 files / 2,554 KB across 7 projects, `~/.claude-personal` is not a git
# repository, and nothing under it was in any snapshot except the history.jsonl added
# hours earlier the same day. So the single most valuable artifact on the machine had
# zero copies.
#
# Found by the fpl session over cross-session messaging, which is the point: this repo's
# own commit that morning said the allowlist "was implicitly scoped to the REPO, so
# nothing outside it could ever be found by diffing the backup against the live tree" --
# and then added exactly one out-of-repo file and stopped looking.
#
# Deliberately a GLOB rather than seven named paths. The sister fork's backup is a
# denylist over a wholesale copy for exactly this reason, and after four expansion
# audits here its shape is the one with the better record: a new project's memory tree
# is covered the day it appears, with nobody needing to notice.
#
# THE FIFTH ADDITION (2026-08-19), and it is the paragraph above coming true again:
# "added exactly one out-of-repo file and stopped looking". `~/.claude-personal/tools/`
# holds `budget.py` -- the ONLY instrument for a limit billed per ACCOUNT -- and, far
# more importantly, `budget-history.json`, its durable rollup. That file is the one
# artifact here that a rebuild cannot recover: the tracker is blind to a deleted session
# unless it ran first, so the rollup holds weeks the transcripts no longer cover.
#
# `skills/` and `commands/` are the same class: hand-built account-level skills, in no
# git remote, some expensive to rebuild (`carver-futures` is 32 chapters / 55K words
# distilled from a copyrighted book that is not in any repo and cannot be re-derived
# cheaply). Found while wiring budget.py into the daily check -- i.e. by accident again,
# which is exactly what the glob-over-allowlist rule exists to stop needing.
#
# NOT copied, deliberately, and large-and-regenerable rather than precious: `plugins/`
# (244 MB, reinstallable), `projects/` (213 MB of transcripts -- its `memory` subtree is
# the entry above and IS the valuable part), `file-history/` (180 MB), `context-mode/`
# (71 MB, a re-indexable knowledge base), `books/` (60 MB of re-obtainable source
# material), and the caches. The three trees added here total ~4 MB.
#
# `transcript-archive/` (365 MB as of 2026-08-25) is ALSO not copied, but it does NOT
# belong in that list and calling it "regenerable" was wrong -- ST78. Nothing regenerates
# an archived transcript. It is where `claude-cleanup-conversations.sh` parks retired
# sessions, and `budget.py` scans it to turn a weekly total from a floor into an exact
# sum, so the 2026-08-14 archive-not-delete fix moved the spend record OUT of `projects/`
# and INTO a tree this script already excluded. Nobody chose that; the exclusion predates
# the fix, and every cleanup run grew the uncovered tree.
#
# It stays excluded for two reasons that have nothing to do with regenerability: 365 MB
# against ~4 MB for everything else here, and the off-site leg rclone-syncs this root to
# a cloud drive, so copying it would ship every prompt and response to a third party --
# the same reason `.credentials.json` is excluded above.
#
# What is copied instead is the DERIVED index: `budget.py` maintains
# `tools/budget-session-index.jsonl` (one row per session: week, bucket, session id,
# units -- 32 KB for 202 sessions), refreshed below before `tools/` is copied. That
# preserves what the archive was being kept FOR without preserving the conversations
# themselves.
EXTERNAL_LEDGER_DIRS=(
    "$HOME/.claude-personal/projects/*/memory:claude-personal/projects"
    "$HOME/.claude-personal/tools:claude-personal"
    "$HOME/.claude-personal/skills:claude-personal"
    "$HOME/.claude-personal/commands:claude-personal"
)

# The last two path components identify a matched directory -- `<project-slug>/memory`.
# The basename alone would collapse all seven trees onto one `memory/` and silently keep
# only the last one copied, which is a data-losing bug in a backup script.
_ext_dir_tail() {
    printf '%s/%s' "$(basename "$(dirname "$1")")" "$(basename "$1")"
}

# The venv interpreter is named directly rather than via `poetry run` -- one less
# moving part on the minimal PATH a systemd user unit gets.
PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || PY="python3"

# ST78: the account-level spend tracker. Its derived index is the backed-up stand-in for
# the excluded `transcript-archive/`; see the block above EXTERNAL_LEDGER_DIRS.
BUDGET_PY="$HOME/.claude-personal/tools/budget.py"
# Derived from the tool's own directory, never spelled a second time: the index is written
# by budget.py and copied because it sits inside the already-covered `tools/` tree, so a
# hardcoded second path could drift out of that tree and be silently uncopied.
BUDGET_INDEX="$(dirname "$BUDGET_PY")/budget-session-index.jsonl"

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
    for pattern in "${LEDGERS[@]}"; do
        matched=0
        while IFS= read -r f; do
            matched=$((matched + 1))
            log "  ledger     $f ($(du -h "$REPO/$f" | cut -f1))"
        done < <(_ledger_matches "$pattern")
        [ "$matched" -eq 0 ] && log "  ledger     $pattern -- NO MATCH, will be skipped"
    done
    for d in "${LEDGER_DIRS[@]}"; do
        if [ -d "$REPO/$d" ]; then
            log "  dir        $d ($(du -sh "$REPO/$d" | cut -f1), $(find "$REPO/$d" -type f | wc -l) files)"
        else
            log "  dir        $d -- ABSENT, will be skipped"
        fi
    done
    for spec in "${EXTERNAL_LEDGERS[@]}"; do
        src="${spec%%:*}"; rel="${spec#*:}"
        if [ -f "$src" ]; then
            log "  external   $rel <- $src ($(du -h "$src" | cut -f1))"
        else
            log "  external   $rel -- ABSENT at $src, will be skipped"
        fi
    done
    for spec in "${EXTERNAL_LEDGER_DIRS[@]}"; do
        pattern="${spec%%:*}"; rel="${spec#*:}"
        matched=0
        # Unquoted on purpose -- this is where the glob expands.
        for src in $pattern; do
            [ -d "$src" ] || continue
            # Skip EMPTY trees. Content-based, not a name denylist: every headless
            # `-tmp-*` card run leaves a bare memory/ dir and 66 of those have existed at
            # once, so a name filter would have to guess while this cannot drop anything
            # that holds a byte. -print -quit stops at the first hit.
            [ -n "$(find "$src" -type f -print -quit)" ] || continue
            matched=$((matched + 1))
            log "  ext-dir    $rel/$(_ext_dir_tail "$src") ($(du -sh "$src" | cut -f1), $(find "$src" -type f | wc -l) files)"
        done
        [ "$matched" -eq 0 ] && log "  ext-dir    $rel -- NO MATCH for $pattern, will be skipped"
    done
    # Reported here as well as done in the real path, for the same reason the glob is
    # expanded in both loops: a report that omits a step the copy performs promises
    # different coverage than it delivers.
    if [ -f "$BUDGET_PY" ]; then
        if [ -f "$BUDGET_INDEX" ]; then
            log "  spend-idx  would refresh $BUDGET_INDEX ($(du -h "$BUDGET_INDEX" | cut -f1), $(wc -l < "$BUDGET_INDEX") sessions) before tools/ is copied"
        else
            log "  spend-idx  would CREATE $BUDGET_INDEX before tools/ is copied"
        fi
    else
        log "  spend-idx  budget.py ABSENT at $BUDGET_PY -- index will not be refreshed"
    fi
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
for pattern in "${LEDGERS[@]}"; do
    while IFS= read -r f; do
        mkdir -p "$daily_dir/$(dirname "$f")"
        cp "$REPO/$f" "$daily_dir/$f"
    done < <(_ledger_matches "$pattern")
done

# --- ledger directories -------------------------------------------------------
# `cp -R "$src/."` copies the CONTENTS into an existing dir, so a repeated run
# cannot nest video-notes/video-notes. -p preserves mtimes, which the notes' own
# date-based filenames do not encode (the ingest date is in the name, the edit
# time is not).
for d in "${LEDGER_DIRS[@]}"; do
    if [ -d "$REPO/$d" ]; then
        mkdir -p "$daily_dir/$d"
        cp -Rp "$REPO/$d/." "$daily_dir/$d/"
    fi
done

# --- refresh the derived spend index before tools/ is copied -------------------
# ST78: `tools/budget-session-index.jsonl` is the backed-up stand-in for the excluded
# `transcript-archive/`. Refreshing it HERE rather than trusting the daily check to have
# run is the point -- a backup that copies a stale index has the same did-not-run-looks-
# like-passed shape the index exists to close. Best-effort: budget.py is account-level and
# legitimately absent on another box, and it must never fail a backup whose crown jewels
# are already verified above.
if [ -f "$BUDGET_PY" ]; then
    "$PY" "$BUDGET_PY" --export-index >/dev/null 2>&1 || \
        log "  WARNING budget session index refresh FAILED -- tools/ copy may be stale"
fi

# --- external ledgers ---------------------------------------------------------
# Absent is not fatal: these live outside the repo, so a fresh clone or a different
# box legitimately has none of them, and a missing one must not fail a backup whose
# actual crown jewels are already verified above.
for spec in "${EXTERNAL_LEDGERS[@]}"; do
    src="${spec%%:*}"; rel="${spec#*:}"
    if [ -f "$src" ]; then
        mkdir -p "$daily_dir/_external/$(dirname "$rel")"
        cp -p "$src" "$daily_dir/_external/$rel"
    fi
done

for spec in "${EXTERNAL_LEDGER_DIRS[@]}"; do
    pattern="${spec%%:*}"; rel="${spec#*:}"
    # Unquoted on purpose -- this is where the glob expands. A pattern that matches
    # nothing expands to itself, which the -d test then rejects, so a missing tree is a
    # skip rather than a failure (same contract as the file loop above).
    for src in $pattern; do
        [ -d "$src" ] || continue
        [ -n "$(find "$src" -type f -print -quit)" ] || continue
        dest="$daily_dir/_external/$rel/$(_ext_dir_tail "$src")"
        mkdir -p "$dest"
        cp -Rp "$src/." "$dest/"
    done
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
