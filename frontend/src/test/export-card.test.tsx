import { render, screen } from "@testing-library/react"
import { ExportCard } from "../components/dashboard/ExportCard"

describe("ExportCard", () => {
  it("renders three export formats", () => {
    render(<ExportCard />)
    expect(screen.getByText("Export")).toBeInTheDocument()
    expect(screen.getByText("Export your transcript or summary")).toBeInTheDocument()
    for (const name of ["TXT", "SRT", "MD"]) {
      expect(screen.getByText(name)).toBeInTheDocument()
    }
    expect(screen.getByText(/More formats coming soon/)).toBeInTheDocument()
  })
})
