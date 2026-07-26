import type { Episode } from "@/api/types"
import { Button } from "@/components/ui/button"

export function EpisodeStatusPanel({
  episode,
  onCancel,
  onRetry,
  onDelete,
}: {
  episode: Episode
  onCancel: () => void
  onRetry: () => void
  onDelete?: () => void
}) {
  if (episode.status === "completed") return null
  const active =
    episode.status === "queued" || episode.status === "processing"
  return (
    <section className="card-glow rounded-xl bg-card p-6">
      <h2 className="text-lg font-semibold text-gold">
        {episode.status === "failed"
          ? "Processing failed"
          : episode.status === "canceled"
            ? "Processing canceled"
            : episode.message}
      </h2>
      {episode.status === "queued" && episode.queuePosition ? (
        <p className="mt-2 text-sm text-muted-warm">
          Queue position: {episode.queuePosition}
        </p>
      ) : null}
      {active ? (
        <div className="mt-4">
          <div className="h-2 rounded-full bg-raised">
            <div
              className="bg-copper-gradient h-full rounded-full"
              style={{ width: `${episode.progress}%` }}
            />
          </div>
          <p className="mt-2 text-sm text-muted-warm">{episode.progress}%</p>
        </div>
      ) : null}
      <div className="mt-4 flex gap-2">
        {active ? <Button onClick={onCancel}>Cancel</Button> : null}
        {!active ? <Button onClick={onRetry}>Retry</Button> : null}
        {!active ? (
          <Button variant="destructive" onClick={onDelete}>
            Delete Episode
          </Button>
        ) : null}
      </div>
    </section>
  )
}
