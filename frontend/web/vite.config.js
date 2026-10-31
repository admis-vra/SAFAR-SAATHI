import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

const backend = process.env.SAFAR_API || 'http://localhost:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    proxy: {
      '/api': { target: backend, changeOrigin: true, rewrite: (p) => p.replace(/^\/api/, '') },
      '/ws': { target: backend.replace(/^http/, 'ws'), ws: true },
    },
  },
})
