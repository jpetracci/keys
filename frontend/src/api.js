import axios from "axios";
import { ACCESS_TOKEN, REFRESH_TOKEN } from "./constants";

// The API normally shares the page's origin: the deployed app serves the
// built frontend itself, and in development the Vite dev server forwards
// /api to Django (see vite.config.js). Set VITE_API_URL at build time to
// call an API somewhere else.
const baseURL = import.meta.env.VITE_API_URL || "";

const api = axios.create({ baseURL });

// Login and refresh take credentials in the body and answer 401 when those
// are bad: a stored access token is neither needed nor at fault there.
const isTokenEndpoint = (url = "") => url.startsWith("/api/token/");

api.interceptors.request.use(
  (config) => {
    const token = localStorage.getItem(ACCESS_TOKEN);
    if (token && !config.withoutToken && !isTokenEndpoint(config.url)) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  },
  (error) => {
    return Promise.reject(error);
  }
);

let refreshing = null;

// Trade the refresh token for a new access token. Calls made while one is
// already in flight share it. Resolves to true when a new access token is
// stored, and to false when there is no refresh token or the server no
// longer accepts it. Rejects when the server could not be asked at all, in
// which case the stored tokens may still be good.
function refreshAccessToken() {
  const refresh = localStorage.getItem(REFRESH_TOKEN);
  if (!refresh) return Promise.resolve(false);
  refreshing ??= api
    .post("/api/token/refresh/", { refresh })
    .then((res) => {
      localStorage.setItem(ACCESS_TOKEN, res.data.access);
      return true;
    })
    .catch((error) => {
      const status = error.response?.status;
      if (status === 400 || status === 401) return false;
      throw error;
    })
    .finally(() => {
      refreshing = null;
    });
  return refreshing;
}

// What became of the stored tokens after a request was refused: "renewed"
// (there is a new access token), "refused" (the server will never accept
// them again) or "unknown" (the server could not be asked).
async function settleTokens(refusal) {
  // The account the tokens belong to no longer exists, for example because
  // the database was replaced. Asking for a renewal would only fail as well.
  if (refusal.data?.code === "user_not_found") return "refused";
  try {
    return (await refreshAccessToken()) ? "renewed" : "refused";
  } catch {
    return "unknown";
  }
}

// The server answers 401 to any request that carries an expired or invalid
// token, even on endpoints that need no login, so a token left in the browser
// from an earlier session would break every call. Renew it if possible and
// send the request again, without a token if there is no new one: the API
// may not need a login at all, and if it does the retry is refused too.
api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const { config, response } = error;
    const recoverable =
      response?.status === 401 &&
      config?.headers?.Authorization &&
      !config.retriedAfter401 &&
      !isTokenEndpoint(config.url);
    if (!recoverable) return Promise.reject(error);

    const tokens = await settleTokens(response);
    if (tokens === "refused") {
      localStorage.removeItem(ACCESS_TOKEN);
      localStorage.removeItem(REFRESH_TOKEN);
    }
    // When the answer is unknown (a network or server problem) the tokens
    // stay stored, since they may still be good, but are left out of the
    // retry.
    config.retriedAfter401 = true;
    config.withoutToken = tokens !== "renewed";
    delete config.headers.Authorization;
    return api(config);
  }
);

export default api;
