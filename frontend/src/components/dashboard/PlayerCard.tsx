import { MoreVertical, Pencil } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Card } from "@/components/ui/card"
import type { Episode } from "@/api/types"
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
      {episode.mediaUrl ? (
        <video
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
