import { useState, type FormEvent } from "react"

import type { Chapter, ChapterCreateInput, ChapterUpdateInput } from "@/api/types"
import { Button } from "@/components/ui/button"

export function ChapterEditor({
  chapter,
  episodeDurationSec,
  onSubmit,
  onCancel,
}: {
  chapter?: Chapter
  episodeDurationSec: number
  onSubmit: (input: ChapterCreateInput | ChapterUpdateInput) => Promise<void>
  onCancel: () => void
}) {
  const [startSec, setStartSec] = useState(String(chapter?.startSec ?? 0))
  const [title, setTitle] = useState(chapter?.title ?? "")
  const [error, setError] = useState<string | null>(null)

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    const start = Number(startSec)
    if (!Number.isFinite(start) || start < 0 || start > episodeDurationSec) {
      setError("Start must be within the Episode duration")
      return
    }
    if (!title.trim()) {
      setError("Title is required")
      return
    }
    const input = chapter
      ? {
          ...(start !== chapter.startSec ? { startSec: start } : {}),
          ...(title.trim() !== chapter.title ? { title: title.trim() } : {}),
        }
      : { startSec: start, title: title.trim() }
    await onSubmit(input)
  }

  return (
    <form onSubmit={(event) => void submit(event).catch(() => undefined)}>
      <label>
        Chapter start
        <input
          type="number"
          value={startSec}
          onChange={(event) => setStartSec(event.target.value)}
        />
      </label>
      <label>
        Chapter title
        <input value={title} onChange={(event) => setTitle(event.target.value)} />
      </label>
      {error ? <p role="alert">{error}</p> : null}
      <Button type="submit">{chapter ? "Save chapter" : "Create chapter"}</Button>
      <Button type="button" variant="outline" onClick={onCancel}>Cancel</Button>
    </form>
  )
}
