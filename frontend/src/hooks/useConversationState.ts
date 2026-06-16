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
  aiHasSpoken: boolean
  setTurn: (turn: ConversationTurn) => void
  setPhase: (phase: ConversationPhase) => void
  setAiHasSpoken: (aiHasSpoken: boolean) => void
  reset: () => void
}

const INITIAL: Pick<ConversationStateStore, "turn" | "phase" | "aiHasSpoken"> = {
  turn: "user",
  phase: "gathering",
  aiHasSpoken: false,
}

export const useConversationStateStore = create<ConversationStateStore>((set) => ({
  ...INITIAL,
  setTurn: (turn) => set({ turn }),
  setPhase: (phase) => set({ phase }),
  setAiHasSpoken: (aiHasSpoken) => set({ aiHasSpoken }),
  reset: () => set(INITIAL),
}))
