"use client"

import {
  AssistantRuntimeProvider,
  makeAssistantDataUI,
  useLocalRuntime,
} from "@assistant-ui/react"
import type { ReactNode } from "react"
import { useEffect, useMemo, useRef, useState } from "react"
import { toast } from "sonner"

import { BuildCard } from "@/components/assistant-ui/build-card"
import { getAccessToken } from "@/hooks/useAuth"
import { useConversationStateStore } from "@/hooks/useConversationState"
import { createModelAdapter } from "./model-adapter"

const BuildDataUI = makeAssistantDataUI({ name: "build", render: BuildCard })

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"

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
  const adapter = useMemo(
    () => createModelAdapter(conversationIdRef.current),
    [],
  )

  const runtime = useLocalRuntime(adapter, {
    initialMessages: [],
  })

  const phase = useConversationStateStore((s) => s.phase)
  const buildData = useConversationStateStore((s) => s.buildData)
  const [_listings, setListings] = useState<Record<string, unknown[]>>({})

  useEffect(() => {
    if (phase === "complete" && buildData) {
      toast.success("Build ready!")

      // Fetch listings for each recommended part
      const fetchListingsForParts = async () => {
        const token = await getAccessToken()
        const headers: Record<string, string> = {
          "Content-Type": "application/json",
        }
        if (token) headers.Authorization = `Bearer ${token}`

        const results: Record<string, unknown[]> = {}

        await Promise.allSettled(
          buildData.parts.map(async (part) => {
            try {
              const response = await fetch(
                `${API_BASE}/api/v1/listings/?part_id=${part.part_id}`,
                {
                  headers,
                },
              )

              if (response.ok) {
                const data = await response.json()
                results[part.component] = data.data ?? []
              }
            } catch {
              // Silently fail for individual parts
              results[part.component] = []
            }
          }),
        )

        setListings(results)
      }

      fetchListingsForParts()
    }
  }, [phase, buildData])

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <BuildDataUI />
      {children}
    </AssistantRuntimeProvider>
  )
}

export { INITIAL_SUGGESTIONS }
