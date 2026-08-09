"use client"

import { ExternalLink } from "lucide-react"
import { useState } from "react"
import { Input } from "@/components/ui/input"
import type { GuideVideo } from "@/lib/guides"

import "lite-youtube-embed/src/lite-yt-embed.js"
import "lite-youtube-embed/src/lite-yt-embed.css"

function VideoCard({ video }: { video: GuideVideo }) {
  return (
    <div className="flex flex-col gap-2">
      <div className="flex justify-center">
        {video.youtube_video_id ? (
          /* @ts-expect-error lite-youtube is a custom element */
          <lite-youtube
            videoid={video.youtube_video_id}
            title={video.title}
            className="rounded-lg shadow-lg"
            style={{ width: "100%" }}
          />
        ) : (
          // A link the admin saved that isn't a YouTube URL — still listed, but
          // it opens externally instead of playing inline.
          <a
            href={video.url}
            target="_blank"
            rel="noopener noreferrer"
            className="flex aspect-video w-full items-center justify-center gap-2 rounded-lg border bg-muted/40 text-sm text-muted-foreground shadow-lg transition-colors hover:bg-muted"
          >
            Watch externally
            <ExternalLink className="size-3.5" />
          </a>
        )}
      </div>
      <p className="truncate px-2 text-center text-sm font-medium text-muted-foreground">
        {video.title}
      </p>
    </div>
  )
}

export function VideoGuides({ videos }: { videos: GuideVideo[] }) {
  const [searchQuery, setSearchQuery] = useState("")

  const filteredVideos = videos.filter((video) =>
    video.title.toLowerCase().includes(searchQuery.toLowerCase()),
  )

  return (
    <div className="flex flex-col gap-6 py-8 px-4">
      {/* Search Bar */}
      <div className="w-full max-w-md mx-auto">
        <Input
          type="text"
          placeholder="Search videos by title..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
        />
      </div>

      {/* 3-Column Scrollable Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6 max-h-[75vh] overflow-y-auto px-2 pb-4">
        {filteredVideos.map((video) => (
          <VideoCard key={video.id} video={video} />
        ))}
      </div>

      {videos.length === 0 && (
        <p className="text-center text-muted-foreground mt-8">
          No guides yet — check back soon.
        </p>
      )}

      {/* No results message */}
      {videos.length > 0 && filteredVideos.length === 0 && (
        <p className="text-center text-muted-foreground mt-8">
          No videos found matching &quot;{searchQuery}&quot;
        </p>
      )}
    </div>
  )
}
