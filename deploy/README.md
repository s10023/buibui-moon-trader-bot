# buibui VPS deploy kit

Run the bot 24/7 on a hardened always-on box instead of the laptop. Two systemd
timers replace the unreliable GitHub Actions cron:

| Timer | Cadence | Runs |
| --- | --- | --- |
| `buibui-signal-watch` | every 15 min | `signal watch --once --telegram` (Binance-direct) |
| `buibui-xsmom` | daily 00:10 UTC | universe 1d sync → XS executor (`dry_run`→`testnet`→`live`) |

Full design + rationale: `docs/superpowers/specs/2026-06-25-vps-deployment-design.md`.

> **Safety first.** This box will eventually hold live trading keys. The single most
> important control is the **Binance API key config** (Step 4): withdrawals disabled +
> IP-restricted. Do that before ever switching `EXEC_MODE=live`.

## Laptop install (user-scope timers) — the CURRENT live deployment

The VPS is deferred; the automation actually runs today as **systemd user timers on
the laptop**. Those units are committed at `deploy/systemd/user/` — copies of what is
installed, kept in the repo because `~/.config/systemd/user/` is outside every git
tree and a reclone would otherwise lose them silently.

### What runs, and when

| Timer | Fires (UTC) | Fires (MYT) | What it does | Missing a run costs |
| --- | --- | --- | --- | --- |
| `buibui-signal-watch` | `*:01/15` — every 15 min | same | One scan cycle, closed candles, `--catch-up` | **Permanent evidence loss.** The SoT-N8 watermark drags past an un-scanned candle and never revisits it. |
| `buibui-xsmom-daily` | `00:20`, `02:20`, `06:20` | 08:20, 10:20, 14:20 | Universe 1d sync → executor **dry-run** | **Nothing.** The sync is incremental and the executor recomputes; yesterday's bars are still there tomorrow. |
| `buibui-backup` | `07:40`, `12:40` | 15:40, 20:40 | Verified `analytics.db` snapshot + ledgers → `~/backups/buibui` | Nothing *immediately* — but it is the only copy of unreconstructible evidence, so exposure compounds. |
| `buibui-backup-offsite` | `13:25` | 21:25 | `rclone sync` of `~/backups/buibui` → a remote | **Everything, on one hardware event.** Every local copy shares the laptop's disk. |
| `buibui-daily-check` | `09:10` | 17:10 | `daily_check.py --exit-on-tier2` → Telegram on any red | Nothing directly; it is the *notifier* for all of the above. Without it a red waits for a session to notice. |

The asymmetry in that last column is the whole design. signal-watch must be punctual;
the others only need to happen *eventually*, which is why they fire repeatedly and
cheaply rather than once precisely. All carry `Persistent=true`, so a fire missed
while suspended runs **once** on resume — not once per missed slot.

`buibui-daily-check` fires **once** daily, at an hour the operator can act on. That is a
deliberate constraint, not a default: a nudge that arrives while he is asleep is one he
learns to ignore, which is the same failure the tier markers exist to prevent. It passes
`--exit-on-tier2` because tier-2 lines (chart-drops, external-context) do **not** set
exit 1 on their own — without the flag the push would be silent on precisely the
staleness that motivated wanting a nudge. The interactive contract is unchanged: a
hand-run `daily_check.py` still exits 1 only on tier 1.

**It also sets `TELEGRAM_ALWAYS=1` (2026-08-10), so the report arrives every day —
green or red.** `run-job.sh` otherwise pushes only on `rc != 0`, and that contract
("silence = healthy") is **unfalsifiable**: a dead timer and a healthy day are
indistinguishable on the phone, and the delivery path is then exercised for the first
time on the day you most need it working. The flag is **opt-in per job** — set it on a
job a human is meant to read, never on `signal-watch`, which would send 96 messages a
day. To silence the heartbeat again, drop the `Environment=TELEGRAM_ALWAYS=1` line and
`systemctl --user daemon-reload`.

Both push paths HTML-escape the body and wrap it in `<pre>`. That is a **bug fix**: the
sender uses `parse_mode=HTML`, so an unescaped traceback (`line 33, in <module>`) was
rejected with a 400 and the failure alert died on exactly the crashes it exists to
report. `<pre>` additionally preserves the report's column alignment, which Telegram's
proportional font otherwise destroys. Bodies are capped at 3400 chars — Telegram rejects
a message over 4096 outright, so an over-long alert fails exactly like no alert.

Every non-signal-watch schedule avoids `:01/:16/:31/:46`. signal-watch takes an
exclusive DuckDB lock there, and on duckdb 1.5.5 a second **process** is refused even
with `read_only=True` — only reader-vs-reader shares. Overlapping does not corrupt
anything, it just forces the other job onto a slower fallback or a retry.

### Install

```bash
cp deploy/systemd/user/buibui-signal-watch.{service,timer} ~/.config/systemd/user/
cp deploy/systemd/user/buibui-xsmom-daily.{service,timer}  ~/.config/systemd/user/
cp deploy/systemd/user/buibui-backup.{service,timer}        ~/.config/systemd/user/
cp deploy/systemd/user/buibui-daily-check.{service,timer}   ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now buibui-signal-watch.timer buibui-xsmom-daily.timer \
    buibui-backup.timer buibui-daily-check.timer
sudo loginctl enable-linger "$USER"   # keep timers running while logged out
systemctl --user list-timers 'buibui-*'
```

#### Off-machine backup — configure BEFORE enabling

`buibui-backup-offsite` is installed separately because it needs a credential that
cannot be scripted. Its script **exits 1 when `BUIBUI_BACKUP_REMOTE` is unset**, on
purpose: an enabled-but-unconfigured timer must complain daily rather than look green
while protecting nothing.

##### How much remote space — measured 2026-08-08, not estimated

| tier | per snapshot | retention | at steady state |
| --- | --- | --- | --- |
| `daily/` | 259 MiB (`analytics.db`, compacted) | 14 (`BUIBUI_KEEP_DAILY`) | 3.55 GiB |
| `weekly/` | 111 MiB (parquet export) | 8 (`BUIBUI_KEEP_WEEKLY`) | 888 MiB |
| | | | **≈ 4.4 GiB** |

Each tier fills on its own clock, so a fresh install sits far below that for ~2 months
(630 MiB on 2026-08-08, at 2 daily + 1 weekly snapshots). **The total is bounded, not
accumulating** — `prune()` caps the snapshot count and `sync` mirrors those deletions to
the remote. What does creep is the snapshot itself: the DB grew **512 KiB** between the
08-07 and 08-08 snapshots (one day of universe OHLCV + outcomes), so the whole retained
window grows roughly **4 GiB/year**.

**Do not size the remote off `ls -l analytics.db`.** The snapshot is *smaller* than the
live DB — 259 MiB against 320 MiB — because `COPY FROM DATABASE` compacts out free space.

**14 near-identical 259 MiB copies of a DB that changes 512 KiB/day is ~99.8%
redundant.** The local leg already covers the likely failure (fat-finger, bad script,
tool bug); this leg exists for disk loss, which needs the newest good snapshot plus
enough depth to outlast corruption nobody noticed. `BUIBUI_KEEP_DAILY=7` halves the
daily tier to 1.77 GiB (total ~2.7 GiB) and costs only depth that was unlikely to be
used.

##### Provider — Google Drive on a dedicated account (decided 2026-08-08)

15 GB free is the largest tier needing **no payment method at all**, which is the
property that matters for an unattended job growing ~4 GiB/year. Backblaze B2's 10 GB
free tier is real but sits on a *billing* account: cross the line through retention
drift and you are billed rather than blocked. Use an account **separate from the GitHub
identity** so a backup-credential compromise cannot reach personal storage — the same
reasoning as the `~/.gitconfig` remote-keyed identity split.

**⚠ This machine does NOT follow that last sentence, and the deviation is recorded rather
than hidden.** As of 2026-08-15 the remote is the operator's personal 100 GB account
(dedicated-account signup was blocked on Google's phone verification), so isolation comes
from the folder confinement in step 5 plus the two script guards, not from account
separation. What that buys and what it does not: rclone cannot address anything above the
backup folder, but the OAuth token still carries `scope=drive` and would grant full access
to the whole account if it leaked. **Narrowing to `drive.file` is not the fix** — it can
only touch files it created, so a rebuilt config loses the ability to prune what the old
one uploaded, and `sync` must be able to delete for retention to work at all. Switching to
a dedicated account later costs one `rclone config reconnect` and one re-sync.

```bash
# 1. Install rclone
brew install rclone                       # or: sudo dnf install rclone

# 2. Create your OWN OAuth client ID first (~10 min, free). rclone's built-in
#    Google client ID is shared by every rclone user and heavily rate-limited, so
#    the first ~4 GiB push crawls without one.
#      console.cloud.google.com -> new project -> enable "Google Drive API"
#      -> OAuth consent screen: External; add the backup address as a Test user
#      -> Credentials -> Create OAuth client ID -> application type "Desktop app"
#    Keep the client_id + client_secret for step 3.

# 3. rclone config -- OR the non-interactive one-liner, which is fewer prompts
#    to get wrong. EITHER WAY, NOTE THE REDIRECT AND DO NOT DROP IT:
#
#      rclone config create gdrive drive \
#        client_id=<ID> client_secret=<SECRET> scope=drive >/dev/null
#
#    ⚠ `rclone config create` PRINTS THE WHOLE REMOTE ON SUCCESS -- client_secret,
#    access_token and refresh_token -- unprompted, with no warning and no flag
#    needed. That is a live credential on your terminal, one copy-paste from a
#    chat log or a ticket. `>/dev/null` is the control. "Remember not to paste
#    it" is NOT: it failed twice in one day (2026-08-15), the second time within
#    one command of being written down, because the burden was on a human to
#    notice output they did not ask for.
#
#    The interactive wizard prints the same block at the end and cannot be
#    redirected without hiding its prompts -- so if you use it, CLEAR YOUR
#    SCROLLBACK afterwards rather than trusting yourself to scroll past.
#
#    Wizard answers: n(ew) -> name it `gdrive` -> storage `drive`
#      client_id / client_secret : paste from step 2
#      scope                     : 1   (full access)
#      root_folder_id, service_account_file : blank
#      Edit advanced config      : n
#      Use web browser to authenticate : y  (sign in as the BACKUP account)
#      Configure as a Shared Drive     : n
#    Scope 1, not 3: `drive.file` can only touch files rclone itself created, so a
#    recreated config loses the ability to prune what the old one uploaded -- and
#    `sync` must be able to delete in order to mirror retention.

# 4. Prove the remote answers before wiring it in
rclone about gdrive:                      # should print the quota
rclone mkdir gdrive:buibui-backups

# 5. CONFINE the remote to that folder. `sync` mirrors deletions, so this is
#    what stops a mistyped path reaching anything else on the drive. Read the
#    folder id, pin it, then PROVE it -- `lsf` must show the folder's contents
#    (empty on a fresh install), never the drive root.
rclone lsf gdrive: --dirs-only --format ip | grep buibui-backups
rclone config update gdrive root_folder_id=<ID> --non-interactive >/dev/null
rclone lsf gdrive:                         # empty == confined. Assume nothing.

# 6. Wire it in. BOTH of the first two lines matter -- see the trash note below.
#    The path is RELATIVE to the confined root now, so it is `snapshots`, not
#    `buibui-backups` (which would nest a second copy of the name).
echo 'BUIBUI_BACKUP_REMOTE=gdrive:snapshots' >> .env
echo 'BUIBUI_RCLONE_FLAGS=--drive-use-trash=false' >> .env
echo 'BUIBUI_KEEP_DAILY=7' >> .env         # optional; see the redundancy note above

# 7. Dry-run BEFORE the timer exists
./deploy/backup-offsite.sh --dry-run       # must list the snapshot tree, exit 0

# 8. First real sync by hand -- this is the slow one (~2-4 GiB)
./deploy/backup-offsite.sh

# 9. Only now install the timer
cp deploy/systemd/user/buibui-backup-offsite.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now buibui-backup-offsite.timer
systemctl --user list-timers 'buibui-*'    # expect FIVE timers
```

**⚠ `--drive-use-trash=false` is load-bearing on Google Drive, not a tidiness flag.**
rclone's Drive backend defaults to moving deletions to Trash, Trash counts against the
15 GB quota, and Drive only auto-empties it after 30 days. Retention prunes one 259 MiB
snapshot per day, so the default parks up to **~7.6 GiB of dead snapshots** in Trash on
top of ~4.4 GiB live — quietly eating most of the quota while `rclone about` still reads
healthy. The `BUIBUI_RCLONE_FLAGS` hook (`backup-offsite.sh:98`) exists for exactly this
class of provider-specific flag.

It runs `rclone sync`, not `copy`, so remote retention matches local retention instead
of growing forever. The consequence to respect: **`sync` mirrors deletions.** A bug that
emptied `~/backups/buibui` would empty the remote on the next fire, so the script
refuses to run when it finds no `MANIFEST.json` under the backup root — an empty source
is treated as a fault, never as "nothing to do". Do not remove that check.

Deletions mirror into the **destination** just as readily, so two more checks sit beside
it: the script rejects a remote with no path component (a bare `remote:` is the whole
drive), and rejects a destination holding entries the local root does not have. Both are
tracked code and survive a reclone, which `root_folder_id` does not — that is the whole
reason they duplicate its protection rather than trusting it. All three are pinned by
`tests/test_backup_offsite_guards.py`; there is no shellcheck here, so those tests are the
only gate this script has. They were mutation-tested at merge: disabling either guard
fails exactly its own test and nothing else.

⚠ **The destination check compares TOP-LEVEL entries only — do not read it as protection
against another repo.** Measured 2026-08-15: the wifey fork's tree is also `daily/` +
`weekly/`, so a wifey-shaped root aimed at this repo's destination passed the guard and
dry-ran `Skipped delete` over real snapshots. It catches an unrelated folder; it cannot
tell a same-shaped sibling from your own data.

**Two repos on one drive is a confinement problem, not a guard problem.** Give each its own
remote with its own `root_folder_id` — rclone cannot navigate above a pinned root, so the
collision becomes unreachable rather than merely detectable. A sibling *path* under one
shared root does not do this: everything on that remote resolves inside the same confined
folder, so one typo still reaches the other repo's backup. Create the second remote
UNPINNED first (it needs to see the drive root to make its own folder), then pin it — `..`
cannot escape an existing pin, which is the point of one.

Verify a unit parses **before** trusting it — `systemctl start` will happily report a
typo'd directive as a runtime failure, while `verify` names the line:

```bash
systemd-analyze --user verify ~/.config/systemd/user/buibui-backup.timer   # silence == clean
```

**That rule is now a gate, not advice — `tests/test_systemd_units.py` runs in `make test`.**
It stayed prose for months and caught nothing, which is the point: the wifey fork shipped
`OnFailure=` under `[Service]`, where systemd logs *"Unknown key … ignoring"* and starts the
unit anyway, so the alert was inert and `systemctl start` looked perfectly healthy. The test
parses the units itself rather than shelling out to `systemd-analyze`, because that binary is
not guaranteed on a CI runner and a test that skips when its tool is missing is green without
ever having run.

Three checks: no `[Unit]`-only directive under `[Service]` (and vice versa), `OnFailure` must
name a unit that exists, and every hardcoded in-repo path must resolve — **including the ones
in `ExecStart`'s arguments**, since the wrapper form puts the real script after `--`.

They hardcode `/home/kng/repo/buibui-moon-trader-bot` exactly as the VPS units
hardcode `/opt/buibui`; `sed -i "s#/home/kng#$HOME#g"` them on a different machine.
**That rename is what the path check guards** — it is the one edit most likely to leave a
unit that starts cleanly and then fails at runtime.

### Where the logs are, and how to read them

There is **no logfile**. Everything goes to the systemd journal, which handles its own
rotation — deliberately, so nothing grows an unmanaged file on a laptop.

```bash
# what a job actually printed  <- START HERE, this is the useful one
journalctl --user -t buibui-backup -n 50 --no-pager
journalctl --user -t buibui-xsmom-daily -n 50 --no-pager
journalctl --user -t buibui-signal-watch -n 50 --no-pager

# follow live
journalctl --user -t buibui-signal-watch -f

# narrow by time
journalctl --user -t buibui-xsmom-daily --since "today" --no-pager
journalctl --user -t buibui-backup --since "3 days ago" --no-pager

# systemd's own view: did the unit start, succeed, how long did it take
journalctl --user -u buibui-backup.service -n 20 --no-pager
systemctl --user status buibui-backup.service
```

**`-t` (identifier), not `-u` (unit), is the one to reach for**, and the distinction is
load-bearing rather than stylistic. Every `ExecStart` is wrapped in `run-job.sh`, so
journald tags the output with the *executable* name unless `SyslogIdentifier=` overrides
it. Each unit sets one. `-u` shows systemd's lifecycle lines (Starting / Finished /
Failed); `-t` shows what the job printed. When these two disagree, believe `-t`.

This is not theoretical: before `SyslogIdentifier=` was added, `journalctl -u` returned
**zero lines** while 120 lines of real output sat in the journal under a different
identity — and `daily_check.py::_runs_24h`, which counts runs that way, would have read
zero forever.

```bash
# scheduling: when did each last fire, when does it fire next
systemctl --user list-timers 'buibui-*' --no-pager

# was the last run a success? (Result=success / ExecMainStatus=0)
systemctl --user show buibui-xsmom-daily.service -p Result -p ExecMainStatus

# run one right now, out of schedule
systemctl --user start buibui-backup.service

# kill switch, per job
systemctl --user disable --now buibui-xsmom-daily.timer
```

Note `systemctl --user start <name>.service` **blocks until a `Type=oneshot` job
finishes** and exits non-zero if it failed — so it is a real test, not fire-and-forget.

### When something breaks

| Symptom | Where to look | Usual cause |
| --- | --- | --- |
| Telegram says a job FAILED | `journalctl --user -t <id> --since "1 hour ago"` | The message carries only the last 25 log lines; the journal has the rest. |
| Timer shows no `LAST` | `systemctl --user list-timers` | Never fired since install, or linger is off (`loginctl show-user "$USER" -p Linger`). |
| `journalctl -u` is empty but the job clearly ran | use `-t` instead | Missing/renamed `SyslogIdentifier=`. |
| Job fails only on resume from suspend | `run-job.sh` DNS gate | See the resume-from-suspend race below. |
| `IOException ... Conflicting lock` | `systemctl --user list-timers` | Overlapped a signal-watch fire. Reschedule off `:01/:16/:31/:46` — **never** stop the daemon to clear it; it is the OOS ledger writer. |
| Timer shows `NEXT = -` and looks broken | `systemctl --user show <t>.timer -p NextElapseUSecRealtime` | **Usually nothing is wrong.** `list-timers` blanks NEXT while the triggered `Type=oneshot` service is still *running*; the schedule reappears when it finishes. Confirm with `is-active <name>.service` before diagnosing — a running job and a dead timer look identical in that column. |

### The backup job specifically

`deploy/backup-analytics.sh` writes verified snapshots to `~/backups/buibui`
(override with `BUIBUI_BACKUP_ROOT`).

```bash
./deploy/backup-analytics.sh --dry-run     # report only, writes nothing
./deploy/backup-analytics.sh               # daily .db snapshot
./deploy/backup-analytics.sh --weekly      # force the parquet export too
./deploy/backup-analytics.sh --weekly-if-due   # export only if the newest is >=7d old (what the timer uses)
```

| Env | Default | Purpose |
| --- | --- | --- |
| `BUIBUI_BACKUP_ROOT` | `~/backups/buibui` | Destination. Point this at a mounted drive to get the off-machine leg. |
| `BUIBUI_KEEP_DAILY` | `14` | Daily snapshots retained (~258MB each). |
| `BUIBUI_KEEP_WEEKLY` | `8` | Parquet exports retained (~111MB each). |
| `BUIBUI_LOCK_RETRIES` / `BUIBUI_LOCK_SLEEP` | `10` / `30` | How long to wait out the signal-watch lock before the byte-copy fallback. |

Three properties worth knowing before trusting it:

- **It is `COPY FROM DATABASE`, not `cp`.** Measured on the real 320MB file: 4.6s and
  258MB out, versus 0.6s and 320MB for a byte copy. The extra 4s buys a
  transactionally consistent read; the smaller file is free (it repacks dead space).
  Byte copy survives only as the fallback, because it takes no lock.
- **Snapshots are staged and renamed, never written in place.** A crash mid-run leaves
  a `.staging-*` directory, swept on the next run — never a plausible-looking bad
  snapshot at the real path. This was added *because* a laptop crash on 2026-08-07 left
  a 100MB directory that DuckDB opened without error and reported **zero tables**: the
  same failure that makes the committed `live_signal.duckdb` useless as a backup.
- **`MANIFEST.json` is written last.** Its presence is what makes a snapshot
  trustworthy, which is why `daily_check.py` reds on a directory that lacks one rather
  than merely on an old date.

Restoring is a copy — the snapshot is a normal DuckDB file:

```bash
cp ~/backups/buibui/daily/<DATE>/analytics.db ./analytics.db          # from the .db snapshot
# or, format-independent, from parquet:
poetry run python -c "import duckdb; duckdb.connect('analytics.db').execute(\"IMPORT DATABASE '$HOME/backups/buibui/weekly/<DATE>/parquet'\")"
```

Ledgers restore the same way — they sit at their repo-relative paths inside the snapshot.
**`_external/` is the exception and does NOT restore into the repo**: those files came from
outside it and must go back where they came from, or the tool that reads them will not see
them.

```bash
cp ~/backups/buibui/daily/<DATE>/docs/plans/pundit-calls.jsonl docs/plans/     # in-repo ledger
cp ~/backups/buibui/daily/<DATE>/_external/claude-personal/history.jsonl \
   ~/.claude-personal/history.jsonl                                            # OUT of repo

# the cross-session memory trees -- all projects, or one:
cp -Rp ~/backups/buibui/daily/<DATE>/_external/claude-personal/projects/. \
   ~/.claude-personal/projects/

# account-level tooling, skills and instructions (2026-08-19). Note the doubled
# path component: these land under `_external/claude-personal/.claude-personal/`
# because `_ext_dir_tail` always contributes the source's last TWO components.
# Ugly, restorable, and NOT to be "fixed" by collapsing to a basename -- that is
# precisely the collapse the function exists to prevent.
cp -Rp ~/backups/buibui/daily/<DATE>/_external/claude-personal/.claude-personal/. \
   ~/.claude-personal/
cp ~/backups/buibui/daily/<DATE>/_external/claude-personal/CLAUDE.md \
   ~/.claude-personal/CLAUDE.md
```

**`~/.claude-personal/tools/budget-history.json` is the one file here a rebuild cannot
recover.** `budget.py`
is blind to a deleted session unless it ran first, so that rollup holds weeks the
transcripts no longer cover — restore it before running the tracker again, or those weeks
are gone for good.

**The memory trees are the highest-value thing in the snapshot and the easiest to restore
wrongly.** Each lands at `_external/claude-personal/projects/<project-slug>/memory/`, and
the slug matters: restoring one tree into the wrong project silently gives that project
another repo's rulings. Copy the whole `projects/.` as above, or name one slug explicitly —
never `cp` a bare `memory/` directory, which is exactly the collapse the backup script's
`_ext_dir_tail` exists to prevent.

**This is the local leg only.** It defeats accidental deletion, a bad script, or a tool
bug. It does **not** survive disk failure or a lost laptop — that needs an `rclone` of
`$BUIBUI_BACKUP_ROOT` to a remote, which is deliberately a separate step.

How the laptop units differ from the VPS pair, and why (the first two rows are
signal-watch-specific; the rest apply to all three):

| Difference | Why |
| --- | --- |
| `--catch-up` on the `ExecStart` | A laptop *creates* gaps. Without it a suspend costs ledger evidence, not just a late scan. Backfilled candles are persisted but never alerted. |
| `OnCalendar=*:01/15` (not `*:0/15`) | Fires one minute after the 15m close so the exchange has published the closed bar. |
| Explicit `Environment=PATH=...` | User units get a minimal PATH that cannot find linuxbrew's `poetry`. `run-job.sh` needs it on its **failure** path, so without it the alert about a broken run would itself break — and `run-xsmom.sh` needs it for the **job itself**, since that script shells out to `poetry run`. |
| `SyslogIdentifier=buibui-signal-watch` | journald otherwise tags lines with the executable name (`run-job.sh`), losing unit attribution — `journalctl --user -u ...` returns nothing while the lines sit under another identity. |
| **No `After=/Wants=network-online.target`** | Deliberate, not an omission — see below. |

### The resume-from-suspend race (why the network gate lives in `run-job.sh`)

`Persistent=true` runs the fire it missed the **instant** the user manager resumes —
before NetworkManager has re-associated. Measured 2026-08-06: an ~18-second window
in which nothing resolved took out all three network legs at once — the healthchecks
`/start` ping, the job (`rc=1`, DNS `NameResolutionError` on `api.binance.com`), and
the `/fail` ping. The dead-man's-switch saw *no* ping rather than a failed one, and
the operator got a Telegram that read like a broken bot instead of a laptop opening
its lid.

Ordering on `network-online.target` cannot fix it. That target **does not exist in
the systemd user manager**, and even in system scope it is a *boot-time* barrier that
stays active across suspend and never re-arms on resume — so the VPS units carry the
same latent race and are immune only because a VPS never suspends.

So the gate sits in `deploy/run-job.sh`, ahead of the healthchecks pings, where a
unit-level dependency never reached. It waits for the resolver to answer, then runs.
It is bounded and outcome-preserving: a genuine outage still fails exactly as before,
just `NET_WAIT_SECS` later — the gate only ever changes **timing**, never the exit
code, so it cannot mask a real network failure.

| Env | Default | Purpose |
| --- | --- | --- |
| `NET_WAIT_SECS` | `60` | Total wait budget. `0` = probe once, never wait. |
| `NET_WAIT_INTERVAL` | `2` | Seconds between probes. |
| `NET_WAIT_HOSTS` | `api.telegram.org` | Space-separated; the first host to resolve wins. Telegram is the default because it is the one host every job needs — it is the failure-reporting channel. |

## Step 0 — Provision the VPS

**Primary: Oracle Cloud Always-Free**, ARM Ampere A1, Ubuntu 24.04. Region **Malaysia
(Johor)** if offered (lowest latency from MY — Oracle added it recently), else **Singapore
(`ap-singapore-1`)**. The **home region is permanent**, so pick carefully. Even 1 OCPU /
6 GB is ample.

**Signup gotchas:** the card-verify step rejects many prepaid/local debit cards — use a
**Visa/Mastercard credit card** (expect a ~$1 refunded auth hold). Do **not** "Upgrade to
Pay As You Go" — a free account is **suspended, not billed**, if it ever exceeds a limit.

### Create instance — field by field

`☰ Menu → Compute → Instances → Create instance`:

| Field | Setting | Why |
| --- | --- | --- |
| Name | `buibui-prod` | — |
| Placement | default AD-1; **no Fault Domain** ("Let Oracle choose") | A1 capacity is transient — see below |
| Image | **Edit → Change image → Canonical Ubuntu 24.04**, full **`aarch64`** build — **not "Minimal"** | Minimal strips packages we need (deadsnakes PPA, build deps) |
| Shape | **Change shape → Ampere → VM.Standard.A1.Flex → 1 OCPU / 6 GB** (green "Always Free-eligible") | The free ARM box |
| Capacity type | **On-demand** | Not Preemptible (reclaimed) / Reservation (paid) |
| Live migration | **Enabled** if offered | No-downtime host maintenance; `Persistent=true` timers cover a reboot regardless |
| Shielded instance | **Off** | Not our threat model; can cause ARM boot trouble |
| Networking | **Create new VCN → Create new subnet (PUBLIC)** | A private subnet greys out the public-IP option |
| Assign public IPv4 | tick if it un-greys; else assign a Reserved IP after creation (below) | — |
| VNIC name | blank (auto) | — |
| SSH keys | **Upload public key file (.pub)** → your `.pub` (Ctrl+H in the file dialog reveals the hidden `.ssh` folder) | You keep your private key |
| Boot volume | **default (~50 GB) — do not bump** | Stays inside the 200 GB Always-Free limit |
| Initialization script | blank | We provision manually (Step 1) |

The estimated cost (~$2.76/mo for the boot volume) is **wrong for the free tier** — the
calculator "does not reflect any tier unit pricing." Real cost is **$0** (boot vol within
200 GB free, A1 within 4 OCPU / 24 GB free).

**→ Create → wait for RUNNING → copy the Public IP.**

### "Out of capacity for shape VM.Standard.A1.Flex"

The #1 Oracle ARM gotcha — **transient, not a hard block.** In order:

1. **Retry Create** — capacity is released continuously; it often lands within a few tries.
2. Try **AD-2 / AD-3** if the region offers them (single-AD regions like Johor won't), and
   leave the Fault Domain unset.
3. Retry at **off-peak local hours** (early morning) — noticeably better odds.
4. Hands-off: the **auto-retry loop** below hammers the launch API across every AD until
   one frees up (lands unattended, often overnight).
5. Still stuck → **fallback host** (below).

### Auto-retry the A1 launch (free-tier capacity loop)

`deploy/oci-retry-launch.sh` keeps calling the launch API until capacity frees up, then
Telegrams you the IP. Run it under `tmux`/`nohup` and walk away.

**One-time OCI-CLI setup:**

1. Install the CLI:

   ```bash
   bash -c "$(curl -L https://raw.githubusercontent.com/oracle/oci-cli/master/scripts/install/install.sh)"
   ```

2. Configure auth — `oci setup config` generates a keypair in `~/.oci/` and asks for your
   **User OCID** + **Tenancy OCID** + **region** (Console → Profile menu → your user →
   *OCID*; → Tenancy → *OCID*; region e.g. `ap-johor-1` / `ap-singapore-1`). Then paste
   `~/.oci/oci_api_key_public.pem` into Console → **Identity → Users → <you> → API Keys →
   Add API Key → Paste public key**. Verify: `oci iam region list` prints a table (not an
   auth error).
3. Create the **VCN + a PUBLIC subnet** once in the console (networking has no capacity
   limit), and copy the **subnet OCID**.

**Run it:**

```bash
SUBNET_OCID=ocid1.subnet.oc1... \
SSH_KEY=~/.ssh/id_oracle_buibui.pub \
  tmux new -s oci 'bash deploy/oci-retry-launch.sh'
```

The script auto-discovers the latest Ubuntu 24.04 `aarch64` image, sweeps **all**
availability domains each cycle, aborts immediately on a *non-capacity* error (bad config),
and (if `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` are in `.env`) pings you on success. Optional
env: `DISPLAY_NAME`, `OCPUS`, `MEM_GB`, `BOOT_VOL_GB`, `SLEEP_SECS`, `COMPARTMENT_OCID`.

### Assign the static (Reserved) public IP

Required for the Binance key IP-allowlist (Step 4); also makes the IP survive stop/start.

1. Instance → **Resources → Attached VNICs** → primary VNIC.
2. **Resources → IPv4 Addresses** → primary private IP → **⋮ → Edit**.
3. **Public IP type → Reserved public IP → Create new reserved public IP** (`buibui-ip`) →
   **Save**.

If the dialog offers no public-IP option your subnet is **Private** — terminate, recreate,
and force **Create new VCN → Public subnet** (verify under Networking → VCN → Subnets →
"Subnet Access").

### Fallback host (if Oracle A1 won't land)

- **AMD micro** (`VM.Standard.E2.1.Micro`, Always-Free, usually has capacity) — but **1 GB
  RAM**, tight for `poetry install` (pandas/pyarrow/duckdb); add a 2 GB swapfile and expect
  it sluggish.
- **Paid:** Racknerd (~$15/yr) or Hetzner CX22 (~€4/mo), 2+ GB RAM, no capacity lottery.
  **Every step below is identical.**

Then SSH in (default login user `ubuntu`):

```bash
ssh -i ~/.ssh/id_oracle_buibui ubuntu@<PUBLIC_IP>
```

## Step 1 — Box bring-up

```bash
# as root / sudo
adduser --disabled-password --gecos "" buibui
# add YOUR ssh public key:
mkdir -p /home/buibui/.ssh && chmod 700 /home/buibui/.ssh
# paste your key into /home/buibui/.ssh/authorized_keys, then:
chown -R buibui:buibui /home/buibui/.ssh && chmod 600 /home/buibui/.ssh/authorized_keys

apt-get update && apt-get install -y git pipx software-properties-common
# Python 3.13 (repo requires >=3.13,<3.14; Ubuntu 24.04 ships 3.12)
add-apt-repository -y ppa:deadsnakes/ppa
apt-get update && apt-get install -y python3.13 python3.13-venv
```

```bash
# as the buibui user
sudo -iu buibui
pipx install poetry
sudo mkdir -p /opt/buibui && sudo chown buibui:buibui /opt/buibui
git clone <repo-url> /opt/buibui && cd /opt/buibui
poetry env use python3.13
poetry install --no-root
```

Secrets/config (both gitignored — never committed):

```bash
cp .env.example .env && chmod 600 .env      # fill in keys; see vars below
cp config/coins.json.example config/coins.json   # or your real coins.json
```

Seed `analytics.db` once (public mainnet market data, no key needed); the timers keep
it fresh thereafter:

```bash
# universe 1d for the XS book (needs >=288 days of history)
poetry run python buibui.py analytics backfill --universe --timeframes 1d
# coins.json symbols across the live signal timeframes
poetry run python buibui.py analytics backfill --timeframes 15m 1h 4h
```

### `.env` vars this kit adds

| Var | Purpose |
| --- | --- |
| `DATA_SOURCE` | `binance` on the box (not geo-blocked in SG/MY) |
| `EXEC_MODE` | `dry_run` → `testnet` → `live` for the XS timer |
| `EXEC_CAPITAL` | optional fixed sizing capital for a testnet A/B (omit for live). `run-xsmom.sh` passes it as `--capital`, so **every** run of a soak configured this way is capital-pinned — a pinned run is a hypothetical and never moves the stored `peak_equity` high-water mark (it is still *compared* against it, so the drawdown halt still applies). Before that guard existed, one pinned run permanently poisoned the peak; see the `--capital` note in `.claude/context/execution.md` |
| `EXEC_EXTRA_ARGS` | optional executor flags, e.g. `--vol-target 0.10` |
| `BINANCE_TESTNET_API_KEY` / `_SECRET` | Futures **testnet** keys (soak) |
| `HEALTHCHECKS_URL_SIGNAL` / `_XSMOM` | healthchecks.io ping URLs (Step 5) |

## Step 2 — Harden the box

```bash
sudo SERVICE_USER=buibui bash /opt/buibui/deploy/harden.sh
```

Idempotent: installs `unattended-upgrades` + `fail2ban`, sets UFW default-deny inbound
(SSH allowed), and switches SSH to key-only / no-root **only if** your authorized_keys is
already in place (lock-out guard). Optionally add **Tailscale** and close public SSH
entirely (`ufw delete allow OpenSSH` after `tailscale up`) — recommended for a key-bearing
box, not required.

## Step 3 — Install the timers

```bash
sudo cp /opt/buibui/deploy/systemd/buibui-*.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now buibui-signal-watch.timer buibui-xsmom.timer
systemctl list-timers 'buibui-*'      # confirm both are scheduled
```

`Persistent=true` means a job missed during downtime runs at next boot — the property GH
Actions lacked. Logs: `journalctl -u buibui-xsmom -f`.

## Step 4 — Binance API key safety (do BEFORE going live)

On the Binance API-management page, the **mainnet** key used for `EXEC_MODE=live`:

- **Enable Futures** trading only.
- **Disable withdrawals** (leave unchecked).
- **Restrict access to the box's reserved IP** (the static IP from Step 0).

Even a full box compromise then cannot withdraw funds or trade from another IP. Testnet
keys (no real funds) are throwaway and don't need this.

## Step 5 — Monitoring (healthchecks.io)

Create two free checks at <https://healthchecks.io> (period 20 min for signal-watch,
1 day + grace for xsmom), paste their ping URLs into `HEALTHCHECKS_URL_SIGNAL` /
`HEALTHCHECKS_URL_XSMOM`. The wrapper pings success / `/fail` each run and Telegrams the
log tail on failure — so a silently-missed run (or a dead box) alerts you. Leaving a URL
empty disables that check.

## Step 6 — Operational sequencing (gates — do not skip)

1. **`dry_run` (default).** `sudo systemctl start buibui-xsmom.service` → confirm the book
   table prints, the overlay summary is sane, **no orders submitted**, and the healthchecks
   ping + journald log land. Start `buibui-signal-watch.service` → confirm a Telegram alert.
   Let both run unattended ~48 h; reboot once and confirm `Persistent=true` recovers a job.
2. **Testnet soak (30–60 days).** Set `EXEC_MODE=testnet` (+ `EXEC_CAPITAL=<your real
   equity>` for a capital-matched A/B; see below). Success = the daily loop ran unattended
   every day and placed correct **testnet** orders — **not** testnet P&L.
3. **Supervised mainnet flip (later, supervised).** Only after the soak: swap to the
   IP-locked mainnet key, set `EXEC_MODE=live`, add `EXEC_EXTRA_ARGS="--vol-target 0.10"`,
   and run the executor **manually** the first time with the live double-gate:

   ```bash
   BINANCE_ALLOW_LIVE=1 poetry run python tools/xsmom_execute.py \
       --mode live --i-understand-live --vol-target 0.10
   ```

### Capital-matched testnet A/B

Testnet faucets fund ~15k USDT, but position sizing discretization (min-notional, lot
rounding) is capital-dependent — so an unmatched comparison isn't apples-to-apples. Set
`EXEC_CAPITAL` to your real account's equity on testnet so both books size and skip
identically, then compare **% growth** (return-space). Note: testnet fills/slippage differ
from mainnet, so the comparison is indicative, not exact.

## Ops cheatsheet

```bash
# kill-switch (fail-closed: blocks the whole next plan)
poetry run python tools/xsmom_execute.py --mode <mode> --kill
poetry run python tools/xsmom_execute.py --mode <mode> --resume

systemctl list-timers 'buibui-*'         # next fire times
journalctl -u buibui-xsmom -n 100        # last run log
sudo systemctl start buibui-xsmom.service   # run now (off-schedule)
```

## Multi-tenant (wifey, later)

The box is laid out so the US-equities wifey fork slots in with no rework: clone to
`/opt/wifey`, its own venv + `.env` (yfinance is keyless — no trading keys, lower risk),
copy the systemd units with `wifey-` names and a US-session cadence, and add its own
healthchecks. Hardening is shared. A wifey-repo handoff prompt covers the exact steps once
this box is live.
