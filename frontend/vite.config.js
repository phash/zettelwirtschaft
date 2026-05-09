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
      includeAssets: ['favicon.ico'],
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
            urlPattern: /^\/api\/.*\/file$/,
            handler: 'NetworkOnly',
          },
          {
            urlPattern: /^\/api\/.*\/thumbnail$/,
            handler: 'NetworkOnly',
          },
          {
            urlPattern: /^\/api\//,
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
    port: 3000,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
