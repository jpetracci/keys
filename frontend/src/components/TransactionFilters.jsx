import { hasFilters, NO_FILTERS } from "../transactionList"

// Offer the value in use even when the server no longer lists it (its last
// transaction was just deleted or re-categorized), so the control never shows
// something other than the filter that is applied.
const withCurrent = (choices, current) =>
  !current || choices.some((choice) => choice.toLowerCase() === current.toLowerCase())
    ? choices
    : [current, ...choices]

// Controls that narrow the list and the totals. The labels differ from those
// of the create and edit forms ("Category", "Account", "Date") so each field
// can be told apart by name.
function TransactionFilters({ filters, options, onChange }) {
  const set = (e) => onChange({ ...filters, [e.target.name]: e.target.value })
  const clear = () => onChange(NO_FILTERS)

  return (
    <form
      className="transaction-filters"
      aria-label="Filter transactions"
      onSubmit={(e) => e.preventDefault()}
    >
      <div>
        <label htmlFor="filter_category">Filter by category</label>
        <select
          id="filter_category"
          name="category"
          value={filters.category}
          onChange={set}
        >
          <option value="">All categories</option>
          {withCurrent(options.categories, filters.category).map((category) => (
            <option key={category} value={category}>
              {category}
            </option>
          ))}
        </select>
      </div>

      <div>
        <label htmlFor="filter_account">Filter by account</label>
        <select
          id="filter_account"
          name="account"
          value={filters.account}
          onChange={set}
        >
          <option value="">All accounts</option>
          {withCurrent(options.accounts, filters.account).map((account) => (
            <option key={account} value={account}>
              {account}
            </option>
          ))}
        </select>
      </div>

      <div>
        <label htmlFor="filter_start">From date</label>
        <input
          type="date"
          id="filter_start"
          name="start"
          value={filters.start}
          onChange={set}
        />
      </div>

      <div>
        <label htmlFor="filter_end">To date</label>
        <input
          type="date"
          id="filter_end"
          name="end"
          value={filters.end}
          onChange={set}
        />
      </div>

      <button type="button" onClick={clear} disabled={!hasFilters(filters)}>
        Clear filters
      </button>
    </form>
  )
}

export default TransactionFilters
