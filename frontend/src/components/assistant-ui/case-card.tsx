"use client"

import type { DataMessagePartComponent } from "@assistant-ui/react"
import {
  useAssistantTransportSendCommand,
  useAuiState,
} from "@assistant-ui/react"
import { CheckIcon, PcCaseIcon } from "lucide-react"
import { useState } from "react"
import type { ChatAgentState } from "@/components/Chat/converter"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { cn } from "@/lib/utils"
import type { CaseOption, CaseOptionsData } from "@/types/build"

/**
 * Whether a turn is in flight right now.
 *
 * Read off the backend's own state — a running turn always has a `pipeline`
 * step set, and the transport clears it when the turn ends — rather than from
 * local component state, which a remount would lose. That distinction has
 * teeth here: the picker sits on a message that stays on screen while the
 * resumed turn streams a new one in below it, and a second pick would be
 * refused by the paused build's one-shot claim and surface as "this session
 * expired" on a build that is finishing perfectly well.
 */
function useTurnRunning(): boolean {
  return useAuiState((s) => {
    const extras = s.thread?.extras as { state?: ChatAgentState } | undefined
    return Boolean(extras?.state?.pipeline)
  })
}

const formatPrice = (cents: number) =>
  (cents / 100).toLocaleString("en-US", { style: "currency", currency: "USD" })

/**
 * The case photo, with a Skeleton standing in until the browser has actually
 * painted it. `loaded` starts false even for cached images — onLoad still
 * fires for those, just synchronously enough that the skeleton never shows.
 *
 * The credit line is not decoration: images are sourced from manufacturer
 * press/product pages, and displaying them under an attribution (linked to the
 * source when known) is the licensing basis for having them at all — see the
 * image_* columns on pc_parts.
 */
function CaseImage({ option }: { option: CaseOption }) {
  const [loaded, setLoaded] = useState(false)
  const [failed, setFailed] = useState(false)

  if (!option.image_url || failed) {
    // No image sourced for this case (or it 404ed): a quiet pictogram rather
    // than a broken image or an endless skeleton.
    return (
      <div className="flex aspect-square w-full items-center justify-center rounded-lg bg-muted">
        <PcCaseIcon className="size-10 text-muted-foreground/50" />
      </div>
    )
  }

  return (
    <div className="relative aspect-square w-full overflow-hidden rounded-lg bg-muted">
      {!loaded && <Skeleton className="absolute inset-0" />}
      {/* Plain <img>: the src is an external GCS URL at natural card size, so
          next/image would need remotePatterns config for optimization that
          doesn't pay off on a fixed small square. */}
      <img
        src={option.image_url}
        alt={option.name}
        className={cn(
          "size-full object-contain transition-opacity duration-300",
          loaded ? "opacity-100" : "opacity-0",
        )}
        onLoad={() => setLoaded(true)}
        onError={() => setFailed(true)}
      />
      {option.image_credit && (
        <span className="absolute right-1 bottom-1 rounded bg-background/70 px-1 py-0.5 text-[10px] text-muted-foreground">
          {option.image_source_url ? (
            <a
              href={option.image_source_url}
              target="_blank"
              rel="noopener noreferrer"
              className="hover:underline"
            >
              {option.image_credit}
            </a>
          ) : (
            option.image_credit
          )}
        </span>
      )}
    </div>
  )
}

function CaseCard({
  option,
  rank,
  chosen,
  pending,
  locked,
  onChoose,
}: {
  option: CaseOption
  rank: number
  /** The picker's resolved choice, or null while the build is still paused. */
  chosen: string | null
  /** Name submitted and awaiting the stream's confirmation, or null. */
  pending: string | null
  /** A turn is running, so no further pick may be started. */
  locked: boolean
  onChoose: (name: string) => void
}) {
  const isChosen = chosen === option.name
  const closed = chosen !== null || pending !== null || locked

  return (
    <Card
      className={cn(
        "flex flex-col gap-3 py-4",
        isChosen && "border-primary",
        // Once a different case is locked in, the also-rans recede.
        chosen !== null && !isChosen && "opacity-60",
      )}
    >
      <CardHeader className="px-4">
        <CaseImage option={option} />
        <div className="flex items-start justify-between gap-2 pt-2">
          <CardTitle className="text-sm leading-snug">{option.name}</CardTitle>
          {rank === 0 && (
            <Badge variant="secondary" className="shrink-0">
              Best value
            </Badge>
          )}
        </div>
      </CardHeader>
      <CardContent className="flex-1 px-4">
        <p className="text-muted-foreground text-xs">{option.reason}</p>
      </CardContent>
      <CardFooter className="flex items-center justify-between gap-2 px-4">
        <div className="flex flex-col">
          {option.approx_price != null && (
            <span className="text-sm font-medium">
              ~{formatPrice(option.approx_price)}*
            </span>
          )}
          {option.size && (
            <span className="text-muted-foreground text-xs uppercase">
              {option.size}
            </span>
          )}
        </div>
        <Button
          type="button"
          size="sm"
          variant={isChosen ? "default" : "outline"}
          disabled={closed && !isChosen}
          aria-pressed={isChosen}
          onClick={() => onChoose(option.name)}
        >
          {isChosen ? (
            <>
              <CheckIcon className="size-4" /> Selected
            </>
          ) : pending === option.name ? (
            "Choosing…"
          ) : (
            "Choose"
          )}
        </Button>
      </CardFooter>
    </Card>
  )
}

/**
 * The mid-build case picker — three CaseCards for the options the pipeline
 * paused on. Rendered by makeAssistantDataUI({name: "case_options"}).
 *
 * PICKING STARTS A TURN. The click sends a `select-case` command, which the
 * runtime delivers on an ordinary /chat request; the server resumes the saved
 * pipeline and streams the finished build back as a new assistant message.
 * That is why this is a command rather than a side-channel POST — a resumed
 * build gets the same worker dispatch, the same event stream and the same
 * reattach-after-reload as any other turn, for free.
 *
 * THE CARD DOES NOT DECIDE ITS OWN OUTCOME. `chosen` is set by the server's
 * re-emit of this data part, not locally, so what the card shows can never
 * disagree with the case the pipeline actually built around. `pending` only
 * bridges the gap between the click and that emit.
 */
export const CaseOptionsCard: DataMessagePartComponent<CaseOptionsData> = (
  props,
) => {
  const data = props.data as CaseOptionsData
  const sendCommand = useAssistantTransportSendCommand()
  const turnRunning = useTurnRunning()
  const [pending, setPending] = useState<string | null>(null)

  const choose = (name: string) => {
    if (data.chosen !== null || pending !== null || turnRunning) return
    setPending(name)
    // Failure surfaces through the runtime's own onError (a toast, and the
    // command handed back), so there is nothing to catch here.
    sendCommand({ type: "select-case", token: data.token, caseName: name })
  }

  return (
    <div className="my-2 flex w-full max-w-(--thread-max-width) flex-col gap-2">
      <div>
        <p className="text-sm font-medium">
          {data.chosen === null ? "Pick your case" : "Case options"}
        </p>
        <p className="text-muted-foreground text-xs">
          {data.chosen === null
            ? "All three fit your parts — this one's about looks and space. Your build finishes once you choose."
            : "The build was finished with the selected case."}
        </p>
      </div>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
        {data.options.map((option, rank) => (
          <CaseCard
            key={option.part_id || option.name}
            option={option}
            rank={rank}
            chosen={data.chosen}
            pending={pending}
            locked={turnRunning}
            onChoose={choose}
          />
        ))}
      </div>
      <p className="text-muted-foreground px-1 text-xs">
        *Approximate street prices
      </p>
    </div>
  )
}
