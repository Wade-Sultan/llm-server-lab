import type {
  AssistantTransportCommand,
  AssistantTransportConnectionMetadata,
  DataMessagePart,
  ThreadMessageLike,
} from "@assistant-ui/react"
import { unstable_createMessageConverter as createMessageConverter } from "@assistant-ui/react"
import type { ReadonlyJSONValue } from "assistant-stream/utils"

import type { BuildData, CaseOptionsData } from "@/types/build"
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
  /** The mid-build case picker, on the turn that asked. See types/build.ts. */
  case_options?: CaseOptionsData | null
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
 * One thread message per state message, always. See ONE BUBBLE PER TURN below.
 */
const NEVER_JOIN = { joinStrategy: "none" } as const

/**
 * `toThreadMessages` does the assistant-ui bookkeeping — ids, statuses, message
 * metadata — that a hand-built `ThreadMessageLike[]` does not satisfy. This
 * callback only has to say what each message *is*.
 *
 * The BuildCard is rendered by `makeAssistantDataUI({name: "build"})`, which
 * looks for a data part of that name — the same shape `ConversationLoader`
 * rebuilds from persisted metadata, so BuildCard itself is untouched.
 *
 * ONE BUBBLE PER TURN. `convertExternalMessages` starts a new thread message
 * only when it sees a user or system message, so by default it concatenates a
 * run of consecutive assistant messages into a single bubble. Every other turn
 * here is answering something the user typed, so that default is invisible —
 * except after a case pick, which is a click on a card and therefore carries no
 * user message. The picker's turn and the turn that finishes the build are two
 * adjacent assistant messages, and joining them rendered the whole thing as one
 * reply: the case prompt, the cards, the build lead-in and the BuildCard all in
 * one bubble, with the picker wedged in the middle of prose from two different
 * turns instead of reading as the reply it is.
 *
 * `joinStrategy: "none"` keeps each state message its own bubble. That is also
 * a contract with the server, not only a cosmetic choice: assistant-ui ids a
 * converted message by its position in the *converted* list, and
 * `rewind_prefix` in backend/app/services/transport.py reads `parentId` back as
 * an index into state. Every join silently shifts the two apart, so an edit
 * made after a case pick would rewind the conversation to the wrong message.
 */
const messageConverter = createMessageConverter<ChatMessageState>(
  (message): ThreadMessageLike & { convertConfig: typeof NEVER_JOIN } => {
    if (
      message.role !== "assistant" ||
      (!message.build && !message.case_options)
    ) {
      return {
        role: message.role,
        content: message.content,
        convertConfig: NEVER_JOIN,
      }
    }

    // Picker above the card: the options appear mid-turn, before the build
    // exists, and keeping that order on replay preserves the story of the turn.
    const parts: (
      | DataMessagePart<CaseOptionsData>
      | DataMessagePart<BuildData>
    )[] = []
    if (message.case_options) {
      parts.push({
        type: "data",
        name: "case_options",
        data: message.case_options,
      })
    }
    if (message.build) {
      parts.push({ type: "data", name: "build", data: message.build })
    }
    return {
      role: "assistant",
      content: [{ type: "text", text: message.content }, ...parts],
      convertConfig: NEVER_JOIN,
    }
  },
)

/**
 * The messages a batch of `add-message` commands is asking us to add.
 *
 * Two callers, and the second is the reason this is not inlined: the optimistic
 * echo below, and the failure handlers in chatruntimeprovider.tsx, which put a
 * message the server never accepted back into state so it stays on screen. Both
 * have to agree on what a command renders as, or a failed send would redraw the
 * message differently than it was drawn while in flight.
 *
 * Mirrors `command_messages` in backend/app/services/transport.py.
 */
export function commandsToMessages(
  commands: readonly AssistantTransportCommand[],
): ChatMessageState[] {
  const out: ChatMessageState[] = []
  for (const command of commands) {
    if (command.type !== "add-message") continue
    // `parts` is a union across user (text|image) and assistant (text)
    // messages, so narrowing has to be structural rather than by a type
    // predicate over one branch.
    const text = (command.message.parts as readonly { type: string }[])
      .filter((p): p is { type: "text"; text: string } => p.type === "text")
      .map((p) => p.text)
      .join("\n")
    if (text) out.push({ role: command.message.role, content: text })
  }
  return out
}

export function createConverter() {
  return (
    state: ChatAgentState,
    metadata: AssistantTransportConnectionMetadata,
  ) => {
    // Optimistic echo: commands the runtime has queued but not yet sent. This
    // covers only the gap before the request goes out — once the first state
    // operation lands, the runtime drops these and the server's copy of the
    // same message takes over (see `_append_pending` in transport.py).
    const renderable: ChatMessageState[] = [
      ...(state.messages ?? []),
      ...commandsToMessages(metadata.pendingCommands),
    ]

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
