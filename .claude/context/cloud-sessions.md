# Cloud Sessions — what a claude.ai/code container is and is not

A cloud session runs in an ephemeral Linux container that clones this repo fresh. It is **not
the laptop**, and the difference is not a matter of degree: everything that makes the laptop the
operator's machine is gitignored, account-level or host-bound, so none of it arrives. Measured
2026-10-10 in a cloud container. Re-measure rather than trust this table when it matters — the
environment's setup script can change the plugin row at any time (#1016).

`.claude/hooks/open-issues.py` prints a banner pointing here on every session whose environment
sets `CLAUDE_CODE_REMOTE=true`, which is the cloud harness and nothing else.

**A claude.ai Routine fires a cloud session, so this table binds every Routine too.** A cadence
that reads `analytics.db`, `docs/plans/` or the keys (`daily_check.py`, `/decay-review`,
`/db-update`) stays on the laptop's timers; which of the rest move is #1019.

## What is and is not there

| | Laptop | Cloud session |
| --- | --- | --- |
| `analytics.db` | present | **absent** (gitignored, single-copy) |
| `docs/plans/` — handoff, ledgers, journal, `task-marks/`, `daily_check.py`, `scratch/` | present | **absent** (gitignored) |
| Memory tree (`MEMORY.md` + topic files) | present | **absent** — in no git remote |
| `.env` (Binance, Telegram, Groq keys), `config/coins.json`, `config/pundit_roster.toml` | present | **absent** |
| `.claude/sensitive-terms.txt` | present | **absent**, so `make post-branch-text` and the sweep's `sensitive-terms` leg report NOT CONFIGURED — screen by reading instead |
| Account `CLAUDE.md`, skills and plugins (`mattpocock-skills`, book-to-skill skills) | present | **absent** |
| Project plugins enabled in `.claude/settings.json` (`superpowers`, `postiz`) | installed | **not installed** — enabled is not installed |
| Repo skills, hooks, agents, rules (`.claude/`) | yes | yes — tracked |
| Git history | full | **shallow** (50 commits): `analytics.eras` and anything reading old history degrade |
| GitHub | `gh` with the personal token | MCP tools only, scoped to this repo; `gh`'s GraphQL path is refused with a 403 |
| `.venv` / Python | Poetry venv | none until `poetry install`; system Python may differ from 3.11 |
| Binance account | reachable from the allowlisted IP | **unreachable** — the key's IP allowlist excludes the container |
| Timers, daemon, Telegram pushes | the laptop runs them | none |
| Lifetime | persistent | reclaimed after inactivity — commit and push anything worth keeping |

So a cloud session can do work that lives in **tracked files plus the public web**: code with
its tests, docs, audits over committed fixtures, research surveys, issue filing. It cannot run
anything against `analytics.db`, read or write the handoff or memory, touch the exchange, or use
an account-level skill. That boundary is exactly what the `cloud-ok` label asserts
(`docs/agents/issue-tracker.md`).

## Filing Issues from a cloud session

1. **File the Issue; never leave the work as a chat prompt alone.** The tracker is the planning
   surface, and a prompt in chat dies with the container.
2. **Label it `needs-triage`, never `ready-for-agent` or `ready-for-human`.** The role is
   `/triage`'s to grant, and `/triage` is a `mattpocock-skills` skill the container does not
   have. Self-granting it is how #1009–#1015 first went out.
3. **Add `cloud-ok` only when the work fits the table above** — checked, not assumed.
4. **Work that needs the laptop carries a `## Local session prompt` block** in the body: a
   quoted prompt the operator can paste into a local session as-is, naming what to run and when
   to close the Issue.
5. Screen the body by reading it for account figures and employer or client names, since the
   term list is absent. Say in the reply that the list was not available.

A prompt with no Issue is right only for a one-off command the operator runs immediately (a
single `gh api` call whose output they paste back), where an Issue would outlive the need.

## What a cloud session must not stand in for

Do not fake a missing input. A study needing `analytics.db` is not "approximately" run on
fixtures; a handoff not readable is not reconstructed from memory of a previous conversation;
an account skill not installed is not imitated by hand and reported as if it ran. Say what was
missing and file the remainder.
