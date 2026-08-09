"use client"
import Image from "next/image"
import Link from "next/link"

import { useLogoAssets } from "@/hooks/useLogoAssets"
import { cn } from "@/lib/utils"

interface LogoProps {
  variant?: "full" | "icon" | "responsive"
  className?: string
  asLink?: boolean
}

export function Logo({
  variant = "full",
  className,
  asLink = true,
}: LogoProps) {
  const { icon: iconLogo, full: fullLogo } = useLogoAssets()

  const content =
    variant === "responsive" ? (
      <>
        <Image
          src={fullLogo}
          alt="Palladium"
          width={0}
          height={24}
          style={{ width: "auto" }}
          className={cn("group-data-[collapsible=icon]:hidden", className)}
        />
        <Image
          src={iconLogo}
          alt="Palladium"
          width={20}
          height={20}
          className={cn(
            "hidden group-data-[collapsible=icon]:block",
            className,
          )}
        />
      </>
    ) : (
      <Image
        src={variant === "full" ? fullLogo : iconLogo}
        alt="Palladium"
        width={variant === "full" ? 0 : 20}
        height={variant === "full" ? 24 : 20}
        style={variant === "full" ? { width: "auto" } : undefined}
        className={className}
      />
    )

  if (!asLink) {
    return content
  }

  return <Link href="/build/new">{content}</Link>
}
