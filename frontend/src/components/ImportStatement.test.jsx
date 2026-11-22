// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import ImportStatement from "./ImportStatement"

// The component talks to this instead of the real HTTP client.
const backend = vi.hoisted(() => ({ post: null }))

vi.mock("../api", () => ({
  default: { post: (...args) => backend.post(...args) },
}))

const RESULT = {
  dry_run: false,
  created: 0,
  skipped: 0,
  errors: 0,
  row_errors: [],
  warnings: [],
  note: null,
}

// Stand-in for POST /api/transactions/import/: answers with `answer` (the
// dry_run flag follows the request) and records what it was sent.
function startServer(answer = {}) {
  const requests = []
  backend.post = vi.fn(async (url, body, config) => {
    requests.push({ url, body, config })
    return { data: { ...RESULT, ...answer, dry_run: config.params.dry_run } }
  })
  return requests
}

// A failure the way axios reports it.
const refusal = (status, data) => Object.assign(new Error("refused"), { response: { status, data } })

const statement = () =>
  new File(["Date,Description,Amount\n2024-03-01,Coffee,-5.00\n"], "march.csv", { type: "text/csv" })

function chooseFile(file = statement()) {
  fireEvent.change(screen.getByLabelText("CSV file"), { target: { files: [file] } })
  return file
}

const button = (name) => screen.getByRole("button", { name })

beforeEach(() => {
  vi.spyOn(console, "error").mockImplementation(() => {})
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe("previewing a statement", () => {
  it("can only be asked for once a file is chosen", () => {
    startServer()
    render(<ImportStatement />)

    expect(button("Preview").disabled).toBe(true)
    expect(button("Import").disabled).toBe(true)

    chooseFile()
    expect(button("Preview").disabled).toBe(false)
    expect(button("Import").disabled).toBe(false)
  })

  it("sends the file as text/csv with the options and shows what would happen", async () => {
    const requests = startServer({ created: 3, skipped: 1 })
    const onChange = vi.fn()
    render(<ImportStatement onChange={onChange} />)

    const file = chooseFile()
    fireEvent.change(screen.getByLabelText("Account name"), { target: { value: " Checking " } })
    fireEvent.click(screen.getByLabelText("Include pending rows"))
    fireEvent.click(button("Preview"))

    await screen.findByText(
      "Preview, nothing saved: would create 3 transactions, skipped 1 row, 0 errors."
    )
    expect(requests).toHaveLength(1)
    expect(requests[0].url).toBe("/api/transactions/import/")
    expect(requests[0].body).toBe(file)
    expect(requests[0].config.headers["Content-Type"]).toBe("text/csv")
    expect(requests[0].config.params).toEqual({
      filename: "march.csv",
      account: "Checking",
      dry_run: true,
      flip_amounts: false,
      debits_positive: false,
      include_pending: true,
    })
    // Nothing was saved, so there is nothing for the page to reload.
    expect(onChange).not.toHaveBeenCalled()
  })

  it("drops the preview when an option changes, since it no longer applies", async () => {
    startServer({ created: 3 })
    render(<ImportStatement />)
    chooseFile()
    fireEvent.click(button("Preview"))
    await screen.findByText(/would create 3 transactions/)

    fireEvent.click(screen.getByLabelText(/flip every amount/))

    expect(screen.queryByText(/would create 3 transactions/)).toBeNull()
  })
})

describe("importing a statement", () => {
  it("imports, shows the counts and tells the page to reload", async () => {
    const requests = startServer({ created: 2, skipped: 5 })
    const onChange = vi.fn()
    render(<ImportStatement onChange={onChange} />)

    chooseFile()
    fireEvent.click(button("Import"))

    await screen.findByText("Imported: created 2 transactions, skipped 5 rows, 0 errors.")
    expect(requests[0].config.params.dry_run).toBe(false)
    expect(onChange).toHaveBeenCalledTimes(1)
    expect(onChange).toHaveBeenCalledWith({ failed: false })
    // The file is done with: a second click must not send it again.
    expect(button("Import").disabled).toBe(true)
  })

  it("lists row errors with their lines, warnings and the other-file note", async () => {
    startServer({
      created: 1,
      errors: 3,
      row_errors: [
        { line: 3, message: "invalid amount: abc" },
        { line: 7, message: "missing transaction date" },
      ],
      warnings: ["not valid UTF-8, read as cp1252"],
      note: "2 skipped row(s) matched transactions that did not come from a file with this name.",
    })
    render(<ImportStatement />)

    chooseFile()
    fireEvent.click(button("Import"))

    await screen.findByText("Imported: created 1 transaction, skipped 0 rows, 3 errors.")
    expect(screen.getByText("Line 3: invalid amount: abc")).toBeTruthy()
    expect(screen.getByText("Line 7: missing transaction date")).toBeTruthy()
    expect(screen.getByText("and 1 more.")).toBeTruthy()
    expect(screen.getByText("not valid UTF-8, read as cp1252")).toBeTruthy()
    expect(screen.getByText(/2 skipped row\(s\) matched transactions/)).toBeTruthy()
  })
})

describe("a refused or failed upload", () => {
  it("shows the server's reason for refusing a file", async () => {
    startServer()
    backend.post.mockRejectedValueOnce(
      refusal(400, { detail: "The file could not be read as a CSV statement; nothing was imported." })
    )
    render(<ImportStatement />)

    chooseFile()
    fireEvent.click(button("Preview"))

    const alert = await screen.findByRole("alert")
    expect(alert.textContent).toBe(
      "The file could not be read as a CSV statement; nothing was imported."
    )
    // The file is still chosen, so the user can fix an option and retry.
    expect(button("Preview").disabled).toBe(false)
  })

  it("shows why an option was rejected", async () => {
    startServer()
    backend.post.mockRejectedValueOnce(refusal(400, { encoding: ["Unknown encoding 'x'."] }))
    render(<ImportStatement />)

    chooseFile()
    fireEvent.click(button("Preview"))

    expect((await screen.findByRole("alert")).textContent).toBe("Unknown encoding 'x'.")
  })

  it("reloads the page when an import gets no answer, as it may have gone through", async () => {
    startServer()
    backend.post.mockRejectedValueOnce(new Error("connection lost"))
    const onChange = vi.fn()
    render(<ImportStatement onChange={onChange} />)

    chooseFile()
    fireEvent.click(button("Import"))

    expect((await screen.findByRole("alert")).textContent).toMatch(/may or may not have been imported/)
    await waitFor(() => expect(onChange).toHaveBeenCalledWith({ failed: true }))
  })
})
