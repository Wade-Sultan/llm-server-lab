import { Analytics } from "@vercel/analytics/next"
import type { Metadata } from "next"
import { DM_Sans, Raleway } from "next/font/google"
import type React from "react"
import { ThemeProvider } from "@/components/theme-provider"
import { Toaster } from "@/components/ui/sonner"
import "@/index.css"

const raleway = Raleway({
  subsets: ["latin"],
  variable: "--font-raleway",
})

const dmSans = DM_Sans({
  subsets: ["latin"],
  variable: "--font-dm-sans",
})

export const metadata: Metadata = {
  title: "Palladium",
  description:
    "Palladium is an AI-powered PC building platform with strict database-enforced compatability and other tools to enhance the whole experience.",
  icons: {
    icon: "/assets/images/palladium-logo-main.png",
  },
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="en" suppressHydrationWarning className={`${raleway.variable} ${dmSans.variable}`}>
      <head>
        <script
          dangerouslySetInnerHTML={{
            __html: `(function(){try{var t=localStorage.getItem('app-theme');var d=document.documentElement;if(t==='dark'){d.classList.add('dark')}else if(t==='light'){d.classList.add('light')}else{if(window.matchMedia('(prefers-color-scheme: dark)').matches){d.classList.add('dark')}else{d.classList.add('light')}}}catch(e){}})()`,
          }}
        />
      </head>
      <body>
        <ThemeProvider>
          <div id="root">{children}</div>
          <Toaster />
        </ThemeProvider>
        <Analytics />
      </body>
    </html>
  )
}
