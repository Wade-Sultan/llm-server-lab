"use client"

import { AssistantRuntimeProvider, useLocalRuntime } from "@assistant-ui/react"
import { useEffect, useMemo, useRef } from "react"
import type { ReactNode } from "react"
import { toast } from "sonner"

import { useConversationStateStore } from "@/hooks/useConversationState"
import { createModelAdapter } from "./model-adapter"

const INITIAL_SUGGESTIONS = [
  { prompt: "I want to build a gaming PC for 1440p 144fps" },
  { prompt: "Help me build a workstation for AI model training" },
  { prompt: "I need a quiet, compact PC for video editing" },
  { prompt: "Build me a budget-friendly PC for general use and light gaming" },
]

interface ChatRuntimeProviderProps {
  children: ReactNode
}

export function ChatRuntimeProvider({ children }: ChatRuntimeProviderProps) {
  const conversationIdRef = useRef<string>(crypto.randomUUID())
  const adapter = useMemo(() => createModelAdapter(conversationIdRef.current), [])

  const runtime = useLocalRuntime(adapter, {
    initialMessages: [],
  })

  const phase = useConversationStateStore((s) => s.phase)
  useEffect(() => {
    if (phase === "complete") {
      toast.success("Build ready!")
    }
  }, [phase])

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      {children}
    </AssistantRuntimeProvider>
  )
}

export { INITIAL_SUGGESTIONS }
