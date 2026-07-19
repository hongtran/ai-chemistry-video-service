import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      // Backend runs on :8000 by default; the client fetches relative /api
      // paths so it never needs to know the API origin in dev. Override with
      // VITE_API_PROXY if the backend runs elsewhere.
      '/api': process.env.VITE_API_PROXY ?? 'http://localhost:8000',
    },
  },
})
