import { create } from "zustand"

const MIN_DISPLAY_MS = 450

interface PipelineStatusStore {
  message: string | null
  setMessage: (msg: string | null) => void
  reset: () => void
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
  /**
   * Drop everything, including a delayed message that has not shown yet.
   *
   * This store is a module singleton and `pendingTimer` outlives the component
   * that queued it, so leaving a conversation mid-turn would otherwise let a
   * stale progress line fire onto whatever is on screen next.
   */
  reset: () => {
    if (pendingTimer) {
      clearTimeout(pendingTimer)
      pendingTimer = null
    }
    lastShownAt = 0
    set({ message: null })
  },
}))
