import { derived, writable } from "svelte/store";
import { getConfig, type ConfigResponse } from "../api";

export const configStore = writable<ConfigResponse>({});
export const configError = writable<string | null>(null);

export async function loadConfig(): Promise<void> {
  try {
    configStore.set(await getConfig());
  } catch (e) {
    configError.set(String(e));
  }
}

export const symbols = derived(configStore, ($c) => Object.keys($c).sort());
