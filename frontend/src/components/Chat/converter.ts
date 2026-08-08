import type {
  AssistantTransportConnectionMetadata,
  DataMessagePart,
  ThreadMessageLike,
} from "@assistant-ui/react"
import { unstable_createMessageConverter as createMessageConverter } from "@assistant-ui/react"
import type { ReadonlyJSONValue } from "assistant-stream/utils"

import type { BuildData } from "@/hooks/useConversationState"
import { stepMessage } from "./pipeline-steps"

/**
 * The state shape the backend owns.
 *
 * Mirrors `initial_state()` in backend/app/services/transport.py. `build` and
 * `pipeline` are state rather than events, which is the substantive change from
 * the SSE adapter this replaced: the browser no longer accumulates them as they
 * stream past, so a reconnect mid-build gets the BuildCard and the progress line
 * back without having kept anything.
 */
export interface ChatMessageState {
  role: "user" | "assistant"
  content: string
}

export interface ChatAgentState {
  messages: ChatMessageState[]
  build: BuildData | null
  pipeline: { step?: string | null; message?: string | null } | null
}

export const initialAgentState: ChatAgentState = {
  messages: [],
  build: null,
  pipeline: null,
}

/**
 * Attach the BuildCard to the message it belongs to.
 *
 * The card is rendered by `makeAssistantDataUI({name: "build"})`, which looks
 * for a data part of that name — the same shape the SSE adapter produced and the
 * same shape `ConversationLoader` rebuilds from persisted metadata, so BuildCard
 * itself is untouched by this migration.
 *
 * It hangs off the last assistant message because that is the turn that produced
 * it; a build never arrives without one.
 */
/** One message plus, on the turn that produced it, the build it produced. */
interface RenderableMessage extends ChatMessageState {
  build?: BuildData | null
}

/**
 * `toThreadMessages` does the assistant-ui bookkeeping — ids, statuses, message
 * metadata — that a hand-built `ThreadMessageLike[]` does not satisfy. This
 * callback only has to say what each message *is*.
 */
const messageConverter = createMessageConverter<RenderableMessage>(
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
    const messages = state.messages ?? []
    const lastAssistant = messages.reduce(
      (acc, m, i) => (m.role === "assistant" ? i : acc),
      -1,
    )

    const renderable: RenderableMessage[] = messages.map((message, i) =>
      i === lastAssistant ? { ...message, build: state.build } : message,
    )

    // Optimistic echo: commands the runtime has queued but not yet sent. Without
    // this the user's own message vanishes between pressing enter and the server
    // acknowledging it.
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
      // Re-exposed so `useAssistantTransportState` can read build and pipeline.
      // Cast because the option is typed as plain JSON and ChatAgentState is an
      // interface without an index signature; the value really is JSON — it
      // round-trips to the server on every request.
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
