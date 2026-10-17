import { test, expect, Page } from '@playwright/test';
import {
  setupSearchMocks,
  MOCK_SEARCH_RESULTS,
  MOCK_SAVED_SEARCHES,
} from '../helpers/mock.helpers';

// Weitere Such-Regressionen (Highlight als <mark>, geleerte Betragsfelder,
// gespeicherte Suchen mit allen Filtern, Seite 2) stehen in
// 30-search-regressions.spec.ts.

/** Naechster Such-Request (GET /api/search?...) als URLSearchParams; optional
 *  der erste, dessen Parameter `match` erfuellen. */
async function nextSearch(page: Page, action: () => Promise<unknown>, match: (p: URLSearchParams) => boolean = () => true) {
  const [req] = await Promise.all([
    page.waitForRequest((r) => {
      const url = new URL(r.url());
      return url.pathname === '/api/search' && match(url.searchParams);
    }),
    action(),
  ]);
  return new URL(req.url()).searchParams;
}

const searchInput = (page: Page) => page.getByRole('textbox', { name: 'Volltextsuche' });

test.describe('Search', () => {
  test.beforeEach(async ({ page }) => {
    await setupSearchMocks(page);
  });

  test('Search page shows search input', async ({ page }) => {
    await page.goto('/suche');
    await expect(searchInput(page)).toBeVisible();
  });

  test('Execute search and see results', async ({ page }) => {
    await page.goto('/suche');
    await searchInput(page).fill('Stromrechnung');

    const params = await nextSearch(page, () => page.getByRole('button', { name: 'Suchen', exact: true }).click());
    expect(params.get('q')).toBe('Stromrechnung');

    for (const result of MOCK_SEARCH_RESULTS.results) {
      await expect(page.getByRole('heading', { name: result.title })).toBeVisible();
    }
    // Tags kommen als Strings, Datum als document_date
    const first = page.getByRole('link').filter({ has: page.getByRole('heading', { name: MOCK_SEARCH_RESULTS.results[0].title }) });
    await expect(first).toContainText('nebenkosten');
    await expect(first).toContainText('15.1.2026');
    await expect(first).toContainText('Stadtwerke München');
  });

  test('Search shows result count', async ({ page }) => {
    await page.goto('/suche');
    await searchInput(page).fill('test');
    await searchInput(page).press('Enter');

    // Gezielt den Zaehler pruefen — ein beliebiges "2" traefe z.B. die Version.
    await expect(page.getByText(`${MOCK_SEARCH_RESULTS.total} Ergebnisse`, { exact: true })).toBeVisible();
  });

  test('Search result navigates to the document', async ({ page }) => {
    await page.route('**/api/documents/*', (route) => route.fulfill({ status: 404, json: { detail: 'egal' } }));
    await page.goto('/suche');
    await searchInput(page).fill('Strom');
    await searchInput(page).press('Enter');

    await expect(page.getByRole('link').filter({ hasText: MOCK_SEARCH_RESULTS.results[0].title })).toHaveAttribute(
      'href', `/dokumente/${MOCK_SEARCH_RESULTS.results[0].id}`
    );
  });

  test('Toggle advanced filters', async ({ page }) => {
    await page.goto('/suche');
    await page.getByRole('button', { name: 'Erweitert' }).click();

    await expect(page.getByLabel('Datum von')).toBeVisible();
    await expect(page.getByLabel('Min. Betrag')).toBeVisible();
    await page.getByRole('button', { name: 'Einfach' }).click();
    await expect(page.getByLabel('Datum von')).toHaveCount(0);
  });

  test('Advanced filter: date range', async ({ page }) => {
    await page.goto('/suche');
    await page.getByRole('button', { name: 'Erweitert' }).click();
    await page.getByLabel('Datum von').fill('2026-01-01');
    await searchInput(page).fill('test');

    const params = await nextSearch(page, () => searchInput(page).press('Enter'));
    expect(params.get('date_from')).toBe('2026-01-01');
  });

  test('Advanced filter: amount range', async ({ page }) => {
    await page.goto('/suche');
    await page.getByRole('button', { name: 'Erweitert' }).click();
    await page.getByLabel('Min. Betrag').fill('50');
    await page.getByLabel('Max. Betrag').fill('100');
    await searchInput(page).fill('test');

    const params = await nextSearch(page, () => searchInput(page).press('Enter'));
    expect(params.get('amount_min')).toBe('50');
    expect(params.get('amount_max')).toBe('100');
  });

  test('Tax relevant filter in advanced search', async ({ page }) => {
    await page.goto('/suche');
    await page.getByRole('button', { name: 'Erweitert' }).click();
    await page.getByRole('checkbox', { name: 'Nur steuerrelevante' }).check();
    await searchInput(page).fill('test');

    const params = await nextSearch(page, () => searchInput(page).press('Enter'));
    expect(params.get('tax_relevant')).toBe('true');
  });

  test('Document type facets show label and count', async ({ page }) => {
    await page.goto('/suche');
    const facets = page.getByTestId('facet-types');
    await expect(facets.getByRole('checkbox')).toHaveCount(2);
    await expect(facets.locator('label').filter({ hasText: /^\s*Rechnung/ })).toContainText('1');
    await expect(facets.locator('label').filter({ hasText: 'Lohnabrechnung' })).toContainText('1');
    // Kein JSON-Dump der Facetten
    await expect(facets).not.toContainText('{');
  });

  test('Multiple type facets combine into one comma-separated filter', async ({ page }) => {
    await page.goto('/suche');
    const facets = page.getByTestId('facet-types');
    await expect(facets.getByRole('checkbox')).toHaveCount(2);

    const first = await nextSearch(page, () =>
      facets.locator('label').filter({ hasText: /^\s*Rechnung/ }).getByRole('checkbox').check()
    );
    expect(first.get('document_type')).toBe('RECHNUNG');

    const both = await nextSearch(page, () =>
      facets.locator('label').filter({ hasText: 'Lohnabrechnung' }).getByRole('checkbox').check()
    );
    expect(both.get('document_type')?.split(',').sort()).toEqual(['LOHNABRECHNUNG', 'RECHNUNG']);
    await expect(page.getByTestId('active-filters')).toContainText('Typ: Rechnung');
    await expect(page.getByTestId('active-filters')).toContainText('Typ: Lohnabrechnung');

    const one = await nextSearch(page, () =>
      page.getByRole('button', { name: 'Filter „Typ: Rechnung“ entfernen' }).click()
    );
    expect(one.get('document_type')).toBe('LOHNABRECHNUNG');
  });

  test('Saved searches are listed', async ({ page }) => {
    await page.goto('/suche');
    for (const saved of MOCK_SAVED_SEARCHES) {
      await expect(page.getByRole('button', { name: saved.name, exact: true })).toBeVisible();
    }
  });

  test('Click saved search executes it with its parameters', async ({ page }) => {
    await page.goto('/suche');
    await expect(page.getByRole('heading', { name: MOCK_SEARCH_RESULTS.results[0].title })).toBeVisible();
    const params = await nextSearch(
      page,
      () => page.getByRole('button', { name: MOCK_SAVED_SEARCHES[0].name, exact: true }).click(),
      (p) => p.has('q'),
    );
    expect(params.get('q')).toBe('rechnung');
    expect(params.get('date_from')).toBe('2026-01-01');
    await expect(searchInput(page)).toHaveValue('rechnung');
  });

  test('Save current search', async ({ page }) => {
    const [req] = await Promise.all([
      page.waitForRequest((r) => r.method() === 'POST' && new URL(r.url()).pathname === '/api/saved-searches'),
      (async () => {
        await page.goto('/suche');
        await searchInput(page).fill('strom');
        await page.getByRole('button', { name: '+ Speichern' }).click();
        await page.getByLabel('Name der Suche').fill('Meine Suche');
        await page.getByRole('button', { name: 'Speichern', exact: true }).click();
      })(),
    ]);
    expect(req.postDataJSON()).toMatchObject({ name: 'Meine Suche', query_params: { q: 'strom' } });
    await expect(page.getByText('Suche gespeichert.')).toBeVisible();
  });

  test('Delete saved search', async ({ page }) => {
    const deleted: string[] = [];
    await page.route('**/api/saved-searches/*', (route) => {
      if (route.request().method() !== 'DELETE') return route.fallback();
      deleted.push(new URL(route.request().url()).pathname);
      return route.fulfill({ status: 204, body: '' });
    });
    await page.goto('/suche');

    const first = MOCK_SAVED_SEARCHES[0];
    await page.getByRole('button', { name: `Gespeicherte Suche „${first.name}“ löschen` }).click();

    await expect.poll(() => deleted).toEqual([`/api/saved-searches/${first.id}`]);
    await expect(page.getByRole('button', { name: first.name, exact: true })).toHaveCount(0);
    await expect(page.getByRole('button', { name: MOCK_SAVED_SEARCHES[1].name, exact: true })).toBeVisible();
  });

  test('Sort dropdown changes order', async ({ page }) => {
    await page.goto('/suche');
    await expect(page.getByRole('heading', { name: MOCK_SEARCH_RESULTS.results[0].title })).toBeVisible();

    const params = await nextSearch(page, () => page.getByLabel('Sortierung:').selectOption({ label: 'Datum' }));
    expect(params.get('sort_by')).toBe('date');
    expect(params.get('sort_order')).toBe('desc');
  });

  test('Search error shows a message', async ({ page }) => {
    await page.route('**/api/search?*', (route) =>
      route.fulfill({ status: 500, json: { detail: 'Interner Serverfehler' } })
    );
    await page.goto('/suche');
    await expect(page.getByText('Suche fehlgeschlagen.')).toBeVisible();
  });

  test('Reset advanced filters', async ({ page }) => {
    await page.goto('/suche');
    await page.getByRole('button', { name: 'Erweitert' }).click();

    const dateFrom = page.getByLabel('Datum von');
    await dateFrom.fill('2026-01-01');
    await page.getByRole('button', { name: 'Zurücksetzen', exact: true }).click();

    await expect(dateFrom).toHaveValue('');
  });
});
