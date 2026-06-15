"use client"

import Image from "next/image"
import { useEffect, useState } from "react"

export function LoadingIndicator({ message }: { message: string }) {
  const [dots, setDots] = useState(0)

  useEffect(() => {
    const id = setInterval(() => setDots((d) => (d + 1) % 4), 500)
    return () => clearInterval(id)
  }, [])

  // Strip trailing ellipsis so animated dots are the sole source of punctuation
  const text = message.replace(/[…\.]+$/, "")

  return (
    <div className="flex items-center gap-2.5 py-1">
      <Image
        src="/assets/images/palladium-logo.svg"
        alt=""
        width={18}
        height={18}
        className="animate-[spin_8s_linear_infinite] shrink-0 opacity-70"
      />
      <span className="text-sm text-muted-foreground">
        {text}
        <span className="inline-block w-[1.5ch]">{"...".slice(0, dots)}</span>
      </span>
    </div>
  )
}
