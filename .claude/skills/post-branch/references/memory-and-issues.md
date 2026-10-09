# Steps 5 and 6 — MEMORY.md and the Issue reconcile

**When:** Read before Steps 5–6 if anything about the MEMORY.md protocol or the Issue tracker looks unfamiliar, and always when the branch lands a `docs/research/` doc.

Reference for `.claude/skills/post-branch/SKILL.md`, which holds the run order and the condensed rules. This file holds the full text, including the dated incidents behind each rule. The passages below are carried over verbatim from the pre-split SKILL.md (#884), so where one says "this file", "this skill", "above" or "below" about the phase table or a step, it means SKILL.md.

## Step 5 — MEMORY.md update

Regardless of the behaviour gate, **always update MEMORY.md's "Current
State"** at the end of every session. This is project policy (CLAUDE.md
"Session Memory Protocol"):

- **Rewrite the "Latest" bullet** to today's date + a one-line summary of what
  changed. "Latest" is **at most 2 lines**; every other Current State bullet is
  exactly 1 line.
- **Current State holds at most 6 bullets.** If yours would be the 7th, first
  roll the oldest bullet **verbatim** into
  `memory/project_session_log_<month>.md`. Prune by MOVING, never by deleting —
  session logs have no size limit; that is what they are for.
- Convert any relative dates ("Thursday") to absolute (`2026-05-01`)
- **Do NOT record open questions or pending decisions here.** File each as an Issue
  labelled `question` (Step 6), and if MEMORY.md still carries such a line, move it to an
  Issue and delete it. MEMORY.md keeps the last-session line plus judgement and verdicts;
  open work lives in Issues only (CLAUDE.md, operator ruling 2026-10-01, #865).

**There is no "Previous session" bullet, and there has not been one for
months.** This step used to say *"Set 'Last session' … move the previous to
'Previous session'"*, which described a protocol MEMORY.md does not implement,
so every run silently worked around it by hand — filed as skill-fix `7h` after
it bit three consecutive sessions. **A step that is wrong every run and correct
never is worse than no step.** The authority is CLAUDE.md's "Session Memory
Protocol"; if the two ever disagree again, CLAUDE.md wins and this text is the
one to fix.

This step runs even when the behaviour gate skipped the user-facing doc
walk, because MEMORY.md tracks **what changed in the session**, not just
behaviour-visible changes.

## Step 6 — Issue reconcile

Planning lives in **GitHub Issues** on this repo since 2026-09-29; the memory SoT
(`project_todo_master.md`) is a pointer stub carrying rulings and an old-id → Issue map, and
takes no status.

**Two questions. First: does this branch close, change, or contradict an open Issue?**
If it closes one, put `Closes #<n>` in the PR body (Step 8) so the merge closes it, and add a
one-line verdict comment. If it only changes or contradicts one, comment on the Issue **now,
in this same session**. A branch that surfaces a NEW to-do, future plan, skill fix, open
question or pending decision files a new Issue — never a memory row, never a handoff list.
Questions and decisions waiting on the operator carry the `question` label. Issues publish with the repo on a visibility flip: redact account figures and screen any
composed body with `make post-branch-text FILE=<path>`.

Cheap way to find the Issue — the old SoT id (e.g. `ST139`) survives in each migrated body:

```bash
GH_TOKEN=$(gh auth token --user s10023) gh issue list -R s10023/buibui-moon-trader-bot \
  --state open --search "$TOPIC in:title,body" --json number,title
```

⚠ **An empty search is not "nothing to reconcile"** when `gh` failed — check the exit code
before trusting a blank answer.

### The second question: did this branch land a `docs/research/` doc that RECOMMENDS work?

If yes, **file its own GitHub Issue naming the filename, in this same session.** A research doc
that recommends a book, a repo, an ingest or a build and files no row has no owner, and
nothing anywhere will ever ask for one.

`tools/docs_index.py` indexes `docs/audits/` and `docs/superpowers/specs/` **only**
(`AUDIT_DIR` / `SPEC_DIR`, `:46-47`), so a `docs/research/` file sits outside the generated
INDEX, outside `TestEveryNewAuditExposesItsVerdict`, and outside `daily_check.py`'s tier-2
`audit verdicts` join. It is a directory no tool reads. Measured 2026-08-17e:
`2026-08-14-trading-canon-audit.md` recommended **three books and a repo** and sat unowned for
three days with every gate green — the reason ST33 was invisible was structural, not judgement.

⚠ **This is prose enforcement, and it is chosen with its weakness in view.** The alternative
was to add `docs/research/` to the docs-index surface, which would be machine-enforced but
would import the verdict-prose rule and the frozen-set machinery into a directory
`AGENTS.md` deliberately exempts — a real cost on every future research doc, to catch a case
that arrives a few times a year. Operator ruling 2026-09-06: take the cheap narrow rule. So
the honest caveat is that ST34 exists *because* prose enforcement already failed here once;
if a second research doc lands unowned, that is the trigger to revisit the machine-enforced
option, not a reason to restate this paragraph.

**Why this step exists, and why it is separate from Step 5.** The session-memory wiring
(CLAUDE.md "Session Memory Protocol", this skill's Step 5, `/sanity-check`, `/backtest-findings`) touches
**MEMORY.md**; nothing else closes planning items. Under the memory SoT that gap let rows drift
for months — the reason planning moved to Issues, where `Closes #n` closes on merge.

**The failure it prevents is misinformation, not clutter.** Found 2026-08-09: N8 still sat as
an **OPEN defect with ~1,900 characters of live diagnosis two days after PR #580 closed it**,
beside a row quoting a `_SCAN_WINDOW=200` that #580 had changed to 600 for 15m. A session
picking up work from it would have re-opened a solved problem and coded against a stale
constant. **An open Issue that is already done is worse than a missing one**, because it reads
as current evidence — verify it against the code or comment that it is unverified, dated.

## MEMORY.md is never committed (Step 7)

**MEMORY.md is never committed.** It lives outside the repo under
`~/.claude-personal/...`, so it is not part of any project commit — save it
via the `Edit` tool only, and never `git add` it. If the MEMORY.md update is
the only thing this step produced, there is simply nothing to commit here;
say so and move on.
