import { Link, useParams } from "react-router"

import { ChaptersCard } from "@/components/dashboard/ChaptersCard"
import { ExportCard } from "@/components/dashboard/ExportCard"
import { PlayerCard } from "@/components/dashboard/PlayerCard"
import { SummaryCard } from "@/components/dashboard/SummaryCard"
import { TranscriptCard } from "@/components/dashboard/TranscriptCard"
import { EpisodeStatusPanel } from "@/features/episode/EpisodeStatusPanel"
import { useEpisode } from "@/features/episode/useEpisode"

export function EpisodePage() {
  const { id = "" } = useParams()
  const state = useEpisode(id)

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
      />
      {episode.status === "completed" ? (
        <div className="grid gap-5 lg:grid-cols-2">
          <PlayerCard episode={episode} />
          <SummaryCard
            summary={state.summary}
            loading={state.contentLoading}
          />
          <TranscriptCard segments={state.transcript} />
          <ChaptersCard chapters={state.chapters} />
          <ExportCard />
        </div>
      ) : null}
    </div>
  )
}
