import { useEffect, useState } from "react"
import { ChaptersCard } from "@/components/dashboard/ChaptersCard"
import { ExportCard } from "@/components/dashboard/ExportCard"
import { PlayerCard } from "@/components/dashboard/PlayerCard"
import { RecentProjectsCard } from "@/components/dashboard/RecentProjectsCard"
import { SummaryCard } from "@/components/dashboard/SummaryCard"
import { TranscriptCard } from "@/components/dashboard/TranscriptCard"
import { UploadCard } from "@/components/dashboard/UploadCard"
import { dataProvider } from "@/data/provider"
import type { DashboardData } from "@/data/types"

export function DashboardPage() {
  const [data, setData] = useState<DashboardData | null>(null)

  useEffect(() => {
    void dataProvider.getDashboard().then(setData)
  }, [])

  if (!data) {
    return <div className="p-6 text-sm text-muted-warm">Loading dashboard…</div>
  }

  return (
    <div className="grid h-[calc(100vh-3.5rem)] grid-cols-12 grid-rows-[1.1fr_1.15fr_0.9fr] gap-4 p-6">
      <div className="col-span-4 min-h-0">
        <UploadCard />
      </div>
      <div className="col-span-4 min-h-0">
        {data.currentEpisode ? (
          <PlayerCard episode={data.currentEpisode} />
        ) : (
          <div className="card-glow flex h-full items-center justify-center rounded-2xl bg-card text-sm text-muted-warm">
            No completed Episode yet.
          </div>
        )}
      </div>
      <div className="col-span-4 min-h-0">
        <SummaryCard summary={data.summary} />
      </div>
      <div className="col-span-7 min-h-0">
        <TranscriptCard segments={data.transcript} />
      </div>
      <div className="col-span-5 min-h-0">
        <ChaptersCard chapters={data.chapters} />
      </div>
      <div className="col-span-7 min-h-0">
        <RecentProjectsCard projects={data.recentProjects} />
      </div>
      <div className="col-span-5 min-h-0">
        <ExportCard />
      </div>
    </div>
  )
}
