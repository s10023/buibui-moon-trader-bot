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

## Step 0 — Provision the VPS

**Primary: Oracle Cloud Always-Free.** ARM Ampere A1, Ubuntu 24.04, region
`ap-singapore-1` (closest to Malaysia; **home region is permanent — pick Singapore**).
Even 1 OCPU / 6 GB is ample.

- **Reserve a static public IP** (Networking → Reserved Public IPs) — required for the
  Binance API-key IP allowlist in Step 4.
- Known signup friction: the card-verify step rejects many prepaid/local debit cards —
  use a **Visa/Mastercard credit card**. If ARM shows "out of host capacity", retry
  creation, try another availability domain, or fall back to the free **AMD micro** shape.

**Fallback if Oracle blocks you:** Racknerd (~$15/yr) or Hetzner CX22 (~€4/mo), Ubuntu
24.04, Singapore/nearest region. Every step below is identical.

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
| `EXEC_CAPITAL` | optional fixed sizing capital for a testnet A/B (omit for live) |
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
