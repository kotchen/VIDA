import { useState } from "react"
import { ListChecks, Plus, Sparkles } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { ScrollArea } from "@/components/ui/scroll-area"
import { SummaryContent } from "@/components/dashboard/SummaryCard"
import { ChapterList } from "@/components/dashboard/ChaptersCard"
import type { Chapter, Summary } from "@/api/types"

type InsightsTab = "summary" | "chapters"

export function InsightsCard({
  summary,
  summaryLoading = false,
  onRegenerateSummary,
  regenerating = false,
  chapters,
  onCreateChapter,
  onEditChapter,
  onDeleteChapter,
  onBookmarkChapter,
  onSeekChapter,
}: {
  summary: Summary | null
  summaryLoading?: boolean
  onRegenerateSummary?: () => void
  regenerating?: boolean
  chapters: Chapter[]
  onCreateChapter?: () => void
  onEditChapter?: (chapter: Chapter) => void
  onDeleteChapter?: (chapter: Chapter) => void
  onBookmarkChapter?: (chapter: Chapter) => void
  onSeekChapter?: (sec: number) => void
}) {
  const [tab, setTab] = useState<InsightsTab>("summary")
  return (
    <Card className="card-glow flex h-full flex-col gap-3 rounded-2xl border-warm/60 bg-card p-4">
      <div className="flex items-center justify-between gap-2">
        <div
          className="flex gap-1 rounded-xl bg-raised p-1"
          role="tablist"
          aria-label="Episode insights"
        >
          <TabButton
            active={tab === "summary"}
            icon={Sparkles}
            label="Summary"
            onClick={() => setTab("summary")}
          />
          <TabButton
            active={tab === "chapters"}
            icon={ListChecks}
            label="Chapters"
            onClick={() => setTab("chapters")}
          />
        </div>
        {tab === "chapters" && onCreateChapter ? (
          <Button
            onClick={onCreateChapter}
            size="sm"
            className="bg-copper-gradient text-on-copper hover:opacity-90"
          >
            <Plus className="size-4" />
            Add Chapter
          </Button>
        ) : null}
      </div>
      <div className="flex min-h-0 flex-1 flex-col" role="tabpanel">
        {tab === "summary" ? (
          <ScrollArea className="min-h-0 flex-1">
            <div className="pr-3">
              {summaryLoading ? (
                <p className="text-sm text-muted-warm">Loading summary…</p>
              ) : (
                <SummaryContent
                  summary={summary}
                  onRegenerate={onRegenerateSummary}
                  regenerating={regenerating}
                />
              )}
            </div>
          </ScrollArea>
        ) : (
          <ChapterList
            chapters={chapters}
            onEdit={onEditChapter}
            onDelete={onDeleteChapter}
            onBookmark={onBookmarkChapter}
            onSeek={onSeekChapter}
          />
        )}
      </div>
    </Card>
  )
}

function TabButton({
  active,
  icon: Icon,
  label,
  onClick,
}: {
  active: boolean
  icon: typeof Sparkles
  label: string
  onClick: () => void
}) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      onClick={onClick}
      className={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition-colors ${
        active
          ? "bg-copper-gradient text-on-copper"
          : "text-muted-warm hover:text-cream"
      }`}
    >
      <Icon className="size-3.5" />
      {label}
    </button>
  )
}
