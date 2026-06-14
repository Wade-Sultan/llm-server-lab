import { create } from "zustand"

export type ConversationTurn = "user" | "ai"

/**
 * gathering: not enough info yet; AI is asking follow-up questions
 * recommending: enough info collected; AI is selecting and presenting a build
 * complete: recommendation has been delivered to the user
 */
export type ConversationPhase = "gathering" | "recommending" | "complete"

interface ConversationStateStore {
  turn: ConversationTurn
  phase: ConversationPhase
  setTurn: (turn: ConversationTurn) => void
  setPhase: (phase: ConversationPhase) => void
  reset: () => void
}

const INITIAL: Pick<ConversationStateStore, "turn" | "phase"> = {
  turn: "user",
  phase: "gathering",
}

export const useConversationStateStore = create<ConversationStateStore>((set) => ({
  ...INITIAL,
  setTurn: (turn) => set({ turn }),
  setPhase: (phase) => set({ phase }),
  reset: () => set(INITIAL),
}))
