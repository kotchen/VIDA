import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { MemoryRouter, Route, Routes } from "react-router"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { ApiError } from "@/api/client"
import { episodesApi } from "@/api/episodes"
import type { Episode } from "@/api/types"
import { EpisodePage } from "@/pages/EpisodePage"

vi.mock("@/api/episodes", () => ({
  episodesApi: {
    get: vi.fn(),
    getTranscript: vi.fn(),
    getSummary: vi.fn(),
    getChapters: vi.fn(),
    cancel: vi.fn(),
    retry: vi.fn(),
    exportUrl: vi.fn((id: string, format: string) =>
      `/api/v2/episodes/${id}/export?format=${format}`,
    ),
  },
}))
vi.mock("@/features/events/useV2Events", () => ({
  useV2Events: vi.fn(),
  useFallbackRefresh: vi.fn(),
}))

function episode(status: Episode["status"]): Episode {
  return {
    id: "episode-1",
    title: "Lifecycle Episode",
    sourceType: "upload",
    mediaUrl: status === "completed" ? "/api/v2/media" : null,
    posterUrl: null,
    durationSec: 120,
    resolution: null,
    status,
    language: "en",
    createdAt: "2026-07-25T00:00:00Z",
    progress: status === "completed" ? 100 : 42,
    message: status === "processing" ? "Transcribing" : "Queued",
    queuePosition: status === "queued" ? 2 : null,
    providerProfileId: "profile-1",
    warnings:
      status === "completed"
        ? [{ stage: "summary", code: "partial", message: "Summary fallback" }]
        : [],
  }
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/episodes/episode-1"]}>
      <Routes>
        <Route path="/episodes/:id" element={<EpisodePage />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe("EpisodePage", () => {
  beforeEach(() => {
    vi.mocked(episodesApi.get).mockReset()
    vi.mocked(episodesApi.getTranscript).mockReset().mockResolvedValue([
      { id: "seg-1", startSec: 0, endSec: 4, speaker: "Alex", text: "Hello" },
    ])
    vi.mocked(episodesApi.getSummary).mockReset().mockResolvedValue({
      episodeId: "episode-1",
      content: "Summary content",
      readTimeMin: 1,
      keyPoints: 2,
      confidence: 90,
      generatedBy: "VIDA",
    })
    vi.mocked(episodesApi.getChapters).mockReset().mockResolvedValue([
      {
        id: "chapter-1",
        startSec: 0,
        title: "Opening",
        durationSec: 120,
        thumbnailUrl: null,
        bookmarked: false,
        source: "generated",
      },
    ])
    vi.mocked(episodesApi.cancel).mockReset().mockResolvedValue({} as never)
    vi.mocked(episodesApi.retry).mockReset().mockResolvedValue({} as never)
  })

  it.each([
    ["queued", /Queue position: 2/, "Cancel"],
    ["processing", /Transcribing/, "Cancel"],
    ["failed", /Processing failed/, "Retry"],
    ["canceled", /Processing canceled/, "Retry"],
  ] as const)("renders %s lifecycle controls", async (status, text, action) => {
    vi.mocked(episodesApi.get).mockResolvedValue(episode(status))
    renderPage()

    expect(await screen.findByText(text)).toBeInTheDocument()
    expect(screen.getByRole("button", { name: action })).toBeInTheDocument()
    if (status === "failed" || status === "canceled") {
      expect(
        screen.getByRole("button", { name: "Delete Episode" }),
      ).toBeInTheDocument()
    }
  })

  it("loads completed content and groups warnings", async () => {
    vi.mocked(episodesApi.get).mockResolvedValue(episode("completed"))
    renderPage()

    expect(await screen.findByTitle("Lifecycle Episode")).toHaveAttribute(
      "src",
      "/api/v2/media",
    )
    expect(await screen.findByText("Hello")).toBeInTheDocument()
    expect(screen.getByText("Summary content")).toBeInTheDocument()
    expect(screen.getByText("Opening")).toBeInTheDocument()
    expect(screen.getByText("Export")).toBeInTheDocument()
    expect(screen.getByText(/summary warnings/i)).toBeInTheDocument()
    expect(episodesApi.getTranscript).toHaveBeenCalledWith(
      "episode-1",
      expect.any(AbortSignal),
    )
  })

  it("treats summary_not_found as a local empty state", async () => {
    vi.mocked(episodesApi.get).mockResolvedValue(episode("completed"))
    vi.mocked(episodesApi.getSummary).mockRejectedValue(
      new ApiError(404, "summary_not_found", "Missing"),
    )
    renderPage()
    expect(await screen.findByText("Summary is not available yet.")).toBeInTheDocument()
  })

  it("refreshes after cancel and retry actions", async () => {
    vi.mocked(episodesApi.get)
      .mockResolvedValueOnce(episode("processing"))
      .mockResolvedValueOnce(episode("canceled"))
    renderPage()
    fireEvent.click(await screen.findByRole("button", { name: "Cancel" }))
    await waitFor(() => expect(episodesApi.cancel).toHaveBeenCalledWith("episode-1"))
    await waitFor(() => expect(episodesApi.get).toHaveBeenCalledTimes(2))
  })

  it("shows a deleted prompt for episode_not_found", async () => {
    vi.mocked(episodesApi.get).mockRejectedValue(
      new ApiError(404, "episode_not_found", "Missing"),
    )
    renderPage()
    expect(await screen.findByText("This Episode was deleted or does not exist.")).toBeInTheDocument()
    expect(screen.getByRole("link", { name: "Return to Library" })).toHaveAttribute(
      "href",
      "/library",
    )
  })
})
