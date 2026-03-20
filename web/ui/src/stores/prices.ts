import { writable } from "svelte/store";
import { createSSEStream, type PriceRow, type PriceStreamFrame } from "../api";

export const pricesStore = writable<Map<string, PriceRow>>(new Map());
export const pricesConnected = writable(false);

let cleanup: (() => void) | null = null;

export function startPricesSSE(): void {
  cleanup = createSSEStream<PriceStreamFrame>(
    "/api/stream/prices",
    (updates) => {
      pricesStore.update((m) => {
        const next = new Map(m);
        for (const u of updates) next.set(u.symbol, u);
        return next;
      });
      pricesConnected.set(true);
    },
    () => pricesConnected.set(false)
  );
}

export function stopPricesSSE(): void {
  cleanup?.();
  cleanup = null;
}
