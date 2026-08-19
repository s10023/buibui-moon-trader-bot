# Laptop migration manifest — buibui · wifey · fpl

**This file is committed on purpose.** Everything it describes is gitignored or lives
outside a repo, so a manifest stored *with* that state would die alongside it. It carries
**paths and procedure only — never secrets**; this repo's visibility oscillates
(it goes public when the month's GitHub Actions allowance runs out), so treat it as
world-readable and never paste a key, token or account figure into it.

Covers all three personal repos, because they share credentials, SSH config and the
Claude memory directory:

| repo | remote |
| --- | --- |
| `~/repo/buibui-moon-trader-bot` | `s10023/buibui-moon-trader-bot` |
| `~/repo/buibui-wifey-wall-street-bot` | `s10023/buibui-wifey-wall-street-bot` |
| `~/repo/fpl` | `s10023/fpl` |

> **s10023 repos MUST clone over the `github.com-personal` SSH alias.** A plain
> `github.com` remote hits the work key and fails as "Repository not found" — a
> misleading error that reads like a permissions problem.

---

## 0. Pre-flight on the OLD laptop — do this before copying anything

Nothing below is reversible once the disk is wiped.

```bash
for r in ~/repo/buibui-moon-trader-bot ~/repo/buibui-wifey-wall-street-bot ~/repo/fpl; do
  echo "=== $r"; git -C "$r" status -sb; git -C "$r" stash list
  git -C "$r" log --branches --not --remotes --oneline      # unpushed commits
  git -C "$r" for-each-ref --format='%(refname:short) %(upstream)' refs/heads  # no upstream = local only
done
```

**Stashes are the trap.** They live only on this disk — no remote, no branch, invisible to
`git status`. As of 2026-08-06 buibui held **19**, oldest 2025-07-08, newest 2026-04-14,
all predating the Phase 2 package refactor (2026-05-02) that replaced the very files they
patch (`analytics/signal_lib.py`, `analytics/backtest_lib.py`, …). They would conflict
wholesale. **Decide explicitly — do not let a wipe decide for you.** If unsure, bundle
rather than keep:

```bash
cd ~/repo/buibui-moon-trader-bot
mkdir -p /tmp/stash-archive
for i in $(seq 0 $(( $(git stash list | wc -l) - 1 ))); do
  git stash show -p "stash@{$i}" > "/tmp/stash-archive/stash-$i.patch"
done   # copy the archive across, then drop them on the new box if still unwanted
```

Also stop the live daemon so nothing writes mid-copy:

```bash
systemctl --user disable --now buibui-signal-watch.timer
```

---

## 1. MUST MOVE — irreplaceable, per repo

Not on any remote and not regenerable. Sizes are 2026-08-06 snapshots.

### buibui-moon-trader-bot

| path | why it cannot be rebuilt |
| --- | --- |
| `.env` | API + Telegram credentials |
| `config/coins.json` | the watched symbol set |
| `config/pundit_roster.toml` | hand-curated pundit roster + handles |
| `config/youtube_channels.toml` | channel follow list, handles and `item_cap`s |
| `analytics.db` (~320M) | **the OOS evidence base.** Backtests regenerate; `signal_alert_outcomes` does NOT — it is a live record of candles that have passed |
| `signal_state.json` | alert watermarks. Losing it is not fatal (a cold-start guard restricts a fresh file to the latest candle) but it re-opens dedup |
| `docs/plans/` (~9M) | trade journal, `ai-cards.jsonl`, `pundit-calls.jsonl`, video notes, external-context, `next-conversation-prompt.md`, `daily_check.py`, the ST14 write-up |
| `.claude/settings.json`, `.claude/settings.local.json` | **the hooks** — `guard-destructive.py` and the `/post-branch` backstop. Gitignored, so a reclone silently loses both |
| `.claude/hooks/` | the hook scripts themselves |
| `web/ui/.env.local` | frontend env |
| `~/.gitconfig` + `~/.gitconfig-personal` | **the commit-identity override, and losing it re-opens a live leak.** The global identity is the work address; the `includeIf "hasconfig:remote.*.url:git@github.com-personal:*/**"` block is what keeps it out of this repo's commits. A migrated box without it publishes the work email on the first commit after cloning — into a repo whose visibility oscillates public. Copy both, then **verify with a positive control**, not by reading the file: clone or init a repo with a personal remote and no local config, and check `git config user.email` resolves to the gmail. The `*/**` glob is load-bearing — a bare `**` does not cross the `/` in `owner/repo.git`, so a mistyped pattern installs cleanly and matches nothing |
| `~/.config/systemd/user/buibui-*` + `loginctl enable-linger` | the five timers. Unit files are committed under `deploy/systemd/user/`, but the *installed copies*, their enabled state, and linger are machine state — a migration that skips these leaves every accumulator silently dark, which is the failure mode `daily_check.py` exists to catch |

### buibui-wifey-wall-street-bot

`.env` · `config/stocks.json` · `analytics.db` (~152M) · `signal_state.json` ·
`docs/plans/` · `.claude/settings.json` + `settings.local.json` · `web/ui/.env.local`

Check out the working branch on the new box — as of 2026-08-06 it sits on
`fix/adr-gate-degenerate-on-daily-timeframes`, not `main`.

### fpl

`client_secret.json` (Google OAuth client — **treat as a credential**) ·
`docs/plans/next-session.md` · `.claude/settings.local.json`

---

## 2. MUST MOVE — outside any repo

| path | why |
| --- | --- |
| `~/.claude-personal/` | **Claude's memory for every project.** `MEMORY.md` + topic files are the session-to-session brain; losing it loses every settled verdict and re-opens research already closed |
| `~/binance-history-local/` | ST14 raw Binance exports, 4.2 years. Re-fetching costs rate-limited async-export calls |
| `~/.ssh/` | keys **and** `config` — the `github.com-personal` alias lives here |
| `~/.config/gh/` | `gh` auth for both accounts |
| `~/.zshrc` | shell config |

**No longer must-copy:** `~/.config/systemd/user/buibui-signal-watch.{service,timer}` are
committed at `deploy/systemd/user/` as of 2026-08-06 and arrive with the clone. Copy them
only to preserve a machine-specific edit; otherwise install from the repo (§4 step 5).

The unit sources `.env` via `EnvironmentFile=`, and the healthchecks.io ping URL lives
there as `HEALTHCHECKS_URL_SIGNAL`. Moving `.env` therefore carries the dead-man's-switch
across intact — **do not create a second check** on the new machine, or the old one goes
permanently red and the new one starts with no history.

`~/.claude/` is the **work** Claude CLI config — move it only if the new machine is also
the work machine.

---

## 3. REBUILD — do not copy

`.venv/` (`poetry install --no-root`) · `node_modules/` (`make web-install`) ·
`.cache/` (~502M buibui, ~101M wifey) · `__pycache__/`, `.pytest_cache/`, `.mypy_cache/`,
`.ruff_cache/` · `.idea/` · `web/ui/.vite/` · `.claude/worktrees/`

Copying `.cache/` alone would move ~600M of pure regenerable bulk.

---

## 4. Setup on the NEW laptop

1. Install: Python 3.11+, Poetry, Node, `ffmpeg`, `agent-browser` (npm global). See
   `README.md` and the Dependencies section of `AGENTS.md`.
2. Restore `~/.ssh` (mode `700` dir / `600` keys) and confirm the personal alias:
   `ssh -T git@github.com-personal`.
3. Clone all three over that alias, then drop the gitignored files back in.
4. `poetry install --no-root` in each Python repo.
5. Re-enable the timer. The units ship with the repo, so install them from there first —
   a fresh clone has nothing in `~/.config/systemd/user/` and `enable` would silently
   enable nothing. Then linger, which is what lets it run with no login session
   (**units alone are not enough**):

   ```bash
   cp deploy/systemd/user/buibui-signal-watch.{service,timer} ~/.config/systemd/user/
   sed -i "s#/home/kng#$HOME#g" ~/.config/systemd/user/buibui-signal-watch.service
   systemctl --user daemon-reload
   systemctl --user enable --now buibui-signal-watch.timer
   loginctl enable-linger "$USER"
   ```

   On Windows, WSL2 with `[boot] systemd=true` in `/etc/wsl.conf` runs the identical
   units; add a Task Scheduler entry at logon to start WSL, which does not auto-start.

**Never run signal-watch on two machines at once** — they double-write the ledger and
race `signal_state.json`.

---

## 5. Verify — assert the thing works, not that the file exists

A copied file proves nothing about a working system. Each line below fails loudly.

```bash
poetry run python docs/plans/daily_check.py     # tier 1 clear, exit 0
systemctl --user list-timers buibui-signal-watch.timer   # NEXT populated
make test && make lint-py && make typecheck     # the repo actually runs
GH_TOKEN=$(gh auth token --user s10023) gh api user --jq .login   # -> s10023
```

Then confirm the ledger travelled rather than reset — the row count must match the old
box, not start from zero:

```bash
poetry run python -c "import duckdb;print(duckdb.connect('analytics.db',read_only=True).execute('select count(*),max(fired_at_ms) from signal_alert_outcomes').fetchone())"
```

Record the old box's number **before** wiping. There is no other way to detect a silently
truncated copy.

---

## 6. Leave nothing behind

After verifying on the new machine, on the old one: the three repo trees, `~/.claude-personal`,
`~/binance-history-local`, `~/.ssh`, `~/.config/gh`, and any stash archive under `/tmp`.
`.env`, `client_secret.json` and `~/.ssh` hold live credentials — if the old laptop is
returned to an employer, **rotate the exchange and Telegram credentials rather than
trusting a delete.**

> If the new laptop is also employer-owned, note that this moves personal trading
> credentials onto company hardware. That is a decision to make deliberately, not by
> default — and it is the reason the long-term plan is to move signal-watch off a work
> machine entirely.
