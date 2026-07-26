import { useState } from "react"

import { Button } from "@/components/ui/button"

export function DeleteEpisodeDialog({
  title,
  deleting,
  onConfirm,
  onCancel,
}: {
  title: string
  deleting: boolean
  onConfirm: () => Promise<void>
  onCancel: () => void
}) {
  const [confirmation, setConfirmation] = useState("")
  return (
    <section role="dialog" aria-modal="true" className="card-glow rounded-xl bg-card p-6">
      <h2 className="text-lg font-semibold">Delete Episode</h2>
      <p>Type “{title}” to permanently delete this Episode and its content.</p>
      <label>
        Confirm Episode title
        <input value={confirmation} onChange={(event) => setConfirmation(event.target.value)} />
      </label>
      <Button
        variant="destructive"
        disabled={deleting || confirmation !== title}
        onClick={() => void onConfirm().catch(() => undefined)}
      >
        Confirm delete
      </Button>
      <Button variant="outline" onClick={onCancel}>Cancel</Button>
    </section>
  )
}
