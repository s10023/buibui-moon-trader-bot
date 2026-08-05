  #!/usr/bin/env bash
# ST15 — capture the daily Coinglass panel set with agent-browser.
#
# REQUIRES a logged-in Coinglass session: only the BTC heatmap is free. ETH/SOL
# heatmaps and the Liquidation Map (every coin) show "Log in to unlock full data",
# which blurs the chart while LEAVING ALL THE TEXT IN THE DOM — so this script
# asserts the wall's absence, never the presence of a heading alone.
#
# Auth comes from the operator's real Chrome profile, driven by the real Chrome
# binary so the "Chrome Safe Storage" keyring entry matches and cookies decrypt.
# GOOGLE OAUTH CANNOT BE USED from inside automation ("this browser may not be
# secure"), so reusing an already-signed-in profile is the route.
#
#   CHROME MUST BE FULLY CLOSED — Playwright takes a persistent-context lock.
#
# Usage: tools/coinglass_capture.sh [PROFILE]     (default profile: Default)

set -uo pipefail

PROFILE="${1:-Default}"
DROPS="$(cd "$(dirname "$0")/.." && pwd)/docs/plans/chart-drops"
STAMP="$(TZ=Asia/Kuala_Lumpur date +%Y%m%d)"
AB=(agent-browser --session cgcap --profile "$PROFILE" --executable-path /usr/bin/google-chrome)

ok=0; fail=0; min=0

# Absence of the wall AND the expected coin in the heading. Either alone is
# insufficient: the wall leaves the heading readable, and a 404 page has no wall.
verify() {
  "${AB[@]}" eval "(()=>{const t=document.body.innerText;
    return JSON.stringify({wall:/Log in to unlock full data/.test(t),
      notfound:/404 Not Found/.test(t),
      heading:(t.match(/Binance [A-Z]+\/USDT[^\n]{0,40}/)||['NONE'])[0]});})()" 2>&1 | tail -1
}

# Page default is 0.85; the capture protocol wants 0.90. Arrow keys and a direct
# .value assignment are both no-ops — React's synthetic layer ignores them.
set_threshold() {
  "${AB[@]}" eval "(()=>{const n=document.querySelector('.MuiSlider-root input');
    if(!n) return 'no-slider';
    const st=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;
    st.call(n,'0.9');
    n.dispatchEvent(new Event('input',{bubbles:true}));
    n.dispatchEvent(new Event('change',{bubbles:true}));return 'ok';})()" >/dev/null 2>&1
}

dismiss_overlays() {
  "${AB[@]}" find text "Accept" click       >/dev/null 2>&1   # cookie banner
  "${AB[@]}" click ".shou.cg-style-1w7o7rf" >/dev/null 2>&1   # ad close (x)
}

# The filename timestamp is parsed by chart_drops.py into captured_at_ms and is
# meant to be the real MYT wall clock. An earlier version fabricated it from a
# counter (1601/1602/1603), which silently backdated a 17:00 capture by an hour.
# Read the clock; only bump the minute to break a genuine same-minute collision.
drop_path() {
  local coin="$1" t; t="$(TZ=Asia/Kuala_Lumpur date +%H%M)"
  local p="${DROPS}/coinglass_${coin}USDT_${STAMP}-${t}.png"
  local n=0
  while [[ -e "$p" && $n -lt 60 ]]; do
    n=$((n + 1))
    t="$(TZ=Asia/Kuala_Lumpur date -d "+${n} minute" +%H%M)"
    p="${DROPS}/coinglass_${coin}USDT_${STAMP}-${t}.png"
  done
  printf '%s' "$p"
}

capture() {
  local coin="$1" url="$2"
  min=$((min + 1))
  local out; out="$(drop_path "$coin")"

  echo "--- ${coin} ---"
  "${AB[@]}" open "$url" >/dev/null 2>&1
  sleep 8
  dismiss_overlays
  "${AB[@]}" set viewport 1920 1400 >/dev/null 2>&1
  sleep 2
  set_threshold
  sleep 3

  local v; v="$(verify)"
  echo "    $v"
  if [[ "$v" == *'"wall":true'* || "$v" == *'"notfound":true'* || "$v" != *"$coin"* ]]; then
    echo "    SKIP — gated, 404, or wrong coin. Nothing written."
    fail=$((fail + 1)); return
  fi

  "${AB[@]}" screenshot "$out" >/dev/null 2>&1
  if [[ -s "$out" ]]; then echo "    saved $(basename "$out")"; ok=$((ok + 1))
  else echo "    SCREENSHOT FAILED"; fail=$((fail + 1)); fi
}

if pgrep -x chrome >/dev/null 2>&1 || pgrep -x google-chrome >/dev/null 2>&1; then
  echo "WARNING: Chrome appears to be running. The profile lock will fail the run."
  echo "Close Chrome completely, then re-run."
fi

# An agent-browser daemon that is ALREADY UP silently ignores --profile and
# --executable-path (it prints "daemon already running" and keeps its old
# options), so the run proceeds on a throwaway /tmp profile — i.e. LOGGED OUT.
# That fails quietly in the worst way: BTC is free, so it captures one good panel
# and reports ETH/SOL as "gated", which is indistinguishable from a genuinely
# expired login. Tear the daemon down first, then PROVE the profile took.
agent-browser close --all >/dev/null 2>&1
sleep 3

if "${AB[@]}" open "about:blank" 2>&1 | grep -q "ignored: daemon already running"; then
  echo "FATAL: agent-browser daemon would not restart, so --profile was ignored."
  echo "       The run would be logged out and only BTC would succeed."
  echo "       Kill it by hand (pkill -f agent-browser-linux) and re-run."
  exit 1
fi

for coin in BTC ETH SOL; do
  capture "$coin" "https://www.coinglass.com/pro/futures/LiquidationHeatMap?coin=${coin}"
done

# The Liquidation Map half is UNVERIFIED. ?coin= 404s on this route, so the coin
# must be chosen through the dropdown, and the 1d window control was never
# reached logged-out. Left here deliberately as the next thing to build.
echo "--- Liquidation Map (1d) — NOT IMPLEMENTED ---"
echo "    ?coin= 404s on /pro/futures/LiquidationMap; needs dropdown + 1d window."
echo "    Capture these three panels by hand for now."

"${AB[@]}" close >/dev/null 2>&1
echo
echo "heatmaps saved: ${ok}  skipped/failed: ${fail}"
echo "A COMPLETE daily set is 6 panels. Do not run /ingest-charts on a partial set —"
echo "the daily check asserts recency only, so one fresh drop greens it while the"
echo "rest go stale."
