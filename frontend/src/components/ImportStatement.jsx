import { useRef, useState } from "react"
import api from "../api"
import "../styles/ImportStatement.css"

const OPTIONS = [
  {
    name: "flip_amounts",
    label: "Charges are positive numbers in this file (flip every amount)",
  },
  {
    name: "debits_positive",
    label: "The Debit and Credit columns are the wrong way round",
  },
  { name: "include_pending", label: "Include pending rows" },
]

const NO_OPTIONS = {
  flip_amounts: false,
  debits_positive: false,
  include_pending: false,
}

const plural = (count, word) => `${count} ${word}${count === 1 ? "" : "s"}`

// What the server said about a refused upload, in one line.
function failureMessage(err, dryRun) {
  const data = err.response?.data
  if (typeof data?.detail === "string") return data.detail
  if (data && typeof data === "object") {
    // A rejected option: { encoding: ["Unknown encoding 'x'."] }
    const messages = Object.values(data).flat().filter((m) => typeof m === "string")
    if (messages.length) return messages.join(" ")
  }
  if (err.response?.status === 413) return "The file is too large to import."
  return dryRun
    ? "Could not check the file: the server did not answer."
    : "The server did not answer, so the file may or may not have been imported. Preview it to see what is left to import."
}

// Upload a CSV statement: "Preview" asks the server what importing the file
// would do without saving anything, "Import" does it. `onChange` is called
// after every import attempt (see syncAfterChange in Home.jsx).
function ImportStatement({ onChange }) {
  const [file, setFile] = useState(null)
  const [account, setAccount] = useState("")
  const [options, setOptions] = useState(NO_OPTIONS)
  const [busy, setBusy] = useState(null) // "preview" | "import" | null
  const [result, setResult] = useState(null)
  const [error, setError] = useState("")
  const fileInput = useRef(null)

  // A result describes one file with one set of options: once either changes
  // it no longer says what "Import" would do.
  const clearOutcome = () => {
    setResult(null)
    setError("")
  }

  const send = async (dryRun) => {
    if (!file || busy) return
    setBusy(dryRun ? "preview" : "import")
    clearOutcome()
    let failed = false
    try {
      // The file itself is the body. It must go as text/csv: the server
      // refuses form uploads so that other websites cannot post to it.
      const res = await api.post("/api/transactions/import/", file, {
        headers: { "Content-Type": "text/csv" },
        params: {
          filename: file.name,
          account: account.trim(),
          dry_run: dryRun,
          ...options,
        },
      })
      setResult(res.data)
      if (!dryRun) {
        // Imported: clear the picker so the same file is not sent twice.
        setFile(null)
        if (fileInput.current) fileInput.current.value = ""
      }
    } catch (err) {
      failed = true
      setError(failureMessage(err, dryRun))
      console.error(err)
    }
    setBusy(null)
    // After a failure too: the server may have imported the file although
    // its answer never arrived.
    if (!dryRun) onChange?.({ failed })
  }

  return (
    <section className="import-section" aria-labelledby="import-heading">
      <h2 id="import-heading">Import statement</h2>
      <form
        className="transaction-form import-form"
        onSubmit={(e) => {
          e.preventDefault()
          send(true)
        }}
      >
        <label htmlFor="import_file">CSV file</label>
        <input
          type="file"
          id="import_file"
          accept=".csv,text/csv"
          ref={fileInput}
          onChange={(e) => {
            setFile(e.target.files?.[0] ?? null)
            clearOutcome()
          }}
        />

        <label htmlFor="import_account">Account name</label>
        <input
          type="text"
          id="import_account"
          maxLength={100}
          value={account}
          onChange={(e) => {
            setAccount(e.target.value)
            clearOutcome()
          }}
          placeholder="Optional, e.g. Checking"
        />
        <p className="import-hint">
          Give each account its own name when you import more than one, so the
          same charge on two accounts is kept twice.
        </p>

        {OPTIONS.map(({ name, label }) => (
          <label className="import-option" key={name}>
            <input
              type="checkbox"
              checked={options[name]}
              onChange={(e) => {
                setOptions((current) => ({ ...current, [name]: e.target.checked }))
                clearOutcome()
              }}
            />
            {label}
          </label>
        ))}

        <div className="import-actions">
          <button type="submit" disabled={!file || busy !== null}>
            {busy === "preview" ? "Checking..." : "Preview"}
          </button>
          <button
            type="button"
            className="import-primary"
            disabled={!file || busy !== null}
            onClick={() => send(false)}
          >
            {busy === "import" ? "Importing..." : "Import"}
          </button>
        </div>
      </form>

      {error && (
        <p className="error-message" role="alert">
          {error}
        </p>
      )}

      {result && (
        <div className="import-result" role="status">
          <p className="import-counts">
            {result.dry_run
              ? `Preview, nothing saved: would create ${plural(result.created, "transaction")}`
              : `Imported: created ${plural(result.created, "transaction")}`}
            {`, skipped ${plural(result.skipped, "row")}, ${plural(result.errors, "error")}.`}
          </p>
          {result.warnings?.map((warning) => (
            <p className="import-note" key={warning}>
              {warning}
            </p>
          ))}
          {result.note && <p className="import-note">{result.note}</p>}
          {result.row_errors?.length > 0 && (
            <>
              <ul className="import-errors">
                {result.row_errors.map(({ line, message }) => (
                  <li key={`${line}-${message}`}>
                    Line {line}: {message}
                  </li>
                ))}
              </ul>
              {result.errors > result.row_errors.length && (
                <p className="import-hint">
                  and {result.errors - result.row_errors.length} more.
                </p>
              )}
            </>
          )}
        </div>
      )}
    </section>
  )
}

export default ImportStatement
