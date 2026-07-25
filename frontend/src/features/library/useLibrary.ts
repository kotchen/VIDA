import { useCallback, useEffect, useState } from "react"
import { episodesApi } from "@/api/episodes"
import type { Project } from "@/api/types"
import { useV2Events } from "@/features/events/useV2Events"

const PAGE_SIZE = 12

export function useLibrary() {
  const [projects, setProjects] = useState<Project[]>([])
  const [offset, setOffset] = useState(0)
  const [hasMore, setHasMore] = useState(true)
  const load = useCallback(async (nextOffset = 0) => {
    const rows = await episodesApi.list({ limit: PAGE_SIZE, offset: nextOffset })
    setProjects((current) => {
      const source = nextOffset === 0 ? rows : [...current, ...rows]
      return [...new Map(source.map((row) => [row.id, row])).values()]
    })
    setOffset(nextOffset + rows.length)
    setHasMore(rows.length === PAGE_SIZE)
  }, [])
  useEffect(() => void load(0), [load])
  useV2Events(
    (event) =>
      event.type === "episode.updated" ||
      event.type === "episode.deleted" ||
      event.type === "reconnected",
    () => void load(0),
  )
  return {
    projects,
    hasMore,
    loadMore: () => load(offset),
    remove: async (id: string) => {
      await episodesApi.delete(id)
      setProjects((current) => current.filter((row) => row.id !== id))
    },
    cancel: async (id: string) => {
      await episodesApi.cancel(id)
      await load(0)
    },
  }
}
