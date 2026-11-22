import { describe, expect, it } from "vitest"
import {
  appendNewRows,
  fetchFirstRows,
  fetchPage,
  filterErrorMessage,
  filterParams,
  hasFilters,
  matchesFilters,
  MAX_PAGE_SIZE,
  NO_FILTERS,
  PAGE_SIZE,
} from "./transactionList"

// Stand-in for GET /api/transactions/: rows newest first, paged by number
// the way the backend's PageNumberPagination does it, narrowed by the
// category, account, start and end parameters. `fieldsOf(id)` gives a row
// the fields to filter on.
function fakeServer(rowCount, fieldsOf = () => ({})) {
  const newRow = (id) => ({ id, ...fieldsOf(id) })
  let rows = Array.from({ length: rowCount }, (_, i) => newRow(rowCount - i))
  let lastId = rowCount
  const requests = []

  const selected = ({ category, account, start, end } = {}) =>
    rows.filter(
      (row) =>
        (!category || row.trans_category?.toLowerCase() === category.toLowerCase()) &&
        (!account || row.account_name?.toLowerCase() === account.toLowerCase()) &&
        (!start || row.trans_date >= start) &&
        (!end || row.trans_date <= end)
    )

  const api = {
    async get(url, { params = {} } = {}) {
      requests.push(params)
      const matching = selected(params)
      const pageSize = Math.min(params.page_size ?? PAGE_SIZE, MAX_PAGE_SIZE)
      const start = ((params.page ?? 1) - 1) * pageSize
      const end = start + pageSize
      return {
        data: {
          count: matching.length,
          results: matching.slice(start, end),
          next: end < matching.length ? "http://backend/api/transactions/?page=n" : null,
        },
      }
    },
  }

  return {
    api,
    requests,
    ids: (filters) => selected(filters).map((row) => row.id),
    // A row dated today sorts first; one dated years ago sorts last.
    addNewest() {
      const row = newRow(++lastId)
      rows = [row, ...rows]
      return row
    },
    addOldest() {
      const row = newRow(++lastId)
      rows = [...rows, row]
      return row
    },
    remove(id) {
      rows = rows.filter((row) => row.id !== id)
    },
  }
}

const idsOf = (list) => list.rows.map((row) => row.id)

// What the page does when the user clicks "Load more".
async function loadMore(api, list, filters) {
  const page = await fetchPage(api, list.nextPage, filters)
  return { rows: appendNewRows(list.rows, page.rows), nextPage: page.nextPage }
}

// What the page does after the user adds, edits or deletes a row.
const reload = (api, list, filters) => fetchFirstRows(api, list.rows.length, filters)

describe("loading the list", () => {
  it("starts with one page and says where to continue", async () => {
    const server = fakeServer(120)

    const list = await fetchFirstRows(server.api)

    expect(idsOf(list)).toEqual(server.ids().slice(0, PAGE_SIZE))
    expect(list.nextPage).toBe(2)
  })

  it("reaches every row exactly once and then reports the end", async () => {
    const server = fakeServer(120)

    let list = await fetchFirstRows(server.api)
    while (list.nextPage) {
      list = await loadMore(server.api, list)
    }

    expect(idsOf(list)).toEqual(server.ids())
  })

  it("has no next page when everything fits on the first", async () => {
    const list = await fetchFirstRows(fakeServer(30).api)

    expect(list.nextPage).toBeNull()
  })
})

describe("after the user changes something", () => {
  it("shows no row twice once a row has been added", async () => {
    // Regression: the new row pushed the last row shown onto page 2, and
    // "Load more" then listed that row a second time.
    const server = fakeServer(120)
    let list = await fetchFirstRows(server.api)

    server.addNewest()
    list = await reload(server.api, list)
    list = await loadMore(server.api, list)

    expect(idsOf(list)).toEqual(server.ids().slice(0, 2 * PAGE_SIZE))
  })

  it("does not skip the row that moves up a page once a row has been deleted", async () => {
    // Regression: deleting a shown row pulled page 2's first row onto page 1,
    // which was already loaded, so that row never appeared.
    const server = fakeServer(120)
    let list = await fetchFirstRows(server.api)
    const movesUp = server.ids()[PAGE_SIZE]

    server.remove(list.rows[10].id)
    list = await reload(server.api, list)
    list = await loadMore(server.api, list)

    expect(idsOf(list)).toContain(movesUp)
    expect(idsOf(list)).toEqual(server.ids().slice(0, 2 * PAGE_SIZE))
  })

  it("reloads as many whole pages as were on screen, up to the server's limit", async () => {
    const server = fakeServer(2000)
    const cases = [
      { shown: 0, pageSize: 50, nextPage: 2 },
      { shown: 50, pageSize: 50, nextPage: 2 },
      { shown: 51, pageSize: 100, nextPage: 3 },
      { shown: 150, pageSize: 150, nextPage: 4 },
      { shown: 1700, pageSize: MAX_PAGE_SIZE, nextPage: 11 },
    ]

    for (const { shown, pageSize, nextPage } of cases) {
      const list = await fetchFirstRows(server.api, shown)

      expect(server.requests.at(-1)).toEqual({ page_size: pageSize })
      expect(list.rows).toHaveLength(pageSize)
      expect(list.nextPage).toBe(nextPage)
    }
  })

  it("continues with 50-row pages after a larger reload", async () => {
    const server = fakeServer(400)

    let list = await fetchFirstRows(server.api, 150)
    list = await loadMore(server.api, list)

    expect(idsOf(list)).toEqual(server.ids().slice(0, 200))
    expect(list.nextPage).toBe(5)
  })
})

describe("when the data changes behind the page's back", () => {
  it("adds each row once even if a page repeats rows already shown", async () => {
    // Something else (an import, another tab) added a row, so page 2 now
    // starts with the row that was last on page 1.
    const server = fakeServer(120)
    let list = await fetchFirstRows(server.api)

    server.addNewest()
    list = await loadMore(server.api, list)

    expect(new Set(idsOf(list)).size).toBe(list.rows.length)
    expect(list.nextPage).toBe(3)
  })

  it("keeps a row already on screen when its own page arrives later", async () => {
    // A row created with an old date is pinned on top although it sorts last.
    const server = fakeServer(60)
    const first = await fetchFirstRows(server.api)
    const pinned = server.addOldest()

    const list = await loadMore(server.api, {
      rows: [pinned, ...first.rows],
      nextPage: first.nextPage,
    })

    expect(idsOf(list).filter((id) => id === pinned.id)).toHaveLength(1)
    expect(idsOf(list).toSorted()).toEqual(server.ids().toSorted())
  })
})

describe("with filters", () => {
  // Every third row is food, the rest rent; ids double as days of 2024.
  const fieldsOf = (id) => ({
    trans_category: id % 3 === 0 ? "Food" : "Rent",
    account_name: id % 2 === 0 ? "Checking" : "Card",
    trans_date: new Date(Date.UTC(2024, 0, id)).toISOString().slice(0, 10),
  })
  const food = { ...NO_FILTERS, category: "Food" }

  it("sends the filters that are set along with the paging, and no others", async () => {
    const server = fakeServer(10, fieldsOf)
    const filters = { category: "Food", account: "", start: "2024-01-02", end: "" }

    await fetchFirstRows(server.api, 0, filters)
    await fetchPage(server.api, 2, filters)

    expect(server.requests).toEqual([
      { category: "Food", start: "2024-01-02", page_size: PAGE_SIZE },
      { category: "Food", start: "2024-01-02", page: 2, page_size: PAGE_SIZE },
    ])
  })

  it("reaches every matching row exactly once and no other row", async () => {
    const server = fakeServer(400, fieldsOf)

    let list = await fetchFirstRows(server.api, 0, food)
    expect(list.rows).toHaveLength(PAGE_SIZE)
    while (list.nextPage) {
      list = await loadMore(server.api, list, food)
    }

    expect(idsOf(list)).toEqual(server.ids(food))
    expect(idsOf(list)).toHaveLength(133)
  })

  it("has no next page when the matching rows fit on the first", async () => {
    const server = fakeServer(400, fieldsOf)
    const january = { ...NO_FILTERS, start: "2024-01-01", end: "2024-01-31" }

    const list = await fetchFirstRows(server.api, 0, january)

    expect(idsOf(list)).toEqual(server.ids(january))
    expect(list.rows).toHaveLength(31)
    expect(list.nextPage).toBeNull()
  })

  it("keeps the pages lined up when a matching row is added or deleted", async () => {
    const server = fakeServer(400, fieldsOf)
    let list = await fetchFirstRows(server.api, 0, food)

    while (server.addNewest().trans_category !== "Food");
    list = await reload(server.api, list, food)
    server.remove(list.rows[10].id)
    list = await reload(server.api, list, food)
    list = await loadMore(server.api, list, food)

    expect(idsOf(list)).toEqual(server.ids(food).slice(0, 2 * PAGE_SIZE))
  })

  it("knows which filters are set", () => {
    expect(hasFilters(NO_FILTERS)).toBe(false)
    expect(hasFilters({ ...NO_FILTERS, end: "2024-01-31" })).toBe(true)
    expect(filterParams(NO_FILTERS)).toEqual({})
    expect(filterParams()).toEqual({})
    expect(filterParams({ ...NO_FILTERS, account: "Card" })).toEqual({ account: "Card" })
  })

  it("judges a row the way the server does", () => {
    const row = { trans_category: "Food", account_name: "Card", trans_date: "2024-02-10" }
    const cases = [
      [NO_FILTERS, true],
      [{ category: "food" }, true],
      [{ category: "Foo" }, false],
      [{ account: "CARD" }, true],
      [{ account: "Checking" }, false],
      [{ start: "2024-02-10", end: "2024-02-10" }, true],
      [{ start: "2024-02-11" }, false],
      [{ end: "2024-02-09" }, false],
      [{ category: "Food", account: "Checking" }, false],
    ]

    for (const [filters, expected] of cases) {
      expect(matchesFilters(row, { ...NO_FILTERS, ...filters }), JSON.stringify(filters)).toBe(
        expected
      )
    }
    expect(matchesFilters({ ...row, account_name: "" }, { ...NO_FILTERS, account: "Card" })).toBe(
      false
    )
  })

  it("explains a refused filter and stays silent about other failures", () => {
    const refusal = (data) => ({ response: { status: 400, data } })

    expect(filterErrorMessage(refusal({ start: ["Date has wrong format."] }))).toBe(
      "The From date filter is not one the server accepts. Change or clear it to see transactions."
    )
    expect(filterErrorMessage(refusal({ start: ["x"], end: ["x"] }))).toContain(
      "The From date and To date filters are not ones"
    )
    expect(filterErrorMessage(refusal({}))).toContain("These filters")
    expect(filterErrorMessage({ response: { status: 500 } })).toBe("")
    expect(filterErrorMessage(new Error("connection lost"))).toBe("")
  })
})
