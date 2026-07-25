import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { MemoryRouter, Route, Routes } from "react-router"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { episodesApi } from "@/api/episodes"
import { profilesApi } from "@/api/profiles"
import type { Episode, ProviderProfile } from "@/api/types"
import { ApiError } from "@/api/client"
import { SubmissionForm } from "@/features/submission/SubmissionForm"
import { uploadEpisode } from "@/features/submission/upload"
import { setSubmissionPreferences } from "@/features/profiles/preferences"

vi.mock("@/api/episodes", () => ({
  episodesApi: { submitUrl: vi.fn() },
}))
vi.mock("@/api/profiles", () => ({
  profilesApi: { list: vi.fn() },
}))

const profile: ProviderProfile = {
  id: "profile-1",
  name: "Primary",
  baseUrl: "https://api.example/v1",
  modelId: "model",
  temperature: 0.1,
  revision: 1,
  activeRevisionId: "revision-1",
  apiKeyMasked: "••••cret",
  hasApiKey: true,
  createdAt: "2026-07-25T00:00:00Z",
  updatedAt: "2026-07-25T00:00:00Z",
}

const episode: Episode = {
  id: "episode-1",
  title: "Episode",
  sourceType: "url",
  mediaUrl: null,
  posterUrl: null,
  durationSec: 0,
  resolution: null,
  status: "queued",
  language: "en",
  createdAt: "2026-07-25T00:00:00Z",
  progress: 0,
  message: "Queued",
  queuePosition: 1,
  providerProfileId: "profile-1",
  warnings: [],
}

class FakeXMLHttpRequest {
  static instance: FakeXMLHttpRequest
  upload: { onprogress: ((event: ProgressEvent) => void) | null } = {
    onprogress: null,
  }
  status = 0
  responseText = ""
  onload: (() => void) | null = null
  onerror: (() => void) | null = null
  onabort: (() => void) | null = null
  body: Document | XMLHttpRequestBodyInit | null = null
  method = ""
  url = ""

  constructor() {
    FakeXMLHttpRequest.instance = this
  }

  open(method: string, url: string) {
    this.method = method
    this.url = url
  }

  send(body: Document | XMLHttpRequestBodyInit | null) {
    this.body = body
  }

  abort() {
    this.onabort?.()
  }

  getResponseHeader(name: string) {
    return name.toLowerCase() === "x-request-id" ? "req-xhr" : null
  }
}

describe("SubmissionForm", () => {
  beforeEach(() => {
    localStorage.clear()
    vi.mocked(profilesApi.list).mockReset().mockResolvedValue([profile])
    vi.mocked(episodesApi.submitUrl).mockReset().mockResolvedValue(episode)
  })

  it("keeps URL and file sources exclusive and submits URL JSON", async () => {
    setSubmissionPreferences({
      providerProfileId: "profile-1",
      summaryLanguage: "en",
    })
    render(
      <MemoryRouter initialEntries={["/transcribe"]}>
        <Routes>
          <Route path="/transcribe" element={<SubmissionForm />} />
          <Route path="/episodes/:id" element={<p>Episode destination</p>} />
        </Routes>
      </MemoryRouter>,
    )
    await screen.findByRole("option", { name: "Primary" })

    expect(screen.getByLabelText("Media file")).toBeInTheDocument()
    expect(screen.queryByLabelText("Source URL")).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Use URL" }))
    expect(screen.getByLabelText("Source URL")).toBeInTheDocument()
    expect(screen.queryByLabelText("Media file")).not.toBeInTheDocument()
    fireEvent.change(screen.getByLabelText("Source URL"), {
      target: { value: "https://media.example/one" },
    })
    fireEvent.change(screen.getByLabelText("Title"), {
      target: { value: "Optional title" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Start processing" }))

    await waitFor(() =>
      expect(episodesApi.submitUrl).toHaveBeenCalledWith({
        sourceUrl: "https://media.example/one",
        providerProfileId: "profile-1",
        summaryLanguage: "en",
        title: "Optional title",
      }),
    )
    expect(await screen.findByText("Episode destination")).toBeInTheDocument()
  })

  it("requires a profile and accepts only the advertised media extensions", async () => {
    render(
      <MemoryRouter>
        <SubmissionForm />
      </MemoryRouter>,
    )
    await screen.findByRole("option", { name: "Primary" })
    const input = screen.getByLabelText("Media file")
    expect(input).toHaveAttribute(
      "accept",
      ".mp3,.mp4,.m4a,.wav,.webm,.mkv,.ogg,.flac",
    )
    fireEvent.change(input, {
      target: { files: [new File(["x"], "clip.mov", { type: "video/quicktime" })] },
    })
    expect(screen.getByRole("alert")).toHaveTextContent("Unsupported file type")

    fireEvent.click(screen.getByRole("button", { name: "Use URL" }))
    fireEvent.change(screen.getByLabelText("Source URL"), {
      target: { value: "https://media.example/one" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Start processing" }))
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Choose a provider profile",
    )
  })
})

describe("uploadEpisode", () => {
  beforeEach(() => {
    vi.stubGlobal("XMLHttpRequest", FakeXMLHttpRequest)
  })

  it("sends exact multipart fields and reports integer upload progress", async () => {
    const progress = vi.fn()
    const promise = uploadEpisode(
      {
        file: new File(["media"], "clip.mp3"),
        providerProfileId: "profile-1",
        summaryLanguage: "en",
        title: "  Episode  ",
      },
      progress,
    )
    const xhr = FakeXMLHttpRequest.instance
    const body = xhr.body as FormData
    expect(xhr.method).toBe("POST")
    expect(xhr.url).toBe("/api/v2/episodes")
    expect([...body.keys()]).toEqual([
      "file",
      "providerProfileId",
      "summaryLanguage",
      "title",
    ])
    xhr.upload.onprogress?.({
      lengthComputable: true,
      loaded: 1,
      total: 3,
    } as ProgressEvent)
    expect(progress).toHaveBeenCalledWith(33)
    xhr.status = 202
    xhr.responseText = JSON.stringify(episode)
    xhr.onload?.()
    await expect(promise).resolves.toEqual(episode)
  })

  it("normalizes an XHR v2 error envelope", async () => {
    const promise = uploadEpisode(
      {
        file: new File(["media"], "clip.mp3"),
        providerProfileId: "profile-1",
        summaryLanguage: "en",
      },
      () => undefined,
    )
    const xhr = FakeXMLHttpRequest.instance
    xhr.status = 422
    xhr.responseText = JSON.stringify({
      error: {
        code: "provider_profile_inactive",
        message: "Profile inactive",
        details: {},
        requestId: "body-id",
      },
    })
    xhr.onload?.()
    await expect(promise).rejects.toEqual(
      new ApiError(
        422,
        "provider_profile_inactive",
        "Profile inactive",
        {},
        "req-xhr",
      ),
    )
  })
})
