import { ApiError } from "@/api/client"
import type { Episode } from "@/api/types"

export interface EpisodeUploadInput {
  file: File
  providerProfileId: string
  summaryLanguage: string
  title?: string
}

export function uploadEpisode(
  input: EpisodeUploadInput,
  onProgress: (progress: number) => void,
  signal?: AbortSignal,
): Promise<Episode> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    const body = new FormData()
    body.append("file", input.file)
    body.append("providerProfileId", input.providerProfileId)
    body.append("summaryLanguage", input.summaryLanguage)
    if (input.title?.trim()) body.append("title", input.title.trim())

    xhr.open("POST", "/api/v2/episodes")
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable && event.total > 0) {
        onProgress(Math.round((event.loaded / event.total) * 100))
      }
    }
    xhr.onerror = () =>
      reject(new ApiError(0, "network_error", "Unable to reach the server"))
    xhr.onabort = () =>
      reject(new ApiError(0, "request_aborted", "Upload canceled"))
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText) as Episode)
        } catch {
          reject(
            new ApiError(
              xhr.status,
              "invalid_response",
              "Server returned an invalid response",
              {},
              xhr.getResponseHeader("X-Request-ID"),
            ),
          )
        }
        return
      }
      reject(xhrError(xhr))
    }

    if (signal?.aborted) {
      reject(new ApiError(0, "request_aborted", "Upload canceled"))
      return
    }
    signal?.addEventListener("abort", () => xhr.abort(), { once: true })
    xhr.send(body)
  })
}

function xhrError(xhr: XMLHttpRequest): ApiError {
  let error: Record<string, unknown> = {}
  try {
    const envelope = JSON.parse(xhr.responseText.slice(0, 64 * 1024)) as {
      error?: Record<string, unknown>
    }
    error = envelope.error ?? {}
  } catch {
    // Malformed response text is intentionally discarded.
  }
  return new ApiError(
    xhr.status,
    typeof error.code === "string" ? error.code : "http_error",
    typeof error.message === "string" ? error.message : "Request failed",
    error.details !== null &&
      typeof error.details === "object" &&
      !Array.isArray(error.details)
      ? (error.details as Record<string, unknown>)
      : {},
    xhr.getResponseHeader("X-Request-ID") ??
      (typeof error.requestId === "string" ? error.requestId : null),
  )
}
