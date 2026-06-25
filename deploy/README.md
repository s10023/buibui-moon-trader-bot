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
