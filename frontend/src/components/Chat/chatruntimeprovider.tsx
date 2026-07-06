"use client"

import type { ThreadMessageLike } from "@assistant-ui/react"
import {
  AssistantRuntimeProvider,
  makeAssistantDataUI,
  useLocalRuntime,
} from "@assistant-ui/react"
import { useParams, useRouter } from "next/navigation"
import type { ReactNode } from "react"
import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react"
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

export interface ConversationMeta {
  title: string | null
  created_at: string
}

const ConversationMetaContext = createContext<ConversationMeta | null>(null)

export function useConversationMeta() {
  return useContext(ConversationMetaContext)
}

interface ChatRuntimeProviderProps {
  children: ReactNode
}

export function ChatRuntimeProvider({ children }: ChatRuntimeProviderProps) {
  const params = useParams<{ id?: string }>()
  const conversationId = typeof params?.id === "string" ? params.id : undefined

  if (conversationId) {
    return (
      <ConversationLoader key={conversationId} conversationId={conversationId}>
        {children}
      </ConversationLoader>
    )
  }

  return <ChatRuntimeMount key="new">{children}</ChatRuntimeMount>
}

type LoaderState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | {
      status: "ready"
      meta: ConversationMeta
      initialMessages: ThreadMessageLike[]
    }

function ConversationLoader({
  conversationId,
  children,
}: {
  conversationId: string
  children: ReactNode
}) {
  const router = useRouter()
  const [state, setState] = useState<LoaderState>({ status: "loading" })

  useEffect(() => {
    let cancelled = false

    const load = async () => {
      try {
        const token = await getAccessToken()
        const res = await fetch(
          `${API_BASE}/api/v1/conversations/${conversationId}`,
          { headers: token ? { Authorization: `Bearer ${token}` } : {} },
        )
        if (res.status === 404) {
          router.replace("/buildhistory")
          return
        }
        if (!res.ok) throw new Error(`${res.status}`)
        const data = await res.json()
        if (cancelled) return

        setState({
          status: "ready",
          meta: { title: data.title, created_at: data.created_at },
          initialMessages: (
            data.messages as Array<{
              id: string
              role: string
              content: string | null
              created_at: string
            }>
          )
            .filter((m) => m.role === "user" || m.role === "assistant")
            .map((m) => ({
              id: m.id,
              role: m.role as "user" | "assistant",
              content: m.content ?? "",
              createdAt: new Date(m.created_at),
            })),
        })
      } catch {
        if (!cancelled) {
          setState({
            status: "error",
            message: "Failed to load this conversation.",
          })
        }
      }
    }

    load()
    return () => {
      cancelled = true
    }
  }, [conversationId, router])

  if (state.status === "loading") return null

  if (state.status === "error") {
    return (
      <div className="flex h-full items-center justify-center p-8">
        <p className="text-sm text-destructive">{state.message}</p>
      </div>
    )
  }

  return (
    <ConversationMetaContext.Provider value={state.meta}>
      <ChatRuntimeMount
        conversationId={conversationId}
        initialMessages={state.initialMessages}
      >
        {children}
      </ChatRuntimeMount>
    </ConversationMetaContext.Provider>
  )
}

function ChatRuntimeMount({
  conversationId,
  initialMessages,
  children,
}: {
  conversationId?: string
  initialMessages?: ThreadMessageLike[]
  children: ReactNode
}) {
  const conversationIdRef = useRef<string>(
    conversationId ?? crypto.randomUUID(),
  )
  const adapter = useMemo(
    () => createModelAdapter(conversationIdRef.current),
    [],
  )

  const runtime = useLocalRuntime(adapter, {
    initialMessages: initialMessages ?? [],
  })

  const phase = useConversationStateStore((s) => s.phase)
  const buildData = useConversationStateStore((s) => s.buildData)
  const [_listings, setListings] = useState<Record<string, unknown[]>>({})

  useEffect(() => {
    return () => {
      useConversationStateStore.getState().reset()
    }
  }, [])

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
