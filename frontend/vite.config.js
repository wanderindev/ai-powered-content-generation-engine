import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
  ],
  build: {
    // The public entry is now ~670 kB (was 2.6 MB). The only chunks still
    // above Vite's default 500 kB warning are admin-only and lazy-loaded:
    // the AG Grid vendor chunk (~1.2 MB) and the TipTap editor chunk
    // (~440 kB) — neither ships in the public path. AG Grid is an
    // irreducible CMS-grid dependency, so the limit is raised to clear the
    // warning while still flagging any future regression beyond ~1.3 MB.
    chunkSizeWarningLimit: 1300,
    rollupOptions: {
      output: {
        // Split stable, rarely-changing vendor groups into their own chunks
        // so they stay cached across deploys instead of churning the app
        // chunk (#18). AG Grid / TipTap are NOT listed here — they split
        // naturally via the lazy /admin route boundary in App.jsx; naming
        // them would force them back into a chunk the public site references.
        manualChunks: {
          'react-vendor': ['react', 'react-dom', 'react-router-dom'],
          i18n: [
            'i18next',
            'react-i18next',
            'i18next-browser-languagedetector',
            'i18next-http-backend',
          ],
          sentry: ['@sentry/react'],
        },
      },
    },
  },
  server: {
    allowedHosts: true,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  }
})
