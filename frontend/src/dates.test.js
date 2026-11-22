import { describe, expect, it } from "vitest"
import { localDateString } from "./dates"

describe("localDateString", () => {
  // 11:30pm on 21 November in Los Angeles is 7:30am on the 22nd in UTC.
  const lateEvening = new Date(2026, 10, 21, 23, 30)

  it("is tested in a time zone behind UTC (set in vite.config.js)", () => {
    expect(lateEvening.toISOString().slice(0, 10)).toBe("2026-11-22")
  })

  it("gives the local calendar date when UTC is already on the next day", () => {
    expect(localDateString(lateEvening)).toBe("2026-11-21")
  })

  it("pads single-digit months and days", () => {
    expect(localDateString(new Date(2026, 0, 5, 9, 0))).toBe("2026-01-05")
  })

  it("defaults to the current date", () => {
    expect(localDateString()).toMatch(/^\d{4}-\d{2}-\d{2}$/)
  })
})
