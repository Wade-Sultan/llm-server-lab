"use client"

import type { AssistantTransportCommand } from "@assistant-ui/react"
import {
  AssistantRuntimeProvider,
  makeAssistantDataUI,
  useAssistantTransportRuntime,
  useAuiState,
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
import { CaseOptionsCard } from "@/components/assistant-ui/case-card"
import { getAccessToken } from "@/hooks/useAuth"
import { usePipelineStatusStore } from "@/hooks/usePipelineStatus"
import type { FeedbackRating } from "@/lib/feedback"
import type { BuildData, CaseOptionsData } from "@/types/build"
import {
  type ChatAgentState,
  commandsToMessages,
  createConverter,
  initialAgentState,
  pipelineMessage,
} from "./converter"
import { type AgentTransition, diffAgentState } from "./transitions"

const BuildDataUI = makeAssistantDataUI({ name: "build", render: BuildCard })
const CaseOptionsUI = makeAssistantDataUI({
  name: "case_options",
  render: CaseOptionsCard,
})

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

export interface ChatConversation {
  /**
   * The id this chat posts under — which exists before the conversation does.
   * A new chat mints one client-side (see conversationIdRef) and the backend
   * creates the row under it on the first turn, so the BuildCard can rate a
   * build without waiting for a round trip to learn the id.
   */
  id: string
  /** The rating already on this conversation, or null. Server state at load. */
  initialFeedback: FeedbackRating | null
}

const ChatConversationContext = createContext<ChatConversation | null>(null)

export function useChatConversation() {
  return useContext(ChatConversationContext)
}

/**
 * Renders children as belonging to no conversation.
 *
 * Every route under (main) mounts a chat runtime, so a page that merely
 * *reuses* a chat component — the shared-build page rendering BuildCard — sits
 * inside a ChatConversationContext for a conversation that has nothing to do
 * with it, and in the shared case does not exist at all. Without this, the
 * card offers feedback thumbs that POST a rating against a random id.
 */
export function OutsideConversation({ children }: { children: ReactNode }) {
  return (
    <ChatConversationContext.Provider value={null}>
      {children}
    </ChatConversationContext.Provider>
  )
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
      feedback: FeedbackRating | null
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
            metadata?: { build?: BuildData; case_options?: CaseOptionsData }
          }>
        ).filter((m) => m.role === "user" || m.role === "assistant")

        // History is rehydrated as agent state rather than as thread messages,
        // because state is what the transport round-trips: the next turn POSTs
        // this back, which is how the backend reconstructs the conversation
        // without re-reading Postgres.
        setState({
          status: "ready",
          meta: { title: data.title, created_at: data.created_at },
          feedback: data.feedback?.rating ?? null,
          initialState: {
            // Each build stays on the message it was persisted against, so a
            // conversation containing several of them renders all of them, each
            // under the turn that produced it.
            messages: persisted.map((m) => ({
              role: m.role as "user" | "assistant",
              content: m.content ?? "",
              build: m.metadata?.build ?? null,
              // Persisted with `chosen` always set (see save_turn), so history
              // renders a locked picker, never one still soliciting a click.
              case_options: m.metadata?.case_options ?? null,
            })),
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
        initialFeedback={state.feedback}
      >
        {children}
      </ChatRuntimeMount>
    </ConversationMetaContext.Provider>
  )
}

/**
 * Fold commands the server never accepted back into state, and stop the clock.
 *
 * `pipeline: null` matters as much as the messages. The progress line is driven
 * by transitions out of state (see AgentStateSync), so a turn that dies with a
 * step still set emits no `pipeline-idle` and leaves "Selecting a GPU…" pinned
 * under a turn that is no longer running.
 */
function restoreCommands(
  state: ChatAgentState,
  commands: AssistantTransportCommand[],
): ChatAgentState {
  return {
    ...state,
    messages: [...(state.messages ?? []), ...commandsToMessages(commands)],
    pipeline: null,
  }
}

function ChatRuntimeMount({
  conversationId,
  initialState,
  initialFeedback = null,
  children,
}: {
  conversationId?: string
  initialState?: ChatAgentState
  initialFeedback?: FeedbackRating | null
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
    // Without this the runtime never attaches `onEdit`, and the external-store
    // core's `beginEdit` throws "Runtime does not support editing." The Edit
    // button is not capability-gated — it calls straight through — so the throw
    // surfaced as a button that silently did nothing.
    //
    // Enabling it here is only half of editing. The runtime sends the edited
    // message as a plain `add-message` plus a `parentId`, so the server is what
    // decides that an edit REPLACES history rather than appending to it — see
    // `rewind_prefix` in backend/app/services/transport.py.
    capabilities: { edit: true },
    // WHY BOTH OF THESE EXIST. On failure or cancellation the runtime calls
    // `commandQueue.reset()` and then hands the dropped commands to these
    // callbacks. Leaving them unset does not merely lose an error message: the
    // dropped commands stop appearing in `pendingCommands`, so the optimistic
    // echo vanishes and the message the user just typed disappears from the
    // thread with no explanation — on a fresh conversation, straight back to
    // the welcome screen.
    //
    // Putting the commands into state is what keeps them on screen, and it is
    // safe against duplication by construction: these receive only commands the
    // server never acknowledged. `markDelivered()` clears the in-transit queue
    // the moment the first state operation arrives, so a command that reached
    // the server is already in state and is never passed here.
    onError: (error, { commands, updateState }) => {
      updateState((state) => restoreCommands(state, commands))
      console.error("chat turn failed", error)
      toast.error("Couldn't send that message. Please try again.")
    },
    onCancel: ({ commands, updateState }) => {
      updateState((state) => restoreCommands(state, commands))
    },
  })

  useEffect(() => {
    // Both stores are module singletons, so anything left set here shows up on
    // the next conversation — including a progress line queued behind the
    // store's own display-time delay, which would land after the navigation.
    return () => {
      usePipelineStatusStore.getState().reset()
    }
  }, [])

  const conversation = useMemo(
    () => ({ id: conversationIdRef.current, initialFeedback }),
    [initialFeedback],
  )

  return (
    <ChatConversationContext.Provider value={conversation}>
      <AssistantRuntimeProvider runtime={runtime}>
        <BuildDataUI />
        <CaseOptionsUI />
        <AgentStateSync />
        {children}
      </AssistantRuntimeProvider>
    </ChatConversationContext.Provider>
  )
}

/**
 * Watches the backend's state and acts on what *changed* in it.
 *
 * The distinction is the whole point. Server state is a snapshot, and a
 * snapshot cannot tell "a build just finished" from "a build is present" — so
 * anything driven off the value fires again every time the value is merely
 * observed. That is what made opening a finished build from history announce
 * "Build ready!" all over again. `diffAgentState` turns the snapshots into
 * transitions and this decides, one by one, which of them should do something.
 *
 * Rendered inside AssistantRuntimeProvider because it reads the active thread's
 * state from that context. It does nothing until a Thread is actually mounted —
 * see the note in the body.
 */
function AgentStateSync() {
  // Read defensively rather than through useAssistantTransportState, which
  // asserts the active thread belongs to a transport runtime and throws if it
  // does not. This component sits at provider level in (main)/layout.tsx, so it
  // mounts on every route under it — including /buildhistory, which never
  // renders a Thread and therefore never causes the thread-list runtime to
  // instantiate the transport thread. That assertion took the whole page down.
  const state = useAuiState((s) => {
    const extras = s.thread?.extras as { state?: ChatAgentState } | undefined
    return extras?.state ?? null
  })

  // The last state actually observed. `null` means nothing has been seen yet,
  // which is deliberately distinct from "seen an empty conversation": the first
  // observation establishes a baseline and emits no transitions, so rehydrated
  // history arrives silently while a build finishing does not.
  const previous = useRef<ChatAgentState | null>(null)

  useEffect(() => {
    if (!state) return

    const prev = previous.current
    previous.current = state
    if (prev === null) return

    for (const transition of diffAgentState(prev, state)) {
      handleTransition(transition)
    }
  }, [state])

  return null
}

/**
 * Which transitions are worth acting on, and what they do.
 *
 * Kept as a flat switch on purpose: it is the one place to look to answer "why
 * did the UI do that", and adding a side effect means adding a case here rather
 * than finding another value somewhere to watch.
 */
function handleTransition(transition: AgentTransition): void {
  switch (transition.type) {
    case "build-completed":
      // Listings for the recommended parts are fetched by BuildCard itself
      // (from the commerce service), so historical builds render without this.
      toast.success("Build ready!")
      break
    case "pipeline-step":
      usePipelineStatusStore.getState().setMessage(
        pipelineMessage({
          messages: [],
          pipeline: { step: transition.step, message: transition.message },
        }),
      )
      break
    case "pipeline-idle":
      usePipelineStatusStore.getState().setMessage(null)
      break
    case "message-added":
      // Nothing yet. Listed rather than defaulted so the next person can see
      // that it is a transition the layer already reports.
      break
  }
}

export { INITIAL_SUGGESTIONS }
