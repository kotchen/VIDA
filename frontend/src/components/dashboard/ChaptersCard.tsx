import { Bookmark, MoreVertical, Plus } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { ScrollArea } from "@/components/ui/scroll-area"
import type { Chapter } from "@/data/types"
import { formatSeconds } from "@/lib/format"

type ChapterListProps = {
  chapters: Chapter[]
  onEdit?: (chapter: Chapter) => void
  onDelete?: (chapter: Chapter) => void
  onBookmark?: (chapter: Chapter) => void
  onSeek?: (sec: number) => void
}

export function ChaptersCard({
  chapters,
  onCreate,
  onEdit,
  onDelete,
  onBookmark,
  onSeek,
}: ChapterListProps & { onCreate?: () => void }) {
  return (
    <Card className="card-glow flex h-full flex-col gap-3 rounded-2xl border-warm/60 bg-card p-4">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-base font-semibold text-gold">Chapters &amp; Highlights</h2>
        <Button onClick={onCreate} size="sm" className="bg-copper-gradient text-on-copper hover:opacity-90">
          <Plus className="size-4" />
          Add Chapter
        </Button>
      </div>
      <ChapterList
        chapters={chapters}
        onEdit={onEdit}
        onDelete={onDelete}
        onBookmark={onBookmark}
        onSeek={onSeek}
      />
    </Card>
  )
}

/** Card-free chapter list shared by ChaptersCard and InsightsCard. */
export function ChapterList({
  chapters,
  onEdit,
  onDelete,
  onBookmark,
  onSeek,
}: ChapterListProps) {
  return (
    <ScrollArea className="min-h-0 flex-1">
      <ol className="flex flex-col gap-2 pr-3">
        {chapters.map((ch) => (
          <li key={ch.id} className="flex items-center gap-3 rounded-xl bg-raised/60 p-2">
            {ch.thumbnailUrl ? (
              <img src={ch.thumbnailUrl} alt="" className="size-11 shrink-0 rounded-lg object-cover" />
            ) : (
              <span className="size-11 shrink-0 rounded-lg bg-warm/40" />
            )}
            {onSeek ? (
              <button
                type="button"
                aria-label={`Seek to ${formatSeconds(ch.startSec)}`}
                onClick={() => onSeek(ch.startSec)}
                className="tnum w-11 shrink-0 cursor-pointer text-left text-xs text-copper-300 underline-offset-2 transition-colors hover:text-gold hover:underline"
              >
                {formatSeconds(ch.startSec)}
              </button>
            ) : (
              <span className="tnum w-11 shrink-0 text-xs text-copper-300">{formatSeconds(ch.startSec)}</span>
            )}
            {onSeek ? (
              <button
                type="button"
                onClick={() => onSeek(ch.startSec)}
                className="min-w-0 flex-1 cursor-pointer truncate text-left text-sm transition-colors hover:text-gold"
              >
                {ch.title}
              </button>
            ) : (
              <span className="min-w-0 flex-1 truncate text-sm">{ch.title}</span>
            )}
            <span className="tnum shrink-0 text-xs text-muted-warm">{formatSeconds(ch.durationSec)}</span>
            <button aria-label={`Bookmark ${ch.title}`} onClick={() => onBookmark?.(ch)}>
              <Bookmark className={`size-4 shrink-0 ${ch.bookmarked ? "fill-current text-gold" : "text-muted-warm"}`} />
            </button>
            {ch.source === "manual" ? (
              <>
                <button aria-label={`Edit ${ch.title}`} onClick={() => onEdit?.(ch)}>
                  <MoreVertical className="size-4 shrink-0 text-muted-warm" />
                </button>
                <button aria-label={`Delete ${ch.title}`} onClick={() => onDelete?.(ch)}>
                  Delete
                </button>
              </>
            ) : null}
          </li>
        ))}
      </ol>
    </ScrollArea>
  )
}
