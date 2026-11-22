import { AxiosError } from "axios"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import api from "./api"
import { ACCESS_TOKEN, REFRESH_TOKEN } from "./constants"

const LIST_URL = "/api/transactions/"
const REFRESH_URL = "/api/token/refresh/"

// The browser's localStorage, which Node does not provide.
function memoryStorage() {
  const items = new Map()
  return {
    getItem: (key) => (items.has(key) ? items.get(key) : null),
    setItem: (key, value) => {
      items.set(key, String(value))
    },
    removeItem: (key) => {
      items.delete(key)
    },
  }
}

const settings = { adapter: api.defaults.adapter, baseURL: api.defaults.baseURL }

// Stand-in for the backend, installed as the transport of the real client.
// Like Django it answers 401 to any request that carries a token other than
// the valid one, whether or not the endpoint needs a login; requests without
// a token succeed unless `requireLogin` is set. `refresh` says what the
// refresh endpoint does: hand out the valid token ("ok"), refuse the refresh
// token ("expired"), fail with a server error ("broken"), or not be reachable
// at all ("offline"). `refusal` is the reason given with a 401.
function startBackend({
  validToken = null,
  requireLogin = false,
  refresh = "expired",
  refusal = "token_not_valid",
} = {}) {
  const requests = []
  const respond = (config, status, data) => {
    const response = { status, statusText: String(status), data, headers: {}, config }
    if (status < 400) return response
    throw new AxiosError(`status ${status}`, AxiosError.ERR_BAD_REQUEST, config, null, response)
  }

  api.defaults.adapter = async (config) => {
    const authorization = config.headers.Authorization ?? null
    requests.push({ url: config.url, uri: api.getUri(config), authorization, body: config.data })

    if (config.url === REFRESH_URL) {
      if (refresh === "offline") {
        throw new AxiosError("Network Error", AxiosError.ERR_NETWORK, config)
      }
      if (refresh === "broken") return respond(config, 500, {})
      return refresh === "ok"
        ? respond(config, 200, { access: validToken })
        : respond(config, 401, { code: "token_not_valid" })
    }
    const refused = authorization ? authorization !== `Bearer ${validToken}` : requireLogin
    return refused ? respond(config, 401, { code: refusal }) : respond(config, 200, { ok: true })
  }

  return {
    requests,
    // Authorization header of each request, in order, with the refresh call
    // shown by name.
    trail: () => requests.map((r) => (r.url === REFRESH_URL ? "refresh" : r.authorization)),
  }
}

beforeEach(() => {
  vi.stubGlobal("localStorage", memoryStorage())
})

afterEach(() => {
  vi.unstubAllGlobals()
  Object.assign(api.defaults, settings)
})

describe("without a stored token", () => {
  it("sends no Authorization header", async () => {
    const backend = startBackend()

    const res = await api.get(LIST_URL)

    expect(res.data).toEqual({ ok: true })
    expect(backend.trail()).toEqual([null])
  })
})

describe("with an expired access token", () => {
  beforeEach(() => {
    localStorage.setItem(ACCESS_TOKEN, "expired")
  })

  it("renews it with the refresh token and repeats the request", async () => {
    localStorage.setItem(REFRESH_TOKEN, "still-good")
    const backend = startBackend({ validToken: "fresh", refresh: "ok" })

    const res = await api.get(LIST_URL)

    expect(res.data).toEqual({ ok: true })
    expect(backend.trail()).toEqual(["Bearer expired", "refresh", "Bearer fresh"])
    expect(JSON.parse(backend.requests[1].body)).toEqual({ refresh: "still-good" })
    expect(backend.requests[1].authorization).toBeNull()
    expect(localStorage.getItem(ACCESS_TOKEN)).toBe("fresh")
  })

  it("drops the tokens when the server refuses to renew and repeats the request without one", async () => {
    // Regression: half an hour after a login every request came back 401,
    // although the API needs no login, until the user visited /logout.
    localStorage.setItem(REFRESH_TOKEN, "expired-too")
    const backend = startBackend({ refresh: "expired" })

    const res = await api.get(LIST_URL)

    expect(res.data).toEqual({ ok: true })
    expect(backend.trail()).toEqual(["Bearer expired", "refresh", null])
    expect(localStorage.getItem(ACCESS_TOKEN)).toBeNull()
    expect(localStorage.getItem(REFRESH_TOKEN)).toBeNull()
  })

  it("does the same when there is no refresh token at all", async () => {
    const backend = startBackend()

    await api.get(LIST_URL)

    expect(backend.trail()).toEqual(["Bearer expired", null])
    expect(localStorage.getItem(ACCESS_TOKEN)).toBeNull()
  })

  it("keeps the tokens when the server could not be asked, and still gets the data", async () => {
    // A dropped connection or a server error says nothing about the refresh
    // token, so it is not thrown away; the request is repeated without a
    // token because this API may not need one.
    for (const refresh of ["offline", "broken"]) {
      localStorage.setItem(ACCESS_TOKEN, "expired")
      localStorage.setItem(REFRESH_TOKEN, "still-good")
      const backend = startBackend({ refresh })

      const res = await api.get(LIST_URL)

      expect(res.data).toEqual({ ok: true })
      expect(backend.trail()).toEqual(["Bearer expired", "refresh", null])
      expect(localStorage.getItem(ACCESS_TOKEN)).toBe("expired")
      expect(localStorage.getItem(REFRESH_TOKEN)).toBe("still-good")
    }
  })

  it("reports the failure when the server could not be asked and a login is required", async () => {
    localStorage.setItem(REFRESH_TOKEN, "still-good")
    const backend = startBackend({ refresh: "offline", requireLogin: true })

    await expect(api.get(LIST_URL)).rejects.toMatchObject({ response: { status: 401 } })

    expect(backend.trail()).toEqual(["Bearer expired", "refresh", null])
    expect(localStorage.getItem(REFRESH_TOKEN)).toBe("still-good")
  })

  it("drops the tokens of an account that no longer exists without asking to renew them", async () => {
    // Regression: after the database was replaced, the renewal itself failed
    // with a server error, so the tokens were kept and every request stayed
    // refused for as long as the refresh token lived.
    localStorage.setItem(REFRESH_TOKEN, "of-a-deleted-user")
    const backend = startBackend({ refusal: "user_not_found", refresh: "broken" })

    const res = await api.get(LIST_URL)

    expect(res.data).toEqual({ ok: true })
    expect(backend.trail()).toEqual(["Bearer expired", null])
    expect(localStorage.getItem(ACCESS_TOKEN)).toBeNull()
    expect(localStorage.getItem(REFRESH_TOKEN)).toBeNull()
  })

  it("asks for the new token at the same address as every other request", async () => {
    // Regression: the refresh URL was glued together by hand, so a base URL
    // ending in a slash produced "//api/token/refresh/" (a 404 and, for a
    // base of "/", a request to a host called "api").
    localStorage.setItem(REFRESH_TOKEN, "still-good")
    api.defaults.baseURL = "http://backend.example/"
    const backend = startBackend({ validToken: "fresh", refresh: "ok" })

    await api.get(LIST_URL)

    expect(backend.requests.map((r) => r.uri)).toEqual([
      "http://backend.example/api/transactions/",
      "http://backend.example/api/token/refresh/",
      "http://backend.example/api/transactions/",
    ])
  })

  it("repeats a request with a body unchanged", async () => {
    const backend = startBackend()

    await api.post(LIST_URL, { trans_description: "Lunch" })

    expect(backend.requests.map((r) => r.body)).toEqual([
      '{"trans_description":"Lunch"}',
      '{"trans_description":"Lunch"}',
    ])
  })

  it("gives up after one retry if the server still answers 401", async () => {
    const backend = startBackend({ requireLogin: true })

    await expect(api.get(LIST_URL)).rejects.toMatchObject({ response: { status: 401 } })

    expect(backend.trail()).toEqual(["Bearer expired", null])
  })

  it("renews once for requests that fail at the same time", async () => {
    localStorage.setItem(REFRESH_TOKEN, "still-good")
    const backend = startBackend({ validToken: "fresh", refresh: "ok" })

    await Promise.all([api.get(LIST_URL), api.get("/api/transactions/summary/")])

    expect(backend.trail().filter((step) => step === "refresh")).toHaveLength(1)
    expect(backend.trail().filter((step) => step === "Bearer fresh")).toHaveLength(2)
  })

  it("leaves a rejected login alone and does not send it the old token", async () => {
    const backend = startBackend({ requireLogin: true })

    await expect(api.post("/api/token/", { username: "a", password: "b" })).rejects.toMatchObject({
      response: { status: 401 },
    })

    expect(backend.trail()).toEqual([null])
    expect(localStorage.getItem(ACCESS_TOKEN)).toBe("expired")
  })
})

describe("other failures", () => {
  it("are passed on untouched", async () => {
    localStorage.setItem(ACCESS_TOKEN, "valid")
    api.defaults.adapter = async (config) => {
      const response = { status: 500, statusText: "Server Error", data: {}, headers: {}, config }
      throw new AxiosError("boom", AxiosError.ERR_BAD_RESPONSE, config, null, response)
    }

    await expect(api.get(LIST_URL)).rejects.toMatchObject({ response: { status: 500 } })
    expect(localStorage.getItem(ACCESS_TOKEN)).toBe("valid")
  })
})
