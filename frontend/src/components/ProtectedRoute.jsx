import { useCallback, useEffect, useState } from "react"
import { Navigate } from "react-router-dom"
import { jwtDecode } from "jwt-decode"
import api from "../api"
import { REFRESH_TOKEN, ACCESS_TOKEN } from "../constants"

function ProtectedRoute({ children }) {
  const [isAuthorized, setIsAuthorized] = useState(null)

  const refreshToken = useCallback(async () => {
    const refresh = localStorage.getItem(REFRESH_TOKEN)
    if (!refresh) {
      setIsAuthorized(false)
      return
    }

    try {
      const res = await api.post("/api/token/refresh/", { refresh })
      localStorage.setItem(ACCESS_TOKEN, res.data.access)
      setIsAuthorized(true)
    } catch (error) {
      console.error(error)
      localStorage.clear()
      setIsAuthorized(false)
    }
  }, [])

  const auth = useCallback(async () => {
    const token = localStorage.getItem(ACCESS_TOKEN)
    if (!token) {
      setIsAuthorized(false)
      return
    }

    try {
      const decoded = jwtDecode(token)
      const tokenExpiration = decoded.exp
      const now = Date.now() / 1000

      if (tokenExpiration < now) {
        await refreshToken()
      } else {
        setIsAuthorized(true)
      }
    } catch (error) {
      console.error(error)
      localStorage.clear()
      setIsAuthorized(false)
    }
  }, [refreshToken])

  useEffect(() => {
    auth().catch(() => setIsAuthorized(false))
  }, [auth])

  if (isAuthorized === null) {
    return <div>Loading...</div>
  }

  return isAuthorized ? children : <Navigate to="/login" />
}

export default ProtectedRoute
