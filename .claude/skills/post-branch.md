# Post-Branch Docs Check

Run after every branch is finished (lint/typecheck/tests pass, commit done, PR summary written).
Checks whether `CLAUDE.md`, `README.md`, `MEMORY.md`, `Makefile`, and `docker-compose.yml` need
updates to reflect the branch's changes.

## When to use

After finishing any feature or fix branch. The user should never need to ask — run this
automatically as part of wrapping up a branch.

## Task: run post-branch docs check

1. Get the diff of what changed: `git diff main..HEAD --stat` + `git log main..HEAD --oneline`

2. **CLAUDE.md** — check if the project structure section needs updating:
   - New files added → add to the relevant module list with a one-line description
   - Existing module description is now stale → update it
   - New exports from a lib (`def foo`, `class Bar`) → add to the module's description
   - No structural change → no action

3. **README.md** — check if any user-facing section needs updating:
   - New CLI subcommand or flag → add to the commands section
   - Changed behaviour a user would notice → update the relevant section
   - Internal refactor with no visible change → no action

4. **MEMORY.md** — always update:
   - Set "Last session" to today's date + one-line summary of what changed
   - Mark completed items with `~~strikethrough~~ ✅ Done` in the To-Do table
   - Add any new to-do items surfaced during the work
   - Update "Open questions / pending" if anything was resolved or added

5. **Makefile** — check if a new command needs a `buibui-*` or `docker-*` target:
   - New `buibui.py` subcommand → add `make buibui-<name>` target
   - No new commands → no action

6. **docker-compose.yml** — check if a new long-running daemon needs a service:
   - New daemon → `restart: unless-stopped`
   - New one-shot tool → `profiles: [tools]`
   - No new processes → no action

7. Report what was updated (or confirmed unchanged) for each file. Be explicit — "README: no
   changes needed (internal refactor only)" is useful; silence is not.

## PR Summary

The PR summary must follow the template in `.claude/skills/pr-summary.md` exactly —
read that file before writing. Do not compose from scratch or skip sections.
The template requires: PR Title, Background, Summary, How it works, Params/Config,
Test plan (CI items pre-ticked), Stats, and the Claude Code footer.

## Output format

```
CLAUDE.md    — updated: <what changed> | no change needed: <reason>
README.md    — updated: <what changed> | no change needed: <reason>
MEMORY.md    — updated: current state + A19 marked done
Makefile     — no change needed: no new CLI commands
docker-compose.yml — no change needed: no new processes
PR summary   — written to /tmp/pr-<branch>.md (following pr-summary.md template)
```
