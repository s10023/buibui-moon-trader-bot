#!/usr/bin/env bash
# One signal-watch scan cycle (auto-picks today's config by UTC weekday).
# Invoked by the buibui-signal-watch systemd timer via run-job.sh.
set -euo pipefail
cd "$(dirname "$0")/.."

export DATA_SOURCE="${DATA_SOURCE:-binance}"
poetry run python buibui.py signal watch --once --telegram
