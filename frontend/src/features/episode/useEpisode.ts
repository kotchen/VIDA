import { useCallback, useEffect, useRef, useState } from "react"

import { ApiError } from "@/api/client"
import { episodesApi } from "@/api/episodes"
import type {
  Chapter,
  Episode,
  Summary,
  TranscriptSegment,
} from "@/api/types"
import {
  useFallbackRefresh,
  useV2Events,
} from "@/features/events/useV2Events"

export function useEpisode(id: string) {
  const [episode, setEpisode] = useState<Episode | null>(null)
  const [transcript, setTranscript] = useState<TranscriptSegment[]>([])
  const [summary, setSummary] = useState<Summary | null>(null)
  const [chapters, setChapters] = useState<Chapter[]>([])
  const [loading, setLoading] = useState(true)
  const [contentLoading, setContentLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notFound, setNotFound] = useState(false)
  const [operation, setOperation] = useState<string | null>(null)
  const regenerationJob = useRef<string | null>(null)
  const controller = useRef<AbortController | null>(null)
  const requestId = useRef(0)

  const refresh = useCallback(async () => {
    controller.current?.abort()
    const currentController = new AbortController()
    controller.current = currentController
    const currentRequest = ++requestId.current
    setLoading(true)
    try {
      const nextEpisode = await episodesApi.get(id, currentController.signal)
      if (!isCurrent(currentController, currentRequest, requestId.current)) return
      setEpisode(nextEpisode)
      setNotFound(false)
      setError(null)
      if (nextEpisode.status !== "completed") {
        setTranscript([])
        setSummary(null)
        setChapters([])
        return
      }

      setContentLoading(true)
      const [transcriptResult, summaryResult, chaptersResult] =
        await Promise.allSettled([
          episodesApi.getTranscript(id, currentController.signal),
          episodesApi.getSummary(id, currentController.signal),
          episodesApi.getChapters(id, currentController.signal),
        ])
      if (!isCurrent(currentController, currentRequest, requestId.current)) return
      if (transcriptResult.status === "fulfilled") {
        setTranscript(transcriptResult.value)
      } else {
        throw transcriptResult.reason
      }
      if (chaptersResult.status === "fulfilled") {
        setChapters(chaptersResult.value)
      } else {
        throw chaptersResult.reason
      }
      if (summaryResult.status === "fulfilled") {
        setSummary(summaryResult.value)
      } else if (
        summaryResult.reason instanceof ApiError &&
        summaryResult.reason.code === "summary_not_found"
      ) {
        setSummary(null)
      } else {
        throw summaryResult.reason
      }
    } catch (caught) {
      if (currentController.signal.aborted) return
      if (
        caught instanceof ApiError &&
        caught.code === "episode_not_found"
      ) {
        setNotFound(true)
        setEpisode(null)
      } else {
        setError(errorMessage(caught))
      }
    } finally {
      if (isCurrent(currentController, currentRequest, requestId.current)) {
        setLoading(false)
        setContentLoading(false)
      }
    }
  }, [id])

  useEffect(() => {
    void refresh()
    return () => controller.current?.abort()
  }, [refresh])

  useV2Events(
    (event) =>
      event.type === "reconnected" ||
      (("episodeId" in event.data) && event.data.episodeId === id),
    (event) => {
      if (
        event.type === "job.updated" &&
        event.data.jobId === regenerationJob.current &&
        ["completed", "failed", "canceled"].includes(event.data.status)
      ) {
        regenerationJob.current = null
        setOperation(null)
      }
      void refresh()
    },
  )

  const active =
    episode?.status === "queued" || episode?.status === "processing"
  useFallbackRefresh(() => void refresh(), active)

  const runAction = useCallback(
    async (action: () => Promise<unknown>) => {
      try {
        await action()
        await refresh()
      } catch (caught) {
        if (caught instanceof ApiError && caught.httpStatus === 409) {
          await refresh()
        }
        setError(errorMessage(caught))
      }
    },
    [refresh],
  )

  const mutateContent = useCallback(
    async (key: string, action: () => Promise<unknown>) => {
      setOperation(key)
      try {
        await action()
        await refresh()
      } catch (caught) {
        if (caught instanceof ApiError && caught.httpStatus === 409) {
          await refresh()
        }
        setError(errorMessage(caught))
      } finally {
        if (!regenerationJob.current) setOperation(null)
      }
    },
    [refresh],
  )

  const regenerate = useCallback(
    async (kind: "summary" | "chapters") => {
      setOperation(`regenerate:${kind}`)
      try {
        const job =
          kind === "summary"
            ? await episodesApi.regenerateSummary(id)
            : await episodesApi.regenerateChapters(id)
        regenerationJob.current = job.id
      } catch (caught) {
        if (caught instanceof ApiError && caught.httpStatus === 409) {
          await refresh()
          setError("A regeneration operation is already running")
        } else {
          setError(errorMessage(caught))
        }
        setOperation(null)
      }
    },
    [id, refresh],
  )

  return {
    episode,
    transcript,
    summary,
    chapters,
    loading,
    contentLoading,
    error,
    notFound,
    operation,
    refresh,
    cancel: () => runAction(() => episodesApi.cancel(id)),
    retry: () => runAction(() => episodesApi.retry(id)),
    createChapter: (input: Parameters<typeof episodesApi.createChapter>[1]) =>
      mutateContent("chapter", () => episodesApi.createChapter(id, input)),
    updateChapter: (
      chapterId: string,
      input: Parameters<typeof episodesApi.updateChapter>[2],
    ) => mutateContent("chapter", () => episodesApi.updateChapter(id, chapterId, input)),
    deleteChapter: (chapterId: string) =>
      mutateContent("chapter", () => episodesApi.deleteChapter(id, chapterId)),
    toggleBookmark: (chapter: Chapter) =>
      mutateContent("bookmark", () =>
        episodesApi.updateChapter(id, chapter.id, {
          bookmarked: !chapter.bookmarked,
        }),
      ),
    regenerateSummary: () => regenerate("summary"),
    regenerateChapters: () => regenerate("chapters"),
    deleteEpisode: () => episodesApi.delete(id),
  }
}

function isCurrent(
  controller: AbortController,
  request: number,
  latest: number,
): boolean {
  return !controller.signal.aborted && request === latest
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return error.requestId
      ? `${error.message} (Request ${error.requestId})`
      : error.message
  }
  return "Unable to load Episode"
}
