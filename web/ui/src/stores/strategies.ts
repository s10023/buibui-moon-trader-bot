import { derived, writable } from "svelte/store";
import { getStrategies, type StrategiesResponse } from "../api";

export const strategiesStore = writable<StrategiesResponse>({});
export const strategiesError = writable<string | null>(null);

export async function loadStrategies(): Promise<void> {
  try {
    strategiesStore.set(await getStrategies());
  } catch (e) {
    strategiesError.set(String(e));
  }
}

export const strategyNames = derived(strategiesStore, ($s) =>
  Object.keys($s)
    .filter((n) => n !== "seasonality")
    .sort()
);
