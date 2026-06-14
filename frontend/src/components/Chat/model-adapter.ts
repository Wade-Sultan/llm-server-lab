import type { ChatModelAdapter } from "@assistant-ui/react"
import { getAccessToken } from "@/hooks/useAuth"
import { useConversationStateStore } from "@/hooks/useConversationState"
import { usePipelineStatusStore } from "@/hooks/usePipelineStatus"

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"

export function createModelAdapter(conversationId: string): ChatModelAdapter {
  return {
    async *run({ messages, abortSignal }) {
      const { setMessage } = usePipelineStatusStore.getState()
      const { setTurn, setPhase } = useConversationStateStore.getState()

      setTurn("ai")
      setMessage("Booting up")

      const token = await getAccessToken()
      const headers: Record<string, string> = { "Content-Type": "application/json" }
      if (token) headers["Authorization"] = `Bearer ${token}`

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

      const reader = response.body?.getReader()
      if (!reader) {
        setMessage(null)
        setTurn("user")
        throw new Error("No response body")
      }

      const decoder = new TextDecoder()
      let fullText = ""
      let tokenReceived = false

      try {
        while (true) {
          const { done, value } = await reader.read()
          if (done) break

          const chunk = decoder.decode(value, { stream: true })
          const lines = chunk.split("\n")

          for (const line of lines) {
            if (!line.startsWith("data: ")) continue
            const data = line.slice(6).trim()

            if (data === "[DONE]") break

            try {
              const parsed = JSON.parse(data)

              if (parsed.type === "progress") {
                // progress events only appear on the recommendation path, never during elicitation
                setPhase("recommending")
                setMessage(parsed.message ?? null)
              } else if (parsed.type === "build") {
                setPhase("complete")
              } else if (parsed.type === "token" || parsed.type === "content") {
                if (!tokenReceived) {
                  tokenReceived = true
                  setMessage(null)
                }
                fullText += parsed.text ?? parsed.content ?? ""
                yield { content: [{ type: "text" as const, text: fullText }] }
              }
            } catch {
              // Non-JSON line, skip
            }
          }
        }
      } finally {
        reader.releaseLock()
        setMessage(null)
        setTurn("user")
      }
    },
  }
}
