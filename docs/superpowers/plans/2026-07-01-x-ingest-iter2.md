# /ingest-x iteration 2 — batch ingestion (cooldown + cache + token efficiency)

**Date:** 2026-07-01
**Status:** implemented
**Spec:** `docs/superpowers/specs/2026-06-30-x-post-ingest-design.md` (iteration 1)
**Backlog source:** memory `project_ingest_x_iter2_backlog.md`

## Why

Iteration 1 (PR #466) shipped and immediately went into heavy live use — 12 posts in
one session, which ate ~84% of the context window. Two things blocked comfortable
multi-post use:

1. **Token blow-up (the binding constraint).** Each extraction subagent inherited
   Opus and re-read the ~7K-token SoT + `thesis-inbox.md` per post.
2. **No batch path, double network fetch.** One URL per call; each `/ingest-x`
   re-injected the whole SKILL.md, and the fetch hit the network twice per post
   (once for text, once to download the chart).

The operator also wanted to paste several tweets at once without tripping an IP-ban /
rate-limit. Honest read: both endpoints are public CDNs
(`cdn.syndication.twimg.com/tweet-result`, `pbs.twimg.com`) that power embedded tweets
everywhere — ~30 requests over minutes is trivial and ban risk is low. So pacing is
cheap insurance; the larger safety win is a dedup cache (zero network on re-runs),
which also removes the double-fetch.

## What changed

- **`tools/x_fetch.py`**
  - `XPost` gains `quoted_text` / `quoted_author` (best-effort from the payload's
    nested `quoted_tweet`); surfaced in `_format_human` and CLI JSON.
  - New `BatchResult` + `fetch_x_batch(urls, *, cache_dir, media_root, min_delay=4,
    max_delay=12, force, get, sleep, rng)`: per-id dedup cache at
    `.cache/x-posts/<id>.json`; a randomized `rng.uniform(min_delay, max_delay)` pause
    **between network fetches only** (never before the first, never on a cache hit);
    charts downloaded once. `sleep`/`rng`/`get` are injected for deterministic,
    network-free tests. `fetch_x_post` stays pure (no sleep).
  - CLI accepts N URLs + `--batch` / `--force` / `--min-delay` / `--max-delay` /
    `--cache-dir` / `--media-root`; batch mode prints a JSON array
    (`url`, `cached`, `photo_paths`, `post` | `unavailable`).
- **`.claude/skills/ingest-x/SKILL.md`**
  - Accepts many URLs; step 1 is a single `--batch --json` call returning text +
    local chart paths (no more `python -c` re-download).
  - Step 2 dispatches the per-post extraction subagent **pinned to `model: "sonnet"`**
    with a **self-contained inline rubric** (frozen detectors + already-tested
    verdicts + parked/data-blocked + the 4-bucket taxonomy) so it reads only its
    image — no SoT re-read.
  - One consolidated digest + one approval route the whole batch.

## Decisions

- **Pacing:** light randomized jitter (default 4–12 s) + dedup cache. Heavier
  human-like throttle deferred — no large one-shot batches planned now.
- **Quoted-tweet:** surfaced (many posts self-quote a prior call). Cheap, high-value.

## Deferred (out of scope)

Heavier human-like throttle; video ingestion (yt-dlp/Whisper); reply-count / reply
bodies; `.cache/x-posts` age eviction; the pundit-ledger scoring loop (grade
`pundit-calls.jsonl` vs realized price) — a separate future tool.

## Verification

`make lint-py` / `make typecheck` / `make test` all green (2118 passed; 10 new
`tools/x_fetch` tests cover quoted-tweet mapping + display, jitter-between-network-only,
cache hit skips network+sleep, cache write + chart download, `force` re-fetch,
`Unavailable`-not-cached, and the CLI array vs single-object output). No detector /
pipeline change ⇒ regression goldens unmoved. Live smoke is operator-run:
`tools/x_fetch.py <url1> <url2> --batch --json` → `cached:false` then `cached:true` on
re-run.
