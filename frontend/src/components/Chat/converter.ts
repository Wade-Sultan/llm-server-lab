import type {
  AssistantTransportConnectionMetadata,
  DataMessagePart,
  ThreadMessageLike,
} from "@assistant-ui/react"
import { unstable_createMessageConverter as createMessageConverter } from "@assistant-ui/react"
import type { ReadonlyJSONValue } from "assistant-stream/utils"

import type { BuildData } from "@/types/build"
import { stepMessage } from "./pipeline-steps"

/**
 * The state shape the backend owns.
 *
 * Mirrors `initial_state()` in backend/app/services/transport.py. The build is
 * state rather than an event, which is the substantive change from the SSE
 * adapter this replaced: the browser no longer accumulates it as it streams
 * past, so a reconnect mid-build gets the BuildCard back without having kept
 * anything.
 *
 * `build` hangs off the message that produced it rather than sitting in a
 * top-level slot. A conversation can contain several builds, and a single slot
 * both loses the older ones and re-attaches the survivor to whichever assistant
 * message happens to be last — so asking a follow-up after a build dragged the
 * card down onto the reply.
 */
export interface ChatMessageState {
  role: "user" | "assistant"
  content: string
  build?: BuildData | null
}

export interface ChatAgentState {
  messages: ChatMessageState[]
  pipeline: { step?: string | null; message?: string | null } | null
}

export const initialAgentState: ChatAgentState = {
  messages: [],
  pipeline: null,
}

/**
 * `toThreadMessages` does the assistant-ui bookkeeping — ids, statuses, message
 * metadata — that a hand-built `ThreadMessageLike[]` does not satisfy. This
 * callback only has to say what each message *is*.
 *
 * The BuildCard is rendered by `makeAssistantDataUI({name: "build"})`, which
 * looks for a data part of that name — the same shape `ConversationLoader`
 * rebuilds from persisted metadata, so BuildCard itself is untouched.
 */
const messageConverter = createMessageConverter<ChatMessageState>(
  (message): ThreadMessageLike => {
    if (message.role !== "assistant" || !message.build) {
      return { role: message.role, content: message.content }
    }

    const buildPart: DataMessagePart<BuildData> = {
      type: "data",
      name: "build",
      data: message.build,
    }
    return {
      role: "assistant",
      content: [{ type: "text", text: message.content }, buildPart],
    }
  },
)

export function createConverter() {
  return (
    state: ChatAgentState,
    metadata: AssistantTransportConnectionMetadata,
  ) => {
    const renderable: ChatMessageState[] = [...(state.messages ?? [])]

    // Optimistic echo: commands the runtime has queued but not yet sent. This
    // covers only the gap before the request goes out — once the first state
    // operation lands, the runtime drops these and the server's copy of the
    // same message takes over (see `_append_pending` in transport.py).
    for (const command of metadata.pendingCommands) {
      if (command.type !== "add-message") continue
      // `parts` is a union across user (text|image) and assistant (text)
      // messages, so narrowing has to be structural rather than by a type
      // predicate over one branch.
      const text = (command.message.parts as readonly { type: string }[])
        .filter((p): p is { type: "text"; text: string } => p.type === "text")
        .map((p) => p.text)
        .join("\n")
      if (text) renderable.push({ role: command.message.role, content: text })
    }

    return {
      messages: messageConverter.toThreadMessages(
        renderable,
        metadata.isSending,
      ),
      isRunning: metadata.isSending,
      // Re-exposed so the transition layer can read messages and pipeline back
      // out. Cast because the option is typed as plain JSON and ChatAgentState
      // is an interface without an index signature; the value really is JSON —
      // it round-trips to the server on every request.
      state: state as unknown as ReadonlyJSONValue,
    }
  }
}

/**
 * The progress line, resolved through the local copy table.
 *
 * `step` is the stable identifier and the server's `message` is only a fallback
 * for steps this build has not been taught yet — so wording changes stay a
 * frontend deploy. See pipeline-steps.ts.
 */
export function pipelineMessage(state: ChatAgentState): string | null {
  if (!state.pipeline) return null
  return stepMessage(
    state.pipeline.step ?? undefined,
    state.pipeline.message ?? null,
  )
}
