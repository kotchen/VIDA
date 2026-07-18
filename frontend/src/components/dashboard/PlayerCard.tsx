import { Captions, Maximize2, MoreVertical, Pencil, Play } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Card } from "@/components/ui/card"
import type { Episode } from "@/data/types"
import { formatDate, formatSeconds } from "@/lib/format"

export function PlayerCard({ episode }: { episode: Episode }) {
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
      <div className="relative min-h-0 flex-1 overflow-hidden rounded-xl">
        <img src={episode.posterUrl} alt={episode.title} className="absolute inset-0 size-full object-cover" />
        <button
          aria-label="Play"
          className="bg-copper-gradient absolute left-1/2 top-1/2 flex size-12 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full text-[#1A0E04] shadow-lg"
        >
          <Play className="size-5 fill-current" />
        </button>
      </div>
      <div className="flex items-center gap-3 text-xs text-muted-warm">
        <span className="tnum">00:00 / {formatSeconds(episode.durationSec)}</span>
        <div className="h-1 flex-1 rounded-full bg-raised">
          <div className="bg-copper-gradient h-full w-0 rounded-full" />
        </div>
        <span>1x</span>
        <Captions className="size-4" />
        <Maximize2 className="size-4" />
      </div>
    </Card>
  )
}
