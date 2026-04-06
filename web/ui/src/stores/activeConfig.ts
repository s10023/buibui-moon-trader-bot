import { derived, writable } from "svelte/store";
import { getActiveConfig, type ActiveConfigResponse } from "../api";

export const activeConfigStore = writable<ActiveConfigResponse | null>(null);

export async function loadActiveConfig(): Promise<ActiveConfigResponse | null> {
  try {
    const cfg = await getActiveConfig();
    activeConfigStore.set(cfg);
    return cfg;
  } catch {
    // No active config — server started without --config
    return null;
  }
}

export const configName = derived(
  activeConfigStore,
  ($c) => $c?.config_name ?? null,
);

// First symbol from the active config's symbol list (used as default across tabs)
export const configDefaultSymbol = derived(
  activeConfigStore,
  ($c) => ($c?.symbols && $c.symbols.length > 0 ? $c.symbols[0] : null),
);
