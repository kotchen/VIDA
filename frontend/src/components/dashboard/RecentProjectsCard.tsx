import { MoreVertical } from "lucide-react"
import { Link } from "react-router"
import { Badge } from "@/components/ui/badge"
import { Card } from "@/components/ui/card"
import type { Project } from "@/data/types"
import { formatDate, formatSeconds } from "@/lib/format"

export function RecentProjectsCard({ projects }: { projects: Project[] }) {
  return (
    <Card className="card-glow flex h-full flex-col gap-3 rounded-2xl border-warm/60 bg-card p-4">
      <div className="flex items-center justify-between">
        <h2 className="text-base font-semibold text-gold">Recent Projects</h2>
        <Link to="/library" className="text-xs text-copper-300 transition-colors hover:text-copper-500">View all</Link>
      </div>
      <div className="grid min-h-0 flex-1 grid-cols-4 gap-3">
        {projects.map((p) => (
          <Link to={`/episodes/${encodeURIComponent(p.id)}`} key={p.id} className="flex min-w-0 flex-col overflow-hidden rounded-xl bg-raised/60">
            {p.thumbnailUrl ? (
              <img src={p.thumbnailUrl} alt="" className="h-14 w-full shrink-0 object-cover" />
            ) : (
              <div className="h-14 w-full shrink-0 bg-warm/40" />
            )}
            <div className="flex min-h-0 flex-1 flex-col gap-0.5 p-2">
              <p className="truncate text-xs font-medium">{p.title}</p>
              <p className="tnum text-[10px] text-muted-warm">
                {formatDate(p.createdAt)} · <span>{formatSeconds(p.durationSec)}</span>
              </p>
              <div className="mt-auto flex items-center justify-between pt-1">
                {p.status === "completed" ? (
                  <Badge className="border-success/40 bg-success/15 px-1.5 py-0 text-[10px] text-success">Completed</Badge>
                ) : p.status === "failed" ? (
                  <Badge variant="destructive" className="px-1.5 py-0 text-[10px]">Failed</Badge>
                ) : (
                  <Badge className="border-copper-500/40 bg-copper-500/15 px-1.5 py-0 text-[10px] text-copper-300">Processing</Badge>
                )}
                <MoreVertical className="size-3.5 text-muted-warm" />
              </div>
            </div>
          </Link>
        ))}
      </div>
    </Card>
  )
}
