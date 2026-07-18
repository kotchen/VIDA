import { render, screen } from "@testing-library/react"
import { PlayerCard } from "../components/dashboard/PlayerCard"
import { mockDashboard } from "../data/mock"

describe("PlayerCard", () => {
  it("renders episode title and meta", () => {
    render(<PlayerCard episode={mockDashboard.currentEpisode} />)
    expect(screen.getByText("AI Podcast Episode 12")).toBeInTheDocument()
    expect(screen.getByText("May 16, 2024")).toBeInTheDocument()
    expect(screen.getByText("57:42")).toBeInTheDocument()
    expect(screen.getByText("1080p")).toBeInTheDocument()
    expect(screen.getByText("Completed")).toBeInTheDocument()
    expect(screen.getByText("00:00 / 57:42")).toBeInTheDocument()
  })
})
