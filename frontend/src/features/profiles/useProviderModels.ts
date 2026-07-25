import { useCallback, useEffect, useRef, useState } from "react"

import { ApiError } from "@/api/client"
import { profilesApi } from "@/api/profiles"
import type { ProviderModelOption } from "@/api/types"

export type ProviderModelsStatus = "idle" | "loading" | "success" | "error"

interface UseProviderModelsInput {
  profileId?: string
  baseUrl: string
  apiKey: string
}

interface UseProviderModelsResult {
  models: ProviderModelOption[]
  status: ProviderModelsStatus
  message: string
  canFetch: boolean
  refresh: () => void
}

export function useProviderModels({
  profileId,
  baseUrl,
  apiKey,
}: UseProviderModelsInput): UseProviderModelsResult {
  const [models, setModels] = useState<ProviderModelOption[]>([])
  const [status, setStatus] = useState<ProviderModelsStatus>("idle")
  const [message, setMessage] = useState("")
  const controllerRef = useRef<AbortController | null>(null)
  const requestTokenRef = useRef(0)
  const profileIdentityRef = useRef(profileId)
  const normalizedBaseUrl = baseUrl.trim()
  const normalizedApiKey = apiKey.trim()
  const canFetch =
    validProviderUrl(normalizedBaseUrl) &&
    Boolean(profileId || normalizedApiKey)

  useEffect(() => {
    if (profileIdentityRef.current === profileId) return
    profileIdentityRef.current = profileId
    controllerRef.current?.abort()
    requestTokenRef.current += 1
    setModels([])
    setStatus("idle")
    setMessage("")
  }, [profileId])

  const execute = useCallback(async () => {
    if (!canFetch) return

    controllerRef.current?.abort()
    const controller = new AbortController()
    controllerRef.current = controller
    const token = ++requestTokenRef.current
    setStatus("loading")
    setMessage("Fetching models…")

    try {
      const result = await profilesApi.discoverModels(
        {
          ...(profileId ? { profileId } : {}),
          baseUrl: normalizedBaseUrl,
          ...(normalizedApiKey ? { apiKey: normalizedApiKey } : {}),
        },
        controller.signal,
      )
      if (
        controller.signal.aborted ||
        token !== requestTokenRef.current
      ) {
        return
      }
      setModels(result.models)
      setStatus("success")
      setMessage(
        result.models.length === 0
          ? "No models returned"
          : `Loaded ${result.models.length} models · ${result.latencyMs} ms`,
      )
    } catch (caught) {
      if (
        controller.signal.aborted ||
        token !== requestTokenRef.current
      ) {
        return
      }
      setStatus("error")
      setMessage(
        caught instanceof ApiError
          ? caught.message
          : "Unable to fetch provider models",
      )
    }
  }, [
    canFetch,
    normalizedApiKey,
    normalizedBaseUrl,
    profileId,
  ])

  useEffect(() => {
    if (!canFetch) {
      controllerRef.current?.abort()
      requestTokenRef.current += 1
      setStatus("idle")
      setMessage("")
      return
    }

    const timer = setTimeout(() => void execute(), 900)
    return () => {
      clearTimeout(timer)
      controllerRef.current?.abort()
      requestTokenRef.current += 1
    }
  }, [canFetch, execute])

  const refresh = useCallback(() => {
    if (canFetch) void execute()
  }, [canFetch, execute])

  return { models, status, message, canFetch, refresh }
}

function validProviderUrl(value: string): boolean {
  try {
    const url = new URL(value)
    return url.protocol === "http:" || url.protocol === "https:"
  } catch {
    return false
  }
}
