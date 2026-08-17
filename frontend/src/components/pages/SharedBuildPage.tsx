"use client"

import { FileDownIcon, LinkIcon } from "lucide-react"
import { useParams } from "next/navigation"
import { useEffect, useState } from "react"
import { toast } from "sonner"
import { BuildCard } from "@/components/assistant-ui/build-card"
import { OutsideConversation } from "@/components/Chat/chatruntimeprovider"
import { LoadingIndicator } from "@/components/Common/LoadingIndicator"
import { Button } from "@/components/ui/button"
import {
  fetchSharedBuild,
  type SharedBuildResponse,
  sharedBuildPdfUrl,
  sharedBuildUrl,
} from "@/lib/share"
import type { BuildData } from "@/types/build"

/**
 * The public page behind a build's share link (/b/{token}).
 *
 * Renders the frozen shared_builds snapshot through the same BuildCard the
 * chat uses — the snapshot is shaped as BuildData minus `key`. Reusing the
 * card gets live listings and marketplace buttons for free; its feedback
 * thumbs are suppressed via OutsideConversation, since a visitor holding a
 * share link has no conversation of their own to rate.
 *
 * No auth anywhere: the token in the URL is the whole credential, and the
 * snapshot was stripped of everything conversational before it was stored.
 */
export default function SharedBuildPage() {
  const params = useParams<{ token?: string }>()
  const token = typeof params?.token === "string" ? params.token : ""

  const [state, setState] = useState<
    | { status: "loading" }
    | { status: "missing" }
    | { status: "ready"; shared: SharedBuildResponse }
  >({ status: "loading" })

  useEffect(() => {
    let cancelled = false
    if (!token) {
      setState({ status: "missing" })
      return
    }
    fetchSharedBuild(token).then((shared) => {
      if (cancelled) return
      setState(shared ? { status: "ready", shared } : { status: "missing" })
    })
    return () => {
      cancelled = true
    }
  }, [token])

  if (state.status === "loading") {
    return <LoadingIndicator message="Loading build" />
  }

  if (state.status === "missing") {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 p-8 text-center">
        <p className="font-medium">This build link doesn't exist</p>
        <p className="text-muted-foreground text-sm">
          It may have been mistyped — check the link you were sent.
        </p>
      </div>
    )
  }

  const { shared } = state
  // The BuildCard renders straight from its data prop; `key` only feeds the
  // feedback flow, which is inert outside a chat.
  const buildData: BuildData = { key: shared.token, ...shared.build }

  const copyLink = async () => {
    try {
      await navigator.clipboard.writeText(sharedBuildUrl(shared.token))
      toast.success("Build link copied")
    } catch {
      toast.error("Couldn't copy the link")
    }
  }

  return (
    <div className="mx-auto flex w-full max-w-2xl flex-col gap-4 p-4 sm:p-8">
      <div className="flex items-center justify-between gap-2">
        <div>
          <h1 className="text-xl font-semibold">Shared build</h1>
          <p className="text-muted-foreground text-sm">
            Generated {new Date(shared.created_at).toLocaleDateString()}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button type="button" variant="outline" size="sm" onClick={copyLink}>
            <LinkIcon className="size-4" /> Copy link
          </Button>
          <Button type="button" size="sm" asChild>
            <a href={sharedBuildPdfUrl(shared.token)} download>
              <FileDownIcon className="size-4" /> PDF
            </a>
          </Button>
        </div>
      </div>
      {/* Every (main) route mounts a chat runtime, so the card would otherwise
          inherit a conversation context that has nothing to do with this page
          and offer feedback thumbs against an id that does not exist.

          Only `data` is read by the card; the rest of the message-part props
          are satisfied literally since we render it outside a thread. */}
      <OutsideConversation>
        <BuildCard
          type="data"
          name="build"
          data={buildData}
          status={{ type: "complete" }}
        />
      </OutsideConversation>
    </div>
  )
}
