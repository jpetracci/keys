import { useEffect, useRef, useState } from "react"
import api from "../api"
import Transaction from "../components/Transaction"
import TransactionFilters from "../components/TransactionFilters"
import { localDateString } from "../dates"
import {
  appendNewRows,
  fetchFirstRows,
  fetchPage,
  filterErrorMessage,
  filterParams,
  hasFilters,
  matchesFilters,
  NO_FILTERS,
  PAGE_SIZE,
} from "../transactionList"
import "../styles/Home.css"

const currencyFormatter = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
})

// Built when the form is shown or cleared, so the default date is today's
// even if the page has been open since yesterday.
const emptyForm = () => ({
  trans_date: localDateString(),
  trans_description: "",
  trans_category: "Uncategorized",
  trans_amount: "",
  account_name: "",
})

const EMPTY_SUMMARY = { income: "0", expenses: "0", net: "0", count: 0 }
const NO_OPTIONS = { categories: [], accounts: [] }

function Home() {
  const [transactions, setTransactions] = useState([])
  const [summary, setSummary] = useState(EMPTY_SUMMARY)
  const [nextPage, setNextPage] = useState(null)
  const [formData, setFormData] = useState(emptyForm)
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [reloading, setReloading] = useState(false)
  // True from a change until the list has been reloaded: the rows on screen
  // may not line up with the server's pages (see transactionList.js).
  const [listStale, setListStale] = useState(false)
  const [error, setError] = useState("")
  const [notice, setNotice] = useState("")
  const [filters, setFilters] = useState(NO_FILTERS)
  const [filterOptions, setFilterOptions] = useState(NO_OPTIONS)
  // Set while the server refuses the filters: there is no list to show.
  const [filterError, setFilterError] = useState("")
  // The filters every request uses. A ref, not the state above, because the
  // reload after a change runs from a handler that may have been created
  // before the filters last changed.
  const activeFilters = useRef(NO_FILTERS)
  const latestListRequest = useRef(0)
  const latestSummaryRequest = useRef(0)
  const pendingReloads = useRef(0)

  useEffect(() => {
    refresh()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Server-side totals: always accurate regardless of pagination, and avoids
  // the floating-point drift of summing currency strings on the client.
  // They cover the filtered set, like the list. As with the list, only the
  // most recent request counts: totals for an earlier filter must not replace
  // those for the current one.
  const refreshSummary = async () => {
    const request = ++latestSummaryRequest.current
    try {
      const res = await api.get("/api/transactions/summary/", {
        params: filterParams(activeFilters.current),
      })
      if (request === latestSummaryRequest.current) setSummary(res.data)
    } catch (err) {
      console.error(err)
      // Refused filters select nothing; totals for other filters would mislead.
      if (request === latestSummaryRequest.current && filterErrorMessage(err)) {
        setSummary(EMPTY_SUMMARY)
      }
    }
  }

  // The categories and accounts to offer in the filter controls.
  const refreshFilterOptions = async () => {
    try {
      const res = await api.get("/api/transactions/filter-options/")
      setFilterOptions({ ...NO_OPTIONS, ...res.data })
    } catch (err) {
      console.error(err)
    }
  }

  // Fetch rows with the active filters. Filters the server refuses are not a
  // failed load: the answer is "no rows", with a message saying why.
  const loadRows = async (fetchRows) => {
    try {
      return await fetchRows(activeFilters.current)
    } catch (err) {
      const message = filterErrorMessage(err)
      if (!message) throw err
      return { rows: [], nextPage: null, filterError: message }
    }
  }

  // Fetch rows for the list and hand them to `show`. Only the most recent
  // request may touch the list: an older response arriving late would put
  // back rows as they were before a newer change.
  const requestList = async (load, show, failureMessage) => {
    const request = ++latestListRequest.current
    try {
      const page = await load()
      if (request !== latestListRequest.current) return
      show(page)
      setNextPage(page.nextPage)
      setFilterError(page.filterError || "")
    } catch (err) {
      console.error(err)
      if (request !== latestListRequest.current) return
      if (failureMessage) setError(failureMessage)
    }
    setLoading(false)
  }

  // Replace the list with the newest rows on the server. Called after every
  // change so the rows on screen and the next page to load always line up
  // (see transactionList.js). `created` is a row that was just added: if its
  // date puts it below everything loaded it stays pinned on top, so it is
  // clear that it was saved. A row the filters exclude is not pinned: the
  // list and "Showing N of M" describe the filtered set only.
  const reloadList = async (
    shownCount,
    created = null,
    failureMessage = "Could not reload transactions."
  ) => {
    // "Load more" is disabled meanwhile: which page comes next is only known
    // once the reload has settled.
    pendingReloads.current += 1
    setReloading(true)
    const pinnable = created && matchesFilters(created, activeFilters.current)
    await requestList(
      () => loadRows((active) => fetchFirstRows(api, shownCount, active)),
      ({ rows }) => {
        const pinned = pinnable && !rows.some((row) => row.id === created.id)
        setTransactions(pinned ? [created, ...rows] : rows)
        setListStale(false)
      },
      failureMessage
    )
    pendingReloads.current -= 1
    setReloading(pendingReloads.current > 0)
  }

  const refresh = async () => {
    setLoading(true)
    setError("")
    await requestList(
      async () => {
        const [page] = await Promise.all([
          loadRows((active) => fetchFirstRows(api, 0, active)),
          refreshSummary(),
          refreshFilterOptions(),
        ])
        return page
      },
      ({ rows }) => {
        setTransactions(rows)
        setListStale(false)
      },
      "Could not load transactions."
    )
  }

  // Changing a filter starts again from the first page: the rows on screen
  // and the next page number belong to the old filters.
  const applyFilters = (next) => {
    activeFilters.current = next
    setFilters(next)
    setNotice("")
    refresh()
  }

  const loadMore = async () => {
    if (!nextPage) return
    setLoadingMore(true)
    setError("")
    if (listStale) {
      // The reload after the last change failed, so the next page number can
      // no longer be trusted: fetch everything shown plus one page instead.
      await reloadList(transactions.length + PAGE_SIZE)
    } else {
      await requestList(
        () => fetchPage(api, nextPage, activeFilters.current),
        ({ rows }) => setTransactions((current) => appendNewRows(current, rows)),
        "Could not load more transactions."
      )
    }
    setLoadingMore(false)
  }

  // Bring the totals and the list back in line with the server after a
  // change. This runs after a failed request too: the server may have made
  // the change although its answer never arrived. In that case a reload that
  // fails as well stays quiet, so the message about the change stays up.
  const syncAfterChange = ({ created = null, failed = false } = {}) => {
    setListStale(true)
    refreshSummary()
    refreshFilterOptions()
    reloadList(
      transactions.length,
      created,
      failed ? null : "Could not reload transactions."
    )
  }

  const deleteTransaction = async (id) => {
    setError("")
    setNotice("")
    let failed = false
    try {
      await api.delete(`/api/transactions/${id}/`)
      setTransactions((current) => current.filter((t) => t.id !== id))
    } catch (err) {
      failed = true
      setError("Could not delete the transaction.")
      console.error(err)
    }
    syncAfterChange({ failed })
  }

  const updateTransaction = async (id, changes) => {
    setError("")
    setNotice("")
    let failed = false
    try {
      const res = await api.patch(`/api/transactions/${id}/`, changes)
      setTransactions((current) =>
        current.map((t) => (t.id === id ? res.data : t))
      )
    } catch (err) {
      failed = true
      setError("Could not update the transaction.")
      console.error(err)
      throw err
    } finally {
      syncAfterChange({ failed })
    }
  }

  const createTransaction = async (e) => {
    e.preventDefault()
    setError("")
    setNotice("")
    let created = null
    try {
      const res = await api.post("/api/transactions/", formData)
      created = res.data
      if (matchesFilters(created, activeFilters.current)) {
        setTransactions((current) => [created, ...current])
      } else {
        setNotice(
          `Saved "${created.trans_description}". It is not listed because it does not match the filters.`
        )
      }
      setFormData(emptyForm())
    } catch (err) {
      setError("Could not create the transaction.")
      console.error(err)
    }
    syncAfterChange({ created, failed: !created })
  }

  const handleChange = (e) => {
    setFormData((current) => ({
      ...current,
      [e.target.name]: e.target.value,
    }))
  }

  // Currency strings are formatted once here; arithmetic happens on the
  // server in Decimal so totals don't drift.
  const formatAmount = (value) => currencyFormatter.format(Number(value || 0))

  return (
    <main className="home-page">
      <section className="summary-grid" aria-label="Transaction summary">
        <div className="summary-card">
          <span>Income / Credits</span>
          <strong>{formatAmount(summary.income)}</strong>
        </div>
        <div className="summary-card">
          <span>Expenses / Debits</span>
          <strong>{formatAmount(summary.expenses)}</strong>
        </div>
        <div className="summary-card">
          <span>Net</span>
          <strong>{formatAmount(summary.net)}</strong>
        </div>
      </section>

      {error && <p className="error-message">{error}</p>}
      {notice && (
        <p className="notice-message" role="status">
          {notice}
        </p>
      )}

      <section className="transaction-section">
        <div className="section-header">
          <h2>Transactions</h2>
          <button type="button" onClick={refresh} disabled={loading}>
            Refresh
          </button>
        </div>
        <TransactionFilters
          filters={filters}
          options={filterOptions}
          onChange={applyFilters}
        />
        {loading ? (
          <p>Loading transactions...</p>
        ) : filterError ? (
          <p className="error-message" role="alert">
            {filterError}
          </p>
        ) : transactions.length ? (
          <>
            <div className="transaction-list">
              {transactions.map((transaction) => (
                <Transaction
                  transaction={transaction}
                  onDelete={deleteTransaction}
                  onUpdate={updateTransaction}
                  key={transaction.id}
                />
              ))}
            </div>
            <p className="transaction-count">
              Showing {transactions.length} of {summary.count}
            </p>
            {nextPage && (
              <button
                type="button"
                onClick={loadMore}
                disabled={loadingMore || reloading}
                className="load-more"
              >
                {loadingMore ? "Loading..." : "Load more"}
              </button>
            )}
          </>
        ) : !hasFilters(filters) ? (
          <p>No transactions yet.</p>
        ) : filters.start && filters.end && filters.start > filters.end ? (
          <p>No transactions match: the From date is after the To date.</p>
        ) : (
          <p>No transactions match these filters.</p>
        )}
      </section>

      <section className="transaction-form-section">
        <h2>Create a transaction</h2>
        <form className="transaction-form" onSubmit={createTransaction}>
          <label htmlFor="trans_date">Date</label>
          <input
            type="date"
            id="trans_date"
            name="trans_date"
            required
            onChange={handleChange}
            value={formData.trans_date}
          />

          <label htmlFor="trans_description">Description</label>
          <input
            type="text"
            id="trans_description"
            name="trans_description"
            required
            onChange={handleChange}
            value={formData.trans_description}
          />

          <label htmlFor="trans_category">Category</label>
          <input
            type="text"
            id="trans_category"
            name="trans_category"
            required
            onChange={handleChange}
            value={formData.trans_category}
          />

          <label htmlFor="trans_amount">Amount</label>
          <input
            type="number"
            id="trans_amount"
            name="trans_amount"
            step="0.01"
            required
            onChange={handleChange}
            value={formData.trans_amount}
          />

          <label htmlFor="account_name">Account</label>
          <input
            type="text"
            id="account_name"
            name="account_name"
            onChange={handleChange}
            value={formData.account_name}
            placeholder="Optional"
          />

          <input type="submit" value="Submit" />
        </form>
      </section>
    </main>
  )
}

export default Home
