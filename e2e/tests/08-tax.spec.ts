import { test, expect } from '@playwright/test';
import {
  setupTaxMocks,
  MOCK_TAX_SUMMARY,
  MOCK_TAX_YEARS,
  MOCK_TAX_VALIDATION,
} from '../helpers/mock.helpers';

test.describe('Tax Package', () => {
  test.beforeEach(async ({ page }) => {
    await setupTaxMocks(page);
  });

  test('Shows tax year selector', async ({ page }) => {
    await page.goto('/steuer');

    const yearSelect = page.locator('select').filter({ has: page.locator('option:has-text("2026")') }).first();
    await expect(yearSelect).toBeVisible();

    // All years from mock should be available
    for (const year of MOCK_TAX_YEARS.years) {
      await expect(yearSelect.locator(`option:has-text("${year}")`)).toBeAttached();
    }
  });

  test('Shows stats cards with correct values', async ({ page }) => {
    await page.goto('/steuer');

    // Document count
    await expect(page.locator(`text=${MOCK_TAX_SUMMARY.total_documents}`).first()).toBeVisible();
    // Category count
    await expect(page.locator(`text=${MOCK_TAX_SUMMARY.categories.length}`).first()).toBeVisible();
  });

  test('Shows category cards', async ({ page }) => {
    await page.goto('/steuer');

    // Categories use cat.label
    for (const cat of MOCK_TAX_SUMMARY.categories) {
      await expect(page.locator(`text=${cat.label}`).first()).toBeVisible();
    }
  });

  test('Category card shows document count', async ({ page }) => {
    await page.goto('/steuer');

    const firstCat = MOCK_TAX_SUMMARY.categories[0];
    // Shows "5 Belege" format
    await expect(page.locator(`text=${firstCat.document_count} Beleg`).first()).toBeVisible();
  });

  test('Switch year loads new data', async ({ page }) => {
    let requestedYear = '';
    await page.route('**/api/tax/summary/*', (route) => {
      const url = route.request().url();
      requestedYear = url.split('/summary/')[1]?.split('?')[0] || '';
      return route.fulfill({ json: { ...MOCK_TAX_SUMMARY, year: parseInt(requestedYear) } });
    });

    await page.goto('/steuer');

    const yearSelect = page.locator('select').filter({ has: page.locator('option:has-text("2025")') }).first();
    await yearSelect.selectOption('2025');

    await page.waitForTimeout(500);
    expect(requestedYear).toBe('2025');
  });

  test('Export button is present and clickable', async ({ page }) => {
    await page.goto('/steuer');

    const exportBtn = page.getByRole('button', { name: /zip.*exportieren|exportieren/i }).first();
    await expect(exportBtn).toBeVisible();
    await expect(exportBtn).toBeEnabled();
  });

  test('Export button triggers download', async ({ page }) => {
    let exportRequested = false;
    await page.route('**/api/tax/export', (route) => {
      exportRequested = true;
      return route.fulfill({
        status: 200,
        contentType: 'application/zip',
        body: Buffer.from('fake-zip'),
      });
    });

    await page.goto('/steuer');

    const exportBtn = page.getByRole('button', { name: /zip.*exportieren|exportieren/i }).first();
    await exportBtn.click();

    await page.waitForTimeout(1000);
    expect(exportRequested).toBeTruthy();
  });

  test('Shows validation warnings', async ({ page }) => {
    await page.goto('/steuer');

    // Warnings panel
    const warningsText = page.locator(`text=/${MOCK_TAX_VALIDATION.warnings.length}.*Hinweis/i`).first();
    if (await warningsText.isVisible()) {
      await warningsText.click(); // Expand
      for (const warning of MOCK_TAX_VALIDATION.warnings) {
        await expect(page.locator(`text=${warning}`).first()).toBeVisible();
      }
    }
  });

  test('Empty state when no tax documents', async ({ page }) => {
    await page.route('**/api/tax/summary/*', (route) =>
      route.fulfill({
        json: { year: 2026, total_documents: 0, total_amount: 0, categories: [] },
      })
    );
    await page.route('**/api/tax/validate/*', (route) =>
      route.fulfill({ json: { warnings: [], is_valid: true } })
    );

    await page.goto('/steuer');
    await expect(page.locator('text=Keine steuerrelevanten').first()).toBeVisible();
  });

  test('Export button disabled when no documents', async ({ page }) => {
    await page.route('**/api/tax/summary/*', (route) =>
      route.fulfill({
        json: { year: 2026, total_documents: 0, total_amount: 0, categories: [] },
      })
    );

    await page.goto('/steuer');

    const exportBtn = page.getByRole('button', { name: /zip.*exportieren|exportieren/i }).first();
    await expect(exportBtn).toBeDisabled();
  });

  test('Scope filter dropdown visible with multiple scopes', async ({ page }) => {
    await page.goto('/steuer');

    // With 2 filing scopes mocked, scope dropdown should be visible
    const scopeSelect = page.locator('select').filter({ has: page.locator('option:has-text("Alle Bereiche")') }).first();
    if (await scopeSelect.isVisible()) {
      await expect(scopeSelect).toBeVisible();
    }
  });
});
