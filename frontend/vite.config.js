import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // Forward API calls to Django, so the browser only ever talks to this dev
    // server. No request crosses origins and the backend needs no CORS setup.
    proxy: {
      '/api': 'http://127.0.0.1:8000',
    },
  },
  test: {
    // A zone behind UTC, where "today" and "today in UTC" differ every
    // evening: the date tests would pass by accident on a machine set to UTC.
    env: { TZ: 'America/Los_Angeles' },
  },
})
