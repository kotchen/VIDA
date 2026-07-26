import { ListFilter, Search, SlidersHorizontal } from "lucide-react"
import { Card } from "@/components/ui/card"
import { ScrollArea } from "@/components/ui/scroll-area"
import type { TranscriptSegment } from "@/api/types"
import { formatTimestamp } from "@/lib/format"
import { cn } from "@/lib/utils"
import { useMemo, useState } from "react"

export function TranscriptCard({
  segments,
  className,
  onSeek,
}: {
  segments: TranscriptSegment[]
  className?: string
  onSeek?: (sec: number) => void
}) {
  const [query, setQuery] = useState("")
  const filtered = useMemo(() => {
    const value = query.trim().toLowerCase()
    return value
      ? segments.filter((segment) =>
          `${segment.speaker} ${segment.text}`.toLowerCase().includes(value),
        )
      : segments
  }, [query, segments])
  return (
    <Card className={cn("card-glow flex h-full flex-col gap-3 rounded-2xl border-warm/60 bg-card p-4", className)}>
      <div className="flex items-center justify-between gap-3">
        <h2 className="shrink-0 text-base font-semibold text-gold">Transcript</h2>
        <div className="flex items-center gap-2">
          <div className="flex items-center gap-2 rounded-lg border border-warm/60 bg-raised px-3 py-1.5 text-muted-warm">
            <Search className="size-3.5" />
            <input
              type="search"
              aria-label="Search transcript"
              className="w-32 bg-transparent text-xs text-cream outline-none placeholder:text-muted-warm"
              placeholder="Search transcript..."
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </div>
          <SlidersHorizontal className="size-4 text-muted-warm" />
          <ListFilter className="size-4 text-muted-warm" />
        </div>
      </div>
      <ScrollArea className="min-h-0 flex-1">
        <ol className="flex flex-col gap-1 pr-3">
          {filtered.map((seg, i) => (
            <li key={seg.id} className={`flex gap-3 rounded-lg px-2 py-1.5 ${i === 0 ? "bg-copper-500/15" : ""}`}>
              {onSeek ? (
                <button
                  type="button"
                  aria-label={`Seek to ${formatTimestamp(seg.startSec)}`}
                  onClick={() => onSeek(seg.startSec)}
                  className="tnum w-20 shrink-0 cursor-pointer pt-0.5 text-left text-xs text-copper-300 underline-offset-2 transition-colors hover:text-gold hover:underline"
                >
                  {formatTimestamp(seg.startSec)}
                </button>
              ) : (
                <span className="tnum w-20 shrink-0 pt-0.5 text-xs text-copper-300">{formatTimestamp(seg.startSec)}</span>
              )}
              <p className="text-sm leading-relaxed">
                <span className="font-semibold text-cream">{seg.speaker}: </span>
                <span className="text-cream/80">{seg.text}</span>
              </p>
            </li>
          ))}
        </ol>
      </ScrollArea>
    </Card>
  )
}
