"use client"

import { VideoGuides } from "@/components/pages/VideoGuides"
import type { GuideVideo } from "@/lib/guides"

export default function GuidesPage({ videos }: { videos: GuideVideo[] }) {
  return <VideoGuides videos={videos} />
}
