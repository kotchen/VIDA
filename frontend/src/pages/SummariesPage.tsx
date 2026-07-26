import { useEffect, useState } from "react"
import { episodesApi } from "@/api/episodes"
import type { Project, Summary } from "@/api/types"
import { SummaryCard } from "@/components/dashboard/SummaryCard"

export function SummariesPage() {
  const [projects, setProjects] = useState<Project[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [summary, setSummary] = useState<Summary | null>(null)
  useEffect(() => {
    void episodesApi.list({ limit: 100, offset: 0 }).then((rows) =>
      setProjects(rows.filter((row) => row.status === "completed")),
    )
  }, [])
  useEffect(() => {
    if (!selected) return
    setSummary(null)
    void episodesApi.getSummary(selected).then(setSummary).catch(() => setSummary(null))
  }, [selected])
  return <div className="p-8">
    <h1 className="font-display text-3xl text-gold">Summaries</h1>
    {projects.map((project) => <button key={project.id} onClick={() => setSelected(project.id)}>{project.title}</button>)}
    {selected ? <SummaryCard summary={summary} onRegenerate={() => void episodesApi.regenerateSummary(selected)} /> : <p>Select a completed Episode.</p>}
  </div>
}
