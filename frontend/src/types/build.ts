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
  /**
   * Token of the public shared_builds snapshot created alongside this build.
   * Backs the "copy link" and "download PDF" actions. Absent on builds
   * generated before the feature (and when the snapshot write failed).
   */
  share_token?: string | null
}

/**
 * One of the three cases the pipeline offers mid-build. Enriched from the
 * catalog (see _enrich_case_options in backend chat_pipeline.py); everything
 * beyond name + reason is best-effort and may be null when the model named a
 * case the catalog couldn't resolve.
 */
export interface CaseOption {
  name: string
  reason: string
  part_id: string
  /** Street price PER UNIT in cents, like RecommendedPart.approx_price. */
  approx_price: number | null
  size: string | null
  image_url: string | null
  /** Attribution line for the image, e.g. "Image: Fractal Design". */
  image_credit: string | null
  image_source_url: string | null
}

/**
 * The case picker's wire shape. `chosen` is null while the pipeline is
 * waiting; once set (by the user's pick or the timeout fallback) the picker is
 * closed — including on history replays, which only ever see the closed state.
 */
export interface CaseOptionsData {
  token: string
  chosen: string | null
  options: CaseOption[]
}
