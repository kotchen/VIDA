import { useState } from "react"
import { Link, useNavigate, useParams } from "react-router"

import { ChaptersCard } from "@/components/dashboard/ChaptersCard"
import { ExportCard } from "@/components/dashboard/ExportCard"
import { PlayerCard } from "@/components/dashboard/PlayerCard"
import { SummaryCard } from "@/components/dashboard/SummaryCard"
import { TranscriptCard } from "@/components/dashboard/TranscriptCard"
import { EpisodeStatusPanel } from "@/features/episode/EpisodeStatusPanel"
import { useEpisode } from "@/features/episode/useEpisode"
import { ChapterEditor } from "@/features/episode/ChapterEditor"
import { DeleteEpisodeDialog } from "@/features/episode/DeleteEpisodeDialog"
import type { Chapter } from "@/api/types"
import { Button } from "@/components/ui/button"

export function EpisodePage() {
  const { id = "" } = useParams()
  const navigate = useNavigate()
  const state = useEpisode(id)
  const [chapterEditor, setChapterEditor] = useState<Chapter | "new" | null>(null)
  const [deleteOpen, setDeleteOpen] = useState(false)

  if (state.notFound) {
    return (
      <div className="p-8">
        <p>This Episode was deleted or does not exist.</p>
        <Link className="mt-3 inline-block text-gold" to="/library">
          Return to Library
        </Link>
      </div>
    )
  }
  if (state.loading && state.episode === null) {
    return <p className="p-8 text-muted-warm">Loading Episode…</p>
  }
  if (state.episode === null) {
    return <p role="alert" className="p-8">{state.error ?? "Unable to load Episode"}</p>
  }
  const episode = state.episode
  const warnings = episode.warnings.reduce<
    Record<string, typeof episode.warnings>
  >((groups, warning) => {
    const stageWarnings = groups[warning.stage] ?? []
    stageWarnings.push(warning)
    groups[warning.stage] = stageWarnings
    return groups
  }, {})

  return (
    <div className="space-y-6 p-8">
      {state.error ? <p role="alert" className="text-destructive">{state.error}</p> : null}
      <h1 className="font-display text-3xl text-gold">{episode.title}</h1>
      {episode.warnings.length > 0 ? (
        <section className="rounded-xl border border-warm bg-card p-4">
          {Object.entries(warnings).map(([stage, rows]) => (
            <div key={stage}>
              <h2 className="font-semibold capitalize">{stage} warnings</h2>
              {rows?.map((warning) => (
                <p key={warning.code} className="text-sm text-muted-warm">
                  {warning.message}
                </p>
              ))}
            </div>
          ))}
        </section>
      ) : null}
      <EpisodeStatusPanel
        episode={episode}
        onCancel={() => void state.cancel()}
        onRetry={() => void state.retry()}
        onDelete={() => setDeleteOpen(true)}
      />
      {episode.status === "completed" ? (
        <div className="grid gap-5 lg:grid-cols-2">
          <PlayerCard episode={episode} />
          <SummaryCard
            summary={state.summary}
            loading={state.contentLoading}
            onRegenerate={() => void state.regenerateSummary()}
            regenerating={state.operation !== null}
          />
          <TranscriptCard segments={state.transcript} />
          <ChaptersCard
            chapters={state.chapters}
            onCreate={() => setChapterEditor("new")}
            onEdit={setChapterEditor}
            onDelete={(chapter) => void state.deleteChapter(chapter.id)}
            onBookmark={(chapter) => void state.toggleBookmark(chapter)}
          />
          <div>
            <ExportCard episodeId={episode.id} />
            <Button
              disabled={state.operation !== null}
              onClick={() => void state.regenerateChapters()}
            >
              Regenerate chapters
            </Button>
            <Button variant="destructive" onClick={() => setDeleteOpen(true)}>
              Delete Episode
            </Button>
          </div>
        </div>
      ) : null}
      {chapterEditor ? (
        <ChapterEditor
          chapter={chapterEditor === "new" ? undefined : chapterEditor}
          episodeDurationSec={episode.durationSec}
          onCancel={() => setChapterEditor(null)}
          onSubmit={async (input) => {
            if (
              chapterEditor === "new" &&
              typeof input.startSec === "number" &&
              typeof input.title === "string"
            ) {
              await state.createChapter({
                startSec: input.startSec,
                title: input.title,
              })
            } else if (chapterEditor !== "new") {
              await state.updateChapter(chapterEditor.id, input)
            }
            setChapterEditor(null)
          }}
        />
      ) : null}
      {deleteOpen ? (
        <DeleteEpisodeDialog
          title={episode.title}
          deleting={state.operation === "delete"}
          onCancel={() => setDeleteOpen(false)}
          onConfirm={async () => {
            await state.deleteEpisode()
            navigate("/library")
          }}
        />
      ) : null}
    </div>
  )
}
