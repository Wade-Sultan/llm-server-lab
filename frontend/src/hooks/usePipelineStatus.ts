import { create } from "zustand"
const MIN_DISPLAY_MS = 450

interface PipelineStatusStore {
  message: string | null
  setMessage: (msg: string | null) => void
}

let lastShownAt = 0
let pendingTimer: ReturnType<typeof setTimeout> | null = null

export const usePipelineStatusStore = create<PipelineStatusStore>((set) => ({
  message: null,
  setMessage: (message) => {
    if (pendingTimer) {
      clearTimeout(pendingTimer)
      pendingTimer = null
    }

    // Clearing the status (turn ended/errored) should never be delayed.
    if (message === null) {
      lastShownAt = 0
      set({ message: null })
      return
    }

    const elapsed = Date.now() - lastShownAt
    if (elapsed >= MIN_DISPLAY_MS) {
      lastShownAt = Date.now()
      set({ message })
    } else {
      pendingTimer = setTimeout(() => {
        lastShownAt = Date.now()
        pendingTimer = null
        set({ message })
      }, MIN_DISPLAY_MS - elapsed)
    }
  },
}))
