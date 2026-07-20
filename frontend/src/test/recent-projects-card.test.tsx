import { render, screen } from "@testing-library/react"
import { MemoryRouter } from "react-router"
import { RecentProjectsCard } from "../components/dashboard/RecentProjectsCard"
import { mockDashboard } from "../data/mock"

describe("RecentProjectsCard", () => {
  it("renders four project cards with status badges", () => {
    render(
      <MemoryRouter>
        <RecentProjectsCard projects={mockDashboard.recentProjects} />
      </MemoryRouter>
    )
    expect(screen.getByText("Recent Projects")).toBeInTheDocument()
    expect(screen.getByText("View all")).toBeInTheDocument()
    expect(screen.getByText("Product Launch Talk")).toBeInTheDocument()
    expect(screen.getByText("Customer Interview #7")).toBeInTheDocument()
    expect(screen.getByText("Design Sprint Debrief")).toBeInTheDocument()
    expect(screen.getAllByText("Completed")).toHaveLength(3)
    expect(screen.getByText("Processing")).toBeInTheDocument()
    expect(screen.getByText("34:18")).toBeInTheDocument()
  })
})
