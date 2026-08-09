"use client"

import type { LucideIcon } from "lucide-react"
import {
  BookOpen,
  ChevronRight,
  Hammer,
  MapPin,
  MessagesSquare,
  Newspaper,
  ScrollText,
  Sparkles,
} from "lucide-react"
import Link from "next/link"
import { usePathname } from "next/navigation"
import React, { useState } from "react"
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
  { icon: MapPin, title: "Find a Builder", path: "/findbuilder" },
]

export function Main() {
  const { isMobile, setOpenMobile } = useSidebar()
  const currentPath = usePathname()
  // Open by default when the current page lives under "More", so the active
  // item isn't hidden inside a collapsed group.
  const [moreOpen, setMoreOpen] = useState(
    () => currentPath.startsWith("/blog") || currentPath === "/changelog",
  )

  const handleMenuClick = () => {
    if (isMobile) {
      setOpenMobile(false)
    }
  }

  const findBuilderIndex = items.findIndex((i) => i.path === "/findbuilder")

  return (
    <SidebarGroup>
      <SidebarGroupContent>
        <SidebarMenu>
          {items.map((item, index) => {
            const isActive =
              currentPath === item.path ||
              // Highlight "My Builds" when viewing a past conversation
              (item.path === "/buildhistory" &&
                currentPath.startsWith("/build/") &&
                currentPath !== "/build/new")

            return (
              <React.Fragment key={item.title}>
                <SidebarMenuItem>
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
                {index === findBuilderIndex && (
                  <Collapsible
                    open={moreOpen}
                    onOpenChange={setMoreOpen}
                    asChild
                  >
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
                          <SidebarMenuSubItem>
                            <SidebarMenuSubButton asChild>
                              <a
                                href="https://github.com/Wade-Sultan/palladium-pc"
                                target="_blank"
                                rel="noopener noreferrer"
                                onClick={handleMenuClick}
                              >
                                <FaGithub className="size-3.5" />
                                <span>GitHub</span>
                              </a>
                            </SidebarMenuSubButton>
                          </SidebarMenuSubItem>
                          <SidebarMenuSubItem>
                            <SidebarMenuSubButton
                              asChild
                              isActive={currentPath.startsWith("/blog")}
                            >
                              <Link href="/blog" onClick={handleMenuClick}>
                                <Newspaper className="size-3.5" />
                                <span>Blog</span>
                              </Link>
                            </SidebarMenuSubButton>
                          </SidebarMenuSubItem>
                          <SidebarMenuSubItem>
                            <SidebarMenuSubButton
                              asChild
                              isActive={currentPath === "/changelog"}
                            >
                              <Link href="/changelog" onClick={handleMenuClick}>
                                <ScrollText className="size-3.5" />
                                <span>Changelog</span>
                              </Link>
                            </SidebarMenuSubButton>
                          </SidebarMenuSubItem>
                        </SidebarMenuSub>
                      </CollapsibleContent>
                    </SidebarMenuItem>
                  </Collapsible>
                )}
              </React.Fragment>
            )
          })}
        </SidebarMenu>
      </SidebarGroupContent>
    </SidebarGroup>
  )
}
