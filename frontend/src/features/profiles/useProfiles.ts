import { useCallback, useEffect, useState } from "react"

import { ApiError } from "@/api/client"
import { profilesApi } from "@/api/profiles"
import type {
  ProviderConnectionTest,
  ProviderProfile,
  ProviderProfileCreateInput,
  ProviderProfileUpdateInput,
} from "@/api/types"
import {
  getSubmissionPreferences,
  setSubmissionPreferences,
} from "@/features/profiles/preferences"
import { useV2Events } from "@/features/events/useV2Events"

export function useProfiles() {
  const [profiles, setProfiles] = useState<ProviderProfile[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [mutation, setMutation] = useState<string | null>(null)

  const load = useCallback(async (signal?: AbortSignal) => {
    try {
      const rows = await profilesApi.list(signal)
      setProfiles(rows)
      setError(null)
    } catch (caught) {
      if (signal?.aborted) return
      setError(errorMessage(caught))
    } finally {
      if (!signal?.aborted) setLoading(false)
    }
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    void load(controller.signal)
    return () => controller.abort()
  }, [load])

  useV2Events(
    (event) =>
      event.type === "profiles.invalidated" || event.type === "reconnected",
    () => void load(),
  )

  const run = useCallback(
    async <T,>(key: string, action: () => Promise<T>): Promise<T> => {
      setMutation(key)
      setError(null)
      try {
        return await action()
      } catch (caught) {
        setError(errorMessage(caught))
        throw caught
      } finally {
        setMutation(null)
      }
    },
    [],
  )

  const create = useCallback(
    (input: ProviderProfileCreateInput) =>
      run("create", async () => {
        const created = await profilesApi.create(input)
        setProfiles((current) => upsert(current, created))
        return created
      }),
    [run],
  )

  const update = useCallback(
    (id: string, input: ProviderProfileUpdateInput) =>
      run(id, async () => {
        const updated = await profilesApi.update(id, input)
        setProfiles((current) => upsert(current, updated))
        return updated
      }),
    [run],
  )

  const remove = useCallback(
    (id: string) =>
      run(id, async () => {
        await profilesApi.delete(id)
        setProfiles((current) => current.filter((profile) => profile.id !== id))
        const preferences = getSubmissionPreferences()
        if (preferences.providerProfileId === id) {
          setSubmissionPreferences({ ...preferences, providerProfileId: null })
        }
      }),
    [run],
  )

  const testConnection = useCallback(
    (id: string): Promise<ProviderConnectionTest> =>
      run(`test:${id}`, () => profilesApi.test(id)),
    [run],
  )

  return {
    profiles,
    loading,
    error,
    mutation,
    create,
    update,
    remove,
    testConnection,
  }
}

function upsert(
  profiles: ProviderProfile[],
  profile: ProviderProfile,
): ProviderProfile[] {
  const exists = profiles.some((current) => current.id === profile.id)
  return exists
    ? profiles.map((current) => (current.id === profile.id ? profile : current))
    : [...profiles, profile]
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return error.requestId
      ? `${error.message} (Request ${error.requestId})`
      : error.message
  }
  return "Unable to update provider profiles"
}
