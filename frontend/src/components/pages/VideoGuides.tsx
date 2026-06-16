"use client"

import { useState } from "react"
import { Input } from "@/components/ui/input"

interface VideoData {
  videoId: string
  title: string
}

const videos: VideoData[] = [
  { videoId: "dQw4w9WgXcQ", title: "Rick Astley" },
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
        <iframe
          width="100%"
          style={{ aspectRatio: "16/9" }}
          src={`https://www.youtube.com/embed/${videoId}`}
          title={title}
          frameBorder="0"
          allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
          allowFullScreen
          className="rounded-lg shadow-lg"
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
    video.title.toLowerCase().includes(searchQuery.toLowerCase())
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
          <VideoCard key={video.videoId} videoId={video.videoId} title={video.title} />
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