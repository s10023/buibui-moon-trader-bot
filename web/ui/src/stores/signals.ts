import { writable } from "svelte/store";
import type { SignalRow } from "../api";

export interface SignalWithMeta extends SignalRow {
  symbol: string;
  timeframe: string;
}

export const signalsStore = writable<SignalWithMeta[]>([]);
export const signalsLoading = writable(true);
export const signalsError = writable<string | null>(null);
export const signalsLastRefresh = writable<Date | null>(null);
