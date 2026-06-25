# 24/7 VPS deployment — signal-watch + XS executor (design)

**Date:** 2026-06-25 · **Status:** approved · **Supersedes:** the memory note
`project_vps_deployment.md` (pre-ChatGPT framing).

## Context

The bot runs manually on the laptop. The one automated piece — signal-watch on a
GitHub Actions cron — **fires unreliably** (the schedule frequently doesn't trigger), and
the user gets no signal that it was missed. We want a single, reliable, **safe** always-on
box that hosts both recurring workloads and removes the GH-Actions dependency. The same box
is the prerequisite for the XS-solo **live mainnet flip** — live Binance keys must never sit
in GH-Actions repo secrets.

## Goals / non-goals

**Goals.** One hardened VPS running (a) signal-watch every 15 min and (b) the XS executor
daily, Binance-direct, with a dead-man's-switch + Telegram failure alerting, sequenced
dry-run → testnet soak → supervised mainnet flip.

**Non-goals (YAGNI).** No web UI / FastAPI service, no live websocket position monitor (both
real workloads are periodic crons; a "3 systemd services + websocket engine" split is
over-built). No Docker/Postgres/Redis. **The mainnet flip is set up but NOT executed here** —
it remains a later supervised slice. This work lands everything up to and including the
testnet soak.

## Decisions

- **Scope:** one box, both crons.
- **Host:** Oracle Cloud **Always-Free** ARM Ampere A1, region `ap-singapore-1`, Ubuntu 24.04
  (user retries signup). Fallback on signup friction: Racknerd (~$15/yr) or Hetzner CX22
  (~€4/mo). Hardening + scheduling are identical for either host.
- **Data source:** `DATA_SOURCE=binance` — a Singapore/Malaysia box is **not** geo-blocked
  (the OKX workaround exists only because US-hosted GH runners get HTTP 451).
- **Scheduling:** **systemd timers, not crontab** — `Persistent=true` re-runs a missed job
  after downtime, journald gives logs + explicit success/failure. Directly answers the
  silent-non-trigger pain.
- **Signal-watch cadence:** **every 15 min** (`OnCalendar=*:0/15`), matched to the fastest
  live timeframe (15m). The GH-Actions hourly cron was a metered-minutes compromise that left
  15m signals up to ~45 min stale; a free box has no per-minute cost. Faster than 15 min adds
  nothing — the same candle re-scans and the cooldown/dedup suppresses it.
- **XS executor cadence:** daily at `00:10 UTC` (just after the daily close); runs the
  universe 1d sync then the executor. Mode (`dry_run`→`testnet`→`live`) is one env var.

## Architecture

```text
systemd timer (15m)  -> run-job.sh signal-watch -> run-signal.sh -> buibui signal watch --once --telegram
systemd timer (daily)-> run-job.sh xsmom        -> run-xsmom.sh  -> analytics sync --universe 1d
                                                                 -> tools/xsmom_execute.py --mode $EXEC_MODE [--capital $EXEC_CAPITAL]
run-job.sh: pings healthchecks.io on success, /fail + Telegram log-tail on non-zero exit
```

- **`deploy/run-job.sh`** — generic wrapper. Args `<label> <HC_ENV_VAR_NAME> -- <cmd...>`.
  Reads the healthchecks URL by indirect env lookup (the *name* is the literal positional, the
  *value* may be empty → handled), runs the command capturing output, pings the dead-man's
  switch on success / `/fail` + a Telegram log-tail on failure, and propagates the exit code.
- **`deploy/run-signal.sh`**, **`deploy/run-xsmom.sh`** — thin workload scripts (set
  `DATA_SOURCE`, `cd` to repo root, run the actual commands). `run-xsmom.sh` reads
  `EXEC_MODE` / `EXEC_CAPITAL` / `EXEC_EXTRA_ARGS` from the `.env` EnvironmentFile.
- **`deploy/systemd/`** — `buibui-signal-watch.{service,timer}` (15 min),
  `buibui-xsmom.{service,timer}` (daily). `Type=oneshot`, `User=buibui`,
  `WorkingDirectory=/opt/buibui`, `EnvironmentFile=/opt/buibui/.env`. A wifey set drops in
  beside them with a different path/user.
- **`deploy/harden.sh`** — optional idempotent box hardening (UFW, fail2ban,
  unattended-upgrades, sshd hardening) with a key-present guard to avoid SSH lock-out.

## Reliability / monitoring

- **Dead-man's-switch: healthchecks.io (free).** Catches the exact "schedule silently didn't
  fire" failure GH Actions has, and works even when the box itself is down (which a from-box
  Telegram ping cannot). One check per job; the wrapper pings on every run.
- **Content alerts via Telegram** on failure (reuses `utils/telegram.py`).
- All runs in journald (`journalctl -u buibui-xsmom`).

## Security ("must be safe") — the load-bearing section

- **Binance API key** (the most important control): Futures trading enabled, **withdrawals
  DISABLED**, **IP-restricted to the box's reserved IP**. A full box compromise then cannot
  withdraw or trade elsewhere. Testnet keys (throwaway) for the soak; a separate IP-locked,
  withdrawal-disabled **mainnet** key only at the flip.
- **Box:** SSH key-only (`PasswordAuthentication no`), root login disabled, `ufw` default-deny
  inbound except SSH, `fail2ban`, `unattended-upgrades`, `.env` `chmod 600`.
- **Tailscale (recommended-optional):** put the box on a tailnet and close public SSH (UFW
  allows 22 only on the tailscale interface). The cheapest large attack-surface cut for a
  key-bearing host; not required for a single box.
- **Kill switch:** `tools/xsmom_execute.py --kill` writes `kill_switch` into the state file;
  the overlay is fail-closed (any breach blocks the whole plan).
- Live keys **never** in git or GH secrets.

## Capital-matched testnet A/B (the one code change)

`tools/xsmom_execute.py` gains `--capital FLOAT` → `run_once(capital_override=…)`: when set,
the book sizes off (and reports) a fixed capital instead of account equity; unset = unchanged
(fetch equity). Run testnet with `--capital` = the real account's equity so **both books size
and skip identically** — min-notional + lot-size discretization is capital-dependent, so the
~15k testnet faucet balance would otherwise trade a fuller book than a ~$2.3k real account and
break the comparison. Compare in **return-space** (% growth), which auto-normalizes the
starting point. Caveat: testnet fills/slippage differ from mainnet (thin liquidity) → the
comparison is **indicative, not exact**. A literal same-dollar compounding curve would need a
small synthetic-equity rebase — deferred.

## Multi-tenant (wifey later, no rework)

Per-project dirs (`/opt/buibui`, `/opt/wifey`), each with its own venv, `.env`, systemd units,
and healthchecks check; hardening is shared. Wifey Phase A is signals-only on free yfinance
data — **no trading keys** → strictly lower-risk than the buibui live executor the box is
already hardened for, so no extra precaution. Wifey itself is a separate repo + spec cycle; the
deliverable is a **handoff prompt written into the wifey repo once the VPS is live**.

## Operational sequencing (gates)

1. Provision + harden box; executor in **`dry_run`**. Verify signal-watch fires correct
   Telegram alerts every 15 min and the daily XS dry-run prints the expected book.
2. **Testnet soak (30–60 days, operational validation).** `EXEC_MODE=testnet` (+ `EXEC_CAPITAL`
   for the A/B). Success = *the daily loop ran unattended every day, synced data, produced a
   valid cross-section, passed the overlay, placed correct testnet orders* — **not** testnet P&L.
3. **Supervised mainnet flip (later slice, NOT here).** IP-locked mainnet key, `--mode live`
   (double-gated by `--i-understand-live` + `BINANCE_ALLOW_LIVE=1`), `--vol-target 0.10`, watch
   first cycles.
4. **GH Actions** signal-watch stays disabled; the YAML is a documented cold-backup.

## Verification

- **Code (`--capital`):** full DoD — `make lint-py typecheck test test-regression`; a unit test
  asserts the override sizes/reports off the fixed capital and that omitting it is unchanged.
- **Deploy artifacts:** `make lint-md`; `shellcheck deploy/*.sh`;
  `systemd-analyze verify deploy/systemd/*.{service,timer}`.
- **On the box (staged):** dry-run prints the book + no orders + healthchecks ping; ~48 h
  unattended shows timers firing (`systemctl list-timers`) and `Persistent=true` recovering a
  job after a reboot; testnet places orders + a forced failure trips `/fail` + Telegram.
