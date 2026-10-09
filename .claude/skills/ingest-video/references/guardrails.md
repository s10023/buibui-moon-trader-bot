# Guardrails — the full text

**When:** Read when a guardrail in SKILL.md seems to conflict with a step, or before changing `FRAME_CAP`, `ITEM_CAP`, the subagent model or the recap rule.

Reference for `.claude/skills/ingest-video/SKILL.md`, which holds the run order, the steps and the condensed rules. This file holds the full text, including the dated incidents behind each rule. The passages below are carried over verbatim from the pre-split SKILL.md (#884), so where one says "this doc", "this document", "above", "below" or names a step, it means SKILL.md.

## Guardrails

- Never write to a stream before the user approves the digest — one approval covers the
  whole batch, exactly as in `/ingest-x`.
- `call_ts_utc` never comes from the model's own date arithmetic. Pass 1 only extracts a
  candidate `stated_ts_utc`/`stated_ts_raw`; the actual resolution — stated-vs-publish,
  bounding, backlog — always runs through `tools/video_calltime.py` (step 4). A subagent
  or the orchestrator computing this by hand reintroduces the exact look-ahead defect the
  tool exists to prevent (see the spec's "ledger-integrity constraint").
- Output is a hypothesis/setup/mechanic to TEST — never an "add a detector" task. The
  22-strategy detector list is frozen, same as `/ingest-x`.
- Routing dedup is `tools/route_dedup.py` (checked in step 7, marked in step 8) — the
  manual "grep the sink yourself" guardrail is retired. One thing it does NOT do, and
  you must not assume otherwise: it never auto-drops a near-duplicate — the digest and
  the human decide, on Stream C and everywhere else. On Stream C its near-duplicate pass
  is scoped to a single `source_id`, so it will never flag two authors making the same
  call, which are two real observations rather than a duplicate.
- A `setup` the speaker declined is not a call. `rejected: true` is the only thing
  standing between "he talked through this short" and "he is scored on this short";
  pass it to `route_target` rather than applying it by eye.
- A `setup` from the channel's opening recap block is not today's call. `intro_recap_s`
  (step 3, from `yt_feed.py hint`) is the only thing that catches it, and it is **opt-in
  per channel** — an unconfigured channel silently has no rule, which is correct but means
  a newly-followed recap-style channel needs the field set before its first ingest. The
  companion failure is quieter: `route_target`'s `retrospective` drop is **setup-only**, so
  a retrospective `mechanic` or `claim` has NO code path stopping it — that is why those
  carry `is_intro_recap` into the digest instead of being dropped. If a future round finds
  a past trade's exit management sitting in `mechanics-backlog.md`, the bug is that the
  flag never reached the digest, not that the case is unknown.
- A `setup` the speaker **relayed** is not their call either. The attribution filter in
  step 3 drops it before the cap, so it never reaches frames, pass 2, or a sink — which
  is also why pass 2 has no `originating_author` field to fill. This one has regressed
  once already (diagnosed round 1, repeated round 7) because the fix lived in a memory
  file instead of in this document. If a future round finds relays in the kept set again,
  the bug is that step 3's rule was dropped from the pass-1 prompt, not that it is
  unknown.
- Two subagent passes, both pinned to `model: "sonnet"` — never let either inherit Opus.
  Neither may read any repo, SoT, or memory file; the rubric above is the only context
  either needs beyond the video's own transcript/frames.
- `FRAME_CAP` (15) and `ITEM_CAP` (5) are a-priori constants in
  `tools/video_marks.py`. Raising either **globally** is a visible, deliberate change to
  the design spec's constants table — not a silent tuning knob inside a subagent prompt.
  A per-channel `item_cap` in `config/youtube_channels.toml` is the sanctioned exception
  and is not the thing that rule guards against: it is committed config, it is reported by
  `hint`, and step 3 passes it through explicitly. The failure mode it introduces is the
  opposite one — a cap that is configured, fetched, and then never reaches the prompt.
