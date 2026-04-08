<script lang="ts">
  import { getBacktestAnalysis, type DigestResult } from "../api";

  let { query, title, description, minTrades = 5, topN = 20 } = $props<{
    query: string;
    title: string;
    description: string;
    minTrades?: number;
    topN?: number;
  }>();

  type CardState = "idle" | "loading" | "loaded" | "error";
  let cardState = $state<CardState>("idle");
  let result = $state<DigestResult | null>(null);
  let errorMsg = $state("");
  let sortCol = $state(0);
  let sortAsc = $state(false);

  async function run() {
    cardState = "loading";
    errorMsg = "";
    try {
      result = await getBacktestAnalysis(query, minTrades, topN);
      sortCol = 0;
      sortAsc = false;
      cardState = "loaded";
    } catch (e) {
      errorMsg = e instanceof Error ? e.message : String(e);
      cardState = "error";
    }
  }

  function setSort(i: number) {
    if (sortCol === i) {
      sortAsc = !sortAsc;
    } else {
      sortCol = i;
      sortAsc = false;
    }
  }

  const sortedRows = $derived((() => {
    if (!result) return [];
    return [...result.rows].sort((a, b) => {
      const av = a[sortCol];
      const bv = b[sortCol];
      if (av === null && bv === null) return 0;
      if (av === null) return 1;
      if (bv === null) return -1;
      const cmp = av < bv ? -1 : av > bv ? 1 : 0;
      return sortAsc ? cmp : -cmp;
    });
  })());

  function fmt(v: string | number | null): string {
    if (v === null || v === undefined) return "—";
    if (typeof v === "number") {
      if (Number.isInteger(v)) return String(v);
      return v.toFixed(3);
    }
    return String(v);
  }

  function deltaClass(v: string | number | null): string {
    if (typeof v !== "number") return "";
    if (v > 0) return "pos";
    if (v < 0) return "neg";
    return "";
  }
</script>

<div class="card">
  <div class="card-header">
    <div class="card-meta">
      <span class="card-title">{title}</span>
      <span class="card-desc">{description}</span>
    </div>
    <button class="run-btn" onclick={run} disabled={cardState === "loading"}>
      {cardState === "loading" ? "Running…" : cardState === "loaded" ? "↺ Refresh" : "▶ Run"}
    </button>
  </div>

  {#if cardState === "idle"}
    <div class="placeholder">Click Run to load</div>
  {:else if cardState === "loading"}
    <div class="placeholder">Loading…</div>
  {:else if cardState === "error"}
    <div class="placeholder error">{errorMsg}</div>
  {:else if result && sortedRows.length === 0}
    <div class="placeholder">No data — run backtests with <code>--save</code> first</div>
  {:else if result}
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            {#each result.columns as col, i}
              <th
                class:active={sortCol === i}
                class:asc={sortCol === i && sortAsc}
                onclick={() => setSort(i)}
              >
                {col}
                {#if sortCol === i}<span class="sort-arrow">{sortAsc ? "▲" : "▼"}</span>{/if}
              </th>
            {/each}
          </tr>
        </thead>
        <tbody>
          {#each sortedRows as row}
            <tr>
              {#each row as cell, i}
                <td class={deltaClass(cell)}>{fmt(cell)}</td>
              {/each}
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
    <div class="row-count">{sortedRows.length} rows</div>
  {/if}
</div>

<style>
  .card {
    background: var(--bg-card, #1a1a1a);
    border: 1px solid var(--border, #2a2a2a);
    border-radius: 6px;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }

  .card-header {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 1rem;
    padding: 0.75rem 1rem;
    border-bottom: 1px solid var(--border, #2a2a2a);
  }

  .card-meta {
    display: flex;
    flex-direction: column;
    gap: 0.2rem;
  }

  .card-title {
    font-size: 0.85rem;
    font-weight: 600;
    color: var(--fg, #e0e0e0);
    letter-spacing: 0.02em;
    text-transform: uppercase;
  }

  .card-desc {
    font-size: 0.72rem;
    color: var(--fg-dim, #666);
  }

  .run-btn {
    flex-shrink: 0;
    padding: 0.3rem 0.75rem;
    font-size: 0.75rem;
    background: var(--accent, #3a3a5c);
    color: var(--fg, #e0e0e0);
    border: 1px solid var(--accent-border, #4a4a7a);
    border-radius: 4px;
    cursor: pointer;
    white-space: nowrap;
  }

  .run-btn:hover:not(:disabled) {
    background: var(--accent-hover, #4a4a7a);
  }

  .run-btn:disabled {
    opacity: 0.5;
    cursor: default;
  }

  .placeholder {
    padding: 1.5rem 1rem;
    font-size: 0.78rem;
    color: var(--fg-dim, #555);
    text-align: center;
  }

  .placeholder.error {
    color: var(--red, #e05555);
  }

  .table-wrap {
    overflow-x: auto;
    flex: 1;
  }

  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.75rem;
  }

  th {
    padding: 0.4rem 0.6rem;
    text-align: right;
    color: var(--fg-dim, #666);
    font-weight: 500;
    border-bottom: 1px solid var(--border, #2a2a2a);
    cursor: pointer;
    white-space: nowrap;
    user-select: none;
  }

  th:first-child {
    text-align: left;
  }

  th.active {
    color: var(--fg, #e0e0e0);
  }

  .sort-arrow {
    font-size: 0.65rem;
    margin-left: 0.2rem;
  }

  td {
    padding: 0.35rem 0.6rem;
    text-align: right;
    border-bottom: 1px solid var(--border-faint, #1e1e1e);
    color: var(--fg-dim, #aaa);
    white-space: nowrap;
  }

  td:first-child {
    text-align: left;
    color: var(--fg, #e0e0e0);
    font-weight: 500;
  }

  td.pos { color: var(--green, #4caf50); }
  td.neg { color: var(--red, #e05555); }

  tbody tr:hover td {
    background: var(--row-hover, #1f1f2a);
  }

  .row-count {
    padding: 0.3rem 0.8rem;
    font-size: 0.68rem;
    color: var(--fg-dim, #444);
    text-align: right;
    border-top: 1px solid var(--border, #2a2a2a);
  }
</style>
