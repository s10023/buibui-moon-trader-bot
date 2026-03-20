<script lang="ts">
  let { label = "Loading..." }: { label?: string } = $props();
</script>

<div class="spinner-wrap">
  <div class="dots">
    <span></span>
    <span></span>
    <span></span>
  </div>
  <span class="label">{label}</span>
</div>

<style>
  .spinner-wrap {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 28px 0;
  }

  .dots {
    display: flex;
    gap: 5px;
    align-items: center;
  }

  .dots span {
    display: block;
    width: 5px;
    height: 5px;
    border-radius: 50%;
    background: var(--accent);
    animation: pulse-dot 1.2s ease-in-out infinite both;
  }

  .dots span:nth-child(2) { animation-delay: 0.2s; }
  .dots span:nth-child(3) { animation-delay: 0.4s; }

  @keyframes pulse-dot {
    0%, 80%, 100% { opacity: 0.15; transform: scale(0.8); }
    40%            { opacity: 1;    transform: scale(1.1); }
  }

  .label {
    font-size: 11px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--muted);
  }
</style>
