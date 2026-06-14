import { create } from "zustand"

interface PipelineStatusStore {
  message: string | null
  setMessage: (msg: string | null) => void
}

export const usePipelineStatusStore = create<PipelineStatusStore>((set) => ({
  message: null,
  setMessage: (message) => set({ message }),
}))
