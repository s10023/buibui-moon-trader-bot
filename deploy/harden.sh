#!/usr/bin/env bash
# Optional, idempotent box hardening for an Ubuntu 24.04 VPS that will hold
# trading keys. Run as root once after creating the service user. Re-running is
# safe. The runbook (deploy/README.md) documents the same steps manually.
#
#   sudo SERVICE_USER=buibui bash deploy/harden.sh
#
# GUARD: this refuses to disable SSH password auth unless the service user
# already has an authorized_keys entry — so you cannot lock yourself out.
set -euo pipefail

SERVICE_USER="${SERVICE_USER:-buibui}"

if [ "$(id -u)" -ne 0 ]; then
    echo "must run as root (sudo)" >&2
    exit 1
fi

echo "==> unattended security upgrades"
apt-get update -y
DEBIAN_FRONTEND=noninteractive apt-get install -y \
    unattended-upgrades fail2ban ufw
dpkg-reconfigure -f noninteractive unattended-upgrades || true

echo "==> firewall: default-deny inbound, allow SSH"
ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw --force enable

echo "==> fail2ban"
systemctl enable --now fail2ban

key_file="/home/${SERVICE_USER}/.ssh/authorized_keys"
if [ -s "$key_file" ]; then
    echo "==> sshd: key-only auth, no root login"
    sed -i \
        -e 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' \
        -e 's/^#\?PermitRootLogin.*/PermitRootLogin no/' \
        /etc/ssh/sshd_config
    systemctl reload ssh || systemctl reload sshd || true
else
    echo "!! ${key_file} missing/empty — NOT disabling password auth (lock-out guard)." >&2
    echo "!! Add your SSH public key for ${SERVICE_USER} then re-run." >&2
fi

echo "==> done. Review: ufw status, fail2ban-client status, sshd_config"
