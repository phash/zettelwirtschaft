import { test, expect, Page } from '@playwright/test';
import { setupBaseMocks } from '../helpers/mock.helpers';

// Regressionstests SearchView: Treffer-Markierung, leere Betragsfelder,
// gespeicherte Suchen, Filter-Chips, Sortierrichtung, doppelte Requests.

const STX = '\u0002';
const ETX = '\u0003';

function result(id: string, title: string, highlight = '') {
  return {
    id,
    title,
    document_type: 'RECHNUNG',
    document_date: '2026-01-15',
    amount: 89.5,
    currency: 'EUR',
    issuer: 'Stadtwerke München',
    tags: [],
    tax_relevant: false,
    highlight,
    relevance_score: 1,
    ai_confidence: 0.9,
    created_at: '2026-01-16T10:00:00Z',
  };
}

const RESULTS = {
  results: [
    result('doc-1', 'Stromrechnung Januar', `Monatliche ${STX}Stromrechnung${ETX} für <Januar>`),
    result('doc-2', 'Gasrechnung'),
  ],
  total: 60,
  page: 1,
  page_size: 25,
  facets: {
    document_types: { RECHNUNG: 2, QUITTUNG: 1 },
    years: { '2026': 2, '2025': 1 },
    top_issuers: { 'Stadtwerke München': 2 },
    filing_scopes: [{ id: 'scope-privat', name: 'Privat', count: 2 }],
  },
};

const EMPTY = {
  results: [],
  total: 0,
  page: 1,
  page_size: 25,
  facets: { document_types: {}, years: {}, top_issuers: {}, filing_scopes: [] },
};

interface SearchMock {
  requests: URL[];
  savedBodies: Record<string, unknown>[];
}

async function setupSearch(page: Page, opts: { savedSearches?: unknown[]; respond?: (url: URL) => unknown } = {}) {
  await setupBaseMocks(page);
  const state: SearchMock = { requests: [], savedBodies: [] };
  await page.route('**/api/search?*', (route) => {
    const url = new URL(route.request().url());
    state.requests.push(url);
    return route.fulfill({ json: opts.respond ? opts.respond(url) : RESULTS });
  });
  await page.route('**/api/saved-searches', (route) => {
    if (route.request().method() === 'POST') {
      const body = route.request().postDataJSON() as Record<string, unknown>;
      state.savedBodies.push(body);
      return route.fulfill({ status: 201, json: { id: 'saved-new', ...body, created_at: '2026-03-03T10:00:00Z' } });
    }
    return route.fulfill({ json: opts.savedSearches ?? [] });
  });
  return state;
}

test.describe('Search regressions', () => {
  test('highlight sentinels render as <mark>, never as raw markup', async ({ page }) => {
    await setupSearch(page);
    await page.goto('/suche');

    const snippet = page.getByTestId('search-highlight').first();
    await expect(snippet.locator('mark')).toHaveText('Stromrechnung');
    // Spitze Klammern aus dem OCR-Text bleiben Text (kein v-html).
    await expect(snippet).toContainText('für <Januar>');
    await expect(snippet.locator('januar')).toHaveCount(0);
    expect(await snippet.innerHTML()).not.toContain('\u0002');
  });

  test('clearing an amount field does not send an empty amount_min', async ({ page }) => {
    const state = await setupSearch(page);
    await page.goto('/suche');
    await page.getByRole('button', { name: 'Erweitert' }).click();

    const min = page.getByLabel('Min. Betrag');
    await min.fill('100');
    await expect.poll(() => state.requests.at(-1)?.searchParams.get('amount_min')).toBe('100');

    await min.fill('');
    await expect.poll(() => state.requests.at(-1)?.searchParams.has('amount_min')).toBe(false);
    await expect(page.getByText('Suche fehlgeschlagen.')).toHaveCount(0);
  });

  test('saved search stores all filters and applying it resets stale ones', async ({ page }) => {
    const state = await setupSearch(page, {
      savedSearches: [
        { id: 's1', name: 'Nur Text', query_params: { q: 'strom' }, created_at: '2026-03-01T00:00:00Z' },
      ],
    });
    await page.goto('/suche');
    await page.getByRole('button', { name: 'Erweitert' }).click();
    await page.getByLabel('Min. Betrag').fill('50');
    await page.getByText('Nur steuerrelevante').click();
    await page.getByLabel('Ablagebereich').selectOption('scope-privat');
    await page.getByLabel('Sortierung:').selectOption('title');

    await page.getByRole('button', { name: '+ Speichern' }).click();
    await page.getByLabel('Name der Suche').fill('Teure Belege');
    await page.getByRole('button', { name: 'Speichern', exact: true }).click();

    await expect.poll(() => state.savedBodies.length).toBe(1);
    const params = state.savedBodies[0].query_params as Record<string, unknown>;
    expect(params).toMatchObject({
      amount_min: 50,
      tax_relevant: true,
      filing_scope_id: 'scope-privat',
      sort_by: 'title',
      sort_order: 'asc',
    });

    // Gespeicherte Suche ohne Filter anwenden: alte Filter duerfen nicht mitwirken.
    const before = state.requests.length;
    await page.getByRole('button', { name: 'Nur Text', exact: true }).click();
    await expect.poll(() => state.requests.length).toBeGreaterThan(before);
    const last = state.requests.at(-1)!;
    expect(last.searchParams.get('q')).toBe('strom');
    expect(last.searchParams.has('amount_min')).toBe(false);
    expect(last.searchParams.has('tax_relevant')).toBe(false);
    expect(last.searchParams.has('filing_scope_id')).toBe(false);
    expect(last.searchParams.get('sort_by')).toBe('relevance');
  });

  test('active type filter stays visible and removable at 0 hits', async ({ page }) => {
    await setupSearch(page, {
      respond: (url) => (url.searchParams.get('document_type') ? EMPTY : RESULTS),
    });
    await page.goto('/suche');

    await page.getByTestId('facet-types').getByText('Quittung').click();
    await expect(page.getByText('Keine Ergebnisse gefunden.')).toBeVisible();

    // Chip + Facette bleiben sichtbar, obwohl die Facetten leer sind.
    await expect(page.getByTestId('active-filters')).toContainText('Typ: Quittung');
    const facetBox = page.getByTestId('facet-types');
    await expect(facetBox.getByText('Quittung')).toBeVisible();
    await expect(facetBox.getByRole('checkbox')).toBeChecked();

    await page.getByRole('button', { name: 'Filter zurücksetzen' }).last().click();
    await expect(page.getByText('Stromrechnung Januar')).toBeVisible();
    await expect(page.getByTestId('active-filters')).toHaveCount(0);
  });

  test('sort direction defaults per key and can be toggled', async ({ page }) => {
    const state = await setupSearch(page);
    await page.goto('/suche');

    await page.getByLabel('Sortierung:').selectOption('title');
    await expect.poll(() => state.requests.at(-1)?.searchParams.get('sort_order')).toBe('asc');

    await page.getByTestId('sort-order-toggle').click();
    await expect.poll(() => state.requests.at(-1)?.searchParams.get('sort_order')).toBe('desc');
    expect(state.requests.at(-1)?.searchParams.get('sort_by')).toBe('title');
  });

  test('new search from page 2 sends exactly one request', async ({ page }) => {
    await page.clock.install();
    const state = await setupSearch(page);
    await page.goto('/suche');
    await expect(page.getByText('Stromrechnung Januar')).toBeVisible();

    await page.getByRole('button', { name: '2', exact: true }).click();
    await expect.poll(() => state.requests.at(-1)?.searchParams.get('page')).toBe('2');

    const before = state.requests.length;
    await page.getByRole('button', { name: 'Suchen', exact: true }).click();
    await expect.poll(() => state.requests.length).toBe(before + 1);
    // Alle (auch entprellten) Timer ablaufen lassen: kein doppelter Request.
    await page.clock.runFor(2_000);
    expect(state.requests.length).toBe(before + 1);
    expect(state.requests.at(-1)?.searchParams.get('page')).toBe('1');
  });

  test('year facet sets the date range', async ({ page }) => {
    const state = await setupSearch(page);
    await page.goto('/suche');

    await page.getByTestId('facet-years').getByRole('button', { name: /2025/ }).click();
    await expect.poll(() => state.requests.at(-1)?.searchParams.get('date_from')).toBe('2025-01-01');
    expect(state.requests.at(-1)?.searchParams.get('date_to')).toBe('2025-12-31');
    await expect(page.getByTestId('active-filters')).toContainText('Zeitraum: 1.1.2025 – 31.12.2025');
  });

  test('saved-search delete button has an accessible name', async ({ page }) => {
    await setupSearch(page, {
      savedSearches: [{ id: 's1', name: 'Strom', query_params: { q: 'strom' }, created_at: '2026-03-01T00:00:00Z' }],
    });
    await page.goto('/suche');
    await expect(page.getByRole('button', { name: 'Gespeicherte Suche „Strom“ löschen' })).toBeVisible();
  });
});
