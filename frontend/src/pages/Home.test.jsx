// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { localDateString } from "../dates"
import Home from "./Home"

// The page talks to this instead of the real HTTP client.
const backend = vi.hoisted(() => ({ server: null }))

vi.mock("../api", () => ({
  default: {
    get: (url, config) => backend.server.get(url, config),
    post: (url, data) => backend.server.post(url, data),
    patch: (url, data) => backend.server.patch(url, data),
    delete: (url) => backend.server.delete(url),
  },
}))

// Stand-in for the Django API: transactions newest first (by date, then id),
// the list paged by number, 50 rows unless page_size says otherwise.
// "Row 1" is the newest of the rows it starts with.
function startServer(rowCount) {
  let rows = []
  let lastId = 0
  let listFailures = 0
  let loseNextAnswer = false

  const add = (fields) => {
    const row = { id: ++lastId, trans_category: "Test", account_name: "", ...fields }
    rows.push(row)
    return row
  }
  for (let n = rowCount; n >= 1; n--) {
    const date = new Date(Date.UTC(2020, 11, 31 - (n - 1))).toISOString().slice(0, 10)
    add({ trans_description: `Row ${n}`, trans_date: date, trans_amount: "-1.00" })
  }

  const inOrder = () =>
    [...rows].sort((a, b) => b.trans_date.localeCompare(a.trans_date) || b.id - a.id)
  const idIn = (url) => Number(url.match(/\/(\d+)\/$/)[1])

  backend.server = {
    async get(url, { params = {} } = {}) {
      if (url.endsWith("/summary/")) {
        return { data: { income: "0", expenses: "0", net: "0", count: rows.length } }
      }
      if (listFailures > 0) {
        listFailures -= 1
        throw new Error("connection lost")
      }
      const pageSize = Math.min(params.page_size ?? 50, 500)
      const start = ((params.page ?? 1) - 1) * pageSize
      const end = start + pageSize
      return {
        data: {
          count: rows.length,
          results: inOrder().slice(start, end).map((row) => ({ ...row })),
          next: end < rows.length ? "next" : null,
        },
      }
    },
    async post(url, data) {
      return { data: { ...add({ ...data }) } }
    },
    async patch(url, changes) {
      const row = rows.find((candidate) => candidate.id === idIn(url))
      Object.assign(row, changes)
      return { data: { ...row } }
    },
    async delete(url) {
      rows = rows.filter((row) => row.id !== idIn(url))
      if (loseNextAnswer) {
        loseNextAnswer = false
        throw new Error("connection lost")
      }
      return { data: null }
    },
  }

  return {
    descriptions: () => inOrder().map((row) => row.trans_description),
    failNextListRequest: () => {
      listFailures += 1
    },
    // The next delete is carried out, but its answer never reaches the page.
    loseNextAnswer: () => {
      loseNextAnswer = true
    },
  }
}

const shown = () =>
  [...document.querySelectorAll(".transaction-description")].map((el) => el.textContent)

const rowOf = (description) => screen.getByText(description).closest(".transaction-container")

// The "Load more" button once it can be used, or null when every row is shown.
async function loadMoreButton() {
  await waitFor(() => {
    expect(screen.queryByRole("button", { name: "Loading..." })).toBeNull()
    expect(screen.queryByRole("button", { name: "Load more" })?.disabled ?? false).toBe(false)
  })
  return screen.queryByRole("button", { name: "Load more" })
}

async function loadEverything() {
  for (let button = await loadMoreButton(); button; button = await loadMoreButton()) {
    const before = shown().length
    fireEvent.click(button)
    await waitFor(() => expect(shown().length).toBeGreaterThan(before))
  }
}

function addTransaction({ description, amount, date }) {
  if (date) fireEvent.change(screen.getByLabelText("Date"), { target: { value: date } })
  fireEvent.change(screen.getByLabelText("Description"), { target: { value: description } })
  fireEvent.change(screen.getByLabelText("Amount"), { target: { value: amount } })
  fireEvent.click(screen.getByRole("button", { name: "Submit" }))
}

beforeEach(() => {
  vi.spyOn(window, "confirm").mockReturnValue(true)
  vi.spyOn(console, "error").mockImplementation(() => {})
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe("the transaction list", () => {
  it("shows the newest rows first and loads the rest on request", async () => {
    const server = startServer(120)
    render(<Home />)

    await screen.findByText("Showing 50 of 120")
    expect(shown()).toEqual(server.descriptions().slice(0, 50))

    await loadEverything()
    expect(shown()).toEqual(server.descriptions())
  })

  it("lists every row once after a transaction is added", async () => {
    // Regression: the new row pushed the last one shown onto the next page,
    // and "Load more" then listed that row twice.
    const server = startServer(120)
    render(<Home />)
    await screen.findByText("Showing 50 of 120")

    addTransaction({ description: "Added today", amount: "-3.50" })
    await screen.findByText("Showing 50 of 121")
    expect(shown()[0]).toBe("Added today")

    await loadEverything()
    expect(shown()).toEqual(server.descriptions())
  })

  it("keeps a transaction added with an old date in view, and lists it once", async () => {
    const server = startServer(120)
    render(<Home />)
    await screen.findByText("Showing 50 of 120")

    addTransaction({ description: "Added late", amount: "-9.99", date: "2001-01-01" })
    await screen.findByText("Showing 51 of 121")
    expect(shown()[0]).toBe("Added late")

    await loadEverything()
    expect(shown().filter((description) => description === "Added late")).toHaveLength(1)
    expect(shown().toSorted()).toEqual(server.descriptions().toSorted())
  })

  it("does not skip a row after a transaction is deleted", async () => {
    // Regression: the first row of the next page moved up into the page
    // already loaded and was never shown.
    const server = startServer(120)
    render(<Home />)
    await screen.findByText("Showing 50 of 120")

    fireEvent.click(within(rowOf("Row 10")).getByRole("button", { name: "Delete" }))
    await screen.findByText("Showing 50 of 119")
    expect(shown()).toContain("Row 51")

    await loadEverything()
    expect(shown()).toEqual(server.descriptions())
  })

  it("does not skip a row when the reload after a delete fails", async () => {
    // Regression: the list kept its old next-page number, so "Load more"
    // went on from the wrong place and one row never appeared.
    const server = startServer(120)
    render(<Home />)
    await screen.findByText("Showing 50 of 120")

    server.failNextListRequest()
    fireEvent.click(within(rowOf("Row 10")).getByRole("button", { name: "Delete" }))
    await screen.findByText("Could not reload transactions.")
    expect(shown()).not.toContain("Row 10")

    await loadEverything()
    expect(shown()).toEqual(server.descriptions())
    expect(screen.queryByText("Could not reload transactions.")).toBeNull()
  })

  it("shows what the server did when the answer to a delete is lost", async () => {
    // Regression: the page reported a failure and kept the row, the server
    // had deleted it, and "Load more" then skipped a row.
    const server = startServer(120)
    render(<Home />)
    await screen.findByText("Showing 50 of 120")

    server.loseNextAnswer()
    fireEvent.click(within(rowOf("Row 10")).getByRole("button", { name: "Delete" }))
    await screen.findByText("Could not delete the transaction.")
    await waitFor(() => expect(shown()).not.toContain("Row 10"))

    await loadEverything()
    expect(shown()).toEqual(server.descriptions())
  })

  it("moves a row to its new place when its date is edited", async () => {
    const server = startServer(120)
    render(<Home />)
    await screen.findByText("Showing 50 of 120")

    fireEvent.click(within(rowOf("Row 1")).getByRole("button", { name: "Edit" }))
    fireEvent.change(screen.getByLabelText("Date", { selector: ".edit-input" }), {
      target: { value: "2001-01-01" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Save" }))

    await waitFor(() => expect(shown()).not.toContain("Row 1"))
    await loadEverything()
    expect(shown()).toEqual(server.descriptions())
    expect(shown().at(-1)).toBe("Row 1")
  })
})

describe("deleting", () => {
  it("asks first and leaves the row alone when the answer is no", async () => {
    const server = startServer(5)
    window.confirm.mockReturnValue(false)
    render(<Home />)
    await screen.findByText("Showing 5 of 5")

    fireEvent.click(within(rowOf("Row 3")).getByRole("button", { name: "Delete" }))

    expect(window.confirm).toHaveBeenCalledWith('Delete "Row 3" (-$1.00)? This cannot be undone.')
    expect(server.descriptions()).toContain("Row 3")
    expect(shown()).toContain("Row 3")
  })
})

describe("the new transaction form", () => {
  it("starts on today's date where the user is", async () => {
    startServer(1)
    render(<Home />)
    await screen.findByText("Showing 1 of 1")

    expect(screen.getByLabelText("Date").value).toBe(localDateString())
  })
})
