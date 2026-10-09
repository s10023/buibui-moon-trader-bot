# Step 3 — pass 1, the per-channel knobs and the filter order

**When:** Read before writing a pass-1 prompt, when `hint` returns `matched: false`, and when a kept set looks wrong (relays, recaps, a non-crypto setup, or the cap).

Reference for `.claude/skills/ingest-video/SKILL.md`, which holds the run order, the steps and the condensed rules. This file holds the full text, including the dated incidents behind each rule. The passages below are carried over verbatim from the pre-split SKILL.md (#884), so where one says "this doc", "this document", "above", "below" or names a step, it means SKILL.md.

## Step 3 — dispatch and the per-channel knobs

For each shape-3 video, dispatch a subagent via the Task tool with **`model: "sonnet"`**
(do not inherit Opus) and **`subagent_type: "Explore"`** — measured **3.6× cheaper** than
`general-purpose` at identical quality on the same extraction shape (24,187 vs 87,975
tokens, 2026-08-03 A/B), the likely mechanism being that it does not inherit full project
context. `Explore` is documented as reading *excerpts*, so state explicitly that it must
**Read the transcript file in full and not sample it**. Give it:

- the video's `transcript_path` from step 1 — **the path, not the transcript.** Instruct
  it to Read that file; `segments` is the `"segments"` key inside it. Pasting the array
  into the prompt puts the whole transcript in your context for no gain, since the
  subagent has its own.
- `meta.publish_ts_utc`, `meta.author`, `meta.lang` — **context only**, for resolving a
  relative stated date ("last Monday") and inferring a speaker's timezone from channel
  locale. It must NOT compute a final call time itself — that happens in code, step 4.
- the channel's `intro_recap_s` and `item_cap` (see below) — numbers, not rules to
  re-derive
- the inline classification rubric (below)

**First, ask the config for this channel's two per-video knobs:**

```bash
PYTHONPATH=. poetry run python tools/yt_feed.py hint --author "<meta.author>"
```

Pure local config read, no API key, no network. Returns
`{"matched": …, "intro_recap_s": N, "item_cap": N, …}`. Run it per video — a batch can
span channels. Both knobs degrade quietly to a default: `matched: false` or
`intro_recap_s: 0` means no recap rule and nothing changes, and `item_cap` falls back to
`video_marks.ITEM_CAP` (5).

⚠ **`intro_recap_s` is a per-CHANNEL constant and the video's own chapters beat it.**
Compute the per-video window from step 1's `meta.chapters`:

```bash
PYTHONPATH=. poetry run python -c "
import json,sys
from tools.video_fetch import Chapter, recap_window_s
ch = tuple(Chapter(**c) for c in json.load(sys.stdin))
print(recap_window_s(ch))
" <<< '<meta.chapters as JSON>'
```

A **positive** result REPLACES `intro_recap_s` for that video, in both directions — a
shorter chapter window must narrow the trim too, or the override is just a bigger
constant. **`0.0` means fall back to `intro_recap_s`** (no leading recap chapter, or no
chapters at all — about half the corpus). Measured on `4Dkw1jz04lY`: @GiantCutie-K's
configured 120s against a recap chapter that actually runs to 186s, i.e. 66s of recap
that the constant reads as fresh content.

**`item_cap` is applied by the pass-1 PROMPT, not by code — so a value fetched here and
not passed on does nothing.** Carry it into rule 5 below as a literal. This shipped
broken: #558 added the key to `config/youtube_channels.toml`, `tools/yt_feed.py`, its
tests and `.claude/context/tools.md`, but left this document saying the cap was always
5 — so on the first run after it merged, `hint` returned `@KoluniteVIP`'s `item_cap: 12`
and the flow discarded it. A cap plumbed everywhere except its one consumer is not
plumbed.

Note this keys on `meta.author` (an @handle), which matches neither the `UC…` id the poll
path uses nor a CJK display `name` — that is why `config/youtube_channels.toml` carries a
`handle` field. **A channel with no `handle` never matches, and then BOTH knobs silently
take their defaults** — measured on `@KoluniteVIP` (2026-08-05): no `handle`, no
`item_cap`, `hint` returned `matched: false`, and the cap sat at 5 on a channel that
relays ~8 traders per upload. Neither degradation raises anything; the run just quietly
keeps less.

It must NOT read any repo, SoT, or memory file — the rubric is self-contained. Instruct
it to return ONLY this JSON:

```json
{
  "summary": "one paragraph, English",
  "stated_ts_utc": "ISO-8601 with an explicit UTC offset, or null",
  "stated_date_only": false,
  "stated_ts_raw": "verbatim quote or empty",
  "candidates": [
    {"ts": 252.0, "content_type": "setup|claim|mechanic", "specificity": 1-5,
     "is_relay": false, "originating_author": "@ThisChannel",
     "item_stated_ts_utc": null, "item_stated_ts_raw": "",
     "is_intro_recap": false, "retrospective": false, "gist": "..."}
  ]
}
```

## Step 3 — the filter order and the drop report

**Filter for ATTRIBUTION and SUBJECT BEFORE applying the cap — not after.** The cap is a
budget for items this repo can actually use, so spending a slot on one it will drop at
routing wastes the slot silently. Instruct the subagent, in this order:

1. **Drop `setup` candidates inside the intro-recap window — they are past
   calls.** Use the chapter-derived window from step 3 when it is positive, and
   `intro_recap_s` otherwise; if that window is non-zero, set `is_intro_recap: true` on
   every candidate with `ts < window`. For a `setup`, ALSO set `retrospective: true`,
   which is what makes `route_target` drop it. For a `claim` or `mechanic`, set only
   `is_intro_recap` and keep the candidate — an idea stays portable regardless of when in
   the video it was said — but the digest must show the flag so the human can decline it.

   **Why this is a rule and not a judgement call.** Some channels open every single upload
   by replaying prior positions before saying anything new. A `setup` lifted from that
   window is a *previous* call stamped with *today's* `call_ts_utc`, so
   `tools/pundit_score.py` scores the author on a trade that already resolved. On
   2026-08-03 the 7.24 upload's opening block described an entry that **never filled**
   (「離我入場的位置綠色框框就差幾塊錢」) — scoring that would have credited a trade nobody took.

   **And why `claim`/`mechanic` still need the flag rather than nothing.** `route_target`'s
   `retrospective` drop is **setup-only by design**, so a retrospective *mechanic* has no
   code path that stops it. That is exactly what happened the same day: the 7.20 upload's
   recap block ("上週14號我們是入場了一個比特幣的多單" — a past trade's exit management) was
   typed as a `mechanic` and routed to Stream B with nothing objecting. Only an operator
   reading the digest caught it. The flag is what makes that visible instead of silent.

2. **Relayed `setup` candidates are kept ONLY if the roster resolves the originating
   name.** Set `is_relay: true` whenever the speaker is reading out, reacting to, or
   summarising a call made by **someone else** (「X老师给了一个多单」, a screenshot of
   another analyst's Discord/Telegram post, an on-screen name card introducing a third
   party), and put that person's name **verbatim** in `originating_author` — do not
   normalise, correct or "fix" it, because the raw string is what the roster's alias
   lists are built from and a helpful correction destroys the evidence that grows them.
   Set `is_relay: false` and `originating_author` to the channel's own handle only for
   the speaker's own calls. **`claim` and `mechanic` candidates are exempt** — an idea is
   portable regardless of who first said it, and Streams A/B do not score anyone.
   Resolution happens in step 4; anything that does not resolve to `mapped` drops.
3. **Drop non-crypto `setup` candidates.** A setup on SPX, gold, DXY, oil or a
   single equity routes to wifey (step 2b's subject rule), never to `pundit-calls.jsonl`
   here — our scorer resolves against 24/7 perp bars it has no data for.
4. **`mechanic` candidates stay eligible regardless of symbol.** A risk-management or
   execution technique demonstrated on SPX is just as portable as one on BTC; the
   instrument is incidental to a mechanic in a way it never is to a setup.
5. **Then** rank what remains by `specificity` descending and keep the top `item_cap` —
   **the number `hint` returned for THIS channel**, written into the prompt as a literal.
   It is not a constant: it defaults to `tools/video_marks.py::ITEM_CAP` (5) and is raised
   per channel for aggregators that relay several traders per upload. Never let the
   subagent infer it, and never hard-code 5 here.

Report every dropped candidate with a one-line reason, distinguishing the four causes —
`intro-recap window (ts < intro_recap_s), a prior call not today's` vs
`relayed call by 陈哥, not the speaker's own` vs `non-crypto setup (SPX), routes to wifey`
vs `specificity 2, below the top-12 cutoff (this channel's item_cap)` — for the digest
and the note. **Filtered items
belong in the note too:** they are the record that the video contained something this repo
deliberately declined, not something it failed to see. Relayed calls in particular must be
preserved verbatim in the note with their `originating_author`, so a future attribution
roster can mine them without re-fetching the video.

Round 5 (2026-08-01) established the subject half and round 6 confirmed it paid: a
**specificity-4 S&P 500 setup** was dropped before the cap, freeing a slot for a crypto
item that would otherwise have been cut at rank 6.
