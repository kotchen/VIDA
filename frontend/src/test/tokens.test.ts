// @vitest-environment node
import { readFileSync } from "node:fs"

const css = readFileSync(new URL("../theme/tokens.css", import.meta.url), "utf-8")

describe("design tokens", () => {
  it.each([
    "--color-page: #120B05",
    "--color-sidebar: #110802",
    "--color-card: #1F140A",
    "--color-raised: #2A1609",
    "--color-warm: #46301E",
    "--color-copper-300: #D09050",
    "--color-copper-500: #B07030",
    "--color-copper-700: #905020",
    "--color-gold: #C28D51",
    "--color-cream: #F3E9DA",
    "--color-muted-warm: #A68B70",
    "--color-success: #7BA05B",
    "--color-on-copper: #1A0E04",
  ])("defines %s", (token) => {
    expect(css).toContain(token)
  })

  it("defines composite utilities", () => {
    for (const cls of [".bg-copper-gradient", ".text-gold-gradient", ".card-glow", ".tnum"]) {
      expect(css).toContain(cls)
    }
  })
})
