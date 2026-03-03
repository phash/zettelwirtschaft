import { test, expect } from '@playwright/test';
import {
  setupWarrantyMocks,
  MOCK_WARRANTIES,
  MOCK_WARRANTY_STATS,
} from '../helpers/mock.helpers';

test.describe('Warranty Tracker', () => {
  test.beforeEach(async ({ page }) => {
    await setupWarrantyMocks(page);
  });

  test('Shows warranty stats cards', async ({ page }) => {
    await page.goto('/garantien');

    // Stats card labels should be present (use .card scope to avoid matching <option> elements)
    await expect(page.locator('.card:has-text("Gesamt")').first()).toBeVisible();
    await expect(page.locator('.card:has-text("Aktiv")').first()).toBeVisible();
  });

  test('Shows warranty list items with product names', async ({ page }) => {
    await page.goto('/garantien');

    for (const warranty of MOCK_WARRANTIES) {
      await expect(page.locator(`text=${warranty.product_name}`).first()).toBeVisible();
    }
  });

  test('Warranty items show status badge', async ({ page }) => {
    await page.goto('/garantien');

    // Expired warranty should show "Abgelaufen" badge (use .badge selector to avoid matching <option>)
    await expect(page.locator('.badge:has-text("Abgelaufen")').first()).toBeVisible();
    // Active warranty should show "Aktiv" or remaining days in badge
    const activeBadges = page.locator('.badge').filter({ hasText: /Aktiv|\d+ Tage/ });
    const count = await activeBadges.count();
    expect(count).toBeGreaterThan(0);
  });

  test('Warranty items show retailer info', async ({ page }) => {
    await page.goto('/garantien');

    for (const warranty of MOCK_WARRANTIES) {
      if (warranty.retailer) {
        await expect(page.locator(`text=${warranty.retailer}`).first()).toBeVisible();
      }
    }
  });

  test('Warranty item is clickable and navigates to document', async ({ page }) => {
    // Mock document detail
    await page.route('**/api/documents/*', (route) => {
      if (route.request().method() === 'GET') {
        return route.fulfill({
          json: {
            id: 2,
            title: 'Kaufvertrag Laptop',
            document_type: 'KAUFVERTRAG',
            date: '2026-01-10',
            status: 'ACTIVE',
            tags: [],
            filing_scope: { id: 1, name: 'Privat', slug: 'privat', color: '#3B82F6' },
            filing_scope_id: 1,
            review_questions: [],
            warranty_info: null,
            ocr_confidence: 0.9,
          },
        });
      }
      return route.continue();
    });
    await page.route('**/api/documents/*/file', (route) =>
      route.fulfill({ status: 200, contentType: 'application/pdf', body: Buffer.from('%PDF') })
    );
    await page.route('**/api/documents/*/thumbnail', (route) =>
      route.fulfill({ status: 200, contentType: 'image/png', body: Buffer.from('fake') })
    );
    await page.route('**/api/tags', (route) =>
      route.fulfill({ json: [] })
    );

    await page.goto('/garantien');

    const firstItem = page.locator(`text=${MOCK_WARRANTIES[0].product_name}`).first();
    await firstItem.click();

    await expect(page).toHaveURL(/\/dokumente\/\d+/);
  });

  test('Status filter dropdown works', async ({ page }) => {
    await page.goto('/garantien');

    const filterSelect = page.locator('select').first();
    await expect(filterSelect).toBeVisible();

    // Select "Aktiv"
    await filterSelect.selectOption('active');
    await page.waitForTimeout(500);
  });

  test('Filter active warranties only', async ({ page }) => {
    let filteredUrl = '';
    await page.route('**/api/warranties?*', (route) => {
      filteredUrl = route.request().url();
      return route.fulfill({
        json: MOCK_WARRANTIES.filter((w) => !w.is_expired),
      });
    });

    await page.goto('/garantien');

    const filterSelect = page.locator('select').first();
    await filterSelect.selectOption('active');
    await page.waitForTimeout(500);
    expect(filteredUrl).toContain('active');
  });

  test('Empty state when no warranties', async ({ page }) => {
    await page.route('**/api/warranties?*', (route) =>
      route.fulfill({ json: [] })
    );
    await page.route('**/api/warranties', (route) => {
      if (route.request().method() === 'GET') {
        return route.fulfill({ json: [] });
      }
      return route.continue();
    });
    await page.route('**/api/warranties/stats', (route) =>
      route.fulfill({ json: { total: 0, active: 0, expiring_soon: 0, expired: 0 } })
    );

    await page.goto('/garantien');
    await expect(page.locator('text=Keine Garantien').first()).toBeVisible();
  });

  test('Progress bars show warranty remaining time', async ({ page }) => {
    await page.goto('/garantien');

    // Progress bars should exist (one per warranty)
    const progressBars = page.locator('[class*="rounded-full"][class*="bg-"]');
    const count = await progressBars.count();
    expect(count).toBeGreaterThan(0);
  });
});
