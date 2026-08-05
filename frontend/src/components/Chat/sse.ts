/**
 * Minimal SSE frame parser over a fetch() body stream.
 *
 * WHY NOT EventSource. EventSource would give us Last-Event-ID handling for
 * free, but it can only issue GET requests and cannot set an Authorization
 * header — and the turn is started by an authenticated POST carrying the message
 * history. So the transport stays fetch(), and this module takes on the two jobs
 * EventSource would otherwise have done: reassembling frames, and tracking the
 * last event id so a reconnect can resume from it.
 *
 * WHY A CARRY BUFFER. A network chunk boundary falls wherever TCP decides, not
 * on frame boundaries, so a single `data:` line routinely arrives split across
 * two reads. Splitting each chunk on newlines independently — as the previous
 * implementation did — silently dropped those halves into a JSON parse error,
 * which showed up as tokens occasionally going missing from a response under
 * exactly the conditions hardest to reproduce locally.
 */

export interface SSEFrame {
  id: string | null
  data: string
}

/**
 * Yields complete SSE frames from a response body.
 *
 * Frames are separated by a blank line; comment lines (`:` prefix, used here for
 * keepalive pings) carry no data and are skipped without being surfaced.
 */
export async function* parseSSE(
  body: ReadableStream<Uint8Array>,
): AsyncGenerator<SSEFrame> {
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffer = ""

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })

      // A frame ends at a blank line. Everything after the last one stays in the
      // buffer until the bytes completing it arrive.
      while (true) {
        const sep = buffer.indexOf("\n\n")
        if (sep === -1) break

        const raw = buffer.slice(0, sep)
        buffer = buffer.slice(sep + 2)

        let id: string | null = null
        const dataLines: string[] = []

        for (const line of raw.split("\n")) {
          if (line.startsWith(":")) continue // keepalive comment
          if (line.startsWith("id:")) {
            id = line.slice(3).trim()
          } else if (line.startsWith("data:")) {
            dataLines.push(line.slice(5).trimStart())
          }
        }

        if (dataLines.length > 0) {
          // Multi-line data fields are joined with newlines, per the spec.
          yield { id, data: dataLines.join("\n") }
        }
      }
    }
  } finally {
    reader.releaseLock()
  }
}
