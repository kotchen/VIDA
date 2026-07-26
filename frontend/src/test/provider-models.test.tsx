import { act, renderHook } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { profilesApi } from "@/api/profiles"
import type { ProviderModelDiscovery } from "@/api/types"
import { useProviderModels } from "@/features/profiles/useProviderModels"

vi.mock("@/api/profiles", () => ({
  profilesApi: {
    discoverModels: vi.fn(),
  },
}))

interface Deferred<T> {
  promise: Promise<T>
  resolve: (value: T) => void
  reject: (reason: unknown) => void
}

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((onResolve, onReject) => {
    resolve = onResolve
    reject = onReject
  })
  return { promise, resolve, reject }
}

describe("useProviderModels", () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.mocked(profilesApi.discoverModels).mockReset().mockResolvedValue({
      models: [{ id: "model-a", name: "Model A" }],
      latencyMs: 18,
    })
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it("waits 900ms before fetching draft credentials", async () => {
    const { result } = renderHook(() =>
      useProviderModels({
        baseUrl: "https://api.example/v1",
        apiKey: "draft-secret",
      }),
    )

    await act(async () => vi.advanceTimersByTimeAsync(899))
    expect(profilesApi.discoverModels).not.toHaveBeenCalled()

    await act(async () => vi.advanceTimersByTimeAsync(1))

    expect(profilesApi.discoverModels).toHaveBeenCalledWith(
      {
        baseUrl: "https://api.example/v1",
        apiKey: "draft-secret",
      },
      expect.any(AbortSignal),
    )
    expect(result.current.models).toEqual([
      { id: "model-a", name: "Model A" },
    ])
    expect(result.current.status).toBe("success")
    expect(result.current.message).toBe("Loaded 1 models · 18 ms")
  })

  it("reuses an existing profile without sending an empty API key", async () => {
    renderHook(() =>
      useProviderModels({
        profileId: "profile-1",
        baseUrl: "https://api.example/v1",
        apiKey: "",
      }),
    )

    await act(async () => vi.advanceTimersByTimeAsync(900))

    expect(profilesApi.discoverModels).toHaveBeenCalledWith(
      {
        profileId: "profile-1",
        baseUrl: "https://api.example/v1",
      },
      expect.any(AbortSignal),
    )
  })

  it("stays idle for incomplete input and refreshes eligible input immediately", async () => {
    const { result, rerender } = renderHook(
      ({ baseUrl, apiKey }) => useProviderModels({ baseUrl, apiKey }),
      { initialProps: { baseUrl: "not-a-url", apiKey: "secret" } },
    )

    await act(async () => vi.advanceTimersByTimeAsync(900))
    expect(result.current.canFetch).toBe(false)
    expect(result.current.status).toBe("idle")
    expect(profilesApi.discoverModels).not.toHaveBeenCalled()

    rerender({
      baseUrl: "https://api.example/v1",
      apiKey: "secret",
    })
    await act(async () => result.current.refresh())

    expect(profilesApi.discoverModels).toHaveBeenCalledTimes(1)
    expect(result.current.status).toBe("success")
  })

  it("keeps the newest response when an aborted request resolves late", async () => {
    const first = deferred<ProviderModelDiscovery>()
    const second = deferred<ProviderModelDiscovery>()
    vi.mocked(profilesApi.discoverModels)
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise)
    const { result, rerender, unmount } = renderHook(
      ({ baseUrl }) =>
        useProviderModels({ baseUrl, apiKey: "draft-secret" }),
      { initialProps: { baseUrl: "https://first.example/v1" } },
    )

    await act(async () => vi.advanceTimersByTimeAsync(900))
    const firstSignal = vi.mocked(profilesApi.discoverModels).mock.calls[0][1]
    rerender({ baseUrl: "https://second.example/v1" })
    expect(firstSignal?.aborted).toBe(true)
    await act(async () => vi.advanceTimersByTimeAsync(900))

    await act(async () => {
      second.resolve({
        models: [{ id: "new-model", name: "New model" }],
        latencyMs: 12,
      })
      await second.promise
    })
    await act(async () => {
      first.resolve({
        models: [{ id: "old-model", name: "Old model" }],
        latencyMs: 99,
      })
      await first.promise
    })

    expect(result.current.models).toEqual([
      { id: "new-model", name: "New model" },
    ])
    expect(result.current.message).toBe("Loaded 1 models · 12 ms")

    const secondSignal = vi.mocked(profilesApi.discoverModels).mock.calls[1][1]
    unmount()
    expect(secondSignal?.aborted).toBe(true)
  })

  it("reports empty and safe error states without discarding prior models", async () => {
    const { result, rerender } = renderHook(
      ({ baseUrl }) =>
        useProviderModels({ baseUrl, apiKey: "draft-secret" }),
      { initialProps: { baseUrl: "https://first.example/v1" } },
    )
    await act(async () => vi.advanceTimersByTimeAsync(900))
    expect(result.current.models).toHaveLength(1)

    vi.mocked(profilesApi.discoverModels).mockRejectedValueOnce(
      new Error("raw upstream secret"),
    )
    rerender({ baseUrl: "https://failed.example/v1" })
    await act(async () => vi.advanceTimersByTimeAsync(900))
    expect(result.current.status).toBe("error")
    expect(result.current.message).toBe("Unable to fetch provider models")
    expect(result.current.models).toHaveLength(1)

    vi.mocked(profilesApi.discoverModels).mockResolvedValueOnce({
      models: [],
      latencyMs: 8,
    })
    rerender({ baseUrl: "https://empty.example/v1" })
    await act(async () => vi.advanceTimersByTimeAsync(900))
    expect(result.current.status).toBe("success")
    expect(result.current.message).toBe("No models returned")
    expect(result.current.models).toEqual([])
  })

  it("clears models when switching between provider profiles", async () => {
    const { result, rerender } = renderHook(
      ({ profileId }) =>
        useProviderModels({
          profileId,
          baseUrl: "https://api.example/v1",
          apiKey: "",
        }),
      { initialProps: { profileId: "profile-1" } },
    )
    await act(async () => vi.advanceTimersByTimeAsync(900))
    expect(result.current.models).toHaveLength(1)

    rerender({ profileId: "profile-2" })

    expect(result.current.models).toEqual([])
    expect(result.current.status).toBe("idle")
    expect(result.current.message).toBe("")
  })
})
