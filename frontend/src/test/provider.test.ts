import { dataProvider } from "../data/provider"

describe("MockProvider", () => {
  it("returns dashboard data matching the design mock", async () => {
    const data = await dataProvider.getDashboard()
    expect(data.currentEpisode?.title).toBe("AI Podcast Episode 12")
    expect(data.currentEpisode?.status).toBe("completed")
    expect(data.summary?.confidence).toBe(92)
    expect(data.transcript).toHaveLength(6)
    expect(data.chapters).toHaveLength(6)
    expect(data.recentProjects).toHaveLength(4)
    expect(data.recentProjects[3].status).toBe("processing")
  })

  it("every transcript segment has speaker and ordered timestamps", async () => {
    const { transcript } = await dataProvider.getDashboard()
    for (const seg of transcript) {
      expect(seg.speaker.length).toBeGreaterThan(0)
      expect(seg.endSec).toBeGreaterThan(seg.startSec)
      expect(seg.text.length).toBeGreaterThan(0)
    }
  })
})
