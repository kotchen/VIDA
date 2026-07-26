import { useMemo, useState } from "react"
import { Link } from "react-router"
import { Clapperboard, Search } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { useLibrary } from "@/features/library/useLibrary"
import { formatDate, formatSeconds } from "@/lib/format"
import type { EpisodeStatus, Project } from "@/api/types"

const STATUS_OPTIONS: ("all" | EpisodeStatus)[] = [
  "all",
  "queued",
  "processing",
  "completed",
  "failed",
  "canceled",
]

export function LibraryPage() {
  const state = useLibrary()
  const [query, setQuery] = useState("")
  const [status, setStatus] = useState<"all" | EpisodeStatus>("all")
  const rows = useMemo(() => state.projects.filter((row) =>
    (status === "all" || row.status === status) &&
    row.title.toLowerCase().includes(query.toLowerCase()),
  ), [query, state.projects, status])
  return (
    <div className="flex flex-col gap-6 p-8">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-display text-3xl text-gold">Library</h1>
          <p className="mt-1 text-sm text-muted-warm">
            {state.projects.length} episode{state.projects.length === 1 ? "" : "s"} in your library
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <div className="relative">
            <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-warm" />
            <input
              aria-label="Filter projects"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search titles..."
              className="w-64 rounded-lg border border-warm bg-page py-2 pl-9 pr-3 text-sm text-cream outline-none placeholder:text-muted-warm/70 focus:border-copper-500"
            />
          </div>
          <select
            aria-label="Filter status"
            value={status}
            onChange={(event) => setStatus(event.target.value as typeof status)}
            className="rounded-lg border border-warm bg-page px-3 py-2 text-sm text-cream outline-none focus:border-copper-500"
          >
            {STATUS_OPTIONS.map((value) => (
              <option key={value} value={value}>
                {value === "all" ? "All statuses" : STATUS_LABELS[value]}
              </option>
            ))}
          </select>
        </div>
      </header>

      {rows.length === 0 ? (
        <EmptyState hasAny={state.projects.length > 0} />
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {rows.map((row) => (
            <LibraryCard
              key={row.id}
              project={row}
              onCancel={() => void state.cancel(row.id)}
              onDelete={() => void state.remove(row.id)}
            />
          ))}
        </div>
      )}

      {state.hasMore ? (
        <div className="flex justify-center">
          <Button variant="outline" onClick={() => void state.loadMore()}>
            Load more
          </Button>
        </div>
      ) : null}
    </div>
  )
}

const STATUS_LABELS: Record<EpisodeStatus, string> = {
  queued: "Queued",
  processing: "Processing",
  completed: "Completed",
  failed: "Failed",
  canceled: "Canceled",
}

function StatusBadge({ status }: { status: EpisodeStatus }) {
  if (status === "completed") {
    return <Badge className="border-success/40 bg-success/15 text-success">Completed</Badge>
  }
  if (status === "failed") {
    return <Badge variant="destructive">Failed</Badge>
  }
  if (status === "canceled") {
    return <Badge className="border-warm bg-raised/60 text-muted-warm">Canceled</Badge>
  }
  return (
    <Badge className="border-copper-500/40 bg-copper-500/15 text-copper-300">
      {STATUS_LABELS[status]}
    </Badge>
  )
}

function LibraryCard({
  project,
  onCancel,
  onDelete,
}: {
  project: Project
  onCancel: () => void
  onDelete: () => void
}) {
  const href = `/episodes/${encodeURIComponent(project.id)}`
  const active = project.status === "queued" || project.status === "processing"
  return (
    <article className="card-glow flex flex-col overflow-hidden rounded-2xl bg-card transition-colors hover:border-copper-500/40">
      <Link to={href} className="block">
        {project.thumbnailUrl ? (
          <img
            src={project.thumbnailUrl}
            alt=""
            className="aspect-video w-full object-cover"
          />
        ) : (
          <div className="flex aspect-video w-full items-center justify-center bg-warm/30">
            <Clapperboard className="size-8 text-muted-warm/60" />
          </div>
        )}
      </Link>
      <div className="flex flex-1 flex-col gap-2 p-4">
        <Link
          to={href}
          className="line-clamp-2 text-sm font-medium text-cream transition-colors hover:text-gold"
        >
          {project.title}
        </Link>
        <p className="tnum text-xs text-muted-warm">
          {formatDate(project.createdAt)} · <span>{formatSeconds(project.durationSec)}</span>
        </p>
        <div className="mt-auto flex items-center justify-between gap-2 pt-2">
          <StatusBadge status={project.status} />
          {active ? (
            <Button variant="outline" size="xs" onClick={onCancel}>
              Cancel
            </Button>
          ) : (
            <Button variant="ghost" size="xs" className="text-destructive hover:text-destructive" onClick={onDelete}>
              Delete
            </Button>
          )}
        </div>
      </div>
    </article>
  )
}

function EmptyState({ hasAny }: { hasAny: boolean }) {
  return (
    <div className="flex flex-col items-center gap-3 rounded-2xl border border-dashed border-warm/60 py-16 text-center">
      <Clapperboard className="size-10 text-muted-warm/50" />
      <p className="text-sm text-muted-warm">
        {hasAny ? "No episodes match your filters" : "Your library is empty"}
      </p>
      {!hasAny ? (
        <Button asChild className="mt-2">
          <Link to="/transcribe">Transcribe media</Link>
        </Button>
      ) : null}
    </div>
  )
}
