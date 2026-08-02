import type { ChatModelAdapter, DataMessagePart } from "@assistant-ui/react"
import { getAccessToken } from "@/hooks/useAuth"
import type { BuildData } from "@/hooks/useConversationState"
import { useConversationStateStore } from "@/hooks/useConversationState"
import { usePipelineStatusStore } from "@/hooks/usePipelineStatus"
import { stepMessage } from "./pipeline-steps"
import { parseSSE } from "./sse"

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"

/**
 * How many times to reconnect to a turn's stream before giving up.
 *
 * A reconnect only ever resumes — the turn keeps running on a worker regardless
 * of whether this browser is attached — so retrying is cheap and never
 * re-triggers the model. The cap exists for the case the stream is genuinely
 * gone (expired past TURN_STREAM_TTL_S, or the turn id is stale), where the
 * server answers 404 and retrying cannot help.
 */
const MAX_RESUMES = 5

/** Backoff before resuming. Short: the turn is still running while we wait. */
const RESUME_DELAY_MS = 750

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

export function createModelAdapter(conversationId: string): ChatModelAdapter {
  return {
    async *run({ messages, abortSignal }) {
      const { setMessage } = usePipelineStatusStore.getState()
      const { setTurn, setPhase, setBuildData, aiHasSpoken } =
        useConversationStateStore.getState()

      setTurn("ai")
      // Only show "Booting up" on the very first AI turn
      if (!aiHasSpoken) {
        setMessage("Booting up")
      }

      const token = await getAccessToken()
      const headers: Record<string, string> = {
        "Content-Type": "application/json",
      }
      if (token) headers.Authorization = `Bearer ${token}`

      let response: Response
      try {
        response = await fetch(`${API_BASE}/api/v1/chat`, {
          method: "POST",
          headers,
          body: JSON.stringify({
            conversation_id: token ? conversationId : null,
            messages: messages.map((m) => ({
              role: m.role,
              content: m.content
                .filter(
                  (p): p is { type: "text"; text: string } => p.type === "text",
                )
                .map((p) => p.text)
                .join("\n"),
            })),
          }),
          signal: abortSignal,
        })
      } catch (err) {
        setMessage(null)
        setTurn("user")
        throw err
      }

      if (!response.ok) {
        setMessage(null)
        setTurn("user")
        throw new Error(
          `Chat API returned ${response.status}: ${response.statusText}`,
        )
      }

      // State survives across reconnects — that is the entire point. `fullText`
      // holds what has already been rendered, and `lastEventId` is where the
      // server should resume from, so a dropped connection costs a round trip
      // rather than the whole build.
      let fullText = ""
      let tokenReceived = false
      let buildPart: DataMessagePart<BuildData> | null = null
      let turnId: string | null = null
      let lastEventId: string | null = null
      let completed = false
      let resumes = 0

      try {
        let body: ReadableStream<Uint8Array> | null = response.body

        while (!completed) {
          if (!body) throw new Error("No response body")

          try {
            for await (const frame of parseSSE(body)) {
              if (frame.id) lastEventId = frame.id

              if (frame.data === "[DONE]") {
                completed = true
                break
              }

              let parsed: {
                type?: string
                turn_id?: string
                step?: string
                message?: string
                text?: string
                content?: string
                data?: BuildData
              }
              try {
                parsed = JSON.parse(frame.data)
              } catch {
                continue // non-JSON frame, skip
              }

              if (parsed.type === "turn") {
                // Sent first by the server; the handle we reconnect with.
                turnId = parsed.turn_id ?? null
              } else if (parsed.type === "progress") {
                // progress events only appear on the recommendation path, never during elicitation
                setPhase("recommending")
                // `step` is the stable identifier; the server's `message` is
                // only a fallback for steps this build doesn't know yet.
                setMessage(stepMessage(parsed.step, parsed.message))
              } else if (parsed.type === "build") {
                setPhase("complete")
                setBuildData(parsed.data as BuildData)
                buildPart = {
                  type: "data" as const,
                  name: "build",
                  data: parsed.data as BuildData,
                }
                yield {
                  content: [
                    { type: "text" as const, text: fullText },
                    buildPart,
                  ],
                }
              } else if (parsed.type === "token" || parsed.type === "content") {
                if (!tokenReceived) {
                  tokenReceived = true
                  setMessage(null)
                }
                fullText += parsed.text ?? parsed.content ?? ""
                yield {
                  content: buildPart
                    ? [{ type: "text" as const, text: fullText }, buildPart]
                    : [{ type: "text" as const, text: fullText }],
                }
              }
            }

            // The body ended without a [DONE]. The connection dropped mid-turn
            // rather than the turn finishing, so resume rather than accepting a
            // truncated answer as complete.
            if (!completed && !turnId) {
              // No turn id means the server ran this turn inline (no Valkey),
              // where there is nothing to resume — the turn died with the
              // connection. Surfacing what we have beats an error.
              break
            }
          } catch (err) {
            if (abortSignal?.aborted) throw err
            if (!turnId) throw err
            // Fall through to the resume path below.
          }

          if (completed) break
          if (abortSignal?.aborted) break

          if (resumes >= MAX_RESUMES) {
            console.warn(
              `Gave up resuming turn ${turnId} after ${resumes} attempts`,
            )
            break
          }
          resumes += 1
          await sleep(RESUME_DELAY_MS)

          const resumeUrl =
            `${API_BASE}/api/v1/chat/${turnId}/stream` +
            `?resume=${encodeURIComponent(lastEventId ?? "0")}`

          let resumed: Response
          try {
            // No Authorization header: the turn id is the capability, and a
            // token may well have expired while the tab was backgrounded, which
            // is exactly when this path runs.
            resumed = await fetch(resumeUrl, { signal: abortSignal })
          } catch {
            continue // network still down; retry until the cap
          }

          if (resumed.status === 404) {
            // Stream expired or unknown. Nothing to resume; keep what we have.
            break
          }
          if (!resumed.ok) break

          body = resumed.body
        }

        // Mark that AI has spoken after the first complete response
        if (!aiHasSpoken) {
          useConversationStateStore.getState().setAiHasSpoken(true)
        }
      } finally {
        setMessage(null)
        setTurn("user")
      }
    },
  }
}
