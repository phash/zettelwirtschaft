import { test, expect, Page } from '@playwright/test';
import { setupDashboardMocks, MOCK_STATS } from '../helpers/mock.helpers';

// Einziger Test MIT aktivem Service-Worker (alle anderen blockieren ihn in
// playwright.config.ts, weil SW-Requests an page.route() vorbeigehen).
// Prueft die Runtime-Caching-Regeln aus frontend/vite.config.js:
//   - /api/*-JSON: NetworkFirst (Fallback auf Cache, wenn das Netz weg ist)
//   - /api/.../file|thumbnail und Backup-Downloads: NetworkOnly (nie im Cache)
// SW-Requests sieht nur context.route() — daher hier keine page.route()-Mocks
// fuer die geprueften Pfade.

test.use({ serviceWorkers: 'allow' });

async function waitForServiceWorkerControl(page: Page) {
  await page.evaluate(async () => {
    await navigator.serviceWorker.ready;
    if (navigator.serviceWorker.controller) return;
    await new Promise<void>((resolve) => {
      navigator.serviceWorker.addEventListener('controllerchange', () => resolve(), { once: true });
    });
  });
}

/** fetch() im Seitenkontext — laeuft ueber den Service-Worker. */
function pageFetch(page: Page, url: string) {
  return page.evaluate(async (u) => {
    try {
      const res = await fetch(u);
      return { ok: res.ok, status: res.status, body: await res.text() };
    } catch (e) {
      return { ok: false, status: 0, body: String(e) };
    }
  }, url);
}

test.describe('PWA service worker caching', () => {
  test('API JSON falls back to the cache, files and backups never do', async ({ page, context }) => {
    await setupDashboardMocks(page);
    await page.goto('/');
    await expect(page.getByText('Gesamt Dokumente')).toBeVisible();
    await waitForServiceWorkerControl(page);

    let online = true;
    const offline = (route: import('@playwright/test').Route) => route.abort('internetdisconnected');
    await context.route('**/api/stats', (route) =>
      online ? route.fulfill({ json: MOCK_STATS }) : offline(route)
    );
    await context.route('**/api/documents/doc-1/file', (route) =>
      online ? route.fulfill({ status: 200, contentType: 'application/pdf', body: '%PDF-1.4 fake' }) : offline(route)
    );
    await context.route('**/api/system/backups/backup_db_1.zip', (route) =>
      online ? route.fulfill({ status: 200, contentType: 'application/zip', body: 'zip' }) : offline(route)
    );

    // Online: alles kommt vom Netz
    expect((await pageFetch(page, '/api/stats')).ok).toBe(true);
    expect((await pageFetch(page, '/api/documents/doc-1/file')).ok).toBe(true);
    expect((await pageFetch(page, '/api/system/backups/backup_db_1.zip')).ok).toBe(true);

    online = false;

    // NetworkFirst: JSON kommt jetzt aus dem Cache
    const stats = await pageFetch(page, '/api/stats');
    expect(stats.ok).toBe(true);
    expect(JSON.parse(stats.body)).toEqual(MOCK_STATS);

    // NetworkOnly: Datei und Backup wurden nicht zwischengespeichert
    expect((await pageFetch(page, '/api/documents/doc-1/file')).ok).toBe(false);
    expect((await pageFetch(page, '/api/system/backups/backup_db_1.zip')).ok).toBe(false);
  });
});
