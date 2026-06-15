import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'
import { VitePWA } from 'vite-plugin-pwa'
import { readFileSync } from 'fs'
import { resolve } from 'path'

// Version primär aus package.json (immer im Build-Context), fallback auf VERSION.
// Docker-Frontend-Build sieht ../VERSION nicht, weil nur frontend/ kopiert wird.
let appVersion = 'dev'
try {
  appVersion = JSON.parse(readFileSync(resolve(__dirname, 'package.json'), 'utf-8')).version || 'dev'
} catch {}
if (appVersion === 'dev') {
  try {
    appVersion = readFileSync(resolve(__dirname, '../VERSION'), 'utf-8').trim()
  } catch {}
}

export default defineConfig({
  define: {
    __APP_VERSION__: JSON.stringify(appVersion),
  },
  plugins: [
    vue(),
    tailwindcss(),
    VitePWA({
      registerType: 'autoUpdate',
      includeAssets: ['favicon.svg'],
      manifest: {
        name: 'Zettelwirtschaft',
        short_name: 'Zettel',
        description: 'Lokales Dokumentenmanagementsystem',
        theme_color: '#4F46E5',
        background_color: '#F9FAFB',
        display: 'standalone',
        orientation: 'portrait',
        start_url: '/',
        icons: [
          {
            src: '/pwa-192x192.png',
            sizes: '192x192',
            type: 'image/png',
          },
          {
            src: '/pwa-512x512.png',
            sizes: '512x512',
            type: 'image/png',
          },
          {
            src: '/pwa-512x512.png',
            sizes: '512x512',
            type: 'image/png',
            purpose: 'any maskable',
          },
        ],
      },
      workbox: {
        globPatterns: ['**/*.{js,css,html,ico,png,svg,woff2}'],
        navigateFallbackDenylist: [/^\/api\//],
        runtimeCaching: [
          {
            // FE-H1: Function-Matcher gegen url.pathname. Ein RegExp-urlPattern
            // matcht in Workbox gegen url.href (inkl. Origin "https://host"),
            // sodass ein mit ^\/api\/ verankertes Muster NIE greift und die
            // gesamte Caching-Policy still wirkungslos war. Binaer-Endpunkte
            // (file/thumbnail) duerfen nicht gecacht werden, sonst liefert der
            // Service-Worker index.html statt der Datei (iframe-Vorschau bricht).
            // NetworkOnly MUSS vor der breiten /api/-Regel stehen.
            urlPattern: ({ url }) => /\/api\/.*\/(file|thumbnail)$/.test(url.pathname),
            handler: 'NetworkOnly',
          },
          {
            urlPattern: ({ url }) => url.pathname.startsWith('/api/'),
            handler: 'NetworkFirst',
            options: {
              cacheName: 'api-cache',
              expiration: { maxEntries: 50, maxAgeSeconds: 300 },
            },
          },
        ],
      },
    }),
  ],
  server: {
    // F-09: Dev-Server bindet nur an localhost (statt 0.0.0.0). Verhindert
    // versehentliches Exponieren des unhardened Vite-Dev-Servers im LAN.
    // Wer im LAN testen will: explizit `npm run dev -- --host 0.0.0.0`.
    host: '127.0.0.1',
    port: 3000,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    // F-09: Production-Builds ohne Source-Maps. Source-Maps geben den
    // kompletten Original-Code preis (auch wenn der ohnehin Open-Source ist —
    // Defense-in-Depth: weniger Angriffsflaeche fuer XSS/Reverse-Engineering).
    sourcemap: false,
  },
})
