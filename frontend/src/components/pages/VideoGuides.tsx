"use client"

interface VideoProps {
  videoId: string
  title: string
}

export function Video({ videoId, title }: VideoProps) {
  return (
    <div className="flex justify-center">
      <iframe
        width="560"
        height="315"
        src={`https://www.youtube.com/embed/${videoId}`}
        title={title}
        frameBorder="0"
        allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
        allowFullScreen
        className="rounded-lg shadow-lg"
      />
    </div>
  )
}

export function VideoGuides() {
  return (
    <div className="flex flex-col items-center gap-8 min-h-[60vh] py-8">
      <Video videoId="dQw4w9WgXcQ" title="Never Gonna Give You Up" />
    </div>
  )
}