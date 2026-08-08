"use client"

import { useAui } from "@assistant-ui/react"
import { useCallback } from "react"
import { toast } from "sonner"

import { type SiteMode, useSiteModeStore } from "@/hooks/useSiteMode"

/**
 * Typing a code into the composer flips the site instead of sending a message.
 *
 * THESE ARE NOT SECRETS, which is why they carry the `NEXT_PUBLIC_` prefix
 * rather than reading as server config. The check happens in the browser, so
 * both values are inlined into the JS bundle at build time and anyone can read
 * them out of devtools in about ten seconds. That is inherent to the feature —
 * a code that never reaches the server cannot be verified by the server — not a
 * shortcut taken here. Never put a value here that matters if it leaks.
 *
 * Inlined *at build time*, not read at runtime: changing either one means a
 * rebuild, not a restart. `NEXT_PUBLIC_` is also what makes them exist in the
 * browser at all; without the prefix Next leaves them out and both read as
 * `undefined`, so the codes would silently never match.
 */
const CODES: ReadonlyArray<
  readonly [code: string | undefined, mode: SiteMode]
> = [
  [process.env.NEXT_PUBLIC_SECRET_MESSAGE_1, "degenerate"],
  [process.env.NEXT_PUBLIC_SECRET_MESSAGE_0, "normal"],
]

const ANNOUNCEMENTS: Record<SiteMode, string> = {
  degenerate: "you degenerate",
  normal: "ok good",
}

export function matchSecretCode(text: string): SiteMode | null {
  for (const [code, mode] of CODES) {
    if (code && text === code) return mode
  }
  return null
}

/**
 * An event handler for a composer's submit paths: swallows a code, and passes
 * everything else through untouched.
 *
 * ONE HOOK, FOUR PROPS. A composer has two independent ways to submit — Enter
 * calls `form.requestSubmit()` and lands on Root's `onSubmit`, while the send
 * button is a `type="button"` whose `onClick` calls send() directly and never
 * touches the form — and the thread composer and the edit composer each have
 * both. Guarding three of the four leaves the code sendable by the fourth.
 *
 * `preventDefault()` is what suppresses the send. Both props are merged with
 * Radix's `composeEventHandlers(ours, theirs)`, which runs ours first and skips
 * theirs once the event is defaulted, so the library never sees the submit.
 *
 * WORKS UNMODIFIED IN THE EDIT COMPOSER, because `aui.composer()` resolves to
 * whichever composer is in scope: the thread's under the viewport footer, and a
 * message's edit composer under a MessagePrimitive.Root. That is the same
 * resolution ComposerPrimitive.Input relies on to know which text to show.
 *
 * Editing stays non-destructive throughout — `setText` writes a draft field and
 * never the stored message — so cancelling an edit whose text this cleared
 * still restores the original. See the note on Cancel in thread.tsx.
 *
 * Fires on every code, not only ones that change the mode. Sending "67" while
 * already degenerate re-announces and stays put; the alternative leaks the code
 * into the conversation exactly when the user has lost track of which mode they
 * are in.
 */
export function useSecretCodeInterceptor(): (event: {
  preventDefault: () => void
}) => void {
  const aui = useAui()

  return useCallback(
    (event: { preventDefault: () => void }) => {
      const composer = aui.composer()
      const mode = matchSecretCode(composer.getState().text)
      if (mode === null) return

      event.preventDefault()
      // Cleared so the code "disappears" rather than riding along on whatever
      // the user types next.
      composer.setText("")
      useSiteModeStore.getState().setMode(mode)
      toast(ANNOUNCEMENTS[mode])
    },
    [aui],
  )
}
