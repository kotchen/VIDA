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
    <div className="grid grid-cols-12 gap-4 p-6">
      <div className="col-span-4 h-[248px]">
        <UploadCard />
      </div>
      <div className="col-span-4 h-[248px]">
        <PlayerCard episode={data.currentEpisode} />
      </div>
      <div className="col-span-4 h-[248px]">
        <SummaryCard summary={data.summary} />
      </div>
      <div className="col-span-7 h-[400px]">
        <TranscriptCard segments={data.transcript} />
      </div>
      <div className="col-span-5 h-[400px]">
        <ChaptersCard chapters={data.chapters} />
      </div>
      <div className="col-span-7 h-[190px]">
        <RecentProjectsCard projects={data.recentProjects} />
      </div>
      <div className="col-span-5 h-[190px]">
        <ExportCard />
      </div>
    </div>
  )
}
