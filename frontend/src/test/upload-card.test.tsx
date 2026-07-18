import { render, screen } from "@testing-library/react"
import { UploadCard } from "../components/dashboard/UploadCard"

describe("UploadCard", () => {
  it("renders upload copy", () => {
    render(<UploadCard />)
    expect(screen.getByText("Upload a video or audio file")).toBeInTheDocument()
    expect(screen.getByText(/Drag & drop a file here/)).toBeInTheDocument()
    expect(screen.getByText(/MP4, MOV, MKV, AVI, MP3, M4A/)).toBeInTheDocument()
  })
})
