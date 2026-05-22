import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    host: true,  // bind to 0.0.0.0 so LAN devices can reach the dev server
    proxy: {
      // Embed endpoints are unversioned on the backend (/embed/*)
      // — must be listed BEFORE the generic /api rule
      '/api/embed': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        rewrite: path => path.replace(/^\/api/, '')
      },
      // All other API calls go to the versioned backend (/v1/*)
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        rewrite: path => path.replace(/^\/api/, '/v1')
      },
      // FastAPI Swagger UI, ReDoc, and OpenAPI spec — proxy to backend
      '/docs': { target: 'http://localhost:8000', changeOrigin: true },
      '/redoc': { target: 'http://localhost:8000', changeOrigin: true },
      '/openapi.json': { target: 'http://localhost:8000', changeOrigin: true },
    }
  },
  build: {
    rollupOptions: {
      input: {
        main:  'index.html',
        embed: 'src/embed/embed.html',
      }
    }
  }
})
