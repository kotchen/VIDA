import { render, screen } from "@testing-library/react"
import { MemoryRouter } from "react-router"
import { Sidebar } from "../components/layout/Sidebar"

describe("Sidebar", () => {
  it("renders brand and all five nav items", () => {
    render(
      <MemoryRouter initialEntries={["/dashboard"]}>
        <Sidebar />
      </MemoryRouter>
    )
    expect(screen.getByText("VIDA")).toBeInTheDocument()
    expect(screen.getByText("Video Intelligence, Dialogue, Analysis")).toBeInTheDocument()
    for (const label of ["Dashboard", "Transcribe", "Library", "Summaries", "Settings"]) {
      expect(screen.getByText(label)).toBeInTheDocument()
    }
    expect(screen.getByText(/Fueling insights/)).toBeInTheDocument()
  })

  it("marks the active route", () => {
    render(
      <MemoryRouter initialEntries={["/library"]}>
        <Sidebar />
      </MemoryRouter>
    )
    const library = screen.getByText("Library").closest("a")
    expect(library?.className).toContain("bg-copper-gradient")
    const dashboard = screen.getByText("Dashboard").closest("a")
    expect(dashboard?.className).not.toContain("bg-copper-gradient")
  })
})
