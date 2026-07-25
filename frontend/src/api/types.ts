export type EpisodeStatus =
  | "queued"
  | "processing"
  | "completed"
  | "failed"
  | "canceled"

export type JobType =
  | "process_episode"
  | "regenerate_summary"
  | "regenerate_chapters"

export interface ProcessingWarning {
  stage: "optimization" | "summary" | "chapters"
  code: string
  message: string
}

export interface Episode {
  id: string
  title: string
  sourceType: "upload" | "url"
  mediaUrl: string | null
  posterUrl: string | null
  durationSec: number
  resolution: string | null
  status: EpisodeStatus
  language: string
  createdAt: string
  progress: number
  message: string
  queuePosition: number | null
  providerProfileId: string
  warnings: ProcessingWarning[]
}

export interface Project {
  id: string
  title: string
  createdAt: string
  durationSec: number
  status: EpisodeStatus
  thumbnailUrl: string | null
}

export interface TranscriptSegment {
  id: string
  startSec: number
  endSec: number
  speaker: string
  text: string
}

export interface Summary {
  episodeId: string
  content: string
  readTimeMin: number
  keyPoints: number
  confidence: number
  generatedBy: "VIDA"
}

export interface Chapter {
  id: string
  startSec: number
  title: string
  durationSec: number
  thumbnailUrl: string | null
  bookmarked: boolean
}

export interface DashboardData {
  currentEpisode: Episode | null
  summary: Summary | null
  transcript: TranscriptSegment[]
  chapters: Chapter[]
  recentProjects: Project[]
}

export interface Job {
  id: string
  episodeId: string
  type: JobType
  attempt: number
  status: EpisodeStatus
  providerProfileRevisionId: string
  submittedAt: string
  startedAt: string | null
  finishedAt: string | null
  progress: number
  message: string
  queuePosition: number | null
  errorCode: string | null
  errorMessage: string | null
}

export interface ProviderProfile {
  id: string
  name: string
  baseUrl: string
  modelId: string
  temperature: number
  revision: number
  activeRevisionId: string
  apiKeyMasked: string
  hasApiKey: boolean
  createdAt: string
  updatedAt: string
}

export interface ProviderConnectionTest {
  ok: boolean
  latencyMs: number
  modelAvailable: boolean
  message: string
}

export interface EpisodeUrlSubmissionInput {
  sourceUrl: string
  providerProfileId: string
  summaryLanguage: string
  title?: string
}

export interface ChapterCreateInput {
  startSec: number
  title: string
}

export interface ChapterUpdateInput {
  startSec?: number
  title?: string
  bookmarked?: boolean
}

export interface ProviderProfileCreateInput {
  name: string
  baseUrl: string
  apiKey: string
  modelId: string
  temperature: number
}

export interface ProviderProfileUpdateInput {
  name?: string
  baseUrl?: string
  apiKey?: string
  modelId?: string
  temperature?: number
}

export type V2Event =
  | {
      type: "episode.updated"
      data: {
        episodeId: string
        status: EpisodeStatus
        progress: number
      }
    }
  | {
      type: "episode.deleted"
      data: { episodeId: string }
    }
  | {
      type: "job.updated"
      data: {
        jobId: string
        episodeId: string
        status: EpisodeStatus
        progress: number
      }
    }
  | {
      type: "profiles.invalidated" | "dashboard.invalidated"
      data: Record<string, never>
    }
