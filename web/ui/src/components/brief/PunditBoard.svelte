<script lang="ts">
  import type { BriefPunditBoard } from "../../api";
  import Card from "./Card.svelte";
  import { fmtCoverage, fmtPct, fmtR } from "./format";

  type Props = { pundit: BriefPunditBoard };
  let { pundit }: Props = $props();
</script>

<Card title="Pundit Board">
  {#snippet aside()}
    <span class="count">
      {pundit.ledger_total} calls tracked
      {#if pundit.ledger_skipped}<span class="skipped"
          >· {pundit.ledger_skipped} skipped</span
        >{/if}
    </span>
  {/snippet}

  {#if pundit.priors_status !== "ok"}
    <div class="notice">
      priors {pundit.priors_status} — run <code>make buibui-pundit-score</code>
    </div>
  {:else if pundit.priors_age_days !== null && pundit.priors_age_days > 3}
    <div class="notice">
      priors {pundit.priors_age_days}d stale — consider re-running the scorer
    </div>
  {/if}

  <!--
    No max-height here on purpose. This list used to be clipped to 260px with
    overflow-y:auto, which put the calls — the thing the board exists to show —
    in a small scrolling window inside an already-scrolling page, and truncated
    the entry/target text that carries the actual claim.
  -->
  <ol class="calls">
    {#each pundit.recent_calls as call}
      <li class="call">
        <div class="call-head">
          <span class="dot" class:on-panel={call.on_panel} aria-hidden="true">
            {call.on_panel ? "●" : "·"}
          </span>
          <span class="author">{call.author}</span>
          {#if call.prior}
            <span class="prior" class:flagged={call.prior.flagged}>
              {call.prior.flagged
                ? `⚠ n=${call.prior.n}`
                : `n=${call.prior.n}${
                    call.prior.hit_rate !== null ? ` · ${fmtPct(call.prior.hit_rate)}` : ""
                  }`}
            </span>
          {/if}
          <span class="symbol">{call.symbol}</span>
          <span
            class="dir"
            class:long={call.direction === "long"}
            class:short={call.direction === "short"}
          >
            {call.direction}
          </span>
          <span class="age">{call.age_days}d</span>
        </div>
        <p class="claim">
          <span class="entry">{call.entry}</span>
          {#if call.target}
            <span class="arrow" aria-hidden="true">→</span>
            <span class="target">{call.target}</span>
          {/if}
        </p>
      </li>
    {:else}
      <li class="empty">no recent calls</li>
    {/each}
  </ol>

  <div class="tables">
    <div class="table-block">
      <h4 class="block-title">By author</h4>
      <table>
        <thead>
          <tr><th>author</th><th>n</th><th>hit</th><th>ATR-R</th><th>avg R</th></tr>
        </thead>
        <tbody>
          {#each pundit.authors as a}
            <tr class:flagged-row={a.flagged}>
              <td>{a.author}</td>
              <td class="num dim">{a.n}</td>
              <td class="num">{fmtPct(a.hit_rate)}</td>
              <td
                class="num"
                class:pos={(a.avg_atr_r ?? 0) > 0}
                class:neg={(a.avg_atr_r ?? 0) < 0}
              >
                {fmtR(a.avg_atr_r)}
              </td>
              <td class="num dim">
                {fmtR(a.avg_r)}<span class="cov">{fmtCoverage(a.r_coverage)}</span>
              </td>
            </tr>
          {:else}
            <tr><td colspan="5" class="dim">no authors scored yet</td></tr>
          {/each}
        </tbody>
      </table>
    </div>

    <div class="table-block">
      <h4 class="block-title">By family</h4>
      <table>
        <thead>
          <tr><th>family</th><th>dir</th><th>n</th><th>hit</th><th>ATR-R</th><th>avg R</th></tr>
        </thead>
        <tbody>
          {#each pundit.families as f}
            <tr class:flagged-row={f.flagged}>
              <td>{f.family}</td>
              <td class:long={f.direction === "long"} class:short={f.direction === "short"}>
                {f.direction}
              </td>
              <td class="num dim">{f.n}</td>
              <td class="num">{fmtPct(f.hit_rate)}</td>
              <td
                class="num"
                class:pos={(f.avg_atr_r ?? 0) > 0}
                class:neg={(f.avg_atr_r ?? 0) < 0}
              >
                {fmtR(f.avg_atr_r)}
              </td>
              <td class="num dim">
                {fmtR(f.avg_r)}<span class="cov">{fmtCoverage(f.r_coverage)}</span>
              </td>
            </tr>
          {:else}
            <tr><td colspan="6" class="dim">no families scored yet</td></tr>
          {/each}
        </tbody>
      </table>
    </div>
  </div>
</Card>

<style>
  .count { font-size: var(--fs-annot); color: var(--text-soft); }
  .skipped { color: var(--muted); }

  .notice {
    font-size: var(--fs-annot);
    color: var(--yellow);
    background: color-mix(in srgb, var(--yellow) 8%, transparent);
    border: 1px solid color-mix(in srgb, var(--yellow) 25%, transparent);
    border-radius: 3px;
    padding: 6px 10px;
    margin-bottom: 12px;
  }

  .notice code { font-family: var(--font-mono); color: var(--text); }

  .calls {
    list-style: none;
    margin: 0 0 18px;
    padding: 0;
    display: grid;
    gap: 9px;
  }

  .call {
    padding-bottom: 8px;
    border-bottom: 1px solid var(--border-dim);
  }

  .call:last-child { border-bottom: none; padding-bottom: 0; }

  .call-head {
    display: flex;
    align-items: baseline;
    flex-wrap: wrap;
    gap: 7px;
    margin-bottom: 3px;
  }

  .dot { color: var(--muted); }
  .dot.on-panel { color: var(--accent); }

  .author {
    color: var(--text);
    font-weight: 700;
    font-size: var(--fs-annot);
  }

  .prior {
    font-size: 9px;
    letter-spacing: 0.03em;
    padding: 1px 5px;
    border-radius: 3px;
    color: var(--accent);
    background: var(--accent-dim);
  }

  .prior.flagged {
    color: var(--yellow);
    background: color-mix(in srgb, var(--yellow) 12%, transparent);
  }

  .symbol { color: var(--text-soft); font-size: var(--fs-micro); }

  .dir {
    font-size: 9px;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    font-weight: 700;
  }

  .age { margin-left: auto; color: var(--muted); font-size: var(--fs-micro); }

  /* The claim is the content. It gets a full-width line, real leading and the
     readable text colour — it was previously an inline quoted fragment
     competing with six other spans on one wrapping row. */
  .claim {
    margin: 0;
    padding-left: 15px;
    font-size: var(--fs-annot);
    line-height: 1.65;
    color: var(--text-dim);
  }

  .entry { color: var(--text); }
  .arrow { color: var(--muted); margin: 0 5px; }
  .target { color: var(--text-dim); }

  .empty { color: var(--muted); list-style: none; }

  .tables {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
    gap: 16px;
  }

  .block-title {
    font-size: var(--fs-eyebrow);
    font-weight: 700;
    letter-spacing: var(--ls-eyebrow);
    text-transform: uppercase;
    color: var(--text-soft);
    margin-bottom: 6px;
  }

  table { width: 100%; border-collapse: collapse; font-size: var(--fs-annot); }

  th {
    text-align: left;
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: var(--text-soft);
    border-bottom: 1px solid var(--border);
    padding: 4px 6px;
  }

  td {
    padding: 4px 6px;
    border-bottom: 1px solid var(--border-dim);
    color: var(--text);
    font-size: var(--fs-annot);
  }

  tbody tr:last-child td { border-bottom: none; }

  .flagged-row td:first-child { box-shadow: inset 2px 0 0 var(--yellow); }

  .dim { color: var(--text-soft); }
  /* Coverage rides with avg R and must never outweigh it. */
  .cov {
    margin-left: 0.35em;
    font-size: 0.85em;
    color: var(--text-soft);
  }
  .num { font-variant-numeric: tabular-nums; text-align: right; }
  .pos { color: var(--green); }
  .neg { color: var(--red); }
  .long { color: var(--green); }
  .short { color: var(--red); }
</style>
