import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  base: '/ui/',
  server: {
    port: 5173,
    proxy: {
      '/documents': 'http://localhost:8000',
      '/search': 'http://localhost:8000',
      '/index': 'http://localhost:8000',
      '/context': 'http://localhost:8000',
      '/health': 'http://localhost:8000',
    },
  },
})
