"use client"

import { ArrowLeft } from "lucide-react"
import { useRouter } from "next/navigation"
import { ChatInterface } from "@/components/Chat/chatinterface"
import { useConversationMeta } from "@/components/Chat/chatruntimeprovider"

export default function ConversationPage() {
  const router = useRouter()
  const meta = useConversationMeta()

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center gap-3 px-6 py-4 border-b shrink-0">
        <button
          type="button"
          onClick={() => router.push("/buildhistory")}
          className="rounded-md p-1 hover:bg-accent text-muted-foreground"
          aria-label="Back to builds"
        >
          <ArrowLeft className="h-4 w-4" />
        </button>
        <div className="min-w-0">
          <h1 className="font-semibold truncate">
            {meta?.title ?? "Untitled Build"}
          </h1>
          {meta && (
            <p className="text-xs text-muted-foreground">
              {new Date(meta.created_at).toLocaleDateString(undefined, {
                year: "numeric",
                month: "long",
                day: "numeric",
              })}
            </p>
          )}
        </div>
      </div>

      <div className="flex-1 min-h-0">
        <ChatInterface />
      </div>
    </div>
  )
}
