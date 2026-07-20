import { render, screen } from "@testing-library/react"
import { ChaptersCard } from "../components/dashboard/ChaptersCard"
import { mockDashboard } from "../data/mock"

describe("ChaptersCard", () => {
  it("renders all chapters with timestamps and durations", () => {
    render(<ChaptersCard chapters={mockDashboard.chapters} />)
    expect(screen.getByText(/Chapters/)).toBeInTheDocument()
    expect(screen.getByText("Add Chapter")).toBeInTheDocument()
    for (const title of [
      "Introduction & Welcome",
      "AI in Healthcare",
      "Creative AI Revolution",
      "Ethical Considerations",
      "The Future of AI",
      "Q&A and Closing Thoughts",
    ]) {
      expect(screen.getByText(title)).toBeInTheDocument()
    }
    expect(screen.getByText("11:57")).toBeInTheDocument()
    expect(screen.getByText("23:22")).toBeInTheDocument()
  })
})
