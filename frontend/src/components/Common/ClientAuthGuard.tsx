"use client"

import { usePathname, useRouter } from "next/navigation"
import { useEffect } from "react"
import type { ReactNode } from "react"
import useAuth from "@/hooks/useAuth"

// Routes that render their own auth-aware UI instead of redirecting guests
const GUEST_ALLOWED_PATHS = ["/build/new", "/buildhistory", "/guides", "/changelog", "/findbuilder"]

export default function ClientAuthGuard({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth()
  const router = useRouter()
  const pathname = usePathname()
  const guestAllowed = GUEST_ALLOWED_PATHS.includes(pathname)

  useEffect(() => {
    if (!loading && !user && !guestAllowed) {
      router.replace("/signup")
    }
  }, [user, loading, router, guestAllowed])

  if (loading) return null
  if (!user && !guestAllowed) return null
  return <>{children}</>
}

