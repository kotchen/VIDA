import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { describe, expect, it, vi } from "vitest"

import { ChaptersCard } from "@/components/dashboard/ChaptersCard"
import { ExportCard } from "@/components/dashboard/ExportCard"
import { ChapterEditor } from "@/features/episode/ChapterEditor"
import { DeleteEpisodeDialog } from "@/features/episode/DeleteEpisodeDialog"
import type { Chapter } from "@/api/types"

const generated: Chapter = {
  id: "generated",
  startSec: 0,
  title: "Generated",
  durationSec: 50,
  thumbnailUrl: null,
  bookmarked: false,
  source: "generated",
}
const manual: Chapter = {
  ...generated,
  id: "manual",
  title: "Manual",
  startSec: 50,
  source: "manual",
}

describe("Episode content actions", () => {
  it("bookmarks both sources but exposes content controls only for manual chapters", () => {
    const bookmark = vi.fn()
    render(
      <ChaptersCard
        chapters={[generated, manual]}
        onBookmark={bookmark}
        onEdit={() => undefined}
        onDelete={() => undefined}
      />,
    )

    expect(screen.queryByLabelText("Edit Generated")).not.toBeInTheDocument()
    expect(screen.queryByLabelText("Delete Generated")).not.toBeInTheDocument()
    expect(screen.getByLabelText("Edit Manual")).toBeInTheDocument()
    expect(screen.getByLabelText("Delete Manual")).toBeInTheDocument()
    fireEvent.click(screen.getByLabelText("Bookmark Generated"))
    fireEvent.click(screen.getByLabelText("Bookmark Manual"))
    expect(bookmark).toHaveBeenNthCalledWith(1, generated)
    expect(bookmark).toHaveBeenNthCalledWith(2, manual)
  })

  it("sends only changed manual fields", async () => {
    const submit = vi.fn().mockResolvedValue(undefined)
    render(
      <ChapterEditor
        chapter={manual}
        episodeDurationSec={100}
        onSubmit={submit}
        onCancel={() => undefined}
      />,
    )
    fireEvent.change(screen.getByLabelText("Chapter title"), {
      target: { value: "Renamed" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Save chapter" }))
    await waitFor(() => expect(submit).toHaveBeenCalledWith({ title: "Renamed" }))
  })

  it("requires typing the Episode title before deletion", async () => {
    const confirm = vi.fn().mockResolvedValue(undefined)
    render(
      <DeleteEpisodeDialog
        title="My Episode"
        deleting={false}
        onConfirm={confirm}
        onCancel={() => undefined}
      />,
    )
    const button = screen.getByRole("button", { name: "Confirm delete" })
    expect(button).toBeDisabled()
    fireEvent.change(screen.getByLabelText("Confirm Episode title"), {
      target: { value: "My Episode" },
    })
    fireEvent.click(button)
    await waitFor(() => expect(confirm).toHaveBeenCalledOnce())
  })

  it("uses encoded same-origin export links", () => {
    render(<ExportCard episodeId="episode/one" />)
    expect(screen.getByText("MD").closest("a")).toHaveAttribute(
      "href",
      "/api/v2/episodes/episode%2Fone/export?format=md",
    )
  })
})
