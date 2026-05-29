import { useEffect, useState } from "react"
import api from "../api"
import Transaction from "../components/Transaction"
import "../styles/Home.css"

const currencyFormatter = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
})

const today = new Date().toISOString().slice(0, 10)

const EMPTY_FORM = {
  trans_date: today,
  trans_description: "",
  trans_category: "Uncategorized",
  trans_amount: "",
  account_name: "",
}

const EMPTY_SUMMARY = { income: "0", expenses: "0", net: "0", count: 0 }

function Home() {
  const [transactions, setTransactions] = useState([])
  const [summary, setSummary] = useState(EMPTY_SUMMARY)
  const [nextPageUrl, setNextPageUrl] = useState(null)
  const [formData, setFormData] = useState(EMPTY_FORM)
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState("")

  useEffect(() => {
    refresh()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Server-side totals: always accurate regardless of pagination, and avoids
  // the floating-point drift of summing currency strings on the client.
  const refreshSummary = async () => {
    try {
      const res = await api.get("/api/transactions/summary/")
      setSummary(res.data)
    } catch (err) {
      console.error(err)
    }
  }

  const refresh = async () => {
    setLoading(true)
    setError("")
    try {
      const [listRes] = await Promise.all([
        api.get("/api/transactions/"),
        refreshSummary(),
      ])
      setTransactions(listRes.data.results || [])
      setNextPageUrl(listRes.data.next || null)
    } catch (err) {
      setError("Could not load transactions.")
      console.error(err)
    } finally {
      setLoading(false)
    }
  }

  const loadMore = async () => {
    if (!nextPageUrl) return
    setLoadingMore(true)
    try {
      // nextPageUrl is an absolute URL from DRF; strip the origin so axios
      // routes it through the configured baseURL (handy in Choreo).
      const path = nextPageUrl.replace(/^https?:\/\/[^/]+/, "")
      const res = await api.get(path)
      setTransactions((current) => [...current, ...(res.data.results || [])])
      setNextPageUrl(res.data.next || null)
    } catch (err) {
      setError("Could not load more transactions.")
      console.error(err)
    } finally {
      setLoadingMore(false)
    }
  }

  const deleteTransaction = async (id) => {
    try {
      await api.delete(`/api/transactions/${id}/`)
      setTransactions((current) => current.filter((t) => t.id !== id))
      refreshSummary()
    } catch (err) {
      setError("Could not delete the transaction.")
      console.error(err)
    }
  }

  const updateTransaction = async (id, changes) => {
    try {
      const res = await api.patch(`/api/transactions/${id}/`, changes)
      setTransactions((current) =>
        current.map((t) => (t.id === id ? res.data : t))
      )
      // Amount may have changed -- refresh totals.
      if (Object.prototype.hasOwnProperty.call(changes, "trans_amount")) {
        refreshSummary()
      }
    } catch (err) {
      setError("Could not update the transaction.")
      console.error(err)
      throw err
    }
  }

  const createTransaction = async (e) => {
    e.preventDefault()
    setError("")

    try {
      const res = await api.post("/api/transactions/", formData)
      setTransactions((current) => [res.data, ...current])
      setFormData(EMPTY_FORM)
      refreshSummary()
    } catch (err) {
      setError("Could not create the transaction.")
      console.error(err)
    }
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

      <section className="transaction-section">
        <div className="section-header">
          <h2>Transactions</h2>
          <button type="button" onClick={refresh} disabled={loading}>
            Refresh
          </button>
        </div>
        {loading ? (
          <p>Loading transactions...</p>
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
            {nextPageUrl && (
              <button
                type="button"
                onClick={loadMore}
                disabled={loadingMore}
                className="load-more"
              >
                {loadingMore ? "Loading..." : "Load more"}
              </button>
            )}
          </>
        ) : (
          <p>No transactions yet.</p>
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
