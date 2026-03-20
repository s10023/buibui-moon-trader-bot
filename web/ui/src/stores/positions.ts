import { writable } from "svelte/store";
import {
  createSSEStream,
  type PositionsResponse,
  type PositionsStreamFrame,
} from "../api";

export const positionsStore = writable<PositionsResponse>({
  positions: [],
  wallet_balance: 0,
  unrealized_pnl: 0,
  available_balance: 0,
  total_risk_usd: 0,
});
export const positionsConnected = writable(false);

let cleanup: (() => void) | null = null;

export function startPositionsSSE(): void {
  cleanup = createSSEStream<PositionsStreamFrame>(
    "/api/stream/positions",
    (frame) => { positionsStore.set(frame); },
    () => positionsConnected.set(false),
    () => positionsConnected.set(true)
  );
}

export function stopPositionsSSE(): void {
  cleanup?.();
  cleanup = null;
  positionsConnected.set(false);
}
