"use client"

import { usePathname, useRouter } from "next/navigation"
import type { ReactNode } from "react"
import { useEffect } from "react"
import useAuth from "@/hooks/useAuth"

// Routes that render their own auth-aware UI instead of redirecting guests
const GUEST_ALLOWED_PATHS = [
  "/build/new",
  "/buildhistory",
  "/guides",
  "/findbuilder",
]

// Guest-allowed route trees, matched by prefix. The blog is public marketing
// content with a post per slug, so it can't be enumerated as exact paths.
// /b is the shared-build page — a share link exists precisely to be opened by
// someone without an account.
const GUEST_ALLOWED_PREFIXES = ["/blog", "/b"]

export default function ClientAuthGuard({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth()
  const router = useRouter()
  const pathname = usePathname()
  const guestAllowed =
    GUEST_ALLOWED_PATHS.includes(pathname) ||
    GUEST_ALLOWED_PREFIXES.some(
      (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`),
    )

  useEffect(() => {
    if (!loading && !user && !guestAllowed) {
      router.replace("/signup")
    }
  }, [user, loading, router, guestAllowed])

  if (loading) return null
  if (!user && !guestAllowed) return null
  return <>{children}</>
}
