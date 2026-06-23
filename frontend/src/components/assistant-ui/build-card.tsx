"use client"

import type { DataMessagePartComponent } from "@assistant-ui/react"
import { ShoppingCartIcon } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import type { BuildData } from "@/hooks/useConversationState"

const formatPrice = (value: number) =>
  value.toLocaleString("en-US", { style: "currency", currency: "USD" })

export const BuildCard: DataMessagePartComponent<BuildData> = (props) => {
  const data = props.data as BuildData

  return (
    <Card className="aui-build-card my-2 w-full max-w-(--thread-max-width)">
      <CardHeader>
        <div className="flex items-center justify-between gap-2">
          <CardTitle className="text-base">{data.label}</CardTitle>
          <Badge variant="secondary">{formatPrice(data.total_approx)}</Badge>
        </div>
        <CardDescription>{data.description}</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-2">
        {data.parts.map((part) => (
          <div
            key={part.part_id}
            className="flex items-center justify-between gap-3 rounded-lg border px-3 py-2"
          >
            <div className="min-w-0">
              <p className="text-muted-foreground text-xs uppercase tracking-wide">
                {part.component}
              </p>
              <p className="truncate text-sm font-medium">
                {part.brand} {part.model}
              </p>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <span className="text-sm font-medium">
                {formatPrice(part.approx_price)}
              </span>
              <Button
                type="button"
                variant="outline"
                size="sm"
                aria-label={`Buy ${part.brand} ${part.model} on Amazon`}
              >
                <ShoppingCartIcon className="size-4" />
                Amazon
              </Button>
            </div>
          </div>
        ))}
      </CardContent>
    </Card>
  )
}
