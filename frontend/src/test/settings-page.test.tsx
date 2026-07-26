import { act, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { SettingsPage } from "@/pages/SettingsPage"
import { profilesApi } from "@/api/profiles"
import type { ProviderProfile } from "@/api/types"
import {
  getSubmissionPreferences,
  setSubmissionPreferences,
} from "@/features/profiles/preferences"

vi.mock("@/api/profiles", () => ({
  profilesApi: {
    list: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    delete: vi.fn(),
    test: vi.fn(),
    discoverModels: vi.fn(),
  },
}))

vi.mock("@/features/events/useV2Events", () => ({
  useV2Events: vi.fn(),
}))

const profile: ProviderProfile = {
  id: "profile-1",
  name: "Primary",
  baseUrl: "https://api.example/v1",
  modelId: "model-1",
  temperature: 0.1,
  revision: 3,
  activeRevisionId: "revision-3",
  apiKeyMasked: "••••cret",
  hasApiKey: true,
  createdAt: "2026-07-25T00:00:00Z",
  updatedAt: "2026-07-25T00:00:00Z",
}

describe("SettingsPage", () => {
  beforeEach(() => {
    localStorage.clear()
    vi.mocked(profilesApi.list).mockReset().mockResolvedValue([profile])
    vi.mocked(profilesApi.create).mockReset().mockResolvedValue(profile)
    vi.mocked(profilesApi.update).mockReset().mockResolvedValue(profile)
    vi.mocked(profilesApi.delete).mockReset().mockResolvedValue(undefined)
    vi.mocked(profilesApi.test).mockReset().mockResolvedValue({
      ok: true,
      latencyMs: 18,
      modelAvailable: true,
      message: "Connection successful",
    })
    vi.mocked(profilesApi.discoverModels).mockReset().mockResolvedValue({
      models: [
        { id: "model-1", name: "Model One" },
        { id: "model-2", name: "Model Two" },
      ],
      latencyMs: 18,
    })
  })

  it("renders existing provider profiles", async () => {
    render(<SettingsPage />)
    expect(await screen.findByText("Primary")).toBeInTheDocument()
    expect(screen.getByText("••••cret")).toBeInTheDocument()
    expect(screen.getByText("Revision 3")).toBeInTheDocument()
  })

  it("edits without sending an empty API key and tests connection", async () => {
    render(<SettingsPage />)
    await screen.findByText("Primary")

    fireEvent.click(screen.getByRole("button", { name: "Edit Primary" }))
    expect(screen.getByLabelText("API key")).toHaveValue("")
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "Renamed" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Save profile" }))

    await waitFor(() =>
      expect(profilesApi.update).toHaveBeenCalledWith(
        "profile-1",
        expect.not.objectContaining({ apiKey: expect.anything() }),
      ),
    )

    fireEvent.click(screen.getByRole("button", { name: "Test Primary" }))
    expect(await screen.findByText(/Connection successful.*18 ms/)).toBeInTheDocument()
  })

  it("auto-fetches models and submits the selected model", async () => {
    render(<SettingsPage />)
    await screen.findByText("Primary")
    vi.useFakeTimers()
    try {
      fireEvent.click(screen.getByRole("button", { name: "New profile" }))
      fireEvent.change(screen.getByLabelText("Name"), {
        target: { value: "Secondary" },
      })
      fireEvent.change(screen.getByLabelText("Base URL"), {
        target: { value: "https://second.example/v1" },
      })
      fireEvent.change(screen.getByLabelText("API key"), {
        target: { value: "secret-key" },
      })

      await act(async () => vi.advanceTimersByTimeAsync(900))

      expect(profilesApi.discoverModels).toHaveBeenCalledWith(
        {
          baseUrl: "https://second.example/v1",
          apiKey: "secret-key",
        },
        expect.any(AbortSignal),
      )
      expect(screen.getByText("Loaded 2 models · 18 ms")).toBeInTheDocument()
      fireEvent.change(screen.getByRole("combobox", { name: "Model ID" }), {
        target: { value: "model-2" },
      })
      fireEvent.click(screen.getByRole("button", { name: "Create profile" }))

      await act(async () => Promise.resolve())
      expect(profilesApi.create).toHaveBeenCalledWith({
        name: "Secondary",
        baseUrl: "https://second.example/v1",
        apiKey: "secret-key",
        modelId: "model-2",
        temperature: 0.1,
      })
    } finally {
      vi.useRealTimers()
    }
  })

  it("fetches immediately from the manual button and disables it while loading", async () => {
    let resolveDiscovery:
      | ((value: {
          models: { id: string; name: string }[]
          latencyMs: number
        }) => void)
      | undefined
    vi.mocked(profilesApi.discoverModels).mockReturnValueOnce(
      new Promise((resolve) => {
        resolveDiscovery = resolve
      }),
    )
    render(<SettingsPage />)
    await screen.findByText("Primary")
    fireEvent.click(screen.getByRole("button", { name: "New profile" }))
    fireEvent.change(screen.getByLabelText("Base URL"), {
      target: { value: "https://second.example/v1" },
    })
    fireEvent.change(screen.getByLabelText("API key"), {
      target: { value: "secret-key" },
    })

    const fetchButton = screen.getByRole("button", { name: "Fetch models" })
    fireEvent.click(fetchButton)

    expect(profilesApi.discoverModels).toHaveBeenCalledTimes(1)
    expect(fetchButton).toBeDisabled()
    expect(screen.getByText("Fetching models…")).toBeInTheDocument()
    await act(async () => {
      resolveDiscovery?.({
        models: [{ id: "model-2", name: "Model Two" }],
        latencyMs: 9,
      })
    })
    expect(fetchButton).toBeEnabled()
  })

  it("reuses saved credentials and preserves a current missing model", async () => {
    vi.mocked(profilesApi.discoverModels).mockResolvedValueOnce({
      models: [{ id: "model-2", name: "Model Two" }],
      latencyMs: 7,
    })
    render(<SettingsPage />)
    await screen.findByText("Primary")
    vi.useFakeTimers()
    try {
      fireEvent.click(screen.getByRole("button", { name: "Edit Primary" }))
      await act(async () => vi.advanceTimersByTimeAsync(900))

      expect(profilesApi.discoverModels).toHaveBeenCalledWith(
        {
          profileId: "profile-1",
          baseUrl: "https://api.example/v1",
        },
        expect.any(AbortSignal),
      )
      expect(
        screen.getByRole("option", {
          name: "Current: model-1 — not returned by provider",
        }),
      ).toBeInTheDocument()
      expect(screen.getByRole("combobox", { name: "Model ID" })).toHaveValue(
        "model-1",
      )
    } finally {
      vi.useRealTimers()
    }
  })

  it("requires explicit delete confirmation and clears selected preference", async () => {
    setSubmissionPreferences({
      providerProfileId: "profile-1",
      summaryLanguage: "en",
    })
    render(<SettingsPage />)
    await screen.findByText("Primary")

    fireEvent.click(screen.getByRole("button", { name: "Delete Primary" }))
    expect(profilesApi.delete).not.toHaveBeenCalled()
    expect(
      screen.getByText(/Permanently delete “Primary”/),
    ).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Confirm delete" }))

    await waitFor(() =>
      expect(profilesApi.delete).toHaveBeenCalledWith("profile-1"),
    )
    expect(getSubmissionPreferences()).toEqual({
      providerProfileId: null,
      summaryLanguage: "en",
    })
  })

  it("falls back safely when stored preferences are malformed", () => {
    localStorage.setItem("vida.v2.submissionPreferences", "{broken")
    expect(getSubmissionPreferences()).toEqual({
      providerProfileId: null,
      summaryLanguage: "zh-Hans",
    })
  })

  it("migrates the legacy generic zh preference to Simplified Chinese", () => {
    setSubmissionPreferences({
      providerProfileId: "profile-1",
      summaryLanguage: "zh",
    })
    expect(getSubmissionPreferences()).toEqual({
      providerProfileId: "profile-1",
      summaryLanguage: "zh-Hans",
    })
  })
})
