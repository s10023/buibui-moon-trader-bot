<script lang="ts">
  import Card from "./Card.svelte";

  // Glossary for the brief. The wording is unchanged from when this lived
  // inline in Brief.svelte — only the presentation moved. It is long by design;
  // it is behind a toggle precisely so length costs nothing until asked for.
</script>

<Card title="Reading this brief">
  {#snippet aside()}
    <span class="hint">definitions for every field below</span>
  {/snippet}

  <dl class="legend">
    <dt>Regime 1D / 4H</dt>
    <dd>
      Classifier output (trend / range / high-vol / unknown) from the same regime model that
      soft-gates live signals.
    </dd>

    <dt>ATR14</dt>
    <dd>
      Daily Wilder ATR in price units. Every ± number on levels and zones is a distance in
      daily ATRs from the Last price.
    </dd>

    <dt>Levels</dt>
    <dd>
      PDH/PDL prior day high/low · PWH/PWL prior week · MonH/MonL Monday's range (active Tue
      onward) · DO/WO/MO today's / this week's / this month's open.
    </dd>

    <dt>swept</dt>
    <dd>
      Price pierced the level during the current period and now trades back on the original
      side (sweep + reclaim/reject).
    </dd>

    <dt>Zones</dt>
    <dd>
      Structural zones per timeframe: FVG fair-value gap · OB order block · BOS break of
      structure · EQH/EQL equal highs/lows. Tag shows ATR distance, or "inside" when price is
      within the zone.
    </dd>

    <dt>Last</dt>
    <dd>
      Reference price for all distances: freshest completed 1h close, falling back to the
      forming or last completed daily bar (tagged).
    </dd>

    <dt>Seasonality</dt>
    <dd>
      Day-of-week stats over the lookback: bull % of days, average range, which session most
      often prints the day's high/low, and typical weekly high/low days.
    </dd>

    <dt>EMA</dt>
    <dd>
      Price vs the 20/50/200-day EMAs (▲ above / ▼ below / — not enough history), the stack
      order (bullish 20&gt;50&gt;200), and the 200's 5-day slope.
    </dd>

    <dt>State</dt>
    <dd>
      Current 1D regime and how many bars it has held; when ranging, the run's high–low band
      and where price sits inside it.
    </dd>

    <dt>Monday</dt>
    <dd>
      Price vs this week's Monday range (above / inside / below); "forming" on Mondays while
      the range is still being set.
    </dd>

    <dt>Candle</dt>
    <dd>
      Anatomy patterns detected on yesterday's completed daily candle (engulfing, pin bar,
      doji, inside bar, hammer, star).
    </dd>

    <dt>PA</dt>
    <dd>
      Price-action character over the last 10 days: impulse (fast directional) vs grind (slow
      directional) vs chop, from efficiency ratio × ATR-normalised speed.
    </dd>

    <dt>BB/VWAP</dt>
    <dd>
      Bollinger(20,2σ) %B and bandwidth (p = squeeze percentile), plus price distance in ATRs
      from the weekly (W) and monthly (M) anchored VWAPs.
    </dd>

    <dt>VP 60d</dt>
    <dd>
      60-day volume profile from our own 1h data: point of control and 70% value area, with
      price above / inside / below value.
    </dd>

    <dt>Session clock</dt>
    <dd>
      Current MYT trading session: Asia 08–14 · London 14–22 · NY 22–04; 20–22 = London–NY
      overlap. "between sessions" = the Off gap.
    </dd>

    <dt>Sessions</dt>
    <dd>
      Last 3 completed sessions: net move and range in ATR14 units. "(n/m bars)" flags partial
      1h coverage.
    </dd>

    <dt>set-high / set-low</dt>
    <dd>Which of the 3 sessions printed the day's high / low.</dd>

    <dt>tendency</dt>
    <dd>Share of the last 180 days each session made the daily high or low.</dd>

    <dt>Month</dt>
    <dd>
      Month-to-date return, its percentile against completed prior months, and where price
      sits in the month's range (0 = low, 1 = high), with the share of the month elapsed. The
      percentile is unconditional — every completed prior month, not split by direction.
      Deliberately three numbers rather than a cone like the weekly block: splitting ~90
      months by direction leaves ~45 per group, too thin to draw percentile bands from.
    </dd>

    <dt>Week</dt>
    <dd>
      Where the forming week sits in the weekly path cone: the direction of the path
      <em>so far</em>, hours elapsed since the Monday 00:00 UTC open, and the move in AWR14
      units. The percentiles rank this path against past weeks that closed the same direction,
      and against all weeks. <strong
        >This describes the week so far. It is not a forecast, and the direction shown is not a
        claim about where the week closes.</strong
      > Weeks are grouped by how they <em>ended</em>, so the bull band sits above the all-weeks
      band by construction — mid-week you do not know which group this week belongs to. When no
      same-direction cohort resolves, only the all-weeks rank is shown. Hours are UTC, since the
      week is defined by the Monday 00:00 UTC candle, with MYT alongside in the readout.
    </dd>

    <dt>External</dt>
    <dd>
      Verified liquidation and order-book levels read from operator-dropped chart screenshots
      (Coinglass / MMT). Each line is one snapshot: source (with /venue when the panel shows
      one specific exchange's data, e.g. coinglass/hyperliquid), panel (liq / book / map),
      window, an "agg" tag when the panel shows exchange-aggregated rather than pair-specific
      data, capture age, then price bands split above / below the reference price (nearest
      first, capped per side) with intensity (HIGH = brightest) and ATR distance. ⚠spot flags
      a snapshot whose printed spot price disagrees with the brief's reference price.
    </dd>

    <dt>Pundit board</dt>
    <dd>
      Recent ledger calls with per-author priors: n calls scored, hit rate, and average R
      (ATR-proxy R for stop-less calls). ⚠ marks low-sample authors; ● marks calls on a symbol
      shown above.
    </dd>
  </dl>
</Card>

<style>
  .hint { font-size: var(--fs-annot); color: var(--muted); }

  /* Two columns on wide screens so the term sits beside its definition, but the
     definition column keeps a comfortable measure rather than running the full
     card width — long glossary prose at 1400px is its own readability problem. */
  .legend {
    display: grid;
    grid-template-columns: max-content minmax(0, 68ch);
    gap: 9px 18px;
    margin: 0;
    font-size: var(--fs-annot);
  }

  dt {
    font-weight: 700;
    color: var(--text);
    letter-spacing: 0.03em;
  }

  dd {
    margin: 0;
    color: var(--text-dim);
    line-height: 1.7;
  }

  dd :global(strong) { color: var(--text); }
  dd :global(em) { color: var(--text-soft); font-style: italic; }

  @media (max-width: 720px) {
    .legend { grid-template-columns: 1fr; gap: 2px 0; }
    dt { margin-top: 10px; }
  }
</style>
