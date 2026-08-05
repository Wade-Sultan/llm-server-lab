"use client"

import { useState } from "react"
import { Input } from "@/components/ui/input"

import "lite-youtube-embed/src/lite-yt-embed.js"
import "lite-youtube-embed/src/lite-yt-embed.css"

interface VideoData {
  videoId: string
  title: string
}

const videos: VideoData[] = [
  { videoId: "s1fxZ-VWs2U", title: "PC Building Guide 2024" },
  { videoId: "gNMQFT2HAiY", title: "How to install Windows 11" },
  { videoId: "Ogd1HT9v4Rs", title: "Intel vs AMD CPU" },
]

interface VideoCardProps {
  videoId: string
  title: string
}

function VideoCard({ videoId, title }: VideoCardProps) {
  return (
    <div className="flex flex-col gap-2">
      <div className="flex justify-center">
        {/* @ts-expect-error lite-youtube is a custom element */}
        <lite-youtube
          videoid={videoId}
          title={title}
          className="rounded-lg shadow-lg"
          style={{ width: "100%" }}
        />
      </div>
      <p className="text-center text-sm font-medium text-muted-foreground truncate px-2">
        {title}
      </p>
    </div>
  )
}

export function VideoGuides() {
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
          <VideoCard
            key={video.videoId}
            videoId={video.videoId}
            title={video.title}
          />
        ))}
      </div>

      {/* No results message */}
      {filteredVideos.length === 0 && (
        <p className="text-center text-muted-foreground mt-8">
          No videos found matching &quot;{searchQuery}&quot;
        </p>
      )}
    </div>
  )
}
