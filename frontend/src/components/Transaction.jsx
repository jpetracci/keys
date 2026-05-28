import { useState } from "react"
import "../styles/Transaction.css"

const currencyFormatter = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
})

function Transaction({ transaction, onDelete, onUpdate }) {
  const [isEditing, setIsEditing] = useState(false)
  const [draft, setDraft] = useState(transaction)
  const [saving, setSaving] = useState(false)

  const amount = Number(transaction.trans_amount)
  const formattedDate = new Date(`${transaction.trans_date}T00:00:00`).toLocaleDateString("en-US")

  const startEdit = () => {
    setDraft(transaction)
    setIsEditing(true)
  }

  const cancelEdit = () => {
    setIsEditing(false)
    setDraft(transaction)
  }

  const handleChange = (e) => {
    setDraft((current) => ({ ...current, [e.target.name]: e.target.value }))
  }

  const saveEdit = async () => {
    setSaving(true)
    try {
      await onUpdate(transaction.id, {
        trans_date: draft.trans_date,
        trans_description: draft.trans_description,
        trans_category: draft.trans_category,
        trans_amount: draft.trans_amount,
        account_name: draft.account_name || "",
      })
      setIsEditing(false)
    } finally {
      setSaving(false)
    }
  }

  if (isEditing) {
    return (
      <div className="transaction-container editing">
        <input
          className="edit-input"
          name="trans_description"
          value={draft.trans_description}
          onChange={handleChange}
        />
        <input
          className="edit-input"
          name="trans_category"
          value={draft.trans_category}
          onChange={handleChange}
        />
        <input
          className="edit-input"
          type="date"
          name="trans_date"
          value={draft.trans_date}
          onChange={handleChange}
        />
        <input
          className="edit-input"
          type="number"
          step="0.01"
          name="trans_amount"
          value={draft.trans_amount}
          onChange={handleChange}
        />
        <input
          className="edit-input"
          name="account_name"
          placeholder="Account"
          value={draft.account_name || ""}
          onChange={handleChange}
        />
        <div className="row-actions">
          <button type="button" onClick={saveEdit} disabled={saving}>
            {saving ? "Saving..." : "Save"}
          </button>
          <button type="button" onClick={cancelEdit} disabled={saving}>
            Cancel
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="transaction-container">
      <div className="transaction-main">
        <p className="transaction-description">{transaction.trans_description}</p>
        <p className="transaction-meta">
          {transaction.trans_category}
          {transaction.account_name ? ` • ${transaction.account_name}` : ""}
        </p>
      </div>
      <p className="transaction-date">{formattedDate}</p>
      <p className={amount < 0 ? "transaction-amount negative" : "transaction-amount positive"}>
        {currencyFormatter.format(amount)}
      </p>
      <div className="row-actions">
        <button type="button" className="edit-button" onClick={startEdit}>
          Edit
        </button>
        <button
          type="button"
          className="delete-button"
          onClick={() => onDelete(transaction.id)}
        >
          Delete
        </button>
      </div>
    </div>
  )
}

export default Transaction
