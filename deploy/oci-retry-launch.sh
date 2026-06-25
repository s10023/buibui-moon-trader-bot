#!/usr/bin/env bash
# deploy/oci-retry-launch.sh — keep launching a free OCI Ampere A1 instance until
# capacity frees up. The free A1 shape is heavily contended ("Out of host capacity");
# this loops the launch API across every availability domain until one succeeds, then
# (best-effort) Telegrams you the public IP. Run it under tmux/nohup and walk away.
#
# ── One-time prereqs ────────────────────────────────────────────────────────────────
#   1. Install the OCI CLI:
#        bash -c "$(curl -L https://raw.githubusercontent.com/oracle/oci-cli/master/scripts/install/install.sh)"
#   2. Configure auth:  oci setup config
#        It generates a keypair in ~/.oci/ and asks for your User OCID + Tenancy OCID +
#        region (Console → Profile menu → your user → OCID; → Tenancy → OCID; region e.g.
#        ap-johor-1 / ap-singapore-1). Then paste ~/.oci/oci_api_key_public.pem into
#        Console → Identity → Users → <you> → API Keys → Add API Key → Paste public key.
#        Verify:  oci iam region list   (should print a table, not an auth error).
#   3. Create the VCN + a PUBLIC subnet ONCE (networking has no capacity limit — do it in
#        the console, or it already exists from an earlier attempt). Copy the subnet OCID.
#
# ── Usage ───────────────────────────────────────────────────────────────────────────
#   SUBNET_OCID=ocid1.subnet.oc1... \
#   SSH_KEY=~/.ssh/id_oracle_buibui.pub \
#     bash deploy/oci-retry-launch.sh
#
#   Run unattended:  tmux new -s oci 'SUBNET_OCID=... SSH_KEY=... bash deploy/oci-retry-launch.sh'
#
# ── Optional env ──────────────────────────────────────────────────────────────────────
#   COMPARTMENT_OCID  default: tenancy OCID from ~/.oci/config (root compartment)
#   DISPLAY_NAME      default: buibui-prod
#   OCPUS / MEM_GB    default: 1 / 6   (Always-Free A1 budget is 4 OCPU / 24 GB)
#   BOOT_VOL_GB       default: 50      (Always-Free block storage budget is 200 GB)
#   SLEEP_SECS        default: 60      (pause between full sweeps of all ADs)
#   TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID  success ping (auto-loaded from ./.env if present)
set -euo pipefail
cd "$(dirname "$0")/.."

# best-effort: pick up TELEGRAM_* (and anything else) from the repo .env
if [ -f .env ]; then set -a; . ./.env 2>/dev/null || true; set +a; fi

: "${SUBNET_OCID:?set SUBNET_OCID — the PUBLIC subnet to launch into (create the VCN first)}"
: "${SSH_KEY:?set SSH_KEY — path to your SSH .pub (e.g. ~/.ssh/id_oracle_buibui.pub)}"
SSH_KEY_PATH="${SSH_KEY/#\~/$HOME}"
[ -f "$SSH_KEY_PATH" ] || { echo "SSH pubkey not found: $SSH_KEY_PATH" >&2; exit 1; }

command -v oci >/dev/null 2>&1 || { echo "oci CLI not found — see prereqs at top of this script" >&2; exit 1; }

DISPLAY_NAME="${DISPLAY_NAME:-buibui-prod}"
OCPUS="${OCPUS:-1}"
MEM_GB="${MEM_GB:-6}"
BOOT_VOL_GB="${BOOT_VOL_GB:-50}"
SLEEP_SECS="${SLEEP_SECS:-60}"
SHAPE="VM.Standard.A1.Flex"

# compartment defaults to the tenancy (root compartment) from the CLI config
if [ -z "${COMPARTMENT_OCID:-}" ]; then
  COMPARTMENT_OCID="$(awk -F= '/^tenancy/{gsub(/[[:space:]]/,"",$2); print $2}' ~/.oci/config | head -1)"
fi
: "${COMPARTMENT_OCID:?could not resolve COMPARTMENT_OCID — set it explicitly}"

notify() {  # best-effort Telegram
  [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ] || return 0
  curl -fsS -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    -d chat_id="${TELEGRAM_CHAT_ID}" --data-urlencode "text=$1" >/dev/null 2>&1 || true
}

echo "Resolving latest Ubuntu 24.04 aarch64 image for ${SHAPE}…"
IMAGE_OCID="$(oci compute image list \
  --compartment-id "$COMPARTMENT_OCID" \
  --operating-system "Canonical Ubuntu" \
  --operating-system-version "24.04" \
  --shape "$SHAPE" \
  --sort-by TIMECREATED --sort-order DESC \
  --query 'data[0].id' --raw-output)"
[ -n "${IMAGE_OCID:-}" ] && [ "$IMAGE_OCID" != "null" ] || { echo "no Ubuntu 24.04 image found for $SHAPE" >&2; exit 1; }
echo "  image: $IMAGE_OCID"

# every availability domain — sweeping all of them each cycle maximizes the odds
mapfile -t ADS < <(oci iam availability-domain list \
  --compartment-id "$COMPARTMENT_OCID" --query 'data[].name' --output json \
  | python3 -c 'import sys, json; [print(x) for x in json.load(sys.stdin)]')
[ "${#ADS[@]}" -gt 0 ] || { echo "no availability domains found" >&2; exit 1; }
echo "Availability domains: ${ADS[*]}"

# ssh key → metadata file (python handles JSON escaping safely)
META_FILE="$(mktemp)"
trap 'rm -f "$META_FILE"' EXIT
python3 - "$SSH_KEY_PATH" >"$META_FILE" <<'PY'
import json, sys
print(json.dumps({"ssh_authorized_keys": open(sys.argv[1]).read().strip()}))
PY

echo "Launching ${SHAPE} (${OCPUS} OCPU / ${MEM_GB} GB) as '${DISPLAY_NAME}', retrying until capacity frees up. Ctrl-C to stop."
attempt=0
while true; do
  for AD in "${ADS[@]}"; do
    attempt=$((attempt + 1))
    printf '[%s] attempt %d — AD=%s … ' "$(date '+%Y-%m-%d %H:%M:%S')" "$attempt" "$AD"
    if out="$(oci compute instance launch \
        --compartment-id "$COMPARTMENT_OCID" \
        --availability-domain "$AD" \
        --shape "$SHAPE" \
        --shape-config "{\"ocpus\": ${OCPUS}, \"memoryInGBs\": ${MEM_GB}}" \
        --image-id "$IMAGE_OCID" \
        --subnet-id "$SUBNET_OCID" \
        --assign-public-ip true \
        --display-name "$DISPLAY_NAME" \
        --boot-volume-size-in-gbs "$BOOT_VOL_GB" \
        --metadata "file://${META_FILE}" \
        --wait-for-state RUNNING 2>&1)"; then
      echo "LAUNCHED ✅"
      iid="$(printf '%s' "$out" | python3 -c 'import sys, json; print(json.load(sys.stdin)["data"]["id"])' 2>/dev/null || true)"
      ip=""
      [ -n "$iid" ] && ip="$(oci compute instance list-vnics --instance-id "$iid" --query 'data[0]."public-ip"' --raw-output 2>/dev/null || true)"
      echo "  instance: ${iid:-<see console>}"
      echo "  public IP: ${ip:-<assign a Reserved IP per the runbook>}"
      notify "✅ OCI A1 '${DISPLAY_NAME}' launched after ${attempt} attempts. IP: ${ip:-<console>}. Next: assign a Reserved IP, then ssh ubuntu@${ip:-IP}."
      exit 0
    fi
    if printf '%s' "$out" | grep -qiE 'out of host capacity|out of capacity|capacity|toomanyrequests|429|500|internalerror|service unavailable|503'; then
      echo "no capacity / transient"
    else
      echo "ERROR (non-capacity) — stopping:"
      printf '%s\n' "$out" >&2
      exit 1
    fi
  done
  sleep "$SLEEP_SECS"
done
