import { render, screen } from "@testing-library/react"
import { MemoryRouter } from "react-router"
import { DashboardPage } from "../pages/DashboardPage"

describe("DashboardPage", () => {
  it("loads provider data and renders all seven panels", async () => {
    render(
      <MemoryRouter>
        <DashboardPage />
      </MemoryRouter>
    )
    // Episode title appears in both PlayerCard and RecentProjectsCard, so use findAllByText
    expect((await screen.findAllByText("AI Podcast Episode 12")).length).toBeGreaterThan(0)
    expect(screen.getByText("Upload a video or audio file")).toBeInTheDocument()
    expect(screen.getByText(/AI Summary/)).toBeInTheDocument()
    expect(screen.getByText("Transcript")).toBeInTheDocument()
    expect(screen.getByText(/Chapters/)).toBeInTheDocument()
    expect(screen.getByText("Recent Projects")).toBeInTheDocument()
    expect(screen.getByText("Export")).toBeInTheDocument()
  })
})
