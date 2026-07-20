import { formatDate, formatSeconds, formatTimestamp } from "../lib/format"

describe("formatSeconds", () => {
  it("formats mm:ss", () => {
    expect(formatSeconds(3462)).toBe("57:42")
    expect(formatSeconds(105)).toBe("1:45")
    expect(formatSeconds(9)).toBe("0:09")
  })
  it("formats h:mm:ss when >= 1 hour", () => {
    expect(formatSeconds(3723)).toBe("1:02:03")
  })
})

describe("formatTimestamp", () => {
  it("formats hh:mm:ss padded", () => {
    expect(formatTimestamp(0)).toBe("00:00:00")
    expect(formatTimestamp(9)).toBe("00:00:09")
    expect(formatTimestamp(62)).toBe("00:01:02")
  })
})

describe("formatDate", () => {
  it("formats en-US short date in UTC", () => {
    expect(formatDate("2024-05-16T09:00:00Z")).toBe("May 16, 2024")
  })
})
