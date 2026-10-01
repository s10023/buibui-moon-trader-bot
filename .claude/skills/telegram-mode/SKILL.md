---
name: telegram-mode
description: >
  Toggle Telegram pushes for finished skills: show the mode, turn it on or off,
  add or remove a skill from the opt-in allowlist, or send one test push.
  Invoke when the user says "/telegram-mode", "notify me when /card finishes",
  "stop the Telegram pushes", "which skills push to my phone", or asks to test
  that skill-completion pushes reach the phone.
---

# /telegram-mode — skill-completion pushes to Telegram

A `Stop` hook (`.claude/hooks/telegram-notify.py`) sends ONE short push when a turn
that ran an allowlisted skill ends. This skill only edits the allowlist it reads.
Design and ruling: `docs/superpowers/specs/2026-08-29-st113-telegram-notify-design.md`,
Issue #828 (operator ruling 2026-10-01).

## Commands

Run from the repo root. Each change prints the resulting status line.

```bash
PYTHONPATH=. poetry run python tools/notify_telegram.py mode status
PYTHONPATH=. poetry run python tools/notify_telegram.py mode add card      # also turns the mode on
PYTHONPATH=. poetry run python tools/notify_telegram.py mode remove card
PYTHONPATH=. poetry run python tools/notify_telegram.py mode off           # keeps the list
PYTHONPATH=. poetry run python tools/notify_telegram.py mode on
PYTHONPATH=. poetry run python tools/notify_telegram.py mode test          # one synchronous push
```

Map the user's words onto one of these. With no argument, run `status`. Report the printed
status line back verbatim.

## Rules

- **Opt-in per skill, never "all skills".** The anti-goal is a flooded channel: the
  15-minute signal-watch would send 96 messages a day if it inherited a push. Suggest
  skills whose finish the operator acts on (`card`, `ingest-charts`, `brief`,
  `journal-trade`). Never suggest lint, test or formatting skills.
- **A skill that already owns a Telegram layout keeps it.** `/card --telegram` and the
  daily check push their own message. Adding `card` here sends a short "finished" push
  beside it, not instead of it, so say so if the user adds `card` and already runs
  cards with `TG=1`.
- **Run `mode test` after the first `add`** on a new machine. A silent hook has two
  causes, an empty allowlist or a broken delivery path, and `test` separates them. It
  needs `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env`.
- **The allowlist is gitignored** (`docs/plans/telegram-notify.toml`), so it is
  per-machine and rides the backup's `docs/plans/*` glob. A cloud session has no
  Telegram credentials and no allowlist, so the hook is silent there by design.

## What a push contains

The headline names the skill(s), then the finish time (UTC), elapsed time, git branch and
the first lines of the turn's final message. Every line is at most 46 columns so the phone
never scrolls sideways. One push per turn: a session resumed or compacted re-fires `Stop`
on the same turn, and a dedup marker (`docs/plans/.telegram-notify-state.json`) skips it.

## Known limits

- **Long tasks that are not skills do not push.** That is v2 in the spec.
- **Detection reads the session transcript**, because the `Stop` payload does not name
  the skill. If a harness update changes the transcript shape, the hook goes silent rather
  than pushing wrong. `mode test` still works, so a silent hook with a working `test`
  points at detection.
