import { useEffect, useMemo, useState } from "react"
import api from "../api"
import Transaction from "../components/Transaction"
import "../styles/Home.css"

const currencyFormatter = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
})

const today = new Date().toISOString().slice(0, 10)

function Home() {
  const [transactions, setTransactions] = useState([])
  const [formData, setFormData] = useState({
    trans_date: today,
    trans_description: "",
    trans_category: "Uncategorized",
    trans_amount: "",
    account_name: "",
  })
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState("")

  const totals = useMemo(() => {
    return transactions.reduce(
      (summary, transaction) => {
        const amount = Number(transaction.trans_amount)
        summary.net += amount
        if (amount >= 0) {
          summary.income += amount
        } else {
          summary.expenses += amount
        }
        return summary
      },
      { income: 0, expenses: 0, net: 0 }
    )
  }, [transactions])

  useEffect(() => {
    getTransactions()
  }, [])

  const getTransactions = async () => {
    setLoading(true)
    setError("")
    try {
      const res = await api.get("/api/transactions/")
      setTransactions(res.data)
    } catch (err) {
      setError("Could not load transactions.")
      console.error(err)
    } finally {
      setLoading(false)
    }
  }

  const deleteTransaction = async (id) => {
    try {
      await api.delete(`/api/transactions/${id}/`)
      setTransactions((currentTransactions) =>
        currentTransactions.filter((transaction) => transaction.id !== id)
      )
    } catch (err) {
      setError("Could not delete the transaction.")
      console.error(err)
    }
  }

  const updateTransaction = async (id, changes) => {
    try {
      const res = await api.patch(`/api/transactions/${id}/`, changes)
      setTransactions((currentTransactions) =>
        currentTransactions.map((transaction) =>
          transaction.id === id ? res.data : transaction
        )
      )
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
      setTransactions((currentTransactions) => [res.data, ...currentTransactions])
      setFormData({
        trans_date: today,
        trans_description: "",
        trans_category: "Uncategorized",
        trans_amount: "",
        account_name: "",
      })
    } catch (err) {
      setError("Could not create the transaction.")
      console.error(err)
    }
  }

  const handleChange = (e) => {
    setFormData((currentFormData) => ({
      ...currentFormData,
      [e.target.name]: e.target.value,
    }))
  }

  return (
    <main className="home-page">
      <section className="summary-grid" aria-label="Transaction summary">
        <div className="summary-card">
          <span>Income / Credits</span>
          <strong>{currencyFormatter.format(totals.income)}</strong>
        </div>
        <div className="summary-card">
          <span>Expenses / Debits</span>
          <strong>{currencyFormatter.format(totals.expenses)}</strong>
        </div>
        <div className="summary-card">
          <span>Net</span>
          <strong>{currencyFormatter.format(totals.net)}</strong>
        </div>
      </section>

      {error && <p className="error-message">{error}</p>}

      <section className="transaction-section">
        <div className="section-header">
          <h2>Transactions</h2>
          <button type="button" onClick={getTransactions} disabled={loading}>
            Refresh
          </button>
        </div>
        {loading ? (
          <p>Loading transactions...</p>
        ) : transactions.length ? (
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
