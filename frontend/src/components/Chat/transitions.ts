import type { BuildData } from "@/types/build"
import type { ChatAgentState, ChatMessageState } from "./converter"

/**
 * Turning the backend's state into things that *happened*.
 *
 * WHY THIS EXISTS. The backend owns the conversation and streams snapshots of
 * it; the client renders whatever it is told. That is the right shape for
 * rendering and the wrong shape for side effects, because a snapshot cannot
 * distinguish "a build just finished" from "a build is present" — and a toast,
 * a sound, or an analytics event only ever wants the first. Firing on the
 * second is exactly the bug where opening any finished build from history
 * announced "Build ready!" all over again.
 *
 * So state is tracked here across renders and diffed into discrete transitions.
 * Every consumer then gets to decide, explicitly, which of them should actually
 * do something — rather than inferring it from a value that happens to be set.
 *
 * The first observation of a conversation produces NO transitions. Rehydrated
 * history is a starting position, not news; that single rule is what makes
 * loading an old build silent while finishing a new one is not.
 */
export type AgentTransition =
  /** A build landed on a turn that did not have one. The reason toasts fire. */
  | { type: "build-completed"; index: number; build: BuildData }
  /** The pipeline moved to a new step, or started one. */
  | { type: "pipeline-step"; step: string | null; message: string | null }
  /** The pipeline stopped — the turn ended, errored, or produced its build. */
  | { type: "pipeline-idle" }
  /** A message was added. `role` says by whom. */
  | { type: "message-added"; index: number; role: ChatMessageState["role"] }

function samePipeline(
  a: ChatAgentState["pipeline"],
  b: ChatAgentState["pipeline"],
): boolean {
  if (a === b) return true
  if (!a || !b) return false
  return a.step === b.step && a.message === b.message
}

/**
 * What changed between two observations of the backend's state.
 *
 * Message scanning starts one before the previous length rather than at zero.
 * A turn only ever appends messages and mutates the one it is streaming into,
 * so that window covers every real change — and this runs on every token, so
 * walking a long conversation each time would be work done purely to find
 * nothing.
 */
export function diffAgentState(
  prev: ChatAgentState,
  next: ChatAgentState,
): AgentTransition[] {
  const out: AgentTransition[] = []
  const prevMessages = prev.messages ?? []
  const nextMessages = next.messages ?? []

  for (
    let i = Math.max(0, prevMessages.length - 1);
    i < nextMessages.length;
    i++
  ) {
    const before = prevMessages[i]
    const after = nextMessages[i]
    if (!after) continue

    if (!before) {
      out.push({ type: "message-added", index: i, role: after.role })
    }
    if (after.build && !before?.build) {
      out.push({ type: "build-completed", index: i, build: after.build })
    }
  }

  if (!samePipeline(prev.pipeline, next.pipeline)) {
    out.push(
      next.pipeline
        ? {
            type: "pipeline-step",
            step: next.pipeline.step ?? null,
            message: next.pipeline.message ?? null,
          }
        : { type: "pipeline-idle" },
    )
  }

  return out
}
