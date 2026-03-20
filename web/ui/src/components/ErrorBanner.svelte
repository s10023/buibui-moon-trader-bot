<script lang="ts">
  let { error }: { error: string } = $props();
</script>

<div class="error-banner">
  <span class="icon">!</span>
  <span class="msg">{error}</span>
</div>

<style>
  .error-banner {
    display: flex;
    align-items: flex-start;
    gap: 10px;
    background: var(--red-dim);
    color: var(--red);
    border: 1px solid #4a1515;
    border-left: 3px solid var(--red);
    border-radius: 3px;
    padding: 8px 12px;
    margin-bottom: 14px;
    font-size: 12px;
  }

  .icon {
    font-weight: 700;
    font-size: 13px;
    flex-shrink: 0;
    margin-top: 1px;
  }

  .msg { line-height: 1.5; }
</style>
