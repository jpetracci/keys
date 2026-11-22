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

// Load the newest rows in one request: as many whole pages as it takes to
// cover `shownCount`, so a reload keeps about as many rows on screen as there
// were before it.
export async function fetchFirstRows(api, shownCount = 0) {
  const pages = Math.min(
    Math.max(1, Math.ceil(shownCount / PAGE_SIZE)),
    MAX_PAGE_SIZE / PAGE_SIZE
  )
  const res = await api.get(LIST_URL, {
    params: { page_size: pages * PAGE_SIZE },
  })
  return {
    rows: res.data.results || [],
    nextPage: res.data.next ? pages + 1 : null,
  }
}

// Load one further page of PAGE_SIZE rows.
export async function fetchPage(api, page) {
  const res = await api.get(LIST_URL, {
    params: { page, page_size: PAGE_SIZE },
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
