import { describe, expect, it } from "vitest"
import {
  appendNewRows,
  fetchFirstRows,
  fetchPage,
  MAX_PAGE_SIZE,
  PAGE_SIZE,
} from "./transactionList"

// Stand-in for GET /api/transactions/: rows newest first, paged by number
// the way the backend's PageNumberPagination does it.
function fakeServer(rowCount) {
  let rows = Array.from({ length: rowCount }, (_, i) => ({ id: rowCount - i }))
  let lastId = rowCount
  const requests = []

  const api = {
    async get(url, { params = {} } = {}) {
      requests.push(params)
      const pageSize = Math.min(params.page_size ?? PAGE_SIZE, MAX_PAGE_SIZE)
      const start = ((params.page ?? 1) - 1) * pageSize
      const end = start + pageSize
      return {
        data: {
          count: rows.length,
          results: rows.slice(start, end),
          next: end < rows.length ? "http://backend/api/transactions/?page=n" : null,
        },
      }
    },
  }

  return {
    api,
    requests,
    ids: () => rows.map((row) => row.id),
    // A row dated today sorts first; one dated years ago sorts last.
    addNewest() {
      const row = { id: ++lastId }
      rows = [row, ...rows]
      return row
    },
    addOldest() {
      const row = { id: ++lastId }
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
async function loadMore(api, list) {
  const page = await fetchPage(api, list.nextPage)
  return { rows: appendNewRows(list.rows, page.rows), nextPage: page.nextPage }
}

// What the page does after the user adds, edits or deletes a row.
const reload = (api, list) => fetchFirstRows(api, list.rows.length)

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
