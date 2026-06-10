"use client"

import { usePathname, useRouter } from "next/navigation"
import { useEffect, useRef } from "react"
import type { ReactNode } from "react"
import useAuth from "@/hooks/useAuth"
import { wakeUpBuilder } from "@/lib/wake-up-builder"

// Routes that render their own auth-aware UI instead of redirecting guests
const GUEST_ALLOWED_PATHS = ["/build/new", "/buildhistory"]

export default function ClientAuthGuard({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth()
  const router = useRouter()
  const pathname = usePathname()
  const guestAllowed = GUEST_ALLOWED_PATHS.includes(pathname)
  const hasWokenUp = useRef(false)

  useEffect(() => {
    if (!loading && !user && !guestAllowed) {
      router.replace("/signup")
    }
  }, [user, loading, router, guestAllowed])

  useEffect(() => {
    if (!loading && user && !hasWokenUp.current) {
      hasWokenUp.current = true
      wakeUpBuilder()
    }
  }, [user, loading])

  if (loading) return null
  if (!user && !guestAllowed) return null
  return <>{children}</>
}
