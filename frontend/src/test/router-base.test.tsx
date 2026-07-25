import { render, screen } from "@testing-library/react"
import { Route, Routes } from "react-router"

import { V2BrowserRouter } from "@/router"

describe("V2BrowserRouter", () => {
  it("resolves client routes below /v2", () => {
    window.history.pushState({}, "", "/v2/episodes/episode-1")

    render(
      <V2BrowserRouter>
        <Routes>
          <Route path="/episodes/:id" element={<p>Episode detail</p>} />
        </Routes>
      </V2BrowserRouter>,
    )

    expect(screen.getByText("Episode detail")).toBeInTheDocument()
  })
})
