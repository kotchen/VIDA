import { useCallback, useEffect, useState } from "react"
import { episodesApi } from "@/api/episodes"
import type { DashboardData } from "@/api/types"
import { useFallbackRefresh, useV2Events } from "@/features/events/useV2Events"

export function useDashboard() {
  const [data, setData] = useState<DashboardData | null>(null)
  const [error, setError] = useState(false)
  const load = useCallback(async () => {
    try {
      setData(await episodesApi.getDashboard())
      setError(false)
    } catch {
      setError(true)
    }
  }, [])
  useEffect(() => void load(), [load])
  useV2Events(
    (event) =>
      event.type === "dashboard.invalidated" ||
      event.type === "episode.updated" ||
      event.type === "episode.deleted" ||
      event.type === "reconnected",
    () => void load(),
  )
  useFallbackRefresh(() => void load(), data?.recentProjects.some(
    (project) => project.status === "queued" || project.status === "processing",
  ) ?? false)
  return { data, error, retry: load }
}
