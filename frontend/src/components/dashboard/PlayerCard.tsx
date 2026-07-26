import { useEffect, useMemo, useRef, useState } from "react"
import { MoreVertical, Pencil } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Card } from "@/components/ui/card"
import type { Episode } from "@/api/types"
import { networkEmbed } from "@/lib/embed"
import { formatDate, formatSeconds } from "@/lib/format"

/** A request to jump the player to a position; `nonce` re-triggers repeated seeks to the same second. */
export type SeekRequest = { sec: number; nonce: number }

export function PlayerCard({
  episode,
  seek,
}: {
  episode: Episode
  seek?: SeekRequest | null
}) {
  const embed = useMemo(
    () =>
      episode.sourceType === "url" && episode.sourceUrl
        ? networkEmbed(episode.sourceUrl)
        : null,
    [episode.sourceType, episode.sourceUrl],
  )
  const videoRef = useRef<HTMLVideoElement>(null)
  const iframeRef = useRef<HTMLIFrameElement>(null)
  const [biliStart, setBiliStart] = useState<number | null>(null)

  useEffect(() => {
    if (!seek) return
    const sec = Math.max(0, Math.floor(seek.sec))
    if (embed) {
      if (embed.provider === "youtube") {
        const post = (func: string, args: unknown[] = []) =>
          iframeRef.current?.contentWindow?.postMessage(
            JSON.stringify({ event: "command", func, args }),
            "*",
          )
        post("seekTo", [sec, true])
        post("playVideo")
      } else {
        // Bilibili's embed player has no cross-origin seek API; reload it
        // with a start time instead.
        setBiliStart(sec)
      }
      return
    }
    const video = videoRef.current
    if (video) {
      video.currentTime = sec
      try {
        const playing = video.play() as Promise<void> | undefined
        playing?.catch(() => undefined)
      } catch {
        // jsdom and similar environments do not implement HTMLMediaElement.play
      }
    }
  }, [seek, embed])

  const embedSrc =
    embed?.provider === "bilibili"
      ? biliStart !== null
        ? `${embed.url}&t=${biliStart}&autoplay=1`
        : `${embed.url}&autoplay=0`
      : embed?.url

  return (
    <Card className="card-glow flex h-full flex-col gap-3 rounded-2xl border-warm/60 bg-card p-4">
      <div className="flex items-center justify-between">
        <h2 className="text-base font-semibold text-gold">{episode.title}</h2>
        <div className="flex items-center gap-2 text-muted-warm">
          <Pencil className="size-4" />
          <MoreVertical className="size-4" />
        </div>
      </div>
      <div className="flex items-center gap-3 text-xs text-muted-warm">
        <span>{formatDate(episode.createdAt)}</span>
        <span className="tnum">{formatSeconds(episode.durationSec)}</span>
        {episode.resolution ? <span>{episode.resolution}</span> : null}
        {episode.status === "completed" ? (
          <Badge className="border-success/40 bg-success/15 text-success">Completed</Badge>
        ) : null}
      </div>
      {embedSrc ? (
        <iframe
          ref={iframeRef}
          className="aspect-video min-h-0 w-full flex-1 rounded-xl bg-black"
          title={episode.title}
          src={embedSrc}
          allow="autoplay; fullscreen; encrypted-media; picture-in-picture"
          allowFullScreen
        />
      ) : episode.mediaUrl ? (
        <video
          ref={videoRef}
          className="min-h-0 flex-1 rounded-xl bg-black object-contain"
          title={episode.title}
          src={episode.mediaUrl}
          poster={episode.posterUrl ?? undefined}
          controls
        />
      ) : (
        <div className="flex min-h-40 flex-1 items-center justify-center rounded-xl bg-raised text-sm text-muted-warm">
          Media is not available.
        </div>
      )}
    </Card>
  )
}
