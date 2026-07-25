import { describe, expectTypeOf, it } from "vitest"

import type {
  Chapter,
  DashboardData,
  Episode,
  EpisodeStatus,
  Job,
  ProviderProfile,
  V2Event,
} from "@/api/types"
import { mockDashboard } from "@/data/mock"

describe("v2 public types", () => {
  it("matches nullable and complete response fields", () => {
    expectTypeOf<DashboardData["currentEpisode"]>().toEqualTypeOf<Episode | null>()
    expectTypeOf<DashboardData["summary"]>().toBeNullable()
    expectTypeOf<Episode["mediaUrl"]>().toEqualTypeOf<string | null>()
    expectTypeOf<Episode["posterUrl"]>().toEqualTypeOf<string | null>()
    expectTypeOf<Chapter["thumbnailUrl"]>().toEqualTypeOf<string | null>()
    expectTypeOf<EpisodeStatus>().toEqualTypeOf<
      "queued" | "processing" | "completed" | "failed" | "canceled"
    >()
    expectTypeOf<Job["providerProfileRevisionId"]>().toBeString()
    expectTypeOf<ProviderProfile["apiKeyMasked"]>().toBeString()
    expectTypeOf<V2Event["type"]>().toEqualTypeOf<
      | "episode.updated"
      | "episode.deleted"
      | "job.updated"
      | "profiles.invalidated"
      | "dashboard.invalidated"
    >()
  })

  it("keeps mock data as a typed fixture", () => {
    expectTypeOf(mockDashboard).toMatchTypeOf<DashboardData>()
  })
})
