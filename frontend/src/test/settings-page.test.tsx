import { fireEvent, render, screen, waitFor } from "@testing-library/react"
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
  })

  it("renders profiles and creates a complete profile", async () => {
    render(<SettingsPage />)
    expect(await screen.findByText("Primary")).toBeInTheDocument()
    expect(screen.getByText("••••cret")).toBeInTheDocument()
    expect(screen.getByText("Revision 3")).toBeInTheDocument()

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
    fireEvent.change(screen.getByLabelText("Model ID"), {
      target: { value: "model-2" },
    })
    fireEvent.change(screen.getByLabelText("Temperature"), {
      target: { value: "0.4" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Create profile" }))

    await waitFor(() =>
      expect(profilesApi.create).toHaveBeenCalledWith({
        name: "Secondary",
        baseUrl: "https://second.example/v1",
        apiKey: "secret-key",
        modelId: "model-2",
        temperature: 0.4,
      }),
    )
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
      summaryLanguage: "zh",
    })
  })
})
