#!/usr/bin/env bash
# One signal-watch scan cycle (auto-picks today's config by UTC weekday).
# Invoked by the buibui-signal-watch systemd timer via run-job.sh.
set -euo pipefail
cd "$(dirname "$0")/.."

export DATA_SOURCE="${DATA_SOURCE:-binance}"

# --catch-up replays every un-alerted CLOSED candle since the last run, not just
# the newest. Omitting it is defensible on a 24/7 box (there is nothing to catch
# up on) and wrong anywhere the host sleeps: without it a missed cycle loses
# those ledger rows PERMANENTLY, and the loss is DOW-shaped -- a biased sample,
# not merely a thinner one. The live ledger is the OOS evidence base, so that
# bias is the expensive kind. Backfilled candles are persisted + watermarked but
# never sent to Telegram; only the newest closed candle can alert, so this
# cannot produce a burst of stale alerts.
poetry run python buibui.py signal watch --once --catch-up --telegram
