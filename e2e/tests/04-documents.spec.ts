import { test, expect } from '@playwright/test';
import { setupDocumentsMocks, MOCK_DOCUMENTS } from '../helpers/mock.helpers';

test.describe('Documents List', () => {
  test.beforeEach(async ({ page }) => {
    await setupDocumentsMocks(page);
    await page.goto('/dokumente');
  });

  test('Shows document table with correct data', async ({ page }) => {
    // Check header - Hochladen button
    await expect(page.locator('a, button').filter({ hasText: /hochladen/i }).first()).toBeVisible();

    // Check first document is listed
    const firstDoc = MOCK_DOCUMENTS.items[0];
    await expect(page.locator(`text=${firstDoc.title}`).first()).toBeVisible();
  });

  test('Document row is clickable and navigates to detail', async ({ page }) => {
    const firstDoc = MOCK_DOCUMENTS.items[0];
    // Mock the detail page
    await page.route(`**/api/documents/${firstDoc.id}`, (route) => {
      if (route.request().method() === 'GET') {
        return route.fulfill({ json: firstDoc });
      }
      return route.continue();
    });
    await page.route(`**/api/documents/${firstDoc.id}/file`, (route) =>
      route.fulfill({ status: 200, contentType: 'application/pdf', body: Buffer.from('%PDF') })
    );
    await page.route(`**/api/documents/${firstDoc.id}/thumbnail`, (route) =>
      route.fulfill({ status: 200, contentType: 'image/png', body: Buffer.from('fake') })
    );

    await page.locator(`text=${firstDoc.title}`).first().click();
    await expect(page).toHaveURL(`/dokumente/${firstDoc.id}`);
  });

  test('Filter by document type', async ({ page }) => {
    const typeSelect = page.locator('select').first();
    await expect(typeSelect).toBeVisible();

    // Select RECHNUNG
    let filteredUrl = '';
    await page.route('**/api/documents?*', (route) => {
      filteredUrl = route.request().url();
      return route.fulfill({ json: MOCK_DOCUMENTS });
    });

    await typeSelect.selectOption('RECHNUNG');
    // Wait for the request
    await page.waitForTimeout(500);
    expect(filteredUrl).toContain('RECHNUNG');
  });

  test('Filter by date range', async ({ page }) => {
    const dateInputs = page.locator('input[type="date"]');
    const fromDate = dateInputs.nth(0);
    const toDate = dateInputs.nth(1);

    await expect(fromDate).toBeVisible();
    await expect(toDate).toBeVisible();

    let filteredUrl = '';
    await page.route('**/api/documents?*', (route) => {
      filteredUrl = route.request().url();
      return route.fulfill({ json: MOCK_DOCUMENTS });
    });

    await fromDate.fill('2026-01-01');
    await page.waitForTimeout(500);
    expect(filteredUrl).toContain('2026-01-01');
  });

  test('Tax relevant checkbox filter', async ({ page }) => {
    const taxFilter = page.locator('#taxFilter');
    await expect(taxFilter).toBeVisible();

    let filteredUrl = '';
    await page.route('**/api/documents?*', (route) => {
      filteredUrl = route.request().url();
      return route.fulfill({ json: MOCK_DOCUMENTS });
    });

    await taxFilter.check();
    await page.waitForTimeout(500);
    expect(filteredUrl).toContain('tax_relevant=true');
  });

  test('Reset filters button clears all filters', async ({ page }) => {
    // Set a filter first
    const typeSelect = page.locator('select').first();
    await typeSelect.selectOption('RECHNUNG');

    // Click reset
    const resetBtn = page.getByRole('button', { name: /zuruecksetzen/i });
    await expect(resetBtn).toBeVisible();
    await resetBtn.click();

    // Type select should be back to default
    await expect(typeSelect).toHaveValue('');
  });

  test('Sort by column header click', async ({ page }) => {
    let sortUrl = '';
    await page.route('**/api/documents?*', (route) => {
      sortUrl = route.request().url();
      return route.fulfill({ json: MOCK_DOCUMENTS });
    });

    // Wait for table to be stable, then click Titel header (first sortable)
    await page.waitForTimeout(500);
    await page.locator('th:has-text("Titel")').first().click({ force: true });
    await page.waitForTimeout(1000);
    expect(sortUrl).toContain('sort');
  });

  test('Toggle tax relevance inline', async ({ page }) => {
    let patchCalled = false;
    await page.route('**/api/documents/*/tax_relevant*', (route) => {
      patchCalled = true;
      return route.fulfill({ json: { ...MOCK_DOCUMENTS.items[0], tax_relevant: false } });
    });
    // Also catch PATCH on documents
    await page.route('**/api/documents/*', (route) => {
      if (route.request().method() === 'PATCH') {
        patchCalled = true;
        return route.fulfill({ json: { ...MOCK_DOCUMENTS.items[0], tax_relevant: false } });
      }
      return route.continue();
    });

    // Find and click a tax checkbox in the table
    const taxCheckboxes = page.locator('td input[type="checkbox"]');
    const count = await taxCheckboxes.count();
    if (count > 0) {
      await taxCheckboxes.first().click();
      await page.waitForTimeout(500);
      expect(patchCalled).toBeTruthy();
    }
  });

  test('Empty state when no documents', async ({ page }) => {
    await page.route('**/api/documents*', (route) => {
      if (route.request().method() === 'GET') {
        return route.fulfill({ json: { items: [], total: 0, page: 1, page_size: 20 } });
      }
      return route.continue();
    });

    await page.goto('/dokumente');
    await expect(page.locator('text=Keine Dokumente').first()).toBeVisible();
  });

  test('Upload button navigates to upload page', async ({ page }) => {
    const uploadLink = page.getByRole('link', { name: /hochladen/i }).first();
    await uploadLink.click();
    await expect(page).toHaveURL('/upload');
  });
});
