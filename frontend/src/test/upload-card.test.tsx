import { fireEvent, render, screen } from "@testing-library/react"
import { UploadCard } from "../components/dashboard/UploadCard"

describe("UploadCard", () => {
  it("renders upload copy", () => {
    render(<UploadCard />)
    expect(screen.getByText("Upload a video or audio file")).toBeInTheDocument()
    expect(screen.getByText(/Drag & drop a file here/)).toBeInTheDocument()
    expect(
      screen.getByText(/MP3, MP4, M4A, WAV, WEBM, MKV, OGG, FLAC/),
    ).toBeInTheDocument()
    expect(screen.queryByText(/MOV|AVI/)).not.toBeInTheDocument()
  })

  it("passes selected files to its callback", () => {
    const onFileSelected = vi.fn()
    render(<UploadCard onFileSelected={onFileSelected} />)
    const file = new File(["media"], "episode.mp3")

    fireEvent.change(screen.getByLabelText("Quick upload media file"), {
      target: { files: [file] },
    })

    expect(onFileSelected).toHaveBeenCalledWith(file)
  })
})
