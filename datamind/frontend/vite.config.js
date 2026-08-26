import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Which backend this dev server proxies to. Defaults to the single local
// backend; override to point a second dev server at a second instance, e.g.
//   VITE_BACKEND=http://localhost:8001 npm run dev -- --port 5174
// so both SUBSCRIPTION_FREE modes can be driven side by side against one DB.
const BACKEND = process.env.VITE_BACKEND || 'http://localhost:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    host: true,  // bind to 0.0.0.0 so LAN devices can reach the dev server
    proxy: {
      // Embed endpoints are unversioned on the backend (/embed/*)
      // — must be listed BEFORE the generic /api rule
      '/api/embed': {
        target: BACKEND,
        changeOrigin: true,
        rewrite: path => path.replace(/^\/api/, '')
      },
      // QA dev routes are unversioned (/qa/*) — like /embed, must precede /api.
      // Dev-server only; there is no production equivalent of this rule, and
      // the backend refuses to mount /qa outside a dev box anyway.
      '/api/qa': {
        target: BACKEND,
        changeOrigin: true,
        rewrite: path => path.replace(/^\/api/, '')
      },
      // All other API calls go to the versioned backend (/v1/*)
      '/api': {
        target: BACKEND,
        changeOrigin: true,
        rewrite: path => path.replace(/^\/api/, '/v1')
      },
      // FastAPI Swagger UI, ReDoc, and OpenAPI spec — proxy to backend
      '/docs': { target: BACKEND, changeOrigin: true },
      '/redoc': { target: BACKEND, changeOrigin: true },
      '/openapi.json': { target: BACKEND, changeOrigin: true },
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
