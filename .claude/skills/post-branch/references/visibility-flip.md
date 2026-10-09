# Step 7 — the visibility flip and the text screen

**When:** Read before deciding the visibility flip at Step 7, and again before the flip back after the merge.

Reference for `.claude/skills/post-branch/SKILL.md`, which holds the run order and the condensed rules. This file holds the full text, including the dated incidents behind each rule. The passages below are carried over verbatim from the pre-split SKILL.md (#884), so where one says "this file", "this skill", "above" or "below" about the phase table or a step, it means SKILL.md.

**Gate it on `sensitive-terms` first.** Step 0's sweep carries the leg; read it before
flipping, because the flip publishes the whole history and nothing downstream can take that
back. `NOT CONFIGURED` means the gitignored term list is missing (reclone, fresh machine, **or a
WORKTREE** — a tracked-files-only checkout never receives a gitignored file, so this leg fires
on every worktree run) — restore or copy it in rather than flipping past it. A hit on the branch's own commits is
a STOP: scrubbing in a follow-up commit does not unexpose the blob.

**Then screen the PR title and body, which that leg cannot see.** They are neither the tree
nor a commit, so the sweep reports `clean` on a body naming every term — and a PR body is the
only INDEXABLE one of the four surfaces, served and crawled on its own:

```bash
make post-branch-text FILE=docs/plans/scratch/pr-<branch>.md  # the body /pr-summary wrote
printf '%s' "$TITLE" | make post-branch-text FILE=-             # the title, via stdin
```

It GATES rather than advising. ⚠ Through `make` the exit code is make's own **2**, never the
tool's 1 — read the banner. An unreadable `FILE` is also 2, deliberately: it must not render
as a clean one-check run. Findings print line numbers and a masked term, never the matching
line. Run it on the FINAL text; an edited body does not unpublish the posted one.

Pushing costs no CI; the meter starts at `gh pr create`. So this is the last free
moment, and it is the one decision in the whole skill that must be put to the
user every single time.

⚠ **THE FLIP IS OPERATOR-RUN — you cannot perform it.** `gh repo edit --visibility` is
blocked by the permission classifier, and it is blocked in BOTH directions, so the flip back
in Step 11 is the operator's too. Do not discover this mid-chain: on #669 it surfaced with
the branch already pushed and the PR body already written, costing a round trip. **Order:
finish the PR body FIRST, then hand over the exact command and WAIT for confirmation, then
`gh pr create`** — an unconfirmed flip plus a created PR is a billing-red for nothing.

```bash
# hand this to the operator; they run it with a leading `!` in the prompt
GH_TOKEN=$(gh auth token --user s10023) gh repo edit s10023/buibui-moon-trader-bot \
  --visibility public --accept-visibility-change-consequences
```

On the Windows host the operator's terminal is PowerShell, which rejects the bash prefix.
Hand over this form there (`private` for the flip back):

```powershell
$env:GH_TOKEN = gh auth token --user s10023; gh repo edit s10023/buibui-moon-trader-bot --visibility public --accept-visibility-change-consequences; Remove-Item Env:GH_TOKEN
```

**Confirm it landed before creating the PR** — `gh repo view … --json visibility` is a read
and is not blocked. Never assume the flip happened because you printed the command.

⚠ **Confirm the flip with the user on every occasion.** AGENTS.md > CI quota
makes the **mechanics** standing authorisation and the **timing** not, because
the public window publishes this repo's whole history for its duration and only
the operator knows whether now is a good moment. Ask the unsettled half; never
re-ask the settled one.

- **A docs-only diff skips the flip** — the path-filtered checks execute zero
  steps on a `.md`-only change. Step 1 has already read the diff, so this is
  already known by the time you get here.
- **Step 11 closes the other half of the pair** — the flip BACK, gated on
  `make wait-ci-main`. Do not treat the flip as done when the PR opens.
- ⚠ **`make wait-ci-main` settles on ONE workflow; the flip affects ALL of them.** It gates
  on the `CI` workflow's job-count floor, so a *different* workflow starting after CI settles
  is invisible to it — the same vacuous-check shape as the chained-job defect, one layer up.
  Observed on #669: `Dependency Graph` began at 05:55:45Z, after CI had settled, and was
  `in_progress` at the moment of the flip back. It did not bite there — but **do NOT file this
  as latent.** On #670 the late workflow was `security-scan` (Trivy), which consumes Actions
  minutes, so flipping mid-run kills it and reds main for billing. The risk is *which*
  workflow starts late, never whether the listing was honest.
  ⚠ **And one call does NOT close it — the rule is check → flip → RE-VERIFY.** A listing
  cannot see a workflow that does not yet EXIST: on #670 and again on #672 the pre-flip
  `gh run list` read clean on every workflow, the operator flipped, and `Dependency Graph`
  was created on the merge SHA *after* the check. That is the same vacuous-check shape at a
  THIRD NESTED LAYER (a count of layers, not of sightings) — a chained job does not exist
  until its dependency ends · the waiter watches one workflow and cannot see a sibling · a
  listing of all workflows cannot see one not yet created. ⚠ **It has now recurred on the flip-back
  for #674, #675 and #678 — five sightings**, the last created at 07:07:09Z on `a306410` and
  still `queued` at the moment of the flip; every one was caught by the post-flip re-verify and
  by nothing else — a pre-flip check cannot see a run that does not yet exist, so **the
  re-verify is the ONLY step that catches this class**, never a belt-and-braces extra.
  **A check is only ever true about the scope it looked at, at the moment it looked**, so
  re-run it after the operator confirms the flip:

  ```bash
  GH_TOKEN=$(gh auth token --user s10023) gh api \
    "repos/s10023/buibui-moon-trader-bot/actions/runs?head_sha=<merge-sha>" \
    --jq '.workflow_runs[] | [.name, .status, .conclusion] | @tsv'
  ```

  Never a branch listing (`gh run list --branch main`, REST `?branch=main`) for this: it has returned months-old runs at arbitrary
  moments (ST147; REST form 2026-09-30/10-01), so it can read clean on the wrong commits. Read an empty answer as
  UNVERIFIED, not clean.
