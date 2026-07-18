export interface Episode {
  id: string
  title: string
  sourceType: "upload" | "url"
  mediaUrl: string
  posterUrl: string
  durationSec: number
  resolution?: string
  status: "completed" | "processing" | "failed"
  language: string
  createdAt: string
}

export interface TranscriptSegment {
  id: string
  startSec: number
  endSec: number
  speaker: string
  text: string
}

export interface Chapter {
  id: string
  startSec: number
  title: string
  durationSec: number
  thumbnailUrl: string
  bookmarked: boolean
}

export interface Summary {
  episodeId: string
  content: string
  readTimeMin: number
  keyPoints: number
  confidence: number
  generatedBy: string
}

export interface Project {
  id: string
  title: string
  createdAt: string
  durationSec: number
  status: Episode["status"]
  thumbnailUrl: string
}

export interface DashboardData {
  currentEpisode: Episode
  summary: Summary
  transcript: TranscriptSegment[]
  chapters: Chapter[]
  recentProjects: Project[]
}
