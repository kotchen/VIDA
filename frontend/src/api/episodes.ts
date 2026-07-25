import { apiBlob, apiRequest, type ApiBlob } from "@/api/client"
import type {
  Chapter,
  ChapterCreateInput,
  ChapterUpdateInput,
  DashboardData,
  Episode,
  EpisodeUrlSubmissionInput,
  Job,
  Project,
  Summary,
  TranscriptSegment,
} from "@/api/types"

export type ExportFormat = "txt" | "srt" | "md"

export const episodesApi = {
  getDashboard(signal?: AbortSignal): Promise<DashboardData> {
    return apiRequest("/api/v2/dashboard", { signal })
  },

  list(
    options: { limit?: number; offset?: number } = {},
    signal?: AbortSignal,
  ): Promise<Project[]> {
    const query = new URLSearchParams()
    if (options.limit !== undefined) query.set("limit", String(options.limit))
    if (options.offset !== undefined) query.set("offset", String(options.offset))
    const suffix = query.size > 0 ? `?${query.toString()}` : ""
    return apiRequest(`/api/v2/episodes${suffix}`, { signal })
  },

  get(id: string, signal?: AbortSignal): Promise<Episode> {
    return apiRequest(`/api/v2/episodes/${segment(id)}`, { signal })
  },

  getTranscript(id: string, signal?: AbortSignal): Promise<TranscriptSegment[]> {
    return apiRequest(`/api/v2/episodes/${segment(id)}/transcript`, { signal })
  },

  getSummary(id: string, signal?: AbortSignal): Promise<Summary> {
    return apiRequest(`/api/v2/episodes/${segment(id)}/summary`, { signal })
  },

  getChapters(id: string, signal?: AbortSignal): Promise<Chapter[]> {
    return apiRequest(`/api/v2/episodes/${segment(id)}/chapters`, { signal })
  },

  submitUrl(input: EpisodeUrlSubmissionInput): Promise<Episode> {
    return apiRequest("/api/v2/episodes", { method: "POST", body: input })
  },

  cancel(id: string): Promise<Job> {
    return apiRequest(`/api/v2/episodes/${segment(id)}/cancel`, {
      method: "POST",
    })
  },

  retry(id: string): Promise<Job> {
    return apiRequest(`/api/v2/episodes/${segment(id)}/retry`, {
      method: "POST",
    })
  },

  regenerateSummary(id: string): Promise<Job> {
    return apiRequest(`/api/v2/episodes/${segment(id)}/summary/regenerate`, {
      method: "POST",
    })
  },

  regenerateChapters(id: string): Promise<Job> {
    return apiRequest(`/api/v2/episodes/${segment(id)}/chapters/regenerate`, {
      method: "POST",
    })
  },

  createChapter(id: string, input: ChapterCreateInput): Promise<Chapter> {
    return apiRequest(`/api/v2/episodes/${segment(id)}/chapters`, {
      method: "POST",
      body: input,
    })
  },

  updateChapter(
    id: string,
    chapterId: string,
    input: ChapterUpdateInput,
  ): Promise<Chapter> {
    return apiRequest(
      `/api/v2/episodes/${segment(id)}/chapters/${segment(chapterId)}`,
      { method: "PATCH", body: input },
    )
  },

  deleteChapter(id: string, chapterId: string): Promise<void> {
    return apiRequest(
      `/api/v2/episodes/${segment(id)}/chapters/${segment(chapterId)}`,
      { method: "DELETE" },
    )
  },

  delete(id: string): Promise<void> {
    return apiRequest(`/api/v2/episodes/${segment(id)}`, { method: "DELETE" })
  },

  exportUrl(id: string, format: ExportFormat): string {
    return `/api/v2/episodes/${segment(id)}/export?format=${encodeURIComponent(format)}`
  },

  export(id: string, format: ExportFormat): Promise<ApiBlob> {
    return apiBlob(this.exportUrl(id, format))
  },
}

function segment(value: string): string {
  return encodeURIComponent(value)
}
