import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// The backend runs on 8017 by default (8000 is often taken). Override with RELAY_API.
const api = process.env.RELAY_API ?? 'http://127.0.0.1:8017'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': api,
      '/ws': { target: api.replace('http', 'ws'), ws: true },
    },
  },
  test: { environment: 'jsdom', globals: true },
})
