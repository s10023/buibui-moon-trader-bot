# Step 9 — the pre-merge readiness check

**When:** Read before Step 9, and before reporting ANY failing check.

Reference for `.claude/skills/post-branch/SKILL.md`, which holds the run order and the condensed rules. This file holds the full text, including the dated incidents behind each rule. The passages below are carried over verbatim from the pre-split SKILL.md (#884), so where one says "this file", "this skill", "above" or "below" about the phase table or a step, it means SKILL.md.

After the doc walk closes, the user usually wants two more things before
moving on: a quick pre-merge readiness check, and a self-contained prompt
they can paste into a fresh conversation when this branch is done. Bake
both in here so the user doesn't have to ask each time.

Prefer the tool over a hand-rolled waiter:

```bash
make wait-ci PR=<n>     # resolves the SHA, prints steps=EXECUTED/DECLARED per job
```

⚠ **`wait_ci.py`'s exit codes do not survive `make`** — GNU make collapses any
recipe failure to its own exit 2, so read the printed banner (3 = Actions
allowance exhausted, `steps=0`, a **billing** failure never a code one; 1 = real
failure; 4 = green but the step counts were unreadable, which is not a pass).

⚠ **Read the EXECUTED half, not the declared one.** A paths-filtered job declares
its full step list on every diff and skips the body, so `steps=5/14` is a docs diff
correctly skipping the heavy leg — while the bare `14` this banner used to print
read as the opposite (ST50(f), measured on #670). Billing is unchanged: an
exhausted allowance declares nothing.

Then run a short status sweep and report any blockers in one line each:

```bash
git status --short                                      # working tree clean?
git log @{u}..HEAD --oneline 2>/dev/null || true        # unpushed commits?
GH_TOKEN=$(gh auth token --user s10023) \
gh api repos/s10023/buibui-moon-trader-bot/pulls/<PR#> --jq '{mergeable, mergeable_state, sha: .head.sha}'
GH_TOKEN=$(gh auth token --user s10023) \
gh api repos/s10023/buibui-moon-trader-bot/pulls/<PR#>/reviews --jq '[.[].state] | last'
GH_TOKEN=$(gh auth token --user s10023) \
gh api repos/s10023/buibui-moon-trader-bot/commits/<sha>/check-runs --paginate \
  --jq '.check_runs[] | {name, conclusion, started_at, completed_at}'
```

**Prefix every `gh` call inline like that, never `export … ;`** — the allowlist matches
a command's first word, so the export form prompts every time while the inline form's
command word is `gh`. It cannot be dropped either: the *active* gh account here is the
work one, so a bare call authenticates as the wrong user. ⚠ **`gh auth switch` is
something to ASK the operator for, never to run yourself.**

Flag, do not fix:

- Uncommitted changes in the working tree
- Local commits not pushed to the PR branch
- `mergeable: false` or `mergeable_state: dirty` (`null` means GitHub is still
  computing it — re-query)
- Failing required checks in `check-runs`
- A latest review state of `CHANGES_REQUESTED`

**⚠ Before reporting ANY failing check, compute its runtime from
`started_at`/`completed_at` — that is why they are in the `--jq` above.**

**The discriminator is the STEP LIST, not a duration.** When the GitHub Actions
allowance is exhausted, every job fails in 2–5 seconds with **zero steps executed**,
which renders identically to a real test failure. Pull the steps and look:

```bash
gh api repos/s10023/buibui-moon-trader-bot/actions/runs/<id>/jobs --jq '.jobs[] | {name, steps: [.steps[] | {name, conclusion}]}'
```

`steps: []` on a **FAILED** job is billing. A populated step list is a real run,
whatever the clock says. ⚠ **`steps: []` on a SKIPPED job is neither** — a failed
`needs:` dependency or a job-level `if:` — and reading it as billing points at a public
flip to debug someone else's failure. ST125, 2026-09-08: `wait_ci.py` did exactly that
on main `b9ce0ef`, where `Regression tests` was skipped because `lint-typecheck-test`
had failed on a timed-out test.

**⚠ Do NOT use a flat "under ~10 seconds never ran" rule — it is wrong in both
directions, and this skill carried it until 2026-08-11.** Trivy died at *exactly* 10s
against a real 22s baseline, so the constant cleared a check that genuinely failed;
and a fast *green* can be legitimate path-filtering (#592: 8s against a 3m45s norm).
The dangerous half is the green one — a fast red gets investigated, a fast green gets
merged. **When you do compare durations, compare a check against ITS OWN normal
runtime, never against a shared constant**: `lint-typecheck-test` runs ~4m18s here,
Regression ~2m41s, Trivy ~22s. Three checks, three different "too fast".

Report it as such — *"4 checks failed in 2–3s each: GHA billing, not code"* — and point at
the standing workaround (flip the repo public for the open-PR window, private again on
merge; confirm with the operator every time). **Never open a debugging session on that
shape.** Observed on #589 and #590; on #590 it rendered as four `FAILURE`s while
`make lint-py` / `typecheck` / `test` / `lint-md` were all green locally.

Output one line per item. If everything is green, say so explicitly:
`pre-merge: clean — ready when you are.`
