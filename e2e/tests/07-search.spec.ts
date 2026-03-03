import { test, expect } from '@playwright/test';
import {
  setupSearchMocks,
  MOCK_SEARCH_RESULTS,
  MOCK_SAVED_SEARCHES,
} from '../helpers/mock.helpers';

test.describe('Search', () => {
  test.beforeEach(async ({ page }) => {
    await setupSearchMocks(page);
  });

  test('Search page shows search input', async ({ page }) => {
    await page.goto('/suche');
    const searchInput = page.getByPlaceholder(/volltextsuche/i).first();
    await expect(searchInput).toBeVisible();
  });

  test('Execute search and see results', async ({ page }) => {
    await page.goto('/suche');

    const searchInput = page.getByPlaceholder(/volltextsuche/i).first();
    await searchInput.fill('Stromrechnung');

    const searchBtn = page.getByRole('button', { name: /suchen/i }).first();
    await searchBtn.click();

    // Should show results
    for (const result of MOCK_SEARCH_RESULTS.results) {
      await expect(page.locator(`text=${result.title}`).first()).toBeVisible();
    }
  });

  test('Search shows result count', async ({ page }) => {
    await page.goto('/suche');

    const searchInput = page.getByPlaceholder(/volltextsuche/i).first();
    await searchInput.fill('test');
    await searchInput.press('Enter');

    // Should show "2" results or similar count
    await expect(page.locator(`text=${MOCK_SEARCH_RESULTS.total}`).first()).toBeVisible();
  });

  test('Search result is clickable', async ({ page }) => {
    // Mock document detail for navigation
    await page.route('**/api/documents/*', (route) =>
      route.fulfill({ json: MOCK_SEARCH_RESULTS.results[0] })
    );

    await page.goto('/suche');

    const searchInput = page.getByPlaceholder(/volltextsuche/i).first();
    await searchInput.fill('Strom');
    await searchInput.press('Enter');

    // Click first result
    await page.locator(`text=${MOCK_SEARCH_RESULTS.results[0].title}`).first().click();
    await expect(page).toHaveURL(/\/dokumente\/\d+/);
  });

  test('Toggle advanced filters', async ({ page }) => {
    await page.goto('/suche');

    const toggleBtn = page.getByRole('button', { name: /erweitert/i }).first();
    await toggleBtn.click();

    // Advanced filter inputs should appear
    await expect(page.locator('input[type="date"]').first()).toBeVisible();
    await expect(page.locator('input[type="number"]').first()).toBeVisible();
  });

  test('Advanced filter: date range', async ({ page }) => {
    await page.goto('/suche');

    // Open advanced filters
    const toggleBtn = page.getByRole('button', { name: /erweitert/i }).first();
    await toggleBtn.click();

    const dateFrom = page.locator('input[type="date"]').first();
    await dateFrom.fill('2026-01-01');

    // Execute search
    let searchUrl = '';
    await page.route('**/api/search?*', (route) => {
      searchUrl = route.request().url();
      return route.fulfill({ json: MOCK_SEARCH_RESULTS });
    });

    const searchInput = page.getByPlaceholder(/volltextsuche/i).first();
    await searchInput.fill('test');
    await searchInput.press('Enter');

    await page.waitForTimeout(500);
    expect(searchUrl).toContain('date_from');
  });

  test('Advanced filter: amount range', async ({ page }) => {
    await page.goto('/suche');

    const toggleBtn = page.getByRole('button', { name: /erweitert/i }).first();
    await toggleBtn.click();

    const minAmount = page.locator('input[type="number"]').first();
    await minAmount.fill('50');

    let searchUrl = '';
    await page.route('**/api/search?*', (route) => {
      searchUrl = route.request().url();
      return route.fulfill({ json: MOCK_SEARCH_RESULTS });
    });

    await page.getByPlaceholder(/volltextsuche/i).first().fill('test');
    await page.getByPlaceholder(/volltextsuche/i).first().press('Enter');

    await page.waitForTimeout(500);
    expect(searchUrl).toContain('amount_min');
  });

  test('Tax relevant filter in advanced search', async ({ page }) => {
    await page.goto('/suche');

    const toggleBtn = page.getByRole('button', { name: /erweitert/i }).first();
    await toggleBtn.click();

    const taxCheckbox = page.locator('input[type="checkbox"]').first();
    await taxCheckbox.check();

    let searchUrl = '';
    await page.route('**/api/search?*', (route) => {
      searchUrl = route.request().url();
      return route.fulfill({ json: MOCK_SEARCH_RESULTS });
    });

    await page.getByPlaceholder(/volltextsuche/i).first().fill('test');
    await page.getByPlaceholder(/volltextsuche/i).first().press('Enter');

    await page.waitForTimeout(500);
    expect(searchUrl).toContain('tax_relevant');
  });

  test('Document type facets are shown', async ({ page }) => {
    await page.goto('/suche');

    await page.getByPlaceholder(/volltextsuche/i).first().fill('test');
    await page.getByPlaceholder(/volltextsuche/i).first().press('Enter');

    // Facets should show document types with counts
    await expect(page.locator('text=RECHNUNG').first()).toBeVisible();
  });

  test('Saved searches are listed', async ({ page }) => {
    await page.goto('/suche');

    for (const saved of MOCK_SAVED_SEARCHES) {
      await expect(page.locator(`text=${saved.name}`).first()).toBeVisible();
    }
  });

  test('Click saved search executes it', async ({ page }) => {
    let searchExecuted = false;
    await page.route('**/api/search?*', (route) => {
      searchExecuted = true;
      return route.fulfill({ json: MOCK_SEARCH_RESULTS });
    });

    await page.goto('/suche');

    await page.locator(`text=${MOCK_SAVED_SEARCHES[0].name}`).first().click();
    await page.waitForTimeout(500);
    expect(searchExecuted).toBeTruthy();
  });

  test('Save current search', async ({ page }) => {
    let saveRequested = false;
    await page.route('**/api/saved-searches', (route) => {
      if (route.request().method() === 'POST') {
        saveRequested = true;
        const body = JSON.parse(route.request().postData() || '{}');
        return route.fulfill({ json: { id: 3, name: body.name, query_params: body.query_params } });
      }
      return route.fulfill({ json: MOCK_SAVED_SEARCHES });
    });

    await page.goto('/suche');

    // Click save link
    const saveLink = page.locator('text=/speichern/i').first();
    await saveLink.click();

    // Fill name
    const nameInput = page.locator('input[placeholder*="Name"], input[type="text"]').last();
    await nameInput.fill('Meine Suche');

    // Confirm save
    const saveBtn = page.getByRole('button', { name: /speichern/i }).last();
    await saveBtn.click();

    await page.waitForTimeout(500);
    expect(saveRequested).toBeTruthy();
  });

  test('Delete saved search', async ({ page }) => {
    let deleteCalled = false;
    await page.route('**/api/saved-searches/*', (route) => {
      if (route.request().method() === 'DELETE') {
        deleteCalled = true;
        return route.fulfill({ json: { message: 'OK' } });
      }
      return route.continue();
    });

    await page.goto('/suche');

    // Find and click X button next to saved search
    const deleteButtons = page.locator('[class*="saved"] button, button[aria-label*="loeschen"]');
    const count = await deleteButtons.count();
    if (count > 0) {
      await deleteButtons.first().click();
      await page.waitForTimeout(500);
      expect(deleteCalled).toBeTruthy();
    }
  });

  test('Sort dropdown changes order', async ({ page }) => {
    await page.goto('/suche');

    await page.getByPlaceholder(/volltextsuche/i).first().fill('test');
    await page.getByPlaceholder(/volltextsuche/i).first().press('Enter');

    // Find sort dropdown
    const sortSelect = page.locator('select').filter({ has: page.locator('option:has-text("Relevanz")') }).first();
    if (await sortSelect.isVisible()) {
      let searchUrl = '';
      await page.route('**/api/search?*', (route) => {
        searchUrl = route.request().url();
        return route.fulfill({ json: MOCK_SEARCH_RESULTS });
      });

      await sortSelect.selectOption({ label: 'Datum' });
      await page.waitForTimeout(500);
      expect(searchUrl).toContain('sort');
    }
  });

  test('Reset advanced filters', async ({ page }) => {
    await page.goto('/suche');

    const toggleBtn = page.getByRole('button', { name: /erweitert/i }).first();
    await toggleBtn.click();

    const dateFrom = page.locator('input[type="date"]').first();
    await dateFrom.fill('2026-01-01');

    const resetBtn = page.getByRole('button', { name: /zuruecksetzen/i }).first();
    await resetBtn.click();

    await expect(dateFrom).toHaveValue('');
  });
});
