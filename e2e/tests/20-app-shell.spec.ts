import { test, expect, Page } from '@playwright/test';
import {
  setupBaseMocks,
  setupDashboardMocks,
  setupDocumentsMocks,
  setupDocumentDetailMocks,
  setupUploadMocks,
  setupSearchMocks,
  installApiFallback,
  setupPinAuthMocks,
  MOCK_DOCUMENTS,
  MOCK_DOCUMENT_DETAIL,
} from '../helpers/mock.helpers';

// App-Shell, Auth-Flow und gemeinsame Infrastruktur (frontend-infra-layout):
// PIN-Seite ohne Shell, 401-Interceptor, Router-Guard, Navigation (Mehr-Menue),
// Header (Benachrichtigungen, Autocomplete, Abmelden), Dialog-/Toast-A11y,
// Einzel-Datei-Upload, Tag-Entfernen per ID, Formatter, PWA-Assets.

/** PIN-Seite mit genau EINEM redirect-Ziel (nicht verschachtelt). */
async function expectPinRedirect(page: Page, target: string) {
  await expect
    .poll(() => {
      const u = new URL(page.url());
      return `${u.pathname} -> ${u.searchParams.get('redirect')}`;
    })
    .toBe(`/pin -> ${target}`);
}

/** PIN aktiv, nicht angemeldet (gemeinsamer Mock: 401 fuer alles ausser
 *  auth/* und /health, wie die echte PIN-Middleware). */
const setupLockedBackend = (page: Page) => setupPinAuthMocks(page, { pin: '123456' });

test.describe('PIN-Seite und Session-Ablauf', () => {
  test('PIN-Seite ohne App-Shell, keine Poller, kein verschachtelter Redirect; nach Login zum Ziel', async ({ page }) => {
    await page.clock.install();
    const backend = await setupLockedBackend(page);

    await page.goto('/hilfe');
    await expectPinRedirect(page, '/hilfe');
    await expect(page.locator('#pin')).toBeVisible();

    // Keine Shell: keine Sidebar, keine Header-Suche, keine Glocke, keine BottomNav
    await expect(page.locator('aside')).toHaveCount(0);
    await expect(page.getByRole('searchbox')).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Benachrichtigungen' })).toHaveCount(0);
    await expect(page.locator('nav.fixed.bottom-0')).toHaveCount(0);

    // > 2 Poll-Intervalle (60 s) auf der PIN-Seite warten
    await page.clock.runFor(130_000);
    expect(new URL(page.url()).searchParams.get('redirect')).toBe('/hilfe');
    expect(backend.blockedRequests.filter((p) => p.includes('/notifications'))).toEqual([]);

    // Nach dem Login antworten die Endpunkte normal
    await setupBaseMocks(page);
    await page.route('**/api/review/stats', (route) => route.fulfill({ json: { open_documents: 0 } }));
    await page.locator('#pin').fill('123456');
    await page.getByRole('button', { name: 'Anmelden' }).click();
    await expect(page).toHaveURL(/\/hilfe$/);
    await expect(page.locator('#pin')).toHaveCount(0);
  });

  test('redirect auf die PIN-Seite selbst wird verworfen', async ({ page }) => {
    await setupLockedBackend(page);
    await page.goto('/pin?redirect=' + encodeURIComponent('/pin?redirect=/steuer'));
    // Erst nach der Statusabfrage die Daten-Mocks setzen (die bringen einen
    // eigenen auth/status ohne PIN mit).
    await expect(page.locator('#pin')).toBeVisible();
    await setupDashboardMocks(page);
    await page.route('**/api/review/stats', (route) => route.fulfill({ json: { open_documents: 0 } }));
    await page.locator('#pin').fill('123456');
    await page.getByRole('button', { name: 'Anmelden' }).click();
    await expect(page).toHaveURL(/\/$/);
    await expect(page.locator('#pin')).toHaveCount(0);
  });

  test('Ladetext des Anmelde-Buttons mit echtem Umlaut', async ({ page }) => {
    await setupLockedBackend(page);
    await page.route('**/api/auth/login', async (route) => {
      await new Promise((r) => setTimeout(r, 800));
      return route.fulfill({ status: 401, json: { detail: 'Falscher PIN' } });
    });
    await page.goto('/pin');
    await page.locator('#pin').fill('0000');
    await page.getByRole('button', { name: 'Anmelden' }).click();
    await expect(page.getByRole('button', { name: 'Prüfen…' })).toBeVisible();
    await expect(page.getByRole('alert')).toHaveText('Falscher PIN');
  });

  test('nach Session-Ablauf (401) bleibt der Guard zu', async ({ page }) => {
    const session = await setupPinAuthMocks(page, {
      authenticated: true,
      dataMocks: async (p) => {
        await setupDocumentsMocks(p);
        await p.route('**/api/review/stats', (route) => route.fulfill({ json: { open_documents: 0 } }));
      },
    });

    await page.goto('/hilfe');
    await expect(page).toHaveURL(/\/hilfe$/);
    // Erst wenn alle Start-Requests durch sind ablaufen lassen — sonst leitet
    // schon ein spaeter Hintergrund-Request (korrekt) zur PIN-Seite um.
    await page.waitForLoadState('networkidle');
    session.expire();
    // Sidebar (Desktop) bzw. BottomNav (Smartphone)
    await page.locator('a[href="/dokumente"]:visible').first().click();
    await expectPinRedirect(page, '/dokumente');

    // Zurueck zu /hilfe ohne Login -> Guard leitet wieder zur PIN-Seite
    await page.goBack();
    await expectPinRedirect(page, '/hilfe');
    await expect(page.locator('aside')).toHaveCount(0);
  });

  test('Abmelden beendet die Sitzung und fuehrt zur PIN-Seite', async ({ page }) => {
    const session = await setupPinAuthMocks(page, {
      authenticated: true,
      dataMocks: async (p) => {
        await setupDashboardMocks(p);
        await p.route('**/api/review/stats', (route) => route.fulfill({ json: { open_documents: 0 } }));
      },
    });

    await page.goto('/');
    await page.getByRole('button', { name: 'Abmelden' }).click();
    await expect(page).toHaveURL(/\/pin/);
    expect(session.logoutCalls).toBe(1);
    await expect(page.locator('#pin')).toBeVisible();
    // Ohne erneuten Login kommt man nicht zurueck
    await page.goto('/dokumente');
    await expectPinRedirect(page, '/dokumente');
  });

  test('kein Abmelden-Button ohne PIN-Schutz', async ({ page }) => {
    await installApiFallback(page);
    await setupDashboardMocks(page);
    await page.route('**/api/review/stats', (route) => route.fulfill({ json: { open_documents: 0 } }));
    await page.goto('/');
    await expect(page.getByRole('heading').first()).toBeVisible();
    await expect(page.getByRole('button', { name: 'Abmelden' })).toHaveCount(0);
  });
});

test.describe('Navigation Smartphone/Tablet', () => {
  test.use({ viewport: { width: 375, height: 740 } });

  test('Mehr-Menue erreicht alle uebrigen Seiten, mit Prüf-Badge', async ({ page }) => {
    await installApiFallback(page);
    await setupDashboardMocks(page);
    await page.route('**/api/review/stats', (route) =>
      route.fulfill({ json: { open_documents: 3, open_questions: 5, answered_questions: 1 } }));

    await page.goto('/hilfe');
    const bottomNav = page.locator('nav.fixed.bottom-0');
    await expect(bottomNav.locator('a')).toHaveCount(4);

    const more = bottomNav.getByRole('button', { name: 'Mehr' });
    await more.click();
    await expect(more).toHaveAttribute('aria-expanded', 'true');
    const sheet = page.getByRole('dialog', { name: 'Weitere Seiten' });
    await expect(sheet).toBeVisible();
    for (const label of ['Dashboard', 'Upload', 'Zu prüfen', 'Steuer', 'Garantien', 'System', 'Hilfe']) {
      await expect(sheet.getByRole('link', { name: new RegExp(label) })).toBeVisible();
    }
    await expect(sheet.getByTestId('review-badge')).toContainText('3');

    // aria-modal: Tab bleibt im Menue (vom letzten Eintrag zurueck zum ersten)
    const links = sheet.getByRole('link');
    await expect(links.first()).toBeFocused();
    await links.last().focus();
    await page.keyboard.press('Tab');
    await expect(links.first()).toBeFocused();
    await page.keyboard.press('Shift+Tab');
    await expect(links.last()).toBeFocused();

    // Escape schliesst, Fokus zurueck auf "Mehr"
    await page.keyboard.press('Escape');
    await expect(sheet).toHaveCount(0);
    await expect(more).toBeFocused();

    // Navigation ueber das Menue schliesst es
    await more.click();
    await page.getByRole('dialog', { name: 'Weitere Seiten' }).getByRole('link', { name: /Dashboard/ }).click();
    await expect(page).toHaveURL(/\/$/);
    await expect(page.getByRole('dialog', { name: 'Weitere Seiten' })).toHaveCount(0);
  });

  test('Header-Logo fuehrt zum Dashboard', async ({ page }) => {
    await installApiFallback(page);
    await setupDashboardMocks(page);
    await page.route('**/api/review/stats', (route) => route.fulfill({ json: { open_documents: 0 } }));
    await page.goto('/hilfe');
    await page.locator('header').getByRole('link', { name: /zum Dashboard/ }).click();
    await expect(page).toHaveURL(/\/$/);
  });
});

test.describe('Header', () => {
  test('Benachrichtigung zeigt Zeitpunkt, markiert sich gelesen und oeffnet das Dokument', async ({ page }) => {
    await installApiFallback(page);
    await setupDashboardMocks(page);
    await setupDocumentDetailMocks(page, { ...MOCK_DOCUMENT_DETAIL, id: 'doc-7' });
    await page.route('**/api/review/stats', (route) => route.fulfill({ json: { open_documents: 0 } }));
    await page.route('**/api/notifications/count', (route) => route.fulfill({ json: { unread: 1 } }));
    await page.route('**/api/notifications?*', (route) =>
      route.fulfill({
        json: [
          {
            id: 11,
            type: 'WARRANTY_EXPIRING',
            title: 'Garantie läuft bald ab',
            message: 'Waschmaschine: noch 30 Tage',
            is_read: false,
            document_id: 'doc-7',
            created_at: '2026-03-01T08:00:00',
          },
        ],
      }));
    const readCalls: string[] = [];
    await page.route('**/api/notifications/*/read', (route) => {
      readCalls.push(new URL(route.request().url()).pathname);
      return route.fulfill({ json: { message: 'ok' } });
    });

    await page.goto('/');
    const bell = page.getByRole('button', { name: 'Benachrichtigungen' });
    await bell.click();
    await expect(bell).toHaveAttribute('aria-expanded', 'true');
    // naiver Zeitstempel = UTC -> 09:00 Berlin
    await expect(page.getByText('1.3.2026, 09:00:00')).toBeVisible();

    // Escape schliesst das Dropdown
    await page.keyboard.press('Escape');
    await expect(page.getByText('Waschmaschine: noch 30 Tage')).toHaveCount(0);

    await bell.click();
    await page.getByRole('button', { name: /Garantie läuft bald ab – Dokument öffnen/ }).click();
    await expect(page).toHaveURL(/\/dokumente\/doc-7$/);
    expect(readCalls).toEqual(['/api/notifications/11/read']);
  });

  test('Autocomplete oeffnet sich nach Enter bzw. geleertem Feld nicht mehr', async ({ page }) => {
    // Feste Uhr statt Wartezeiten: der 200-ms-Debounce feuert nur per runFor.
    await page.clock.install();
    await installApiFallback(page);
    await setupDashboardMocks(page);
    await setupSearchMocks(page);
    await page.route('**/api/review/stats', (route) => route.fulfill({ json: { open_documents: 0 } }));
    let suggestCalls = 0;
    await page.route('**/api/search/suggest?*', (route) => {
      suggestCalls += 1;
      return route.fulfill({ json: { suggestions: ['rechnung', 'rechtsschutz'] } });
    });

    await page.goto('/');
    const search = page.getByRole('searchbox', { name: 'Dokumente durchsuchen' });

    // Feld leeren, bevor der 200-ms-Timer feuert: Timer wird verworfen
    await search.pressSequentially('rech');
    await search.fill('');
    await page.clock.runFor(600);
    expect(suggestCalls).toBe(0);
    await expect(page.getByRole('listbox', { name: 'Suchvorschläge' })).toHaveCount(0);

    // Enter direkt nach dem Tippen: kein Vorschlags-Request mehr
    await search.pressSequentially('rech');
    await search.press('Enter');
    await expect(page).toHaveURL(/\/suche\?q=rech/);
    await page.clock.runFor(600);
    expect(suggestCalls).toBe(0);
    await expect(page.getByRole('listbox', { name: 'Suchvorschläge' })).toHaveCount(0);

    // Normalfall: Vorschlaege erscheinen
    await search.fill('');
    await search.pressSequentially('rech');
    await page.clock.runFor(250);
    await expect(page.getByRole('listbox', { name: 'Suchvorschläge' })).toBeVisible();
    expect(suggestCalls).toBe(1);
  });

  test('PIN-Hinweis bleibt nach dem Ausblenden auch nach Reload weg', async ({ page }) => {
    await installApiFallback(page);
    await setupDashboardMocks(page); // MOCK_AUTH_STATUS: pin_warning true
    await page.route('**/api/review/stats', (route) => route.fulfill({ json: { open_documents: 0 } }));
    await page.goto('/');
    await expect(page.getByText('PIN-Schutz ist deaktiviert')).toBeVisible();
    await page.getByRole('button', { name: /Hinweis ausblenden/ }).click();
    await expect(page.getByText('PIN-Schutz ist deaktiviert')).toHaveCount(0);
    await page.reload();
    await expect(page.getByRole('searchbox', { name: 'Dokumente durchsuchen' })).toBeVisible();
    await expect(page.getByText('PIN-Schutz ist deaktiviert')).toHaveCount(0);
  });
});

test.describe('Gemeinsame Komponenten', () => {
  test('ConfirmDialog: alertdialog, Fokus auf Abbrechen, Escape schliesst', async ({ page }) => {
    await installApiFallback(page);
    await setupDocumentDetailMocks(page);
    await page.route('**/api/review/stats', (route) => route.fulfill({ json: { open_documents: 0 } }));
    await page.goto(`/dokumente/${MOCK_DOCUMENT_DETAIL.id}`);
    await page.getByRole('button', { name: 'Löschen', exact: true }).click();
    const dialog = page.getByRole('alertdialog');
    await expect(dialog).toBeVisible();
    await expect(dialog).toHaveAttribute('aria-modal', 'true');
    await expect(dialog.getByRole('button', { name: 'Abbrechen' })).toBeFocused();
    await page.keyboard.press('Escape');
    await expect(dialog).toHaveCount(0);
  });

  test('Tag wird per ID entfernt (Sonderzeichen im Namen)', async ({ page }) => {
    const doc = { ...MOCK_DOCUMENT_DETAIL, tags: [{ id: 9, name: 'strom/gas #1' }] };
    await installApiFallback(page);
    await setupDocumentDetailMocks(page, doc);
    await page.route('**/api/review/stats', (route) => route.fulfill({ json: { open_documents: 0 } }));
    const deletes: string[] = [];
    await page.route('**/api/documents/*/tags/*', (route) => {
      if (route.request().method() === 'DELETE') {
        deletes.push(new URL(route.request().url()).pathname);
        return route.fulfill({ json: { message: 'entfernt' } });
      }
      return route.fallback();
    });
    await page.goto(`/dokumente/${doc.id}`);
    await page.getByRole('button', { name: 'Tag „strom/gas #1“ entfernen' }).click();
    await expect.poll(() => deletes).toEqual([`/api/documents/${doc.id}/tags/9`]);
  });

  test('Upload: eine Datei pro Request, 413 wird als "Datei zu groß" gemeldet', async ({ page }) => {
    await installApiFallback(page);
    await setupUploadMocks(page);
    await page.route('**/api/review/stats', (route) => route.fulfill({ json: { open_documents: 0 } }));
    let uploads = 0;
    await page.route('**/api/documents/upload', (route) => {
      uploads += 1;
      if (uploads === 2) {
        return route.fulfill({ status: 413, contentType: 'text/html', body: '<html>413 Request Entity Too Large</html>' });
      }
      return route.fulfill({
        json: { uploaded: [{ job_id: 'job-1', document_id: 'job-1', original_filename: 'a.pdf', status: 'PENDING', message: 'ok' }], rejected: [] },
      });
    });
    await page.route('**/api/documents/*/status', (route) =>
      route.fulfill({ json: { id: 'job-1', status: 'PENDING', original_filename: 'a.pdf' } }));

    await page.goto('/upload');
    await page.locator('input[type="file"]').setInputFiles([
      { name: 'a.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4 a') },
      { name: 'gross.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4 b') },
    ]);
    await expect.poll(() => uploads).toBe(2);
    await expect(page.getByText('gross.pdf: Datei zu groß (max. 50 MB).')).toBeVisible();
    // Toast-Region ist fuer Screenreader angekuendigt
    await expect(page.locator('[aria-live="polite"]')).toHaveCount(1);
  });

  test('Dokumentliste: volle Typ-Bezeichnung und Nicht-ISO-Waehrung brechen nichts', async ({ page }) => {
    await installApiFallback(page);
    await setupDocumentsMocks(page);
    await page.route('**/api/review/stats', (route) => route.fulfill({ json: { open_documents: 0 } }));
    const items = [
      { ...MOCK_DOCUMENTS.items[0], document_type: 'VERSICHERUNGSPOLICE', amount: 12.5, currency: '€' },
      ...MOCK_DOCUMENTS.items.slice(1),
    ];
    await page.route('**/api/documents?*', (route) => route.fulfill({ json: { ...MOCK_DOCUMENTS, items } }));
    await page.goto('/dokumente');
    await expect(page.getByText(MOCK_DOCUMENTS.items[1].title).first()).toBeVisible();
    await expect(page.locator('.badge:visible', { hasText: 'Versicherungspolice' }).first()).toBeVisible();
    await expect(page.locator(':text-is("12,50 €"):visible').first()).toBeVisible();
  });
});

test.describe('PWA', () => {
  test('Manifest ist deutsch und alle Icons sind abrufbar', async ({ page }) => {
    const res = await page.request.get('/manifest.webmanifest');
    expect(res.ok()).toBeTruthy();
    const manifest = await res.json();
    expect(manifest.lang).toBe('de');
    expect(manifest.theme_color).toBe('#2563eb');
    const purposes = manifest.icons.map((i: { purpose?: string }) => i.purpose || 'any');
    expect(purposes).not.toContain('any maskable');
    expect(purposes).toContain('maskable');
    for (const icon of manifest.icons) {
      const r = await page.request.get(icon.src);
      expect(r.status(), icon.src).toBe(200);
      expect(r.headers()['content-type']).toContain('image/png');
    }
    const apple = await page.request.get('/apple-touch-icon-180x180.png');
    expect(apple.status()).toBe(200);
  });
});
