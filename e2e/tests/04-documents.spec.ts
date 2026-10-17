import { test, expect, Page } from '@playwright/test';
import { setupDocumentsMocks, setupDocumentDetailMocks, MOCK_DOCUMENTS, MOCK_DOCUMENTS_EMPTY } from '../helpers/mock.helpers';

// Weitere Listen-Faelle (Filter in der URL, veraltete Antworten, Karten auf
// dem Smartphone) stehen in 15-documents-upload-dashboard.spec.ts.

/** Naechster Listen-Request (GET /api/documents?...) als URLSearchParams. */
async function nextListRequest(page: Page, action: () => Promise<unknown>) {
  const [req] = await Promise.all([
    page.waitForRequest((r) => r.method() === 'GET' && new URL(r.url()).pathname === '/api/documents'),
    action(),
  ]);
  return new URL(req.url()).searchParams;
}

test.describe('Documents List', () => {
  test.beforeEach(async ({ page }) => {
    await setupDocumentsMocks(page);
    await page.goto('/dokumente');
    // Initiales Laden abwarten, damit nextListRequest nicht diesen Request faengt
    await expect(page.getByRole('link', { name: `Dokument ${MOCK_DOCUMENTS.items[0].title} öffnen` })).toBeVisible();
  });

  test('Shows documents with formatted date and amount', async ({ page }) => {
    await expect(page.getByRole('link', { name: /hochladen/i }).first()).toBeVisible();

    const first = MOCK_DOCUMENTS.items[0];
    const entry = page.getByRole('link', { name: `Dokument ${first.title} öffnen` });
    await expect(entry).toBeVisible();
    // Datum/Betrag stehen in der Zeile (Desktop) bzw. Karte (Smartphone)
    const container = page.locator('tr, [data-testid="document-card"]').filter({ has: entry });
    await expect(container).toContainText('15.1.2026');
    await expect(container).toContainText('89,50');
  });

  test('Document entry navigates to detail', async ({ page }) => {
    const first = MOCK_DOCUMENTS.items[0];
    await setupDocumentDetailMocks(page, { ...first, review_questions: [], warranty_info: null });
    await page.getByRole('link', { name: `Dokument ${first.title} öffnen` }).click();
    await expect(page).toHaveURL(`/dokumente/${first.id}`);
  });

  test('Filter by document type', async ({ page }) => {
    const params = await nextListRequest(page, () => page.getByLabel('Typ').selectOption('RECHNUNG'));
    expect(params.get('document_type')).toBe('RECHNUNG');
    expect(params.get('page')).toBe('1');
  });

  test('Filter by date range', async ({ page }) => {
    const from = await nextListRequest(page, () => page.getByLabel('Von').fill('2026-01-01'));
    expect(from.get('date_from')).toBe('2026-01-01');
    const to = await nextListRequest(page, () => page.getByLabel('Bis').fill('2026-01-31'));
    expect(to.get('date_from')).toBe('2026-01-01');
    expect(to.get('date_to')).toBe('2026-01-31');
  });

  test('Tax relevant checkbox filter', async ({ page }) => {
    const params = await nextListRequest(page, () => page.locator('#taxFilter').check());
    expect(params.get('tax_relevant')).toBe('true');
  });

  test('Reset filters button clears all filters', async ({ page }) => {
    await nextListRequest(page, () => page.getByLabel('Typ').selectOption('RECHNUNG'));
    const params = await nextListRequest(page, () =>
      page.getByRole('button', { name: 'Zurücksetzen' }).click()
    );
    await expect(page.getByLabel('Typ')).toHaveValue('');
    expect(params.has('document_type')).toBe(false);
  });

  test('Sort by column header (desktop) / sort select (mobile)', async ({ page, isMobile }) => {
    if (isMobile) {
      const params = await nextListRequest(page, () => page.getByLabel('Sortierung').selectOption('title:asc'));
      expect(params.get('sort_by')).toBe('title');
      expect(params.get('sort_order')).toBe('asc');
      return;
    }
    const header = page.locator('th').filter({ hasText: 'Titel' });
    const first = await nextListRequest(page, () => header.click());
    expect(first.get('sort_by')).toBe('title');
    expect(first.get('sort_order')).toBe('desc');
    await expect(header).toHaveAttribute('aria-sort', 'descending');

    const second = await nextListRequest(page, () => header.click());
    expect(second.get('sort_order')).toBe('asc');
    await expect(header).toHaveAttribute('aria-sort', 'ascending');
  });

  test('Toggle tax relevance inline sends PATCH', async ({ page }) => {
    const first = MOCK_DOCUMENTS.items[0];
    let body: Record<string, unknown> | null = null;
    await page.route(`**/api/documents/${first.id}`, (route) => {
      if (route.request().method() !== 'PATCH') return route.fallback();
      body = route.request().postDataJSON();
      return route.fulfill({ json: { ...first, tax_relevant: false } });
    });

    const checkbox = page.getByRole('checkbox', { name: `${first.title} steuerrelevant` });
    await expect(checkbox).toBeChecked();
    await checkbox.click();

    await expect.poll(() => body).toEqual({ tax_relevant: false });
    await expect(checkbox).not.toBeChecked();
  });

  test('Failed inline toggle is rolled back with an error', async ({ page }) => {
    const first = MOCK_DOCUMENTS.items[0];
    await page.route(`**/api/documents/${first.id}`, (route) => {
      if (route.request().method() !== 'PATCH') return route.fallback();
      return route.fulfill({ status: 500, json: { detail: 'Interner Serverfehler' } });
    });

    const checkbox = page.getByRole('checkbox', { name: `${first.title} steuerrelevant` });
    await checkbox.click();
    await expect(page.getByText('Steuerrelevanz konnte nicht geändert werden.')).toBeVisible();
    await expect(checkbox).toBeChecked();
  });

  test('Empty state when no documents', async ({ page }) => {
    await page.route('**/api/documents?*', (route) => route.fulfill({ json: MOCK_DOCUMENTS_EMPTY }));
    await page.goto('/dokumente');
    await expect(page.getByText('Keine Dokumente gefunden.')).toBeVisible();
  });

  test('Load error shows a message instead of an empty list', async ({ page }) => {
    await page.route('**/api/documents?*', (route) =>
      route.fulfill({ status: 500, json: { detail: 'Interner Serverfehler' } })
    );
    await page.goto('/dokumente');
    await expect(page.getByText('Dokumente konnten nicht geladen werden.')).toBeVisible();
  });

  test('Upload button navigates to upload page', async ({ page }) => {
    await page.getByRole('link', { name: /hochladen/i }).first().click();
    await expect(page).toHaveURL('/upload');
  });
});
