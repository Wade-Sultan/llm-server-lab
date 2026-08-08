"use client"

import {
  AssistantRuntimeProvider,
  makeAssistantDataUI,
  useAssistantTransportRuntime,
  useAssistantTransportState,
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
import {
  type BuildData,
  useConversationStateStore,
} from "@/hooks/useConversationState"
import { usePipelineStatusStore } from "@/hooks/usePipelineStatus"
import {
  type ChatAgentState,
  createConverter,
  initialAgentState,
  pipelineMessage,
} from "./converter"

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
      initialState: ChatAgentState
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

        const persisted = (
          data.messages as Array<{
            id: string
            role: string
            content: string | null
            created_at: string
            metadata?: { build?: BuildData }
          }>
        ).filter((m) => m.role === "user" || m.role === "assistant")

        // History is rehydrated as agent state rather than as thread messages,
        // because state is what the transport round-trips: the next turn POSTs
        // this back, which is how the backend reconstructs the conversation
        // without re-reading Postgres.
        setState({
          status: "ready",
          meta: { title: data.title, created_at: data.created_at },
          initialState: {
            messages: persisted.map((m) => ({
              role: m.role as "user" | "assistant",
              content: m.content ?? "",
            })),
            // The most recent persisted build. Only the newest is carried
            // forward — the converter hangs it off the last assistant message,
            // and older builds stay where BuildCard's own fetch can reach them.
            build:
              persisted.reduce<BuildData | null>(
                (acc, m) => m.metadata?.build ?? acc,
                null,
              ) ?? null,
            pipeline: null,
          },
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
        initialState={state.initialState}
      >
        {children}
      </ChatRuntimeMount>
    </ConversationMetaContext.Provider>
  )
}

function ChatRuntimeMount({
  conversationId,
  initialState,
  children,
}: {
  conversationId?: string
  initialState?: ChatAgentState
  children: ReactNode
}) {
  const conversationIdRef = useRef<string>(
    conversationId ?? crypto.randomUUID(),
  )
  const converter = useMemo(() => createConverter(), [])

  const runtime = useAssistantTransportRuntime<ChatAgentState>({
    api: `${API_BASE}/api/v1/chat`,
    // Reconnects to a turn that is still running on a worker — after a reload,
    // a tab switch, or a dropped connection. This is what the 218-line manual
    // retry loop in the old model-adapter used to do; the backend resolves
    // which turn from the conversation id, since the protocol sends no run id.
    resumeApi: `${API_BASE}/api/v1/chat/resume`,
    initialState: initialState ?? initialAgentState,
    converter,
    // Async, and re-read per request on purpose: a Firebase ID token lives an
    // hour, and a long build outlasts one.
    headers: async (): Promise<Record<string, string>> => {
      const token = await getAccessToken()
      return token ? { Authorization: `Bearer ${token}` } : {}
    },
    // Our conversation id, not assistant-ui's threadId — that tracks its own
    // thread list, which this app does not use.
    body: { conversation_id: conversationIdRef.current },
  })

  useEffect(() => {
    return () => {
      useConversationStateStore.getState().reset()
    }
  }, [])

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <BuildDataUI />
      <AgentStateSync />
      {children}
    </AssistantRuntimeProvider>
  )
}

/**
 * Mirrors server state into the two Zustand stores the surrounding UI reads.
 *
 * These used to be written by `model-adapter.ts` as it parsed SSE frames, which
 * meant the progress line and the build phase were client-side accumulations
 * that a reconnect lost. They are derived from server state now; the stores
 * remain only because other components already subscribe to them.
 *
 * Rendered inside AssistantRuntimeProvider because useAssistantTransportState
 * reads from that context.
 */
function AgentStateSync() {
  // The hook is typed against a generic external-state record; the shape is
  // whatever `initialState` established, which is ChatAgentState.
  const state = useAssistantTransportState(
    (s) => s as unknown as ChatAgentState,
  )
  const phase = useConversationStateStore((s) => s.phase)

  useEffect(() => {
    usePipelineStatusStore.getState().setMessage(pipelineMessage(state))
  }, [state])

  useEffect(() => {
    const store = useConversationStateStore.getState()
    if (state.build) {
      store.setBuildData(state.build)
      store.setPhase("complete")
    } else if (state.pipeline) {
      // Progress only ever appears on the recommendation path, never during
      // elicitation — so its presence is what distinguishes the two.
      store.setPhase("recommending")
    }
  }, [state])

  // Listings for the recommended parts are fetched by BuildCard itself (from
  // the commerce service), so they work for historical builds too.
  useEffect(() => {
    if (phase === "complete") {
      toast.success("Build ready!")
    }
  }, [phase])

  return null
}

export { INITIAL_SUGGESTIONS }
