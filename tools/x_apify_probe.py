"""ST38 probe: do kaito's `filter:*` inputs filter server-side BEFORE billing?

Question (ST38, `docs/plans/x-scraper-research.md`): the roster gate wants
`has_media AND names_instrument`, and 26% of a poll is replies. If the actor's
`filter:replies` / `filter:media` inputs cut the result set server-side, we never pay
for the discarded posts. If they are ignored, the gate has to run client-side and the
reply share is billed in full.

Method: same author, same window, three runs that differ ONLY in the filter under test.
Compare item count, billed USD, and composition against the unfiltered baseline.

The actor is PAY_PER_EVENT on `apify-default-dataset-item`, so billing tracks items
PUSHED. A filter that works therefore bills less by construction -- which makes the real
question "does it work, and with which polarity", not "is billing pre- or post-filter".
Polarity is not documented and cannot be assumed: the input schema titles `filter:replies`
as "Filter Retweets" and `filter:safe` as "Filter Videos", so the labels are unreliable.

The default handles are deliberately neutral high-volume public accounts (`NASA` is the
control account in the actor's own documentation), so the tool runs out of the box without
publishing a research roster. Pass `--handle` / `--batch` to point it at real targets.

Never assert on run status. The sibling actor `apidojo/tweet-scraper` reports
SUCCEEDED / exitCode 0 while emitting `noResults` placeholders (the N8 silent-null shape),
so every assertion here is on item count and item content.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

API = "https://api.apify.com/v2"
ACTOR = "kaitoeasyapi~twitter-x-data-tweet-scraper-pay-per-result-cheapest"
PRICE_PER_ITEM_USD = 0.00025  # $0.25/1k, from the actor's pricingInfos


def _token() -> str:
    try:  # a git worktree has no .env of its own; the caller exports instead
        from dotenv import load_dotenv

        load_dotenv()  # APIFY_TOKEN may live only in .env
    except ImportError:
        pass
    tok = os.environ.get("APIFY_TOKEN", "").strip()
    if not tok:
        sys.exit("APIFY_TOKEN is unset (export it, or put it in .env)")
    return tok


def _req(
    method: str,
    path: str,
    token: str,
    payload: dict[str, Any] | None = None,
    **params: Any,
) -> Any:
    params["token"] = token
    url = f"{API}/{path}?{urllib.parse.urlencode(params)}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            body = resp.read().decode()
    except urllib.error.HTTPError as exc:
        sys.exit(f"{method} {path} -> HTTP {exc.code}: {exc.read().decode()[:400]}")
    return json.loads(body) if body else {}


def remaining_credit_usd(token: str) -> float:
    """Free-plan credit left this cycle. Reads the monthly usage endpoint."""
    d = _req("GET", "users/me/usage/monthly", token).get("data", {})
    spent = float(d.get("totalUsageCreditsUsdAfterVolumeDiscount") or 0.0)
    plan = _req("GET", "users/me", token).get("data", {}).get("plan", {})
    credit = float(plan.get("monthlyUsageCreditsUsd") or 0.0)
    return credit - spent


@dataclass
class RunResult:
    label: str
    run_id: str
    status: str
    exit_code: int | None
    items: list[dict[str, Any]]
    charged_usd: float
    charged_events: dict[str, Any]


def run_actor(
    label: str,
    inp: dict[str, Any],
    token: str,
    poll_s: float = 5.0,
    settle_s: float = 8.0,
) -> RunResult:
    started = _req("POST", f"acts/{ACTOR}/runs", token, payload=inp)["data"]
    run_id = started["id"]
    while True:
        run = _req("GET", f"actor-runs/{run_id}", token)["data"]
        if run["status"] not in ("READY", "RUNNING"):
            break
        time.sleep(poll_s)
    # The charge fields lag the status change: read at completion, `usageTotalUsd` is 0.0
    # and `chargedEventCounts` is empty. Re-read after a settle pause or the cost is a lie.
    time.sleep(settle_s)
    run = _req("GET", f"actor-runs/{run_id}", token)["data"]
    items = _req(
        "GET", f"datasets/{run['defaultDatasetId']}/items", token, clean="true"
    )
    if not isinstance(items, list):
        items = []
    return RunResult(
        label=label,
        run_id=run_id,
        status=run["status"],
        exit_code=run.get("exitCode"),
        items=items,
        charged_usd=float(run.get("usageTotalUsd") or 0.0),
        charged_events=run.get("chargedEventCounts") or {},
    )


def is_placeholder(item: dict[str, Any]) -> bool:
    """Padding, not a result -- and it is BILLED.

    kaito pads any under-floor response with `type: "mock_tweet"` rows carrying `id: -1`,
    and says so in the row's own text: "we have a minimum charge of $X per API call, even
    if the response contains no results. Thus, we returned N pieces of mock data." So the
    padding is free to DETECT and impossible to AVOID -- and the vendor reserves the right
    to change N. Any client must drop these before routing, or they become phantom rows.

    `noResults` is the sibling `apidojo/tweet-scraper` shape, kept because a client that
    switches actors must survive both.
    """
    if item.get("type") == "mock_tweet" or item.get("id") in (-1, "-1"):
        return True
    if item.get("noResults") is not None:
        return True
    blob = json.dumps(item).lower()
    return "noresults" in blob and len(item) <= 3


def is_reply(item: dict[str, Any]) -> bool:
    for key in ("isReply", "is_reply"):
        if isinstance(item.get(key), bool):
            return bool(item[key])
    for key in ("inReplyToId", "in_reply_to_status_id_str", "inReplyToStatusId"):
        if item.get(key):
            return True
    conv, tid = item.get("conversationId"), item.get("id") or item.get("id_str")
    return bool(conv and tid and str(conv) != str(tid))


def has_media(item: dict[str, Any]) -> bool:
    ext = item.get("extendedEntities") or item.get("extended_entities") or {}
    if isinstance(ext, dict) and ext.get("media"):
        return True
    ent = item.get("entities") or {}
    if isinstance(ent, dict) and ent.get("media"):
        return True  # not this actor's shape; kept for actor portability
    return any(item.get(key) for key in ("media", "photos", "videos"))


def summarise(r: RunResult) -> dict[str, Any]:
    real = [i for i in r.items if not is_placeholder(i)]
    return {
        "label": r.label,
        "status": r.status,
        "exit_code": r.exit_code,
        "items_total": len(r.items),
        "items_placeholder": len(r.items) - len(real),
        "items_real": len(real),
        "replies": sum(1 for i in real if is_reply(i)),
        "with_media": sum(1 for i in real if has_media(i)),
        "billed_items": (r.charged_events or {}).get("apify-default-dataset-item"),
        "charged_usd": round(r.charged_usd, 6),
        "charged_events": r.charged_events,
        "run_id": r.run_id,
    }


def _write(
    path: str,
    arms: list[tuple[str, dict[str, Any]]],
    rows: list[dict[str, Any]],
    keys: list[str],
    results: list[RunResult],
) -> None:
    with open(path, "w") as fh:
        json.dump(
            {
                "arms": dict(arms),
                "rows": rows,
                "keys": keys,
                "items": {r.label: r.items for r in results},
            },
            fh,
            indent=2,
        )
    print(f"\nwrote {path}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--handle", default="NASA", help="author, no @; pass your own roster"
    )
    ap.add_argument("--since", default="2026-08-15", help="UTC date, inclusive")
    ap.add_argument("--until", default="2026-08-18", help="UTC date, exclusive")
    ap.add_argument(
        "--max-items", type=int, default=30, help="hard per-run billing cap"
    )
    ap.add_argument(
        "--budget-usd", type=float, default=0.05, help="abort if worst case exceeds"
    )
    ap.add_argument(
        "--dry-run", action="store_true", help="print plan + cost, call nothing"
    )
    ap.add_argument(
        "--suite", default="filters", choices=["filters", "cost", "maxitems"]
    )
    ap.add_argument(
        "--batch",
        default="NASA,SpaceX,Space_Station",
        help="cost suite: comma-separated handles to OR into ONE call",
    )
    ap.add_argument("--out", default="", help="write the JSON report here")
    args = ap.parse_args()

    base: dict[str, Any] = {
        "from": args.handle,
        "since": args.since,
        "until": args.until,
        "maxItems": args.max_items,
        "queryType": "Latest",
    }
    window = f"since:{args.since} until:{args.until}"
    suites: dict[str, list[tuple[str, dict[str, Any]]]] = {
        # Does a filter cut the set server-side, and with what polarity?
        "filters": [
            ("A_baseline", dict(base)),
            ("B_filter_replies", {**base, "filter:replies": True}),
            ("C_filter_media", {**base, "filter:media": True}),
        ],
        # Given a billed floor per call, the cost levers are negation and batching.
        "cost": [
            (
                "D_negated_replies",
                {
                    "twitterContent": f"from:{args.handle} {window} -filter:replies",
                    "maxItems": args.max_items,
                    "queryType": "Latest",
                },
            ),
            (
                "E_batched_authors",
                {
                    "twitterContent": f"(from:{' OR from:'.join(args.batch.split(','))}) {window}",
                    "maxItems": args.max_items,
                    "queryType": "Latest",
                },
            ),
            ("F_tiny_maxitems", {**base, "maxItems": 5}),
        ],
        # Does `maxItems` bound spend at all? F says a small value is ignored; this asks
        # whether the E arm's exact-40 was the cap binding or just the natural count.
        "maxitems": [
            (
                "G_maxitems_20",
                {
                    "twitterContent": f"(from:{' OR from:'.join(args.batch.split(','))}) {window}",
                    "maxItems": 20,
                    "queryType": "Latest",
                },
            ),
        ],
    }
    arms = suites[args.suite]
    worst = len(arms) * args.max_items * PRICE_PER_ITEM_USD
    print(
        f"plan: {len(arms)} runs x maxItems={args.max_items} -> worst case ${worst:.4f}"
    )
    for label, inp in arms:
        print(f"  {label}: {json.dumps(inp)}")
    if worst > args.budget_usd:
        sys.exit(f"worst case ${worst:.4f} exceeds --budget-usd ${args.budget_usd:.4f}")
    if args.dry_run:
        return

    token = _token()
    left = remaining_credit_usd(token)
    print(f"free-plan credit remaining this cycle: ${left:.4f}")
    if left < worst:
        sys.exit(f"remaining ${left:.4f} < worst case ${worst:.4f}")

    results: list[RunResult] = []
    for label, inp in arms:
        print(f"running {label} ...", flush=True)
        results.append(run_actor(label, inp, token))

    rows = [summarise(r) for r in results]
    print("\n" + json.dumps(rows, indent=2))

    keys = sorted(results[0].items[0].keys()) if results[0].items else []
    print(f"\nbaseline item keys ({len(keys)}): {keys}")

    print("\n--- billed floor ---")
    for row in rows:
        billed, real = row["billed_items"], row["items_real"]
        if billed and real < billed:
            print(
                f"{row['label']}: {real} real but {billed} BILLED "
                f"({row['items_placeholder']} mock) -- paying for padding."
            )
        else:
            print(f"{row['label']}: {real} real, {billed} billed -- no padding.")

    if args.suite != "filters":
        if args.out:
            _write(args.out, arms, rows, keys, results)
        return

    a, b, c = rows
    print("\n--- verdict ---")
    if a["items_real"] == 0:
        print("INCONCLUSIVE: baseline returned no real items -- widen the window.")
        return
    for arm, name, comp in (
        (b, "filter:replies", "replies"),
        (c, "filter:media", "with_media"),
    ):
        if arm["items_real"] == a["items_real"] and arm[comp] == a[comp]:
            print(f"{name}: IGNORED -- identical count and composition to baseline.")
        elif arm["items_real"] == 0:
            print(
                f"{name}: EMPTIES the result set at this window (unusable, or 0 matched)."
            )
        elif comp == "replies" and arm["replies"] == 0:
            print(
                f"{name}: EXCLUDES replies. Billed ${arm['charged_usd']:.5f} vs baseline "
                f"${a['charged_usd']:.5f} -- server-side, pre-billing."
            )
        elif comp == "replies" and arm["replies"] == arm["items_real"]:
            print(f"{name}: INVERTED -- returns ONLY replies. Use the negated form.")
        elif comp == "with_media" and arm["with_media"] == arm["items_real"]:
            print(
                f"{name}: keeps ONLY media posts. Billed ${arm['charged_usd']:.5f} vs baseline "
                f"${a['charged_usd']:.5f} -- server-side, pre-billing."
            )
        else:
            print(f"{name}: PARTIAL -- changed the set but not cleanly; read the JSON.")

    if args.out:
        _write(args.out, arms, rows, keys, results)


if __name__ == "__main__":
    main()
