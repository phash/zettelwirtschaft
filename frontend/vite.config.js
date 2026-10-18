import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'
import { VitePWA } from 'vite-plugin-pwa'
import { readFileSync } from 'fs'
import { resolve } from 'path'

// Single Source of Truth fuer die angezeigte Version ist die Repo-Datei VERSION
// (wird vom Release-Prozess gepflegt). package.json traegt bewusst KEIN
// "version"-Feld mehr — es war veraltet (1.3.1 statt 1.5.0) und wurde bevorzugt.
// Docker-Build: das Dockerfile kopiert VERSION ueber den benannten Build-Context
// "repo" nach /VERSION, also wieder genau ../VERSION relativ zu /app.
let appVersion = 'dev'
try {
  appVersion = readFileSync(resolve(import.meta.dirname, '../VERSION'), 'utf-8').trim() || 'dev'
} catch {}

export default defineConfig({
  define: {
    __APP_VERSION__: JSON.stringify(appVersion),
  },
  plugins: [
    vue(),
    tailwindcss(),
    VitePWA({
      registerType: 'autoUpdate',
      // Icons via `npm run generate-pwa-assets` (pwa-assets.config.js) erzeugt
      // und in public/ eingecheckt. Chrome bietet die Installation nur an, wenn
      // 192er- und 512er-PNG tatsaechlich abrufbar sind.
      includeAssets: ['favicon.svg', 'favicon.ico', 'apple-touch-icon-180x180.png'],
      manifest: {
        name: 'Zettelwirtschaft',
        short_name: 'Zettel',
        description: 'Lokales Dokumentenmanagementsystem',
        lang: 'de',
        // Eine Markenfarbe ueberall: primary-600 (style.css), Favicon, Icons,
        // <meta name="theme-color"> in index.html.
        theme_color: '#2563eb',
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
            // Eigenes Maskable-Icon (mit Safe-Zone-Padding) statt das
            // any-Icon zusaetzlich als 'any maskable' zu deklarieren — das
            // kombinierte purpose beschneidet das normale Icon.
            src: '/maskable-icon-512x512.png',
            sizes: '512x512',
            type: 'image/png',
            purpose: 'maskable',
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
            // Backup-Downloads (ZIP mit kompletter DB, bei /full auch allen
            // Dokumenten) duerfen NIE in Cache Storage landen.
            // NetworkOnly MUSS vor der breiten /api/-Regel stehen.
            // Seitenbilder des Aufteilungs-Editors (/api/split/<id>/pages/<n>/
            // thumbnail|preview) sind ebenfalls Binaerdaten.
            urlPattern: ({ url }) =>
              /\/api\/.*\/(file|thumbnail)$/.test(url.pathname) ||
              /^\/api\/split\/[^/]+\/pages\//.test(url.pathname) ||
              url.pathname.startsWith('/api/system/backups/'),
            handler: 'NetworkOnly',
          },
          {
            // Nur fetch()/XHR-Aufrufe der App, keine Navigationen (Download-
            // Links, direkt geoeffnete API-URLs). Gecacht werden ausschliesslich
            // 200er-JSON-Antworten — Binaer-/HTML-/Fehlerantworten nie.
            // Beim Abmelden wird 'api-cache' geleert (utils/apiCache.js).
            urlPattern: ({ url, request }) =>
              request.mode !== 'navigate' && url.pathname.startsWith('/api/'),
            handler: 'NetworkFirst',
            options: {
              cacheName: 'api-cache',
              expiration: { maxEntries: 50, maxAgeSeconds: 300 },
              cacheableResponse: {
                statuses: [200],
                headers: { 'content-type': 'application/json' },
              },
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
  // Unit-Tests (Vitest): `npm test`. Nur reine Logik (utils, composables,
  // stores) — UI-Verhalten deckt Playwright (e2e/) ab.
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.js'],
    // Feste Zeitzone, damit die Datums-Tests auf jedem Rechner gleich laufen.
    env: { TZ: 'Europe/Berlin' },
  },
  build: {
    // F-09: Production-Builds ohne Source-Maps. Source-Maps geben den
    // kompletten Original-Code preis (auch wenn der ohnehin Open-Source ist —
    // Defense-in-Depth: weniger Angriffsflaeche fuer XSS/Reverse-Engineering).
    sourcemap: false,
  },
})
