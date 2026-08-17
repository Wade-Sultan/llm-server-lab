/**
 * Custom assistant-transport commands.
 *
 * assistant-ui's command vocabulary is `add-message` and `add-tool-result`,
 * extended through this augmentation point. Declaring `select-case` here is
 * what lets the case picker's click ride an ordinary /chat request instead of
 * a side channel of its own — so a pick reuses the whole turn machinery
 * (worker dispatch, the Valkey event stream, resume-on-reload) rather than
 * reimplementing a second, weaker copy of it.
 *
 * The backend counterpart is `_case_pick` in backend/app/api/routes/chat.py.
 * A pick carries no message, which is why the route accepts a request whose
 * `commands` produce no text.
 */
import "@assistant-ui/react"

declare module "@assistant-ui/react" {
  namespace Assistant {
    interface Commands {
      selectCase: {
        type: "select-case"
        /** Identifies the paused build this pick resumes. */
        token: string
        /** Must be one of the three offered; the server validates. */
        caseName: string
      }
    }
  }
}
