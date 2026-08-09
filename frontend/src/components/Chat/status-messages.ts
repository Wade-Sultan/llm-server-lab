/**
 * Display copy for the status line while a turn is running but is not on one of
 * the fixed build steps.
 *
 * Lives in the frontend for the same reason PIPELINE_STEP_MESSAGES does: wording
 * is a frontend concern, so changing it is a frontend deploy rather than a
 * backend one, and the strings sit somewhere a translation pass can reach them.
 * See pipeline-steps.ts.
 *
 * WHY THERE IS A GAP TO FILL AT ALL. The backend emits a `progress` event only
 * once a turn is known to be building — `collect()` in
 * backend/app/services/graph/nodes.py deliberately stays silent, because a
 * progress event is what tells the client the turn is on the recommend path, and
 * that is not decided until the router runs. So every turn opens with a stretch
 * of two LLM calls that have no step to show, and an elicitation turn never gets
 * one at all. Before this, both showed a bare spinner.
 *
 * These strings are therefore the *absence* of a pipeline step, never a
 * replacement for one: the moment a real step arrives it wins. See
 * AssistantMessage in components/assistant-ui/thread.tsx.
 *
 * Trailing ellipses are intentional and match the pipeline copy. LoadingIndicator
 * strips them and animates its own dots, so a string without one renders with no
 * punctuation at all next to the animation.
 */

import type { SiteMode } from "@/hooks/useSiteMode"

/**
 * The pools, by site mode and then by which wait they cover.
 *
 * TWO DIMENSIONS. `firstTurn` versus `thinking` is about *when* in a
 * conversation the wait happens; normal versus degenerate is about what voice
 * the site is speaking in. They are independent, so this is a 2x2 rather than
 * one list with special cases in it.
 *
 * `firstTurn` is distinct from `thinking` because the first turn is the one wait
 * the user has no context for yet — nothing is on screen, so the copy carries
 * the weight of "something is happening" rather than "still going".
 *
 * The pools are deliberately NOT required to be the same length. Picking scales
 * a random fraction by whichever pool was selected, so degenerate can carry
 * three openers against normal's five without anything needing to line up.
 */
const STATUS_MESSAGES = {
  normal: {
    firstTurn: [
      "Booting up…",
      "Starting up…",
      "Warming up…",
      "Spinning up…",
      "Getting started…",
    ],
    thinking: [
      "Thinking…",
      "Pondering…",
      "Just a moment…",
      "Working on it…",
      "Mulling it over…",
      "Considering your options…",
      "Weighing it up…",
      "Give me a second…",
    ],
  },
  degenerate: {
    firstTurn: ["Locking in…", "Entering flow state…", "I am the sigma…"],
    thinking: ["Cooking…", "Bet…", "Low key…", "High key…", "Vibing…"],
  },
} as const satisfies Record<
  SiteMode,
  { firstTurn: readonly string[]; thinking: readonly string[] }
>

/**
 * One phrase from whichever pool fits this turn and this mode.
 *
 * Call this once per message rather than per render — it is random, so a caller
 * that invokes it in a render body deals a fresh phrase on every token that
 * arrives.
 */
export function pickStatusMessage(
  isFirstTurn: boolean,
  mode: SiteMode,
): string {
  const pool = STATUS_MESSAGES[mode][isFirstTurn ? "firstTurn" : "thinking"]
  return pool[Math.floor(Math.random() * pool.length)]
}
