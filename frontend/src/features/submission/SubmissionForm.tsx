import { useEffect, useState, type FormEvent } from "react"
import { useNavigate } from "react-router"

import { ApiError } from "@/api/client"
import { episodesApi } from "@/api/episodes"
import { profilesApi } from "@/api/profiles"
import type { ProviderProfile } from "@/api/types"
import { Button } from "@/components/ui/button"
import {
  getSubmissionPreferences,
  setSubmissionPreferences,
} from "@/features/profiles/preferences"
import { uploadEpisode } from "@/features/submission/upload"

const ACCEPTED_EXTENSIONS = [
  ".mp3",
  ".mp4",
  ".m4a",
  ".wav",
  ".webm",
  ".mkv",
  ".ogg",
  ".flac",
] as const

export function SubmissionForm({ initialFile }: { initialFile?: File | null }) {
  const navigate = useNavigate()
  const preferences = getSubmissionPreferences()
  const [mode, setMode] = useState<"file" | "url">("file")
  const [profiles, setProfiles] = useState<ProviderProfile[]>([])
  const [profileId, setProfileId] = useState(preferences.providerProfileId ?? "")
  const [language, setLanguage] = useState(preferences.summaryLanguage)
  const [file, setFile] = useState<File | null>(initialFile ?? null)
  const [sourceUrl, setSourceUrl] = useState("")
  const [title, setTitle] = useState("")
  const [progress, setProgress] = useState(0)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    profilesApi
      .list(controller.signal)
      .then(setProfiles)
      .catch((caught: unknown) => {
        if (!controller.signal.aborted) setError(errorMessage(caught))
      })
    return () => controller.abort()
  }, [])

  const chooseFile = (selected: File | null) => {
    if (selected && !hasAllowedExtension(selected.name)) {
      setFile(null)
      setError("Unsupported file type")
      return
    }
    setFile(selected)
    setError(null)
  }

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (!profileId) {
      setError("Choose a provider profile")
      return
    }
    if (!language.trim()) {
      setError("Choose a summary language")
      return
    }
    if (mode === "file" && file === null) {
      setError("Choose a media file")
      return
    }
    if (mode === "url" && !sourceUrl.trim()) {
      setError("Enter a source URL")
      return
    }

    setSubmissionPreferences({
      providerProfileId: profileId,
      summaryLanguage: language,
    })
    setSubmitting(true)
    setError(null)
    try {
      const optionalTitle = title.trim() ? { title: title.trim() } : {}
      const episode =
        mode === "url"
          ? await episodesApi.submitUrl({
              sourceUrl: sourceUrl.trim(),
              providerProfileId: profileId,
              summaryLanguage: language,
              ...optionalTitle,
            })
          : await uploadEpisode(
              {
                file: file!,
                providerProfileId: profileId,
                summaryLanguage: language,
                ...optionalTitle,
              },
              setProgress,
            )
      navigate(`/episodes/${encodeURIComponent(episode.id)}`)
    } catch (caught) {
      setError(errorMessage(caught))
      setSubmitting(false)
    }
  }

  const fieldClass =
    "mt-1 w-full rounded-lg border border-warm bg-page px-3 py-2 text-sm text-cream outline-none focus:border-copper-500"

  return (
    <form
      className="card-glow rounded-2xl bg-card p-6"
      onSubmit={(event) => void submit(event)}
    >
      <div className="mb-6 flex gap-2">
        <Button
          type="button"
          variant={mode === "file" ? "default" : "outline"}
          onClick={() => setMode("file")}
        >
          Use file
        </Button>
        <Button
          type="button"
          variant={mode === "url" ? "default" : "outline"}
          onClick={() => setMode("url")}
        >
          Use URL
        </Button>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <label className="block text-sm text-muted-warm">
          Provider profile
          <select
            className={fieldClass}
            value={profileId}
            onChange={(event) => setProfileId(event.target.value)}
          >
            <option value="">Choose a profile</option>
            {profiles.map((profile) => (
              <option key={profile.id} value={profile.id}>
                {profile.name}
              </option>
            ))}
          </select>
        </label>
        <label className="block text-sm text-muted-warm">
          Summary language
          <select
            className={fieldClass}
            value={language}
            onChange={(event) => setLanguage(event.target.value)}
          >
            <option value="zh-Hans">简体中文</option>
            <option value="zh-Hant">繁體中文</option>
            <option value="en">English</option>
            <option value="ja">日本語</option>
            <option value="es">Español</option>
          </select>
        </label>
      </div>

      <div className="mt-4">
        {mode === "file" ? (
          <label className="block text-sm text-muted-warm">
            Media file
            <input
              className={fieldClass}
              type="file"
              accept={ACCEPTED_EXTENSIONS.join(",")}
              onChange={(event) => chooseFile(event.target.files?.[0] ?? null)}
            />
          </label>
        ) : (
          <label className="block text-sm text-muted-warm">
            Source URL
            <input
              className={fieldClass}
              type="url"
              value={sourceUrl}
              onChange={(event) => setSourceUrl(event.target.value)}
            />
          </label>
        )}
      </div>

      <label className="mt-4 block text-sm text-muted-warm">
        Title
        <input
          className={fieldClass}
          value={title}
          onChange={(event) => setTitle(event.target.value)}
          placeholder="Optional"
        />
      </label>

      {submitting && mode === "file" ? (
        <div className="mt-4 text-sm text-muted-warm">
          <p>Uploading to server · {progress}%</p>
          <p>Processing on server starts after upload</p>
        </div>
      ) : null}
      {error ? <p role="alert" className="mt-4 text-sm text-destructive">{error}</p> : null}
      <Button className="mt-6" type="submit" disabled={submitting}>
        Start processing
      </Button>
    </form>
  )
}

function hasAllowedExtension(filename: string): boolean {
  const lower = filename.toLowerCase()
  return ACCEPTED_EXTENSIONS.some((extension) => lower.endsWith(extension))
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return error.requestId
      ? `${error.message} (Request ${error.requestId})`
      : error.message
  }
  return "Unable to submit Episode"
}
