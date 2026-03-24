import { writable, get } from "svelte/store";

const LS_KEY = "buibui_watchlist_symbol";

function getInitialSymbol(): string {
  try {
    return localStorage.getItem(LS_KEY) ?? "";
  } catch {
    return "";
  }
}

export const selectedSymbol = writable<string>(getInitialSymbol());

selectedSymbol.subscribe((sym) => {
  try {
    localStorage.setItem(LS_KEY, sym);
  } catch {
    // ignore
  }
});

export function selectSymbol(sym: string): void {
  selectedSymbol.set(sym);
}

export function getSelectedSymbol(): string {
  return get(selectedSymbol);
}
