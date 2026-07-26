import { fireEvent, render, screen } from "@testing-library/react"
import { InsightsCard } from "../components/dashboard/InsightsCard"
import { mockDashboard } from "../data/mock"

describe("InsightsCard", () => {
  it("shows the summary tab by default and switches to chapters", () => {
    render(
      <InsightsCard
        summary={mockDashboard.summary}
        chapters={mockDashboard.chapters}
      />,
    )
    expect(screen.getByRole("tab", { name: "Summary" })).toHaveAttribute(
      "aria-selected",
      "true",
    )
    expect(
      screen.getByText(/In this episode, the hosts explore/),
    ).toBeInTheDocument()
    expect(screen.queryByText("AI in Healthcare")).not.toBeInTheDocument()
    expect(screen.queryByText("Add Chapter")).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole("tab", { name: "Chapters" }))

    expect(screen.getByRole("tab", { name: "Chapters" })).toHaveAttribute(
      "aria-selected",
      "true",
    )
    expect(screen.getByText("AI in Healthcare")).toBeInTheDocument()
    expect(
      screen.queryByText(/In this episode, the hosts explore/),
    ).not.toBeInTheDocument()
  })

  it("shows the Add Chapter action only on the chapters tab", () => {
    const onCreate = vi.fn()
    render(
      <InsightsCard
        summary={mockDashboard.summary}
        chapters={mockDashboard.chapters}
        onCreateChapter={onCreate}
      />,
    )
    expect(screen.queryByText("Add Chapter")).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole("tab", { name: "Chapters" }))
    fireEvent.click(screen.getByText("Add Chapter"))
    expect(onCreate).toHaveBeenCalledTimes(1)
  })

  it("renders the summary loading and empty states inside the tab", () => {
    const { unmount } = render(
      <InsightsCard summary={null} summaryLoading chapters={[]} />,
    )
    expect(screen.getByText("Loading summary…")).toBeInTheDocument()
    unmount()
    render(<InsightsCard summary={null} chapters={[]} />)
    expect(screen.getByText("Summary is not available yet.")).toBeInTheDocument()
  })

  it("calls onSeekChapter with the chapter start when a chapter is clicked", () => {
    const onSeekChapter = vi.fn()
    render(
      <InsightsCard
        summary={mockDashboard.summary}
        chapters={mockDashboard.chapters}
        onSeekChapter={onSeekChapter}
      />,
    )
    fireEvent.click(screen.getByRole("tab", { name: "Chapters" }))
    fireEvent.click(screen.getByText("AI in Healthcare"))
    expect(onSeekChapter).toHaveBeenCalledTimes(1)
    expect(onSeekChapter).toHaveBeenCalledWith(105)
  })
})
