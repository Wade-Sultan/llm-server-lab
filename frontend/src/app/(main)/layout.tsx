import type React from "react"
import "@/index.css"

import { ChatRuntimeProvider } from "@/components/Chat/chatruntimeprovider"
import ClientAuthGuard from "@/components/Common/ClientAuthGuard"
import AppSidebar from "@/components/Sidebar/AppSidebar"
import {
  SidebarInset,
  SidebarProvider,
  SidebarTrigger,
} from "@/components/ui/sidebar"

export default function MainLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <SidebarProvider>
      <AppSidebar />
      <SidebarInset className="h-svh overflow-hidden">
        <header className="sticky top-0 z-10 flex h-16 shrink-0 items-center gap-2 border-b px-4">
          <SidebarTrigger className="-ml-1 text-muted-foreground" />
        </header>
        <main className="flex-1 min-h-0">
          <ClientAuthGuard>
            <ChatRuntimeProvider>{children}</ChatRuntimeProvider>
          </ClientAuthGuard>
        </main>
      </SidebarInset>
    </SidebarProvider>
  )
}
