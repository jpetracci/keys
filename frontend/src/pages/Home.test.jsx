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
    post: (url, data, config) => backend.server.post(url, data, config),
    patch: (url, data) => backend.server.patch(url, data),
    delete: (url) => backend.server.delete(url),
  },
}))

// Stand-in for the Django API: transactions newest first (by date, then id),
// the list paged by number, 50 rows unless page_size says otherwise.
// "Row 1" is the newest of the rows it starts with. `fieldsOf(n)` gives
// "Row n" fields other than the defaults. The list and the summary are
// narrowed by the category, account, start and end parameters.
function startServer(rowCount, fieldsOf = () => ({})) {
  let rows = []
  let lastId = 0
  let listFailures = 0
  let loseNextAnswer = false
  let held = null

  const add = (fields) => {
    const row = { id: ++lastId, trans_category: "Test", account_name: "", ...fields }
    rows.push(row)
    return row
  }
  for (let n = rowCount; n >= 1; n--) {
    const date = new Date(Date.UTC(2020, 11, 31 - (n - 1))).toISOString().slice(0, 10)
    add({ trans_description: `Row ${n}`, trans_date: date, trans_amount: "-1.00", ...fieldsOf(n) })
  }

  const sameText = (a, b) => a.toLowerCase() === b.toLowerCase()
  const inOrder = ({ category, account, start, end } = {}) =>
    rows
      .filter(
        (row) =>
          (!category || sameText(row.trans_category, category)) &&
          (!account || sameText(row.account_name, account)) &&
          (!start || row.trans_date >= start) &&
          (!end || row.trans_date <= end)
      )
      .sort((a, b) => b.trans_date.localeCompare(a.trans_date) || b.id - a.id)
  // Like Django, which has no year 0 and none past 9999.
  const checkDates = (params) => {
    const refused = ["start", "end"].filter(
      (name) => params[name] && !/^(?!0000)\d{4}-\d{2}-\d{2}$/.test(params[name])
    )
    if (!refused.length) return
    const error = new Error("Request failed with status code 400")
    error.response = {
      status: 400,
      data: Object.fromEntries(refused.map((name) => [name, ["Date has wrong format."]])),
    }
    throw error
  }
  const sum = (list) =>
    (list.reduce((cents, row) => cents + Math.round(Number(row.trans_amount) * 100), 0) / 100).toFixed(2)
  const distinct = (field) => [...new Set(rows.map((row) => row[field]).filter(Boolean))].sort()
  const idIn = (url) => Number(url.match(/\/(\d+)\/$/)[1])

  backend.server = {
    async get(url, { params = {} } = {}) {
      if (url.endsWith("/filter-options/")) {
        return { data: { categories: distinct("trans_category"), accounts: distinct("account_name") } }
      }
      if (held && params.category === held.category) await held.gate
      checkDates(params)
      const matching = inOrder(params)
      if (url.endsWith("/summary/")) {
        return {
          data: {
            income: sum(matching.filter((row) => Number(row.trans_amount) >= 0)),
            expenses: sum(matching.filter((row) => Number(row.trans_amount) < 0)),
            net: sum(matching),
            count: matching.length,
          },
        }
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
          count: matching.length,
          results: matching.slice(start, end).map((row) => ({ ...row })),
          next: end < matching.length ? "next" : null,
        },
      }
    },
    async post(url, data, config) {
      if (url.endsWith("/import/")) {
        // Every uploaded statement holds one new transaction, named after it.
        const created = config.params.dry_run ? 0 : 1
        if (created) {
          add({ trans_description: `From ${config.params.filename}`, trans_date: "2021-01-01", trans_amount: "-2.00" })
        }
        return { data: { dry_run: !created, created, skipped: 0, errors: 0, row_errors: [], warnings: [], note: null } }
      }
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
    descriptions: (filters) => inOrder(filters).map((row) => row.trans_description),
    // Requests filtered by `category` get no answer until the returned
    // function is called.
    holdAnswersFor: (category) => {
      let release
      held = { category, gate: new Promise((resolve) => (release = resolve)) }
      return release
    },
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

// One of the three totals above the list, as shown.
const total = (label) => screen.getByText(label).parentElement.querySelector("strong").textContent

// Choose a category or account to filter by, once the page offers it.
async function chooseFilter(label, value) {
  const select = screen.getByLabelText(label)
  await within(select).findByRole("option", { name: value })
  fireEvent.change(select, { target: { value } })
}

const setFilterDate = (label, value) =>
  fireEvent.change(screen.getByLabelText(label), { target: { value } })

function addTransaction({ description, amount, date, category }) {
  if (date) fireEvent.change(screen.getByLabelText("Date"), { target: { value: date } })
  if (category) fireEvent.change(screen.getByLabelText("Category"), { target: { value: category } })
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

describe("filtering", () => {
  // Rows 1..120, newest first, one a day back from 2020-12-31. Every third
  // is food on the card at 2.00; the rest are rent from checking at 10.00.
  const mixed = (n) =>
    n % 3 === 0
      ? { trans_category: "Food", account_name: "Card", trans_amount: "-2.00" }
      : { trans_category: "Rent", account_name: "Checking", trans_amount: "-10.00" }
  const food = { category: "Food" }

  it("narrows the list and the totals, and Load more stays within the filter", async () => {
    const server = startServer(120, mixed)
    render(<Home />)
    await screen.findByText("Showing 50 of 120")
    expect(total("Expenses / Debits")).toBe("-$880.00")

    await chooseFilter("Filter by category", "Food")

    await screen.findByText("Showing 40 of 40")
    expect(shown()).toEqual(server.descriptions(food))
    expect(total("Expenses / Debits")).toBe("-$80.00")
    expect(total("Net")).toBe("-$80.00")
    expect(await loadMoreButton()).toBeNull()

    fireEvent.click(screen.getByRole("button", { name: "Clear filters" }))
    await chooseFilter("Filter by account", "Checking")

    await screen.findByText("Showing 50 of 80")
    expect(total("Expenses / Debits")).toBe("-$800.00")
    await loadEverything()
    expect(shown()).toEqual(server.descriptions({ account: "Checking" }))
    expect(screen.getByText("Showing 80 of 80")).toBeTruthy()
  })

  it("combines a date range with the other filters, both ends included", async () => {
    const server = startServer(120, mixed)
    render(<Home />)
    await screen.findByText("Showing 50 of 120")

    setFilterDate("From date", "2020-12-22")
    setFilterDate("To date", "2020-12-31")
    await screen.findByText("Showing 10 of 10")
    expect(shown()).toEqual(server.descriptions().slice(0, 10))

    await chooseFilter("Filter by category", "Food")
    await screen.findByText("Showing 3 of 3")
    expect(shown()).toEqual(["Row 3", "Row 6", "Row 9"])
    expect(total("Expenses / Debits")).toBe("-$6.00")
  })

  it("restores everything when the filters are cleared", async () => {
    const server = startServer(120, mixed)
    render(<Home />)
    await screen.findByText("Showing 50 of 120")
    expect(screen.getByRole("button", { name: "Clear filters" }).disabled).toBe(true)

    await chooseFilter("Filter by category", "Food")
    setFilterDate("From date", "2020-12-01")
    await screen.findByText("Showing 10 of 10")

    fireEvent.click(screen.getByRole("button", { name: "Clear filters" }))

    await screen.findByText("Showing 50 of 120")
    expect(screen.getByLabelText("Filter by category").value).toBe("")
    expect(screen.getByLabelText("From date").value).toBe("")
    expect(total("Expenses / Debits")).toBe("-$880.00")
    await loadEverything()
    expect(shown()).toEqual(server.descriptions())
  })

  it("says so when nothing matches", async () => {
    startServer(5, mixed)
    render(<Home />)
    await screen.findByText("Showing 5 of 5")

    setFilterDate("From date", "2021-06-01")
    await screen.findByText("No transactions match these filters.")
    expect(total("Net")).toBe("$0.00")

    setFilterDate("To date", "2021-01-01")
    await screen.findByText("No transactions match: the From date is after the To date.")
  })

  it("shows the newer filter when the answer for an older one arrives late", async () => {
    const server = startServer(120, mixed)
    render(<Home />)
    await screen.findByText("Showing 50 of 120")

    const releaseFood = server.holdAnswersFor("Food")
    await chooseFilter("Filter by category", "Food")
    await chooseFilter("Filter by category", "Rent")
    await screen.findByText("Showing 50 of 80")

    releaseFood()
    await new Promise((resolve) => setTimeout(resolve, 20))

    expect(screen.getByText("Showing 50 of 80")).toBeTruthy()
    expect(shown()).toEqual(server.descriptions({ category: "Rent" }).slice(0, 50))
    expect(total("Expenses / Debits")).toBe("-$800.00")
    await loadEverything()
    expect(shown()).toEqual(server.descriptions({ category: "Rent" }))
  })

  it("explains a date the server refuses instead of showing an empty list", async () => {
    const server = startServer(120, mixed)
    render(<Home />)
    await screen.findByText("Showing 50 of 120")

    setFilterDate("From date", "20260-01-01")

    const alert = await screen.findByRole("alert")
    expect(alert.textContent).toBe(
      "The From date filter is not one the server accepts. Change or clear it to see transactions."
    )
    expect(shown()).toEqual([])
    expect(screen.queryByText(/No transactions/)).toBeNull()
    expect(total("Net")).toBe("$0.00")

    setFilterDate("From date", "2020-12-30")
    await screen.findByText("Showing 2 of 2")
    expect(screen.queryByRole("alert")).toBeNull()
    expect(shown()).toEqual(server.descriptions().slice(0, 2))
  })

  it("offers the categories and accounts in use, including ones just added", async () => {
    startServer(6, mixed)
    render(<Home />)
    await screen.findByText("Showing 6 of 6")

    const optionsOf = (label) =>
      within(screen.getByLabelText(label))
        .getAllByRole("option")
        .map((option) => option.textContent)
    await waitFor(() =>
      expect(optionsOf("Filter by category")).toEqual(["All categories", "Food", "Rent"])
    )
    expect(optionsOf("Filter by account")).toEqual(["All accounts", "Card", "Checking"])

    addTransaction({ description: "Bus", amount: "-3.00", category: "Transit" })
    await waitFor(() => expect(optionsOf("Filter by category")).toContain("Transit"))
  })

  it("lists a matching transaction added under a filter, and counts it", async () => {
    const server = startServer(180, mixed)
    render(<Home />)
    await screen.findByText("Showing 50 of 180")
    await chooseFilter("Filter by category", "Food")
    await screen.findByText("Showing 50 of 60")

    addTransaction({ description: "Groceries", amount: "-5.00", category: "food" })

    await screen.findByText("Showing 50 of 61")
    expect(shown()[0]).toBe("Groceries")
    expect(total("Expenses / Debits")).toBe("-$125.00")
    await loadEverything()
    expect(shown()).toEqual(server.descriptions(food))
  })

  it("does not list a transaction the filter excludes, but says it was saved", async () => {
    const server = startServer(180, mixed)
    render(<Home />)
    await screen.findByText("Showing 50 of 180")
    await chooseFilter("Filter by category", "Food")
    await screen.findByText("Showing 50 of 60")

    addTransaction({ description: "January rent", amount: "-10.00", category: "Rent" })

    await screen.findByText(
      'Saved "January rent". It is not listed because it does not match the filters.'
    )
    await loadEverything()
    expect(screen.getByText("Showing 60 of 60")).toBeTruthy()
    expect(shown()).toEqual(server.descriptions(food))
    expect(total("Expenses / Debits")).toBe("-$120.00")

    fireEvent.click(screen.getByRole("button", { name: "Clear filters" }))
    await screen.findByText("Showing 50 of 181")
    expect(shown()[0]).toBe("January rent")
    expect(screen.queryByText(/It is not listed/)).toBeNull()
  })

  it("drops a row edited out of the filter without skipping the next one", async () => {
    const server = startServer(180, mixed)
    render(<Home />)
    await screen.findByText("Showing 50 of 180")
    await chooseFilter("Filter by category", "Food")
    await screen.findByText("Showing 50 of 60")

    fireEvent.click(within(rowOf("Row 30")).getByRole("button", { name: "Edit" }))
    fireEvent.change(screen.getByLabelText("Category", { selector: ".edit-input" }), {
      target: { value: "Rent" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Save" }))

    await screen.findByText("Showing 50 of 59")
    expect(shown()).not.toContain("Row 30")
    expect(shown()).toContain("Row 153")
    expect(total("Expenses / Debits")).toBe("-$118.00")
    await loadEverything()
    expect(shown()).toEqual(server.descriptions(food))
  })

  it("does not skip a row after a delete under a filter", async () => {
    const server = startServer(180, mixed)
    render(<Home />)
    await screen.findByText("Showing 50 of 180")
    await chooseFilter("Filter by category", "Food")
    await screen.findByText("Showing 50 of 60")

    fireEvent.click(within(rowOf("Row 30")).getByRole("button", { name: "Delete" }))

    await screen.findByText("Showing 50 of 59")
    expect(shown()).toContain("Row 153")
    expect(total("Expenses / Debits")).toBe("-$118.00")
    await loadEverything()
    expect(shown()).toEqual(server.descriptions(food))
    expect(shown()).toHaveLength(59)
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

describe("importing a statement", () => {
  it("reloads the list and the totals after an import, but not after a preview", async () => {
    const server = startServer(120)
    render(<Home />)
    await screen.findByText("Showing 50 of 120")

    fireEvent.change(screen.getByLabelText("CSV file"), {
      target: { files: [new File(["Date,Description,Amount\n"], "march.csv", { type: "text/csv" })] },
    })
    fireEvent.click(screen.getByRole("button", { name: "Preview" }))
    await screen.findByText(/Preview, nothing saved/)
    expect(screen.getByText("Showing 50 of 120")).toBeTruthy()

    fireEvent.click(screen.getByRole("button", { name: "Import" }))
    await screen.findByText("Showing 50 of 121")
    expect(shown()[0]).toBe("From march.csv")

    await loadEverything()
    expect(shown()).toEqual(server.descriptions())
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
