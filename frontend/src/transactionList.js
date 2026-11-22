// Paging for the transaction list.
//
// The server pages by number, so page N+1 continues where page N ended only
// while nothing is added, removed or re-dated in between. A row created here
// would push the last row shown onto the next page (it then appears twice);
// a row deleted here would pull the next page's first row back onto this one
// (it is then never shown). So the list is reloaded from the top after every
// change the user makes, and rows already on screen are dropped when a page
// is appended in case something else changed the data meanwhile.

export const PAGE_SIZE = 50 // TransactionPagination.page_size on the server
export const MAX_PAGE_SIZE = 500 // TransactionPagination.max_page_size

const LIST_URL = "/api/transactions/"

// The list and the totals can be narrowed by these; "" means "not set".
// A page number only means something for the filters it was fetched with, so
// every request for the same list must send the same filters, and changing
// one starts again from the first page.
export const NO_FILTERS = { category: "", account: "", start: "", end: "" }

export const hasFilters = (filters) =>
  Object.keys(NO_FILTERS).some((name) => filters[name])

// The query parameters for the filters that are set.
export function filterParams(filters = NO_FILTERS) {
  const params = {}
  for (const name of Object.keys(NO_FILTERS)) {
    if (filters[name]) params[name] = filters[name]
  }
  return params
}

// Whether the server would include `row` under `filters`: category and
// account compare without regard to case, the dates are inclusive.
export function matchesFilters(row, filters = NO_FILTERS) {
  const same = (a, b) => (a || "").toLowerCase() === b.toLowerCase()
  if (filters.category && !same(row.trans_category, filters.category)) return false
  if (filters.account && !same(row.account_name, filters.account)) return false
  if (filters.start && row.trans_date < filters.start) return false
  if (filters.end && row.trans_date > filters.end) return false
  return true
}

// What to tell the user when the server refuses the filters (a 400, for
// example for a date in the year 0), or "" when `error` is something else.
export function filterErrorMessage(error) {
  if (error?.response?.status !== 400) return ""
  const refused = Object.keys(error.response.data || {})
  const names = [
    ["start", "From date"],
    ["end", "To date"],
    ["category", "category"],
    ["account", "account"],
  ]
    .filter(([field]) => refused.includes(field))
    .map(([, name]) => name)
  if (names.length === 1) {
    return `The ${names[0]} filter is not one the server accepts. Change or clear it to see transactions.`
  }
  const which = names.length ? `The ${names.join(" and ")} filters` : "These filters"
  return `${which} are not ones the server accepts. Change or clear them to see transactions.`
}

// Load the newest rows in one request: as many whole pages as it takes to
// cover `shownCount`, so a reload keeps about as many rows on screen as there
// were before it.
export async function fetchFirstRows(api, shownCount = 0, filters = NO_FILTERS) {
  const pages = Math.min(
    Math.max(1, Math.ceil(shownCount / PAGE_SIZE)),
    MAX_PAGE_SIZE / PAGE_SIZE
  )
  const res = await api.get(LIST_URL, {
    params: { ...filterParams(filters), page_size: pages * PAGE_SIZE },
  })
  return {
    rows: res.data.results || [],
    nextPage: res.data.next ? pages + 1 : null,
  }
}

// Load one further page of PAGE_SIZE rows, with the filters the rows already
// on screen were loaded with.
export async function fetchPage(api, page, filters = NO_FILTERS) {
  const res = await api.get(LIST_URL, {
    params: { ...filterParams(filters), page, page_size: PAGE_SIZE },
  })
  return {
    rows: res.data.results || [],
    nextPage: res.data.next ? page + 1 : null,
  }
}

// Add a fetched page to the rows on screen, skipping any already there.
export function appendNewRows(shown, incoming) {
  const shownIds = new Set(shown.map((row) => row.id))
  return [...shown, ...incoming.filter((row) => !shownIds.has(row.id))]
}
