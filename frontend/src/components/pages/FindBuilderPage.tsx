"use client"

import { Lock } from "lucide-react"
import Link from "next/link"
import { UnderConstruction } from "@/components/Common/UnderConstruction"
import useAuth from "@/hooks/useAuth"

export default function FindBuilderPage() {
  const { user, loading } = useAuth()

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
              Sign in to find a builder
            </h1>
            <p className="text-sm text-muted-foreground">
              Create an account or sign in to browse and connect with local PC
              builders.
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

  return <UnderConstruction pageName="Find a Builder" />
}
