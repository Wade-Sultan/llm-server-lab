/**
 * Display copy for pipeline progress indicators.
 *
 * The backend emits `{type: "progress", step: "gpu", message: "Choosing GPU…"}`.
 * The `step` is the stable identifier and the `message` is a convenience copy of
 * whatever the Python happened to say. This table is the authority, so changing
 * user-facing wording is a frontend deploy rather than a backend one, and the
 * strings sit somewhere a translation pass can reach them.
 *
 * `message` remains the fallback for any step added to the pipeline that has not
 * been given an entry here yet — a new step should show slightly-off copy, never
 * nothing at all.
 *
 * Step names come from _emit() in
 * backend/app/services/recommender/dspy_pipeline.py and the two run_chat_turn
 * emits in backend/app/services/chat_pipeline.py.
 */
export const PIPELINE_STEP_MESSAGES: Record<string, string> = {
  // chat_pipeline.py — the outer recommendation path
  resolving: "Building your PC…",
  presenting: "Preparing your recommendation…",

  // dspy_pipeline.py — per-component selection, in pipeline order
  ddr: "Building your PC…",
  cpu: "Choosing your CPU…",
  cooler: "Picking a cooler…",
  motherboard: "Selecting a motherboard…",
  ram: "Choosing RAM…",
  storage: "Selecting storage…",
  gpu: "Choosing GPU…",
  psu: "Calculating power supply…",
  case: "Picking case options for you…",
  fans: "Checking airflow…",
}

export function stepMessage(
  step: string | undefined,
  fallback: string | null | undefined,
): string | null {
  if (step && PIPELINE_STEP_MESSAGES[step]) return PIPELINE_STEP_MESSAGES[step]
  return fallback ?? null
}
