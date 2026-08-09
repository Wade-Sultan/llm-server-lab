import GuidesPage from "@/components/pages/GuidesPage"
import { fetchGuideVideos } from "@/lib/guides"

// Rendered per request, not at build time: the API isn't reachable from the
// build container, so a prerendered page would bake in the empty state. The
// fetch itself is still cached (see REVALIDATE_SECONDS in lib/guides.ts).
export const dynamic = "force-dynamic"

export default async function Page() {
  const videos = await fetchGuideVideos()
  return <GuidesPage videos={videos} />
}
