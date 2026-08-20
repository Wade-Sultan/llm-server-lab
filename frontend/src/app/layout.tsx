import { Analytics } from "@vercel/analytics/next"
import { SpeedInsights } from "@vercel/speed-insights/next"
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

// Impact's site-verification snippet uses a non-standard `value` attribute
// instead of `content`, which neither Next's Metadata API nor React's `<meta>`
// types will emit. The cast is what lets the attribute through verbatim.
const impactSiteVerification = {
  name: "impact-site-verification",
  value: "0c78d9ac-952a-4533-82e5-d2ad88e82a4e",
} as React.MetaHTMLAttributes<HTMLMetaElement>

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
    <html
      lang="en"
      suppressHydrationWarning
      className={`${raleway.variable} ${dmSans.variable}`}
    >
      <head>
        <meta {...impactSiteVerification} />
        {/*
          Applies the stored theme before first paint, so a dark-mode user does
          not get a white flash while React hydrates. It has to be an inline
          blocking script in <head> for that ordering; a component effect runs
          too late by definition.

          The XSS the rule guards against needs attacker-controlled input in the
          string. This one is a build-time constant: nothing is interpolated,
          and the only value it reads (localStorage) is used in string
          comparisons, never written to the DOM. Keep it that way — the moment
          this template gains a `${...}`, the suppression stops being true.
        */}
        <script
          // biome-ignore lint/security/noDangerouslySetInnerHtml: static, non-interpolated anti-FOUC script; see comment above
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
        <SpeedInsights />
      </body>
    </html>
  )
}
