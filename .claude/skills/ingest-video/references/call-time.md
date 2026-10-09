# Steps 3-4b — stated times and call-time resolution

**When:** Read when pass 1 returns a stated time (video-level or per-item), when a quote and its timestamp disagree, and before touching `video_calltime.py`'s inputs.

Reference for `.claude/skills/ingest-video/SKILL.md`, which holds the run order, the steps and the condensed rules. This file holds the full text, including the dated incidents behind each rule. The passages below are carried over verbatim from the pre-split SKILL.md (#884), so where one says "this doc", "this document", "above", "below" or names a step, it means SKILL.md.

## Step 3 — the stated-time fields pass 1 must return

**`stated_ts_utc` must carry an explicit UTC offset (e.g. `2026-07-14T08:00:00+08:00`),
or be `null` — never a bare local time.** `tools/video_calltime.py` rejects a naive
(offset-less) timestamp and silently falls back to publish time, so a subagent that
emits `2026-07-14T08:00:00` with no offset gets the same downstream result as emitting
nothing, just less honestly. Instruct the subagent: state the offset whenever the
speaker's timezone is inferable from context, otherwise emit `null` — never guess UTC.

**`item_stated_ts_utc` is per-candidate and exists for RELAYS — ask for it explicitly or
you will not get it.** A relayed call was made before the roundup reporting it, and the
speaker usually says when: 「昨天下午」, 「今早」, 「上周五」. Instruct the subagent to
carry any such phrase into that field (same offset rule; `null` when the timezone is not
inferable). Step 4b feeds it to `video_calltime.py --relay --stated`; without it the row
falls back to `publish_relay`, which is honest but can be badly wrong. Measured on
`JcMq-lyHIt4` (2026-08-05): the host said 公有财 gave his view 「昨天下午」 at ts 322.6,
pass 1 returned no per-item time, and the routed row landed ~half a day late. **Do not
fix this downstream** — the orchestrator resolving 「昨天下午」 into a timestamp is
exactly the date arithmetic `video_calltime.py` exists to prevent. It has to come from
the pass that can read the sentence.

**`item_stated_ts_raw` is the verbatim phrase behind `item_stated_ts_utc`, and it is
mandatory whenever that field is non-null.** Ask for the quote in the speaker's own
language, unedited — never a translation or a normalisation. On a relay, pass THAT string
to step 4's `--stated-raw`, not the video-level `stated_ts_raw`; the two describe
different sentences and only the per-item one belongs on the row.

**Why: without it a wrong shift is invisible, and the shifts are wrong about a third of
the time.** Measured 2026-08-07 on `CqsZUQPpEX4` — pass 1 attached 「今早起床之后」 to
三马哥's call, but ts 238.11 says 「三马哥**昨晚**让大家比特币做空」: the short was called
LAST NIGHT, and 「今早起床之后」 (ts 251.09) introduces his *morning commentary* on the
level ladder. Stamping 今早 dates the call **~12h late**, and because BTC fell overnight
that systematically understates a short. Two other phrases in the same video (约翰
「昨晚7点」, 苏醒 「昨晚6点半」) were correct — so this is **1 in 3, not a one-off**. The
only thing that caught it was reading the transcript against the extraction, which does
not scale; with the raw quote on the row a reviewer sees it from the ledger.

**A stated time can also be internally CONTRADICTORY, and pass 1 will silently "fix" it.**
`QyZbF_PhbmE` says 「现在是北京时间**7月5号,周五**早上9点57分」 — July 5 2026 is a
**Sunday**, while 周五 matches the publish date. Pass 1 emitted a reconciled Aug 7
timestamp, i.e. exactly the model date arithmetic this pipeline forbids, which would have
stamped `stated` on a row whose own quote contradicts it. `video_calltime.py` fail-safes
correctly here — fed the literal July 5, the 33-day lead exceeds `STATED_TS_MAX_LEAD_H`
(168h) and it falls back to publish on its own — **so the tool is not the gap.** The gap
is that nothing forced the raw quote onto the row where a human would see the conflict.
**When the quote and the resolved timestamp disagree, emit the quote and let the tool
decide; never reconcile them in the prompt.**

## Step 4 — resolve the call time

```bash
PYTHONPATH=. poetry run python tools/video_calltime.py \
  --publish <meta.publish_ts_utc> \
  [--stated <stated_ts_utc>] [--date-only] \
  --stated-raw "<stated_ts_raw>" \
  --ingested <now, UTC ISO-8601>
```

- Omit `--stated` entirely when pass 1 returned `null` — do not pass the literal string
  `"null"`.
- Pass `--date-only` only when `stated_date_only` was `true`.
- `--stated-raw` is always passed (an empty string is fine).
- `--ingested` is the current UTC time, e.g. `` $(date -u +%Y-%m-%dT%H:%M:%SZ) `` —
  needed so the tool can also compute `backlog`. **Capture this one value per batch and
  reuse it verbatim** everywhere `ingested_ts_utc` is written later (the Stream C line
  in step 8, the per-video note frontmatter in step 9) — do not call `date -u` again at
  those points; two separate calls could disagree by however long the batch took to
  process, and the field exists to say when THIS pipeline saw the video, not to be
  re-timestamped per write site.
- **If `meta.publish_ts_utc` is an empty string** (yt-dlp returned no timestamp field —
  rare, but possible), `video_calltime.py` raises `ValueError` rather than guessing.
  Treat that video as call-time-unresolvable and skip it with a health note; do not pass
  an empty string through.

Output (JSON to stdout):

```json
{
  "call_ts_utc": "...",
  "call_ts_source": "stated|publish|publish_relay",
  "publish_ts_utc": "...",
  "stated_ts_utc": "... | null",
  "stated_ts_raw": "...",
  "backlog": false
}
```

Use these six fields **verbatim** in the digest, the ledger line, and the note. Never
have a subagent or the orchestrator derive `call_ts_utc` by date arithmetic — that is
exactly the look-ahead defect this tool exists to prevent (see Guardrails).

⚠ **`stated_ts_utc` is what the tool was GIVEN, echoed back including `null` — it is the
input, never a second copy of the verdict** (ST70(b)). It is what makes a publish
fallback say why it fell back: **40 of 108** live fallback rows carry a non-empty
`stated_ts_raw`, which is contract-correct when pass 1 emitted `null` for an uninferable
timezone and a real look-ahead rejection when it emitted a timestamp that then failed a
bound. From the row alone those were indistinguishable. Do not "simplify" it away as
redundant with `call_ts_utc`: on a date-only row the two deliberately differ, since the
stated value resolves forward to end-of-day.

## Step 4b — a relayed call's time

A relayed call was made **before** the roundup that reports it, so the video-level
`call_ts_utc` is wrong for it — that is the `retrospective` defect applied to 100% of
relays, and it is signed rather than self-cancelling: the scorer replays forward from
`call_ts`, so a relay of a call that already hit target scores as a non-hit, while one
that already stopped out can catch a later recovery.

When pass 1 returned a per-candidate stated time, run `video_calltime.py` again for that
item with `--relay`; when it did not, run it with `--relay` and no `--stated` so the row
is labelled `publish_relay`:

```bash
PYTHONPATH=. poetry run python tools/video_calltime.py \
  --publish <meta.publish_ts_utc> --relay [--stated <item stated_ts_utc>]
```

`--relay` labels the **fallback only** — a stated time that survives every bound is still
the better answer, and still comes back as `stated`.
