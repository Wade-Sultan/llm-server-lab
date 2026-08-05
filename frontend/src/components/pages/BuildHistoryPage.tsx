"use client"

import { ArrowRight, Lock, MessageSquare } from "lucide-react"
import Link from "next/link"
import { useEffect, useState } from "react"
import { LoadingIndicator } from "@/components/Common/LoadingIndicator"
import useAuth, { getAccessToken } from "@/hooks/useAuth"

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"

interface ConversationSummary {
  id: string
  title: string | null
  created_at: string
  updated_at: string
  message_count: number
}

export default function BuildHistoryPage() {
  const { user, loading } = useAuth()
  const [conversations, setConversations] = useState<ConversationSummary[]>([])
  const [fetching, setFetching] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!user) return
    const fetchHistory = async () => {
      setFetching(true)
      setError(null)
      try {
        const token = await getAccessToken()
        const res = await fetch(`${API_BASE}/api/v1/conversations`, {
          headers: { Authorization: `Bearer ${token}` },
        })
        if (!res.ok) throw new Error(`Failed to fetch: ${res.status}`)
        const data = await res.json()
        setConversations(data)
      } catch {
        setError("Failed to load chat history. Please try again.")
      } finally {
        setFetching(false)
      }
    }
    fetchHistory()
  }, [user])

  if (loading) return null

  if (!user) {
    return (
      <div className="flex h-full items-center justify-center p-8">
        <div className="flex flex-col items-center gap-4 text-center max-w-sm">
          <div className="rounded-full bg-muted p-4">
            <Lock className="h-6 w-6 text-muted-foreground" />
          </div>
          <div className="space-y-1">
            <h1 className="text-lg font-medium tracking-tight">
              Sign in to view your builds
            </h1>
            <p className="text-sm text-muted-foreground">
              Your chat history is saved to your account. Create an account or
              sign in to access it.
            </p>
          </div>
          <div className="flex gap-3">
            <Link
              href="/login"
              className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90"
            >
              Sign in
            </Link>
            <Link
              href="/signup"
              className="rounded-md border px-4 py-2 text-sm font-medium hover:bg-accent"
            >
              Create account
            </Link>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-6 px-8 py-6">
      <h1 className="text-2xl font-bold tracking-tight">My Builds</h1>

      {fetching && <LoadingIndicator message="Loading your builds" />}

      {error && <p className="text-sm text-destructive">{error}</p>}

      {!fetching && !error && conversations.length === 0 && (
        <div className="flex flex-col items-center gap-4 py-16 text-center">
          <div className="rounded-full bg-muted p-4">
            <MessageSquare className="h-6 w-6 text-muted-foreground" />
          </div>
          <div className="space-y-1">
            <p className="font-medium">No builds yet</p>
            <p className="text-sm text-muted-foreground">
              Start a new build and your conversations will appear here.
            </p>
          </div>
          <Link
            href="/build/new"
            className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90"
          >
            Start a build
          </Link>
        </div>
      )}

      {!fetching && conversations.length > 0 && (
        <ul className="flex flex-col gap-2">
          {conversations.map((conv) => (
            <li key={conv.id}>
              <div className="rounded-lg border bg-card px-4 py-3 flex items-center justify-between gap-4">
                <div className="min-w-0">
                  <p className="font-medium truncate">
                    {conv.title ?? "Untitled Build"}
                  </p>
                  <p className="text-xs text-muted-foreground mt-0.5">
                    {new Date(conv.created_at).toLocaleDateString(undefined, {
                      year: "numeric",
                      month: "short",
                      day: "numeric",
                    })}
                    {" · "}
                    {conv.message_count}{" "}
                    {conv.message_count === 1 ? "message" : "messages"}
                  </p>
                </div>
                <Link
                  href={`/build/${conv.id}`}
                  className="shrink-0 rounded-full p-1.5 transition-opacity hover:opacity-80"
                  style={{ color: "oklch(0.738 0.0943 260.62)" }}
                  aria-label="View build"
                >
                  <ArrowRight className="h-4 w-4" />
                </Link>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
