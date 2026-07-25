import { afterEach, describe, expect, it, vi } from "vitest"

import { ApiError, apiBlob, apiRequest } from "@/api/client"
import { episodesApi } from "@/api/episodes"
import { profilesApi } from "@/api/profiles"

afterEach(() => {
  vi.unstubAllGlobals()
})

describe("apiRequest", () => {
  it("returns JSON and forwards an AbortSignal", async () => {
    const signal = new AbortController().signal
    const fetchMock = vi.fn().mockResolvedValue(
      Response.json({ id: "episode-1" }, {
        headers: { "X-Request-ID": "req-success" },
      }),
    )
    vi.stubGlobal("fetch", fetchMock)

    await expect(
      apiRequest<{ id: string }>("/api/v2/episodes/episode-1", { signal }),
    ).resolves.toEqual({ id: "episode-1" })
    expect(fetchMock.mock.calls[0][1]?.signal).toBe(signal)
  })

  it("normalizes the v2 error envelope", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        Response.json(
          {
            error: {
              code: "episode_not_found",
              message: "Episode not found",
              details: {},
              requestId: "body-request",
            },
          },
          {
            status: 404,
            headers: { "X-Request-ID": "req-1" },
          },
        ),
      ),
    )

    await expect(apiRequest("/api/v2/missing")).rejects.toMatchObject({
      httpStatus: 404,
      code: "episode_not_found",
      requestId: "req-1",
      details: {},
    })
  })

  it("returns undefined for 204 and preserves FormData headers", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }))
    vi.stubGlobal("fetch", fetchMock)
    const body = new FormData()
    body.set("title", "Episode")

    await expect(
      apiRequest<void>("/api/v2/episodes/one", {
        method: "DELETE",
        body,
      }),
    ).resolves.toBeUndefined()
    const request = fetchMock.mock.calls[0][1] as RequestInit
    expect(request.body).toBe(body)
    expect(new Headers(request.headers).has("Content-Type")).toBe(false)
  })

  it("uses safe fallbacks for malformed errors and network failures", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response("secret upstream response", {
          status: 502,
          headers: { "X-Request-ID": "req-malformed" },
        }),
      ),
    )
    const malformed = apiRequest("/api/v2/episodes")
    await expect(malformed).rejects.toMatchObject({
      httpStatus: 502,
      code: "http_error",
      message: "Request failed",
      requestId: "req-malformed",
    })
    await expect(malformed).rejects.not.toThrow("secret upstream response")

    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("private URL")))
    await expect(apiRequest("/api/v2/episodes")).rejects.toEqual(
      new ApiError(0, "network_error", "Unable to reach the server"),
    )
  })

  it("rejects absolute API URLs before calling fetch", async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal("fetch", fetchMock)

    await expect(apiRequest("https://example.test/api/v2")).rejects.toMatchObject({
      code: "invalid_api_path",
    })
    expect(fetchMock).not.toHaveBeenCalled()
  })
})

describe("apiBlob", () => {
  it("preserves download metadata", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response("report", {
          headers: {
            "Content-Disposition": 'attachment; filename="report.md"',
            "X-Request-ID": "req-blob",
          },
        }),
      ),
    )

    const result = await apiBlob("/api/v2/episodes/one/export?format=md")
    expect(await result.blob.text()).toBe("report")
    expect(result.contentDisposition).toBe('attachment; filename="report.md"')
    expect(result.requestId).toBe("req-blob")
  })
})

describe("domain API paths", () => {
  it("encodes every dynamic path segment", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }))
    vi.stubGlobal("fetch", fetchMock)

    await episodesApi.deleteChapter("episode/one", "chapter two")
    await profilesApi.delete("profile/one")

    expect(fetchMock.mock.calls[0][0]).toBe(
      "/api/v2/episodes/episode%2Fone/chapters/chapter%20two",
    )
    expect(fetchMock.mock.calls[1][0]).toBe(
      "/api/v2/provider-profiles/profile%2Fone",
    )
    expect(episodesApi.exportUrl("episode/one", "md")).toBe(
      "/api/v2/episodes/episode%2Fone/export?format=md",
    )
  })
})
