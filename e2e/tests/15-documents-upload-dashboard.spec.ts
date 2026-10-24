import { test, expect, Page } from '@playwright/test';
import {
  setupBaseMocks,
  setupDashboardMocks,
  setupDocumentsMocks,
  setupDocumentDetailMocks,
  setupScanMocks,
  setupUploadMocks,
  mockCameraPermissionDenied,
  MOCK_DOCUMENTS,
  MOCK_DOCUMENT_DETAIL,
  MOCK_DOCUMENT_WITH_QUESTIONS,
  MOCK_JOBS_ACTIVE,
  MOCK_JOBS_EMPTY,
  MOCK_STATS,
} from '../helpers/mock.helpers';
import * as path from 'path';

// Regressionstests fuer die Review-Findings der Views Dokumente, Detail,
// Upload, Scan und Dashboard (Paket frontend-documents-upload-dashboard).

const TEST_FILES_DIR = path.resolve(__dirname, '../fixtures/test-files');

// Der PWA-Service-Worker wuerde page.route()-Mocks umgehen.
test.use({ serviceWorkers: 'block' });

// ------------------------------------------------------------------
// Upload: Link zeigt auf das Dokument (JobStatusResponse.document_id)
// ------------------------------------------------------------------

async function mockUploadJob(page: Page, jobId: string, finalStatus: Record<string, unknown>) {
  await page.route('**/api/documents/upload', (route) =>
    route.fulfill({
      json: {
        uploaded: [{ job_id: jobId, original_filename: 'rechnung.pdf', status: 'PENDING', message: 'Hochgeladen' }],
        rejected: [],
      },
    })
  );
  const polled: string[] = [];
  await page.route('**/api/documents/*/status', (route) => {
    polled.push(new URL(route.request().url()).pathname);
    return route.fulfill({
      json: { id: jobId, original_filename: 'rechnung.pdf', source: 'UPLOAD', ...finalStatus },
    });
  });
  return polled;
}

test.describe('Upload-Link', () => {
  test('Fertiger Upload verlinkt auf die Dokument-ID, nicht auf die Job-ID', async ({ page }) => {
    await setupUploadMocks(page);
    const polled = await mockUploadJob(page, 'job-1', { status: 'COMPLETED', document_id: 'doc-5' });
    await page.goto('/upload');
    await page.locator('input[type="file"]').setInputFiles(path.join(TEST_FILES_DIR, 'rechnung.pdf'));

    const link = page.getByTestId('upload-job').getByRole('link', { name: 'Anzeigen' });
    await expect(link).toHaveAttribute('href', '/dokumente/doc-5', { timeout: 10000 });
    expect(polled).toContain('/api/documents/job-1/status');
  });

  test('Upload mit Prüfbedarf verlinkt mit „Prüfen“ auf das Dokument', async ({ page }) => {
    await setupUploadMocks(page);
    await mockUploadJob(page, 'job-2', { status: 'NEEDS_REVIEW', document_id: 'doc-6' });
    await page.goto('/upload');
    await page.locator('input[type="file"]').setInputFiles(path.join(TEST_FILES_DIR, 'rechnung.pdf'));

    const row = page.getByTestId('upload-job');
    await expect(row.getByRole('link', { name: 'Prüfen' })).toHaveAttribute('href', '/dokumente/doc-6', { timeout: 10000 });
    await expect(row.getByText('Prüfung nötig')).toBeVisible();
  });
});

// ------------------------------------------------------------------
// Detailansicht
// ------------------------------------------------------------------

function capturePatch(page: Page, doc: Record<string, unknown>) {
  const bodies: Record<string, unknown>[] = [];
  return {
    bodies,
    install: () =>
      page.route(`**/api/documents/${doc.id}`, (route) => {
        if (route.request().method() === 'PATCH') {
          const body = route.request().postDataJSON() as Record<string, unknown>;
          bodies.push(body);
          return route.fulfill({ json: { ...doc, ...body } });
        }
        return route.fallback();
      }),
  };
}

test.describe('Dokument-Detail', () => {
  test('Steuerjahr ist editierbar und wird gespeichert', async ({ page }) => {
    const doc = { ...MOCK_DOCUMENT_DETAIL, document_date: '2025-12-28', tax_year: 2025 };
    await setupDocumentDetailMocks(page, doc);
    const patch = capturePatch(page, doc);
    await patch.install();
    await page.goto(`/dokumente/${doc.id}`);

    const year = page.getByLabel('Steuerjahr');
    await expect(year).toHaveValue('2025');
    await year.fill('2026');
    await page.getByRole('button', { name: 'Speichern' }).click();

    await expect.poll(() => patch.bodies.length).toBe(1);
    expect(patch.bodies[0]).toEqual({ tax_year: 2026 });
  });

  test('Geleerter Betrag und Datum werden als null gesendet', async ({ page }) => {
    const doc = { ...MOCK_DOCUMENT_DETAIL, document_date: '2026-01-15', amount: 89.5 };
    await setupDocumentDetailMocks(page, doc);
    const patch = capturePatch(page, doc);
    await patch.install();
    await page.goto(`/dokumente/${doc.id}`);

    await page.getByLabel('Betrag').fill('');
    await page.getByLabel('Datum').fill('');
    await page.getByRole('button', { name: 'Speichern' }).click();

    await expect.poll(() => patch.bodies.length).toBe(1);
    expect(patch.bodies[0]).toEqual({ amount: null, document_date: null });
    await expect(page.getByText('Änderungen gespeichert.')).toBeVisible();
  });

  test('Validierungsfehler des Servers wird angezeigt', async ({ page }) => {
    await setupDocumentDetailMocks(page);
    await page.route(`**/api/documents/${MOCK_DOCUMENT_DETAIL.id}`, (route) => {
      if (route.request().method() === 'PATCH') {
        return route.fulfill({ status: 422, json: { detail: 'Titel darf nicht leer sein' } });
      }
      return route.fallback();
    });
    await page.goto(`/dokumente/${MOCK_DOCUMENT_DETAIL.id}`);

    await page.getByLabel('Titel').fill('');
    await page.getByRole('button', { name: 'Speichern' }).click();
    await expect(page.getByText('Titel darf nicht leer sein')).toBeVisible();
  });

  test('Lösch-Dialog verspricht keine Wiederherstellung', async ({ page }) => {
    await setupDocumentDetailMocks(page);
    await page.goto(`/dokumente/${MOCK_DOCUMENT_DETAIL.id}`);
    await page.getByRole('button', { name: 'Löschen' }).click();

    await expect(page.getByText('aus dem Archiv, der Suche und dem Assistenten entfernt')).toBeVisible();
    await expect(page.getByText(/wiederhergestellt/)).toHaveCount(0);
  });

  test('Steuerkategorien mit echten Umlauten und ohne doppeltes „Keine“', async ({ page }) => {
    await setupDocumentDetailMocks(page);
    await page.goto(`/dokumente/${MOCK_DOCUMENT_DETAIL.id}`);
    const select = page.getByLabel('Steuerkategorie');
    await expect(select.locator('option', { hasText: 'Außergewöhnliche Belastungen' })).toHaveCount(1);
    await expect(select.locator('option', { hasText: /^Keine$/ })).toHaveCount(0);
  });

  test('TIFF zeigt Platzhalter mit Download statt kaputtem Bild', async ({ page }) => {
    const doc = { ...MOCK_DOCUMENT_DETAIL, id: 'doc-9', file_type: 'tiff', original_filename: 'scan.tiff' };
    await setupDocumentDetailMocks(page, doc);
    await page.route(`**/api/documents/${doc.id}/thumbnail`, (route) =>
      route.fulfill({ path: path.join(TEST_FILES_DIR, 'scan.png'), contentType: 'image/png' })
    );
    await page.goto(`/dokumente/${doc.id}`);

    const placeholder = page.getByTestId('preview-unavailable');
    await expect(placeholder).toBeVisible();
    await expect(placeholder.getByText('Vorschau im Browser nicht verfügbar.')).toBeVisible();
    await expect(placeholder.getByRole('link', { name: 'Herunterladen' })).toHaveAttribute('href', `/api/documents/${doc.id}/file`);
    await expect(placeholder.locator('img')).toHaveAttribute('src', `/api/documents/${doc.id}/thumbnail`);
  });

  test('Beantwortete Rückfrage aktualisiert das betroffene Formularfeld', async ({ page }) => {
    const doc = { ...MOCK_DOCUMENT_WITH_QUESTIONS, document_type: 'SONSTIGES' };
    await setupDocumentDetailMocks(page, doc);
    let answered = false;
    await page.route(`**/api/review/questions/*/answer`, (route) => {
      answered = true;
      return route.fulfill({ json: { ok: true, all_answered: false } });
    });
    // Nach der Antwort liefert der Server den aktualisierten Stand.
    await page.route(`**/api/documents/${doc.id}`, (route) => {
      if (route.request().method() !== 'GET') return route.fallback();
      if (!answered) return route.fulfill({ json: doc });
      return route.fulfill({
        json: {
          ...doc,
          document_type: 'RECHNUNG',
          review_questions: doc.review_questions.map((q) =>
            q.id === 'q-1' ? { ...q, is_answered: true, answer: 'RECHNUNG' } : q
          ),
        },
      });
    });
    await page.goto(`/dokumente/${doc.id}`);

    // Ungespeicherte Aenderung an einem anderen Feld bleibt erhalten.
    await page.getByLabel('Aussteller').fill('Neuer Aussteller');
    await expect(page.getByLabel('Dokumenttyp', { exact: true })).toHaveValue('SONSTIGES');

    const answerInput = page.getByLabel('Um welchen Dokumenttyp handelt es sich?');
    await answerInput.fill('RECHNUNG');
    await answerInput.press('Enter');

    await expect(page.getByLabel('Dokumenttyp', { exact: true })).toHaveValue('RECHNUNG');
    await expect(page.getByLabel('Aussteller')).toHaveValue('Neuer Aussteller');
  });

  test('Ungespeicherte Änderungen: Verlassen fragt nach', async ({ page }) => {
    await setupDocumentsMocks(page);
    await setupDocumentDetailMocks(page);
    await page.goto(`/dokumente/${MOCK_DOCUMENT_DETAIL.id}`);
    await page.getByLabel('Titel').fill('Geänderter Titel');

    await page.getByRole('button', { name: 'Zurück' }).click();
    await expect(page.getByText('Ungespeicherte Änderungen')).toBeVisible();
    await page.getByRole('button', { name: 'Weiter bearbeiten' }).click();
    await expect(page).toHaveURL(`/dokumente/${MOCK_DOCUMENT_DETAIL.id}`);
    await expect(page.getByLabel('Titel')).toHaveValue('Geänderter Titel');

    await page.getByRole('button', { name: 'Zurück' }).click();
    await page.getByRole('button', { name: 'Verwerfen und verlassen' }).click();
    await expect(page).toHaveURL(/\/dokumente$/);
  });

  test('Gelöschtes Dokument (404) führt mit Hinweis zur Liste', async ({ page }) => {
    await setupDocumentsMocks(page);
    await page.route('**/api/documents/77', (route) =>
      route.fulfill({ status: 404, json: { detail: 'Dokument nicht gefunden' } })
    );
    await page.goto('/dokumente/77');
    await expect(page.getByText('Dokument nicht gefunden – es wurde möglicherweise gelöscht.')).toBeVisible();
    await expect(page).toHaveURL(/\/dokumente$/);
  });

  test('Header-Aktionen bleiben auf dem Smartphone sichtbar', async ({ page }) => {
    await page.setViewportSize({ width: 393, height: 851 });
    await setupDocumentDetailMocks(page, { ...MOCK_DOCUMENT_DETAIL, title: 'Sehr langer Dokumenttitel '.repeat(6) });
    await page.goto(`/dokumente/${MOCK_DOCUMENT_DETAIL.id}`);

    await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
    for (const name of ['Herunterladen', 'Löschen']) {
      const el = page.getByRole('main').getByText(name, { exact: true }).first();
      await expect(el).toBeInViewport();
    }
  });
});

// ------------------------------------------------------------------
// Dokumentliste
// ------------------------------------------------------------------

test.describe('Dokumentliste', () => {
  test('Filter stehen in der URL und überleben den Weg über die Detailansicht', async ({ page }) => {
    await setupDocumentsMocks(page);
    const doc = MOCK_DOCUMENTS.items[0];
    await setupDocumentDetailMocks(page, { ...MOCK_DOCUMENT_DETAIL, ...doc });
    await page.goto('/dokumente');

    await page.getByLabel('Typ').selectOption('RECHNUNG');
    await expect(page).toHaveURL(/typ=RECHNUNG/);

    await page.getByRole('link', { name: `Dokument ${doc.title} öffnen` }).click();
    await expect(page).toHaveURL(`/dokumente/${doc.id}`);
    await page.getByRole('button', { name: 'Zurück' }).click();

    await expect(page).toHaveURL(/\/dokumente\?typ=RECHNUNG/);
    await expect(page.getByLabel('Typ')).toHaveValue('RECHNUNG');
  });

  test('Filter aus der URL werden beim Laden übernommen', async ({ page }) => {
    await setupDocumentsMocks(page);
    const requested: string[] = [];
    await page.route('**/api/documents?*', (route) => {
      requested.push(route.request().url());
      return route.fulfill({ json: MOCK_DOCUMENTS });
    });
    await page.goto('/dokumente?typ=QUITTUNG&steuer=1&seite=2');

    await expect(page.getByLabel('Typ')).toHaveValue('QUITTUNG');
    await expect(page.locator('#taxFilter')).toBeChecked();
    await expect.poll(() => requested.length).toBeGreaterThan(0);
    const last = new URL(requested[requested.length - 1]);
    expect(last.searchParams.get('document_type')).toBe('QUITTUNG');
    expect(last.searchParams.get('tax_relevant')).toBe('true');
    expect(last.searchParams.get('page')).toBe('2');
    // Genau ein Listen-Request beim Laden (kein Doppel-Load durch Watcher).
    expect(requested.length).toBe(1);
  });

  test('Veraltete Antwort überschreibt nicht die neuere', async ({ page }) => {
    await setupDocumentsMocks(page);
    await page.route('**/api/documents?*', async (route) => {
      const type = new URL(route.request().url()).searchParams.get('document_type');
      if (type === 'RECHNUNG') {
        // langsame, veraltete Antwort
        await new Promise((r) => setTimeout(r, 800));
        return route.fulfill({ json: { ...MOCK_DOCUMENTS, items: [MOCK_DOCUMENTS.items[0]], total: 1 } });
      }
      if (type === 'KAUFVERTRAG') {
        return route.fulfill({ json: { ...MOCK_DOCUMENTS, items: [MOCK_DOCUMENTS.items[1]], total: 1 } });
      }
      return route.fulfill({ json: MOCK_DOCUMENTS });
    });
    await page.goto('/dokumente');
    await expect(page.getByText(MOCK_DOCUMENTS.items[2].title).first()).toBeVisible();

    // Auf die langsame (veraltete) Antwort warten statt eine feste Zeit
    const stale = page.waitForEvent('requestfinished', (r) => r.url().includes('document_type=RECHNUNG'));
    await page.getByLabel('Typ').selectOption('RECHNUNG');
    await page.getByLabel('Typ').selectOption('KAUFVERTRAG');
    await expect(page.getByText(MOCK_DOCUMENTS.items[1].title).first()).toBeVisible();
    await stale;
    // Zwei Frames: die App hat die Antwort verarbeitet und neu gerendert
    await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(() => r(null)))));
    await expect(page.getByText(MOCK_DOCUMENTS.items[0].title)).toHaveCount(0);
    // Die aeltere URL-Navigation darf den Filter nicht zuruecksetzen.
    await expect(page.getByLabel('Typ')).toHaveValue('KAUFVERTRAG');
    await expect(page).toHaveURL(/typ=KAUFVERTRAG/);
  });

  test('Klick auf „Dokumente“ in der Navigation setzt die Filter zurück', async ({ page }) => {
    await setupDocumentsMocks(page);
    await page.goto('/dokumente?typ=RECHNUNG');
    await expect(page.getByLabel('Typ')).toHaveValue('RECHNUNG');
    await page.getByRole('link', { name: 'Dokumente', exact: true }).first().click();
    await expect(page).toHaveURL(/\/dokumente$/);
    await expect(page.getByLabel('Typ')).toHaveValue('');
  });

  test('Smartphone: Karten statt abgeschnittener Tabelle, kein horizontales Scrollen', async ({ page }) => {
    await page.setViewportSize({ width: 393, height: 851 });
    await setupDocumentsMocks(page);
    await page.goto('/dokumente');

    const cards = page.getByTestId('document-card');
    await expect(cards).toHaveCount(MOCK_DOCUMENTS.items.length);
    await expect(page.locator('table')).toHaveCount(0);
    await expect(cards.first().getByRole('checkbox', { name: /steuerrelevant$/ })).toBeVisible();

    // Sortierung ist ohne Tabellenkoepfe ueber eine Auswahl erreichbar.
    const requested: string[] = [];
    await page.route('**/api/documents?*', (route) => {
      requested.push(route.request().url());
      return route.fulfill({ json: MOCK_DOCUMENTS });
    });
    await page.getByLabel('Sortierung').selectOption('amount:asc');
    await expect.poll(() => requested.length).toBe(1);
    const url = new URL(requested[0]);
    expect(url.searchParams.get('sort_by')).toBe('amount');
    expect(url.searchParams.get('sort_order')).toBe('asc');
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(0);
  });

  test('Zeilen-Aktionen haben zugängliche Namen', async ({ page }) => {
    await setupDocumentsMocks(page);
    await page.goto('/dokumente');
    const doc = MOCK_DOCUMENTS.items[0];
    await expect(page.getByRole('link', { name: `Dokument ${doc.title} öffnen` })).toBeVisible();
    await expect(page.getByRole('checkbox', { name: `${doc.title} steuerrelevant` })).toBeVisible();
  });
});

// ------------------------------------------------------------------
// Dashboard
// ------------------------------------------------------------------

test.describe('Dashboard', () => {
  test('Statistik-Karten zeigen ein Icon statt der ersten Ziffer', async ({ page }) => {
    await setupDashboardMocks(page);
    await page.goto('/');
    const cards = page.getByTestId('stat-cards');
    await expect(cards).toBeVisible();
    const first = cards.locator('> div').first();
    await expect(first).toContainText('📄');
    await expect(first.locator('[aria-hidden="true"]')).not.toHaveText(String(MOCK_STATS.total_documents).charAt(0));
  });

  const DUPLICATE_JOB = {
    id: 'job-dup',
    original_filename: 'rechnung-doppelt.pdf',
    status: 'FAILED',
    source: 'UPLOAD',
    error_code: 'DUPLICATE',
    error_message: 'Diese Datei ist bereits archiviert („Stromrechnung Januar 2026“)',
    document_id: 'doc-1',
    created_at: '2026-03-02T15:00:00Z',
    updated_at: '2026-03-02T15:00:00Z',
  };
  const ERROR_JOB = {
    id: 'job-err',
    original_filename: 'kaputt.pdf',
    status: 'FAILED',
    source: 'UPLOAD',
    error_code: null,
    error_message: 'OCR fehlgeschlagen',
    created_at: '2026-03-02T15:00:00Z',
    updated_at: '2026-03-02T15:00:00Z',
  };

  async function mockFailedJobs(page: Page, jobs: Record<string, unknown>[]) {
    await page.route('**/api/jobs?*', (route) => {
      const status = new URL(route.request().url()).searchParams.get('status');
      const items = status === 'FAILED' ? jobs : [];
      return route.fulfill({ json: { items, total: items.length, page: 1, page_size: 20 } });
    });
  }

  test('Duplikat: „Bereits vorhanden“ mit Link, ohne „Wiederholen“', async ({ page }) => {
    await setupDashboardMocks(page);
    await mockFailedJobs(page, [DUPLICATE_JOB, ERROR_JOB]);
    await page.goto('/');

    await expect(page.getByRole('heading', { name: 'Fehlgeschlagene Verarbeitung' })).toBeVisible();
    const dup = page.getByTestId('failed-job').filter({ hasText: 'rechnung-doppelt.pdf' });
    await expect(dup.getByText('Bereits vorhanden')).toBeVisible();
    await expect(dup.getByRole('link', { name: 'Vorhandenes Dokument öffnen' })).toHaveAttribute('href', '/dokumente/doc-1');
    await expect(dup.getByRole('button', { name: 'Wiederholen' })).toHaveCount(0);

    const err = page.getByTestId('failed-job').filter({ hasText: 'kaputt.pdf' });
    await expect(err.getByRole('button', { name: 'Wiederholen' })).toBeVisible();
  });

  test('Fehlgeschlagener Eintrag lässt sich nach Bestätigung verwerfen', async ({ page }) => {
    await setupDashboardMocks(page);
    await mockFailedJobs(page, [ERROR_JOB]);
    let deleted = '';
    await page.route('**/api/jobs/job-err', (route) => {
      if (route.request().method() === 'DELETE') {
        deleted = new URL(route.request().url()).pathname;
        return route.fulfill({ status: 204, body: '' });
      }
      return route.fallback();
    });
    await page.goto('/');

    await page.getByTestId('failed-job').getByRole('button', { name: 'Verwerfen' }).click();
    await expect(page.getByText('Eintrag verwerfen')).toBeVisible();
    expect(deleted).toBe('');
    await page.getByRole('button', { name: 'Verwerfen', exact: true }).last().click();

    await expect.poll(() => deleted).toBe('/api/jobs/job-err');
    await expect(page.getByTestId('failed-job')).toHaveCount(0);
  });

  test('Backend nicht erreichbar: nur ein Fehler-Toast und Backoff statt 3-s-Takt', async ({ page }) => {
    await page.clock.install();
    await setupBaseMocks(page);
    let statsCalls = 0;
    await page.route('**/api/stats', (route) => {
      statsCalls++;
      return route.fulfill({ status: 503, json: { detail: 'down' } });
    });
    await page.route('**/api/jobs**', (route) => route.fulfill({ status: 503, json: { detail: 'down' } }));
    await page.route('**/api/documents?*', (route) => route.fulfill({ status: 503, json: { detail: 'down' } }));
    await page.goto('/');

    const toast = page.getByText('Dashboard-Daten konnten nicht geladen werden', { exact: false });
    await expect(toast).toHaveCount(1);
    await expect.poll(() => statsCalls).toBe(1);

    // Backoff 3 s -> 6 s -> 12 s: 4 Versuche in 21 s (vorher: 8 Versuche + 8 Toasts).
    // Schrittweise vorspulen — die (echte) Netzwerkantwort plant den naechsten Timer.
    await page.clock.runFor(3100);
    await expect.poll(() => statsCalls).toBe(2);
    await expect(toast).toHaveCount(1);
    await page.clock.runFor(5000);
    expect(statsCalls).toBe(2);  // kein 3-s-Takt mehr
    await page.clock.runFor(1100);
    await expect.poll(() => statsCalls).toBe(3);
    await page.clock.runFor(12100);
    await expect.poll(() => statsCalls).toBe(4);
    await expect(toast).toHaveCount(0);  // erster Toast abgelaufen, kein neuer
  });

  test('Pausieren während eines laufenden Ladens: alte Antwort setzt den Status nicht zurück', async ({ page }) => {
    await setupDashboardMocks(page);
    await page.route('**/api/jobs?*', (route) => {
      const status = new URL(route.request().url()).searchParams.get('status') ?? '';
      return route.fulfill({ json: status.includes('PENDING') ? MOCK_JOBS_ACTIVE : MOCK_JOBS_EMPTY });
    });
    let serverPaused = false;
    let queueCalls = 0;
    let releaseSlowPoll: () => void = () => {};
    await page.route('**/api/jobs/queue-status', async (route) => {
      queueCalls++;
      const paused = serverPaused;  // Stand zum Zeitpunkt der Anfrage
      if (queueCalls === 2) {
        // Dieses Laden ist noch unterwegs, waehrend der Nutzer pausiert.
        await new Promise<void>((resolve) => { releaseSlowPoll = resolve; });
      }
      return route.fulfill({ json: { paused } });
    });
    await page.route('**/api/jobs/pause', (route) => {
      serverPaused = true;
      return route.fulfill({ json: { paused: true, message: 'Queue pausiert' } });
    });
    await page.goto('/');
    await expect(page.getByRole('button', { name: 'Pausieren' })).toBeVisible();

    // Neu laden wie beim Zurueckkehren in den Tab (statt auf den 3-s-Poll zu warten).
    await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
    await expect.poll(() => queueCalls).toBe(2);
    await page.getByRole('button', { name: 'Pausieren' }).click();
    await expect(page.getByRole('button', { name: 'Fortsetzen' })).toBeVisible();
    await expect.poll(() => queueCalls).toBe(3);  // Pausieren laedt sofort neu

    // Veraltete Antwort (paused: false) trifft erst jetzt ein.
    const stale = page.waitForEvent('requestfinished', (r) => r.url().includes('/api/jobs/queue-status'));
    releaseSlowPoll();
    await stale;
    // Zwei Frames: die App hat die Antwort verarbeitet und neu gerendert
    await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(() => r(null)))));
    await expect(page.getByRole('button', { name: 'Fortsetzen' })).toBeVisible();
  });
});

// ------------------------------------------------------------------
// Scan
// ------------------------------------------------------------------

test.describe('Scan', () => {
  test('Abgelehnte Aufnahme bleibt erhalten, keine Weiterleitung', async ({ page }) => {
    await setupScanMocks(page);
    await mockCameraPermissionDenied(page);
    await page.route('**/api/documents/upload', (route) =>
      route.fulfill({ json: { uploaded: [], rejected: [{ filename: 'scan.png', error: 'Datei ist beschädigt' }] } })
    );
    await page.goto('/scan');

    await page.locator('input[type="file"]').setInputFiles(path.join(TEST_FILES_DIR, 'scan.png'));
    await expect(page.getByText('1 Aufnahme')).toBeVisible();
    await page.getByRole('button', { name: 'Hochladen' }).click();

    await expect(page.getByText('scan.png: Datei ist beschädigt')).toBeVisible();
    await expect(page.getByText('hochgeladen.')).toHaveCount(0);
    await expect(page.getByText('1 Aufnahme')).toBeVisible();
    await expect(page).toHaveURL(/\/scan$/);
  });

  test('Datei behält ihren MIME-Typ beim Upload', async ({ page }) => {
    await setupScanMocks(page);
    await mockCameraPermissionDenied(page);
    let body = '';
    await page.route('**/api/documents/upload', (route) => {
      body = route.request().postDataBuffer()?.toString('latin1') || '';
      return route.fulfill({
        json: { uploaded: [{ job_id: 'job-9', original_filename: 'rechnung.pdf', status: 'PENDING', message: 'OK' }], rejected: [] },
      });
    });
    await page.goto('/scan');
    await page.locator('input[type="file"]').setInputFiles(path.join(TEST_FILES_DIR, 'rechnung.pdf'));
    await page.getByRole('button', { name: 'Hochladen' }).click();

    await expect(page).toHaveURL(/\/dokumente/);
    expect(body).toContain('filename="rechnung.pdf"');
    expect(body).toContain('Content-Type: application/pdf');
  });

  test('Fehlende Kamera bekommt eine eigene Meldung', async ({ page }) => {
    await setupScanMocks(page);
    await page.addInitScript(() => {
      navigator.mediaDevices.getUserMedia = async () => {
        throw new DOMException('Requested device not found', 'NotFoundError');
      };
    });
    await page.goto('/scan');
    await expect(page.getByText('Keine Kamera gefunden.', { exact: false })).toBeVisible();
    await expect(page.getByText('Berechtigung')).toHaveCount(0);
  });

  test('Spät aufgelöster Kamera-Stream wird nach dem Verlassen gestoppt', async ({ page }) => {
    await setupScanMocks(page);
    await page.addInitScript(() => {
      const w = window as unknown as { __trackStopped: boolean };
      w.__trackStopped = false;
      navigator.mediaDevices.getUserMedia = () =>
        new Promise((resolve) => {
          setTimeout(() => {
            const canvas = document.createElement('canvas');
            const stream = canvas.captureStream();
            for (const t of stream.getTracks()) {
              const stop = t.stop.bind(t);
              t.stop = () => { w.__trackStopped = true; stop(); };
            }
            resolve(stream);
          }, 700);
        });
    });
    await page.goto('/scan');
    // Vor dem Aufloesen von getUserMedia per SPA-Navigation verlassen.
    await page.getByRole('link', { name: 'Dokumente', exact: true }).first().click();
    await expect(page).toHaveURL(/\/dokumente$/);
    await expect.poll(() => page.evaluate(() => (window as unknown as { __trackStopped: boolean }).__trackStopped), { timeout: 5000 }).toBe(true);
  });

  test('Kamera-Buttons haben zugängliche Namen', async ({ page }) => {
    await setupBaseMocks(page);
    await page.addInitScript(() => {
      const canvas = document.createElement('canvas');
      navigator.mediaDevices.getUserMedia = async () => canvas.captureStream();
    });
    await page.goto('/scan');
    for (const name of ['Kamera wechseln', 'Foto aufnehmen', 'Kamera schließen']) {
      await expect(page.getByRole('button', { name })).toBeVisible();
    }
  });
});
