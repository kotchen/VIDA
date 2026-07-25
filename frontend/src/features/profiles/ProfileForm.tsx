import { useEffect, useState, type FormEvent } from "react"

import type {
  ProviderProfile,
  ProviderProfileCreateInput,
} from "@/api/types"
import { Button } from "@/components/ui/button"

export type ProfileFormInput = Omit<ProviderProfileCreateInput, "apiKey"> & {
  apiKey?: string
}

interface ProfileFormProps {
  profile: ProviderProfile | null
  submitting: boolean
  onCancel: () => void
  onSubmit: (input: ProfileFormInput) => Promise<void>
}

export function ProfileForm({
  profile,
  submitting,
  onCancel,
  onSubmit,
}: ProfileFormProps) {
  const [name, setName] = useState("")
  const [baseUrl, setBaseUrl] = useState("")
  const [apiKey, setApiKey] = useState("")
  const [modelId, setModelId] = useState("")
  const [temperature, setTemperature] = useState("0.1")

  useEffect(() => {
    setName(profile?.name ?? "")
    setBaseUrl(profile?.baseUrl ?? "")
    setApiKey("")
    setModelId(profile?.modelId ?? "")
    setTemperature(String(profile?.temperature ?? 0.1))
  }, [profile])

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    const common = {
      name: name.trim(),
      baseUrl: baseUrl.trim(),
      modelId: modelId.trim(),
      temperature: Number(temperature),
    }
    const input = profile
      ? {
          ...common,
          ...(apiKey.trim() ? { apiKey: apiKey.trim() } : {}),
        }
      : { ...common, apiKey: apiKey.trim() }
    await onSubmit(input)
  }

  const fieldClass =
    "mt-1 w-full rounded-lg border border-warm bg-page px-3 py-2 text-sm text-cream outline-none focus:border-copper-500"

  return (
    <form
      onSubmit={(event) => void submit(event).catch(() => undefined)}
      className="space-y-4"
    >
      <h2 className="text-lg font-semibold text-gold">
        {profile ? `Edit ${profile.name}` : "New provider profile"}
      </h2>
      <label className="block text-sm text-muted-warm">
        Name
        <input
          className={fieldClass}
          value={name}
          onChange={(event) => setName(event.target.value)}
          required
        />
      </label>
      <label className="block text-sm text-muted-warm">
        Base URL
        <input
          className={fieldClass}
          type="url"
          value={baseUrl}
          onChange={(event) => setBaseUrl(event.target.value)}
          required
        />
      </label>
      <label className="block text-sm text-muted-warm">
        API key
        <input
          className={fieldClass}
          type="password"
          value={apiKey}
          onChange={(event) => setApiKey(event.target.value)}
          required={!profile}
          placeholder={profile ? "Leave blank to keep current key" : undefined}
        />
      </label>
      <label className="block text-sm text-muted-warm">
        Model ID
        <input
          className={fieldClass}
          value={modelId}
          onChange={(event) => setModelId(event.target.value)}
          required
        />
      </label>
      <label className="block text-sm text-muted-warm">
        Temperature
        <input
          className={fieldClass}
          type="number"
          min="0"
          max="2"
          step="0.1"
          value={temperature}
          onChange={(event) => setTemperature(event.target.value)}
          required
        />
      </label>
      <div className="flex gap-2">
        <Button type="submit" disabled={submitting}>
          {profile ? "Save profile" : "Create profile"}
        </Button>
        <Button type="button" variant="outline" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  )
}
