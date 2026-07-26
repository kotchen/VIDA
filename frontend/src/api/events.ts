import type { EpisodeStatus, V2Event } from "@/api/types"

export type V2ClientEvent =
  | V2Event
  | { type: "reconnected"; data: Record<string, never> }

export const V2_EVENT_TYPES = [
  "episode.updated",
  "episode.deleted",
  "job.updated",
  "profiles.invalidated",
  "dashboard.invalidated",
] as const

export function parseV2Event(type: string, rawData: string): V2Event | null {
  if (!isEventType(type)) return null
  let data: unknown
  try {
    data = JSON.parse(rawData)
  } catch {
    return null
  }
  if (!isRecord(data)) return null

  switch (type) {
    case "episode.updated":
      return isString(data.episodeId) &&
        isEpisodeStatus(data.status) &&
        isProgress(data.progress)
        ? { type, data: {
            episodeId: data.episodeId,
            status: data.status,
            progress: data.progress,
          } }
        : null
    case "episode.deleted":
      return isString(data.episodeId)
        ? { type, data: { episodeId: data.episodeId } }
        : null
    case "job.updated":
      return isString(data.jobId) &&
        isString(data.episodeId) &&
        isEpisodeStatus(data.status) &&
        isProgress(data.progress)
        ? { type, data: {
            jobId: data.jobId,
            episodeId: data.episodeId,
            status: data.status,
            progress: data.progress,
          } }
        : null
    case "profiles.invalidated":
    case "dashboard.invalidated":
      return { type, data: {} }
  }
}

function isEventType(value: string): value is V2Event["type"] {
  return (V2_EVENT_TYPES as readonly string[]).includes(value)
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
}

function isString(value: unknown): value is string {
  return typeof value === "string" && value.length > 0
}

function isEpisodeStatus(value: unknown): value is EpisodeStatus {
  return (
    value === "queued" ||
    value === "processing" ||
    value === "completed" ||
    value === "failed" ||
    value === "canceled"
  )
}

function isProgress(value: unknown): value is number {
  return (
    typeof value === "number" &&
    Number.isInteger(value) &&
    value >= 0 &&
    value <= 100
  )
}
