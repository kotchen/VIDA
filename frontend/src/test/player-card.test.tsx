import { render, screen } from "@testing-library/react"
import { PlayerCard } from "../components/dashboard/PlayerCard"
import { networkEmbed } from "@/lib/embed"
import { mockDashboard } from "../data/mock"
import type { Episode } from "@/api/types"

function urlEpisode(sourceUrl: string): Episode {
  return {
    ...mockDashboard.currentEpisode,
    id: "ep-url",
    title: "URL Episode",
    sourceType: "url",
    sourceUrl,
    mediaUrl: "/api/v2/episodes/ep-url/media",
    posterUrl: null,
  }
}

describe("PlayerCard", () => {
  it("renders episode title and meta", () => {
    render(<PlayerCard episode={mockDashboard.currentEpisode} />)
    expect(screen.getByText("AI Podcast Episode 12")).toBeInTheDocument()
    expect(screen.getByText("May 16, 2024")).toBeInTheDocument()
    expect(screen.getByText("57:42")).toBeInTheDocument()
    expect(screen.getByText("1080p")).toBeInTheDocument()
    expect(screen.getByText("Completed")).toBeInTheDocument()
    expect(screen.getByTitle("AI Podcast Episode 12")).toHaveAttribute(
      "src",
      "/api/v2/episodes/ep-12/media",
    )
    expect(screen.getByTitle("AI Podcast Episode 12")).toHaveAttribute("controls")
  })

  it("embeds the bilibili network player for bilibili source pages", () => {
    render(
      <PlayerCard
        episode={urlEpisode(
          "https://www.bilibili.com/video/BV1fp4y1X7JJ/?spm_id_from=333.337.search-card.all.click",
        )}
      />,
    )
    const frame = screen.getByTitle("URL Episode")
    expect(frame.tagName).toBe("IFRAME")
    expect(frame).toHaveAttribute(
      "src",
      "https://player.bilibili.com/player.html?bvid=BV1fp4y1X7JJ&autoplay=0",
    )
  })

  it("embeds the youtube network player with the iframe API enabled", () => {
    const { unmount } = render(
      <PlayerCard episode={urlEpisode("https://www.youtube.com/watch?v=dQw4w9WgXcQ")} />,
    )
    expect(screen.getByTitle("URL Episode")).toHaveAttribute(
      "src",
      "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ?enablejsapi=1",
    )
    unmount()
    render(<PlayerCard episode={urlEpisode("https://youtu.be/dQw4w9WgXcQ")} />)
    expect(screen.getByTitle("URL Episode")).toHaveAttribute(
      "src",
      "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ?enablejsapi=1",
    )
  })

  it("falls back to the committed media for non-embeddable url sources", () => {
    render(<PlayerCard episode={urlEpisode("https://media.example.com/clip.mp4")} />)
    const video = screen.getByTitle("URL Episode")
    expect(video.tagName).toBe("VIDEO")
    expect(video).toHaveAttribute("src", "/api/v2/episodes/ep-url/media")
  })

  it("seeks the local video element when a seek request arrives", () => {
    const { rerender } = render(
      <PlayerCard episode={mockDashboard.currentEpisode} />,
    )
    const video = screen.getByTitle("AI Podcast Episode 12") as HTMLVideoElement
    rerender(
      <PlayerCard
        episode={mockDashboard.currentEpisode}
        seek={{ sec: 125, nonce: 1 }}
      />,
    )
    expect(video.currentTime).toBe(125)
  })

  it("posts a seekTo command to the youtube iframe without reloading it", () => {
    const { rerender } = render(
      <PlayerCard episode={urlEpisode("https://www.youtube.com/watch?v=dQw4w9WgXcQ")} />,
    )
    const frame = screen.getByTitle("URL Episode") as HTMLIFrameElement
    const postMessage = vi.spyOn(frame.contentWindow as Window, "postMessage")
    rerender(
      <PlayerCard
        episode={urlEpisode("https://www.youtube.com/watch?v=dQw4w9WgXcQ")}
        seek={{ sec: 125, nonce: 1 }}
      />,
    )
    expect(frame).toHaveAttribute(
      "src",
      "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ?enablejsapi=1",
    )
    expect(postMessage).toHaveBeenCalledWith(
      JSON.stringify({ event: "command", func: "seekTo", args: [125, true] }),
      "*",
    )
    expect(postMessage).toHaveBeenCalledWith(
      JSON.stringify({ event: "command", func: "playVideo", args: [] }),
      "*",
    )
  })

  it("reloads the bilibili iframe with a start time when seeking", () => {
    const episode = urlEpisode("https://www.bilibili.com/video/BV1fp4y1X7JJ/")
    const { rerender } = render(<PlayerCard episode={episode} />)
    rerender(<PlayerCard episode={episode} seek={{ sec: 125, nonce: 1 }} />)
    expect(screen.getByTitle("URL Episode")).toHaveAttribute(
      "src",
      "https://player.bilibili.com/player.html?bvid=BV1fp4y1X7JJ&t=125&autoplay=1",
    )
  })
})

describe("networkEmbed", () => {
  it("rejects malformed urls and unsupported hosts", () => {
    expect(networkEmbed("not a url")).toBeNull()
    expect(networkEmbed("https://www.tiktok.com/@a/video/1")).toBeNull()
    expect(networkEmbed("https://b23.tv/abc123")).toBeNull()
    expect(networkEmbed("https://www.bilibili.com/bangumi/play/ep1")).toBeNull()
  })
})
