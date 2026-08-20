import type { Metadata } from "next"

import AboutPage from "@/components/pages/AboutPage"

export const metadata: Metadata = {
  title: "About | Palladium",
  description:
    "What Palladium is, and how its affiliate links with eBay and Amazon work.",
}

export default function Page() {
  return <AboutPage />
}
