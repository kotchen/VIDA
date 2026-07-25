import { useState } from "react"

import type {
  ProviderConnectionTest,
  ProviderProfile,
} from "@/api/types"
import { Button } from "@/components/ui/button"
import {
  ProfileForm,
  type ProfileFormInput,
} from "@/features/profiles/ProfileForm"
import { useProfiles } from "@/features/profiles/useProfiles"

export function SettingsPage() {
  const {
    profiles,
    loading,
    error,
    mutation,
    create,
    update,
    remove,
    testConnection,
  } = useProfiles()
  const [editing, setEditing] = useState<"new" | ProviderProfile | null>(null)
  const [confirming, setConfirming] = useState<ProviderProfile | null>(null)
  const [connection, setConnection] = useState<
    Record<string, ProviderConnectionTest>
  >({})

  const submit = async (input: ProfileFormInput) => {
    if (editing === "new") {
      if (!input.apiKey) return
      await create({ ...input, apiKey: input.apiKey })
    } else if (editing !== null) {
      await update(editing.id, input)
    }
    setEditing(null)
  }

  const runTest = async (profile: ProviderProfile) => {
    const result = await testConnection(profile.id)
    setConnection((current) => ({ ...current, [profile.id]: result }))
  }

  return (
    <div className="mx-auto max-w-6xl p-8">
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="font-display text-3xl text-gold">Provider settings</h1>
          <p className="mt-1 text-sm text-muted-warm">
            Credentials stay encrypted and are never shown again.
          </p>
        </div>
        <Button onClick={() => setEditing("new")}>New profile</Button>
      </div>

      {error ? <p role="alert" className="mb-4 text-sm text-destructive">{error}</p> : null}
      {loading ? <p className="text-muted-warm">Loading profiles…</p> : null}

      <div className="grid gap-4 lg:grid-cols-2">
        {profiles.map((profile) => (
          <article
            key={profile.id}
            className="card-glow rounded-xl bg-card p-5"
          >
            <div className="flex items-start justify-between gap-4">
              <div>
                <h2 className="font-semibold text-cream">{profile.name}</h2>
                <p className="mt-1 text-sm text-muted-warm">{profile.baseUrl}</p>
                <p className="mt-2 text-sm text-cream">{profile.modelId}</p>
              </div>
              <span className="text-xs text-muted-warm">
                Revision {profile.revision}
              </span>
            </div>
            <p className="mt-3 font-mono text-sm text-muted-warm">
              {profile.apiKeyMasked}
            </p>
            {connection[profile.id] ? (
              <p className="mt-3 text-sm text-success">
                {connection[profile.id].message} · {connection[profile.id].latencyMs} ms
              </p>
            ) : null}
            <div className="mt-4 flex flex-wrap gap-2">
              <Button
                size="sm"
                variant="outline"
                aria-label={`Edit ${profile.name}`}
                onClick={() => setEditing(profile)}
              >
                Edit
              </Button>
              <Button
                size="sm"
                variant="outline"
                aria-label={`Test ${profile.name}`}
                disabled={mutation !== null}
                onClick={() => void runTest(profile).catch(() => undefined)}
              >
                Test connection
              </Button>
              <Button
                size="sm"
                variant="destructive"
                aria-label={`Delete ${profile.name}`}
                disabled={mutation !== null}
                onClick={() => setConfirming(profile)}
              >
                Delete
              </Button>
            </div>
          </article>
        ))}
      </div>

      {editing !== null ? (
        <section className="card-glow mt-6 rounded-xl bg-card p-6">
          <ProfileForm
            profile={editing === "new" ? null : editing}
            submitting={mutation !== null}
            onCancel={() => setEditing(null)}
            onSubmit={submit}
          />
        </section>
      ) : null}

      {confirming ? (
        <section
          role="dialog"
          aria-modal="true"
          className="card-glow mt-6 rounded-xl bg-card p-6"
        >
          <p>Permanently delete “{confirming.name}”?</p>
          <p className="mt-1 text-sm text-muted-warm">
            Existing jobs keep their pinned revision, but new submissions cannot use it.
          </p>
          <div className="mt-4 flex gap-2">
            <Button
              variant="destructive"
              disabled={mutation !== null}
              onClick={() => {
                const profile = confirming
                void remove(profile.id)
                  .then(() => setConfirming(null))
                  .catch(() => undefined)
              }}
            >
              Confirm delete
            </Button>
            <Button variant="outline" onClick={() => setConfirming(null)}>
              Cancel
            </Button>
          </div>
        </section>
      ) : null}
    </div>
  )
}
