# Cross-repo workflow standard — buibui × wifey, benchmarked against two reference repos

> **Tracked on purpose (moved here from gitignored `docs/plans/scratch/` on 2026-08-19).**
> `/sync-parent` and `/sync-child` scan MERGED PRs, so a gitignored document is invisible to
> the only mechanism the two repos have for carrying work across. This file said what both
> repos should adopt while sitting in the one tier that could never reach the other one.
> `docs/research/` is outside `make docs-index`, so adding a file here needs no index run.
> Owner: SoT **ST44**.
>
> **The two benchmark repos are anonymised as `reference-A` (a large multi-stack service) and
> `reference-B` (a scaffold repo) on purpose — one is a work repository, and a repo that goes
> public during every merge window is no place for its name. Do not "restore" them.**

Date: 2026-08-19. Sources: local file reads only. Peer session
`buibui-wifey-wall-street-bot-ec` supplied the wifey-side self-report; four of its claims
were sampled and reproduced exactly, so the remainder is treated as reliable. Claims it
marked INFERRED are marked here too.

## Verdict

**wifey is better at the shared workflow; buibui is better at the shared machinery. Neither
is better at rules, because both put their rules in a layer that cannot enforce them.**

| Workflow | Better | Margin |
| --- | --- | --- |
| W1 session start | *neither* | both memory-only; buibui has the artifacts, no rule fires them |
| W2 branch-first | tie | prose-only in both |
| W3 pre-PR sweep | **wifey** | decisive — phases in execution order + a mechanical phase 1 |
| W4 `gh pr create` gate | **wifey** | decisive — the flip ask, both directions |
| W5 pre-push gates | tie | same gates; wifey's rationale is better calibrated |
| W6 post-merge | **wifey** | `wait_ci.py` with meaningful exit codes; buibui has no waiter |
| W7 session end | **wifey** | marginal — both broken, wifey's is scoped narrower |
| W8 cadence markers | **buibui** | decisive — `task-marks/` + 3-tier `daily_check.py` |
| W9 fork sync | tie | mirror images, both correctly batched |
| W10 memory growth | **wifey** | a cap with a checker beats a cap without one |

## The diagnosis

The operator named two failure modes. Both are measurable and both have one cause.

### (a) "doesn't follow rules enforced sometimes"

Always-loaded budget, before a single user word:

| File | KB |
| --- | --- |
| buibui `CLAUDE.md` | 55.0 |
| wifey `CLAUDE.md` | 51.1 |
| `~/.claude-personal/CLAUDE.md` | 3.0 |
| buibui `MEMORY.md` | 13.9 |
| wifey `MEMORY.md` | 16.1 |
| **buibui total** | **71.9 KB (~18K tokens)** |
| **wifey total** | **70.2 KB (~17.5K tokens)** |
| *reference-A `CLAUDE.md`, for scale* | *29.6 — a much larger multi-stack service* |
| *reference-B `AGENTS.md`* | *0.9* |

reference-A carries **41% of our always-loaded weight for a far larger system**, because it
pushes repo-specific variance into on-demand `docs/agents/*.md` and enforcement into
pre-commit and CI. We carry ~18K tokens of undifferentiated prose with no severity
hierarchy, so no individual rule has salience.

The peer's own list of prose-only rules found the predictive pattern, and it is better than
"how important is it":

> **Every prose-only rule that gets skipped is a rule about a step that produces no artifact
> when performed correctly.**

Per-repo git identity, branch-first, `make test-regression`, `gh --repo`, writing MEMORY.md
— all invisible when done, all invisible when skipped.

### (b) "alot of roundtrips… keep updating next session prompt"

Root cause, found independently on both sides:

- `~/.claude-personal/CLAUDE.md:53-54` — "updating both is a standing final step of **every
  task**"
- buibui `post-branch/SKILL.md:879-881` — "keeping it so is a final step of **every task**,
  not only of this skill"
- wifey `CLAUDE.md:557` — "At the end of **every session** where anything changed"

The shared account-level file says *task*; the handoff write lives in a per-PR phase. An
N-PR session performs N full handoff rewrites. wifey's live handoff carries the receipt:
`Line count: 191 (prev 181, 191, 190, 191)` — **five stamps**, each a read → `wc -l` → edit
→ re-read → confirm cycle.

A second contradiction compounds it: `/post-branch`'s "confirm every edit" rail versus the
standing "write MEMORY.md **without being asked**" protocol. Resolved toward asking, it
manufactures a round-trip nobody wanted.

### A live instance, caught during this very session

The operator interrupted the port to say: *"always run tests or wait tests/CI in
background."* I had just run `make test` in the foreground.

The rule exists. It is `memory/feedback_background_test_runs.md`, it carries the measured
numbers (`make test` ~295s, `make test-regression` ~93s, ~5 min per branch minimum) and the
hard constraint (never edit the Python tree mid-run — a green run against a tree that no
longer exists is a false "verified"). wifey carries the same rule as
`feedback_run_slow_tests_in_background`.

> **Corrected 2026-08-20 (ST48a).** `make test` read ~145s when this was written and is now
> ~295s — two independent readings on one box, 294.9s and 300.1s over 4205 tests. The figure
> is corrected in place rather than annotated-and-left **because this document is one the
> wifey fork ports from**, so a stale number here propagates to a second repo: it is a live
> claim wearing a dated doc's clothes. ⚠ **`make test-regression`'s ~93s was NOT re-measured
> and must not inherit this correction.**

**It is memory-only in both repos.** `grep -i background CLAUDE.md` in buibui returns two
unrelated lines. So it sits at rung 7 — *below* prose — which means it was never in context
to be skipped from. This is the cleanest possible confirmation of the diagnosis: the rule
was not forgotten, it was never loaded.

Promoted to CLAUDE.md on this branch, with the exception the rule needs or it over-applies:

> **Background anything measured in MINUTES; foreground anything measured in SECONDS.**
> Background `make test`, `make test-regression`, `make wait-ci`, `make wait-ci-main`, any
> CI poll. Foreground `make lint-py`, `make typecheck`, `make lint-md` — their failures
> should stop the next edit. The line is minutes-vs-seconds, **not** tests-vs-not-tests.

Three things the wifey side added, all checked:

1. **wifey's always-loaded file is worse than buibui's silence.** `grep background CLAUDE.md`
   there returns two confident hits — one about verifying subagents, one saying "never write
   a `pgrep` waiter for a background job". Both are *downstream consequences* of
   backgrounding; neither establishes it. A session grepping the topic concludes it is
   covered. **Silence fails open; two adjacent hits fail convincingly.**
2. ⚠ **An allowlist entry silently repeals whatever enforcement a permission prompt was
   providing.** wifey's `settings.local.json` allowlists `Bash(make test *)` and
   `Bash(make test-regression *)`. buibui is worse: `Bash(make test:*)`,
   `Bash(poetry run pytest:*)` and a dozen more pytest variants. So both repos hold the rule
   at rung 6 and *anti*-enforcement at rung 4. Neither of us would have found this by
   grepping for the rule — it only surfaces if you grep for the **command**. Audit
   allowlists against standing rules, not just against risk.
3. **The operator has given this instruction three times across the two repos** — buibui
   today, wifey on 2026-08-05 (`make test`) and again 2026-08-11 (`wait-ci`, after manually
   backgrounding a foreground `gh pr checks` wait mid-turn). **A rule the operator has to
   give three times is not a memory problem, it is a tier problem.**

**Hook design, corrected.** My first suggestion — match the command head — is necessary but
not sufficient, because `run_in_background` is a **Bash tool parameter, not shell text**: a
foreground and a backgrounded `make test` have byte-identical `.tool_input.command`. A
string-only match would fire on every correctly-backgrounded run and train dismissal
(corollary 2 again). The discriminator has to read both fields:

```text
command matches ^\s*(make\s+(test|test-regression)|.*poetry run pytest)
  AND .tool_input.run_in_background != true
```

`PreToolUse`, advisory, anchored at `^\s*` so a heredoc body cannot match. **Neither session
is writing it** — a hook lives in `.claude/settings.json`, which is config, and a peer's
agreement is not the operator's authorisation.

### The meta-pattern: right frame, wrong checkable detail

**Eight** instances surfaced during this exercise, seven of them *inside* work that was
auditing something else:

1. wifey's MEMORY.md cap hook — believed to fire at 19.7KB; **no such hook existed**. Filed
   after reading two of five settings files.
2. `make status` measuring with `du -k` — overstated by up to 4KB.
3. buibui's `#214–#228` window — too narrow; the arc starts at #206.
4. wifey's `--all` commit count — 46 vs a true 40; `--all` counts a squash-merged branch's
   pre-squash commits. Asserted *while correcting instance 3*.
5. wifey's "the operator applied the account-level change themselves" — **it was applied by
   this session, on the operator's approval**. Filed from a file-changed notification, which
   records that a file changed and nothing about who changed it.
6. buibui's "`_is_quoted` has two reachable false-negative paths" — **one is unreachable**;
   `citations()` converts to absolute offsets, so column 0 cannot occur. Filed while
   *correcting* instance 5's class.
7. buibui's "I recognised the short-SHA trap because your docstring named it, in code I had
   ported" — **the text is in neither file**; `grep -i 'short sha' tools/wait_ci.py` and the
   local `post-branch/SKILL.md` both return nothing. The scar never travelled. The cause was
   inferred from first principles and a **provenance confabulated for it afterwards**. Filed
   inside the message arguing that recorded scars prevent repeat mistakes.
8. wifey's "the scar was in the skill, so the prose travelled and prevented the mistake" —
   built on instance 7 without checking it. It verified **where the text lived in its own
   repo** and treated that as evidence about **what happened in the other**. The falsifier was
   one `grep` on a directory readable on the same machine, and it was never run until the
   answer arrived.

Every one had a **correct frame and a wrong checkable detail**. Instances 3, 4, 6 and 8 form
a **four-link chain — each was filed while correcting or building on its predecessor**, across
two independent sessions, under sustained elevated attention, with the pattern explicitly
named and actively under discussion. **If "be more careful" were the fix, it would have
worked by instance 4.** It is not converging, and that is the result. Instance 5 widens the class
usefully: the detail need not be a number — it can be a date or an **attribution**, and
attribution is the more dangerous member, because *a number invites re-derivation and a
provenance claim invites none.* Nobody re-runs "who did this."

Three distinct sub-mechanisms, escalating:

- **1, 5 — reading a source that cannot answer the question**, and taking its silence as
  confirmation.
- **7 — attributing a conclusion to a source retroactively.** No source was read at all. *A
  remembered provenance is not a checked one*, and it is the least likely to be re-derived,
  because it never presents as a claim — it presents as recall.
- **8 — declining to check a source you have, because the claim is one you want to be true.**
  Instance 7 arrived wrapped in a compliment to wifey's practice, and wifey upgraded it rather
  than auditing it. **A claim that flatters your own prior work is the one you check least.**
  This is a *motivational* failure, not an informational one, and "re-derive mechanically"
  does not help if you never open the question.

The shared mechanism across the first group, in the peer's words: **consulting a source that
is silent on the thing you are asserting, and reading its silence as confirmation.** Two of the five are literally
that — a settings scan that skipped three files, and a change notification that carries no
actor. It is "a SKIP is not a PASS" one layer up:

> **The absence of a contradicting signal is not a signal.**

And the actionable half: **the correction rate did not drop when the author knew they were
being audited.** That rules out "be more careful" as the fix and points at mechanical
re-derivation.

### `stale_anchors` — six defects, and how each was found

The leg with no substitute is also the buggiest. All six are latent in wifey's copy and
unfixed in both, pending a change with tests:

| # | Defect | Direction | Found by |
| --- | --- | --- | --- |
| 1 | Proximity binding — `§N` binds to the nearest `.md` on the line, ignoring what the prose names | false **positive** | triage |
| 2 | Ordered-list anchors harvested only when a file declares no headings | false **positive** | triage |
| 3 | Cross-repo qualifier ignored — "wifey's `/post-branch` Step 5c" resolves locally | false **positive** | triage |
| 4 | `_is_quoted` sees only adjacent characters, so a quoted *phrase* reads as a use | false **positive** | reading code |
| 5 | `_QUOTES` is a `str`, so `in` is a substring test and **`"" in _QUOTES` is `True`** — an anchor ending a line, preceded by a quote, is silently suppressed | false **NEGATIVE** | reading code |
| 6 | `handoff-size` returns `[]` on a regex miss, so deleting the stamp makes it vacuously green | false **NEGATIVE** | reading code |

**The split is the finding.** Every false positive surfaced by *triaging findings*; both
false negatives surfaced only by *reading the code*.

> **A finding-triage loop cannot find a suppression bug**, because by construction it
> produces no findings to triage. **Every check needs its suppression paths enumerated
> separately from its findings — the two are found by different activities.**

Third instance of the same family as "a SKIP is not a PASS" and "the absence of a
contradicting signal is not a signal" — and the direction matters, because on this leg a
false positive costs a glance while a false negative ships a dead citation nothing else can
see. Measured FP rate here was **4 of 7**; wifey's copy reports 0 findings, which means
untested, not clean.

⚠ **#5 is ONE reachable path, not two.** `_is_quoted` also returns `True` for an anchor at
column 0, but `citations()` calls `_anchor_after(line, t.end())` and converts to absolute
offsets (`begin = start + m.start()`), so `begin >= t.end() > 0` — column 0 is unreachable and
the `if begin else ""` guard is dead code on the live path. (Narrowed by the peer after I
filed it as two; a correct frame carrying a wrong count, for the sixth time today.)

⚠ **The reachable path is not an odd corner — it targets our own prose.** It fires when the
character before the anchor is a quote *and* the anchor ends the line: the exact signature of
a **quoted phrase wrapped across a markdown line break**, which is what long handoff and
memory paragraphs are full of — and those are the documents this leg exists to sweep. Note it
is the *same construction* that produced false positive #4: one bug suppresses the mention,
the other reports it, and which you get depends on where the line happens to wrap. Neither is
reachable by triage from the other's direction.

⚠ **Do not fix #2 by widening the harvest.** The module's own comment records why: buibui's
`/post-branch` carries three column-0 rubric lists numbered 1..5, which would silently
validate every dead `Step N` citation — the precise defect the leg exists to catch. Fixes #4
and #5 are the same rewrite (test for a quoted *span*, stop indexing single characters); the
one-character `frozenset` version fixes only #5 and is **a partial that looks like a fix**,
which is worse than nothing here because it would close the ticket. Any fix needs a positive
control that the suppression path *can* fire, or a silent false negative is replaced by a
silently untested one.

### Checkers fire on their own documentation

Three self-referential firings in one session: the `gh pr create` hook on a heredoc
documenting the heredoc bug; `stale-anchors` on the sentence describing defect #3; and again
while writing the fix note for it.

> **A checker that matches on surface text will always fire on its own documentation,
> because documentation of a defect necessarily contains the defect's signature.**

Assess every new check against *"what happens when someone writes this check up?"* — a cheap
question that caught three things here. And when it does fire on a write-up: **leave the
finding standing, labelled with its cause.** A suppressed false positive and a fixed one are
indistinguishable next time, which is how the original 16 shell blocks rotted.

### Two boundaries the last exchange established

**Structural immunity protects the code, never the operator at a prompt.** `wait_ci.py` cannot
hold a short SHA — every query path sources a full one from the API, and `sha[:8]` appears only
inside `print()`. That immunity crossed the repo boundary intact and *still did not help*,
because the query that got it wrong was typed by hand at a terminal, outside the code path
entirely. This is the failure mode of every "we made it impossible to get wrong" claim: the
guarantee ends at the edge of the code that carries it.

**The durable artifact was protected by a process; the transient one was not.** Instance 7
never reached either handoff — it lived only in peer messages. Not through judgement: the
handoff gets verified content *because writing it runs a mechanical sweep*, and a message gets
the unverified flourish *because nothing checks a message*. So: **treat a peer message as a
draft, not as a finding** — it is the only surface in this workflow with no gate on it at all.
The tier argument, one more time, on the surface nobody thought to tier.

### The single cause behind both

Rules sit at the wrong rung. Ranked by what survives:

| Rung | Survives reclone | Fires without being remembered | Our use |
| --- | --- | --- | --- |
| 1 CI | ✅ | ✅ | thin — wifey has 1 doc-drift gate, buibui 0 |
| 2 pre-commit | ✅ | ✅ | near-identical, thinner than reference-B |
| 3 Makefile + checker script | ✅ | ❌ must be invoked | **wifey only** |
| 4 Claude Code hook | ❌ **gitignored in both** | ✅ | **buibui only** |
| 5 skill | ✅ | ❌ must be invoked | both, 8–89% drifted |
| 6 always-loaded prose | ✅ | ✅ read, ❌ obeyed | **both, overloaded** |
| 7 memory | ❌ | ❌ | both, buibui 214 files vs wifey 72 |

Every workflow rule we own lives at rungs 4–7. Rung 4 dies on a reclone. Rungs 5–7 need
someone to remember.

## The standard

> **Every rule lives at the lowest rung that can carry it. A skill holds only what a script
> cannot decide. Prose holds only what no script can be written for.**

wifey already stated it, in the file that proves it —
`sanity-check/SKILL.md:17`: *"Everything mechanical is now code. This file holds what a
script cannot decide."* And `CLAUDE.md:507`: *"A skill that answers each new defect with
more prose accumulates defects."*

Four corollaries earned by measurement:

1. **A guard behind a path filter is absent exactly on the diffs it guards.** wifey's
   `lint.yaml:58-65` puts `sanity_checks.py` unconditionally in the *markdownlint* job,
   because the pytest job sits behind `**/*.py` and a docs-only PR is precisely what
   doc-drift checks exist for. Audit all CI for this shape.
2. **A check that is never clean trains dismissal**, the mirror of one that is never green.
   wifey's `negative-claims` reported the same 7 hits on every branch until it was scoped to
   the diff's *added* lines; its sweep went 9 → 2, both survivors true.
3. **A waiter must never turn a read failure into data.** `wait_ci.py` exit **4** exists
   because an earlier version printed "all green" while having failed to read the step
   counts.
4. **A missing marker must read as OVERDUE, never as fresh** (buibui `task-marks/README.md`).

### Scope rule that dissolves the round-trips

> **Confirm edits to git-tracked surfaces AND to untracked single-copy data. Write untracked
> *session state* — the handoff and the memory index, and only those two — unprompted.**

A wrong edit to `CLAUDE.md` ships in a PR. A wrong edit to `MEMORY.md` or the gitignored
handoff is a note the next session rewrites. This resolves wifey's open item 10 and removes
2 of the 4–8 asks per PR.

⚠ **The predicate must name the two surfaces, not the untracked directory class.**
`git ls-files docs/plans/` returns **0 in both repos**, and that tree holds
`pundit-calls.jsonl`, `ai-cards.jsonl`, `journal/`, `routed-ledger.json`, `thesis-inbox.md`
and the `parent-sync/` rulings — single-copy research output that nothing rewrites next
session and that `git clean -xdf` deletes without a prompt. A directory-scoped rule would
read as permission to append to the pundit ledger unasked, which is the one untracked write
that genuinely needs a human. (Caught by the peer against my wider first draft.)

### W7 fix

Split the write by who knows the fact, and separate durability from presentation:

- **Per PR (phase 6):** one targeted `Edit` appending the single new fact — PR number and
  state. No re-read, no `wc -l`, no prune, no confirm.
- **Once at session end:** prune, re-verify, MEMORY.md Current State. Unprompted.
- **Delete the stamp trail as a concept.** `(prev 181, 191, 190, 191)` costs a full
  read/measure/edit/re-read cycle and nobody has acted on it. `handoff-size` then exists to
  verify a number whose only purpose is being verified — cut both; measure the file directly
  if a budget is still wanted.

⚠ **Delete the stamp and the `handoff-size` leg in the SAME change — never the stamp alone.**
Verified in `tools/post_branch_checks.py::_check_handoff_size`: the leg is
`if m and int(m.group(1)) != lines`, and on a regex miss it `return []`. So removing the
stamp does not turn the leg red — it makes it **vacuously green forever**. That is the exact
mirror of the `negative-claims` defect (corollary 2): a check that is never clean trains
dismissal; a check that *can never fire* is dismissal, silently. The leg carries no
threshold, so no size budget survives it — a real one has to be written fresh against
`wc -l`.

⚠ Do **not** simply change "every task" → "every session". A session can be killed at any
moment, which is why the handoff is durable and in-repo. The invariant is **"never more than
one cheap append behind reality."**

## Port list

### To buibui, from wifey — ranked

1. **`tools/post_branch_checks.py` + `make post-branch-checks`** (641 lines, 11 legs:
   `queue-items · handoff-symbols · new-files · new-modules · new-targets · negative-claims ·
   doc-indexes · md-atx · memory-cap · handoff-size · stale-anchors`). These were 16 shell
   blocks inside the skill until 2026-08-18; two had shipped broken. Copy **byte-for-byte**,
   then adapt paths — re-implementation is how the repos drifted while both believed they
   were synced.
2. **`tools/sanity_checks.py` + `make sanity-checks` + the CI step.** Note the placement
   rule in corollary 1.
3. **The visibility-flip ask** — the operator's named example. The transferable design is
   **mechanics = standing authorisation, timing = per-occasion consent**; that is why it
   reads as helpful rather than nagging. Copy **both** halves: flip-FORWARD in phase 5,
   flip-BACK gated in phase 6. buibui's `CLAUDE.md` has **no flip rule at all** (grep clean);
   `post-branch:862` mentions it once in passing as "the standing workaround".
4. **Restructure `post-branch` from 10 steps to 6 phases in execution order**, with the
   "Costs CI?" column. buibui's numbering does not match its run order (1–5, 7 pre-PR; 6,
   10a/c post) — the skill says so itself. wifey's carries the warning that earned it:
   *"Watch for a phase whose output depends on a fact a LATER phase creates. Three found so
   far."*
5. **`tools/wait_ci.py`** with its exit-code taxonomy (3 = Actions-allowance `steps=0`,
   1 = genuine failure, 4 = green but counts unreadable). ⚠ Through `make` you see none of
   these — GNU make collapses recipe failure to exit 2.
6. **`make status`** — repo shape including MEMORY.md KB and Current State bullet count.
   Use `wc -c`, never `du -k`: disk blocks overstate by up to 4KB, worst on the smallest
   file, in the direction that causes needless rolling.

### To wifey, from buibui

1. **`docs/plans/task-marks/`** — one file per periodic task holding an ISO-8601 UTC stamp,
   written by each skill as its final step. Strictly better than generalising a dated report
   artifact, because it decouples *the record that a run happened* from *the artifact a run
   produces*: `/sanity-check` produces no report, which is exactly why wifey's "weekly" is
   unfalsifiable.
2. **`daily_check.py`'s three-tier taxonomy** — tier 1 data integrity (exit 1), tier 2
   freshness (`--exit-on-tier2`), tier 3 info. Plus: overdue markers warn and never set an
   exit code.
3. **`!.claude/agents` in `.gitignore`** — wifey lacks it, so the day it adds a subagent
   definition the file dies silently on clone. Two-line change, do it before it is needed.
4. **The `context-guard.py` + `context-map.json` pattern** — 11 glob→rule cards delivering a
   footgun card at the moment a guarded file is edited, with `test_context_guard.py`
   (31 cases). This is what let buibui shed rules from the always-loaded tier.

### Both repos

**Commit the enforcement layer.** Both `.gitignore` files are `.claude/*` with narrow
negations, so `settings.json` and every hook are untracked and die on reclone — buibui's
four hooks and wifey's two. Both repos' remedy is a prose note asking a human to remember;
a human remembering is the enforcement layer for the enforcement layer. `reference-B`
inverts it and says so:

> "The vendored skill trees … and the shared `.claude/settings.json` are deliberately
> committed. **Do not ignore them.**"

It ignores only `.claude/*.local.*`, `.claude/todos/`, `.claude/worktrees/`.

⚠ Check for secrets in `settings.local.json` before tracking anything.

**Fix the `gh pr create` hook false positive in BOTH repos.** It matches the whole command
string, so a heredoc merely *containing* the text fires it. **Three live instances during
this session** — two here while writing scratch files, and one on the wifey side that fired
*while the peer was writing this very finding into its handoff*, matching its own sentence
describing the bug. No PR existed in any of the three.

Three things that only the live reproductions show:

- **It is self-triggering on its own documentation.** Any handoff, memory file, audit or
  standard that *quotes* the trigger string fires the advisory — so the surfaces most likely
  to trip it are exactly the ones written while fixing it. That is a feedback loop, not a
  false-positive rate.
- **The fix is head-anchoring, not smarter matching.** `^\s*` kills the whole class outright,
  because a heredoc body is never at the command head. No context allowlist is needed.
- ⚠ **Both repos' `CLAUDE.md` document the heredoc footgun explicitly, and both hooks
  shipped with it anyway.** *Prose describing a footgun does not prevent code from having
  it* — even in the same repo, even where the same session can quote the sentence. This is
  failure mode (a) at **rung 4**, which the tier model did not predict: a mechanical layer
  is only as good as the match it encodes.

Apply the same fix before writing any new command-matching hook, so the defect is not
inherited by the background-gates hook above.

## Skill drift — the measurement behind "choppy and inconsistent"

24 skills shared by name. Drift, as differing lines over combined length:

| Skill | Drift | | Skill | Drift |
| --- | --- | --- | --- | --- |
| **post-branch** | **89%** | | recalibrate | 34% |
| **sanity-check** | **84%** | | volume-sweep | 32% |
| ingest-x | 77% | | pr-summary | 29% |
| journal-trade | 69% | | backtest-run | 28% |
| db-update | 57% | | param-sweep-apply | 24% |
| ingest-video | 45% | | investigate-strategy | 21% |
| research-distil | 37% | | *domain skills* | *8–19%* |

**The drift is inverted.** Domain skills, which legitimately differ (crypto vs equities),
barely move: `new-strategy` 8%, `config-refresh` 10%, `frontend-svelte` 10%. The *workflow*
skills, whose content is repo-agnostic and should be identical, have diverged furthest.

**And it is one-directional.** Counted on wifey's `post-branch/SKILL.md` +
`tools/post_branch_checks.py`: **8 PRs in #214–#228** (214, 218, 221, 222, 223, 224,
227, 228) — extracting the checks into a tested script, adding the flip-forward
gate, fixing a phase that ordered a wrong stamp. The arc starts earlier still:
**13 PRs across #206–#228**, and **40 commits** have touched those two files. buibui's `post-branch` has had
**zero** dedicated PRs; every change rode along inside an unrelated feature PR. wifey is not
marginally ahead on the PR workflow — it is the only side that has worked on it.

*(Figure history, since this document is itself auditable: I first wrote 8 for the #214–#228
window; the peer corrected it to 7 and raised the historical totals to 17/46; re-counting
from git gives **8** in-window — the peer's 7 dropped #222, whose subject names `wait-ci`
though it edited the post-branch skill — and **40** commits, not 46. The 17
subject-line matches reproduce. The direction was never in question in any of the three
counts.)*

## From the reference repos

Ranked by value here, not by their importance there.

1. **`docs/agents/*.md` — repo-specific config that generic skills READ.** reference-A keeps
   `issue-tracker.md`, `domain.md`, `triage-labels.md`; the skills stay generic and the repo
   carries the variance. **This is the structural fix for the 89% drift**: `post-branch` and
   `sanity-check` become one shared skill plus a per-repo config naming the flip target, the
   gate list, the doc surfaces.
2. **`AGENTS.md` as the real file; `CLAUDE.md` = `@AGENTS.md` + Claude-only notes.**
   Agent-neutral, and it creates the natural seam for splitting our 55KB by audience.
3. **Vendored skills with hash pins.** `.agents/skills/` real directories, `.claude/skills/`
   as symlinks, `skills-lock.json` hashes, a weekly CI refresh PR, and
   `.claude/rules/vendored-skills.md` — a *path-scoped* rule that fires only when those
   files are edited, warning that local edits are silently overwritten. Path-scoped rules
   are rung-4 enforcement that costs zero always-loaded budget.
4. **Claude Code Review in CI**, running the same skill developers run locally, with an
   explicit clause telling it to skip what the gates already catch and never to re-poll a
   still-running check. Neither trading repo has any automated review.
5. **Thicker pre-commit** — `detect-private-key`, `mixed-line-ending`, `--unsafe` yaml. Both
   our configs are near-identical to each other and thinner than reference-B's.
6. **Dependabot cooldown: 7 days**, plus grouped pre-commit bumps — lets bad releases get
   yanked first.
7. **PR-title guidance table** (reference-A): mechanism-instead-of-symptom, file-instead-of-
   feature, refactor-verb-for-a-real-fix, vague improve/update. Squash-merge makes the PR
   title the permanent commit message and the changelog line.
8. **uv over Poetry.** Genuine but the largest and least urgent change; both repos are on
   Poetry with working lockfiles.

## Resolved — the W7 operator decision (2026-08-19)

`~/.claude-personal/CLAUDE.md` said the handoff and memory index were "a standing final step
of **every task**", the direct cause of failure mode (b), and it governs **every repo**.
**Applied the same day** under `## Session hygiene`: one targeted append per landed unit of
work, with the prune, the re-verify and the index update happening once, at session end.
Written by a buibui session on the operator's explicit diff-approval — not by peer agreement,
which is not authorisation.

⚠ **Both sessions then carried the stale premise for hours**, each telling the other the file
was untouched. The reusable rule, and it is the cheapest one in this document: **an account of
a shared surface goes stale the moment another session works on it — read the surface, not the
report.** A peer message is a draft; only a written surface is a finding.
