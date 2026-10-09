# Steps 10b and 10c — the handoff and the last re-verify

**When:** Read before writing or pruning `docs/plans/next-conversation-prompt.md` at Step 10b, and before Step 10c.

Reference for `.claude/skills/post-branch/SKILL.md`, which holds the run order and the condensed rules. This file holds the full text, including the dated incidents behind each rule. The passages below are carried over verbatim from the pre-split SKILL.md (#884), so where one says "this file", "this skill", "above" or "below" about the phase table or a step, it means SKILL.md.

## 10b — the fresh-conversation handoff

**Precondition — do you OWN the handoff?** This step assumes the running session is
the one that maintains `docs/plans/next-conversation-prompt.md`, which is true solo
and false in a parallel run. If another session owns it, **skip 10b** and instead hand
that session what it needs to fold in: this branch's PR number and state, what it
closes, and any handoff row it makes stale. Say in your final report that 10b was
skipped for ownership and to whom the content went — a skipped step and a forgotten
one look identical next session, which is the whole reason the handoff exists.

Otherwise: offer (don't auto-write) to draft a self-contained prompt the user can
paste into the next conversation. Same shape as `/pr-summary` —
**file-only output, never inline**.

This is the STANDING handoff, and mattpocock's `/handoff` does not replace it: that skill writes a
one-off portable doc to `%TEMP%` for forking a side task, and the operator invokes it.

If the user accepts, write to **`docs/plans/next-conversation-prompt.md`** —
gitignored, but inside the repo and therefore durable. **Not `/tmp`:** the
user deletes conversations, and a handoff that evaporates on reboot defeats
the point. Keep updating that same file rather than starting a new one; it is
a standing document whose whole value is being current, and keeping it so is
a final step of every task, not only of this skill.

**The handoff carries SEQUENCING, never open work** (operator ruling of
2026-10-01, Issue #865). It holds an ordered list of Issue numbers to work, host state, and
standing hazards. A to-do, skill fix, open question, pending decision, "operator
also wants" or "offered, not ruled" item is an Issue (Step 5b) — if you find one
written here, file it and replace it with its Issue number. Lists that lived only
in this file were never filed and went unseen by every Issue query.

**Update it with targeted `Edit`s. NEVER `Write` the whole file.** Its back
half carries standing content — Standing findings and hazards — that the
template below does not reproduce, so a wholesale
overwrite silently destroys it. This is not theoretical: it is why the
"Standing blocks" subsection exists two paragraphs down, and it was
re-confirmed on 2026-08-06j and again on 2026-08-07 when a 449-line prune ran
entirely as `Edit`s and every standing block survived.

**PRUNE it on every task, not only when it gets big** (operator instruction,
2026-08-07). Merged PRs, completed tasks and resolved incidents do NOT belong
here once they land — **move every ✅ item out of the START HERE block into the
month's session log as you write it; the daily check's `handoff done items` line
goes amber while one remains (#905)** — the file is read in full at the start of every session,
so its cost is paid on every conversation. Left alone it reached **1622
lines**, roughly a quarter of it narration of work already merged and recorded
elsewhere. The test is not "old vs new", it is **"does this change what the
next session DOES"**:

- **Delete outright:** merged-PR narration, resolved incidents, superseded
  dated readings (keep the newest only), "kept for provenance" blocks, and any
  closed-task write-up whose verdict already lives in AGENTS.md, a
  `docs/audits/` verdict, or a memory file.
- **Condense to one line + pointer:** a closed task whose VERDICT still binds
  ("do not rebuild X", "do not re-run Y"). The verdict survives; the story of
  reaching it does not.
- **Keep:** the daily operator check, Standing findings and hazards, host state,
  the ordered Issue list.
- **Move to an Issue, then delete:** any skill-fix queue, open question, pending
  decision or task list still written here.

**Read a closed section before deleting it — open items hide inside sections
headed "DONE".** Measured on 2026-08-07: an uncoded `xs_gate_verdict` item sat
inside a block titled "DONE 2026-08-06d. Do NOT redo", and a live card-validity
finding sat inside a closed `/card` task. Deleting on the header alone loses
both. Prune by MOVING to the durable home, never by deleting outright.

**Two structural rules, operator instruction 2026-08-08d. They live here rather than
behind a pointer because a guard rail behind a pointer is not a guard rail:**

- **At most ONE "Just shipped" section, superseded on each merge — never accumulated.**
  Do not add a section per PR. Before writing the new one, move the outgoing one's
  binding verdict to its durable home (AGENTS.md, a `docs/audits/` verdict, or a memory
  file) and delete the rest. The pile had reached **six** such sections before the
  operator asked.
- **Resolve an "Open work" row by DELETING it, never by striking it through.** A struck
  row still costs a read and still reads as state. Move any verdict that still binds
  first, then delete the row. Four struck rows had accumulated by the same date.

**Also check the file's own budget while you are in it** — `daily_check.py` carries a
handoff-size line, and the fix for an over-budget file is to **move the next standing
block to a memory topic file**, not to trim prose. The fat is structural, not narrative.

Structure:

```markdown
# Next conversation — <one-line context>

## DO THIS FIRST — daily operator check (unprompted, every session)

<Carry this block forward VERBATIM, refreshing only the "State at <date>"
line. See "Standing blocks" below — it is not optional and not per-PR.>

## READ FIRST — PR state (snapshot, re-verify before acting)

| PR | Branch | Contents | State at write time |
| --- | --- | --- | --- |
| #<num> | `<branch>` | <one line> | OPEN / MERGED |

**This table is a snapshot, not live state.** First move:
`gh api repos/s10023/buibui-moon-trader-bot/pulls/<num> --jq '{state, merged}'`. If merged, sync main, delete the branch,
and start on a task below — do not re-litigate merged work.

## Just shipped
- PR #<num>: <title> — <one-line outcome / verdict / lift>
- Key finding: <the surprising or load-bearing result, if any>

## State of the world
<2–4 bullets, drawn from MEMORY.md "Current State" + the PR body —
what's live, what's in soft mode, what's still pending. Absolute dates.>

## Reference
- Memory: the tree `tools/memory_dir.py` resolves — `$(PYTHONPATH=. poetry run python tools/memory_dir.py)/MEMORY.md`. ⚠ Write the RESOLVED path into the handoff, never the template string: the config root and the project slug both vary by host.
- <Other docs / tools / branches the next session will need>

## Order of work (Issue numbers — the Issue holds the detail)

1. #<n> — <title>. <one line: why it is first, or what it is blocked on>
2. #<n> — <title>
3. #<n> — <title>
```

### Standing blocks — EDIT IN PLACE, never regenerate

**Do not `Write` this file. Apply targeted `Edit`s to the dated sections** — the
state-at line, the PR table, "just shipped", the task list — and append new
findings. That is the same rule Step 5 states for every other doc, and it is
stated separately here because the template above is *not* the whole file:
anything not in it that a `Write` touches is silently deleted, and this file
carries standing operational content that belongs to the project, not to this
PR.

**Reading the template as a regenerate instruction is the failure mode.** Taken
that way it says: rebuild from a template that omits most of the file, then
manually re-add the rest — which is a step one tired session will skip. Editing
in place makes verbatim preservation the *default* instead of a manual chore.

Blocks that outlive any one PR — refresh only their dated lines:

- **The daily operator check.** `poetry run python docs/plans/daily_check.py`,
  run unprompted every session, reds relayed verbatim. Keep it the FIRST
  section, above the PR table. **Do NOT reinstate the old hand-run commands
  here** (`make buibui-xsmom-daily`, `CATCH_UP=1 make buibui-signal-watch`) —
  both are owned by systemd timers since #579, and hand-running
  `make buibui-signal-watch` is the *looping* form, which races the timer for
  `signal_state.json` and duplicates Telegram. Force a run with
  `systemctl --user start <name>.service`.
- **Standing hazards and host state** — these outlive any one PR. ⚠ The
  skill-fix queue and open questions USED to be standing blocks here; since
  2026-10-01 (#865) they are Issues (`question` label for the latter), so do
  not re-create either block.
- **The lessons corpus** — as of 2026-08-07 this no longer lives in the handoff.
  It grew append-only to 347 lines that reduced to two principles restated
  sixteen times. It now lives in memory, read on trigger:
  `reference_shell_and_tooling_gotchas.md`,
  `feedback_filed_artifact_is_a_hypothesis.md`,
  `project_xsmom_causality_test_vacuous.md`. **File a new finding into whichever
  of those it belongs to, not into the handoff** — and if it is a restatement of
  one already there, add it as a one-line example rather than a new bullet.

This subsection exists because on 2026-08-03 the operator had to ask "why didn't
you remind me to run the xsmom daily?". The reminder was real but lived only in
a dense memory bullet, and once added to the handoff it would have been erased
by the very next run of this skill.

Source the content from:

1. **MEMORY.md "Current State"** — the top bullets are usually the right
   candidates. (There is no "Next focus" section; this said so until
   2026-08-07.) Convert any relative dates to absolute.
2. **This PR's findings** — if the PR closed an option or unblocked one,
   say so plainly so the next session doesn't re-ask.
3. **Open Issues** — the SessionStart digest's p1/p2 queue, plus any
   `question` Issue this PR answered or made actionable. Name Issues by number;
   never restate their bodies here.

Keep it tight: 1–3 Issue numbers in order, not a backlog dump. The goal is a
prompt that costs zero context to bring a fresh session up to speed.

Print only the path + a one-line description. Do **not** echo the
contents.

## 10c — re-verify PR state as the LAST action

This skill writes the handoff *before* the merge, so its most prominent
instruction is the first thing to go stale. On 2026-08-03 all three PRs
(#524, #525, #526) merged within minutes of their handoff being written, and
PR #530 merged the same way — each left the next session with a wrong
opening move. The handoff is the one artifact that survives a session
delete, so a stale first line there is the most expensive kind of stale.

Immediately before you report done — after **every** other step, including
any commit and push — re-query every PR named in the handoff, not just the
one this run created:

```bash
gh api repos/s10023/buibui-moon-trader-bot/pulls/<PR#> --jq '"\(.state) \(.merged_at)"'
```

Then rewrite the state table in place to match. If a PR merged in the
meantime, update the "first move" line too: the next session should be told
to start on a task, not to merge something already merged. If it merged and
the local branch still exists, say so — the branch delete is
`gh pr view` -gated by `[[verify-merge-before-branch-delete]]` and is the
natural first action for the next session.

One API call per PR. That is the whole cost of the difference between a
handoff that opens the next session productively and one that sends it down
a dead path.
