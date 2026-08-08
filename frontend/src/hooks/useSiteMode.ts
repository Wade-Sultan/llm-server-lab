import { create } from "zustand"

export type SiteMode = "normal" | "degenerate"

interface SiteModeStore {
  mode: SiteMode
  setMode: (mode: SiteMode) => void
}

export const useSiteModeStore = create<SiteModeStore>((set) => ({
  mode: "normal",
  setMode: (mode) => set({ mode }),
}))

/** Read the mode reactively. `useSiteMode() === "degenerate"` is the usual call. */
export function useSiteMode(): SiteMode {
  return useSiteModeStore((s) => s.mode)
}
