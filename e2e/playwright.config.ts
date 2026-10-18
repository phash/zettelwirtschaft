import { defineConfig, devices } from '@playwright/test';
import { ensureTestCredentials } from './helpers/credentials';

// Ziel der E2E-Tests ist IMMER ein eigener Preview-Server des aktuellen
// Frontend-Builds — nie Port 8080: dort laeuft die echte Installation (Docker
// FRONTEND_PORT bzw. Native SERVER_PORT). Alle API-Aufrufe werden per
// page.route() gemockt; was durchrutscht, beantwortet der Catch-all aus
// helpers/mock.helpers.ts (installApiFallback) mit 501.
//
//   npx playwright test                    -> baut das Frontend und startet
//                                             `vite preview` auf E2E_PORT (4181)
//   BASE_URL=http://localhost:4210 npx playwright test
//                                          -> nutzt einen bereits laufenden
//                                             Preview-Server, kein webServer
// PIN/Dummy-Passwoerter aus e2e/.env.test bzw. zufaellig — im Hauptprozess
// nach process.env, damit alle Worker-Prozesse dieselben Werte erben.
ensureTestCredentials();

const E2E_PORT = Number(process.env.E2E_PORT || 4181);
const BASE_URL = process.env.BASE_URL || `http://127.0.0.1:${E2E_PORT}`;

if (/:8080(\/|$)/.test(BASE_URL)) {
  throw new Error(
    `BASE_URL ${BASE_URL} zeigt auf Port 8080 (Produktiv-Installation). ` +
    'E2E-Tests laufen nur gegen einen eigenen Preview-Server.'
  );
}

export default defineConfig({
  testDir: './tests',
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: 1,
  reporter: process.env.CI ? 'github' : 'html',
  timeout: 30_000,

  use: {
    baseURL: BASE_URL,
    locale: 'de-DE',
    timezoneId: 'Europe/Berlin',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    // Der PWA-Service-Worker (registerType 'autoUpdate' -> clientsClaim)
    // uebernimmt kurz nach dem Laden alle /api-Requests. Requests, die der SW
    // absetzt, sieht page.route() nicht — sie gingen am Mock vorbei ins echte
    // Netz (Ursache der frueheren "Mock-Drift"-Fehlschlaege). Die SW-
    // Caching-Regeln prueft 17-pwa-service-worker.spec.ts gezielt mit erlaubtem SW.
    serviceWorkers: 'block',
  },

  // Nur wenn kein BASE_URL vorgegeben ist: aktuellen Code bauen und auf einem
  // dedizierten Port ausliefern. --strictPort: belegter Port -> Abbruch statt
  // stillem Ausweichen. Kein reuseExistingServer, sonst koennte ein veralteter
  // Build getestet werden.
  webServer: process.env.BASE_URL
    ? undefined
    : {
        command: `npm run build && npx vite preview --port ${E2E_PORT} --strictPort`,
        cwd: '../frontend',
        url: BASE_URL,
        reuseExistingServer: false,
        timeout: 180_000,
        stdout: 'ignore',
        stderr: 'pipe',
      },

  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
    {
      name: 'mobile',
      use: { ...devices['Pixel 5'] },
      grepInvert: /sidebar/i,
    },
  ],
});
