"use client"

import { useEffect, useState } from "react"

import { useTheme } from "@/components/theme-provider"
import { type SiteMode, useSiteMode } from "@/hooks/useSiteMode"

/**
 * Every logo the app draws, indexed by the two things that select one.
 *
 * TWO DIMENSIONS, NOT ONE. The wordmark has always varied with light/dark; it
 * now also varies with the site mode, and the two are independent — so the
 * options are a 2x2 rather than a list. The icon varies with mode only: it is a
 * single-colour mark that reads on either background, which is why there is no
 * light/dark pair of it to choose between.
 *
 * Kept as a table rather than as string interpolation over the filenames on
 * purpose. The naming is not actually regular — "combined" becomes "special",
 * "palladium-logo" becomes "palladium-logo-special" — so a computed path would
 * be a 404 discovered in production rather than a missing key discovered here.
 */
const LOGO_ASSETS = {
  normal: {
    icon: "/assets/images/palladium-logo.svg",
    light: "/assets/images/palladium-combined-light-mode.svg",
    dark: "/assets/images/palladium-combined-dark-mode.svg",
  },
  degenerate: {
    icon: "/assets/images/palladium-logo-special.svg",
    light: "/assets/images/palladium-special-light-mode.svg",
    dark: "/assets/images/palladium-special-dark-mode.svg",
  },
} as const satisfies Record<
  SiteMode,
  { icon: string; light: string; dark: string }
>

export interface LogoAssets {
  /** The bare mark. Progress indicator, collapsed sidebar. */
  icon: string
  /** Mark plus wordmark, resolved for the current theme. */
  full: string
}

/**
 * The logo pair for the current theme and site mode.
 *
 * THE `mounted` DANCE IS ABOUT THEME, NOT MODE, and it is carried over from
 * Logo.tsx rather than invented here. `resolvedTheme` depends on localStorage
 * and the OS preference, neither of which the server can know, so the first
 * client render has to match the server's guess of light and only then correct
 * itself. Reading it during that first render is what produces a hydration
 * mismatch.
 *
 * The site mode needs no such guard: it is not persisted and always starts
 * "normal", so the server and the first client render already agree.
 */
export function useLogoAssets(): LogoAssets {
  const { resolvedTheme } = useTheme()
  const mode = useSiteMode()
  const [mounted, setMounted] = useState(false)

  useEffect(() => setMounted(true), [])

  const assets = LOGO_ASSETS[mode]
  const isDark = mounted && resolvedTheme === "dark"

  return { icon: assets.icon, full: isDark ? assets.dark : assets.light }
}
