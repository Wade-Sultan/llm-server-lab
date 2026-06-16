import { create } from "zustand"

export type ConversationTurn = "user" | "ai"

/**
 * gathering: not enough info yet; AI is asking follow-up questions
 * recommending: enough info collected; AI is selecting and presenting a build
 * complete: recommendation has been delivered to the user
 */
export type ConversationPhase = "gathering" | "recommending" | "complete"

export interface RecommendedPart {
  component: string
  brand: string
  model: string
  approx_price: number
  part_id: string
}

export interface BuildData {
  key: string
  label: string
  description: string
  total_approx: number
  parts: RecommendedPart[]
}

interface ConversationStateStore {
  turn: ConversationTurn
  phase: ConversationPhase
  aiHasSpoken: boolean
  buildData: BuildData | null
  setTurn: (turn: ConversationTurn) => void
  setPhase: (phase: ConversationPhase) => void
  setAiHasSpoken: (aiHasSpoken: boolean) => void
  setBuildData: (data: BuildData) => void
  reset: () => void
}

const INITIAL: Pick<
  ConversationStateStore,
  "turn" | "phase" | "aiHasSpoken" | "buildData"
> = {
  turn: "user",
  phase: "gathering",
  aiHasSpoken: false,
  buildData: null,
}

export const useConversationStateStore = create<ConversationStateStore>((set) => ({
  ...INITIAL,
  setTurn: (turn) => set({ turn }),
  setPhase: (phase) => set({ phase }),
  setAiHasSpoken: (aiHasSpoken: boolean) => set({ aiHasSpoken }),
  setBuildData: (data: BuildData) => set({ buildData: data }),
  reset: () => set(INITIAL),
}))
