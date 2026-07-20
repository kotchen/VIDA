import { render, screen } from "@testing-library/react"
import { TranscriptCard } from "../components/dashboard/TranscriptCard"
import { mockDashboard } from "../data/mock"

describe("TranscriptCard", () => {
  it("renders all segments with timestamps and speakers", () => {
    render(<TranscriptCard segments={mockDashboard.transcript} />)
    expect(screen.getByText("Transcript")).toBeInTheDocument()
    expect(screen.getByText("00:00:00")).toBeInTheDocument()
    expect(screen.getByText("00:00:09")).toBeInTheDocument()
    expect(screen.getByText(/Welcome back to the AI Podcast!/)).toBeInTheDocument()
    expect(screen.getByText(/Exactly. Bias, transparency/)).toBeInTheDocument()
    expect(screen.getAllByText("Alex:")).toHaveLength(3)
    expect(screen.getAllByText("Sam:")).toHaveLength(3)
  })

  it("highlights the first segment as current", () => {
    render(<TranscriptCard segments={mockDashboard.transcript} />)
    const first = screen.getByText(/Welcome back to the AI Podcast!/).closest("li")
    expect(first?.className).toContain("bg-copper-500/15")
  })
})
