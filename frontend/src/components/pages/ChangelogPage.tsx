"use client"

import { Separator } from "@/components/ui/separator"

type ChangeType = "new" | "improved" | "fixed" | "infra"

const badgeVariant: Record<ChangeType, string> = {
  new: "bg-blue-500/15 text-blue-400 border-blue-500/30",
  improved: "bg-violet-500/15 text-violet-400 border-violet-500/30",
  fixed: "bg-amber-500/15 text-amber-400 border-amber-500/30",
  infra: "bg-emerald-500/15 text-emerald-400 border-emerald-500/30",
}

const badgeLabel: Record<ChangeType, string> = {
  new: "New",
  improved: "Improved",
  fixed: "Fixed",
  infra: "Infra",
}

interface Change {
  type: ChangeType
  text: string
}

interface Release {
  version?: string
  date?: string
  label?: string
  changes: Change[]
}

const releases: Release[] = [
  {
    date: "August 2026 and Beyond",
    label: "Upcoming Features",
    changes: [
      { type: "new", text: "Live Ebay links" },
      { type: "new", text: "Email price alerts for parts" },
      { type: "new", text: "Find a local PC builder page" },
      { type: "improved", text: "Upgraded recommendation pipeline" },
    ],
  },
  {
    date: "July 2026",
    label: "Performance Improvements",
    changes: [{ type: "improved", text: "First chat turn load time" }],
  },
  {
    date: "June 2026",
    label: "Live Links and more",
    changes: [
      {
        type: "new",
        text: "Live Amazon links",
      },
      {
        type: "new",
        text: "PC Building Guides",
      },
    ],
  },
  {
    date: "May 2026",
    label: "User Login and Chat History",
    changes: [
      {
        type: "new",
        text: "User Login/Signup",
      },
      {
        type: "new",
        text: "Chat history and ability to view old conversations",
      },
      {
        type: "new",
        text: "Guest user mode",
      },
    ],
  },
  {
    date: "April 2026",
    label: "Beta Release",
    changes: [
      {
        type: "new",
        text: "Launched Palladium, an AI-powered PC parts recommendation platform to make choosing parts easier than ever before.",
      },
      {
        type: "new",
        text: "Translates user intent into a list of components with database-enforced compatability checks along the way.",
      },
    ],
  },
]

function ChangeBadge({ type }: { type: ChangeType }) {
  return (
    <span
      className={`inline-flex items-center rounded-md border px-2 py-0.5 text-[10px] font-semibold tracking-wide ${badgeVariant[type]}`}
    >
      {badgeLabel[type]}
    </span>
  )
}

export default function ChangelogPage() {
  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-2xl px-6 py-10">
        <div className="mb-8">
          <h1 className="text-2xl font-semibold tracking-tight">Changelog</h1>
          <p className="mt-1.5 text-sm text-muted-foreground">
            A history of Palladium
          </p>
        </div>

        <div className="space-y-10">
          {releases.map((release, i) => (
            <div key={release.date ?? release.label ?? i}>
              <div className="mb-4 flex items-baseline gap-3">
                <span className="font-mono text-sm font-semibold text-foreground">
                  v{release.version}
                </span>
                {release.label && (
                  <span className="text-sm font-medium text-foreground">
                    {release.label}
                  </span>
                )}
                <span className="ml-auto text-xs text-muted-foreground">
                  {release.date}
                </span>
              </div>

              <ul className="space-y-2.5">
                {release.changes.map((change, j) => (
                  <li
                    key={j}
                    className="flex items-start gap-3 text-sm text-muted-foreground"
                  >
                    <ChangeBadge type={change.type} />
                    <span className="leading-relaxed">{change.text}</span>
                  </li>
                ))}
              </ul>

              {i < releases.length - 1 && <Separator className="mt-10" />}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
