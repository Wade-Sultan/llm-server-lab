"use client"

import type { LucideIcon } from "lucide-react"
import {
  BookOpen,
  ChevronRight,
  Hammer,
  Info,
  MessagesSquare,
  Newspaper,
  Sparkles,
} from "lucide-react"
import Link from "next/link"
import { usePathname } from "next/navigation"
import { useState } from "react"
import { FaGithub } from "react-icons/fa"

import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
import {
  SidebarGroup,
  SidebarGroupContent,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarMenuSub,
  SidebarMenuSubButton,
  SidebarMenuSubItem,
  useSidebar,
} from "@/components/ui/sidebar"

type Item = {
  icon: LucideIcon
  title: string
  path: string
}

const items: Item[] = [
  { icon: Hammer, title: "New Build", path: "/build/new" },
  { icon: MessagesSquare, title: "My Builds", path: "/buildhistory" },
  { icon: BookOpen, title: "Guides", path: "/guides" },
  { icon: Info, title: "About", path: "/about" },
]

type MoreItem = {
  icon: LucideIcon | typeof FaGithub
  title: string
  /** Internal route (Next <Link>) — mutually exclusive with `href`. */
  path?: string
  /** External URL, opened in a new tab. */
  href?: string
}

// The "More" group is deliberately its own list rather than an entry spliced
// into `items` at a fixed index: it used to render only when the index matched
// the "Find a Builder" row, so commenting that row out silently deleted the
// whole group.
const moreItems: MoreItem[] = [
  {
    icon: FaGithub,
    title: "GitHub",
    href: "https://github.com/Wade-Sultan/palladium-pc",
  },
  { icon: Newspaper, title: "Blog", path: "/blog" },
]

export function Main() {
  const { isMobile, setOpenMobile } = useSidebar()
  const currentPath = usePathname()
  // Open by default when the current page lives under "More", so the active
  // item isn't hidden inside a collapsed group.
  const [moreOpen, setMoreOpen] = useState(() =>
    moreItems.some((i) => i.path && currentPath.startsWith(i.path)),
  )

  const handleMenuClick = () => {
    if (isMobile) {
      setOpenMobile(false)
    }
  }

  return (
    <SidebarGroup>
      <SidebarGroupContent>
        <SidebarMenu>
          {items.map((item) => {
            const isActive =
              currentPath === item.path ||
              // Highlight "My Builds" when viewing a past conversation
              (item.path === "/buildhistory" &&
                currentPath.startsWith("/build/") &&
                currentPath !== "/build/new")

            return (
              <SidebarMenuItem key={item.title}>
                <SidebarMenuButton
                  tooltip={item.title}
                  isActive={isActive}
                  asChild
                >
                  <Link href={item.path} onClick={handleMenuClick}>
                    <item.icon />
                    <span>{item.title}</span>
                  </Link>
                </SidebarMenuButton>
              </SidebarMenuItem>
            )
          })}

          <Collapsible open={moreOpen} onOpenChange={setMoreOpen} asChild>
            <SidebarMenuItem>
              <CollapsibleTrigger asChild>
                <SidebarMenuButton tooltip="More">
                  <Sparkles />
                  <span>More</span>
                  <ChevronRight
                    className="ml-auto size-4 transition-transform duration-200 data-[state=open]:rotate-90"
                    data-state={moreOpen ? "open" : "closed"}
                  />
                </SidebarMenuButton>
              </CollapsibleTrigger>
              <CollapsibleContent>
                <SidebarMenuSub>
                  {moreItems.map((item) => (
                    <SidebarMenuSubItem key={item.title}>
                      <SidebarMenuSubButton
                        asChild
                        isActive={
                          !!item.path && currentPath.startsWith(item.path)
                        }
                      >
                        {item.path ? (
                          <Link href={item.path} onClick={handleMenuClick}>
                            <item.icon className="size-3.5" />
                            <span>{item.title}</span>
                          </Link>
                        ) : (
                          <a
                            href={item.href}
                            target="_blank"
                            rel="noopener noreferrer"
                            onClick={handleMenuClick}
                          >
                            <item.icon className="size-3.5" />
                            <span>{item.title}</span>
                          </a>
                        )}
                      </SidebarMenuSubButton>
                    </SidebarMenuSubItem>
                  ))}
                </SidebarMenuSub>
              </CollapsibleContent>
            </SidebarMenuItem>
          </Collapsible>
        </SidebarMenu>
      </SidebarGroupContent>
    </SidebarGroup>
  )
}
