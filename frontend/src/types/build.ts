/**
 * The build a recommendation turn produces.
 *
 * Lives here rather than beside a store because it is a wire shape, not UI
 * state: it arrives inside the agent state the backend owns (see
 * backend/app/services/transport.py) and is rendered straight from the message
 * it hangs off. Nothing accumulates it on the client.
 */
export interface RecommendedPart {
  component: string
  brand: string
  model: string
  /**
   * PER UNIT, in cents. The line total is approx_price * quantity. Null when
   * the catalog has no price for the part — a custom build resolves this from
   * the DB (see chat_pipeline._assemble_dspy_build), and a part whose group
   * carries no street price comes through unpriced rather than as zero.
   */
  approx_price: number | null
  /**
   * How many of this part the build uses. Optional because reference builds
   * predate it; treat a missing value as 1. Only ever above 1 for the roles a
   * build can hold several of (GPUs, storage, fans).
   */
  quantity?: number
  part_id: string
  amazon_url?: string | null
}

export interface BuildData {
  key: string
  label: string
  description: string
  total_approx: number
  parts: RecommendedPart[]
}
